"""网关配置：全部来自环境变量（可用 .env 文件，见 .env.example）。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _ids(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def load_dotenv(path: str = ".env") -> None:
    """极简 .env 加载：不覆盖已存在的环境变量。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), value)


@dataclass
class BackendConfig:
    enabled: bool = False
    base_url: str = ""
    api_key: str = ""
    origin: str = ""
    models: list[str] = field(default_factory=list)
    default_model: str = ""
    default_system: str = "You are a helpful assistant. Be concise and helpful."


@dataclass
class Settings:
    host: str
    port: int
    db_path: str
    default_rpm: int
    default_daily: int
    request_timeout: float
    free_auto_chain: list[str]
    qoder_mode: str
    workbuddy: BackendConfig
    qoder: BackendConfig

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=os.environ.get("GATEWAY_HOST", "127.0.0.1"),
            port=int(os.environ.get("GATEWAY_PORT", "8317")),
            db_path=os.environ.get("GATEWAY_DB", "./gateway.db"),
            default_rpm=int(os.environ.get("DEFAULT_RPM", "60")),
            default_daily=int(os.environ.get("DEFAULT_DAILY_REQUESTS", "2000")),
            request_timeout=float(os.environ.get("REQUEST_TIMEOUT", "180")),
            free_auto_chain=_ids(os.environ.get("FREE_AUTO_CHAIN", "workbuddy,qoder")),
            qoder_mode=os.environ.get("QODER_MODE", "direct").strip().lower(),
            workbuddy=BackendConfig(
                enabled=bool(os.environ.get("WB_ENDPOINT") and os.environ.get("WB_PUBLISHABLE_KEY")),
                base_url=os.environ.get("WB_ENDPOINT", "").rstrip("/"),
                api_key=os.environ.get("WB_PUBLISHABLE_KEY", ""),
                origin=os.environ.get("WB_ORIGIN", ""),
                models=_ids(os.environ.get("WB_MODELS", "")),
                default_model=os.environ.get("WB_DEFAULT_MODEL", ""),
                default_system=os.environ.get(
                    "WB_DEFAULT_SYSTEM", "You are a helpful assistant. Be concise and helpful."
                ),
            ),
            qoder=BackendConfig(
                enabled=bool(os.environ.get("QODER_BASE_URL")),
                base_url=os.environ.get("QODER_BASE_URL", "").rstrip("/"),
                api_key=os.environ.get("QODER_API_KEY", "sk-local"),
                origin="",
                models=_ids(os.environ.get("QODER_MODELS", "qwen3.8-flash")),
                default_model=os.environ.get("QODER_DEFAULT_MODEL", "qwen3.8-flash"),
            ),
        )
