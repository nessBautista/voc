"""BERTopic batch discovery over existing embeddings, returning portable results."""

from dataclasses import asdict, dataclass
from importlib.metadata import version

import numpy as np

from voc.analysis import Keyword, ReviewAssignment, TopicRecord
from voc.embeddings.contracts import canonical_json
from voc_ml.analysis import AnalysisConfig, AnalysisInput

from .reduction import CachedReduction, ReductionResult, reduce_inputs

# Small baseline vocabulary policy from the original Spanish-review prototype.
# Accents remain intact. Policy changes must be recorded with each experiment.
STOP_WORDS = tuple(
    sorted(
        [
            "a",
            "al",
            "con",
            "de",
            "del",
            "el",
            "ella",
            "en",
            "es",
            "esta",
            "este",
            "ha",
            "la",
            "las",
            "lo",
            "los",
            "me",
            "mi",
            "mis",
            "para",
            "por",
            "que",
            "se",
            "su",
            "sus",
            "un",
            "una",
            "unos",
            "unas",
            "y",
            "yo",
        ]
    )
)


@dataclass(frozen=True)
class TopicDiscovery:
    assignments: tuple[ReviewAssignment, ...]
    topics: tuple[TopicRecord, ...]
    report: dict

    def validate(self, inputs):
        inputs.validate()
        ids = inputs.reviews.record_id.tolist()
        if [a.record_id for a in self.assignments] != ids:
            raise ValueError(
                "Topic assignments must preserve every selected review in order"
            )
        for row in (*self.assignments, *self.topics):
            row.__post_init__()
        membership = {a.record_id: a.topic_id for a in self.assignments}
        topic_ids = [t.topic_id for t in self.topics]
        if len(topic_ids) != len(set(topic_ids)) or set(topic_ids) != set(
            membership.values()
        ):
            raise ValueError("Topic records must match discovered assignment groups")
        for topic in self.topics:
            if any(membership.get(rid) != topic.topic_id for rid in topic.evidence_ids):
                raise ValueError("Representative evidence must belong to its topic")
        if self.report["input_id"] != inputs.info["input_id"]:
            raise ValueError("Topic report input identity mismatch")
        canonical_json(self.as_dict())

    def as_dict(self):
        """JSON-compatible records for workflow artifacts, without model pickles."""
        return asdict(self)


def _model(inputs, config, reduction):
    from bertopic import BERTopic
    from bertopic.vectorizers import ClassTfidfTransformer
    from hdbscan import HDBSCAN
    from sklearn.feature_extraction.text import CountVectorizer

    return BERTopic(
        # Preserve Spanish accents instead of applying English-only text cleanup.
        language="multilingual",
        # No encoder here: discover() passes our existing embeddings to fit_transform.
        embedding_model=None,
        # Supply verified UMAP coordinates for this exact batch, without refitting.
        umap_model=CachedReduction(inputs, reduction, config.reduction),
        # Group nearby reviews using the configured density settings. Unassigned
        # reviews keep label -1. Use one worker for core-distance calculations.
        hdbscan_model=HDBSCAN(**asdict(config.clustering), core_dist_n_jobs=1),
        # Count terms in the texts grouped by topic to build its vocabulary.
        vectorizer_model=CountVectorizer(
            stop_words=list(STOP_WORDS),  # Ignore our baseline Spanish stop words.
            ngram_range=(1, 2),  # Include single words and two-word phrases.
            min_df=1,  # Keep terms occurring in at least one topic document.
        ),
        # Weight terms by how characteristic they are of a topic, reducing the
        # influence of very frequent words in the normalized term counts.
        ctfidf_model=ClassTfidfTransformer(reduce_frequent_words=True),
        top_n_words=10,  # Retain up to ten representative terms per topic.
        # Keep assigned-cluster membership strengths, without computing the
        # larger matrix of membership scores for every review against every topic.
        calculate_probabilities=False,
        verbose=False,  # Suppress BERTopic's detailed progress logging.
    )


def _records(inputs, model, labels, strengths):
    count = len(inputs.reviews)
    labels, strengths = np.asarray(labels), np.asarray(strengths)
    clusters = np.asarray(model.hdbscan_model.labels_)
    if (
        labels.shape != (count,)
        or clusters.shape != (count,)
        or labels.dtype.kind not in "iu"
        or clusters.dtype.kind not in "iu"
    ):
        raise ValueError(
            "BERTopic must return one integer topic and cluster ID per review"
        )
    if (
        (labels < -1).any()
        or (clusters < -1).any()
        or not np.array_equal(labels == -1, clusters == -1)
    ):
        raise ValueError("BERTopic must preserve HDBSCAN outlier membership")
    if (
        strengths.shape != (count,)
        or strengths.dtype.kind not in "fiu"
        or not np.isfinite(strengths).all()
        or ((strengths < 0) | (strengths > 1)).any()
    ):
        raise ValueError(
            "BERTopic must return one finite membership strength in [0, 1] per review"
        )
    assignments = tuple(
        ReviewAssignment(rid, int(tid), float(strength))
        for rid, tid, strength in zip(inputs.reviews.record_id, labels, strengths)
    )
    topics = []
    for tid in sorted(set(labels.tolist())):
        terms = model.get_topic(tid) or []
        if any(not np.isfinite(weight) or weight < 0 for _, weight in terms):
            raise ValueError("BERTopic keyword weights must be finite and nonnegative")
        keywords = tuple(
            Keyword(str(term), float(weight))
            for term, weight in terms
            if term and weight > 0
        )
        members = inputs.reviews.loc[labels == tid]
        # Deduplicate evidence text only, never the fitting corpus. With duplicate
        # texts, use the first member ID in pinned input order deterministically.
        first_ids = {}
        for rid, text in zip(members.record_id, members.model_text):
            first_ids.setdefault(text, rid)
        evidence = []
        for text in model.get_representative_docs(tid) or []:
            if text not in first_ids:
                raise ValueError(
                    "BERTopic representative text is not a member of its topic"
                )
            if first_ids[text] not in evidence:
                evidence.append(first_ids[text])
        topics.append(
            TopicRecord(
                tid,
                keywords,
                tuple(evidence),
                name="Unassigned"
                if tid == -1
                else " / ".join(k.term for k in keywords[:3]) or f"Topic {tid}",
                description="Reviews without a discovered group."
                if tid == -1
                else "Keyword-based topic description; no LLM interpretation.",
            )
        )
    pairs = sorted(set(zip(clusters.tolist(), labels.tolist())))
    if len(pairs) != len(set(clusters)) or len(pairs) != len(set(labels)):
        raise ValueError("Expected a one-to-one HDBSCAN-to-BERTopic topic mapping")
    return (
        assignments,
        tuple(topics),
        [{"cluster_id": c, "topic_id": t} for c, t in pairs],
    )


def discover(inputs, config, *, cache_dir=None, reduction=None):
    """Fit the BERTopic backend after validating pinned input and configuration.

    Supply a verified ReductionResult to reuse it directly, or let this function
    load/compute the keyed cache. No embeddings, sentiment, LLM calls, databases
    or workflow services are created here. Coordinates are for clustering, not
    a separately fitted 2D visualization.
    """
    if not isinstance(inputs, AnalysisInput) or not isinstance(config, AnalysisConfig):
        raise TypeError("Expected AnalysisInput and AnalysisConfig")
    config.__post_init__()
    config.reduction.__post_init__()
    config.clustering.__post_init__()
    config.selection.__post_init__()
    config.validate_row_count(len(inputs.reviews))
    inputs.validate()
    selected = inputs.info["selection"]
    if (
        config.selection.kind != selected["kind"]
        or config.selection.seed != selected["seed"]
        or (
            config.selection.kind != "full"
            and config.selection.size != selected["size"]
        )
    ):
        raise ValueError("Configured selection differs from pinned input")
    if not np.isfinite(inputs.vectors).all():
        raise ValueError("Input vectors must be finite")
    if reduction is None:
        reduction = reduce_inputs(inputs, config.reduction, cache_dir=cache_dir)
    elif not isinstance(reduction, ReductionResult):
        raise TypeError("reduction must be ReductionResult")
    reduction.validate(inputs, config.reduction)
    model = _model(inputs, config, reduction)
    try:
        labels, strengths = model.fit_transform(
            inputs.reviews.model_text.tolist(), embeddings=inputs.vectors
        )
    except ValueError as error:
        if "empty vocabulary" in str(error).lower():
            raise ValueError(
                "BERTopic found no usable vocabulary for the configured representation"
            ) from error
        raise
    assignments, topics, mapping = _records(inputs, model, labels, strengths)
    result = TopicDiscovery(
        assignments,
        topics,
        {
            "schema_version": "voc-topic-discovery-v1",
            "backend": "bertopic",
            "input_id": inputs.info["input_id"],
            "configuration": config.as_dict(),
            "reduction": reduction.info,
            "reduction_reused": reduction.reused,
            "rows": len(assignments),
            "topic_count": sum(t.topic_id >= 0 for t in topics),
            "unassigned_reviews": sum(a.topic_id == -1 for a in assignments),
            "cluster_to_topic": mapping,
            "software": {
                name: version(name)
                for name in ("bertopic", "hdbscan", "scikit-learn", "numpy", "scipy")
            },
            "representation": {
                "policy": "multilingual-spanish-stopwords-bigrams-v1",
                "language": "multilingual",
                "stop_words": list(STOP_WORDS),
                "ngram_range": [1, 2],
                "min_df": 1,
                "top_n_words": 10,
                "reduce_frequent_words": True,
            },
            "representative_policy": "BERTopic c-TF-IDF representatives; first member ID per distinct text; absent evidence stays empty",
            "membership_strength": "HDBSCAN membership strength, not calibrated sentiment or topic confidence",
            "model_persistence": "portable result records only; cached reducer is batch-only",
        },
    )
    result.validate(inputs)
    return result
