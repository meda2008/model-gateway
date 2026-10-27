# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：model-gateway 单文件 exe（Windows）。

构建（在 model-gateway/ 目录下）：
    pyinstaller packaging/model-gateway.spec --noconfirm --clean
产物：dist/model-gateway.exe（约 25-35MB，自带 Python 运行时与全部依赖）

设计说明：
- uvicorn 的 event loop / protocol / lifespan 是运行时动态 import，必须整包收集，
  否则 exe 启动时才会报 MissingModule（这是 FastAPI 服务打包最常见的坑）
- 不打 .env 与 gateway.db：配置留在项目目录，快捷方式用“起始位置”指向它
- console=True：保留服务日志窗口（看到 listening、报错、Ctrl+C 停止）
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821  (SPECPATH 由 PyInstaller 注入，值为 spec 所在目录，其上级才是项目根)

uv_datas, uv_binaries, uv_hidden = collect_all("uvicorn")
fa_datas, fa_binaries, fa_hidden = collect_all("fastapi")

a = Analysis(
    [str(ROOT / "packaging" / "exe_entry.py")],
    pathex=[str(ROOT)],
    binaries=uv_binaries + fa_binaries,
    datas=uv_datas + fa_datas,
    hiddenimports=uv_hidden
    + fa_hidden
    + [
        "pydantic",
        "pydantic_core",
        "httpx",
        "h11",
        "httpcore",
        "anyio",
        "sniffio",
        "idna",
        "certifi",
        "httptools",
        "websockets",
        "watchfiles",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "PIL", "pyinstaller", "pip", "setuptools"],
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="model-gateway",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "packaging" / "gateway.ico"),
)
