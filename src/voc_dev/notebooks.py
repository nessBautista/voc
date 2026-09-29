"""Create repo notebooks and emit editor links for the host browser launcher."""

import json
import os
import re
import sys
import uuid
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlencode

import click

NOTEBOOKS = Path(__file__).resolve().parents[2] / "notebooks"
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{p}{n}" for p in ("COM", "LPT") for n in range(1, 10)
}


def notebook_path(root, name, kind):
    suffix = ".py" if kind == "marimo" else ".ipynb"
    parts = name.split("/")
    if not parts or any(
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", part)
        or part.endswith(".")
        or part.split(".")[0].upper() in RESERVED
        for part in parts
    ):
        raise ValueError(
            "Use a relative name such as teammate/my-notebook (letters, digits, hyphens, underscores; forward slashes)."
        )
    relative = PurePosixPath(*parts)
    if relative.suffix and relative.suffix != suffix:
        raise ValueError(f"Use no extension, or {suffix} for this notebook type")
    if not relative.suffix:
        relative = relative.with_suffix(suffix)
    target = root / str(relative)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Notebook must stay inside the repository notebooks directory")
    return target, str(relative)


def jupyter_content():
    def cell(kind, source):
        result = {
            "id": uuid.uuid4().hex[:8],
            "cell_type": kind,
            "metadata": {},
            "source": source.splitlines(keepends=True),
        }
        if kind == "code":
            result.update(execution_count=None, outputs=[])
        return result

    return (
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {
                    "kernelspec": {
                        "display_name": "Python 3",
                        "language": "python",
                        "name": "python3",
                    }
                },
                "cells": [
                    cell(
                        "markdown",
                        "# My VOC notebook\nRun cells with Shift+Enter. The shared dataset is read only when you run its cell.\n",
                    ),
                    cell("code", 'print("hello world")\n'),
                    cell(
                        "code",
                        'from voc import collector as co\nsnapshot = co.get_dataset()\nprint(snapshot.info.get("release_id", snapshot.info["artifact_id"]))\nprint(snapshot.data.shape)\nsnapshot.data.head()\n',
                    ),
                ],
            },
            indent=2,
        )
        + "\n"
    )


def create_notebook(root, name, kind):
    target, relative = notebook_path(root, name, kind)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = (
        (root / "templates/shared-dataset.py").read_text()
        if kind == "marimo"
        else jupyter_content()
    )
    try:
        with target.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    except FileExistsError:
        if not target.is_file():
            raise ValueError("The notebook path exists but is not a file") from None
        return relative, False
    return relative, True


def editor_url(kind, port, relative=None, token=""):
    if not 1 <= port <= 65535:
        raise ValueError("Notebook host port must be between 1 and 65535")
    query = {}
    if kind == "marimo":
        path = "/"
        if relative:
            query["file"] = relative
        if token:
            query["access_token"] = token
    else:
        path = "/lab" + ("/tree/" + quote(relative, safe="/") if relative else "")
        if token:
            query["token"] = token
    return f"http://127.0.0.1:{port}{path}" + ("?" + urlencode(query) if query else "")


@click.command()
@click.argument("kind", type=click.Choice(["marimo", "jupyter"]))
@click.option(
    "--name", default=None, help="Create or reuse this relative notebook path."
)
@click.option("--port", required=True, type=click.IntRange(1, 65535))
def main(kind, name, port):
    relative = None
    try:
        if name is not None:
            relative, created = create_notebook(NOTEBOOKS, name, kind)
            print(
                ("Created " if created else "Opening existing ")
                + "notebooks/"
                + relative,
                file=sys.stderr,
            )
        # stdout is captured by the host launcher, never echoed with its login token.
        click.echo(
            editor_url(kind, port, relative, os.environ.get("JUPYTER_TOKEN", ""))
        )
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error


if __name__ == "__main__":
    main()
