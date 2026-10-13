"""SSE 解析工具（backends 与 qoder_direct 共用）。"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

DONE = "[DONE]"


def sse_frame(obj: Any) -> bytes:
    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")


DONE_FRAME = b"data: [DONE]\n\n"


async def iter_sse_events(response) -> AsyncIterator[tuple[str | None, str]]:
    """把 SSE 字节流解析成 (event, data)，跳过注释行。"""
    event: str | None = None
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line == "":
            if data_lines:
                yield event, "\n".join(data_lines)
            event, data_lines = None, []
            continue
        if line.startswith(":"):
            continue
        field_name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field_name == "event":
            event = value
        elif field_name == "data":
            data_lines.append(value)
    if data_lines:
        yield event, "\n".join(data_lines)
