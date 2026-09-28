# Marimo stores display expressions and cell returns in its notebook format.
# ruff: noqa: B018, PLR1711
import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    from voc import collector as co

    return co, mo


@app.cell
def _(mo):
    mo.md("""
    # Starting EDA: prepared reviews

    Load the latest published workable dataset and explore its text. `text` contains source text; `model_text` is the text prepared for downstream NLP.

    VOC handles dataset retrieval and caching. This notebook reads data; it does not collect new reviews or publish a release.

    Rerunning `version="latest"` can load a newer publication. The raw and prepared starter notebooks resolve latest independently.
    """)
    return


@app.cell
def _(co):
    # Load the prepared dataset from the latest published release.
    # VOC uses the configured source and handles retrieval and caching.
    snapshot = co.get_dataset(stage="prepared", version="latest")

    # A pandas DataFrame: rows represent reviews or social posts;
    # columns contain text and associated metadata.
    df = snapshot.data

    print(f"Rows: {df.shape[0]:,} | Columns: {df.shape[1]}")
    return (df,)


@app.cell
def _(df, mo):
    # Preview source text alongside the text prepared for NLP.
    mo.ui.table(
        df[["platform", "title", "text", "model_text", "rating"]].head(),
        label="First five prepared records",
    )
    return


@app.cell
def _(df, mo):
    # Inspect a reproducible sample from across the dataset.
    mo.ui.table(
        df[["platform", "model_text"]].sample(
            n=min(5, len(df)), random_state=42
        ),
        label="Sample review text",
    )
    return


if __name__ == "__main__":
    app.run()
