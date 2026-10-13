"""Qoder 直连客户端（内嵌合并，替代独立 Qoder2Api 进程）。

Adapted from the community project Qoder2Api
(https://github.com/ErfanBagheri404/Qoder2Api), MIT License,
Copyright (c) 2026 ErfanBagheri404. Original copyright and permission
notice retained per the MIT terms.

移植内容（auth.py + client.py）：
- 设备登录态复用同一文件 ~/.qoder2api-auth.json（已登录过的无需重新登录）
- 上游 POST https://api2-v2.qoder.sh/model/v1/chat/completions（OpenAI 格式，Bearer token）
- token 过期自动刷新；模型 id 映射、重试策略与原实现一致
- HTTP 栈换成本项目的 httpx（异步、统一错误包装、统一代理处理）

备选：不想内嵌可设 QODER_MODE=proxy，回到"独立 Qoder2Api 进程 + OpenAI 兼容适配器"。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import string
import time
import urllib.parse
import uuid
from typing import Any, AsyncIterator

import httpx

from .sse import DONE, iter_sse_events

API_HOST = "https://api2-v2.qoder.sh"
CHAT_URL = f"{API_HOST}/model/v1/chat/completions"
AUTH_BASE = "https://qoder.com"
OPENAPI_BASE = "https://openapi.qoder.sh"
CLIENT_ID = "732aef47-9cf2-46a2-95fe-4cebb5d0d1fa"
REDIRECT_URI = "qoder-app://"
POLL_URL = f"{OPENAPI_BASE}/api/v1/deviceToken/poll"
REFRESH_URL = f"{OPENAPI_BASE}/api/v1/deviceToken/refresh"
QUOTA_URL = f"{OPENAPI_BASE}/api/v2/quota/usage"
PLAN_URL = f"{OPENAPI_BASE}/api/v2/user/plan"
USERINFO_URL = f"{OPENAPI_BASE}/api/v1/userinfo"

UA = os.environ.get("QODER_UA") or "Qoder/1.1.61 (Windows; x64) node/20"
# 登录态文件：默认 ~/.qoder2api-auth.json；可用 QODER_AUTH_PATH 指向别处
# （Docker/服务器场景挂载到数据卷，如 QODER_AUTH_PATH=/data/qoder-auth.json）
AUTH_PATH = os.environ.get("QODER_AUTH_PATH") or os.path.join(
    os.path.expanduser("~"), ".qoder2api-auth.json"
)

# 上游模型 id 映射（直连接口只认右侧 id；未知 id 原样透传）
MODELS = {
    "qwen3.8-flash": "qwen-flash",   # 免费，零积分
    "qwen3.7-plus": "qmodel",
    "qwen3.5-flash": "qwen-flash",
    "kimi-k2.7-code": "kmodel",
    "deepseek-v4-pro": "dmodel",
    "minimax-m2.5": "mmodel",
}
DEFAULT_MODEL = "qwen3.8-flash"


class QoderError(Exception):
    """直连客户端错误。status 透传上游 HTTP 状态（无则 502）。"""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


# ---------- 登录态存储（与 Qoder2Api 同一文件） ----------

def load_auth() -> dict | None:
    if not os.path.exists(AUTH_PATH):
        return None
    try:
        with open(AUTH_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_auth(data: dict) -> None:
    os.makedirs(os.path.dirname(AUTH_PATH), exist_ok=True)
    with open(AUTH_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def resolve_model(name: str | None) -> str:
    if not name:
        return MODELS[DEFAULT_MODEL]
    low = name.strip().lower().rsplit("/", 1)[-1]
    return MODELS.get(low, low)


def _expired(s: dict) -> bool:
    exp = s.get("expiresAt") or 0
    try:
        return bool(exp) and time.time() * 1000 >= float(exp)
    except (TypeError, ValueError):
        return False


async def _refresh(s: dict) -> str | None:
    rt = s.get("refreshToken")
    if not rt:
        return None
    verifier = s.get("refreshVerifier") or s.get("loginVerifier")
    data: dict[str, str] = {"refresh_token": rt}
    if verifier:
        data["verifier"] = verifier
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(REFRESH_URL, json=data, headers={"User-Agent": UA})
            if resp.status_code >= 400:
                return None
            j = resp.json()
    except Exception:
        return None
    d = j.get("data") or j
    tok = d.get("token") or d.get("access_token") or d.get("accessToken")
    if not tok:
        return None
    s["token"] = tok
    if d.get("refresh_token") or d.get("refreshToken"):
        s["refreshToken"] = d.get("refresh_token") or d.get("refreshToken")
    if d.get("expires_at") or d.get("expiresAt"):
        s["expiresAt"] = d.get("expires_at") or d.get("expiresAt")
    save_auth(s)
    return tok


async def get_token() -> str | None:
    s = load_auth()
    if not s:
        return None
    tok = s.get("token") or ""
    if _expired(s):
        tok = await _refresh(s) or tok
    return tok or None


def _headers(tok: str, accept: str) -> dict:
    return {
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json",
        "Accept": accept,
        "User-Agent": UA,
    }


# ---------- 对话（流式 / 非流式，5xx 与连接错误自动重试） ----------

async def chat_sse_chunks(body: dict, timeout: float = 300.0, retries: int = 3) -> AsyncIterator[dict]:
    tok = await get_token()
    if not tok:
        raise QoderError("qoder not logged in; run: python -m gateway qoder-login", status=401)
    headers = _headers(tok, "text/event-stream")
    last: str | None = None
    for attempt in range(max(1, retries)):
        client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, read=timeout))
        try:
            req = client.build_request("POST", CHAT_URL, headers=headers, json=body)
            resp = await client.send(req, stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            last = str(exc)
            await asyncio.sleep(2 + attempt * 3)
            continue
        if resp.status_code >= 500 and attempt < retries - 1:
            await resp.aread()
            await resp.aclose()
            await client.aclose()
            last = f"HTTP {resp.status_code}"
            await asyncio.sleep(2 + attempt * 3)
            continue
        if resp.status_code >= 400:
            detail = (await resp.aread()).decode("utf-8", "replace")[:300]
            await resp.aclose()
            await client.aclose()
            raise QoderError(f"qoder upstream HTTP {resp.status_code}: {detail}", status=resp.status_code)
        try:
            async for event, data in iter_sse_events(resp):
                if event == "error":
                    raise QoderError(f"qoder stream error: {data[:300]}")
                if data == DONE:
                    return
                if not data:
                    continue
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if isinstance(chunk, dict) and "error" in chunk:
                    raise QoderError(str(chunk["error"])[:300])
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()
        return
    raise QoderError(f"qoder upstream failed after {retries} attempts: {last}")


async def chat_once(body: dict, timeout: float = 180.0, retries: int = 3) -> dict:
    tok = await get_token()
    if not tok:
        raise QoderError("qoder not logged in; run: python -m gateway qoder-login", status=401)
    headers = _headers(tok, "application/json")
    last: str | None = None
    for attempt in range(max(1, retries)):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, read=timeout)) as client:
                resp = await client.post(CHAT_URL, headers=headers, json=body)
                if resp.status_code >= 500 and attempt < retries - 1:
                    last = f"HTTP {resp.status_code}"
                    raise _Retry
                if resp.status_code >= 400:
                    raise QoderError(
                        f"qoder upstream HTTP {resp.status_code}: {resp.text[:300]}",
                        status=resp.status_code,
                    )
                data = resp.json()
        except _Retry:
            await asyncio.sleep(2 + attempt * 3)
            continue
        except httpx.HTTPError as exc:
            last = str(exc)
            await asyncio.sleep(2 + attempt * 3)
            continue
        if isinstance(data, dict) and "error" in data:
            raise QoderError(str(data["error"])[:300])
        return data
    raise QoderError(f"qoder upstream failed after {retries} attempts: {last}")


class _Retry(Exception):
    pass


# ---------- 设备登录（CLI: python -m gateway qoder-login） ----------

def new_pkce() -> tuple[str, str, str]:
    alphabet = string.ascii_letters + string.digits + "-._~"
    verifier = "".join(secrets.choice(alphabet) for _ in range(64))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    nonce = uuid.uuid4().hex
    return verifier, challenge, nonce


def _machine_id() -> str:
    home = os.path.expanduser("~")
    p = os.path.join(home, ".qoder", ".auth", "machine_id")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return f.read().strip()
    p2 = os.path.join(home, ".qoder2api-machine-id")
    if os.path.exists(p2):
        with open(p2, "r", encoding="utf-8") as f:
            return f.read().strip()
    mid = str(uuid.uuid4())
    os.makedirs(os.path.dirname(p2), exist_ok=True)
    with open(p2, "w", encoding="utf-8") as f:
        f.write(mid)
    return mid


def login_url() -> tuple[str, str, str]:
    """生成设备登录 URL 并把 pending verifier/nonce 落盘。返回 (url, verifier, nonce)。"""
    verifier, challenge, nonce = new_pkce()
    mid = _machine_id()
    q = urllib.parse.urlencode(
        {
            "challenge": challenge,
            "challenge_method": "S256",
            "nonce": nonce,
            "machine_id": mid,
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
        }
    )
    url = f"{AUTH_BASE}/device/selectAccounts?{q}"
    s = load_auth() or {}
    s.update({"loginVerifier": verifier, "loginNonce": nonce})
    save_auth(s)
    return url, verifier, nonce


def poll_login(nonce: str, verifier: str, timeout: int = 1800) -> dict:
    """轮询设备登录结果（默认 30 分钟），成功后把 token 写入 AUTH_PATH。"""
    deadline = time.time() + timeout
    with httpx.Client(timeout=30) as client:
        while time.time() < deadline:
            try:
                resp = client.get(
                    POLL_URL,
                    params={"nonce": nonce, "verifier": verifier, "challenge_method": "S256"},
                    headers={"User-Agent": UA, "Accept": "application/json"},
                )
            except httpx.HTTPError:
                time.sleep(3)
                continue
            if resp.status_code == 200:
                try:
                    j = resp.json()
                except Exception:
                    j = {}
                data = j.get("data") or j
                tok = data.get("token") or data.get("access_token") or data.get("accessToken")
                if tok:
                    creds = {
                        "token": tok,
                        "refreshToken": data.get("refresh_token") or data.get("refreshToken") or "",
                        "refreshTokenExpiresAt": data.get("refresh_token_expires_at")
                        or data.get("refreshTokenExpiresAt")
                        or "",
                        "expiresAt": data.get("expires_at") or data.get("expiresAt") or "",
                        "uid": (data.get("user") or {}).get("uid") or data.get("uid") or "",
                    }
                    s = load_auth() or {}
                    s.update(creds)
                    s.pop("loginNonce", None)
                    s.pop("loginVerifier", None)
                    save_auth(s)
                    return creds
            time.sleep(3)
    raise TimeoutError("login timed out; run login again")


# ---------- 账号状态（CLI: python -m gateway qoder-status） ----------

async def status() -> dict[str, Any]:
    tok = await get_token()
    if not tok:
        raise QoderError("qoder not logged in; run: python -m gateway qoder-login", status=401)
    headers = {"Authorization": f"Bearer {tok}", "User-Agent": UA}
    out: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=30) as client:
        for name, url in (("plan", PLAN_URL), ("quota", QUOTA_URL), ("userinfo", USERINFO_URL)):
            try:
                resp = await client.get(url, headers=headers)
                out[name] = resp.json() if resp.status_code < 400 else f"HTTP {resp.status_code}"
            except httpx.HTTPError as exc:
                out[name] = f"error: {exc}"
    return out
