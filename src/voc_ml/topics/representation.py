"""Refine keywords with BERTopic while keeping saved topic membership fixed."""

import math
import time
import tomllib
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from voc.analysis.contracts import Keyword
from voc.embeddings.profiles import resolve_profile
from voc.paths import data_root

from .bertopic import STOP_WORDS


@dataclass(frozen=True)
class RepresentationConfig:
    method: str = "ctfidf"
    ngram_min: int = 1
    ngram_max: int = 2
    top_n_words: int = 10
    candidate_words: int = 30
    min_df: int = 1
    reduce_frequent_words: bool = True
    diversity: float = 0.3
    nr_repr_docs: int = 5
    nr_samples: int = 500
    batch_size: int = 32

    def __post_init__(self):
        if self.method not in ("ctfidf", "keybert", "mmr", "keybert_mmr"):
            raise ValueError("method must be ctfidf, keybert, mmr or keybert_mmr")
        bounds = {
            "ngram_min": (1, 3),
            "ngram_max": (1, 3),
            "top_n_words": (1, 30),
            "candidate_words": (1, 100),
            "min_df": (1, 100),
            "nr_repr_docs": (1, 20),
            "nr_samples": (1, 1000),
            "batch_size": (1, 256),
        }
        for name, (low, high) in bounds.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer in [{low}, {high}]")
        if self.ngram_min > self.ngram_max:
            raise ValueError("ngram_min must not exceed ngram_max")
        if self.candidate_words < self.top_n_words:
            raise ValueError("candidate_words must be at least top_n_words")
        if self.nr_repr_docs > self.nr_samples:
            raise ValueError("nr_repr_docs must not exceed nr_samples")
        if type(self.reduce_frequent_words) is not bool:
            raise ValueError("reduce_frequent_words must be boolean")
        if type(self.diversity) not in (float, int) or not 0 <= self.diversity <= 1:
            raise ValueError("diversity must be a finite number between zero and one")


def load_config(path):
    with Path(path).open("rb") as stream:
        raw = tomllib.load(stream)
    if set(raw) - RepresentationConfig.__dataclass_fields__.keys():
        raise ValueError("Unknown representation configuration option")
    return RepresentationConfig(**raw)


def _embedder(saved, config, local_files_only):
    """Lazy adapter: only semantic representations need local model weights."""
    import numpy as np
    from bertopic.backend import BaseEmbedder

    from voc.embeddings.contracts import encoder_identity
    from voc_ml.embeddings.sentence_encoder import SentenceEncoder, encoder_descriptor

    profile = resolve_profile(saved.manifest.inputs["embedding"]["profile_name"])
    if profile.profile_id != saved.manifest.inputs["embedding"]["profile_id"]:
        raise ValueError(
            "Representation encoder profile differs from parent embeddings"
        )
    descriptor = encoder_descriptor(profile)
    started = time.perf_counter()
    encoder = SentenceEncoder(
        profile,
        data_root() / "models/sentence-transformers",
        local_files_only=local_files_only,
    )

    class TermEmbedder(BaseEmbedder):
        def __init__(self):
            super().__init__()
            self.cache = {}
            self.report = {
                "profile_id": profile.profile_id,
                "encoder_id": encoder_identity(descriptor),
                "descriptor": descriptor,
                "model_load_seconds": time.perf_counter() - started,
                "inference_seconds": 0.0,
                "requested_texts": 0,
                "encoded_unique": 0,
                "reused_texts": 0,
                "truncated_unique": 0,
                "scope": "candidate terms and representative examples only; in-memory cache",
            }

        def embed(self, documents, verbose=False):
            texts = list(documents)
            missing = list(dict.fromkeys(t for t in texts if t not in self.cache))
            self.report["requested_texts"] += len(texts)
            self.report["reused_texts"] += len(texts) - len(missing)
            if missing:
                began = time.perf_counter()
                vectors, lengths = encoder.encode(missing, batch_size=config.batch_size)
                self.report["inference_seconds"] += time.perf_counter() - began
                if (
                    vectors.shape != (len(missing), profile.dimensions)
                    or not np.isfinite(vectors).all()
                ):
                    raise ValueError("Invalid representation encoder output")
                self.cache.update(zip(missing, vectors))
                self.report["encoded_unique"] += len(missing)
                self.report["truncated_unique"] += sum(
                    int(n > profile.max_length) for n in lengths
                )
            return np.asarray([self.cache[t] for t in texts], dtype=np.float32)

    return TermEmbedder()


def refine(saved, config, *, local_files_only=True):
    """Return new TopicRecords and evidence. No fit, reduction or sentiment call.

    BERTopic.update_topics recomputes class TF-IDF for supplied memberships.
    Titles, descriptions and original evidence IDs belong to the saved result,
    so keyword experiments deliberately preserve them, including reviewed titles.
    """
    from bertopic import BERTopic
    from bertopic.representation import KeyBERTInspired, MaximalMarginalRelevance
    from bertopic.vectorizers import ClassTfidfTransformer
    from sklearn.feature_extraction.text import CountVectorizer

    began = time.perf_counter()
    docs = saved.reviews.model_text.tolist()
    labels = [a.topic_id for a in saved.assignments]
    vectorizer = CountVectorizer(
        stop_words=list(STOP_WORDS),
        min_df=config.min_df,
        ngram_range=(config.ngram_min, config.ngram_max),
    )
    # Check vocabulary on grouped documents, as BERTopic does. Fail before loading
    # weights if every term was filtered out. min_df counts topics, not reviews.
    grouped = [
        " ".join(d for d, label in zip(docs, labels) if label == tid)
        for tid in sorted(set(labels))
    ]
    try:
        vectorizer.fit(grouped)
    except ValueError as error:
        raise ValueError(f"No usable topic vocabulary: {error}") from error

    embedder = (
        None
        if config.method == "ctfidf"
        else _embedder(saved, config, local_files_only)
    )
    representation = None
    if config.method in ("keybert", "keybert_mmr"):
        representation = KeyBERTInspired(
            top_n_words=config.candidate_words
            if config.method == "keybert_mmr"
            else config.top_n_words,
            nr_candidate_words=config.candidate_words,
            nr_repr_docs=config.nr_repr_docs,
            nr_samples=config.nr_samples,
        )
    if config.method in ("mmr", "keybert_mmr"):
        mmr = MaximalMarginalRelevance(
            diversity=config.diversity, top_n_words=config.top_n_words
        )
        representation = [representation, mmr] if representation else mmr

    model = BERTopic(embedding_model=embedder, language="multilingual", verbose=False)
    # Restore only membership, not a serialized fitted model. update_topics uses
    # these exact IDs, including -1. It never calls UMAP or HDBSCAN.
    model.topics_ = labels.copy()
    model.update_topics(
        docs,
        top_n_words=config.candidate_words
        if config.method == "mmr"
        else config.top_n_words,
        vectorizer_model=vectorizer,
        ctfidf_model=ClassTfidfTransformer(
            reduce_frequent_words=config.reduce_frequent_words
        ),
        representation_model=representation,
    )
    if model.topics_ != labels or set(model.topic_representations_) != set(labels):
        raise ValueError("Representation update changed topic membership")
    topics = []
    for topic in saved.topics:
        # The shared Keyword contract uses positive weights. KeyBERT scores are
        # cosine similarities, so zero/negative terms are omitted and disclosed.
        keywords = tuple(
            Keyword(term, float(weight))
            for term, weight in model.get_topic(topic.topic_id)
            if term.strip() and math.isfinite(weight) and weight > 0
        )[: config.top_n_words]
        topics.append(replace(topic, keywords=keywords))
    return tuple(topics), {
        "configuration": asdict(config),
        "sampling_policy": "BERTopic representative-document sampling uses fixed seed 42",
        "duration_seconds": time.perf_counter() - began,
        "encoder": embedder.report if embedder else None,
        "weight_policy": "positive finite c-TF-IDF weights, or cosine similarities for KeyBERT; nonpositive/empty terms omitted",
        "title_policy": "preserve parent names and descriptions",
        "evidence_policy": "preserve original member review IDs; not regenerated representatives",
        "empty_keyword_topics": [t.topic_id for t in topics if not t.keywords],
        "changed_topics": sum(
            a.keywords != b.keywords for a, b in zip(saved.topics, topics)
        ),
    }
