"""Select local publication storage without loading Operator configuration."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    publications_dir: Path
    state_dir: Path
    web_dir: Path | None = None
    planner_mode: str = "fixture"
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "anthropic/claude-sonnet-5.5"
    llm_api_key: str = field(default="", repr=False)
    internal_api_url: str = "http://127.0.0.1:8000"
    turn_timeout_seconds: float = 45

    mcp_enabled: bool = False
    mcp_token: str = field(default="", repr=False)
    mcp_allowed_hosts: tuple[str, ...] = (
        "127.0.0.1:8091",
        "localhost:8091",
        "host.docker.internal:8091",
        "127.0.0.1:8000",
    )
    mcp_allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:8091",
        "http://localhost:8091",
    )

    def __post_init__(self):
        # An enabled transport must never fall back to anonymous access.
        if self.mcp_enabled and (
            not 32 <= len(self.mcp_token) <= 512
            or any(not 33 <= ord(character) <= 126 for character in self.mcp_token)
            or self.mcp_token.startswith("op://")
            or self.mcp_token == self.llm_api_key
        ):
            raise ValueError(
                "MCP requires a separate resolved token of 32–512 ASCII characters"
            )
        for values in (self.mcp_allowed_hosts, self.mcp_allowed_origins):
            if not values or any(not value or "*" in value for value in values):
                raise ValueError(
                    "MCP allowlists must contain explicit hosts and origins"
                )

        if self.planner_mode not in ("fixture", "model"):
            raise ValueError("VOC_PLANNER_MODE must be fixture or model")
        if not 1 <= self.turn_timeout_seconds <= 120:
            raise ValueError("VOC_TURN_TIMEOUT_SECONDS must be between 1 and 120")
        for value, internal in (
            (self.internal_api_url, True),
            (self.llm_base_url, False),
        ):
            parsed = urlsplit(value)
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("Service URLs must not contain credentials or queries")
            if internal:
                if (
                    parsed.scheme != "http"
                    or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
                    or parsed.path not in ("", "/")
                ):
                    raise ValueError("Internal API must use a loopback HTTP origin")
            elif parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("Model provider must use HTTPS")

    @classmethod
    def from_env(cls):
        """Read only application settings; do not create directories or load secrets."""
        locations = []
        for name, default in (
            ("VOC_PUBLICATIONS_DIR", "/app/catalog"),
            ("VOC_APP_STATE_DIR", "/app/state"),
        ):
            value = os.environ.get(
                name,
                default,
            )
            # Deployment paths are local absolute paths, never storage URLs.
            if not value or "://" in value or not Path(value).is_absolute():
                raise ValueError(f"{name} must be an absolute local directory")
            locations.append(Path(value))
        # API-only development remains possible when no web build is configured.
        web = os.environ.get("VOC_WEB_DIR")
        if web is not None and (not web or "://" in web or not Path(web).is_absolute()):
            raise ValueError("VOC_WEB_DIR must be an absolute local directory")
        enabled = os.environ.get("VOC_MCP_ENABLED", "false")
        if enabled not in ("true", "false"):
            raise ValueError("VOC_MCP_ENABLED must be true or false")
        return cls(
            *locations,
            mcp_enabled=enabled == "true",
            mcp_token=os.environ.get("VOC_MCP_TOKEN", ""),
            mcp_allowed_hosts=tuple(
                value.strip()
                for value in os.environ.get(
                    "VOC_MCP_ALLOWED_HOSTS", ",".join(cls.mcp_allowed_hosts)
                ).split(",")
            ),
            mcp_allowed_origins=tuple(
                value.strip()
                for value in os.environ.get(
                    "VOC_MCP_ALLOWED_ORIGINS", ",".join(cls.mcp_allowed_origins)
                ).split(",")
            ),
            web_dir=Path(web) if web is not None else None,
            planner_mode=os.environ.get("VOC_PLANNER_MODE", "fixture"),
            llm_base_url=os.environ.get(
                "VOC_LLM_BASE_URL", "https://openrouter.ai/api/v1"
            ),
            llm_model=os.environ.get("VOC_LLM_MODEL", "anthropic/claude-sonnet-5.5"),
            llm_api_key=os.environ.get("VOC_LLM_API_KEY", ""),
            internal_api_url=os.environ.get(
                "VOC_INTERNAL_API_URL", "http://127.0.0.1:8000"
            ),
            turn_timeout_seconds=float(
                os.environ.get("VOC_TURN_TIMEOUT_SECONDS", "45")
            ),
        )
