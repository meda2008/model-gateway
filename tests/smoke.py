"""端到端 smoke 测试：用 mock 后端验证鉴权 / 路由 / 流式 / 非流式 / 降级 / 限流。

运行（在 model-gateway 目录下）：
    python tests/smoke.py
环境变量 SMOKE_VERBOSE=1 可打印服务端日志。
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
PORT = 18317
BASE = f"http://127.0.0.1:{PORT}"


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="gw-smoke-")
    db = os.path.join(tmp, "smoke.db")
    log_path = os.path.join(tmp, "server.log")

    env = dict(os.environ)
    env.update(
        {
            "MOCK_BACKEND": "1",
            "MOCK_A_FAIL_FIRST": "1",
            "GATEWAY_DB": db,
            "GATEWAY_PORT": str(PORT),
            "GATEWAY_HOST": "127.0.0.1",
            "FREE_AUTO_CHAIN": "mock_a,mock_b",
            # 死上游用例：切 proxy 模式指向封闭端口（直连模式下 QODER_BASE_URL 被忽略）
            "QODER_MODE": "proxy",
            "QODER_BASE_URL": "http://127.0.0.1:1/v1",
        }
    )

    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "gateway"],
        cwd=ROOT,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    results: list[tuple[str, bool, str]] = []

    def check(name: str, cond: bool, extra: str = "") -> None:
        results.append((name, cond, extra))
        print(("PASS  " if cond else "FAIL  ") + name + (f"   {extra}" if extra else ""))

    try:
        for _ in range(60):
            try:
                if httpx.get(BASE + "/healthz", timeout=1.0).status_code == 200:
                    break
            except Exception:
                time.sleep(0.25)
        else:
            raise SystemExit("server did not become healthy")

        sys.path.insert(0, str(ROOT))
        from gateway.keystore import KeyStore

        store = KeyStore(db)
        key = store.create("smoke", rpm=3, daily_requests=100)
        headers = {"Authorization": f"Bearer {key}"}

        check("healthz lists mock backends", "mock_a" in httpx.get(BASE + "/healthz").json()["backends"])

        r = httpx.get(BASE + "/v1/models")
        check("no-auth /v1/models -> 401", r.status_code == 401)

        r = httpx.post(
            BASE + "/v1/chat/completions",
            json={"model": "free/auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        check("no-auth chat -> 401", r.status_code == 401)

        r = httpx.get(BASE + "/v1/models", headers=headers)
        check("auth /v1/models -> 200", r.status_code == 200, r.text[:100])

        # 流式 + 首包前降级（mock_a 第 1 次故障 -> mock_b 应答）
        with httpx.stream(
            "POST",
            BASE + "/v1/chat/completions",
            headers=headers,
            json={"model": "free/auto", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        ) as resp:
            body = "".join(resp.iter_text())
        check("stream -> 200", resp.status_code == 200)
        check("failover mock_a -> mock_b", "[mock_b]" in body, body[:160].replace("\n", " "))
        check("stream ends with [DONE]", body.strip().endswith("[DONE]"))

        # 非流式（降级链：mock_a 故障额度已在上一次流式请求中消耗，这次直接命中 mock_a）
        r = httpx.post(
            BASE + "/v1/chat/completions",
            headers=headers,
            json={"model": "free/auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        payload = r.json()
        check("non-stream -> 200", r.status_code == 200, r.text[:160])
        check(
            "non-stream content synthesized",
            payload["choices"][0]["message"]["content"].startswith("[mock_"),
        )
        check("model echoed as alias", payload.get("model") == "free/auto")

        # 死上游（qoder 指向封闭端口）：必须是干净的 502 + OpenAI 错误体，不能是 500
        r = httpx.post(
            BASE + "/v1/chat/completions",
            headers=headers,
            json={"model": "qoder/qwen3.8-flash", "messages": [{"role": "user", "content": "hi"}]},
        )
        check(
            "dead upstream -> clean 502",
            r.status_code == 502 and "error" in r.json(),
            f"{r.status_code} {r.text[:100]}",
        )

        # 限流：key 为 rpm=3。前半段 3 个请求不会误触（3 不大于 3）；
        # 突发的 7 个请求无论怎么被整分钟边界切分，总有一段计数 >= 4 > 3，
        # 必然出现 429（对分钟边界竞争免疫）
        codes = [
            httpx.post(
                BASE + "/v1/chat/completions",
                headers=headers,
                json={"model": "free/auto", "messages": [{"role": "user", "content": "hi"}]},
            ).status_code
            for _ in range(7)
        ]
        check("rate limit -> 429", 429 in codes, str(codes))

        # 未知模型
        r = httpx.post(
            BASE + "/v1/chat/completions",
            headers=headers,
            json={"model": "nope", "messages": [{"role": "user", "content": "hi"}]},
        )
        check("unknown model -> 400", r.status_code == 400)

        failed = [name for name, ok, _ in results if not ok]
        print(f"\n{len(results) - len(failed)}/{len(results)} passed")
        if failed:
            raise SystemExit(1)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log_file.close()
        if os.environ.get("SMOKE_VERBOSE"):
            print("---- server log ----")
            print(Path(log_path).read_text(encoding="utf-8", errors="replace"))


if __name__ == "__main__":
    main()
