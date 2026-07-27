#!/usr/bin/env python3
"""FastAPI web server for the M3U file generator.

Security model:
- Binds to 127.0.0.1 by default (override with IPTV_HOST). Refuses to bind a
  non-loopback address unless IPTV_API_KEY is set.
- When IPTV_API_KEY is set, all /api/* endpoints require the X-API-Key
  header, and the log WebSocket requires ?api_key=....
- Scrape URLs and process input files are validated (SSRF + path traversal
  guards). Credentials in broadcast log lines are redacted.
- Only one scrape/process job runs at a time (409 if busy).
"""
import asyncio
import ipaddress
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from netguard import is_public_http_url

API_KEY = os.environ.get("IPTV_API_KEY")
HOST = os.environ.get("IPTV_HOST", "127.0.0.1")
PORT = int(os.environ.get("IPTV_PORT", "8000"))

app = FastAPI()

# CORS: same-origin UI needs no CORS at all; restrict to localhost origins
# and no credentials.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://localhost:{PORT}",
        f"http://127.0.0.1:{PORT}",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Content-Type"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Redact credentials embedded in get.php URLs before broadcasting logs.
_CRED_RE = re.compile(r'(username=)[^&\s]+(&(?:password=)[^&\s]+)', re.IGNORECASE)


def _redact(message: str) -> str:
    return _CRED_RE.sub(r'\1***\2***', message)


# Store active websocket connections
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        # Iterate over a snapshot: disconnect() may mutate the list while a
        # broadcast is in flight.
        dead = []
        for connection in list(self.active_connections):
            try:
                await connection.send_text(_redact(message))
            except Exception:
                # Drop dead connections instead of silently swallowing.
                dead.append(connection)
        for connection in dead:
            self.disconnect(connection)


manager = ConnectionManager()

# Only one scrape/process job at a time.
job_lock = asyncio.Lock()


class ScrapeRequest(BaseModel):
    url: str


class ProcessRequest(BaseModel):
    input_file: str = "iptv_m3u_urls.txt"


class ConfigSaveRequest(BaseModel):
    content: Dict[str, Any]


EDITABLE_CONFIGS = ["m3u_filter_config.json"]


def verify_api_key(api_key: Optional[str] = None):
    """Raise 403 unless the request carries the configured API key.

    Auth is only enforced when IPTV_API_KEY is set; with no key configured the
    server is expected to be bound to loopback only (enforced at startup).
    """
    if API_KEY is None:
        return
    if api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")


async def run_script(command: List[str]):
    """Run a script and stream output to websockets"""
    async with job_lock:
        await manager.broadcast(f"Debug: Starting script execution: {' '.join(command)}")
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )

            while True:
                line = await process.stdout.readline()
                if not line:
                    break

                # Use rstrip() to remove trailing newline but preserve leading whitespace (indentation)
                decoded_line = line.decode().rstrip()
                await manager.broadcast(decoded_line)

            await process.wait()
            await manager.broadcast(f"Process finished with exit code {process.returncode}")
        except Exception as e:
            print(f"CRITICAL ERROR in run_script: {e}")
            await manager.broadcast("CRITICAL ERROR: script execution failed (see server log)")


def _resolve_input_file(input_file: str) -> str:
    """Validate a user-supplied input filename and return it if acceptable.

    Must be a relative path that stays inside the project directory.
    """
    candidate = Path(input_file)
    if candidate.is_absolute():
        raise HTTPException(status_code=400, detail="input_file must be a relative path")
    if ".." in candidate.parts:
        raise HTTPException(status_code=400, detail="input_file must not contain '..'")
    resolved = (Path.cwd() / candidate).resolve()
    if Path.cwd() not in resolved.parents:
        raise HTTPException(status_code=400, detail="input_file must be inside the project directory")
    return str(candidate)


@app.get("/")
async def get():
    return FileResponse('static/index.html')


@app.websocket("/ws/logs")
async def websocket_endpoint(websocket: WebSocket):
    if API_KEY is not None and websocket.query_params.get("api_key") != API_KEY:
        # Accept first: closing before the handshake completes makes the server
        # reject it as HTTP 403, and the browser then reports close code 1006
        # instead of 4401, so the client can't tell auth failures apart from
        # ordinary network drops.
        await websocket.accept()
        await websocket.close(code=4401)
        return
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # Keep connection alive
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.post("/api/scrape")
async def run_scraper(request: ScrapeRequest, http_request: Request):
    verify_api_key(http_request.headers.get("X-API-Key"))

    is_safe, reason = is_public_http_url(request.url)
    if not is_safe:
        raise HTTPException(status_code=400, detail=f"URL rejected: {reason}")

    if job_lock.locked():
        raise HTTPException(status_code=409, detail="Another job is already running")

    cmd = [sys.executable, "-u", "iptv_scraper.py", "--url", request.url]
    asyncio.create_task(run_script(cmd))
    return {"status": "started", "command": " ".join(cmd)}


@app.post("/api/process")
async def run_processor(request: ProcessRequest, http_request: Request):
    verify_api_key(http_request.headers.get("X-API-Key"))

    if job_lock.locked():
        raise HTTPException(status_code=409, detail="Another job is already running")

    # Ensure we use the file if it exists, otherwise fallback to urls.txt
    input_file = _resolve_input_file(request.input_file)
    if not os.path.exists(input_file):
        await manager.broadcast(f"Warning: {input_file} not found. Falling back to urls.txt")
        input_file = "urls.txt"

    cmd = [sys.executable, "-u", "m3u_processor.py", "--input", input_file]
    asyncio.create_task(run_script(cmd))
    return {"status": "started", "command": " ".join(cmd)}


@app.get("/api/download")
async def download_file(http_request: Request):
    verify_api_key(http_request.headers.get("X-API-Key"))
    file_path = Path("master_iptv.m3u")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="master_iptv.m3u not found")
    return FileResponse(file_path, filename="master_iptv.m3u", media_type='audio/x-mpegurl')


@app.get("/api/configs")
async def list_configs(http_request: Request):
    verify_api_key(http_request.headers.get("X-API-Key"))
    return {"files": EDITABLE_CONFIGS}


@app.get("/api/config/{filename}")
async def get_config(filename: str, http_request: Request):
    verify_api_key(http_request.headers.get("X-API-Key"))
    if filename not in EDITABLE_CONFIGS:
        raise HTTPException(status_code=404, detail="Config file not found or not editable")

    if not os.path.exists(filename):
        raise HTTPException(status_code=404, detail="File does not exist")

    try:
        with open(filename, 'r', encoding='utf-8') as f:
            content = json.load(f)
        return content
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error reading config {filename}: {e}")
        raise HTTPException(status_code=500, detail="Failed to read config file")


@app.post("/api/config/{filename}")
async def save_config(filename: str, request: ConfigSaveRequest, http_request: Request):
    verify_api_key(http_request.headers.get("X-API-Key"))
    if filename not in EDITABLE_CONFIGS:
        raise HTTPException(status_code=403, detail="Not allowed to edit this file")

    # Atomic write: write to a temp file, then os.replace() so a crash
    # mid-write can't corrupt the live config.
    tmp_path = f"{filename}.tmp"
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(request.content, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, filename)
        return {"status": "saved"}
    except Exception as e:
        print(f"Error saving config {filename}: {e}")
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail="Failed to save config file")


def _check_startup_safety():
    """Refuse to expose an unauthenticated server beyond loopback."""
    if API_KEY is not None:
        return
    try:
        addr = ipaddress.ip_address(HOST)
        if addr.is_loopback:
            return
    except ValueError:
        # Hostname rather than an IP literal (e.g. "localhost" is fine).
        if HOST in ("localhost", "localhost.localdomain"):
            return
    raise SystemExit(
        f"Refusing to start: IPTV_HOST={HOST} is not loopback and IPTV_API_KEY "
        "is not set. Either bind to 127.0.0.1 or set IPTV_API_KEY to require "
        "authentication."
    )


if __name__ == "__main__":
    import uvicorn

    _check_startup_safety()
    if API_KEY is None:
        print("WARNING: IPTV_API_KEY is not set - API endpoints are unauthenticated "
              f"(loopback bind {HOST} only).")
    uvicorn.run(app, host=HOST, port=PORT)
