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
    # Starting EDA: raw reviews

    Load the latest published raw snapshot before workable-dataset preparation. It includes additional source-specific columns, some of which may be empty for other platforms.

    VOC handles dataset retrieval and caching. This notebook reads data; it does not collect new reviews or publish a release.

    Rerunning `version="latest"` can load a newer publication. The raw and prepared starter notebooks resolve latest independently.
    """)
    return


@app.cell
def _(co):
    # Load the raw snapshot from the latest published release.
    # This reads existing data; it does not fetch new reviews from providers.
    # VOC uses the configured source and handles retrieval and caching.
    snapshot = co.get_dataset(stage="raw", version="latest")

    # A pandas DataFrame containing review/post records and the broader
    # collection schema, including source-specific fields.
    df = snapshot.data

    print(f"Rows: {df.shape[0]:,} | Columns: {df.shape[1]}")
    return (df,)


@app.cell
def _(df, mo):
    # List the available columns, then preview the records.
    print(df.columns.tolist())
    mo.ui.table(df.head(), label="First five raw records")
    return


@app.cell
def _(df, mo):
    # Inspect a reproducible sample from across the dataset.
    mo.ui.table(
        df[["platform", "text"]].sample(
            n=min(5, len(df)), random_state=42
        ),
        label="Sample review text",
    )
    return


if __name__ == "__main__":
    app.run()
