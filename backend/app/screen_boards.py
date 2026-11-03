"""Versioned dashboard drafts and immutable published snapshots."""
import copy
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint
from sqlalchemy.exc import IntegrityError

from app.auth import require_configured_operator
from app.database import get_db
from app.models import Base, JSONB

router = APIRouter(prefix="/api/v2/screen-boards", tags=["screen-boards"],
                   dependencies=[Depends(require_configured_operator)])


class ScreenBoard(Base):
    __tablename__ = "screen_boards"
    id = Column(String(36), primary_key=True)
    name = Column(String(80), nullable=False)
    draft = Column(JSONB, nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    published_version = Column(Integer, nullable=False, default=0)
    is_template = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class ScreenPublication(Base):
    __tablename__ = "screen_publications"
    __table_args__ = (UniqueConstraint("board_id", "version"),)
    id = Column(String(36), primary_key=True)
    board_id = Column(String(36), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    config = Column(JSONB, nullable=False)
    name = Column(String(80), nullable=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class ScreenDefault(Base):
    __tablename__ = "screen_default"
    id = Column(Integer, primary_key=True)
    board_id = Column(String(36), nullable=True)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Theme(StrictModel):
    background: str = Field(default="#111217", pattern=r"^#[0-9a-fA-F]{6}$")
    surface: str = Field(default="#1b1c22", pattern=r"^#[0-9a-fA-F]{6}$")
    text: str = Field(default="#e8e9ed", pattern=r"^#[0-9a-fA-F]{6}$")
    accent: str = Field(default="#79c99e", pattern=r"^#[0-9a-fA-F]{6}$")
    font_size: int = Field(default=13, ge=12, le=20)
    radius: int = Field(default=6, ge=0, le=24)


class Widget(StrictModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    type: Literal["overview", "hosts", "cluster", "alerts", "charts", "text"]
    title: str = Field(default="", max_length=80)
    text: str = Field(default="", max_length=4000)
    x: int = Field(default=0, ge=0, le=11)
    y: int = Field(default=0, ge=0, le=500)
    w: int = Field(default=6, ge=2, le=12)
    h: int = Field(default=4, ge=2, le=30)

    @model_validator(mode="after")
    def within_grid(self):
        if self.x + self.w > 12:
            raise ValueError("组件超出12列画布")
        return self


class BoardConfig(StrictModel):
    schema_version: Literal[1] = 1
    title: str = Field(default="运行监控", min_length=1, max_length=80)
    theme: Theme = Field(default_factory=Theme)
    widgets: list[Widget] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({w.id for w in self.widgets}) != len(self.widgets):
            raise ValueError("组件ID重复")
        for i, a in enumerate(self.widgets):
            for b in self.widgets[i + 1:]:
                if a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h:
                    raise ValueError("组件布局重叠，请调整后保存")
        return self


BUILTIN = BoardConfig(widgets=[
    Widget(id="overview", type="overview", title="运行状态总览", w=12, h=2),
    Widget(id="hosts", type="hosts", title="主机资源", y=2, w=7, h=8),
    Widget(id="cluster", type="cluster", title="K3s 集群", x=7, y=2, w=5, h=4),
    Widget(id="alerts", type="alerts", title="告警队列", x=7, y=6, w=5, h=4),
    Widget(id="charts", type="charts", title="指标与趋势", y=10, w=12, h=12),
]).model_dump()


class CreateBoard(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    config: BoardConfig = Field(default_factory=lambda: BoardConfig.model_validate(copy.deepcopy(BUILTIN)))
    is_template: bool = False


class SaveBoard(CreateBoard):
    revision: int = Field(ge=1)


class RevisionRequest(StrictModel):
    revision: int = Field(ge=1)


class DefaultRequest(StrictModel):
    board_id: str | None = Field(default=None, max_length=36)


def board_or_404(db, board_id):
    row = db.get(ScreenBoard, board_id)
    if row is None:
        raise HTTPException(404, "大屏不存在")
    return row


def stamp(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def detail(row):
    return {"id": row.id, "name": row.name, "config": row.draft,
            "revision": row.revision, "published_version": row.published_version,
            "is_template": bool(row.is_template), "updated_at": stamp(row.updated_at)}


def cas(db, row, revision, **values):
    values.update(revision=revision + 1, updated_at=datetime.now(timezone.utc).replace(tzinfo=None))
    changed = db.query(ScreenBoard).filter_by(id=row.id, revision=revision).update(values, synchronize_session=False)
    if changed != 1:
        db.rollback()
        raise HTTPException(409, "大屏已被其他页面修改，请重新加载后编辑")


def commit(db):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "大屏已被其他页面修改，请重新加载")


@router.get("")
def list_boards():
    with get_db() as db:
        current = db.get(ScreenDefault, 1)
        return {"items": [detail(row) for row in db.query(ScreenBoard).order_by(ScreenBoard.updated_at.desc())],
                "default_id": current.board_id if current else None,
                "builtin": {"id": "builtin", "name": "系统监控模板", "config": BUILTIN, "is_template": True}}


@router.get("/default")
def get_default():
    with get_db() as db:
        setting = db.get(ScreenDefault, 1)
        if not setting or not setting.board_id:
            return {"id": "builtin", "name": "运行监控", "version": 0, "config": BUILTIN}
        row = board_or_404(db, setting.board_id)
        published = db.query(ScreenPublication).filter_by(board_id=row.id, version=row.published_version).one()
        return {"id": row.id, "name": published.name, "version": published.version, "config": published.config}


@router.post("/default")
def set_default(payload: DefaultRequest):
    with get_db() as db:
        if payload.board_id:
            row = board_or_404(db, payload.board_id)
            if not row.published_version:
                raise HTTPException(409, "请先发布大屏")
            # Lock the board against a simultaneous delete.
            cas(db, row, row.revision)
        setting = db.query(ScreenDefault).filter_by(id=1).with_for_update().first()
        if setting:
            setting.board_id = payload.board_id
        else:
            db.add(ScreenDefault(id=1, board_id=payload.board_id))
        commit(db)
        return {"default_id": payload.board_id}


@router.post("")
def create_board(payload: CreateBoard):
    if not payload.name.strip():
        raise HTTPException(422, "请输入大屏名称")
    with get_db() as db:
        row = ScreenBoard(id=str(uuid.uuid4()), name=payload.name.strip(), draft=payload.config.model_dump(),
                          revision=1, published_version=0, is_template=int(payload.is_template))
        db.add(row)
        commit(db)
        return detail(row)


@router.get("/{board_id}")
def get_board(board_id: str):
    with get_db() as db:
        return detail(board_or_404(db, board_id))


@router.put("/{board_id}")
def save_board(board_id: str, payload: SaveBoard):
    if not payload.name.strip():
        raise HTTPException(422, "请输入大屏名称")
    with get_db() as db:
        row = board_or_404(db, board_id)
        cas(db, row, payload.revision, name=payload.name.strip(), draft=payload.config.model_dump(),
            is_template=int(payload.is_template))
        commit(db)
        db.expire_all()
        return detail(db.get(ScreenBoard, board_id))


@router.post("/{board_id}/publish")
def publish_board(board_id: str, payload: RevisionRequest):
    with get_db() as db:
        row = board_or_404(db, board_id)
        if not row.draft["widgets"]:
            raise HTTPException(422, "请至少添加一个组件后发布")
        version = row.published_version + 1
        cas(db, row, payload.revision, published_version=version)
        db.add(ScreenPublication(id=str(uuid.uuid4()), board_id=board_id, version=version,
                                 config=copy.deepcopy(row.draft), name=row.name))
        commit(db)
        db.expire_all()
        return detail(db.get(ScreenBoard, board_id))


@router.get("/{board_id}/versions")
def list_versions(board_id: str):
    with get_db() as db:
        board_or_404(db, board_id)
        return {"items": [{"version": row.version, "name": row.name, "created_at": stamp(row.created_at)}
                          for row in db.query(ScreenPublication).filter_by(board_id=board_id)
                          .order_by(ScreenPublication.version.desc())]}


@router.get("/{board_id}/versions/{version}")
def get_version(board_id: str, version: int):
    with get_db() as db:
        row = db.query(ScreenPublication).filter_by(board_id=board_id, version=version).first()
        if row is None:
            raise HTTPException(404, "发布版本不存在")
        return {"id": board_id, "name": row.name, "version": version, "config": row.config}


@router.post("/{board_id}/versions/{version}/restore")
def restore_version(board_id: str, version: int, payload: RevisionRequest):
    with get_db() as db:
        row = board_or_404(db, board_id)
        snapshot = db.query(ScreenPublication).filter_by(board_id=board_id, version=version).first()
        if snapshot is None:
            raise HTTPException(404, "发布版本不存在")
        cas(db, row, payload.revision, draft=copy.deepcopy(snapshot.config), name=snapshot.name)
        commit(db)
        db.expire_all()
        return detail(db.get(ScreenBoard, board_id))


@router.delete("/{board_id}")
def delete_board(board_id: str, revision: int):
    with get_db() as db:
        row = board_or_404(db, board_id)
        cas(db, row, revision)
        setting = db.query(ScreenDefault).filter_by(id=1).with_for_update().first()
        if setting and setting.board_id == board_id:
            raise HTTPException(409, "请先切换默认大屏后再删除")
        db.query(ScreenPublication).filter_by(board_id=board_id).delete()
        db.delete(row)
        commit(db)
        return {"deleted": True}
