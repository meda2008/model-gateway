#!/usr/bin/env python
"""API Key 管理 CLI。

用法（在 model-gateway 目录下）：
    python manage_keys.py add --name cherry-studio [--rpm 60] [--daily 2000]
    python manage_keys.py list
    python manage_keys.py revoke --name cherry-studio
    python manage_keys.py usage [--days 7]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gateway.config import Settings, load_dotenv  # noqa: E402
from gateway.keystore import KeyStore  # noqa: E402


def main() -> None:
    load_dotenv()
    settings = Settings.from_env()
    store = KeyStore(settings.db_path)

    parser = argparse.ArgumentParser(description="统一模型网关 API Key 管理")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="创建新 key（明文仅此次显示）")
    p_add.add_argument("--name", required=True, help="key 用途名，如 cherry-studio")
    p_add.add_argument("--rpm", type=int, default=settings.default_rpm, help="每分钟请求上限")
    p_add.add_argument("--daily", type=int, default=settings.default_daily, help="每日请求上限")

    sub.add_parser("list", help="列出所有 key")

    p_revoke = sub.add_parser("revoke", help="吊销 key")
    p_revoke.add_argument("--name", required=True)

    p_usage = sub.add_parser("usage", help="查看最近用量对账")
    p_usage.add_argument("--days", type=int, default=7)

    args = parser.parse_args()

    if args.cmd == "add":
        raw = store.create(args.name, args.rpm, args.daily)
        print(f"已为 [{args.name}] 创建 API Key（仅此一次显示，请妥善保存）：")
        print(raw)
    elif args.cmd == "list":
        rows = store.list_keys()
        if not rows:
            print("（暂无 key，用 add 子命令创建）")
        for k in rows:
            state = "enabled" if k.enabled else "revoked"
            print(f"{k.name:<24} {k.prefix}...  rpm={k.rpm:<6} daily={k.daily_requests:<7} {state}")
    elif args.cmd == "revoke":
        print("已吊销" if store.revoke(args.name) else "未找到该 name")
    elif args.cmd == "usage":
        rows = store.usage_summary(args.days)
        if not rows:
            print(f"最近 {args.days} 天无调用记录")
        else:
            print(f"{'key':<20}{'backend':<14}{'model':<28}{'calls':>7}{'tokens':>10}{'avg_ms':>9}")
            for key_name, backend, model, calls, tokens, latency in rows:
                avg = int((latency or 0) / calls) if calls else 0
                print(f"{key_name:<20}{backend:<14}{model:<28}{calls:>7}{int(tokens or 0):>10}{avg:>9}")


if __name__ == "__main__":
    main()
