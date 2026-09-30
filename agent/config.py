"""CodeHarness client configuration loaded without MiniOJ internals."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import urlsplit


class ClientConfigurationError(ValueError):
    """Raised when required client configuration is absent or malformed."""


@dataclass(frozen=True)
class ClientSettings:
    """Environment-backed settings needed by the macOS client only."""

    oj_base_url: str
    oj_api_token: str = field(repr=False)
    model_config: Path = Path("config/models.yaml")

    @classmethod
    def from_environment(
        cls,
        environ: Optional[Mapping[str, str]] = None,
        *,
        model_config_override: Optional[str] = None,
    ) -> "ClientSettings":
        values = os.environ if environ is None else environ
        base_url = values.get("OJ_BASE_URL", "").strip().rstrip("/")
        api_token = values.get("OJ_API_TOKEN", "").strip()
        model_config = (
            model_config_override
            or values.get("MODEL_CONFIG", "").strip()
            or "config/models.yaml"
        )

        missing = [
            name
            for name, value in (("OJ_BASE_URL", base_url), ("OJ_API_TOKEN", api_token))
            if not value
        ]
        if missing:
            raise ClientConfigurationError(
                f"Missing required client configuration: {', '.join(missing)}"
            )

        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ClientConfigurationError(
                "OJ_BASE_URL must be an absolute http:// or https:// URL"
            )
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ClientConfigurationError(
                "OJ_BASE_URL must not contain credentials, a query, or a fragment"
            )

        return cls(
            oj_base_url=base_url,
            oj_api_token=api_token,
            model_config=Path(model_config),
        )
