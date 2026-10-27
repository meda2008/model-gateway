"""统一模型网关 —— OpenAI 兼容 HTTP 面。

端点：
- GET  /healthz                 存活探针 + 已配置后端
- GET  /v1/models               聚合模型目录（free/auto、wb/<id>、qoder/<id>）
- POST /v1/chat/completions     对话（stream true/false 均支持，失败自动降级）

模型名解析：
- free/auto（或 auto）   按 FREE_AUTO_CHAIN 顺序尝试各后端默认模型，失败降级
- wb/<id> / qoder/<id>   指定后端与上游模型 id
- 裸 <id>                按各后端目录匹配（WB 静态配置或动态拉取的目录）
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from .backends import (
    BackendError,
    MockBackend,
    OpenAICompatBackend,
    QoderDirectBackend,
    WorkBuddyBackend,
)
from .config import Settings
from .keystore import ApiKey, KeyStore
from .sse import DONE_FRAME, sse_frame

logger = logging.getLogger("gateway")

# 双击桌面快捷方式后自动打开的“落地页”：纯静态、无外部依赖、深色主题。
_STATUS_HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Model Gateway</title>
<style>
:root{--bg:#0f1420;--card:#1a2333;--fg:#e6edf3;--muted:#8b98a9;--accent:#3fb950;--blue:#58a6ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;padding:32px}
.wrap{max-width:760px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--muted);margin-bottom:24px;font-size:13px}
.card{background:var(--card);border:1px solid #2a3344;border-radius:12px;padding:20px;margin-bottom:16px}
.row{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #232c3b;font-size:14px}
.row:last-child{border-bottom:none}
.k{color:var(--muted)}
.v{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;text-align:right}
.badge{display:inline-block;background:rgba(63,185,80,.15);color:var(--accent);padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600}
code{background:#0d1117;padding:2px 6px;border-radius:6px;font-size:12px;color:var(--blue)}
pre{background:#0d1117;padding:14px;border-radius:8px;overflow:auto;font-size:12px;color:var(--blue);margin:8px 0 0;white-space:pre-wrap}
</style></head>
<body><div class="wrap">
<h1>Unified Model Gateway</h1>
<div class="sub">统一模型网关 · OpenAI 兼容 API · 运行中</div>
<div class="card">
<div class="row"><span class="k">状态</span><span class="v"><span class="badge">● 运行中</span></span></div>
<div class="row"><span class="k">OpenAI 兼容端点</span><span class="v">http://127.0.0.1:{PORT}/v1</span></div>
<div class="row"><span class="k">API Key</span><span class="v">{KEY_STATUS}</span></div>
<div class="row"><span class="k">已启用后端</span><span class="v">{BACKENDS}</span></div>
<div class="row"><span class="k">模型总数</span><span class="v">{MODEL_COUNT}（含 free/auto）</span></div>
</div>
<div class="card">
<div class="k" style="margin-bottom:6px">快速调用</div>
<pre>curl http://127.0.0.1:{PORT}/v1/chat/completions \
  -H "Authorization: Bearer YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"free/auto","messages":[{"role":"user","content":"你好"}],"stream":true}'</pre>
<div style="margin-top:10px"><span class="k">模型目录：</span><code>GET http://127.0.0.1:{PORT}/v1/models</code></div>
<div style="margin-top:6px"><span class="k">存活探针：</span><code>GET http://127.0.0.1:{PORT}/healthz</code></div>
</div>
</div></body></html>"""

_PREFIX_BY_BACKEND = {"workbuddy": "wb", "qoder": "qoder"}


class ChatRequest(BaseModel):
    model: str
    messages: list[dict]
    stream: bool = False
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stop: Any = None
    response_format: dict | None = None
    stream_options: dict | None = None
    tools: list | None = None
    tool_choice: Any = None
    repetition_penalty: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    seed: int | None = None
    n: int | None = None
    user: str | None = None

    def params(self) -> dict:
        return self.model_dump(exclude={"model", "messages", "stream"})


def error_body(message: str, type_: str, code: str | None, param: str | None = None) -> dict:
    return {"error": {"message": message, "type": type_, "param": param, "code": code}}


def create_app(settings: Settings) -> FastAPI:
    store = KeyStore(settings.db_path)

    backends: dict[str, Any] = {}
    if settings.workbuddy.enabled:
        backends["workbuddy"] = WorkBuddyBackend(
            settings.workbuddy.base_url,
            settings.workbuddy.api_key,
            origin=settings.workbuddy.origin,
            timeout=settings.request_timeout,
            default_system=settings.workbuddy.default_system,
        )
        logger.info("backend enabled: workbuddy -> %s", settings.workbuddy.base_url)
    if settings.qoder_mode == "direct":
        backends["qoder"] = QoderDirectBackend(timeout=settings.request_timeout)
        logger.info("backend enabled: qoder (direct, embedded)")
    elif settings.qoder.enabled:
        backends["qoder"] = OpenAICompatBackend(
            settings.qoder.base_url, settings.qoder.api_key, timeout=settings.request_timeout
        )
        logger.info("backend enabled: qoder (proxy) -> %s", settings.qoder.base_url)
    if os.environ.get("MOCK_BACKEND") == "1":
        backends["mock_a"] = MockBackend("mock_a", fail_first=int(os.environ.get("MOCK_A_FAIL_FIRST", "0")))
        backends["mock_b"] = MockBackend("mock_b")
        logger.info("backend enabled: mock_a, mock_b (MOCK_BACKEND=1)")

    def cfg(name: str):
        return {"workbuddy": settings.workbuddy, "qoder": settings.qoder}.get(name)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        app.state.catalog = {}
        for name, backend in backends.items():
            try:
                app.state.catalog[name] = await backend.models()
                logger.info("catalog warm-up ok: %s (%d models)", name, len(app.state.catalog[name]))
            except Exception as exc:  # 目录拉取失败不阻塞启动
                app.state.catalog[name] = []
                logger.warning("catalog warm-up failed for %s: %s", name, exc)

        # 后台周期刷新：上游会上新模型（如 WB 云服务目录），默认 5 分钟内自动生效，
        # 无需重启网关；/v1/models 被调用时也会即时刷新一次
        refresh_seconds = float(os.environ.get("CATALOG_REFRESH_SECONDS", "300"))

        async def _refresh_loop() -> None:
            while True:
                await asyncio.sleep(refresh_seconds)
                for name, backend in backends.items():
                    try:
                        app.state.catalog[name] = await backend.models()
                    except Exception as exc:
                        logger.warning("catalog refresh failed for %s: %s", name, exc)

        refresh_task = asyncio.create_task(_refresh_loop())
        try:
            yield
        finally:
            refresh_task.cancel()

    app = FastAPI(title="unified-model-gateway", version="1.0.0", lifespan=lifespan)
    app.state.catalog = {}

    # ---------- 鉴权 ----------

    def _auth(authorization: str | None = Header(default=None)) -> ApiKey:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=401,
                detail="missing bearer token; use the sk-gw- key from manage_keys.py",
                headers={"WWW-Authenticate": "Bearer"},
            )
        key = store.lookup(authorization[7:].strip())
        if not key or not key.enabled:
            raise HTTPException(status_code=401, detail="invalid or disabled api key")
        return key

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_: Any, exc: HTTPException):
        code = "invalid_api_key" if exc.status_code == 401 else None
        type_ = "authentication_error" if exc.status_code == 401 else "invalid_request_error"
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content=error_body(str(exc.detail), type_, code),
        )

    # ---------- 模型解析 ----------

    def _parse_backend_ref(ref: str) -> tuple[str, str] | None:
        """解析 'wb/hy3' -> ('workbuddy', 'hy3')；非法返回 None。用于 qoder 兜底链配置。"""
        ref = (ref or "").strip().lower()
        if not ref or "/" not in ref:
            return None
        prefix, _, mid = ref.partition("/")
        name = {"wb": "workbuddy", "qoder": "qoder"}.get(prefix)
        if not name or not mid:
            return None
        return (name, mid)

    def resolve(model: str) -> list[tuple[str, str]]:
        """请求模型名 -> [(backend_name, upstream_model), ...] 候选链。"""
        if model in ("free/auto", "auto"):
            chain: list[tuple[str, str]] = []
            for name in settings.free_auto_chain:
                if name not in backends:
                    continue
                conf = cfg(name)
                default = getattr(backends[name], "default_model", None) or (
                    conf.default_model if conf else ""
                )
                if default:
                    chain.append((name, default))
            return chain
        for prefix, name in (("wb/", "workbuddy"), ("qoder/", "qoder")):
            if model.startswith(prefix):
                upstream = model[len(prefix):]
                chain = [(name, upstream)] if name in backends else []
                # qoder 上游偶发持续 500 时，原单点链会直接整轮失败。
                # 追加兜底后端（QODER_FALLBACK），qoder 重试耗尽后降级，避免单点故障。
                if name == "qoder" and settings.qoder_fallback and len(chain) == 1:
                    fb = _parse_backend_ref(settings.qoder_fallback)
                    if fb and fb[0] != "qoder" and fb[0] in backends:
                        chain.append(fb)
                return chain
        wb_ids = set(settings.workbuddy.models) | {m.id for m in app.state.catalog.get("workbuddy", [])}
        if model in wb_ids and "workbuddy" in backends:
            return [("workbuddy", model)]
        qoder_ids = set(settings.qoder.models) | {m.id for m in app.state.catalog.get("qoder", [])}
        if model in qoder_ids and "qoder" in backends:
            return [("qoder", model)]
        return []

    # ---------- 端点 ----------

    @app.get("/healthz")
    async def healthz():
        return {
            "ok": True,
            "backends": sorted(backends.keys()),
            "keys_configured": store.count() > 0,
        }

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        catalog = getattr(request.app.state, "catalog", {})
        backend_names = sorted(backends.keys())
        model_count = sum(len(catalog.get(n, [])) for n in backend_names) + 1  # +1 = free/auto
        keys_configured = store.count() > 0
        key_status = "已配置 ✓" if keys_configured else "未配置 ✗（运行 manage_keys.py add）"
        backends_str = "、".join(backend_names) if backend_names else "（无）"
        html = (
            _STATUS_HTML
            .replace("{PORT}", str(settings.port))
            .replace("{KEY_STATUS}", key_status)
            .replace("{BACKENDS}", backends_str)
            .replace("{MODEL_COUNT}", str(model_count))
        )
        return HTMLResponse(html)

    @app.get("/v1/models")
    async def list_models(key: ApiKey = Depends(_auth)):
        data: list[dict] = [
            {"id": "free/auto", "object": "model", "created": int(time.time()), "owned_by": "gateway"}
        ]
        for name, backend in backends.items():
            try:
                models = await backend.models()
            except BackendError as exc:
                logger.warning("models() failed for %s: %s", name, exc)
                models = []
            app.state.catalog[name] = models
            prefix = _PREFIX_BY_BACKEND.get(name, name)
            for m in models:
                data.append(
                    {
                        "id": f"{prefix}/{m.id}",
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": m.owned_by,
                    }
                )
        return {"object": "list", "data": data}

    @app.post("/v1/chat/completions")
    async def chat_completions(req: ChatRequest, key: ApiKey = Depends(_auth)):
        started = time.time()
        chain = resolve(req.model)
        if not chain:
            return JSONResponse(
                status_code=400,
                content=error_body(
                    f"model '{req.model}' is not available; use free/auto or GET /v1/models",
                    "invalid_request_error",
                    "model_not_found",
                    param="model",
                ),
            )
        allowed, retry_after, reason = store.check_and_consume(key)
        if not allowed:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry_after)},
                content=error_body(reason, "rate_limit_error", "rate_limit_exceeded"),
            )
        params = req.params()

        if req.stream:
            return StreamingResponse(
                _sse_with_failover(chain, req, params, key, started),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        last_error: BackendError | None = None
        for name, upstream in chain:
            try:
                result = await backends[name].chat_json(upstream, req.messages, params)
                result["model"] = req.model
                store.log_request(
                    key.name,
                    name,
                    req.model,
                    result.get("usage"),
                    int((time.time() - started) * 1000),
                )
                return JSONResponse(result)
            except BackendError as exc:
                last_error = exc
                logger.warning("non-stream backend %s failed, trying next: %s", name, exc)
        status = last_error.status if last_error else 502
        return JSONResponse(
            status_code=status,
            content=error_body(
                str(last_error) if last_error else "no backend available",
                "upstream_error",
                last_error.code if last_error else "no_backend",
            ),
        )

    async def _sse_with_failover(chain, req, params, key: ApiKey, started: float):
        """流式响应：首包发出前可在后端间降级；流开始后中断只能以错误事件收尾。"""
        last_error: BackendError | None = None
        for name, upstream in chain:
            backend = backends[name]
            gen = backend.chat_sse(upstream, req.messages, params)
            sent_any = False
            usage: dict | None = None
            try:
                while True:
                    chunk = await gen.__anext__()
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    yield sse_frame(chunk)
                    sent_any = True
            except StopAsyncIteration:
                store.log_request(key.name, name, req.model, usage, int((time.time() - started) * 1000))
                yield DONE_FRAME
                return
            except BackendError as exc:
                await gen.aclose()
                if sent_any:
                    store.log_request(key.name, name, req.model, usage, int((time.time() - started) * 1000))
                    yield sse_frame(error_body(str(exc), "upstream_error", exc.code))
                    yield DONE_FRAME
                    return
                last_error = exc
                logger.warning("stream backend %s failed before first byte, trying next: %s", name, exc)
                continue
        yield sse_frame(
            error_body(
                str(last_error) if last_error else "no backend available",
                "upstream_error",
                last_error.code if last_error else "no_backend",
            )
        )
        yield DONE_FRAME

    return app
