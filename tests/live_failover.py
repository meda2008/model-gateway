"""真实双池降级验证。

起两个临时网关实例（不碰主网关），分别验证两个方向的 failover：
  A: WB 指向无效域名 + Qoder 正常  -> free/auto 应降级到 Qoder 作答
  B: Qoder 指向封闭端口 + WB 正常  -> free/auto 应降级到 WB 作答

前置条件：Qoder2Api 在 61025 跑着；.env 里 WB/Qoder 配置就绪。
运行：python tests/live_failover.py
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
PROMPT = "用一句话介绍西湖"


def run_instance(port: int, env_extra: dict, label: str) -> httpx.Response:
    tmp = tempfile.mkdtemp(prefix="gw-fo-")
    db = os.path.join(tmp, "fo.db")
    env = dict(os.environ)
    env.update(
        {
            "GATEWAY_DB": db,
            "GATEWAY_PORT": str(port),
            "GATEWAY_HOST": "127.0.0.1",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
    )
    env.update(env_extra)  # 覆盖 .env（load_dotenv 用 setdefault，不覆盖已有环境变量）
    log = open(os.path.join(tmp, "srv.log"), "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "gateway"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT
    )
    try:
        for _ in range(80):
            try:
                if (
                    httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1, trust_env=False).status_code
                    == 200
                ):
                    break
            except Exception:
                time.sleep(0.25)
        else:
            raise SystemExit(f"[{label}] instance on {port} did not start")

        sys.path.insert(0, str(ROOT))
        from gateway.keystore import KeyStore

        key = KeyStore(db).create("fo", 30, 500)
        r = httpx.post(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            timeout=240,
            trust_env=False,
            json={
                "model": "free/auto",
                "max_tokens": 600,
                "messages": [{"role": "user", "content": PROMPT}],
            },
        )
        return r
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()


def main() -> None:
    results = []

    # A: WB 挂 -> 降级 Qoder
    r = run_instance(
        18321,
        {"WB_ENDPOINT": "https://invalid.invalid", "WB_PUBLISHABLE_KEY": "wbpk_broken"},
        "A",
    )
    d = r.json()
    ok = r.status_code == 200 and "choices" in d and d["choices"][0]["message"]["content"]
    results.append(("WB 挂 -> 降级到 Qoder", ok, f"{r.status_code} {str(d)[:80]}"))
    print(("PASS  " if ok else "FAIL  ") + f"WB-down -> qoder: {r.status_code} {str(d)[:100]}")

    # B: Qoder 挂（链上放第一位）-> 降级 WB
    # 直连模式下让 qoder 不可达的最简办法：切 proxy 模式指向封闭端口（failover 逻辑与后端形态无关）
    r = run_instance(
        18322,
        {
            "QODER_MODE": "proxy",
            "QODER_BASE_URL": "http://127.0.0.1:1/v1",
            "QODER_API_KEY": "x",
            "FREE_AUTO_CHAIN": "qoder,workbuddy",
        },
        "B",
    )
    d = r.json()
    ok = r.status_code == 200 and "choices" in d and d["choices"][0]["message"]["content"]
    results.append(("Qoder 挂 -> 降级到 WB", ok, f"{r.status_code} {str(d)[:80]}"))
    print(("PASS  " if ok else "FAIL  ") + f"qoder-down -> WB: {r.status_code} {str(d)[:100]}")

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
