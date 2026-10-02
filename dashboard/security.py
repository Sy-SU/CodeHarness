"""A single output boundary for trace, file, configuration and API text."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping, Optional

from dotenv import dotenv_values

from agent.workspace.task import SECRET_KEYS, _redact


def secret_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return (normalized in SECRET_KEYS or normalized.endswith(("_key", "_token", "_secret", "_password"))
            or "authorization" in normalized or "credential" in normalized)


class Sanitizer:
    def __init__(self, *, environ: Optional[Mapping[str, str]] = None, env_file: Optional[Path] = None):
        values = dict(os.environ if environ is None else environ)
        if env_file is not None:
            try:
                local = dotenv_values(env_file, interpolate=False)
                values = {**local, **values}
            except (OSError, ValueError):
                pass
        self._secrets = sorted({str(value) for key, value in values.items()
                                if secret_key(key) and value}, key=len, reverse=True)

    def text(self, value: str) -> str:
        for secret in self._secrets:
            value = value.replace(secret, "<redacted>")
        value = _redact(value)
        # Historical files can contain assignments as well as structured keys.
        return re.sub(r'''(?ix)(["']?(?:[\w-]*(?:api[_-]?key|api[_-]?token|secret|password)|authorization)["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;}\n]+)''',
                      r"\1<redacted>", value)

    def value(self, value: Any, *, compact: bool = False, depth: int = 0) -> Any:
        if depth > 12:
            return "<nested data omitted>"
        if isinstance(value, dict):
            return {self.text(str(key)): ("<redacted>" if secret_key(str(key)) else
                    self.value(item, compact=compact, depth=depth + 1))
                    for key, item in (list(value.items())[:300] if compact else value.items())}
        if isinstance(value, (tuple, list)):
            return [self.value(item, compact=compact, depth=depth + 1) for item in (value[:300] if compact else value)]
        if isinstance(value, str):
            safe = self.text(value)
            return safe[:1200] + "… <truncated>" if compact and len(safe) > 1200 else safe
        if value is None or isinstance(value, (bool, int)):
            return value
        if isinstance(value, float):
            import math
            return value if math.isfinite(value) else None
        return self.text(str(value))
