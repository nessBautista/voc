"""Model-free proofs of prediction reuse, durable batching and terminal coverage."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from voc.embeddings.contracts import text_hash
from voc_ml.sentiment import robertuito as r
from voc_ml.sentiment.cache import Prediction, PredictionCache, prediction


class CountingClassifier:
    def __init__(self, fail_call=None):
        self.calls = []
        self.fail_call = fail_call

    def predict(self, texts):
        self.calls.append(list(texts))
        if len(self.calls) == self.fail_call:
            raise RuntimeError("inference unavailable")
        return [
            prediction(
                {"negative": 0.1, "neutral": 0.2, "positive": 0.7}, len(t.split()) + 2
            )
            for t in texts
        ]


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("VOC_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(r, "_prepare", lambda text: text.strip())
    # No optional model packages are needed for the unit cache tests.
    monkeypatch.setattr(r, "version", lambda name: "test-version")
    return tmp_path


def frame(texts):
    return pd.DataFrame(
        {
            "record_id": [f"r{i}" for i in range(len(texts))],
            "model_text": texts,
            "embedding_text": ["must not be used"] * len(texts),
            "topic_id": [-1] * len(texts),
        }
    )


def run(reviews, classifier, config=None):
    return r.classify_reviews(
        reviews, config, classifier_factory=lambda *args, **kwargs: classifier
    )


def test_repeat_reorder_duplicates_and_outliers(runtime):
    reviews = frame(["buena app", "mala app", "buena app"])
    original = reviews.copy(deep=True)
    first = CountingClassifier()
    result = run(reviews, first)
    assert first.calls == [["buena app", "mala app"]]
    assert [s.record_id for s in result.sentiments] == ["r0", "r1", "r2"]
    assert result.sentiments[0].input_sha256 == text_hash("buena app")
    assert result.report["counts"]["computed_unique"] == 2
    second = r.classify_reviews(
        reviews.iloc[::-1],
        classifier_factory=Mock(
            side_effect=AssertionError("Cache hit must not load model")
        ),
    )
    assert [s.record_id for s in second.sentiments] == ["r2", "r1", "r0"]
    assert second.report["counts"]["reused_unique"] == 2
    assert second.report["counts"]["classified"] == 3
    pd.testing.assert_frame_equal(reviews, original)


def test_changed_text_only_computes_new_input_and_topic_changes_reuse(runtime):
    reviews = frame(["one", "two", "three"])
    original = run(reviews, CountingClassifier())
    reviews.loc[1, "model_text"] = "changed"
    reviews["topic_id"] = [1, 2, 3]
    classifier = CountingClassifier()
    result = run(reviews, classifier, r.SentimentConfig(batch_size=1))
    assert classifier.calls == [["changed"]]
    assert result.report["counts"]["computed_unique"] == 1
    assert result.report["counts"]["reused_unique"] == 2
    assert result.report["classifier_id"] == original.report["classifier_id"]
    assert original.sentiments[1].input_sha256 == text_hash("two")


@pytest.mark.parametrize("change", ["length", "revision", "software"])
def test_incompatible_settings_do_not_reuse(runtime, monkeypatch, change):
    reviews = frame(["one"])
    first = run(reviews, CountingClassifier())
    config = r.SentimentConfig()
    if change == "length":
        config = replace(config, max_length=64)
    elif change == "revision":
        config = replace(config, revision="a" * 40)
    else:
        monkeypatch.setattr(r, "version", lambda name: "another-version")
    classifier = CountingClassifier()
    second = run(reviews, classifier, config)
    assert classifier.calls == [["one"]]
    assert first.report["classifier_id"] != second.report["classifier_id"]


def test_batch_failure_resume_keeps_committed_work(runtime):
    reviews = frame(["one", "two", "three", "four", "one"])
    classifier = CountingClassifier(fail_call=2)
    config = r.SentimentConfig(batch_size=2)
    with pytest.raises(r.SentimentRunError) as caught:
        run(reviews, classifier, config)
    report = caught.value.report
    progress = json.loads(Path(report["progress_path"]).read_text())
    assert report["state"] == "failed"
    assert report["counts"]["computed_unique"] == 2
    assert report["counts"]["classified"] == 3
    assert progress["pending_record_ids"] == ["r2", "r3"]
    resumed_classifier = CountingClassifier()
    resumed = run(reviews, resumed_classifier, config)
    assert resumed_classifier.calls == [["three", "four"]]
    assert resumed.report["counts"]["reused_unique"] == 2
    assert resumed.report["counts"]["classified"] == 5
    assert resumed.report["counts"]["pending_rows"] == 0


def test_exclusions_preprocessing_failure_and_truncation(runtime, monkeypatch):
    def prepare(text):
        if text == "bad":
            raise ValueError("bad text")
        return "" if text == "empty" else text

    monkeypatch.setattr(r, "_prepare", prepare)
    reviews = frame(
        [None, " ", 42, "bad", "empty", "one two three four", "one two three four"]
    )
    classifier = CountingClassifier()
    result = run(reviews, classifier, r.SentimentConfig(max_length=4))
    assert [s.status for s in result.sentiments] == [
        "excluded",
        "excluded",
        "failed",
        "failed",
        "excluded",
        "classified",
        "classified",
    ]
    assert all(
        s.model_label is None and s.probabilities is None for s in result.sentiments[:5]
    )
    assert result.report["counts"] == {
        "rows": 7,
        "unique_inputs": 1,
        "computed_unique": 1,
        "reused_unique": 0,
        "pending_unique": 0,
        "pending_rows": 0,
        "truncated_rows": 2,
        "classified": 2,
        "excluded": 3,
        "failed": 2,
    }
    saved = json.loads(Path(result.report["progress_path"]).read_text())
    assert len(saved["sentiments"]) == 7
    again = run(reviews, CountingClassifier(), r.SentimentConfig(max_length=4))
    assert again.report["counts"]["truncated_rows"] == 2


def test_all_excluded_does_not_initialize_model(runtime):
    result = r.classify_reviews(
        frame([None, ""]),
        classifier_factory=Mock(side_effect=AssertionError("No model needed")),
    )
    assert result.report["counts"]["excluded"] == 2


def test_bad_batch_is_never_partially_cached(runtime):
    good = prediction({"negative": 0.1, "neutral": 0.2, "positive": 0.7}, 4)
    invalid = Prediction(
        "positive", {"negative": 0.1, "neutral": 0.2, "positive": 0.1}, 4
    )
    classifier = SimpleNamespace(predict=lambda texts: [good, invalid])
    with pytest.raises(r.SentimentRunError):
        run(frame(["one", "two"]), classifier)
    replacement = CountingClassifier()
    run(frame(["one", "two"]), replacement)
    assert replacement.calls == [["one", "two"]]


def test_conflicting_cache_batch_rolls_back_and_corruption_fails(runtime):
    path = runtime / "cache.sqlite"
    first = prediction({"negative": 0.1, "neutral": 0.2, "positive": 0.7}, 4)
    other = prediction({"negative": 0.8, "neutral": 0.1, "positive": 0.1}, 4)
    with PredictionCache(path, {"adapter": "fake"}) as cache:
        cache.put_batch([(text_hash("one"), first)])
        with pytest.raises(ValueError, match="refusing overwrite"):
            cache.put_batch([(text_hash("two"), first), (text_hash("one"), other)])
        assert cache.get(text_hash("two")) is None
        assert cache.get(text_hash("one")) == first
        cache.db.execute("UPDATE predictions SET payload='{}'")
        cache.db.commit()
        with pytest.raises(ValueError, match="checksum"):
            cache.get(text_hash("one"))


@pytest.mark.parametrize("mutation", ["empty", "duplicate", "missing-column"])
def test_invalid_reviews_fail_before_runtime_writes(runtime, mutation):
    reviews = frame(["one", "two"])
    if mutation == "empty":
        reviews = reviews.iloc[:0]
    elif mutation == "duplicate":
        reviews.loc[1, "record_id"] = "r0"
    else:
        reviews = reviews.drop(columns="model_text")
    with pytest.raises(ValueError):
        run(reviews, CountingClassifier())
    assert not list(runtime.iterdir())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"revision": "main"},
        {"max_length": 129},
        {"max_length": True},
        {"batch_size": 0},
        {"model": "other/model"},
    ],
)
def test_config_validation(kwargs):
    with pytest.raises(ValueError):
        r.SentimentConfig(**kwargs)


def test_toml_rejects_misspellings(runtime):
    path = runtime / "bad.toml"
    path.write_text("batch_sze = 16")
    with pytest.raises(ValueError, match="Unknown"):
        r.load_config(path)
    assert r.load_config("config/analysis/sentiment.toml") == r.SentimentConfig()


def test_pinned_loader_security_and_label_mapping(runtime, monkeypatch):
    import sys

    snapshot = runtime / r.REVISION
    download = Mock(return_value=str(snapshot))
    tokenizer = SimpleNamespace()
    model = Mock()
    model.to.return_value = model
    model.float.return_value = model
    model.eval.return_value = model
    model.config = SimpleNamespace(
        id2label={0: "NEG", 1: "NEU", 2: "POS"},
        max_position_embeddings=130,
        pad_token_id=1,
    )
    load_tokenizer, load_model = Mock(return_value=tokenizer), Mock(return_value=model)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=download)
    )
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            AutoTokenizer=SimpleNamespace(from_pretrained=load_tokenizer),
            AutoModelForSequenceClassification=SimpleNamespace(
                from_pretrained=load_model
            ),
        ),
    )
    classifier = r.RobertuitoClassifier(r.SentimentConfig(), runtime)
    assert download.call_args.kwargs["revision"] == r.REVISION
    assert download.call_args.kwargs["local_files_only"] is True
    assert "*.safetensors" in download.call_args.kwargs["allow_patterns"]
    assert not any(
        pattern.endswith((".bin", ".py"))
        for pattern in download.call_args.kwargs["allow_patterns"]
    )
    for loader in (load_tokenizer, load_model):
        assert loader.call_args.args == (str(snapshot),)
        assert loader.call_args.kwargs["trust_remote_code"] is False
        assert loader.call_args.kwargs["local_files_only"] is True
        assert loader.call_args.kwargs["token"] is False
    assert load_model.call_args.kwargs["use_safetensors"] is True
    assert classifier.mapping == {0: "negative", 1: "neutral", 2: "positive"}
    model.config.id2label = {0: "LABEL_0", 1: "LABEL_1", 2: "LABEL_2"}
    with pytest.raises(ValueError, match="label mapping"):
        r.RobertuitoClassifier(r.SentimentConfig(), runtime)
    download.return_value = str(runtime / "wrong-revision")
    with pytest.raises(ValueError, match="pinned revision"):
        r.RobertuitoClassifier(r.SentimentConfig(), runtime)


def test_infrastructure_failure_does_not_cache_success(runtime):
    factory = Mock(side_effect=OSError("model unavailable"))
    with pytest.raises(r.SentimentRunError) as caught:
        r.classify_reviews(frame(["one"]), classifier_factory=factory)
    assert caught.value.report["counts"]["classified"] == 0
    assert caught.value.report["counts"]["pending_rows"] == 1
    classifier = CountingClassifier()
    run(frame(["one"]), classifier)
    assert classifier.calls == [["one"]]


def test_read_only_model_cache_can_be_separate_from_writable_predictions(
    runtime, monkeypatch
):
    model_cache = runtime / "mounted-read-only-models"
    monkeypatch.setenv("VOC_SENTIMENT_MODEL_DIR", str(model_cache))
    classifier = Mock(return_value=CountingClassifier())
    result = r.classify_reviews(
        frame(["separate model cache"]), classifier_factory=classifier
    )
    assert result.report["state"] == "completed"
    assert classifier.call_args.args[1] == model_cache
    assert classifier.call_args.kwargs["local_files_only"] is True
    assert (
        not model_cache.exists()
    )  # Selecting the cache must not initialize/download it.
    explicit = runtime / "explicit-cache"
    r.classify_reviews(
        frame(["a different input"]), model_dir=explicit, classifier_factory=classifier
    )
    assert classifier.call_args.args[1] == explicit
