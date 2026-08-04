"""Terminal routes — SSH terminal sessions + SFTP"""

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File as FastAPIFile
from pydantic import BaseModel
from typing import Optional
import asyncio, uuid

from app.ssh_terminal import create_session, get_session, remove_session, get_active_count

router = APIRouter(tags=["terminal"])


class TerminalCreateRequest(BaseModel):
    server_id: str
    cols: int = 80
    rows: int = 24


class SftpMkdirRequest(BaseModel):
    path: str


class SftpRenameRequest(BaseModel):
    old_path: str
    new_path: str


class SftpDeleteRequest(BaseModel):
    path: str


@router.post("/terminal/sessions")
async def api_create_terminal_session(req: TerminalCreateRequest):
    """Create a new SSH terminal session."""
    sid, err = create_session(server_id=req.server_id, server_name="", host="", port=22, user="root")
    if err:
        raise HTTPException(400, err)
    session = get_session(sid)
    if not session:
        raise HTTPException(500, "Failed to create session")
    session.connect_in_background(cols=req.cols, rows=req.rows)
    return {"session_id": sid, "status": "connecting"}


@router.get("/terminal/sessions/{session_id}/status")
def terminal_session_status(session_id: str):
    """Check if a terminal session can be reconnected."""
    session = get_session(session_id)
    if not session:
        return {"alive": False, "reconnectable": False}
    return {"alive": True, "reconnectable": True}


@router.get("/terminal/stats")
def terminal_stats():
    """Get active terminal session count."""
    return {"active_sessions": get_active_count()}
