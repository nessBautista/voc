# First build stage: get a specific version of the uv tools.
FROM ghcr.io/astral-sh/uv:0.12.7 AS uv

# Base for our final image: Python 3.12 on a minimal Debian Bookworm system.
FROM python:3.12-slim-bookworm

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
