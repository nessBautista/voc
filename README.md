# VOC

A Python workspace for collecting and preparing customer reviews. Voice of Customer.

## Functional Organization
Code is grouped into:
- `src/voc_ml` (ML workflows): All functionality related to ML traning.
- `src/voc` (dataset framework): voc will serve future renderizations for dashboards/MIS
- `src/voc_dev` (workspace tools): development tools we require to manage this development environment.

## Demo

[Review topics and sentiment](notebooks/demos/README.md): a Marimo Studio presentation
backed by a pinned S3 checkpoint. Includes topic modelling, interpretation and
review sentiment. See the demo guide for setup and its browser URL.
