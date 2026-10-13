"""后端适配器层。

两种后端形态：

1. WorkBuddyBackend —— WorkBuddy 云服务 LLM API。
   线上协议提取自 @tencent-ai/workbuddy-cloud-sdk（0.1.3-dev，2026-10-05）：
   - 模型目录: GET  {endpoint}/.cloud/llm/models
   - 对话:     POST {endpoint}/.cloud/llm/chat/completions（stream 必须为 true，SSE）
   - 鉴权头:   x-wb-webapp-access-key: wbpk_xxx（匿名可用，无需用户会话）
   - Origin:   浏览器里由浏览器自动携带，服务端校验与应用发布域名一致；
               服务端调用必须显式设置（默认取 endpoint 的 origin）
   - 会话隔离: 按应用环境隔离，配 conversationId 仅用于计量/路由关联（本网关每次请求独立，不传）

2. OpenAICompatBackend —— 任意 OpenAI 兼容上游（默认对接 Qoder2Api 本地代理），
   原生支持流式/非流式。

3. MockBackend —— 测试用假后端，可配置故障次数，用于验证降级链路。
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator
from urllib.parse import urlsplit

import httpx

from . import qoder_direct
from .sse import iter_sse_events


class BackendError(Exception):
    """上游错误。status 透传上游 HTTP 状态（无则 502）。"""

    def __init__(self, message: str, status: int = 502, code: str = "upstream_error"):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class ModelInfo:
    id: str
    name: str
    owned_by: str
    enabled: bool = True


# 透传给上游的 OpenAI 常用参数（None 值会被剔除）
_PASSTHROUGH_PARAMS = (
    "temperature",
    "top_p",
    "max_tokens",
    "stop",
    "response_format",
    "stream_options",
    "tools",
    "tool_choice",
    "repetition_penalty",
    "presence_penalty",
    "frequency_penalty",
    "seed",
    "n",
    "user",
)


def build_body(model: str, messages: list, stream: bool, params: dict) -> dict:
    body: dict[str, Any] = {"model": model, "messages": messages, "stream": stream}
    for key in _PASSTHROUGH_PARAMS:
        if params.get(key) is not None:
            body[key] = params[key]
    return body


def synthesize_completion(chunks: list[dict], requested_model: str) -> dict:
    """把流式 chunk 合并为一条非流式 ChatCompletion（OpenAI 形状）。"""
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: dict[int, dict] = {}
    finish_reason: str | None = None
    usage: dict | None = None
    chunk_id: str | None = None
    created = int(time.time())
    for chunk in chunks:
        chunk_id = chunk_id or chunk.get("id")
        created = chunk.get("created") or created
        if chunk.get("usage"):
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("content"):
                content_parts.append(delta["content"])
            if delta.get("reasoning_content"):
                reasoning_parts.append(delta["reasoning_content"])
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                cur = tool_calls.setdefault(
                    idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                )
                if tc.get("id") and not cur["id"]:
                    cur["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name") and not cur["function"]["name"]:
                    cur["function"]["name"] = fn["name"]
                if fn.get("arguments"):
                    cur["function"]["arguments"] += fn["arguments"]
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
    message: dict[str, Any] = {"role": "assistant", "content": "".join(content_parts)}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if tool_calls:
        message["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
    return {
        "id": chunk_id or f"chatcmpl-gw-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": created,
        "model": requested_model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason or "stop"}],
        "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _extract_items(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "models", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


class WorkBuddyBackend:
    """WorkBuddy 云服务 LLM API 适配器（免密钥 + Origin 校验 + 仅流式）。"""

    kind = "workbuddy"

    def __init__(self, endpoint: str, publishable_key: str, origin: str = "", timeout: float = 180.0,
                 default_system: str = "You are a helpful assistant. Be concise and helpful."):
        if not endpoint or not publishable_key:
            raise ValueError("WorkBuddyBackend requires endpoint and publishable_key")
        self.endpoint = endpoint.rstrip("/")
        self.publishable_key = publishable_key
        parts = urlsplit(self.endpoint)
        self.origin = origin or f"{parts.scheme}://{parts.netloc}"
        self.timeout = timeout
        self.default_system = default_system

    def _headers(self, accept: str) -> dict:
        return {
            "x-wb-webapp-access-key": self.publishable_key,
            "Origin": self.origin,
            "Accept": accept,
            "Content-Type": "application/json",
        }

    async def chat_sse(self, model: str, messages: list, params: dict) -> AsyncIterator[dict]:
        """流式对话：yield 解析后的 chunk 字典。上游仅支持 stream=true。"""
        # WB 要求 messages[0] 必须是 system 消息，缺了自动注入默认值
        if not messages or messages[0].get("role") != "system":
            messages = [{"role": "system", "content": self.default_system}, *messages]
        body = build_body(model, messages, True, params)
        body.setdefault("stream_options", {"include_usage": True})
        timeout = httpx.Timeout(self.timeout, read=self.timeout)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.endpoint}/.cloud/llm/chat/completions",
                    headers=self._headers("text/event-stream"),
                    json=body,
                ) as resp:
                    if resp.status_code >= 400:
                        detail = (await resp.aread()).decode("utf-8", "replace")[:500]
                        raise BackendError(
                            f"workbuddy upstream HTTP {resp.status_code}: {detail}",
                            status=resp.status_code,
                        )
                    async for event, data in iter_sse_events(resp):
                        if event == "error":
                            raise BackendError(f"workbuddy stream error: {data[:300]}")
                        if data == "[DONE]":
                            return
                        if not data:
                            continue
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            raise BackendError(f"workbuddy invalid SSE frame: {data[:200]}")
                        if isinstance(chunk, dict) and "error" in chunk:
                            raise BackendError(str(chunk["error"])[:300])
                        yield chunk
        except httpx.HTTPError as exc:
            raise BackendError(f"workbuddy upstream connection failed: {exc}")

    async def chat_json(self, model: str, messages: list, params: dict) -> dict:
        chunks = [chunk async for chunk in self.chat_sse(model, messages, params)]
        if not chunks:
            raise BackendError("workbuddy returned empty stream")
        return synthesize_completion(chunks, model)

    async def models(self) -> list[ModelInfo]:
        timeout = httpx.Timeout(30.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(
                    f"{self.endpoint}/.cloud/llm/models", headers=self._headers("application/json")
                )
                if resp.status_code >= 400:
                    raise BackendError(
                        f"workbuddy models HTTP {resp.status_code}", status=resp.status_code
                    )
                payload = resp.json()
        except httpx.HTTPError as exc:
            raise BackendError(f"workbuddy models connection failed: {exc}")
        result: list[ModelInfo] = []
        for raw in _extract_items(payload):
            mid = raw.get("id")
            if not mid:
                continue
            disabled = raw.get("disabled") is True or raw.get("enabled") is False
            result.append(
                ModelInfo(
                    id=mid,
                    name=raw.get("name") or mid,
                    owned_by="workbuddy",
                    enabled=not disabled,
                )
            )
        return result


class OpenAICompatBackend:
    """任意 OpenAI 兼容上游（默认 Qoder2Api 本地代理）。"""

    kind = "openai"

    def __init__(self, base_url: str, api_key: str = "sk-local", timeout: float = 180.0):
        if not base_url:
            raise ValueError("OpenAICompatBackend requires base_url")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self, accept: str) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": accept,
            "Content-Type": "application/json",
        }

    async def chat_sse(self, model: str, messages: list, params: dict) -> AsyncIterator[dict]:
        body = build_body(model, messages, True, params)
        body.setdefault("stream_options", {"include_usage": True})
        timeout = httpx.Timeout(self.timeout, read=self.timeout)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=self._headers("text/event-stream"),
                    json=body,
                ) as resp:
                    if resp.status_code >= 400:
                        detail = (await resp.aread()).decode("utf-8", "replace")[:500]
                        raise BackendError(
                            f"upstream HTTP {resp.status_code}: {detail}", status=resp.status_code
                        )
                    async for event, data in iter_sse_events(resp):
                        if event == "error":
                            raise BackendError(f"upstream stream error: {data[:300]}")
                        if data == "[DONE]":
                            return
                        if not data:
                            continue
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(chunk, dict) and "error" in chunk:
                            raise BackendError(str(chunk["error"])[:300])
                        yield chunk
        except httpx.HTTPError as exc:
            raise BackendError(f"upstream connection failed: {exc}")

    async def chat_json(self, model: str, messages: list, params: dict) -> dict:
        body = build_body(model, messages, False, params)
        timeout = httpx.Timeout(self.timeout, read=self.timeout)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers("application/json"),
                    json=body,
                )
                if resp.status_code >= 400:
                    raise BackendError(
                        f"upstream HTTP {resp.status_code}: {resp.text[:500]}",
                        status=resp.status_code,
                    )
                data = resp.json()
        except httpx.HTTPError as exc:
            raise BackendError(f"upstream connection failed: {exc}")
        if isinstance(data, dict) and "error" in data:
            raise BackendError(str(data["error"])[:300])
        return data

    async def models(self) -> list[ModelInfo]:
        timeout = httpx.Timeout(30.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(
                    f"{self.base_url}/models",
                    headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
                )
                if resp.status_code >= 400:
                    raise BackendError(f"upstream models HTTP {resp.status_code}", status=resp.status_code)
                payload = resp.json()
        except httpx.HTTPError as exc:
            raise BackendError(f"upstream models connection failed: {exc}")
        result: list[ModelInfo] = []
        for raw in _extract_items(payload):
            mid = raw.get("id")
            if not mid:
                continue
            result.append(ModelInfo(id=mid, name=raw.get("name") or mid, owned_by="qoder"))
        return result


class MockBackend:
    """测试用假后端：fail_first 次调用先抛错，用于验证降级链路。"""

    kind = "mock"
    default_model = "mock-model"

    def __init__(self, name: str, model_ids: tuple[str, ...] = ("mock-model",), fail_first: int = 0):
        self.name = name
        self.model_ids = model_ids
        self.fail_first = fail_first

    async def chat_sse(self, model: str, messages: list, params: dict) -> AsyncIterator[dict]:
        if self.fail_first > 0:
            self.fail_first -= 1
            raise BackendError(f"{self.name} is down (mock)", status=503)
        base = {
            "id": f"chatcmpl-{self.name}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
        }
        yield {
            **base,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
        }
        for piece in (f"[{self.name}]", "你好", "，世界"):
            yield {**base, "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
        yield {
            **base,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
        }

    async def chat_json(self, model: str, messages: list, params: dict) -> dict:
        chunks = [chunk async for chunk in self.chat_sse(model, messages, params)]
        return synthesize_completion(chunks, model)

    async def models(self) -> list[ModelInfo]:
        return [ModelInfo(id=m, name=m, owned_by=self.name) for m in self.model_ids]


class QoderDirectBackend:
    """Qoder 直连后端：内嵌 qoder_direct（原 Qoder2Api 逻辑），单进程单端口，无第二套 key。"""

    kind = "qoder-direct"
    default_model = qoder_direct.DEFAULT_MODEL

    def __init__(self, timeout: float = 180.0):
        self.timeout = timeout

    async def chat_sse(self, model: str, messages: list, params: dict) -> AsyncIterator[dict]:
        body = build_body(qoder_direct.resolve_model(model), messages, True, params)
        body.setdefault("stream_options", {"include_usage": True})
        try:
            async for chunk in qoder_direct.chat_sse_chunks(body, timeout=self.timeout):
                yield chunk
        except qoder_direct.QoderError as exc:
            raise BackendError(str(exc), status=exc.status)

    async def chat_json(self, model: str, messages: list, params: dict) -> dict:
        body = build_body(qoder_direct.resolve_model(model), messages, False, params)
        try:
            return await qoder_direct.chat_once(body, timeout=self.timeout)
        except qoder_direct.QoderError as exc:
            raise BackendError(str(exc), status=exc.status)

    async def models(self) -> list[ModelInfo]:
        return [ModelInfo(id=m, name=m, owned_by="qoder") for m in qoder_direct.MODELS]
