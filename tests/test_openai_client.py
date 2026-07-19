from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from openai import APIStatusError

from backend.contracts import ProviderFailure
from backend.openai_client import ResponsesClient, load_env_file


class FakeResponse:
    def to_dict(self) -> dict[str, object]:
        return {"id": "resp_sdk_contract", "output": [], "status": "completed"}


class FakeResponsesResource:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **payload: object) -> FakeResponse:
        self.calls.append(payload)
        return FakeResponse()


class FakeOpenAI:
    def __init__(self) -> None:
        self.responses = FakeResponsesResource()


class FailingResponsesResource:
    def create(self, **payload: object) -> FakeResponse:
        del payload
        response = httpx.Response(
            400,
            request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
            headers={"x-request-id": "req_safe_failure"},
        )
        raise APIStatusError(
            "provider rejected request",
            response=response,
            body={
                "error": {
                    "type": "invalid_request_error",
                    "code": "invalid_value",
                    "param": "input[1].encrypted_content",
                    "message": "must never be copied into public runtime evidence",
                }
            },
        )


class FailingOpenAI:
    def __init__(self) -> None:
        self.responses = FailingResponsesResource()


class OpenAISdkTransportTests(unittest.TestCase):
    def test_responses_payload_is_forwarded_through_the_official_sdk_surface(self) -> None:
        sdk = FakeOpenAI()
        transport = ResponsesClient(
            "test-browser-owned-key-for-sdk-contract",
            sdk_client=sdk,
        )
        result = transport.create(
            {"model": "gpt-5.6", "input": [{"role": "user", "content": "safe"}], "store": False}
        )
        self.assertEqual(result["id"], "resp_sdk_contract")
        self.assertEqual(sdk.responses.calls[0]["model"], "gpt-5.6")
        self.assertIs(sdk.responses.calls[0]["store"], False)

    def test_request_size_is_bounded_before_the_sdk_is_called(self) -> None:
        sdk = FakeOpenAI()
        transport = ResponsesClient(
            "test-browser-owned-key-for-sdk-contract",
            sdk_client=sdk,
        )
        with self.assertRaises(ProviderFailure):
            transport.create({"input": "x" * (256 * 1024)})
        self.assertEqual(sdk.responses.calls, [])

    def test_provider_error_keeps_only_safe_nested_diagnostics(self) -> None:
        transport = ResponsesClient(
            "test-browser-owned-key-for-sdk-contract",
            sdk_client=FailingOpenAI(),
        )
        with self.assertRaises(ProviderFailure) as caught:
            transport.create({"model": "gpt-5.6", "input": "safe"})
        self.assertEqual(
            caught.exception.detail,
            {
                "provider_code": "invalid_value",
                "provider_type": "invalid_request_error",
                "provider_param": "input[1].encrypted_content",
                "status": 400,
                "request_id": "req_safe_failure",
            },
        )
        self.assertNotIn("must never", str(caught.exception.detail))

    def test_env_loader_ignores_operator_api_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / ".env"
            env_file.write_text(
                "OPENAI_API_KEY=must-never-load\nOPENAI_MODEL=gpt-5.6\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                load_env_file(env_file)
                self.assertNotIn("OPENAI_API_KEY", os.environ)
                self.assertEqual(os.environ["OPENAI_MODEL"], "gpt-5.6")

    def test_transport_does_not_expose_the_key_in_its_repr(self) -> None:
        sdk = FakeOpenAI()
        key = "test-browser-owned-key-for-repr-contract"
        transport = ResponsesClient(key, sdk_client=sdk)
        self.assertNotIn(key, repr(transport))
        self.assertNotIn(key, json.dumps(transport.__dict__, default=str))


if __name__ == "__main__":
    unittest.main()
