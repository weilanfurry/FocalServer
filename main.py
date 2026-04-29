from __future__ import annotations

import os
import sys
import time
import shutil
import traceback
import asyncio
from pathlib import Path
from subprocess import CompletedProcess, run, Popen, PIPE
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


ROOT_DIR = Path(__file__).resolve().parent
APP_DIR = ROOT_DIR / "app"
TEMPLATES_DIR = ROOT_DIR / "web" / "templates"
STATIC_DIR = ROOT_DIR / "web" / "static"

MAX_OUTPUT_CHARS = 200_000
DEFAULT_TIMEOUT_SEC = 10.0

app = FastAPI(title="SkillBottle Lite", version="1.0.0")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
DEBUG = (os.environ.get("SKILLBOTTLE_DEBUG") or "").strip() in {"1", "true", "yes", "on"}

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _safe_rel_path(rel_path: str) -> Path:
    # Disallow absolute paths and traversal outside APP_DIR
    if not rel_path or "\x00" in rel_path:
        raise HTTPException(status_code=400, detail="Invalid path")
    p = Path(rel_path)
    if p.is_absolute():
        raise HTTPException(status_code=400, detail="Absolute paths not allowed")
    resolved = (APP_DIR / p).resolve()
    try:
        resolved.relative_to(APP_DIR.resolve())
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=400, detail="Path escapes app/") from e
    return resolved


def _preferred_entry() -> str | None:
    if (APP_DIR / "main.py").is_file():
        return "main.py"
    if (APP_DIR / "app.py").is_file():
        return "app.py"
    # Fallback: pick the first Python file under app/
    try:
        candidates = sorted(
            [p for p in APP_DIR.rglob("*.py") if p.is_file()],
            key=lambda p: p.relative_to(APP_DIR).as_posix(),
        )
    except Exception:
        candidates = []
    if candidates:
        return candidates[0].relative_to(APP_DIR).as_posix()
    return None


def _truncate(s: str) -> str:
    if len(s) <= MAX_OUTPUT_CHARS:
        return s
    head = s[: MAX_OUTPUT_CHARS // 2]
    tail = s[-(MAX_OUTPUT_CHARS // 2) :]
    return head + "\n\n... (output truncated) ...\n\n" + tail


def _run_python(
    script_name: str,
    stdin_text: str,
    args: list[str],
    timeout_sec: float,
    python_mode: str,
) -> dict[str, Any]:
    if not APP_DIR.exists():
        raise HTTPException(status_code=400, detail="Missing app/ directory")

    script_path = _safe_rel_path(script_name)
    if not script_path.is_file():
        raise HTTPException(status_code=404, detail=f"Not found: app/{script_name}")

    cmd, python_mode = _build_python_cmd(python_mode, script_path, args or [])
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    start = time.perf_counter()
    try:
        cp: CompletedProcess[str] = run(
            cmd,
            cwd=str(APP_DIR),
            input=stdin_text or "",
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_sec,
            env=env,
        )
        duration_ms = int((time.perf_counter() - start) * 1000)
        return {
            "ok": True,
            "used_entry": script_name,
            "python_mode": python_mode,
            "python_executable": cmd[0] if cmd else None,
            "exit_code": cp.returncode,
            "duration_ms": duration_ms,
            "stdout": _truncate(cp.stdout or ""),
            "stderr": _truncate(cp.stderr or ""),
            "cmd": cmd,
        }
    except Exception as e:
        duration_ms = int((time.perf_counter() - start) * 1000)
        return {
            "ok": False,
            "used_entry": script_name,
            "python_mode": python_mode,
            "python_executable": cmd[0] if cmd else None,
            "duration_ms": duration_ms,
            "error": f"{type(e).__name__}: {e}",
            "cmd": cmd,
        }


def _list_files() -> list[dict[str, Any]]:
    if not APP_DIR.exists():
        return []
    files: list[dict[str, Any]] = []
    for p in sorted(APP_DIR.rglob("*")):
        if p.is_dir():
            continue
        # Skip common noise
        parts = {x.lower() for x in p.parts}
        if "__pycache__" in parts:
            continue
        rel = p.relative_to(APP_DIR).as_posix()
        try:
            size = p.stat().st_size
        except OSError:
            size = None
        files.append({"path": rel, "size": size})
    return files


def _list_entries() -> list[str]:
    """
    Entrypoints are Python files under app/.
    Returned values are POSIX-style paths relative to app/ (e.g. "main.py", "tools/foo.py").
    """
    if not APP_DIR.exists():
        return []
    paths = []
    for p in sorted(APP_DIR.rglob("*.py")):
        if not p.is_file():
            continue
        parts = {x.lower() for x in p.parts}
        if "__pycache__" in parts:
            continue
        paths.append(p.relative_to(APP_DIR).as_posix())
    return paths


def _system_python_cmd() -> list[str] | None:
    # Preferred: explicit path from start.ps1 (or user environment)
    env_py = (os.environ.get("SKILLBOTTLE_SYSTEM_PYTHON") or "").strip()
    if env_py:
        p = Path(env_py)
        if p.is_file():
            return [env_py]

    # Fallback: try PATH
    py = shutil.which("python")
    if py:
        return [py]
    py3 = shutil.which("python3")
    if py3:
        return [py3]
    launcher = shutil.which("py")
    if launcher:
        return ["py", "-3"]
    return None


def _build_python_cmd(python_mode: str, script_path: Path, args: list[str]) -> tuple[list[str], str]:
    mode = (python_mode or "server").strip().lower()
    if mode in {"system", "sys", "global"}:
        base = _system_python_cmd() or [sys.executable]
        mode = "system"
    else:
        base = [sys.executable]
        mode = "server"
    return list(base) + [str(script_path)] + list(args or []), mode


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    print(f"[unhandled] {request.method} {request.url.path}\n{tb}", file=sys.stderr)
    payload: dict[str, Any] = {"detail": "Internal Server Error"}
    if DEBUG:
        payload["error"] = f"{type(exc).__name__}: {exc}"
        payload["traceback"] = tb
    return JSONResponse(payload, status_code=500)


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    preferred = _preferred_entry()
    context = {
        "request": request,
        "preferred_entry": preferred,
        "has_app_dir": APP_DIR.exists(),
    }

    # Starlette templating signature changed across versions.
    # Some versions expect: TemplateResponse(name, context, ...)
    # Others expect: TemplateResponse(request, name, context, ...)
    try:
        return templates.TemplateResponse("index.html", context)
    except TypeError:
        return templates.TemplateResponse(request, "index.html", context)


async def _ws_send_json_safe(ws: WebSocket, payload: dict[str, Any]) -> None:
    try:
        await ws.send_json(payload)
    except Exception:
        # Client probably disconnected
        return


def _decode_bytes(data: bytes) -> str:
    # Keep server resilient regardless of console/codepage.
    return data.decode("utf-8", errors="replace")


async def _pump_stream_to_ws(
    ws: WebSocket,
    stream,
    stream_type: str,
) -> None:
    # stream: BufferedReader (blocking)
    while True:
        chunk = await asyncio.to_thread(stream.read, 4096)
        if not chunk:
            return
        await _ws_send_json_safe(ws, {"type": stream_type, "data": _decode_bytes(chunk)})


@app.websocket("/ws/run")
async def ws_run(ws: WebSocket) -> None:
    await ws.accept()

    proc: Popen[bytes] | None = None
    tasks: list[asyncio.Task[None]] = []

    async def stop_proc(send_exit: bool = True) -> None:
        nonlocal proc, tasks
        for t in tasks:
            t.cancel()
        tasks = []
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
        try:
            code = await asyncio.to_thread(proc.wait, 3)
        except Exception:
            code = proc.poll()
        if send_exit:
            await _ws_send_json_safe(ws, {"type": "exit", "exit_code": code})
        proc = None

    try:
        while True:
            msg = await ws.receive_json()
            mtype = (msg.get("type") or "").strip().lower()

            if mtype == "start":
                if proc is not None:
                    await stop_proc(send_exit=False)

                entry = (msg.get("entry") or "").strip()
                args = msg.get("args") or []
                python_mode = (msg.get("python_mode") or "server").strip().lower()
                if python_mode not in {"", "server", "system", "sys", "global"}:
                    await _ws_send_json_safe(ws, {"type": "error", "error": "python_mode must be 'server' or 'system'"})
                    continue
                if not isinstance(args, list) or any(not isinstance(x, str) for x in args):
                    await _ws_send_json_safe(ws, {"type": "error", "error": "args must be a list of strings"})
                    continue

                if not entry:
                    preferred = _preferred_entry()
                    if not preferred:
                        await _ws_send_json_safe(ws, {"type": "error", "error": "No entry found under app/ (no .py files)"})
                        continue
                    entry = preferred

                script_path = _safe_rel_path(entry)
                if not script_path.is_file():
                    await _ws_send_json_safe(ws, {"type": "error", "error": f"Not found: app/{entry}"})
                    continue

                cmd, python_mode = _build_python_cmd(python_mode, script_path, args or [])
                env = dict(os.environ)
                env["PYTHONUNBUFFERED"] = "1"
                env["PYTHONUTF8"] = "1"
                env["PYTHONIOENCODING"] = "utf-8"

                try:
                    proc = Popen(
                        cmd,
                        cwd=str(APP_DIR),
                        stdin=PIPE,
                        stdout=PIPE,
                        stderr=PIPE,
                        bufsize=0,
                        env=env,
                    )
                except Exception as e:
                    proc = None
                    await _ws_send_json_safe(ws, {"type": "error", "error": f"{type(e).__name__}: {e}"})
                    continue

                assert proc.stdout is not None
                assert proc.stderr is not None
                tasks = [
                    asyncio.create_task(_pump_stream_to_ws(ws, proc.stdout, "stdout")),
                    asyncio.create_task(_pump_stream_to_ws(ws, proc.stderr, "stderr")),
                ]

                await _ws_send_json_safe(
                    ws,
                    {
                        "type": "started",
                        "used_entry": entry,
                        "python_mode": python_mode,
                        "python_executable": cmd[0] if cmd else None,
                        "cmd": cmd,
                    },
                )

                async def _waiter(p: Popen[bytes]) -> None:
                    code = await asyncio.to_thread(p.wait)
                    await _ws_send_json_safe(ws, {"type": "exit", "exit_code": code})

                tasks.append(asyncio.create_task(_waiter(proc)))
                continue

            if mtype == "stdin":
                if proc is None or proc.stdin is None or proc.poll() is not None:
                    await _ws_send_json_safe(ws, {"type": "error", "error": "process not running"})
                    continue
                data = msg.get("data")
                if not isinstance(data, str):
                    await _ws_send_json_safe(ws, {"type": "error", "error": "stdin data must be a string"})
                    continue
                try:
                    await asyncio.to_thread(proc.stdin.write, data.encode("utf-8", errors="replace"))
                    await asyncio.to_thread(proc.stdin.flush)
                except Exception as e:
                    await _ws_send_json_safe(ws, {"type": "error", "error": f"{type(e).__name__}: {e}"})
                continue

            if mtype == "stop":
                await stop_proc(send_exit=True)
                continue

            await _ws_send_json_safe(ws, {"type": "error", "error": f"unknown message type: {mtype!r}"})
    except WebSocketDisconnect:
        pass
    except Exception:
        # Ensure we don't leak processes on server errors
        tb = "".join(traceback.format_exception(*sys.exc_info()))
        print(f"[ws-run error]\n{tb}", file=sys.stderr)
    finally:
        try:
            await stop_proc(send_exit=False)
        except Exception:
            pass


@app.get("/api/status")
def status() -> JSONResponse:
    entries = _list_entries()
    return JSONResponse(
        {
            "app_dir": str(APP_DIR),
            "has_app_dir": APP_DIR.exists(),
            "preferred_entry": _preferred_entry(),
            "available_entries": entries,
            "available_entries_count": len(entries),
            "files_count": len(_list_files()),
            "server_python": sys.executable,
            "system_python": (os.environ.get("SKILLBOTTLE_SYSTEM_PYTHON") or None),
        }
    )


@app.get("/api/entries")
def entries() -> JSONResponse:
    return JSONResponse({"entries": _list_entries(), "preferred_entry": _preferred_entry()})


@app.get("/api/files")
def files() -> JSONResponse:
    return JSONResponse({"files": _list_files()})


@app.get("/api/file")
def file(path: str) -> JSONResponse:
    p = _safe_rel_path(path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return JSONResponse({"path": path, "content": content})


@app.post("/api/run")
async def api_run(request: Request) -> JSONResponse:
    data = await request.json()
    entry = (data.get("entry") or "").strip()
    stdin_text = data.get("stdin") or ""
    args = data.get("args") or []
    timeout_sec = data.get("timeout_sec") or DEFAULT_TIMEOUT_SEC
    python_mode = (data.get("python_mode") or "").strip().lower()
    if python_mode not in {"", "server", "system", "sys", "global"}:
        raise HTTPException(status_code=400, detail="python_mode must be 'server' or 'system'")

    if not isinstance(args, list) or any(not isinstance(x, str) for x in args):
        raise HTTPException(status_code=400, detail="args must be a list of strings")
    try:
        timeout_sec = float(timeout_sec)
    except Exception:
        raise HTTPException(status_code=400, detail="timeout_sec must be a number")
    if timeout_sec <= 0 or timeout_sec > 120:
        raise HTTPException(status_code=400, detail="timeout_sec out of range (0, 120]")

    if not entry:
        preferred = _preferred_entry()
        if not preferred:
            raise HTTPException(status_code=400, detail="No entry found under app/ (no .py files)")
        entry = preferred

    result = _run_python(entry, str(stdin_text), args, timeout_sec, python_mode or "server")
    return JSONResponse(result)
