import json
import weakref

import pandas as pd
import pytest

from voc_demo.sentiment import experiments


def inputs(tmp_path):
    paths = {}
    for name, platform in (("rated", "google_play"), ("social", "x")):
        frame = pd.DataFrame(
            [
                {
                    "record_id": f"{name}-{label}-{i}",
                    "text": f"{name} {label} texto caso {i}",
                    "label": label,
                    "platform": platform,
                }
                for label in ("negative", "neutral", "positive")
                for i in range(20)
            ]
        )
        if name == "rated":
            frame["split"] = (
                frame.groupby("label")
                .cumcount()
                .map(
                    lambda i: "train" if i < 12 else "validation" if i < 16 else "test"
                )
            )
        paths[name] = tmp_path / f"{name}.csv"
        frame.to_csv(paths[name], index=False)
    return paths


def test_both_csvs_are_validated_and_store_splits_preserved(tmp_path):
    paths = inputs(tmp_path)
    datasets = experiments.load_experiment_data(paths)
    for name in ("rated", "social"):
        assert [len(part) for part in datasets[name]["splits"].values()] == [36, 12, 12]
        assert len(datasets[name]["source_sha256"]) == 64
    bad_social = pd.read_csv(paths["social"])
    bad_social.loc[0, "label"] = ""
    bad_social.to_csv(paths["social"], index=False)
    with pytest.raises(ValueError, match="Dataset social"):
        experiments.load_experiment_data(paths)


def test_two_runs_have_distinct_data_artifacts_and_release_previous_model(
    tmp_path, monkeypatch
):
    datasets = experiments.load_experiment_data(inputs(tmp_path))
    calls, progress, weights = [], [], []

    class Model:
        pass

    def fake_adapt(splits, output, cache, **kwargs):
        assert all(reference() is None for reference in weights)
        calls.append((splits, output, cache, kwargs))
        output.mkdir()
        model = Model()
        weights.append(weakref.ref(model))
        return object(), model, {"dataset": output.name}

    monkeypatch.setattr(experiments, "adapt", fake_adapt)
    output = tmp_path / "runs"
    results = experiments.train_experiments(
        datasets,
        output,
        tmp_path / "cache",
        epochs=2,
        batch_size=4,
        on_progress=progress.append,
    )
    assert progress == ["rated", "social"]
    assert calls[0][0] is datasets["rated"]["splits"]
    assert calls[1][0] is datasets["social"]["splits"]
    assert [call[1].name for call in calls] == ["rated", "social"]
    assert all(call[3] == {"epochs": 2, "batch_size": 4} for call in calls)
    assert set(json.loads((output / "experiments.json").read_text())) == {
        "rated",
        "social",
    }
    assert results["rated"]["metrics"] != results["social"]["metrics"]
    for name in results:
        saved = json.loads((output / name / "dataset.json").read_text())
        assert saved["source_sha256"] == datasets[name]["source_sha256"]
    with pytest.raises(FileExistsError):
        experiments.train_experiments(datasets, output, tmp_path / "cache")


def test_first_result_survives_second_training_failure(tmp_path, monkeypatch):
    datasets = experiments.load_experiment_data(inputs(tmp_path))

    def fake_adapt(splits, output, cache, **kwargs):
        if output.name == "social":
            raise RuntimeError("interrupted social run")
        output.mkdir()
        return object(), object(), {"done": True}

    monkeypatch.setattr(experiments, "adapt", fake_adapt)
    output = tmp_path / "runs"
    with pytest.raises(RuntimeError, match="interrupted"):
        experiments.train_experiments(datasets, output, tmp_path / "cache")
    assert set(json.loads((output / "experiments.json").read_text())) == {"rated"}


def test_numbers_input_explains_csv_export(tmp_path):
    paths = inputs(tmp_path)
    numbers = tmp_path / "social.numbers"
    numbers.write_bytes(b"not a csv")
    paths["social"] = numbers
    with pytest.raises(ValueError, match="como CSV"):
        experiments.load_experiment_data(paths)


def test_numbers_csv_title_capitalized_filename_and_spanish_label(tmp_path):
    paths = inputs(tmp_path)
    frame = pd.read_csv(paths["social"])
    frame.loc[0, "label"] = " Negativo "
    exported = tmp_path / "Social.csv"
    exported.write_text(
        "social_reviews_to_label\n" + frame.to_csv(index=False), encoding="utf-8-sig"
    )
    paths["social"].unlink()
    datasets = experiments.load_experiment_data(paths)
    assert datasets["social"]["source_csv"] == str(exported.resolve())
    rows = pd.concat(datasets["social"]["splits"].values())
    assert len(rows) == 60
    assert rows.set_index("record_id").loc["social-negative-0", "label"] == "negative"
