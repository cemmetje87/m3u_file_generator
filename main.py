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

import vocabulary
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


def _file_stat(path: Path) -> Dict[str, Any]:
    """Existence, size and mtime for an artefact the UI reports on."""
    if not path.exists():
        return {"exists": False}
    stat = path.stat()
    return {"exists": True, "size": stat.st_size, "mtime": stat.st_mtime}


# Counting channels means reading the whole master playlist, which is hundreds
# of megabytes. Cache on (size, mtime) so we pay that once per build rather
# than once per status poll.
_channel_count_cache: Dict[Any, int] = {}


def _count_channels(path: Path, key: Any) -> int:
    if key in _channel_count_cache:
        return _channel_count_cache[key]
    count = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("#EXTINF"):
                count += 1
    # One entry is enough: the cache only ever tracks the current build.
    _channel_count_cache.clear()
    _channel_count_cache[key] = count
    return count


@app.get("/api/status")
async def get_status(http_request: Request):
    """Artefact state on disk, so the UI can show real readouts on load.

    Without this the download control could only be enabled by watching a
    process run in the current tab, even when a master playlist already
    exists from an earlier session.
    """
    verify_api_key(http_request.headers.get("X-API-Key"))

    sources_path = Path("iptv_m3u_urls.txt")
    sources = _file_stat(sources_path)
    if sources["exists"]:
        try:
            with open(sources_path, "r", encoding="utf-8") as f:
                sources["count"] = sum(1 for line in f if line.strip())
        except OSError:
            sources["count"] = None

    output_path = Path("master_iptv.m3u")
    output = _file_stat(output_path)
    if output["exists"]:
        try:
            output["channels"] = await asyncio.to_thread(
                _count_channels, output_path, (output["size"], output["mtime"])
            )
        except OSError:
            output["channels"] = None

    return {
        "sources": sources,
        "output": output,
        "busy": job_lock.locked(),
    }


# --- Filter value lookups -------------------------------------------------
# The rule builder offers real group / channel / tvg-name values as you type.
# Indexes are loaded per field on first use and re-read when the vocabulary
# is rebuilt, so a session that only ever touches "group" never pays to load
# the million-value tvg-name field.
_vocab_indexes: Dict[str, Any] = {}
vocab_lock = asyncio.Lock()


def _load_field_index(field: str):
    path = vocabulary.DEFAULT_DIR / f"{field}.tsv"
    if not path.is_file():
        return None
    stat = path.stat()
    key = (stat.st_size, stat.st_mtime)
    cached = _vocab_indexes.get(field)
    if cached and cached[0] == key:
        return cached[1]
    index = vocabulary.FieldIndex.load(path)
    _vocab_indexes[field] = (key, index)
    return index


@app.get("/api/vocabulary")
async def get_vocabulary(http_request: Request):
    """What the lookup index knows, so the builder can explain itself."""
    verify_api_key(http_request.headers.get("X-API-Key"))
    meta = vocabulary.load_meta()
    return {
        "available": meta is not None,
        "meta": meta,
        # Without a build to learn from, the finished playlist is the only
        # other place these values exist.
        "can_derive": Path("master_iptv.m3u").exists(),
        "building": vocab_lock.locked(),
    }


@app.get("/api/vocabulary/{field}")
async def search_vocabulary(field: str, http_request: Request, q: str = "", limit: int = 25):
    verify_api_key(http_request.headers.get("X-API-Key"))
    if field not in vocabulary.FIELDS:
        raise HTTPException(status_code=404, detail="Unknown vocabulary field")

    limit = max(1, min(limit, 100))
    index = await asyncio.to_thread(_load_field_index, field)
    if index is None:
        return {"field": field, "available": False, "matches": []}

    matches = await asyncio.to_thread(index.search, q, limit)
    return {
        "field": field,
        "available": True,
        "total": len(index),
        "matches": [{"value": value, "count": count} for value, count in matches],
    }


@app.post("/api/vocabulary/derive")
async def derive_vocabulary(http_request: Request):
    """Build the lookup index from the existing master playlist.

    A full run captures values before filtering; this fallback can only see
    what the current rules already let through, but it makes the lookups
    usable without waiting for a rebuild.
    """
    verify_api_key(http_request.headers.get("X-API-Key"))

    playlist = Path("master_iptv.m3u")
    if not playlist.exists():
        raise HTTPException(status_code=404, detail="master_iptv.m3u not found")
    if vocab_lock.locked():
        raise HTTPException(status_code=409, detail="Vocabulary is already being built")

    async with vocab_lock:
        await manager.broadcast(f"Indexing filter values from {playlist}...")

        def build():
            return vocabulary.collect_from_playlist(playlist).save(source="playlist")

        try:
            meta = await asyncio.to_thread(build)
        except OSError as e:
            print(f"Error deriving vocabulary: {e}")
            raise HTTPException(status_code=500, detail="Failed to index filter values")

        _vocab_indexes.clear()
        for name, info in meta["fields"].items():
            await manager.broadcast(f"  - {name}: {info['total']} distinct values")
        await manager.broadcast("Filter value index ready.")
        return {"status": "ok", "meta": meta}


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
