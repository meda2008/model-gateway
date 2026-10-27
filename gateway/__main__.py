"""入口：
    python -m gateway                启动网关（默认 Qoder 直连模式，单进程）
    python -m gateway qoder-login    Qoder 设备登录（直连模式用）
    python -m gateway qoder-status   Qoder 账号/积分状态
    python -m gateway qoder-probe    探测 Qoder 上游可用模型 id（Qoder 上新后用）
"""
import argparse
import asyncio
import json
import logging
import socket
import sys
import time
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


def _port_in_use(host: str, port: int) -> bool:
    """双击启动器的高频误操作：服务已在跑时端口被占。预检并友好提示，
    避免 uvicorn 抛 [WinError 10048] 堆栈、黑框闪退看不懂。"""
    check_host = "127.0.0.1" if host in ("", "0.0.0.0") else host
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((check_host, port)) == 0
    except OSError:
        return False


def _open_browser(url: str) -> None:
    """尽量打开默认浏览器到给定地址；环境无浏览器或被拦截时静默忽略。"""
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _open_browser_when_ready(url: str, port: int, timeout: float = 20.0) -> None:
    """后台轮询 healthz，等服务真正就绪后再打开浏览器（避免提前打开 404）。"""
    import threading
    import urllib.request

    def _t() -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                pass
            time.sleep(0.3)
        _open_browser(url)

    threading.Thread(target=_t, daemon=True).start()


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


def _qoder_probe() -> None:
    from . import qoder_direct as qd

    print("  探测 Qoder 上游候选模型（每个 1 token；付费模型会消耗极少试用积分）...")
    try:
        results = asyncio.run(qd.probe_models())
    except Exception as exc:
        print("  探测失败: " + str(exc))
        sys.exit(1)
    accepted = [m for m, ok in results.items() if ok]
    rejected = [m for m, ok in results.items() if not ok]
    print("  可用  : " + (", ".join(accepted) or "(无)"))
    print("  不可用: " + (", ".join(rejected) or "(无)"))
    print("  确认新模型后，.env 加 QODER_EXTRA_MODELS=<proxy-id>=<upstream-id> 即可接入")


def main() -> None:
    _ensure_utf8_stdio()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="gateway", description="统一模型网关")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("qoder-login", help="Qoder 设备登录（直连模式）")
    sub.add_parser("qoder-status", help="Qoder 账号/积分状态")
    sub.add_parser("qoder-probe", help="探测 Qoder 上游可用模型 id（Qoder 上新后用）")
    args = parser.parse_args()

    load_dotenv()
    if args.cmd == "qoder-login":
        _qoder_login()
        return
    if args.cmd == "qoder-status":
        _qoder_status()
        return
    if args.cmd == "qoder-probe":
        _qoder_probe()
        return

    settings = Settings.from_env()
    port = settings.port
    landing = f"http://127.0.0.1:{port}/"

    if _port_in_use(settings.host, port):
        print(f"  端口 {port} 已被占用——服务已在运行：{landing}")
        print("  正在为你打开状态页（无需重复启动）。")
        _open_browser(landing)
        time.sleep(4)  # 让本窗口停留几秒，明确“已打开”，避免黑窗一闪被误判为打不开
        return

    # 首次启动：等服务就绪后自动打开浏览器状态页，给“双击”一个明确反馈
    _open_browser_when_ready(landing, port)
    uvicorn.run(create_app(settings), host=settings.host, port=port, log_level="info")


if __name__ == "__main__":
    main()
