# -*- coding: utf-8 -*-
"""PyInstaller 打包入口：把统一模型网关打成 Windows 单文件 exe。

构建（在 model-gateway/ 目录下执行）：
    pyinstaller packaging/model-gateway.spec --noconfirm --clean
产物：dist/model-gateway.exe

运行时注意：
- .env / gateway.db 按“当前工作目录”解析——桌面快捷方式已把起始位置
  设为 model-gateway/，双击即读到项目里的配置；手动跑 exe 时也请在该目录下运行
- Qoder 登录态按用户目录（~/.qoder2api-auth.json 或 QODER_AUTH_PATH）解析，与目录无关
"""
import os
import sys


def _set_console_title() -> None:
    """让服务窗口标题可读（找窗口 / Ctrl+C 停止时用）。"""
    try:
        os.system("title Model Gateway - http://127.0.0.1:8317/v1")
    except Exception:
        pass


def _bootstrap_path() -> None:
    """开发态直跑（python packaging/exe_entry.py）时把项目根加入 sys.path；
    打包后 gateway 已被收集进 exe，此路径不存在也无害。"""
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root and root not in sys.path:
            sys.path.insert(0, root)
    except Exception:
        pass


if __name__ == "__main__":
    _bootstrap_path()
    _set_console_title()
    from gateway.__main__ import main

    main()
