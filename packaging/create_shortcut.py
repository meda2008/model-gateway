# -*- coding: utf-8 -*-
"""在桌面创建"Model Gateway"快捷方式（纯 Python，无 COM / 无 VBS / 无第三方依赖）。

按 MS-SHLLINK 格式手写 .lnk：本环境的安全策略禁用了 PowerShell COM、
WScript.Shell 与 cscript，这个脚本用 struct 直接拼二进制，行为与 Explorer
创建的快捷方式一致。

用法：
    python packaging/create_shortcut.py [目标exe路径] [起始目录] [图标路径]
不传参数时用下方默认值（相对本仓库）。
"""
import os
import struct
import sys
from pathlib import Path

HEADER_SIZE = 0x4C
LINK_CLSID = bytes.fromhex("0114020000000000c000000000000046")

FLAG_HAS_LINK_INFO = 0x02
FLAG_HAS_WORKING_DIR = 0x10
FLAG_HAS_ICON_LOCATION = 0x40
FLAG_IS_UNICODE = 0x80

FILE_ATTRIBUTE_ARCHIVE = 0x20
DRIVE_FIXED = 3
SW_SHOWNORMAL = 1


def _header(flags: int) -> bytes:
    return struct.pack(
        "<I16sIIQQQIIIHHII",
        HEADER_SIZE,
        LINK_CLSID,
        flags,
        FILE_ATTRIBUTE_ARCHIVE,
        0,  # creation time
        0,  # access time
        0,  # write time
        0,  # file size（0 = 让 Explorer 自己解析）
        0,  # icon index
        SW_SHOWNORMAL,
        0,  # hot key
        0,  # reserved1
        0,  # reserved2
        0,  # reserved3
    )


def _link_info(local_base_path: str) -> bytes:
    """LinkInfo：固定卷 C: + LocalBasePath（ANSI，null 结尾）。"""
    drive = os.path.splitdrive(local_base_path)[0] + "\\"
    label = b"\x00"  # 空卷标（只写 null）
    volume_id = struct.pack("<IIII", 0x11, DRIVE_FIXED, 0, 0x10) + label
    base = local_base_path.encode("mbcs") + b"\x00"
    volume_offset = 0x1C
    base_offset = volume_offset + len(volume_id)
    total = 0x1C + len(volume_id) + len(base)
    header = struct.pack(
        "<IIIIIII",
        total,
        0x1C,
        0x01,  # VolumeIDAndLocalBasePath
        volume_offset,
        base_offset,
        0,  # CommonNetworkRelativeLinkOffset
        0,  # CommonPathSuffixOffset
    )
    return header + volume_id + base


def _string_data(flags: int, working_dir: str, icon_location: str) -> bytes:
    """StringData：按 LinkFlags 位序输出（Name, RelativePath, WorkingDir, Arguments, IconLocation）。"""
    out = b""
    if flags & FLAG_HAS_WORKING_DIR:
        raw = working_dir.encode("utf-16-le") + b"\x00\x00"
        out += struct.pack("<H", len(working_dir)) + raw
    if flags & FLAG_HAS_ICON_LOCATION:
        raw = icon_location.encode("utf-16-le") + b"\x00\x00"
        out += struct.pack("<H", len(icon_location)) + raw
    return out


def create_shortcut(lnk_path: str, target: str, working_dir: str, icon: str, description: str = "") -> None:
    flags = FLAG_HAS_LINK_INFO | FLAG_HAS_WORKING_DIR | FLAG_HAS_ICON_LOCATION | FLAG_IS_UNICODE
    data = _header(flags) + _link_info(target) + _string_data(flags, working_dir, icon)
    # description 走 StringData 需要 HasName；为保持最小结构，写入 lnk 文件名即显示名，
    # 描述信息放在 Explorer 属性的"备注"需要 HasName(0x04)——这里补上：
    if description:
        flags |= 0x04
        name_raw = description.encode("utf-16-le") + b"\x00\x00"
        name_block = struct.pack("<H", len(description)) + name_raw
        data = _header(flags) + _link_info(target) + name_block + _string_data(
            flags & ~0x04, working_dir, icon
        )
    data += b"\x00\x00\x00\x00"  # ExtraData 终止（Size=0 < 4，Explorer 视为结束）
    with open(lnk_path, "wb") as f:
        f.write(data)


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    target = sys.argv[1] if len(sys.argv) > 1 else str(root / "dist" / "model-gateway.exe")
    working_dir = sys.argv[2] if len(sys.argv) > 2 else str(root)
    icon = sys.argv[3] if len(sys.argv) > 3 else str(root / "packaging" / "gateway.ico")

    desktop = Path(os.path.expanduser("~")) / "Desktop"
    if not desktop.is_dir():  # OneDrive 重定向等场景：退回 Shell 已知文件夹
        desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    lnk = desktop / "Model Gateway.lnk"

    create_shortcut(
        str(lnk),
        target=target,
        working_dir=working_dir,
        icon=icon,
        description="Unified model gateway (OpenAI compatible) - http://127.0.0.1:8317/v1",
    )
    print("created:", lnk)
    print("  target :", target)
    print("  startin:", working_dir)
    print("  icon   :", icon)


if __name__ == "__main__":
    main()
