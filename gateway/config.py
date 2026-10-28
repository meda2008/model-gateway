"""网关配置：全部来自环境变量（可用 .env 文件，见 .env.example）。"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

# 实际找到 .env 的目录；作为 gateway.db 等相对路径的解析基准。
# 默认先取 cwd，保证纯开发态（`python -m gateway` 且 cwd=项目根）行为不变。
CONFIG_DIR = os.getcwd()


def _ids(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def _config_search_dirs() -> list[str]:
    """配置文件（.env / gateway.db）的候选查找目录，按优先级：
    1. 当前工作目录（开发态 `python -m gateway` 时即项目根）
    2. 被启动 exe 的真实所在目录（onefile 打包后落在 dist/）
    3. 项目根（dist 的父目录——配置实际存放处）

    打包成独立 exe 后双击运行时，cwd 未必是项目根，必须回退到
    exe 所在目录 / 项目根才能找到外置的 .env 与 gateway.db。
    """
    dirs: list[str] = []
    cwd = os.getcwd()
    if cwd:
        dirs.append(cwd)
    try:
        # onefile 下 sys.argv[0] 是用户双击的那个原始 exe 路径（非 _MEIxxxx 临时解包路径）
        exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    except Exception:
        exe_dir = ""
    if exe_dir and exe_dir not in dirs:
        dirs.append(exe_dir)
    if exe_dir:
        proj_root = os.path.dirname(exe_dir)
        if proj_root and proj_root not in dirs:
            dirs.append(proj_root)
    return dirs


def load_dotenv(path: str = ".env") -> None:
    """极简 .env 加载：不覆盖已存在的环境变量。
    若 path 为相对路径，按候选目录（cwd → exe 目录 → 项目根）查找，
    并记录找到 .env 的目录供 db_path 等相对路径解析使用。"""
    global CONFIG_DIR
    if os.path.isabs(path):
        _apply_dotenv(path)
        return
    for d in _config_search_dirs():
        p = os.path.join(d, path)
        if os.path.exists(p):
            _apply_dotenv(p)
            CONFIG_DIR = d
            return


def _apply_dotenv(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), value)


def _resolve_db_path(raw: str) -> str:
    """gateway.db 路径：绝对路径直接用；相对路径基于 .env 所在目录（CONFIG_DIR）
    解析，避免打包后 cwd 错位导致在错误目录创建/读取空库。"""
    if os.path.isabs(raw):
        return raw
    return os.path.join(CONFIG_DIR, raw)


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
    qoder_fallback: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=os.environ.get("GATEWAY_HOST", "127.0.0.1"),
            port=int(os.environ.get("GATEWAY_PORT", "8317")),
            db_path=_resolve_db_path(os.environ.get("GATEWAY_DB", "gateway.db")),
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
            qoder_fallback=os.environ.get("QODER_FALLBACK", "").strip(),
        )
