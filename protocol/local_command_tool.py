"""
Local command MCP tool.

Runs allowlisted local commands for XiaoZhi tool calls without invoking a shell.
"""

import asyncio
import logging
import os
import shlex
import signal
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

import config

log = logging.getLogger("mcp.local_command")

OutputCallback = Callable[[str | None], None]


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

CHECK_COMMAND_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "job_id": {
            "type": "string",
            "description": "Job id returned by local_command when a command is still running.",
        },
    },
    "required": ["job_id"],
}

STOP_COMMAND_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "job_id": {
            "type": "string",
            "description": "Job id returned by local_command when a command is still running.",
        },
    },
    "required": ["job_id"],
}

DESCRIPTION = (
    "Run an allowlisted local command on this XiaoZhi device and return stdout, "
    "stderr, and exit code. If it is still running after the foreground wait, "
    "returns a job_id for checkCommand or stopCommand."
)

CHECK_COMMAND_DESCRIPTION = (
    "Check a background local command job created by local_command. Returns status, "
    "latest output, and final stdout/stderr/exit_code when complete."
)

STOP_COMMAND_DESCRIPTION = "Stop a running background local command job."


@dataclass
class CommandJob:
    job_id: str
    command: str
    proc: asyncio.subprocess.Process
    output_callback: OutputCallback | None
    include_output: bool = False
    started_at: float = field(default_factory=time.time)
    stdout_chunks: list[bytes] = field(default_factory=list)
    stderr_chunks: list[bytes] = field(default_factory=list)
    status: str = "running"
    exit_code: int | None = None
    error: str = ""
    last_check_at: float = 0.0
    reader_tasks: list[asyncio.Task] = field(default_factory=list)
    monitor_task: asyncio.Task | None = None


_JOBS: dict[str, CommandJob] = {}


async def _stop_job(job: CommandJob):
    job.status = "stopped"

    def signal_group(sig):
        try:
            os.killpg(job.proc.pid, sig)
        except ProcessLookupError:
            pass

    signal_group(signal.SIGTERM)
    finished = asyncio.gather(job.proc.wait(), *job.reader_tasks, return_exceptions=True)
    try:
        await asyncio.wait_for(asyncio.shield(finished), timeout=2)
    except asyncio.TimeoutError:
        pass
    finally:
        signal_group(signal.SIGKILL)
        await finished
        job.exit_code = job.proc.returncode


async def stop_all_commands():
    jobs = tuple(_JOBS.values())
    _JOBS.clear()
    for job in jobs:
        job.output_callback = None
    await asyncio.gather(*(_stop_job(job) for job in jobs if job.status == "running"))


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


def _tail_output(stdout: bytes, stderr: bytes, command: str) -> str:
    text_parts = []
    if stdout:
        text_parts.append(stdout.decode("utf-8", "replace"))
    if stderr:
        text_parts.append(stderr.decode("utf-8", "replace"))
    text = "\n".join(part.rstrip("\n") for part in text_parts if part)
    lines = [line for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if line]
    if not lines:
        lines = [f"$ {command}"]
    return "\n".join(lines[-5:])


def _job_tail(job: CommandJob) -> str:
    return _tail_output(b"".join(job.stdout_chunks), b"".join(job.stderr_chunks), job.command)


def _notify_output(job: CommandJob, status: str = ""):
    if not job.output_callback:
        return
    if job.include_output:
        title = f"$ {job.command}"
        output = "\n".join(
            b"".join(chunks).decode("utf-8", "replace").rstrip("\n")
            for chunks in (job.stdout_chunks, job.stderr_chunks) if chunks
        )
        limit = max(256, config.LOCAL_COMMAND_OUTPUT_LIMIT)
        if len(output) > limit:
            output = "…\n" + output[-limit:]
        job.output_callback("\n".join(part for part in (title, output, status) if part))
    else:
        job.output_callback("\n".join(part for part in (status, _job_tail(job)) if part))


def _finish_output(job: CommandJob, status: str = ""):
    if status:
        _notify_output(job, status)
    if job.output_callback:
        callback, job.output_callback = job.output_callback, None
        callback(None)


def _job_result(job: CommandJob) -> dict:
    output_limit = max(256, config.LOCAL_COMMAND_OUTPUT_LIMIT)
    stdout = b"".join(job.stdout_chunks)
    stderr = b"".join(job.stderr_chunks)
    result = {
        "command": job.command,
        "status": job.status,
        "job_id": job.job_id,
        "running_seconds": round(time.time() - job.started_at, 1),
        "output_tail": _job_tail(job),
        "stdout": _clip(stdout.decode("utf-8", "replace"), output_limit),
        "stderr": _clip(stderr.decode("utf-8", "replace"), output_limit),
    }
    if job.exit_code is not None:
        result["exit_code"] = job.exit_code
    if job.error:
        result["error"] = job.error
    return result


async def _read_stream(
    stream: asyncio.StreamReader | None,
    chunks: list[bytes],
    stream_name: str,
    notify: Callable[[], None],
):
    if not stream:
        return
    while True:
        chunk = await stream.read(512)
        if not chunk:
            return
        chunks.append(chunk)
        log.debug("local command %s chunk: %d bytes", stream_name, len(chunk))
        notify()


async def _monitor_job(job: CommandJob):
    try:
        await job.proc.wait()
        await asyncio.gather(*job.reader_tasks, return_exceptions=True)
        if job.status == "running":
            job.status = "completed"
        job.exit_code = job.proc.returncode
        _finish_output(job, f"job {job.status}")
        log.info("local command job %s finished status=%s exit=%s", job.job_id, job.status, job.exit_code)
    except Exception as e:
        job.status = "error"
        job.error = str(e)
        _finish_output(job, f"job error\n{job.error}")
        log.error("local command job %s monitor error: %s", job.job_id, e)


async def run_local_command(
    params: dict, output_callback: OutputCallback | None = None, *, include_output: bool = False,
) -> dict:
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
    job_id = uuid.uuid4().hex[:8]
    if config.LOCAL_COMMAND_USE_SHELL:
        if not config.LOCAL_COMMAND_UNSAFE:
            raise PermissionError("shell execution requires XIAOZHI_LOCAL_COMMAND_UNSAFE=true")
        if output_callback:
            output_callback(f"$ {command}")
        proc = await asyncio.create_subprocess_shell(
            command,
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    else:
        if output_callback:
            output_callback(f"$ {command}")
        proc = await asyncio.create_subprocess_exec(
            *argv,
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    job = CommandJob(
        job_id=job_id,
        command=command,
        proc=proc,
        output_callback=output_callback,
        include_output=include_output,
    )

    def notify_output():
        _notify_output(job)

    stdout_task = asyncio.create_task(_read_stream(proc.stdout, job.stdout_chunks, "stdout", notify_output))
    stderr_task = asyncio.create_task(_read_stream(proc.stderr, job.stderr_chunks, "stderr", notify_output))
    job.reader_tasks = [stdout_task, stderr_task]

    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
        await asyncio.gather(*job.reader_tasks)
    except asyncio.CancelledError:
        job.output_callback = None
        await _stop_job(job)
        raise
    except asyncio.TimeoutError:
        _JOBS[job.job_id] = job
        job.last_check_at = time.time()
        job.monitor_task = asyncio.create_task(_monitor_job(job))
        _notify_output(job, f"running job {job.job_id}")
        log.info("local command moved to background job=%s after %ss", job.job_id, timeout)
        return {
            "command": command,
            "status": "running",
            "job_id": job.job_id,
            "message": (
                f"Command is still running after {timeout:g}s. "
                f"Use checkCommand with job_id={job.job_id} to get the result, "
                "or stopCommand to stop it."
            ),
            "output_tail": _job_tail(job),
        }

    job.status = "completed"
    job.exit_code = proc.returncode
    _finish_output(job, f"exit {proc.returncode}" if include_output else "")
    output_limit = max(256, config.LOCAL_COMMAND_OUTPUT_LIMIT)
    stdout = b"".join(job.stdout_chunks)
    stderr = b"".join(job.stderr_chunks)
    return {
        "command": command,
        "status": "completed",
        "exit_code": proc.returncode,
        "stdout": _clip(stdout.decode("utf-8", "replace"), output_limit),
        "stderr": _clip(stderr.decode("utf-8", "replace"), output_limit),
    }


async def check_command(params: dict) -> dict:
    job_id = str(params.get("job_id", "")).strip()
    if not job_id:
        raise ValueError("job_id is required")
    job = _JOBS.get(job_id)
    if not job:
        raise ValueError(f"unknown job_id: {job_id}")
    if job.status == "running":
        interval = max(0.0, config.LOCAL_COMMAND_CHECK_INTERVAL_SEC)
        now = time.time()
        elapsed = now - job.last_check_at
        if elapsed < interval:
            delay = interval - elapsed
            log.info("delaying checkCommand for job=%s by %.1fs", job_id, delay)
            await asyncio.sleep(delay)
        job.last_check_at = time.time()
    result = _job_result(job)
    if job.status != "running":
        _JOBS.pop(job_id, None)
    return result


async def stop_command(params: dict) -> dict:
    job_id = str(params.get("job_id", "")).strip()
    if not job_id:
        raise ValueError("job_id is required")
    job = _JOBS.get(job_id)
    if not job:
        raise ValueError(f"unknown job_id: {job_id}")
    if job.status == "running":
        await _stop_job(job)
        _finish_output(job, "job stopped")
    result = _job_result(job)
    _JOBS.pop(job_id, None)
    return result
