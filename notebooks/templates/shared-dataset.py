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
    # Explore a published VOC dataset

    Hello, world! This is a Marimo notebook, saved as a Python file in your repo.
    Click **Load dataset** to read the configured source (S3 for the team setup).
    This example reads data; it does not collect reviews or publish a release.
    """)
    return


@app.cell
def _(mo):
    load_dataset = mo.ui.run_button(label="Load dataset")
    load_dataset
    return (load_dataset,)


@app.cell
def _(co, load_dataset, mo):
    mo.stop(not load_dataset.value, mo.md("Click **Load dataset** to begin."))
    snapshot = co.get_dataset()
    return (snapshot,)


@app.cell
def _(mo, snapshot):
    mo.md(f"""
    **Snapshot:** `{snapshot.info.get("release_id", snapshot.info["artifact_id"])}`

    **Rows:** {len(snapshot.data):,} · **Columns:** {len(snapshot.data.columns)}

    Each click resolves the latest publication. Pin its release ID when you need
    to return to exactly this shared dataset: `co.get_dataset(version=release_id)`.
    """)
    return


@app.cell
def _(mo, snapshot):
    mo.ui.table(snapshot.data.head(20), label="First 20 reviews")
    return


@app.cell
def _(mo, snapshot):
    platform_counts = (
        snapshot.data["platform"]
        .value_counts()
        .rename_axis("platform")
        .reset_index(name="reviews")
    )
    mo.ui.table(platform_counts, label="Reviews by platform")
    return


if __name__ == "__main__":
    app.run()
