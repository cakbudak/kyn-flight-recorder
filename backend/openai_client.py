"""Official OpenAI Responses transport for browser-owned, per-operation keys."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from .contracts import ProviderFailure


MAX_REQUEST_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
ALLOWED_ENVIRONMENT_KEYS = frozenset(
    {
        "OPENAI_MODEL",
        "KYN_DATABASE_PATH",
        "KYN_PUBLIC_MODEL_CALLS_PER_HOUR",
        "KYN_WORKSPACE_MODEL_CALL_LIMIT",
    }
)
PROVIDER_FIELD_RE = re.compile(r"^[A-Za-z0-9_.\[\]-]+$")


def _safe_provider_field(value: Any, *, maximum: int = 120) -> str | None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        return None
    return value if PROVIDER_FIELD_RE.fullmatch(value) else None


def load_env_file(path: str | Path) -> None:
    """Load non-secret runtime settings; operator OpenAI keys are ignored."""

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
    """Small adapter that keeps the official SDK as the sole network transport."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 45.0,
        sdk_client: Any | None = None,
    ) -> None:
        normalized_key = api_key.strip() if isinstance(api_key, str) else ""
        if len(normalized_key) < 20 or any(character.isspace() for character in normalized_key):
            raise ProviderFailure("a valid browser-owned OpenAI API key is required")
        if not isinstance(timeout_seconds, (int, float)) or not 1 <= timeout_seconds <= 180:
            raise ProviderFailure("OpenAI timeout is outside the supported range")
        # Do not retain the key separately. The SDK client is ephemeral for the bounded
        # model action and its repr does not expose credentials.
        self._client = sdk_client or OpenAI(
            api_key=normalized_key,
            timeout=float(timeout_seconds),
            max_retries=1,
        )
        self.timeout_seconds = float(timeout_seconds)

    @property
    def configured(self) -> bool:
        return True

    def create(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        material = dict(payload)
        encoded = json.dumps(
            material, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            raise ProviderFailure("OpenAI request exceeds the runtime limit")
        try:
            response = self._client.responses.create(**material)
        except APIStatusError as error:
            body = getattr(error, "body", None)
            envelope: Mapping[str, Any] = {}
            if isinstance(body, dict):
                nested = body.get("error")
                envelope = nested if isinstance(nested, dict) else body
            provider_type = _safe_provider_field(envelope.get("type"), maximum=80)
            provider_code = (
                _safe_provider_field(envelope.get("code"), maximum=80)
                or provider_type
                or "api_status_error"
            )
            provider_param = _safe_provider_field(envelope.get("param"))
            detail: dict[str, Any] = {
                "provider_code": provider_code,
                "status": int(error.status_code),
                "request_id": getattr(error, "request_id", None),
            }
            if provider_type is not None:
                detail["provider_type"] = provider_type
            if provider_param is not None:
                detail["provider_param"] = provider_param
            raise ProviderFailure(
                f"OpenAI request failed with status {error.status_code}",
                detail=detail,
            ) from None
        except (APITimeoutError, APIConnectionError) as error:
            raise ProviderFailure(
                "OpenAI request could not be completed",
                detail={"provider_code": type(error).__name__},
            ) from None
        except Exception as error:
            # Test seams may raise plain transport errors. Do not leak their message,
            # because third-party exception text can contain request material.
            raise ProviderFailure(
                "OpenAI SDK transport failed",
                detail={"provider_code": type(error).__name__},
            ) from None

        if isinstance(response, dict):
            result = dict(response)
        elif hasattr(response, "model_dump"):
            result = response.model_dump(mode="json")
        elif hasattr(response, "to_dict"):
            result = response.to_dict()
        else:
            raise ProviderFailure("OpenAI returned an invalid response envelope")
        if not isinstance(result, dict):
            raise ProviderFailure("OpenAI returned an invalid response envelope")
        request_id = getattr(response, "_request_id", None)
        if isinstance(request_id, str) and request_id:
            result["_request_id"] = request_id[:128]
        response_size = len(
            json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        if response_size > MAX_RESPONSE_BYTES:
            raise ProviderFailure("OpenAI response exceeds the runtime limit")
        return result


class UnavailableResponsesClient:
    """Fail-loud default used until a browser supplies its key for a model action."""

    configured = False

    def create(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        del payload
        raise ProviderFailure("a browser-owned OpenAI API key is required for this action")
