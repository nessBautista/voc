# Whole-review sentiment

`robertuito.py` loads a pinned Spanish RoBERTuito classifier on CPU. It applies
pysentimiento's Spanish preprocessing, records truncation at 128 tokens, and
returns a prediction for each review. The notebook selects non-outlier topics;
keywords and generated labels are not classifier inputs.

`contracts.py` builds and validates the topic and sentiment JSON artifacts.
Aggregation joins exact review IDs within one dataset release, counts each
classified review equally, and reports unsuccessful predictions separately.
Model probabilities are uncalibrated. This is whole-review sentiment, not ABSA.

The two JSON schemas mirror the vault's workflow 01 contract. The notebook
`notebooks/nb/topic-modelling-v0.py` demonstrates classification, inspection and
local export. It keeps the original topic object separate from its sentiment
summary, joined by topic run ID and topic ID. Feature interpretation remains
pending until the structured interpretation stage is implemented.
