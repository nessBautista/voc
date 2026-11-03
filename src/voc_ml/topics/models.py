"""Lazy model construction shared by production and explanatory notebooks.

These factories construct components only. Fitting belongs to discovery.
"""

STOP_WORDS = sorted(
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


def representation_policy(config):
    return {
        "policy": "spanish-stopwords-bigrams-ctfidf-v1",
        "stop_words": list(STOP_WORDS),
        "ngram_range": [1, 2],
        "min_df": 1,
        "reduce_frequent_words": True,
        "top_n_words": config.top_n_words,
    }


def build_reducer(config):
    config.__post_init__()
    from umap import UMAP

    return UMAP(
        n_neighbors=config.n_neighbors,
        n_components=config.n_components,
        min_dist=config.min_dist,
        metric="cosine",
        random_state=config.random_state,
        n_jobs=1,
    )


def build_clusterer(config):
    config.__post_init__()
    from hdbscan import HDBSCAN

    return HDBSCAN(
        min_cluster_size=config.min_cluster_size,
        min_samples=config.min_samples,
        metric="euclidean",
        cluster_selection_method=config.cluster_selection_method,
        core_dist_n_jobs=1,
    )


def build_vectorizer(policy):
    from sklearn.feature_extraction.text import CountVectorizer

    return CountVectorizer(
        stop_words=list(policy["stop_words"]),
        ngram_range=tuple(policy["ngram_range"]),
        min_df=policy["min_df"],
    )


def build_ctfidf(policy):
    from bertopic.vectorizers import ClassTfidfTransformer

    return ClassTfidfTransformer(reduce_frequent_words=policy["reduce_frequent_words"])


def build_topic_model(config, *, policy=None):
    config.__post_init__()
    from bertopic import BERTopic

    policy = representation_policy(config) if policy is None else policy
    if policy["top_n_words"] != config.top_n_words:
        raise ValueError("Representation and topic configuration differ")
    return BERTopic(
        embedding_model=None,
        language="multilingual",
        umap_model=build_reducer(config),
        hdbscan_model=build_clusterer(config),
        vectorizer_model=build_vectorizer(policy),
        ctfidf_model=build_ctfidf(policy),
        top_n_words=config.top_n_words,
        calculate_probabilities=False,
        verbose=False,
    )
