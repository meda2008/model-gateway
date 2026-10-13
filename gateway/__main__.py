"""入口：
    python -m gateway                启动网关（默认 Qoder 直连模式，单进程）
    python -m gateway qoder-login    Qoder 设备登录（直连模式用）
    python -m gateway qoder-status   Qoder 账号/积分状态
"""
import argparse
import asyncio
import json
import logging
import sys
import webbrowser

import uvicorn

from .app import create_app
from .config import Settings, load_dotenv


def _ensure_utf8_stdio() -> None:
    """Windows 中文控制台默认 GBK 代码页，重配置为 UTF-8 避免打印崩溃（Python 3.7+）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _qoder_login() -> None:
    from . import qoder_direct as qd

    url, verifier, nonce = qd.login_url()
    print("\n  请在浏览器打开并批准登录:\n\n    " + url + "\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    print("  等待批准（最长 30 分钟）...")
    try:
        qd.poll_login(nonce, verifier, timeout=1800)
    except TimeoutError as exc:
        print("  " + str(exc))
        sys.exit(1)
    print("  登录成功，token 已存 " + qd.AUTH_PATH)


def _qoder_status() -> None:
    from . import qoder_direct as qd

    try:
        info = asyncio.run(qd.status())
    except Exception as exc:
        print("  查询失败: " + str(exc))
        sys.exit(1)
    print(json.dumps(info, ensure_ascii=False, indent=2))


def main() -> None:
    _ensure_utf8_stdio()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="gateway", description="统一模型网关")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("qoder-login", help="Qoder 设备登录（直连模式）")
    sub.add_parser("qoder-status", help="Qoder 账号/积分状态")
    args = parser.parse_args()

    load_dotenv()
    if args.cmd == "qoder-login":
        _qoder_login()
        return
    if args.cmd == "qoder-status":
        _qoder_status()
        return

    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
