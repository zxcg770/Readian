"""readian-vault-mcp 的同步客户端封装。

把 vault 读写收敛成标准 MCP 工具调用（stdio 拉起 readian_vault_server.py
子进程），取代 lambda_notify.py 里原本直连 github_client 的做法——
两边最终都是打 GitHub Contents API，区别只是有没有经过标准化的工具层。

一次 VaultSession 内的多次调用共用同一个子进程和握手，
避免每次读一个文件就重新拉起一次 MCP server。
"""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client

from . import config

_MCP_DIR = Path(__file__).resolve().parent.parent / "readian-vault-mcp"
_SERVER_SCRIPT = _MCP_DIR / "readian_vault_server.py"
_SERVER_PYTHON = _MCP_DIR / ".venv" / "bin" / "python"


def _server_params() -> StdioServerParameters:
    # 本地开发用 readian-vault-mcp 自己的 venv；部署到 Lambda 后那个 venv
    # 不会被打进包里，退回用当前解释器（mcp/httpx 跟主依赖一起装进同一个包）。
    python = str(_SERVER_PYTHON) if _SERVER_PYTHON.exists() else sys.executable
    return StdioServerParameters(
        command=python,
        args=[str(_SERVER_SCRIPT)],
        env={
            **os.environ,
            "GITHUB_TOKEN": config.GITHUB_TOKEN,
            "GITHUB_REPO": f"{config.GITHUB_OWNER}/{config.GITHUB_REPO}",
            "GITHUB_BRANCH": config.GITHUB_BRANCH,
        },
    )


class VaultSession:
    """一次会话内可连续调用多个 vault 工具，用完整体关闭子进程。"""

    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "VaultSession":
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(_server_params()))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc) -> None:
        assert self._stack is not None
        await self._stack.aclose()

    async def _call(self, tool: str, **arguments) -> str:
        assert self._session is not None
        result = await self._session.call_tool(tool, arguments)
        return "\n".join(b.text for b in result.content if isinstance(b, types.TextContent))

    async def list_notes(self, folder: str = "") -> list[str]:
        raw = await self._call("list_notes", folder=folder)
        if raw in ("(empty)", "") or raw.startswith(("No such folder", "ERROR")):
            return []
        return [line for line in raw.splitlines() if line and not line.endswith("/")]

    async def read_note(self, path: str) -> str | None:
        text = await self._call("read_note", path=path)
        if text.startswith(("Note not found", "ERROR")):
            return None
        return text

    async def append_to_section(self, path: str, section: str, content: str) -> None:
        result = await self._call("append_to_section", path=path, section=section, content=content)
        if result.startswith("ERROR"):
            raise RuntimeError(result)

    async def write_note(self, path: str, content: str, message: str = "") -> None:
        result = await self._call("write_note", path=path, content=content, message=message)
        if result.startswith("ERROR"):
            raise RuntimeError(result)


# ------------------------------------------------------------ 同步便捷入口
# 单次、低频调用用这几个函数就够了；批量操作（比如扫描一整个目录）
# 应该直接用 `async with VaultSession()`，避免每次调用都重新拉起子进程。


def _run(coro):
    return asyncio.run(coro)


async def _once(method: str, *args, **kwargs):
    async with VaultSession() as vs:
        return await getattr(vs, method)(*args, **kwargs)


def list_notes(folder: str = "") -> list[str]:
    return _run(_once("list_notes", folder))


def read_note(path: str) -> str | None:
    return _run(_once("read_note", path))


def append_to_section(path: str, section: str, content: str) -> None:
    _run(_once("append_to_section", path, section, content))


def write_note(path: str, content: str, message: str = "") -> None:
    _run(_once("write_note", path, content, message))
