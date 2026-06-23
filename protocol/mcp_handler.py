"""
MCP (Model Context Protocol) handler for XiaoZhi.

Processes JSON-RPC 2.0 tool calls received from the server via
WebSocket messages with type "mcp". Maintains a registry of
callable tools that can be invoked by the LLM.
"""

import logging
import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import config

log = logging.getLogger("mcp")

# Tool function: (params: dict) -> dict
ToolFunc = Callable[[dict], Any | Awaitable[Any]]


@dataclass
class Tool:
    func: ToolFunc
    description: str = ""
    input_schema: dict = field(default_factory=lambda: {"type": "object", "properties": {}})


class McpHandler:
    """Registry and dispatcher for MCP tool calls."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        name: str,
        func: ToolFunc,
        description: str = "",
        input_schema: dict | None = None,
    ):
        """Register a tool that the server can invoke."""
        self._tools[name] = Tool(
            func=func,
            description=description,
            input_schema=input_schema or {"type": "object", "properties": {}},
        )
        log.info("registered MCP tool: %s", name)

    def get_descriptors(self) -> list[dict]:
        """Return tool descriptors for IoT/MCP registration with server."""
        return [
            {"name": name, "description": tool.description, "inputSchema": tool.input_schema}
            for name, tool in self._tools.items()
        ]

    async def handle(self, payload: dict) -> tuple[str, dict] | None:
        """Process an incoming MCP message. Returns (id, result) or None."""
        rpc = payload.get("payload", {})
        rpc_id = rpc.get("id")
        method = rpc.get("method", "")
        params = rpc.get("params", {})

        if not rpc_id:
            log.warning("MCP message without id: %s", payload)
            return None

        # Handle MCP protocol handshake
        if method == "initialize":
            log.info("MCP initialize from server (id=%s)", rpc_id)
            return rpc_id, {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "whisplay-xiaozhi", "version": config.APP_VERSION},
            }

        # Handle tools/list — return our registered tools
        if method == "tools/list":
            log.info("MCP tools/list request (id=%s)", rpc_id)
            tools = [
                {"name": name, "description": tool.description, "inputSchema": tool.input_schema}
                for name, tool in self._tools.items()
            ]
            return rpc_id, {"tools": tools}

        # Method format: "tools/call" with tool name in params
        tool_name = params.get("name", method)
        arguments = params.get("arguments", {})

        tool = self._tools.get(tool_name)
        if not tool:
            log.warning("unknown MCP tool: %s", tool_name)
            return rpc_id, {"error": f"Unknown tool: {tool_name}"}

        try:
            result = tool.func(arguments)
            if inspect.isawaitable(result):
                result = await result
            log.info("MCP tool %s executed", tool_name)
            return rpc_id, {"content": [{"type": "text", "text": str(result)}]}
        except Exception as e:
            log.error("MCP tool %s error: %s", tool_name, e)
            return rpc_id, {"error": str(e)}
