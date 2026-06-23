"""
Local command MCP tool.

Runs allowlisted local commands for XiaoZhi tool calls without invoking a shell.
"""

import asyncio
import logging
import shlex

import config

log = logging.getLogger("mcp.local_command")


INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "Command to run on the device, including arguments.",
        },
        "timeout": {
            "type": "number",
            "description": "Optional timeout in seconds.",
        },
    },
    "required": ["command"],
}

DESCRIPTION = (
    "Run an allowlisted local command on this XiaoZhi device and return stdout, "
    "stderr, and exit code."
)


def is_enabled() -> bool:
    return config.LOCAL_COMMAND_TOOL_ENABLED


def _allowed_commands() -> set[str]:
    return {
        item.strip()
        for item in config.LOCAL_COMMAND_ALLOWLIST.split(",")
        if item.strip()
    }


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


async def run_local_command(params: dict) -> dict:
    command = str(params.get("command", "")).strip()
    if not command:
        raise ValueError("command is required")

    try:
        argv = shlex.split(command)
    except ValueError as e:
        raise ValueError(f"invalid command: {e}") from e

    if not argv:
        raise ValueError("command is required")

    executable = argv[0].rsplit("/", 1)[-1]
    if not config.LOCAL_COMMAND_UNSAFE and executable not in _allowed_commands():
        allowed = ", ".join(sorted(_allowed_commands())) or "(none)"
        raise PermissionError(f"command '{executable}' is not allowlisted. Allowed: {allowed}")

    timeout = params.get("timeout", config.LOCAL_COMMAND_TIMEOUT_SEC)
    try:
        timeout = float(timeout)
    except (TypeError, ValueError) as e:
        raise ValueError("timeout must be a number") from e
    timeout = max(0.1, min(timeout, config.LOCAL_COMMAND_TIMEOUT_SEC))

    log.info("running local command tool: %s", command)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise TimeoutError(f"command timed out after {timeout:g}s")

    output_limit = max(256, config.LOCAL_COMMAND_OUTPUT_LIMIT)
    return {
        "command": command,
        "exit_code": proc.returncode,
        "stdout": _clip(stdout.decode("utf-8", "replace"), output_limit),
        "stderr": _clip(stderr.decode("utf-8", "replace"), output_limit),
    }
