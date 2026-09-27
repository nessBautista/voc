# Marimo's notebook format uses display expressions and explicit cell returns.
# ruff: noqa: PLR1711
import marimo

__generated_with = "0.24.2"
app = marimo.App(width="full")


@app.cell
def _():
    import json

    import marimo as mo
    import matplotlib.pyplot as plt
    import pandas as pd

    from voc import collector as co
    from voc.paths import data_root

    return co, data_root, json, mo, pd, plt


@app.cell
def _(mo):
    mo.md("""
    # See how your review dataset grows

    Compare **n**, the latest shared dataset release, with **n−1**, your previous
    release. Here, n names a dataset version, not an individual review.

    Click **Compare releases**. Android uses blue; iOS uses orange. Solid segments
    are reviews already present in the earlier release and still present now.
    **Striped segments are newly added review IDs.** Each platform gets its own
    chart and vertical scale so the larger Android dataset does not hide iOS growth.

    By default, the previous ID comes from your update-and-compare notebook's
    saved checkpoint, only when that checkpoint ends at the current shared latest.
    You can supply an earlier release ID below instead. Shared manifests do not
    yet contain a previous-release link; the notebook does not guess from timestamps.
    This notebook only reads published datasets.
    """)
    return


@app.cell
def _(mo):
    previous_input = mo.ui.text(
        label="Previous release ID (optional checkpoint override)", full_width=True
    )
    compare_button = mo.ui.run_button(label="Compare releases")
    mo.vstack([previous_input, compare_button])
    return compare_button, previous_input


@app.cell
def _(co, compare_button, data_root, json, mo, previous_input):
    mo.stop(
        not compare_button.value, mo.md("Click **Compare releases** to load the data.")
    )
    after = co.get_dataset(source="s3", version="latest")
    previous_id = previous_input.value.strip()
    baseline_origin = "Manually selected baseline"
    if not previous_id:
        _path = data_root() / "notebook-checkpoints/update-comparison.json"
        _state = json.loads(_path.read_text()) if _path.exists() else {}
        mo.stop(
            _state.get("after_release_id") != after.info["release_id"]
            or not _state.get("before_release_id"),
            mo.md("""
            **Choose the previous release.** There is no saved comparison ending
            at this latest release. Enter the earlier shared release ID above,
            then click **Compare releases** again. Use your publisher's comparison
            checkpoint or release output, not a ZenML artifact ID.
            """),
        )
        previous_id = _state["before_release_id"]
        baseline_origin = "Previous release from the saved update comparison"
    mo.stop(
        previous_id == after.info["release_id"],
        mo.md(
            "Choose a different earlier release; both IDs currently name the same dataset."
        ),
    )
    before = co.get_dataset(source="s3", version=previous_id)
    mo.stop(
        before.info["rules"] != after.info["rules"],
        mo.md(
            "Preparation rules differ. Choose matching rules to isolate collection changes."
        ),
    )
    return after, baseline_origin, before


@app.cell
def _(pd, plt):
    def compare_frames(earlier, current):
        for frame in (earlier, current):
            if frame["record_id"].isna().any() or not frame["record_id"].is_unique:
                raise ValueError(
                    "Comparison requires non-null, unique record_id values"
                )
        if list(earlier.columns) != list(current.columns):
            raise ValueError("Comparison requires matching schemas")
        old = earlier.set_index("record_id")
        new = current.set_index("record_id")
        common = new.index.intersection(old.index)
        equal = new.loc[common].eq(old.loc[common]) | (
            new.loc[common].isna() & old.loc[common].isna()
        )
        edited_ids = common[~equal.fillna(False).all(axis=1)]
        added = current.loc[~current["record_id"].isin(earlier["record_id"])].copy()
        removed = earlier.loc[~earlier["record_id"].isin(current["record_id"])].copy()
        retained = current.loc[current["record_id"].isin(earlier["record_id"])].copy()
        assert len(current) - len(earlier) == len(added) - len(removed)
        return added, removed, retained, edited_ids

    def monthly_counts(frame, platform):
        dates = pd.to_datetime(
            frame.loc[frame["platform"].eq(platform), "record_date"],
            errors="coerce",
            format="mixed",
            utc=True,
        )
        # Keep missing/invalid dates visible rather than silently dropping reviews.
        return dates.dt.strftime("%Y-%m").fillna("Unknown date").value_counts()

    def growth_chart(retained, added, platform, title, color):
        old_counts = monthly_counts(retained, platform)
        new_counts = monthly_counts(added, platform)
        months = sorted(set(old_counts.index) | set(new_counts.index))
        fig, ax = plt.subplots(figsize=(12, 4.5), layout="constrained")
        if not months:
            ax.text(
                0.5,
                0.5,
                "No reviews in this platform",
                ha="center",
                va="center",
                transform=ax.transAxes,
            )
        else:
            old_values = old_counts.reindex(months, fill_value=0)
            new_values = new_counts.reindex(months, fill_value=0)
            ax.bar(
                months, old_values, color=color, label="Already in n−1; retained in n"
            )
            ax.bar(
                months,
                new_values,
                bottom=old_values,
                facecolor="white",
                edgecolor=color,
                hatch="////",
                linewidth=1.2,
                label="New IDs in n",
            )
            for i, (old_count, new_count) in enumerate(zip(old_values, new_values)):
                if new_count:
                    ax.annotate(
                        f"+{new_count:,}",
                        (i, old_count + new_count),
                        xytext=(0, 4),
                        textcoords="offset points",
                        ha="center",
                        fontsize=9,
                    )
            ax.set_ylim(0, max(1, float((old_values + new_values).max())) * 1.18)
            ax.legend(loc="upper left")
            ax.tick_params(axis="x", rotation=45)
        ax.set(
            title=title,
            xlabel="Review month in the latest snapshot (UTC)",
            ylabel="Reviews",
        )
        ax.set_ylim(bottom=0)
        ax.set_axisbelow(True)
        ax.grid(axis="y", alpha=0.2)
        plt.close(fig)
        return fig

    return compare_frames, growth_chart


@app.cell
def _(after, before, compare_frames):
    added, removed, retained, edited_ids = compare_frames(before.data, after.data)
    return added, edited_ids, removed, retained


@app.cell
def _(added, after, baseline_origin, before, edited_ids, mo, removed):
    mo.md(f"""
    **n−1:** `{before.info["release_id"]}` — {len(before.data):,} reviews

    **n:** `{after.info["release_id"]}` — {len(after.data):,} reviews

    {baseline_origin}. Both snapshots are pinned for this comparison.

    **Added:** {len(added):,} · **Edited existing IDs:** {len(edited_ids):,} ·
    **Removed:** {len(removed):,} · **Net growth:** {len(after.data) - len(before.data):+,}

    The bars sum to the current dataset for each platform. Solid segments exclude
    removed IDs and use the latest values of retained reviews, including edits.
    An edited date can move a retained review between months. Stripes measure IDs
    newly present in the dataset, not only reviews written since the previous run:
    collection can also discover older reviews. No stripes means no added IDs.
    """)
    return


@app.cell
def _(added, growth_chart, retained):
    growth_chart(retained, added, "google_play", "Android · Google Play", "#1f77b4")
    return


@app.cell
def _(added, growth_chart, retained):
    growth_chart(retained, added, "app_store", "iOS · App Store", "#ff7f0e")
    return


@app.cell
def _(added, after, before, edited_ids, mo, pd, removed, retained):
    _rows = []
    for _platform in sorted(
        set(before.data["platform"].dropna()) | set(after.data["platform"].dropna())
    ):
        _rows.append(
            {
                "platform": _platform,
                "previous": int(before.data["platform"].eq(_platform).sum()),
                "current": int(after.data["platform"].eq(_platform).sum()),
                "added": int(added["platform"].eq(_platform).sum()),
                "removed": int(removed["platform"].eq(_platform).sum()),
                "edited (current platform)": int(
                    (
                        retained["platform"].eq(_platform)
                        & retained["record_id"].isin(edited_ids)
                    ).sum()
                ),
            }
        )
    mo.ui.table(pd.DataFrame(_rows), label="Reconciliation across all sources")
    return


@app.cell
def _(added, mo):
    mo.ui.table(
        added.loc[
            added["platform"].isin(["google_play", "app_store"]),
            ["record_id", "platform", "record_date", "rating", "text"],
        ],
        label="New Android and iOS reviews (striped segments)",
    )
    return


if __name__ == "__main__":
    app.run()
