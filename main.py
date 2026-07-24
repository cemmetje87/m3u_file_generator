from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Body
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import subprocess
import os
import json
import sys
import asyncio
from pathlib import Path
from typing import List, Dict, Any

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Store active websocket connections
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        print(f"DEBUG: Broadcasting to {len(self.active_connections)} clients: {message[:50]}...")
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception as e:
                print(f"DEBUG: Error sending to client: {e}")
                # Optional: remove dead connection?
                # self.active_connections.remove(connection)
                pass

manager = ConnectionManager()

class ScrapeRequest(BaseModel):
    url: str

class ProcessRequest(BaseModel):
    input_file: str = "iptv_m3u_urls.txt"

class ConfigSaveRequest(BaseModel):
    content: Dict[str, Any]

EDITABLE_CONFIGS = ["m3u_filter_config.json"]

async def run_script(command: List[str]):
    """Run a script and stream output to websockets"""
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
            print(f"DEBUG OUTPUT: {decoded_line}") # Server-side log
            await manager.broadcast(decoded_line)

        await process.wait()
        await manager.broadcast(f"Process finished with exit code {process.returncode}")
    except Exception as e:
        await manager.broadcast(f"CRITICAL ERROR in run_script: {str(e)}")
        print(f"CRITICAL ERROR in run_script: {str(e)}")

@app.get("/")
async def get():
    return FileResponse('static/index.html')

@app.websocket("/ws/logs")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text() # Keep connection alive
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.post("/api/scrape")
async def run_scraper(request: ScrapeRequest):
    cmd = [sys.executable, "-u", "iptv_scraper.py", "--url", request.url]
    print(f"DEBUG: Executing command: {cmd}")
    print(f"DEBUG: CWD: {os.getcwd()}")
    
    asyncio.create_task(run_script(cmd))
    return {"status": "started", "command": " ".join(cmd)}

@app.post("/api/process")
async def run_processor(request: ProcessRequest):
    # Ensure we use the file if it exists, otherwise fallback to urls.txt
    input_file = request.input_file
    if not os.path.exists(input_file):
        await manager.broadcast(f"Warning: {input_file} not found. Falling back to urls.txt")
        input_file = "urls.txt"

    cmd = [sys.executable, "-u", "m3u_processor.py", "--input", input_file]
    print(f"DEBUG: Executing command: {cmd}")
    
    asyncio.create_task(run_script(cmd))
    return {"status": "started", "command": " ".join(cmd)}

@app.get("/api/download")
async def download_file():
    file_path = Path("master_iptv.m3u")
    if file_path.exists():
        return FileResponse(file_path, filename="master_iptv.m3u", media_type='audio/x-mpegurl')
    return {"error": "File not found"}

@app.get("/api/configs")
async def list_configs():
    return {"files": EDITABLE_CONFIGS}

@app.get("/api/config/{filename}")
async def get_config(filename: str):
    if filename not in EDITABLE_CONFIGS:
        raise HTTPException(status_code=404, detail="Config file not found or not editable")
    
    if not os.path.exists(filename):
         raise HTTPException(status_code=404, detail="File does not exist")

    try:
        with open(filename, 'r', encoding='utf-8') as f:
            content = json.load(f)
        return content
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/config/{filename}")
async def save_config(filename: str, request: ConfigSaveRequest):
    if filename not in EDITABLE_CONFIGS:
        raise HTTPException(status_code=403, detail="Not allowed to edit this file")
    
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(request.content, f, indent=2, ensure_ascii=False)
        return {"status": "saved"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)