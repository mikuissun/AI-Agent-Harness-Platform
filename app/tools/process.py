"""Internal process primitive. Never registered as a tool."""

import asyncio
import codecs
import ctypes
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from app.harness.models import ToolExecutionResult


def safe_environment() -> dict[str, str]:
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "TEMP", "TMP", "PATHEXT"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update({
        "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C.UTF-8",
    })
    return env


def _run_process(
    argv: list[str], cwd: Path, timeout: float, limit: int, cancelled: threading.Event,
) -> ToolExecutionResult:
    windows = os.name == "nt"
    job = None
    process = None
    readers = []
    chunks: dict[str, list[str]] = {"stdout": [], "stderr": []}
    lock = threading.Lock()
    remaining = limit
    truncated = False
    timed_out = False

    def drain(stream, name):
        nonlocal remaining, truncated
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            raw = stream.read(4096)
            text = decoder.decode(raw, final=not raw)
            with lock:
                if text[:remaining]:
                    chunks[name].append(text[:remaining])
                truncated |= len(text) > remaining
                remaining = max(0, remaining - len(text))
            if not raw:
                break

    try:
        options = {"start_new_session": True} if not windows else {
            "creationflags": subprocess.CREATE_NO_WINDOW | 0x00000004,  # CREATE_SUSPENDED
        }
        process = subprocess.Popen(
            argv, cwd=cwd, env=safe_environment(), shell=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0, **options,
        )
        if windows:
            import win32api
            import win32con
            import win32job

            job = win32job.CreateJobObject(None, "")
            info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
            info["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
            handle = win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, False, process.pid)
            try:
                win32job.AssignProcessToJobObject(job, handle)
                resume = ctypes.WinDLL("ntdll").NtResumeProcess
                resume.argtypes = [ctypes.c_void_p]
                resume.restype = ctypes.c_long
                if resume(int(handle)) != 0:
                    raise OSError("resume_failed")
            finally:
                handle.Close()
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            reader = threading.Thread(target=drain, args=(stream, name), daemon=True)
            reader.start()
            readers.append(reader)
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            timed_out = time.monotonic() >= deadline
            if timed_out or cancelled.is_set():
                break
            cancelled.wait(min(0.02, max(0, deadline - time.monotonic())))
    except FileNotFoundError:
        return ToolExecutionResult(success=False, error="executable_not_found")
    except Exception:
        return ToolExecutionResult(success=False, error="process_start_failed")
    finally:
        if process is not None:
            if windows and job is not None:
                job.Close()  # Kills the entire job, including descendants, even after parent exit.
            elif not windows:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.kill()
            process.wait()
            for reader in readers:
                reader.join()
            process.stdout.close()
            process.stderr.close()
    data = {
        "exit_code": process.returncode,
        "stdout": "".join(chunks["stdout"]), "stderr": "".join(chunks["stderr"]),
        "truncated": truncated,
    }
    error = "cli_timeout" if timed_out else "cli_cancelled" if cancelled.is_set() else None
    if error is None and process.returncode != 0:
        error = "nonzero_exit_code"
    return ToolExecutionResult(success=error is None, data=data, error=error)


async def run_process(argv: list[str], cwd: Path, timeout: float, limit: int) -> ToolExecutionResult:
    cancelled = threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(_run_process, argv, cwd, timeout, limit, cancelled))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancelled.set()
        await asyncio.shield(worker)  # Reap process and readers before propagating cancellation.
        raise
