"""Per-host file management with local and SFTP backends."""
from __future__ import annotations

import base64
import binascii
import os
import posixpath
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.database import get_db
from app.models import Server
from app.ssh_manager import get_ssh_client


router = APIRouter(prefix="/api/v2", tags=["files"])
_TEXT_LIMIT = 1024 * 1024
_TRANSFER_LIMIT = 10 * 1024 * 1024
_PROTECTED_WRITE_PREFIXES = ("/proc", "/sys", "/dev")


class FileWriteRequest(BaseModel):
    content: str = Field(max_length=_TEXT_LIMIT)
    expected_mtime: Optional[float] = None


class DirectoryCreateRequest(BaseModel):
    parent: str
    name: str = Field(min_length=1, max_length=255)


class FileUploadRequest(BaseModel):
    parent: str
    name: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(max_length=14_000_000)


class FileMoveRequest(BaseModel):
    source: str
    target: str


class FileTrashRequest(BaseModel):
    path: str
    confirm_name: str


def _server(server_id: str) -> Server:
    try:
        uid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="主机不存在")
    with get_db() as db:
        row = db.query(Server).filter(Server.id == uid).first()
        if not row:
            raise HTTPException(status_code=404, detail="主机不存在")
        db.expunge(row)
        return row


def _clean_path(value: str, remote: bool) -> str:
    value = (value or "").strip()
    if not value or "\x00" in value or len(value) > 4096:
        raise HTTPException(status_code=400, detail="非法文件路径")
    if remote:
        if not value.startswith("/"):
            raise HTTPException(status_code=400, detail="远程文件路径必须是绝对路径")
        return posixpath.normpath(value)
    return os.path.abspath(value)


def _safe_name(value: str) -> str:
    value = (value or "").strip()
    if value in {"", ".", ".."} or "/" in value or "\\" in value or "\x00" in value:
        raise HTTPException(status_code=400, detail="非法文件名")
    return value


def _ensure_writable(path: str) -> None:
    normalized = path.replace("\\", "/")
    if any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in _PROTECTED_WRITE_PREFIXES):
        raise HTTPException(status_code=400, detail="禁止修改系统伪文件目录")


def _trash_home(remote: bool, sftp) -> str:
    return sftp.normalize(".") if remote else str(Path.home())


@contextmanager
def _filesystem(server: Server):
    if server.agent_type == "local":
        yield False, None, None
        return
    client = get_ssh_client(server)
    if not client:
        raise HTTPException(status_code=400, detail=f"主机 {server.name} 未配置 SSH 凭证或连接失败")
    sftp = None
    try:
        sftp = client.open_sftp()
        yield True, sftp, client
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SFTP 操作失败: {str(exc)[-300:]}")
    finally:
        if sftp:
            sftp.close()
        client.close()


def _entry(name: str, path: str, attrs) -> dict:
    mode = int(attrs.st_mode)
    kind = "directory" if stat.S_ISDIR(mode) else "symlink" if stat.S_ISLNK(mode) else "file"
    return {
        "name": name,
        "path": path,
        "type": kind,
        "size": int(getattr(attrs, "st_size", 0) or 0),
        "mtime": float(getattr(attrs, "st_mtime", 0) or 0),
        "mode": stat.filemode(mode),
    }


@router.get("/servers/{server_id}/files", dependencies=[Depends(get_current_user)])
def list_files(server_id: str, path: str = Query("/"), show_hidden: bool = False):
    started = time.perf_counter()
    server = _server(server_id)
    with _filesystem(server) as (remote, sftp, _client):
        target = _clean_path(path, remote)
        try:
            if remote:
                rows = [_entry(item.filename, posixpath.join(target, item.filename), item) for item in sftp.listdir_attr(target)]
            else:
                rows = [_entry(item.name, item.path, item.stat(follow_symlinks=False)) for item in os.scandir(target)]
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="目录不存在")
        except NotADirectoryError:
            raise HTTPException(status_code=400, detail="目标不是目录")
        except PermissionError:
            raise HTTPException(status_code=403, detail="没有目录读取权限")
    if not show_hidden:
        rows = [item for item in rows if not item["name"].startswith(".")]
    rows.sort(key=lambda item: (item["type"] != "directory", item["name"].lower()))
    return {
        "path": target, "parent": posixpath.dirname(target) if remote else os.path.dirname(target),
        "items": rows[:2000], "total": len(rows), "truncated": len(rows) > 2000,
        "source": "sftp" if remote else "local",
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    }


@router.get("/servers/{server_id}/files/content", dependencies=[Depends(get_current_user)])
def read_file(server_id: str, path: str):
    server = _server(server_id)
    with _filesystem(server) as (remote, sftp, _client):
        target = _clean_path(path, remote)
        try:
            attrs = sftp.stat(target) if remote else os.stat(target)
            if attrs.st_size > _TEXT_LIMIT:
                raise HTTPException(status_code=413, detail="文本预览最大支持 1MB")
            if remote:
                with sftp.open(target, "rb") as handle:
                    raw = handle.read(_TEXT_LIMIT + 1)
            else:
                raw = Path(target).read_bytes()
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="文件不存在")
        except PermissionError:
            raise HTTPException(status_code=403, detail="没有文件读取权限")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=415, detail="当前文件不是 UTF-8 文本，请使用下载功能")
    return {"path": target, "content": content, "size": len(raw), "mtime": float(attrs.st_mtime)}


@router.put("/servers/{server_id}/files/content", dependencies=[Depends(get_current_user)])
def write_file(server_id: str, path: str, payload: FileWriteRequest):
    server = _server(server_id)
    raw = payload.content.encode("utf-8")
    if len(raw) > _TEXT_LIMIT:
        raise HTTPException(status_code=413, detail="文本编辑最大支持 1MB")
    with _filesystem(server) as (remote, sftp, _client):
        target = _clean_path(path, remote)
        _ensure_writable(target)
        try:
            attrs = sftp.stat(target) if remote else os.stat(target)
            if payload.expected_mtime is not None and abs(float(attrs.st_mtime) - payload.expected_mtime) > 0.001:
                raise HTTPException(status_code=409, detail="文件已被其他进程修改，请重新加载")
            if remote:
                with sftp.open(target, "wb") as handle:
                    handle.write(raw)
                mtime = float(sftp.stat(target).st_mtime)
            else:
                temporary = target + f".opscenter-{uuid.uuid4().hex}.tmp"
                Path(temporary).write_bytes(raw)
                os.chmod(temporary, stat.S_IMODE(attrs.st_mode))
                os.replace(temporary, target)
                mtime = float(os.stat(target).st_mtime)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="文件不存在")
        except PermissionError:
            raise HTTPException(status_code=403, detail="没有文件写入权限")
    return {"ok": True, "path": target, "size": len(raw), "mtime": mtime}


@router.post("/servers/{server_id}/files/directories", dependencies=[Depends(get_current_user)], status_code=201)
def create_directory(server_id: str, payload: DirectoryCreateRequest):
    server = _server(server_id)
    name = _safe_name(payload.name)
    with _filesystem(server) as (remote, sftp, _client):
        parent = _clean_path(payload.parent, remote)
        target = posixpath.join(parent, name) if remote else os.path.join(parent, name)
        _ensure_writable(target)
        try:
            sftp.mkdir(target) if remote else os.mkdir(target)
        except FileExistsError:
            raise HTTPException(status_code=409, detail="同名目录已存在")
        except PermissionError:
            raise HTTPException(status_code=403, detail="没有目录创建权限")
    return {"ok": True, "path": target}


@router.post("/servers/{server_id}/files/upload", dependencies=[Depends(get_current_user)], status_code=201)
def upload_file(server_id: str, payload: FileUploadRequest):
    server = _server(server_id)
    name = _safe_name(payload.name)
    try:
        raw = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="上传内容不是合法 Base64")
    if len(raw) > _TRANSFER_LIMIT:
        raise HTTPException(status_code=413, detail="单文件上传最大支持 10MB")
    with _filesystem(server) as (remote, sftp, _client):
        parent = _clean_path(payload.parent, remote)
        target = posixpath.join(parent, name) if remote else os.path.join(parent, name)
        _ensure_writable(target)
        try:
            if remote:
                try:
                    sftp.stat(target)
                except FileNotFoundError:
                    pass
                else:
                    raise HTTPException(status_code=409, detail="同名文件已存在")
                with sftp.open(target, "wb") as handle:
                    handle.write(raw)
            else:
                with open(target, "xb") as handle:
                    handle.write(raw)
        except FileExistsError:
            raise HTTPException(status_code=409, detail="同名文件已存在")
        except PermissionError:
            raise HTTPException(status_code=403, detail="没有文件上传权限")
    return {"ok": True, "path": target, "size": len(raw)}


@router.get("/servers/{server_id}/files/download", dependencies=[Depends(get_current_user)])
def download_file(server_id: str, path: str):
    server = _server(server_id)
    with _filesystem(server) as (remote, sftp, _client):
        target = _clean_path(path, remote)
        try:
            attrs = sftp.stat(target) if remote else os.stat(target)
            if attrs.st_size > _TRANSFER_LIMIT:
                raise HTTPException(status_code=413, detail="当前接口单文件下载最大支持 10MB")
            if remote:
                with sftp.open(target, "rb") as handle:
                    raw = handle.read(_TRANSFER_LIMIT + 1)
            else:
                raw = Path(target).read_bytes()
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="文件不存在")
        except PermissionError:
            raise HTTPException(status_code=403, detail="没有文件下载权限")
    return {"name": posixpath.basename(target) if remote else os.path.basename(target), "size": len(raw), "content_base64": base64.b64encode(raw).decode("ascii")}


@router.post("/servers/{server_id}/files/move", dependencies=[Depends(get_current_user)])
def move_file(server_id: str, payload: FileMoveRequest):
    server = _server(server_id)
    with _filesystem(server) as (remote, sftp, _client):
        source = _clean_path(payload.source, remote)
        target = _clean_path(payload.target, remote)
        _ensure_writable(source)
        _ensure_writable(target)
        if source == target:
            raise HTTPException(status_code=400, detail="源路径和目标路径相同")
        try:
            if remote:
                try:
                    sftp.stat(target)
                except FileNotFoundError:
                    pass
                else:
                    raise HTTPException(status_code=409, detail="目标路径已存在")
                sftp.rename(source, target)
            else:
                if os.path.exists(target):
                    raise HTTPException(status_code=409, detail="目标路径已存在")
                os.rename(source, target)
        except FileExistsError:
            raise HTTPException(status_code=409, detail="目标路径已存在")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="源路径不存在")
        except PermissionError:
            raise HTTPException(status_code=403, detail="没有改名或移动权限")
    return {"ok": True, "source": source, "target": target}


@router.post("/servers/{server_id}/files/trash", dependencies=[Depends(get_current_user)])
def trash_file(server_id: str, payload: FileTrashRequest):
    server = _server(server_id)
    with _filesystem(server) as (remote, sftp, _client):
        target = _clean_path(payload.path, remote)
        name = posixpath.basename(target) if remote else os.path.basename(target)
        if target == "/" or (not remote and target == os.path.abspath(os.path.sep)) or not name:
            raise HTTPException(status_code=400, detail="禁止删除文件系统根目录")
        if payload.confirm_name != name:
            raise HTTPException(status_code=400, detail="确认名称不匹配")
        _ensure_writable(target)
        home = _trash_home(remote, sftp)
        trash_root = posixpath.join(home, ".opscenter-trash") if remote else os.path.join(home, ".opscenter-trash")
        if target == trash_root or target.startswith(trash_root + ("/" if remote else os.sep)):
            raise HTTPException(status_code=400, detail="回收站内文件不能再次移入回收站")
        trash_name = f"{int(time.time())}-{uuid.uuid4().hex[:8]}-{name}"
        destination = posixpath.join(trash_root, trash_name) if remote else os.path.join(trash_root, trash_name)
        try:
            if remote:
                try:
                    sftp.stat(trash_root)
                except FileNotFoundError:
                    sftp.mkdir(trash_root, mode=0o700)
                sftp.rename(target, destination)
            else:
                os.makedirs(trash_root, mode=0o700, exist_ok=True)
                os.rename(target, destination)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="目标文件不存在")
        except PermissionError:
            raise HTTPException(status_code=403, detail="没有文件删除权限")
    return {"ok": True, "path": target, "trash_path": destination, "recoverable": True}
