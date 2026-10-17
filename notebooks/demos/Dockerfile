# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.12.7 AS uv
FROM python:3.12-slim-bookworm
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 MARIMO_STUDIO_EDIT_ROOT=marimo
# HDBSCAN may need compilation on ARM laptops.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /demo
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --group dev --no-install-project
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --group dev
# Compose exposes this editor only on the host's loopback interface.
CMD ["marimo", "edit", "workflow01.py", "--headless", "--host", "0.0.0.0", "--port", "2718", "--no-token"]
