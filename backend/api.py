"""
FastAPI Backend for Web Audit Chatbot
WebSocket bridge to Chrome Extension + Chat API with SSE streaming.
No browser dependencies - all browsing happens in the extension.
"""

import asyncio
import json
import os
import logging
from datetime import datetime
from typing import Any, Dict
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# LLM provider (lazy-loaded)
_llm_provider = None
_llm_config = None


def _get_ai_config():
    global _llm_config
    if _llm_config is None:
        try:
            from ai.llm.config import LLMConfig
            _llm_config = LLMConfig()
        except Exception as e:
            logger.warning(f"AI config not available: {e}")
    return _llm_config


def _get_llm_provider():
    global _llm_provider
    if _llm_provider is None:
        try:
            config = _get_ai_config()
            if config and config.AI_ANALYSIS_ENABLED:
                from ai.llm.factory import create_llm_provider
                _llm_provider = create_llm_provider(config)
        except Exception as e:
            logger.warning(f"LLM provider not available: {e}")
    return _llm_provider


# Initialize FastAPI
app = FastAPI(
    title="Web Audit API",
    description="Backend API for the Web Audit chatbot",
    version="2.0.0",
)

# CORS - allow Next.js dev + production
_AZURE_ORIGIN = os.environ.get("AZURE_APP_URL", "")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        o for o in [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:8000",
            _AZURE_ORIGIN,
        ] if o
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure directories exist
Path("screenshots").mkdir(exist_ok=True)
Path("results").mkdir(exist_ok=True)

# UI directory (served at /ui/*)
_ui_dir = Path(__file__).parent.parent / "ui"
if not _ui_dir.exists():
    # Fallback: look relative to cwd (when running from backend/)
    _ui_dir = Path(__file__).parent / "ui"

# Mount static files
app.mount("/screenshots", StaticFiles(directory="screenshots"), name="screenshots")
if _ui_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(_ui_dir)), name="ui")

# ============================================================
# WebSocket: Extension Bridge
# ============================================================

connected_extensions: Dict[str, WebSocket] = {}
extension_bridges: Dict[str, Any] = {}

# Maps a chat/UI session_id -> the specific extension session_id it is paired
# with. Without this, concurrent users sharing one deployed backend would all
# have their audits routed to whichever extension happened to connect first
# (see _run_audit in chat/router.py before this was added) — one user's audit
# could silently execute in a totally different person's browser. Populated by
# POST /api/chat/pair once the user copies their chat session's pairing code
# into the extension popup. Keyed by chat session_id since one physical
# extension can legitimately serve multiple tabs/sessions for the same person.
session_extension_map: Dict[str, str] = {}


@app.websocket("/ws/extension/{session_id}")
async def websocket_extension(websocket: WebSocket, session_id: str):
    """Bidirectional WebSocket for Chrome extension communication."""
    await websocket.accept()
    connected_extensions[session_id] = websocket

    # Create or update ExtensionBridge
    try:
        from ai.agent.ws_bridge import ExtensionBridge
        config = _get_ai_config()
        timeout = config.AGENT_COMMAND_TIMEOUT if config and hasattr(config, 'AGENT_COMMAND_TIMEOUT') else 30

        existing = extension_bridges.get(session_id)
        if existing:
            existing.update_websocket(websocket)
            logger.info(f"Extension reconnected: {session_id}")
        else:
            bridge = ExtensionBridge(websocket, session_id, timeout=timeout)
            extension_bridges[session_id] = bridge
            logger.info(f"Extension connected: {session_id}")
    except ImportError:
        logger.info(f"Extension connected (no bridge): {session_id}")

    try:
        while True:
            data = await websocket.receive_json()

            if data.get("type") == "heartbeat":
                await websocket.send_json({
                    "type": "heartbeat_ack",
                    "timestamp": datetime.now().isoformat(),
                })

            elif data.get("type") == "pong":
                bridge = extension_bridges.get(session_id)
                if bridge:
                    bridge.resolve_pong()

            elif data.get("type") == "capture_result":
                capture_data = data.get("data", {})
                req_id = data.get("req_id")
                bridge = extension_bridges.get(session_id)
                if bridge and bridge.has_pending:
                    bridge.resolve_pending(capture_data, req_id)
                else:
                    logger.info(f"Capture result received (no pending): {session_id}")

            else:
                logger.info(f"Message from {session_id}: {data.get('type', 'unknown')}")

    except WebSocketDisconnect:
        logger.info(f"Extension disconnected: {session_id}")
    except Exception as e:
        logger.error(f"WebSocket error for {session_id}: {e}")
    finally:
        connected_extensions.pop(session_id, None)
        bridge = extension_bridges.get(session_id)
        if bridge and bridge.has_pending:
            logger.info(f"Keeping bridge for {session_id} (pending command)")
        else:
            extension_bridges.pop(session_id, None)


# ============================================================
# Chat Router
# ============================================================

from chat.router import router as chat_router, configure as configure_chat

configure_chat(connected_extensions, extension_bridges, session_extension_map, _get_llm_provider)
app.include_router(chat_router)


# ============================================================
# Health / Utility Endpoints
# ============================================================

@app.get("/")
async def root():
    """Serve the simple auditor UI if available, otherwise return API info."""
    ui_index = _ui_dir / "index.html"
    if ui_index.exists():
        return FileResponse(str(ui_index), media_type="text/html")
    return {
        "service": "Web Audit API",
        "version": "2.0.0",
        "ui": "/ui/index.html",
        "endpoints": {
            "/health": "Health check",
            "/api/chat/message": "POST - Send chat message",
            "/api/chat/stream/{session_id}": "GET - SSE event stream",
            "/api/chat/extension-status": "GET - Extension connection status",
            "/api/chat/sessions/{id}/pptx": "GET - Download PPTX audit deck",
            "/ws/extension/{session_id}": "WebSocket - Extension bridge",
        },
    }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "2.0.0",
        "connected_extensions": len(connected_extensions),
    }


@app.get("/api/extensions")
async def list_extensions():
    return {
        "connected": list(connected_extensions.keys()),
        "count": len(connected_extensions),
    }


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("API_PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
