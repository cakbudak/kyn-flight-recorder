"""Same-origin JSON API adapter for the standalone control plane."""

from __future__ import annotations

import json
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from http import HTTPStatus
from http.cookies import SimpleCookie
from typing import Any, Callable, Mapping

from .contracts import (
    Forbidden,
    NotFound,
    OpenAIKeyRequired,
    PayloadTooLarge,
    RateLimited,
    RuntimeErrorBase,
    Unauthorized,
)
from .service import ControlPlane


MAX_API_BODY = 256 * 1024
RESOURCE_ID = r"([a-z]+_[0-9a-f]{32})"
WEBHOOK_PATH = re.compile(r"/api/v1/hooks/(hook_[A-Za-z0-9_-]{32,80})")


@dataclass(frozen=True)
class ApiRequest:
    method: str
    path: str
    headers: Mapping[str, str]
    body: bytes
    remote_address: str
    scheme: str
    host: str
    body_too_large: bool = False

    def header(self, name: str, default: str = "") -> str:
        """Return an HTTP header using the protocol's case-insensitive semantics."""

        wanted = name.casefold()
        for key, value in self.headers.items():
            if key.casefold() == wanted:
                return value
        return default


@dataclass(frozen=True)
class ApiResponse:
    status: int
    payload: Mapping[str, Any]
    headers: Mapping[str, str] = field(default_factory=dict)


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._entries: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def require(self, key: str, *, limit: int, window_seconds: float, units: int = 1) -> None:
        now = time.monotonic()
        threshold = now - window_seconds
        with self._lock:
            entries = self._entries[key]
            while entries and entries[0] <= threshold:
                entries.popleft()
            if len(entries) + units > limit:
                raise RateLimited("request budget is exhausted; try again later")
            entries.extend(now for _ in range(units))


class ApiApplication:
    def __init__(
        self,
        control_plane: ControlPlane,
        *,
        workspace_model_call_limit: int = 12,
        global_model_call_limit_per_hour: int = 120,
        address_model_call_limit_per_hour: int = 24,
        concurrent_model_actions: int = 2,
    ) -> None:
        self.control_plane = control_plane
        self.workspace_model_call_limit = workspace_model_call_limit
        self.global_model_call_limit_per_hour = global_model_call_limit_per_hour
        self.address_model_call_limit_per_hour = address_model_call_limit_per_hour
        self.rate_limiter = SlidingWindowLimiter()
        self.model_slots = threading.BoundedSemaphore(concurrent_model_actions)
        self.workspace_model_locks: dict[str, threading.Lock] = {}
        self.workspace_model_locks_guard = threading.Lock()

    def dispatch(self, request: ApiRequest) -> ApiResponse:
        try:
            if request.body_too_large:
                raise PayloadTooLarge("request body exceeds 256 KiB")
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                is_public_webhook = WEBHOOK_PATH.fullmatch(request.path) is not None
                if not is_public_webhook:
                    self._require_same_origin(request)
                self.rate_limiter.require(
                    (
                        f"webhook:{request.remote_address}"
                        if is_public_webhook
                        else f"mutation:{request.remote_address}"
                    ),
                    limit=60 if is_public_webhook else 120,
                    window_seconds=60,
                )
            if request.method == "GET":
                return self._get(request)
            if request.method == "POST":
                return self._post(request)
            return self._error(
                HTTPStatus.METHOD_NOT_ALLOWED,
                "method_not_allowed",
                "API route does not allow this method",
                headers={"Allow": "GET, POST"},
            )
        except RuntimeErrorBase as error:
            code = "origin_rejected" if isinstance(error, Forbidden) else error.code
            return self._error(error.http_status, code, str(error), detail=error.detail)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._error(
                HTTPStatus.BAD_REQUEST,
                "invalid_json",
                "request body is not valid UTF-8 JSON",
            )
        except sqlite_error_types() as error:
            del error
            return self._error(
                HTTPStatus.CONFLICT,
                "database_conflict",
                "the requested state change conflicted with a database invariant",
            )

    def _get(self, request: ApiRequest) -> ApiResponse:
        if request.path == "/api/v1/workspace":
            workspace_id = self._workspace_id(request)
            return self._ok(self.control_plane.snapshot(workspace_id))
        if request.path == "/api/v1/studio":
            workspace_id = self._workspace_id(request)
            return self._ok(self.control_plane.studio_snapshot(workspace_id))
        match = re.fullmatch(rf"/api/v1/studio/actions/{RESOURCE_ID}", request.path)
        if match:
            workspace_id = self._workspace_id(request)
            return self._ok(self.control_plane.get_studio_action(workspace_id, match.group(1)))
        match = re.fullmatch(rf"/api/v1/studio/flows/{RESOURCE_ID}", request.path)
        if match:
            workspace_id = self._workspace_id(request)
            return self._ok(self.control_plane.get_studio_flow(workspace_id, match.group(1)))
        match = re.fullmatch(rf"/api/v1/studio/runs/{RESOURCE_ID}", request.path)
        if match:
            workspace_id = self._workspace_id(request)
            return self._ok(self.control_plane.get_studio_run(workspace_id, match.group(1)))
        match = re.fullmatch(rf"/api/v1/runs/{RESOURCE_ID}", request.path)
        if match:
            workspace_id = self._workspace_id(request)
            return self._ok(self.control_plane.get_run(workspace_id, match.group(1)))
        match = re.fullmatch(rf"/api/v1/flows/{RESOURCE_ID}", request.path)
        if match:
            workspace_id = self._workspace_id(request)
            return self._ok(self.control_plane.get_flow(workspace_id, match.group(1)))
        raise NotFound("API route was not found")

    def _post(self, request: ApiRequest) -> ApiResponse:
        body = self._json_body(request)
        if request.path == "/api/v1/workspaces":
            self._require_exact_keys(body, set())
            self.rate_limiter.require(
                f"workspace:address:{request.remote_address}",
                limit=12,
                window_seconds=3600,
            )
            bootstrap = self.control_plane.create_workspace(seed=True)
            secure = request.scheme == "https"
            cookie = (
                f"kyn_workspace={bootstrap['workspace_token']}; Path=/; HttpOnly; "
                "SameSite=Strict; Max-Age=86400"
            ) + ("; Secure" if secure else "")
            public = {
                "workspace_id": bootstrap["workspace_id"],
                "snapshot": bootstrap["snapshot"],
            }
            return self._ok(public, status=HTTPStatus.CREATED, headers={"Set-Cookie": cookie})

        webhook_match = WEBHOOK_PATH.fullmatch(request.path)
        if webhook_match:
            return self._ok(
                self.control_plane.fire_studio_webhook(webhook_match.group(1), body),
                status=HTTPStatus.ACCEPTED,
            )

        workspace_id = self._workspace_id(request)
        if request.path == "/api/v1/studio/actions":
            self._require_keys(
                body,
                {
                    "name",
                    "slug",
                    "description",
                    "kind",
                    "input_schema",
                    "output_schema",
                    "config",
                    "agent_version_id",
                },
                {"outcomes"},
            )
            return self._ok(
                self.control_plane.create_action(workspace_id, **body),
                status=HTTPStatus.CREATED,
            )
        match = re.fullmatch(
            rf"/api/v1/studio/actions/{RESOURCE_ID}/versions", request.path
        )
        if match:
            self._require_exact_keys(
                body,
                {
                    "expected_version",
                    "name",
                    "description",
                    "kind",
                    "input_schema",
                    "output_schema",
                    "outcomes",
                    "config",
                    "agent_version_id",
                },
            )
            return self._ok(
                self.control_plane.revise_action(
                    workspace_id, match.group(1), **body
                ),
                status=HTTPStatus.CREATED,
            )
        if request.path == "/api/v1/studio/flows":
            self._require_keys(
                body,
                {
                    "name",
                    "slug",
                    "description",
                    "input_schema",
                    "start_node_id",
                    "nodes",
                    "routes",
                },
                {"output_schema", "outcomes"},
            )
            return self._ok(
                self.control_plane.create_studio_flow(workspace_id, **body),
                status=HTTPStatus.CREATED,
            )
        match = re.fullmatch(
            rf"/api/v1/studio/flows/{RESOURCE_ID}/versions", request.path
        )
        if match:
            self._require_keys(
                body,
                {
                    "expected_revision",
                    "input_schema",
                    "start_node_id",
                    "nodes",
                    "routes",
                },
                {"output_schema", "outcomes"},
            )
            return self._ok(
                self.control_plane.revise_studio_flow(
                    workspace_id, match.group(1), **body
                ),
                status=HTTPStatus.CREATED,
            )
        match = re.fullmatch(
            rf"/api/v1/studio/flows/{RESOURCE_ID}/triggers", request.path
        )
        if match:
            self._require_exact_keys(body, {"name", "trigger_type", "config"})
            return self._ok(
                self.control_plane.create_studio_trigger(
                    workspace_id, match.group(1), **body
                ),
                status=HTTPStatus.CREATED,
            )
        match = re.fullmatch(
            rf"/api/v1/studio/triggers/{RESOURCE_ID}/state", request.path
        )
        if match:
            self._require_exact_keys(body, {"enabled", "expected_revision"})
            return self._ok(
                self.control_plane.set_studio_trigger_enabled(
                    workspace_id, match.group(1), **body
                )
            )
        match = re.fullmatch(
            rf"/api/v1/studio/flows/{RESOURCE_ID}/runs:enqueue", request.path
        )
        if match:
            self._require_exact_keys(body, {"input", "idempotency_key"})
            flow_id = match.group(1)
            forecast = self.control_plane.studio_flow_model_call_forecast(
                workspace_id, flow_id
            )
            operation = lambda client: self.control_plane.enqueue_studio_run(
                workspace_id,
                flow_id,
                input_data=body["input"],
                idempotency_key=body["idempotency_key"],
                client=client,
            )
            return self._studio_execution(
                request,
                workspace_id,
                forecast_calls=forecast,
                status=HTTPStatus.ACCEPTED,
                operation=operation,
            )
        match = re.fullmatch(
            rf"/api/v1/studio/flows/{RESOURCE_ID}/runs", request.path
        )
        if match:
            self._require_exact_keys(body, {"input", "idempotency_key"})
            flow_id = match.group(1)
            forecast = self.control_plane.studio_flow_model_call_forecast(
                workspace_id, flow_id
            )
            operation = lambda client: self.control_plane.start_studio_run(
                workspace_id,
                flow_id,
                input_data=body["input"],
                idempotency_key=body["idempotency_key"],
                client=client,
            )
            return self._studio_execution(
                request,
                workspace_id,
                forecast_calls=forecast,
                status=HTTPStatus.CREATED,
                operation=operation,
            )
        match = re.fullmatch(
            rf"/api/v1/studio/approvals/{RESOURCE_ID}/decisions", request.path
        )
        if match:
            self._require_exact_keys(body, {"approved", "actor", "reason"})
            request_id = match.group(1)
            forecast = self.control_plane.studio_approval_model_call_forecast(
                workspace_id, request_id
            )
            operation = lambda client: self.control_plane.decide_studio_approval(
                workspace_id,
                request_id,
                approved=body["approved"],
                actor=body["actor"],
                reason=body["reason"],
                client=client,
            )
            return self._studio_execution(
                request,
                workspace_id,
                forecast_calls=forecast,
                status=HTTPStatus.OK,
                operation=operation,
            )
        match = re.fullmatch(
            rf"/api/v1/studio/runs/{RESOURCE_ID}/reruns", request.path
        )
        if match:
            self._require_exact_keys(body, {"input", "idempotency_key"})
            run_id = match.group(1)
            forecast = self.control_plane.studio_rerun_model_call_forecast(
                workspace_id, run_id
            )
            operation = lambda client: self.control_plane.rerun_studio_run(
                workspace_id,
                run_id,
                input_data=body["input"],
                idempotency_key=body["idempotency_key"],
                client=client,
            )
            return self._studio_execution(
                request,
                workspace_id,
                forecast_calls=forecast,
                status=HTTPStatus.CREATED,
                operation=operation,
            )
        match = re.fullmatch(
            rf"/api/v1/studio/runs/{RESOURCE_ID}:continue", request.path
        )
        if match:
            self._require_exact_keys(body, set())
            run_id = match.group(1)
            forecast = self.control_plane.studio_continue_model_call_forecast(
                workspace_id, run_id
            )
            operation = lambda client: self.control_plane.enqueue_existing_studio_run(
                workspace_id, run_id, client=client
            )
            return self._studio_execution(
                request,
                workspace_id,
                forecast_calls=forecast,
                status=HTTPStatus.ACCEPTED,
                operation=operation,
            )
        match = re.fullmatch(
            rf"/api/v1/studio/runs/{RESOURCE_ID}:cancel", request.path
        )
        if match:
            self._require_exact_keys(body, {"actor", "reason"})
            return self._ok(
                self.control_plane.cancel_studio_run(
                    workspace_id, match.group(1), **body
                )
            )
        match = re.fullmatch(
            rf"/api/v1/studio/runs/{RESOURCE_ID}/diagnoses", request.path
        )
        if match:
            self._require_exact_keys(body, set())
            return self._model_action(
                request,
                workspace_id,
                forecast_calls=1,
                status=HTTPStatus.CREATED,
                operation=lambda client: self.control_plane.diagnose_studio_run(
                    workspace_id, match.group(1), client=client
                ),
            )
        match = re.fullmatch(
            rf"/api/v1/studio/diagnoses/{RESOURCE_ID}/repairs", request.path
        )
        if match:
            self._require_exact_keys(body, set())
            return self._ok(
                self.control_plane.propose_studio_repair(
                    workspace_id, match.group(1)
                ),
                status=HTTPStatus.CREATED,
            )
        match = re.fullmatch(
            rf"/api/v1/studio/repairs/{RESOURCE_ID}/apply", request.path
        )
        if match:
            self._require_exact_keys(
                body,
                {
                    "proposal_hash",
                    "expected_flow_revision",
                    "expected_action_version",
                    "actor",
                    "reason",
                    "acknowledged",
                },
            )
            return self._ok(
                self.control_plane.apply_studio_repair(
                    workspace_id, match.group(1), **body
                )
            )
        match = re.fullmatch(
            rf"/api/v1/studio/repairs/{RESOURCE_ID}/proof", request.path
        )
        if match:
            self._require_exact_keys(body, {"input", "idempotency_key"})
            proposal = self.control_plane.studio.get_repair(
                workspace_id, match.group(1)
            )
            forecast = self.control_plane.studio_flow_model_call_forecast(
                workspace_id, proposal["flow_id"]
            )
            operation = lambda client: self.control_plane.prove_studio_repair(
                workspace_id,
                match.group(1),
                input_data=body["input"],
                idempotency_key=body["idempotency_key"],
                client=client,
            )
            return self._studio_execution(
                request,
                workspace_id,
                forecast_calls=forecast,
                status=HTTPStatus.CREATED,
                operation=operation,
            )
        match = re.fullmatch(rf"/api/v1/flows/{RESOURCE_ID}/runs", request.path)
        if match:
            self._require_exact_keys(body, set())
            return self._model_action(
                request,
                workspace_id,
                forecast_calls=3,
                status=HTTPStatus.CREATED,
                operation=lambda client: self.control_plane.run_flow(
                    workspace_id, match.group(1), client=client
                ),
            )
        match = re.fullmatch(rf"/api/v1/runs/{RESOURCE_ID}/diagnoses", request.path)
        if match:
            self._require_exact_keys(body, set())
            return self._model_action(
                request,
                workspace_id,
                forecast_calls=1,
                status=HTTPStatus.CREATED,
                operation=lambda client: self.control_plane.diagnose_run(
                    workspace_id, match.group(1), client=client
                ),
            )
        match = re.fullmatch(rf"/api/v1/diagnoses/{RESOURCE_ID}/repairs", request.path)
        if match:
            self._require_exact_keys(body, set())
            return self._model_action(
                request,
                workspace_id,
                forecast_calls=1,
                status=HTTPStatus.CREATED,
                operation=lambda client: self.control_plane.propose_repair(
                    workspace_id, match.group(1), client=client
                ),
            )
        match = re.fullmatch(rf"/api/v1/repairs/{RESOURCE_ID}/apply", request.path)
        if match:
            self._require_exact_keys(
                body,
                {
                    "proposal_hash",
                    "expected_flow_revision",
                    "actor",
                    "reason",
                    "acknowledged",
                },
            )
            return self._ok(
                self.control_plane.apply_repair(workspace_id, match.group(1), **body)
            )
        match = re.fullmatch(rf"/api/v1/runs/{RESOURCE_ID}/rerun", request.path)
        if match:
            self._require_exact_keys(body, set())
            run_id = match.group(1)
            existing = self.control_plane.existing_rerun(workspace_id, run_id)
            if existing is not None:
                return self._ok(existing, status=HTTPStatus.CREATED)
            return self._model_action(
                request,
                workspace_id,
                forecast_calls=3,
                status=HTTPStatus.CREATED,
                operation=lambda client: self.control_plane.rerun(
                    workspace_id, run_id, client=client
                ),
            )
        if request.path == "/api/v1/prompts":
            self._require_exact_keys(body, {"name", "slug", "template", "variables"})
            return self._ok(
                self.control_plane.create_prompt(workspace_id, **body),
                status=HTTPStatus.CREATED,
            )
        match = re.fullmatch(rf"/api/v1/prompts/{RESOURCE_ID}/versions", request.path)
        if match:
            self._require_exact_keys(
                body, {"expected_version", "name", "template", "variables"}
            )
            return self._ok(
                self.control_plane.revise_prompt(
                    workspace_id, match.group(1), **body
                ),
                status=HTTPStatus.CREATED,
            )
        if request.path == "/api/v1/skills":
            self._require_exact_keys(
                body,
                {
                    "name",
                    "slug",
                    "instructions",
                    "allowed_tools",
                    "allowed_action_version_ids",
                },
            )
            return self._ok(
                self.control_plane.create_skill(workspace_id, **body),
                status=HTTPStatus.CREATED,
            )
        match = re.fullmatch(rf"/api/v1/skills/{RESOURCE_ID}/versions", request.path)
        if match:
            self._require_exact_keys(
                body,
                {
                    "expected_version",
                    "name",
                    "instructions",
                    "allowed_tools",
                    "allowed_action_version_ids",
                },
            )
            return self._ok(
                self.control_plane.revise_skill(
                    workspace_id, match.group(1), **body
                ),
                status=HTTPStatus.CREATED,
            )
        if request.path == "/api/v1/agents":
            self._require_exact_keys(
                body,
                {
                    "name",
                    "slug",
                    "role",
                    "model",
                    "instructions",
                    "prompt_version_id",
                    "skill_version_ids",
                },
            )
            return self._ok(
                self.control_plane.create_agent(workspace_id, **body),
                status=HTTPStatus.CREATED,
            )
        match = re.fullmatch(rf"/api/v1/agents/{RESOURCE_ID}/versions", request.path)
        if match:
            self._require_exact_keys(
                body,
                {
                    "expected_version",
                    "name",
                    "role",
                    "model",
                    "instructions",
                    "prompt_version_id",
                    "skill_version_ids",
                },
            )
            return self._ok(
                self.control_plane.revise_agent(
                    workspace_id, match.group(1), **body
                ),
                status=HTTPStatus.CREATED,
            )
        if request.path == "/api/v1/flows":
            self._require_exact_keys(
                body,
                {
                    "name",
                    "slug",
                    "executor_agent_version_id",
                    "diagnostician_agent_version_id",
                    "repairer_agent_version_id",
                    "request",
                    "policy",
                    "repair_policy",
                },
            )
            return self._ok(
                self.control_plane.create_flow(workspace_id, **body),
                status=HTTPStatus.CREATED,
            )
        raise NotFound("API route was not found")

    def _studio_execution(
        self,
        request: ApiRequest,
        workspace_id: str,
        *,
        forecast_calls: int,
        status: int,
        operation: Callable[[Any], Any],
    ) -> ApiResponse:
        if forecast_calls <= 0:
            return self._ok(operation(None), status=status)
        return self._model_action(
            request,
            workspace_id,
            forecast_calls=forecast_calls,
            status=status,
            operation=operation,
        )

    def _model_action(
        self,
        request: ApiRequest,
        workspace_id: str,
        *,
        forecast_calls: int,
        status: int,
        operation: Callable[[Any], Any],
    ) -> ApiResponse:
        api_key = self._browser_api_key(request)
        with self.workspace_model_locks_guard:
            workspace_lock = self.workspace_model_locks.setdefault(
                workspace_id, threading.Lock()
            )
        if not workspace_lock.acquire(blocking=False):
            raise RateLimited("another model action is already running for this workspace")
        try:
            snapshot = self.control_plane.snapshot(workspace_id)
            used = int(snapshot["workspace"]["model_calls_used"])
            if used + forecast_calls > self.workspace_model_call_limit:
                raise RateLimited("workspace model-call budget is exhausted")
            self.rate_limiter.require(
                "model:global",
                limit=self.global_model_call_limit_per_hour,
                window_seconds=3600,
                units=forecast_calls,
            )
            self.rate_limiter.require(
                f"model:address:{request.remote_address}",
                limit=self.address_model_call_limit_per_hour,
                window_seconds=3600,
                units=forecast_calls,
            )
            if not self.model_slots.acquire(blocking=False):
                raise RateLimited("model execution capacity is busy; try again shortly")
            try:
                client = self.control_plane.client_for_browser_key(api_key)
                return self._ok(operation(client), status=status)
            finally:
                self.model_slots.release()
        finally:
            workspace_lock.release()

    @staticmethod
    def _browser_api_key(request: ApiRequest) -> str:
        value = request.header("X-OpenAI-API-Key")
        if (
            not isinstance(value, str)
            or not 20 <= len(value) <= 512
            or value != value.strip()
            or any(character.isspace() for character in value)
        ):
            raise OpenAIKeyRequired(
                "configure a valid OpenAI API key in this browser tab before running a model action"
            )
        return value

    @staticmethod
    def _require_same_origin(request: ApiRequest) -> None:
        origin = request.header("Origin")
        expected = f"{request.scheme}://{request.host}"
        if origin != expected:
            raise Forbidden("mutation origin does not match this server")
        fetch_site = request.header("Sec-Fetch-Site")
        if fetch_site not in {"", "same-origin"}:
            raise Forbidden("mutation fetch site is not same-origin")

    def _workspace_id(self, request: ApiRequest) -> str:
        raw_cookie = request.header("Cookie")
        try:
            cookie = SimpleCookie(raw_cookie)
        except Exception:
            raise Unauthorized("workspace cookie is invalid") from None
        morsel = cookie.get("kyn_workspace")
        if morsel is None or not morsel.value:
            raise Unauthorized("workspace cookie is required")
        return self.control_plane.resolve_workspace(morsel.value)

    @staticmethod
    def _json_body(request: ApiRequest) -> dict[str, Any]:
        content_type = request.header("Content-Type")
        if not content_type.lower().startswith("application/json"):
            raise RuntimeErrorBase("Content-Type must be application/json")
        if len(request.body) > MAX_API_BODY:
            raise PayloadTooLarge("request body exceeds 256 KiB")
        parsed = json.loads(request.body.decode("utf-8") or "{}")
        if not isinstance(parsed, dict):
            raise RuntimeErrorBase("request JSON must be an object")
        return parsed

    @staticmethod
    def _require_exact_keys(body: Mapping[str, Any], expected: set[str]) -> None:
        if set(body) != expected:
            missing = sorted(expected - set(body))
            unexpected = sorted(set(body) - expected)
            details = []
            if missing:
                details.append(f"missing: {', '.join(missing)}")
            if unexpected:
                details.append(f"unexpected: {', '.join(unexpected)}")
            raise RuntimeErrorBase("request fields do not match the route contract", detail={"issues": details})

    @staticmethod
    def _require_keys(
        body: Mapping[str, Any], required: set[str], optional: set[str]
    ) -> None:
        missing = sorted(required - set(body))
        unexpected = sorted(set(body) - required - optional)
        if missing or unexpected:
            issues = []
            if missing:
                issues.append(f"missing: {', '.join(missing)}")
            if unexpected:
                issues.append(f"unexpected: {', '.join(unexpected)}")
            raise RuntimeErrorBase(
                "request fields do not match the route contract",
                detail={"issues": issues},
            )

    @staticmethod
    def _ok(
        data: Any,
        *,
        status: int = HTTPStatus.OK,
        headers: Mapping[str, str] | None = None,
    ) -> ApiResponse:
        return ApiResponse(int(status), {"data": data}, dict(headers or {}))

    @staticmethod
    def _error(
        status: int,
        code: str,
        message: str,
        *,
        detail: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> ApiResponse:
        error: dict[str, Any] = {"code": code, "message": message}
        if detail:
            error["detail"] = dict(detail)
        return ApiResponse(int(status), {"error": error}, dict(headers or {}))


def sqlite_error_types() -> tuple[type[Exception], ...]:
    # Delayed import keeps the HTTP adapter's top-level surface small.
    import sqlite3

    return (sqlite3.IntegrityError, sqlite3.OperationalError)
