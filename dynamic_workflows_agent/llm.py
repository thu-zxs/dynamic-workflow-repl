from __future__ import annotations

import asyncio
import contextvars
from contextlib import contextmanager
import json
import os
import random
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable, Protocol


class LLMError(RuntimeError):
    """Raised when a model backend call fails."""


LLMEventCallback = Callable[[str, str, dict[str, Any]], None]
_CURRENT_LLM_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "dynamic_workflows_llm_context",
    default={},
)


@contextmanager
def llm_context(**values: Any):
    current = dict(_CURRENT_LLM_CONTEXT.get({}))
    current.update({key: value for key, value in values.items() if value is not None})
    token = _CURRENT_LLM_CONTEXT.set(current)
    try:
        yield
    finally:
        _CURRENT_LLM_CONTEXT.reset(token)


class LLMClient(Protocol):
    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Return a JSON object from the model."""

    async def chat_json_with_tools(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        tools: list[dict[str, Any]],
        run_tool: Callable[[str, dict[str, Any]], dict[str, Any]],
        max_tokens: int = 4096,
        max_tool_rounds: int = 4,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Return a JSON object after optional model-requested tool calls."""


def extract_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if not text:
        raise LLMError("model returned empty content")
    decoder = json.JSONDecoder()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        try:
            value, _ = decoder.raw_decode(text)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            return value
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise LLMError(f"model did not return a JSON object: {text[:200]}") from None
        try:
            value, _ = decoder.raw_decode(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"model did not return a JSON object: {text[:200]}") from exc
    if not isinstance(value, dict):
        raise LLMError("model JSON response must be an object")
    return value


def jittered_backoff(attempt: int, *, base_delay: float = 0.75, max_delay: float = 8.0) -> float:
    exponent = max(0, attempt - 1)
    delay = min(base_delay * (2**exponent), max_delay)
    return delay + random.uniform(0, delay * 0.35)


class DeepSeekClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "deepseek-v4-pro",
        base_url: str = "https://api.deepseek.com",
        timeout: float = 60.0,
        max_retries: int = 3,
        temperature: float = 0.2,
        event_callback: LLMEventCallback | None = None,
    ) -> None:
        if not api_key:
            raise LLMError("DEEPSEEK_API_KEY is required for the DeepSeek backend")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.temperature = temperature
        self.event_callback = event_callback

    @classmethod
    def from_env(cls) -> "DeepSeekClient":
        return cls(
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro"),
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            timeout=float(os.environ.get("DEEPSEEK_TIMEOUT", "60")),
            max_retries=int(os.environ.get("DEEPSEEK_MAX_RETRIES", "3")),
        )

    def set_event_callback(self, callback: LLMEventCallback | None) -> None:
        self.event_callback = callback

    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._chat_json_sync,
            system=system,
            user=user,
            schema_name=schema_name,
            max_tokens=max_tokens,
        )

    def _chat_json_sync(self, *, system: str, user: str, schema_name: str, max_tokens: int) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "stream": False,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
        }
        data = self._post_chat(body=body, schema_name=schema_name)
        content = data["choices"][0]["message"]["content"]
        return extract_json_object(content)

    async def chat_json_with_tools(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        tools: list[dict[str, Any]],
        run_tool: Callable[[str, dict[str, Any]], dict[str, Any]],
        max_tokens: int = 4096,
        max_tool_rounds: int = 4,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return await asyncio.to_thread(
            self._chat_json_with_tools_sync,
            system=system,
            user=user,
            schema_name=schema_name,
            tools=tools,
            run_tool=run_tool,
            max_tokens=max_tokens,
            max_tool_rounds=max_tool_rounds,
        )

    def _chat_json_with_tools_sync(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        tools: list[dict[str, Any]],
        run_tool: Callable[[str, dict[str, Any]], dict[str, Any]],
        max_tokens: int,
        max_tool_rounds: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        tool_results: list[dict[str, Any]] = []
        for _ in range(max_tool_rounds):
            data = self._post_chat(
                body={
                    "model": self.model,
                    "messages": messages,
                    "tools": tools,
                    "tool_choice": "auto",
                    "stream": False,
                    "temperature": self.temperature,
                    "max_tokens": max_tokens,
                },
                schema_name=schema_name,
            )
            message = data["choices"][0]["message"]
            calls = message.get("tool_calls") or []
            if not calls:
                content = message.get("content") or ""
                return extract_json_object(content), tool_results

            messages.append(message)
            for call in calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                raw_arguments = function.get("arguments") or "{}"
                try:
                    arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                except json.JSONDecodeError:
                    arguments = {"_raw": raw_arguments}
                if not isinstance(arguments, dict):
                    arguments = {"value": arguments}
                self._emit_llm_event(
                    "llm_tool_call",
                    f"tool {name} requested",
                    {
                        "schema_name": schema_name,
                        "tool_name": name,
                        "tool_arguments": arguments,
                    },
                )
                result = run_tool(name, arguments)
                tool_results.append(result)
                self._emit_llm_event(
                    "llm_tool_result",
                    f"tool {name} returned {'ok' if result.get('ok') else 'error'}",
                    {
                        "tool_name": name,
                        "tool_ok": bool(result.get("ok")),
                        "tool_summary": result.get("summary") or result.get("error") or "",
                    },
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        messages.append(
            {
                "role": "user",
                "content": (
                    "Tool-call budget is exhausted. Return the required final JSON now, using only "
                    "the tool results already provided."
                ),
            }
        )
        data = self._post_chat(
            body={
                "model": self.model,
                "messages": messages,
                "response_format": {"type": "json_object"},
                "stream": False,
                "temperature": self.temperature,
                "max_tokens": max_tokens,
            },
            schema_name=schema_name,
        )
        content = data["choices"][0]["message"]["content"]
        return extract_json_object(content), tool_results

    def _post_chat(self, *, body: dict[str, Any], schema_name: str) -> dict[str, Any]:
        endpoint = f"{self.base_url}/chat/completions"
        payload = json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "dynamic-workflows-agent/0.1",
        }

        request_id = uuid.uuid4().hex[:10]
        prompt_tokens_estimate = _estimate_prompt_tokens(body)
        self._emit_llm_event(
            "llm_request_start",
            f"{schema_name}: preparing model request",
            {
                "request_id": request_id,
                "schema_name": schema_name,
                "model": body.get("model") or self.model,
                "prompt_tokens_estimate": prompt_tokens_estimate,
                "max_tokens": body.get("max_tokens"),
                "message_count": len(body.get("messages") or []),
                "tool_count": len(body.get("tools") or []),
            },
        )
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            request = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
            started_at = time.time()
            self._emit_llm_event(
                "llm_request_sent",
                f"{schema_name}: sent ~{prompt_tokens_estimate} prompt tokens",
                {
                    "request_id": request_id,
                    "schema_name": schema_name,
                    "attempt": attempt,
                    "prompt_tokens_estimate": prompt_tokens_estimate,
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    response_body = response.read().decode("utf-8")
                    data = json.loads(response_body)
                    usage = data.get("usage") if isinstance(data, dict) else None
                    completion_estimate = _estimate_completion_tokens(data)
                    self._emit_llm_event(
                        "llm_response",
                        f"{schema_name}: response received",
                        {
                            "request_id": request_id,
                            "schema_name": schema_name,
                            "attempt": attempt,
                            "elapsed_seconds": time.time() - started_at,
                            "usage": usage if isinstance(usage, dict) else {},
                            "completion_tokens_estimate": completion_estimate,
                        },
                    )
                    return data
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last_error = LLMError(f"DeepSeek HTTP {exc.code} while requesting {schema_name}: {detail[:500]}")
                self._emit_llm_event(
                    "llm_request_error",
                    f"{schema_name}: DeepSeek HTTP {exc.code}",
                    {
                        "request_id": request_id,
                        "schema_name": schema_name,
                        "attempt": attempt,
                        "elapsed_seconds": time.time() - started_at,
                        "error": str(last_error),
                    },
                )
                if exc.code not in {408, 409, 429, 500, 502, 503, 504}:
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
                last_error = LLMError(f"DeepSeek request failed while requesting {schema_name}: {exc}")
                self._emit_llm_event(
                    "llm_request_error",
                    f"{schema_name}: request failed",
                    {
                        "request_id": request_id,
                        "schema_name": schema_name,
                        "attempt": attempt,
                        "elapsed_seconds": time.time() - started_at,
                        "error": str(last_error),
                    },
                )
            if attempt < self.max_retries:
                time.sleep(jittered_backoff(attempt))
        raise last_error or LLMError(f"DeepSeek request failed while requesting {schema_name}")

    def _emit_llm_event(self, kind: str, message: str, data: dict[str, Any]) -> None:
        if self.event_callback is None:
            return
        payload = dict(_CURRENT_LLM_CONTEXT.get({}))
        payload.update(data)
        self.event_callback(kind, message, payload)


class FakeLLMClient:
    """Deterministic backend for tests and offline demos.

    This backend is intentionally simple. It verifies orchestration mechanics
    without pretending to be the production planner.
    """

    def __init__(self, *, delay: float = 0.0, force_dispute: bool = False) -> None:
        self.delay = delay
        self.force_dispute = force_dispute
        self.calls: list[str] = []
        self.event_callback: LLMEventCallback | None = None

    def set_event_callback(self, callback: LLMEventCallback | None) -> None:
        self.event_callback = callback

    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        self.calls.append(schema_name)
        request_id = uuid.uuid4().hex[:10]
        prompt_tokens_estimate = _estimate_tokens(system) + _estimate_tokens(user)
        self._emit_llm_event(
            "llm_request_start",
            f"{schema_name}: preparing fake model request",
            {
                "request_id": request_id,
                "schema_name": schema_name,
                "model": "fake",
                "prompt_tokens_estimate": prompt_tokens_estimate,
                "max_tokens": max_tokens,
                "message_count": 2,
                "tool_count": 0,
            },
        )
        self._emit_llm_event(
            "llm_request_sent",
            f"{schema_name}: sent ~{prompt_tokens_estimate} prompt tokens",
            {
                "request_id": request_id,
                "schema_name": schema_name,
                "attempt": 1,
                "prompt_tokens_estimate": prompt_tokens_estimate,
            },
        )
        started_at = time.time()
        if self.delay:
            await asyncio.sleep(self.delay)
        if schema_name == "workflow_plan":
            result = self._workflow_plan(user)
        elif schema_name == "finding":
            result = self._finding(user)
        elif schema_name == "verification":
            result = self._verification(user)
        elif schema_name == "synthesis":
            result = self._synthesis(user)
        else:
            raise LLMError(f"fake backend does not know schema {schema_name}")
        completion_tokens_estimate = _estimate_tokens(json.dumps(result, ensure_ascii=False))
        self._emit_llm_event(
            "llm_response",
            f"{schema_name}: response received",
            {
                "request_id": request_id,
                "schema_name": schema_name,
                "attempt": 1,
                "elapsed_seconds": time.time() - started_at,
                "usage": {
                    "prompt_tokens": prompt_tokens_estimate,
                    "completion_tokens": completion_tokens_estimate,
                    "total_tokens": prompt_tokens_estimate + completion_tokens_estimate,
                },
                "completion_tokens_estimate": completion_tokens_estimate,
            },
        )
        return result

    async def chat_json_with_tools(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        tools: list[dict[str, Any]],
        run_tool: Callable[[str, dict[str, Any]], dict[str, Any]],
        max_tokens: int = 4096,
        max_tool_rounds: int = 4,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        tool_results: list[dict[str, Any]] = []
        if schema_name == "finding":
            self._emit_llm_event(
                "llm_tool_call",
                "tool list_files requested",
                {"schema_name": schema_name, "tool_name": "list_files", "tool_arguments": {"root": ".", "max_results": 20}},
            )
            tool_results.append(run_tool("list_files", {"root": ".", "max_results": 20}))
            self._emit_llm_event(
                "llm_tool_result",
                "tool list_files returned",
                {
                    "schema_name": schema_name,
                    "tool_name": "list_files",
                    "tool_ok": bool(tool_results[-1].get("ok")),
                    "tool_summary": tool_results[-1].get("summary") or tool_results[-1].get("error") or "",
                },
            )
        result = await self.chat_json(system=system, user=user, schema_name=schema_name, max_tokens=max_tokens)
        if schema_name == "finding" and tool_results:
            result["tool_results"] = tool_results
        return result, tool_results

    def _emit_llm_event(self, kind: str, message: str, data: dict[str, Any]) -> None:
        if self.event_callback is None:
            return
        payload = dict(_CURRENT_LLM_CONTEXT.get({}))
        payload.update(data)
        self.event_callback(kind, message, payload)

    def _workflow_plan(self, user: str) -> dict[str, Any]:
        goal = _extract_tag(user, "USER_TASK") or "Complete the requested task"
        followup = "FOLLOW_UP_CONTEXT" in user
        prefix = "FU" if followup else "T"
        focus = [
            ("independent investigator", "Map the problem and produce direct findings."),
            ("adversarial reviewer", "Look for counterexamples, hidden assumptions, and failure modes."),
            ("integration analyst", "Connect findings into an implementation-ready view."),
        ]
        subtasks = []
        for index, (role, instruction) in enumerate(focus, start=1):
            subtask_id = f"{prefix}{index}"
            subtasks.append(
                {
                    "id": subtask_id,
                    "title": f"{role.title()} pass for: {goal[:72]}",
                    "agent_role": role,
                    "prompt": f"{instruction} Work only on this perspective for the goal: {goal}",
                    "depends_on": [],
                    "expected_output": "finding",
                }
            )
        return {
            "goal": goal,
            "success_criteria": [
                "At least three independent findings are produced.",
                "Findings are verified or explicitly disputed.",
                "Final answer separates conclusions from unresolved risks.",
            ],
            "subtasks": subtasks,
            "parallel_groups": [
                {
                    "id": f"{prefix}G1",
                    "subtask_ids": [item["id"] for item in subtasks],
                    "max_concurrency": 3,
                }
            ],
            "verification_steps": [
                {
                    "id": f"{prefix}V1",
                    "target_subtask_ids": [item["id"] for item in subtasks],
                    "mode": "refute",
                    "prompt": "Try to refute each finding and flag weak evidence.",
                }
            ],
            "convergence_policy": {
                "max_rounds": 2,
                "min_confidence": 0.7,
                "require_no_critical_disputes": True,
            },
        }

    def _finding(self, user: str) -> dict[str, Any]:
        subtask = _extract_json_after_tag(user, "SUBTASK_JSON")
        subtask_id = str(subtask.get("id", "T"))
        role = str(subtask.get("agent_role", "worker"))
        title = str(subtask.get("title", "subtask"))
        return {
            "id": f"F-{subtask_id}",
            "subtask_id": subtask_id,
            "agent_role": role,
            "claim": f"{title} found a concrete path forward.",
            "evidence": [
                "The worker received an isolated subtask context.",
                "The output follows the structured Finding protocol.",
            ],
            "confidence": 0.84,
            "limitations": ["Fake backend does not inspect external systems."],
            "recommended_next_steps": ["Verify the finding before synthesis."],
        }

    def _verification(self, user: str) -> dict[str, Any]:
        step = _extract_json_after_tag(user, "VERIFICATION_STEP_JSON")
        target_ids = step.get("target_subtask_ids") or ["T1", "T2", "T3"]
        if self.force_dispute:
            return {
                "id": str(step.get("id", "V")),
                "target_subtask_ids": target_ids,
                "mode": str(step.get("mode", "refute")),
                "verdict": "disputed",
                "issues": ["Forced dispute for convergence testing."],
                "counterarguments": ["The current evidence is intentionally marked incomplete."],
                "needs_followup": True,
                "confidence": 0.9,
            }
        return {
            "id": str(step.get("id", "V")),
            "target_subtask_ids": target_ids,
            "mode": str(step.get("mode", "refute")),
            "verdict": "accepted",
            "issues": [],
            "counterarguments": [],
            "needs_followup": False,
            "confidence": 0.86,
        }

    def _synthesis(self, user: str) -> dict[str, Any]:
        return {
            "report_markdown": (
                "# Final Report\n\n"
                "The workflow completed with independent worker findings, verification, "
                "and a coordinated synthesis.\n\n"
                "## Verified Conclusions\n\n"
                "- The orchestration path produced structured findings.\n"
                "- Verification ran before final synthesis.\n\n"
                "## Residual Risk\n\n"
                "- Offline fake mode cannot validate live model quality.\n"
            )
        }


def _extract_tag(text: str, tag: str) -> str:
    marker = f"{tag}:"
    start = text.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    end = text.find("\n\n", start)
    if end < 0:
        end = len(text)
    return text[start:end].strip()


def _extract_json_after_tag(text: str, tag: str) -> dict[str, Any]:
    marker = f"{tag}:"
    start = text.find(marker)
    if start < 0:
        return {}
    start += len(marker)
    tail = text[start:].strip()
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(tail)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        pass
    try:
        return extract_json_object(tail)
    except LLMError:
        try:
            value, _ = decoder.raw_decode(tail)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}


def _estimate_prompt_tokens(body: dict[str, Any]) -> int:
    total = 0
    messages = body.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict):
                total += _estimate_tokens(str(message.get("role") or ""))
                total += _estimate_tokens(_message_content_text(message.get("content")))
    tools = body.get("tools")
    if isinstance(tools, list) and tools:
        total += _estimate_tokens(json.dumps(tools, ensure_ascii=False))
    response_format = body.get("response_format")
    if isinstance(response_format, dict):
        total += _estimate_tokens(json.dumps(response_format, ensure_ascii=False))
    return max(1, total)


def _estimate_completion_tokens(data: dict[str, Any]) -> int:
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list):
        return 0
    total = 0
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        total += _estimate_tokens(_message_content_text(message.get("content")))
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            total += _estimate_tokens(json.dumps(tool_calls, ensure_ascii=False))
    return total


def _message_content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return ""
    return str(value)


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return max(1, int(ascii_chars / 4 + non_ascii_chars / 1.7))
