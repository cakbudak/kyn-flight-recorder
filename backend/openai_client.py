"""Minimal server-side OpenAI Responses transport with no runtime dependency."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from .contracts import ProviderFailure


RESPONSES_URL = "https://api.openai.com/v1/responses"
ALLOWED_ENVIRONMENT_KEYS = frozenset(
    {
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "KYN_DATABASE_PATH",
        "KYN_PUBLIC_MODEL_CALLS_PER_HOUR",
        "KYN_WORKSPACE_MODEL_CALL_LIMIT",
    }
)


def load_env_file(path: str | Path) -> None:
    """Load only runtime allow-listed keys without echoing values."""

    env_path = Path(path)
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or key not in ALLOWED_ENVIRONMENT_KEYS or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


class ResponsesClient:
    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 45.0,
        url: str = RESPONSES_URL,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ProviderFailure("OPENAI_API_KEY is not configured")
        if url != RESPONSES_URL:
            raise ProviderFailure("OpenAI endpoint override is not permitted")
        self._api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds
        self.url = url

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def create(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > 256 * 1024:
            raise ProviderFailure("OpenAI request exceeds the runtime limit")
        request = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": "kyn-flight-recorder-buildweek/2",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as error:
            provider_code = "http_error"
            try:
                error_body = error.read(64 * 1024)
                parsed = json.loads(error_body)
                candidate = parsed.get("error", {}).get("type")
                if isinstance(candidate, str) and candidate:
                    provider_code = candidate[:80]
            except (OSError, ValueError, TypeError, AttributeError):
                pass
            raise ProviderFailure(
                f"OpenAI request failed with status {error.code}",
                detail={"provider_code": provider_code, "status": error.code},
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ProviderFailure(
                "OpenAI request could not be completed",
                detail={"provider_code": type(error).__name__},
            ) from None
        if len(raw) > 2 * 1024 * 1024:
            raise ProviderFailure("OpenAI response exceeds the runtime limit")
        try:
            result = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderFailure("OpenAI returned invalid JSON") from None
        if not isinstance(result, dict):
            raise ProviderFailure("OpenAI returned an invalid response envelope")
        return result
