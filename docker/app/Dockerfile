# Compile the browser application; Node is only a build tool.
FROM node:22-bookworm-slim AS web-build
WORKDIR /build/web
COPY apps/voc-app/web/package.json apps/voc-app/web/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY apps/voc-app/web/index.html apps/voc-app/web/tsconfig.json apps/voc-app/web/vite.config.ts ./
COPY apps/voc-app/web/src ./src
# The analysis page is a second Vite entry; its HTML lives beside index.html.
COPY apps/voc-app/web/dashboard ./dashboard
RUN npm run build

# Use the same uv version as the Operator, with an independent dependency lock.
FROM ghcr.io/astral-sh/uv:0.12.7 AS uv
FROM python:3.12-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/app-venv UV_LINK_MODE=copy
WORKDIR /opt/voc

# Keep the relative library path expected by the server's pyproject.toml.
COPY pyproject.toml ./
COPY src ./src
COPY apps/voc-app/server/pyproject.toml apps/voc-app/server/uv.lock ./apps/voc-app/server/
COPY apps/voc-app/server/src ./apps/voc-app/server/src
RUN uv sync --project apps/voc-app/server --locked --no-dev --no-editable --no-cache

FROM python:3.12-slim-bookworm
ENV PATH="/opt/app-venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    VOC_PUBLICATIONS_DIR=/app/catalog VOC_APP_STATE_DIR=/app/state VOC_WEB_DIR=/app/web

# Install only the resolved runtime environment; no checkout or Operator tooling.
COPY --from=build /opt/app-venv /opt/app-venv
COPY --from=web-build /build/web/dist /app/web
RUN groupadd --gid 10001 vocapp \
    && useradd --uid 10001 --gid vocapp --no-create-home --home-dir /app vocapp \
    && mkdir -p /app/catalog /app/state \
    && chown vocapp:vocapp /app /app/catalog /app/state
WORKDIR /app
USER vocapp
EXPOSE 8000
CMD ["uvicorn", "voc_app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers"]
