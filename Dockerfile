# Name this first build stage "uv". It supplies the pinned uv tools below.
# This stage is a source of files; it is not a second running container.
FROM ghcr.io/astral-sh/uv:0.12.7 AS uv

# Start the final image with Python 3.12 on a small Debian Bookworm base.
FROM python:3.12-slim-bookworm

# Copy uv (dependency manager) and uvx (tool runner) from the first stage.
# /usr/local/bin is on PATH, so you can invoke these tools by name.
COPY --from=uv /uv /uvx /usr/local/bin/

# Set defaults available during the build and when the container runs:
# UV_PROJECT_ENVIRONMENT: install project dependencies into /opt/venv.
#   Keeping it outside /workspace prevents a later source mount from hiding it.
# UV_LINK_MODE: copy package files from uv's cache into the environment.
# PATH: prefer the environment's Python and installed commands.
# PYTHONUNBUFFERED: show Python output promptly in container logs.
# VOC_DATA_DIR: tell our application where runtime data belongs.
# ZENML_CONFIG_PATH: put ZenML client configuration/state under /data.
# ZENML_CUSTOM_SOURCE_ROOT: identify /workspace as the project source root.
# ZENML_ANALYTICS_OPT_IN: disable ZenML analytics.
# MLFLOW_DISABLE_AGENT_HINT: suppress MLflow's agent hint message.
# These paths alone do not preserve data; Compose will mount storage at /data.
# Each trailing backslash continues this same ENV instruction on the next line.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 \
    VOC_DATA_DIR=/data ZENML_CONFIG_PATH=/data/zenml-client \
    ZENML_CUSTOM_SOURCE_ROOT=/workspace \
    ZENML_ANALYTICS_OPT_IN=false MLFLOW_DISABLE_AGENT_HINT=1

# Install OS tools during the image build: Git and HTTPS trust certificates.
# Skip recommended extras and remove downloaded package lists to save space.
# && runs each following command only if the previous command succeeds.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Use /workspace for subsequent build commands and as the runtime directory.
# Docker creates this directory if it does not exist.
WORKDIR /workspace

# Copy the build context (our repository) into /workspace in the image.
# Files excluded by .dockerignore are not copied. This is not a live mount.
COPY . .

# Install the project dependencies plus the ml and dev dependency groups.
# --locked requires uv.lock to agree with pyproject.toml; fail if it needs updating.
# At this lesson's package=false stage, our own project is not installed as a package.
RUN uv sync --locked --group ml --group dev

# Default command when a container starts: keep it running so you can enter it.
# Unlike RUN, CMD does not execute during the image build.
# This placeholder does not start dashboards; a later lesson replaces it.
CMD ["python", "-m", "src.mlops.services"]
