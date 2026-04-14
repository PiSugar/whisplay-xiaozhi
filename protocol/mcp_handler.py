"""
MCP (Model Context Protocol) handler for XiaoZhi.

Processes JSON-RPC 2.0 tool calls received from the server via
WebSocket messages with type "mcp". Maintains a registry of
callable tools that can be invoked by the LLM.
"""

import logging
from typing import Any, Callable

log = logging.getLogger("mcp")

# Tool function: (params: dict) -> dict
ToolFunc = Callable[[dict], Any]


class McpHandler:
    """Registry and dispatcher for MCP tool calls."""

    def __init__(self):
        self._tools: dict[str, ToolFunc] = {}

    def register(self, name: str, func: ToolFunc, description: str = ""):
        """Register a tool that the server can invoke."""
        self._tools[name] = func
        log.info("registered MCP tool: %s", name)

    def get_descriptors(self) -> list[dict]:
        """Return tool descriptors for IoT/MCP registration with server."""
        return [{"name": name} for name in self._tools]

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
                "serverInfo": {"name": "whisplay", "version": "1.0.0"},
            }

        # Handle tools/list — return our registered tools
        if method == "tools/list":
            log.info("MCP tools/list request (id=%s)", rpc_id)
            tools = [
                {"name": name, "description": "", "inputSchema": {"type": "object", "properties": {}}}
                for name in self._tools
            ]
            return rpc_id, {"tools": tools}

        # Method format: "tools/call" with tool name in params
        tool_name = params.get("name", method)
        arguments = params.get("arguments", {})

        func = self._tools.get(tool_name)
        if not func:
            log.warning("unknown MCP tool: %s", tool_name)
            return rpc_id, {"error": f"Unknown tool: {tool_name}"}

        try:
            result = func(arguments)
            log.info("MCP tool %s executed", tool_name)
            return rpc_id, {"content": [{"type": "text", "text": str(result)}]}
        except Exception as e:
            log.error("MCP tool %s error: %s", tool_name, e)
            return rpc_id, {"error": str(e)}
