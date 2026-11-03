"""Refine saved memberships with local embeddings, without fitting clusters."""

import math
from dataclasses import asdict, dataclass
from importlib.metadata import version
from time import monotonic

import numpy as np

from voc.embeddings.shared.profiles import resolve_profile

from .models import build_ctfidf, build_vectorizer


@dataclass(frozen=True)
class RepresentationConfig:
    method: str = "keybert"
    candidate_words: int = 30
    top_n_words: int = 10
    nr_repr_docs: int = 5
    nr_samples: int = 500
    batch_size: int = 32
    diversity: float = 0.3

    def __post_init__(self):
        if self.method not in ("ctfidf", "keybert", "keybert_mmr"):
            raise ValueError("Unknown representation method")
        for name, low, high in (
            ("candidate_words", 1, 100),
            ("top_n_words", 1, 30),
            ("nr_repr_docs", 1, 20),
            ("nr_samples", 1, 1000),
            ("batch_size", 1, 64),
        ):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"Invalid {name}")
        if (
            self.top_n_words > self.candidate_words
            or self.nr_repr_docs > self.nr_samples
        ):
            raise ValueError("Representation candidate/sample bounds are inconsistent")
        if (
            type(self.diversity) not in (int, float)
            or not math.isfinite(self.diversity)
            or not 0 <= self.diversity <= 1
        ):
            raise ValueError("Invalid MMR diversity")

    def as_dict(self):
        return asdict(self)


def local_embedder(profile_info, config, models_directory):
    from bertopic.backend import BaseEmbedder

    from voc_ml.embeddings.encoding.sentence_encoder import (
        SentenceEncoder,
        encoder_descriptor,
    )

    profile = resolve_profile()
    if profile_info != profile.as_dict():
        raise ValueError("Representation profile differs from parent")
    began = monotonic()
    encoder = SentenceEncoder(profile, models_directory, local_files_only=True)

    class CachedEmbedder(BaseEmbedder):
        def __init__(self):
            super().__init__()
            self.cache = {}
            self.report = {
                "descriptor": encoder_descriptor(profile),
                "load_seconds": monotonic() - began,
                "inference_seconds": 0.0,
                "requested_texts": 0,
                "encoded_unique": 0,
                "reused_texts": 0,
                "truncated_unique": 0,
            }

        def embed(self, documents, verbose=False):
            texts = list(documents)
            missing = list(dict.fromkeys(t for t in texts if t not in self.cache))
            self.report["requested_texts"] += len(texts)
            self.report["reused_texts"] += len(texts) - len(missing)
            if missing:
                began = monotonic()
                vectors, lengths = encoder.encode(missing, batch_size=config.batch_size)
                if (
                    vectors.shape != (len(missing), profile.dimensions)
                    or not np.isfinite(vectors).all()
                ):
                    raise ValueError("Invalid semantic representation vectors")
                self.report["inference_seconds"] += monotonic() - began
                self.report["encoded_unique"] += len(missing)
                self.report["truncated_unique"] += sum(
                    int(n > profile.max_length) for n in lengths
                )
                self.cache.update(zip(missing, vectors, strict=True))
            return np.asarray([self.cache[t] for t in texts], dtype=np.float32)

    return CachedEmbedder()


def refine(parent, config, models_directory, *, embedder_factory=None):
    config.__post_init__()
    from bertopic import BERTopic
    from bertopic.cluster import BaseCluster
    from bertopic.dimensionality import BaseDimensionalityReduction
    from bertopic.representation import KeyBERTInspired, MaximalMarginalRelevance

    policy = parent.execution["fit"]["representation"]
    if policy["top_n_words"] != config.top_n_words:
        raise ValueError("Representation word limit must match parent")
    docs = parent.reviews.model_text.tolist()
    labels = parent.assignments.topic_id.tolist()
    vectorizer = build_vectorizer(policy)
    # Check vocabulary before loading local weights.
    grouped = {}
    for text, label in zip(docs, labels, strict=True):
        grouped.setdefault(label, []).append(text)
    vectorizer.fit([" ".join(grouped[t]) for t in sorted(grouped)])
    del grouped
    factory = embedder_factory if embedder_factory is not None else local_embedder
    embedder = (
        None
        if config.method == "ctfidf"
        else factory(parent.info["input"]["profile"], config, models_directory)
    )
    representation = None
    if config.method != "ctfidf":
        representation = KeyBERTInspired(
            top_n_words=config.candidate_words
            if config.method == "keybert_mmr"
            else config.top_n_words,
            nr_candidate_words=config.candidate_words,
            nr_repr_docs=config.nr_repr_docs,
            nr_samples=config.nr_samples,
            random_state=42,
        )
        if config.method == "keybert_mmr":
            representation = [
                representation,
                MaximalMarginalRelevance(
                    diversity=config.diversity, top_n_words=config.top_n_words
                ),
            ]
    # No fitted model serialization or clustering components are needed.
    model = BERTopic(
        language="multilingual",
        embedding_model=embedder,
        umap_model=BaseDimensionalityReduction(),
        hdbscan_model=BaseCluster(),
        verbose=False,
    )
    model.topics_ = labels.copy()
    model.update_topics(
        docs,
        top_n_words=config.top_n_words,
        vectorizer_model=vectorizer,
        ctfidf_model=build_ctfidf(policy),
        representation_model=representation,
    )
    if model.topics_ != labels or set(model.topic_representations_) != set(labels):
        raise ValueError("Refinement changed membership")
    topics, omitted = [], 0
    for topic in parent.topics:
        keywords, seen = [], set()
        for term, weight in model.get_topic(topic["topic_id"]):
            if (
                term.strip()
                and math.isfinite(weight)
                and weight > 0
                and term not in seen
            ):
                keywords.append({"term": term, "weight": float(weight)})
                seen.add(term)
            else:
                omitted += 1
        topics.append({**topic, "keywords": keywords[: config.top_n_words]})
    return topics, {
        "configuration": config.as_dict(),
        "representation": policy,
        "encoder": embedder.report if embedder else None,
        "software": {
            name: version(name)
            for name in ("bertopic", "scikit-learn", "numpy", "scipy")
        },
        "internal_sample_seed": 42,
        "omitted_terms": omitted,
        "weight_policy": "positive finite terms only, c-TF-IDF or cosine similarity, distinct scales",
        "evidence_policy": "unchanged parent evidence IDs, independent of internal KeyBERT examples",
        "counts": parent.info["counts"],
    }
