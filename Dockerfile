# First build stage: get a specific version of the uv tools.
FROM ghcr.io/astral-sh/uv:0.12.7 AS uv

# Keep the Python patch version aligned with the verified embedding cache identity.
FROM python:3.12.13-slim-bookworm AS workspace

# Copy only the uv executables from the first stage into the final image.
COPY --from=uv /uv /uvx /usr/local/bin/

# Install the project environment outside /workspace so the repo mount won't hide it.
# Copy dependency files instead of linking them to uv's cache.
# PATH selects this environment's Python/commands; unbuffered output shows logs promptly.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1

# HDBSCAN may build from source on ARM, so include C/C++ build tools.
# Install Git and HTTPS trust certificates; remove package lists to reduce image size.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

# Working directory for subsequent build instructions and the running container.
WORKDIR /workspace

# Copy the build context (repo files allowed by .dockerignore) into the image.
# At runtime, Compose's bind mount exposes your live host repo at this same path.
COPY . .

# Install our package, core dependencies, and the ml/dev groups using uv.lock.
# --locked fails if the lockfile needs updating; generate it before building.
RUN uv sync --locked --group ml --group dev

# Start and supervise the Jupyter and Marimo servers.
# If a server exits, the supervisor stops the other server and reports failure.
CMD ["python", "-m", "voc_dev.services"]

# Tree-sitter's official Linux binaries require a newer glibc than Bookworm.
# Build its pinned, checksum-verified crate here; Rust never enters the runtime.
FROM rust:1.90.0-slim-bookworm AS editor-builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY scripts/install-editor.sh /tmp/install-editor.sh
RUN sh /tmp/install-editor.sh /opt tree-sitter

# Opt-in editor tooling layers onto exactly the existing Python environment.
FROM workspace AS nvim
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl unzip ripgrep fd-find \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/bin/fdfind /usr/local/bin/fd
COPY scripts/install-editor.sh /tmp/install-editor.sh
RUN sh /tmp/install-editor.sh /opt && rm /tmp/install-editor.sh
COPY --from=editor-builder /opt/tree-sitter/bin/tree-sitter /usr/local/bin/tree-sitter
ENV PATH="${PATH}:/opt/node/bin"
COPY scripts/nvim /usr/local/bin/nvim
COPY scripts/nvim-init.lua /opt/voc-editor/init.lua
COPY scripts/nvim-plugins.lua /opt/voc-editor/lua/voc_container.lua
RUN chmod 0755 /usr/local/bin/nvim

# Plain docker build retains the original workspace behavior and tool set.
FROM workspace AS default
