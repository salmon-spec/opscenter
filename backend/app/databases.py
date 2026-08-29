"""Managed database inventory and safe administration APIs for OpsCenter 4.2."""
from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.auth import get_current_user
from app.credential_crypto import decrypt_secret, encrypt_secret
from app.database import get_db
from app.models import DatabaseInstance, Server, Service
from app.ssh_tunnel import ssh_forward

router = APIRouter(prefix="/api/v2/databases", tags=["databases"])
_ENGINES = {"mysql", "mariadb", "postgresql", "redis"}
_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,62}$")
_DEFAULT_PORTS = {"mysql": 3306, "mariadb": 3306, "postgresql": 5432, "redis": 6379}


class InstanceCreate(BaseModel):
    server_id: str
    name: str = Field(..., min_length=1, max_length=100)
    engine: str
    host: str = Field(..., min_length=1, max_length=200)
    port: int = Field(..., ge=1, le=65535)
    username: Optional[str] = Field(None, max_length=100)
    password: Optional[str] = Field(None, max_length=500)
    default_database: Optional[str] = Field(None, max_length=100)
    connection_mode: str = "direct"
    container_name: Optional[str] = Field(None, max_length=128)


class InstanceUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    host: Optional[str] = Field(None, min_length=1, max_length=200)
    port: Optional[int] = Field(None, ge=1, le=65535)
    username: Optional[str] = Field(None, max_length=100)
    password: Optional[str] = Field(None, max_length=500)
    default_database: Optional[str] = Field(None, max_length=100)
    connection_mode: Optional[str] = None


class DatabaseCreate(BaseModel):
    name: str
    owner: Optional[str] = None
    charset: str = "utf8mb4"


class AccountCreate(BaseModel):
    username: str
    password: str = Field(..., min_length=1, max_length=500)
    databases: List[str] = Field(default_factory=list)


class AccountUpdate(BaseModel):
    password: Optional[str] = Field(None, min_length=1, max_length=500)
    databases: Optional[List[str]] = None


def _uuid(value: str, label: str = "资源") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"{label}不存在")


def _safe_name(value: str, label: str) -> str:
    value = (value or "").strip()
    if not _NAME_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail=f"{label}仅支持字母、数字、下划线和连字符")
    return value


def _instance_dict(row: DatabaseInstance) -> dict:
    return {
        "id": str(row.id), "server_id": str(row.server_id), "name": row.name,
        "engine": row.engine, "source": row.source, "connection_mode": row.connection_mode,
        "host": row.host, "port": row.port, "username": row.username or "",
        "default_database": row.default_database or "", "container_name": row.container_name or "",
        "version": row.version or "", "status": row.status, "last_error": row.last_error or "",
        "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
        "has_credentials": bool(row.secret_ciphertext),
    }


def _load_instance(instance_id: str):
    with get_db() as db:
        row = db.query(DatabaseInstance).filter(DatabaseInstance.id == _uuid(instance_id, "数据库实例")).first()
        if not row:
            raise HTTPException(status_code=404, detail="数据库实例不存在")
        server = db.query(Server).filter(Server.id == row.server_id).first()
        if not server:
            raise HTTPException(status_code=404, detail="所属主机不存在")
        db.expunge(row)
        db.expunge(server)
        return row, server


@contextmanager
def _target(instance: DatabaseInstance, server: Server):
    if instance.connection_mode in {"ssh", "docker"}:
        with ssh_forward(server, instance.host, instance.port) as target:
            yield target
    else:
        yield instance.host, instance.port


@contextmanager
def _connect(instance: DatabaseInstance, server: Server):
    try:
        password = decrypt_secret(instance.secret_ciphertext or "")
        with _target(instance, server) as (host, port):
            if instance.engine == "postgresql":
                import psycopg
                conn = psycopg.connect(host=host, port=port, user=instance.username, password=password, dbname=instance.default_database or "postgres", connect_timeout=6, autocommit=True)
            elif instance.engine in {"mysql", "mariadb"}:
                import pymysql
                conn = pymysql.connect(host=host, port=port, user=instance.username, password=password, database=instance.default_database or None, connect_timeout=6, read_timeout=10, write_timeout=10, autocommit=True, charset="utf8mb4")
            else:
                import redis
                conn = redis.Redis(host=host, port=port, username=instance.username or None, password=password or None, socket_connect_timeout=6, socket_timeout=10, decode_responses=True)
            try:
                yield conn
            finally:
                conn.close()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"数据库连接失败: {str(exc)[-300:]}")


def _set_check(instance_id: uuid.UUID, ok: bool, version: str = "", error: str = ""):
    with get_db() as db:
        row = db.query(DatabaseInstance).filter(DatabaseInstance.id == instance_id).first()
        if row:
            row.status = "online" if ok else "error"
            row.version = version[:100] if version else row.version
            row.last_error = error[-1000:] if error else None
            row.last_checked_at = datetime.utcnow()
            db.commit()


@router.get("/instances", dependencies=[Depends(get_current_user)])
def list_instances(server_id: str):
    with get_db() as db:
        rows = db.query(DatabaseInstance).filter(DatabaseInstance.server_id == _uuid(server_id, "主机")).order_by(DatabaseInstance.name).all()
        return [_instance_dict(row) for row in rows]


@router.post("/instances", status_code=201, dependencies=[Depends(get_current_user)])
def create_instance(payload: InstanceCreate):
    engine = payload.engine.lower()
    if engine not in _ENGINES:
        raise HTTPException(status_code=400, detail="仅支持 MySQL/MariaDB、PostgreSQL 和 Redis")
    if payload.connection_mode not in {"direct", "ssh", "docker"}:
        raise HTTPException(status_code=400, detail="连接方式仅支持 direct / ssh / docker")
    with get_db() as db:
        server_id = _uuid(payload.server_id, "主机")
        if not db.query(Server).filter(Server.id == server_id).first():
            raise HTTPException(status_code=404, detail="主机不存在")
        row = DatabaseInstance(
            server_id=server_id, name=payload.name.strip(), engine=engine, source="manual",
            connection_mode=payload.connection_mode, host=payload.host.strip(), port=payload.port,
            username=payload.username, secret_ciphertext=encrypt_secret(payload.password or ""),
            default_database=payload.default_database, container_name=payload.container_name,
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="相同数据库实例已存在")
        db.refresh(row)
        return _instance_dict(row)


@router.put("/instances/{instance_id}", dependencies=[Depends(get_current_user)])
def update_instance(instance_id: str, payload: InstanceUpdate):
    with get_db() as db:
        row = db.query(DatabaseInstance).filter(DatabaseInstance.id == _uuid(instance_id, "数据库实例")).first()
        if not row:
            raise HTTPException(status_code=404, detail="数据库实例不存在")
        values = payload.model_dump(exclude_unset=True)
        password = values.pop("password", None)
        for key, value in values.items():
            if value is not None:
                setattr(row, key, value)
        if password:
            row.secret_ciphertext = encrypt_secret(password)
        row.status = "pending"
        db.commit()
        return {"ok": True}


@router.delete("/instances/{instance_id}", dependencies=[Depends(get_current_user)])
def delete_instance(instance_id: str, confirm_name: str = Query(..., min_length=1, max_length=100)):
    with get_db() as db:
        row = db.query(DatabaseInstance).filter(DatabaseInstance.id == _uuid(instance_id, "数据库实例")).first()
        if not row:
            raise HTTPException(status_code=404, detail="数据库实例不存在")
        if confirm_name != row.name:
            raise HTTPException(status_code=400, detail="实例名称确认不匹配")
        db.delete(row)
        db.commit()
    return {"ok": True}


def _infer_engine(service: Service) -> Optional[str]:
    text = f"{service.name} {service.image or ''} {service.url or ''}".lower()
    if "mariadb" in text:
        return "mariadb"
    if "mysql" in text:
        return "mysql"
    if "postgres" in text:
        return "postgresql"
    if "redis" in text:
        return "redis"
    return None


@router.post("/discover", dependencies=[Depends(get_current_user)])
def discover_instances(server_id: str):
    sid = _uuid(server_id, "主机")
    added = 0
    with get_db() as db:
        server = db.query(Server).filter(Server.id == sid).first()
        if not server:
            raise HTTPException(status_code=404, detail="主机不存在")
        services = db.query(Service).filter(Service.server_id == sid).all()
        for service in services:
            engine = _infer_engine(service)
            if not engine:
                continue
            port = service.port or _DEFAULT_PORTS[engine]
            raw_host = (service.host_ip or "").strip()
            # Published wildcard ports are reachable through the managed host.
            # Loopback/container bridge targets on remote hosts require the short-lived SSH tunnel.
            if raw_host in {"", "0.0.0.0", "::"}:
                host = server.host
                mode = "direct"
            elif server.agent_type != "local" and (raw_host.startswith("127.") or raw_host.startswith("172.") or raw_host.startswith("10.") or raw_host.startswith("192.168.")) and raw_host != server.host:
                host = raw_host
                mode = "docker" if service.container_name else "ssh"
            else:
                host = raw_host
                mode = "direct"
            exists = db.query(DatabaseInstance).filter(DatabaseInstance.server_id == sid, DatabaseInstance.engine == engine, DatabaseInstance.host == host, DatabaseInstance.port == port).first()
            if exists:
                continue
            db.add(DatabaseInstance(server_id=sid, name=service.name, engine=engine, source="discovered", connection_mode=mode, host=host, port=port, container_name=service.container_name, status="pending"))
            added += 1
        db.commit()
    return {"ok": True, "added": added}


@router.post("/instances/{instance_id}/test", dependencies=[Depends(get_current_user)])
def test_instance(instance_id: str):
    instance, server = _load_instance(instance_id)
    try:
        with _connect(instance, server) as conn:
            if instance.engine == "postgresql":
                version = conn.execute("select version()").fetchone()[0]
            elif instance.engine in {"mysql", "mariadb"}:
                with conn.cursor() as cursor:
                    cursor.execute("select version()")
                    version = cursor.fetchone()[0]
            else:
                info = conn.info("server")
                version = info.get("redis_version", "")
        _set_check(instance.id, True, str(version))
        return {"ok": True, "version": str(version)}
    except HTTPException as exc:
        _set_check(instance.id, False, error=str(exc.detail))
        raise


@router.get("/instances/{instance_id}/overview", dependencies=[Depends(get_current_user)])
def instance_overview(instance_id: str):
    instance, server = _load_instance(instance_id)
    with _connect(instance, server) as conn:
        if instance.engine == "redis":
            info = conn.info()
            return {"engine": "redis", "version": info.get("redis_version"), "uptime_seconds": info.get("uptime_in_seconds"), "used_memory": info.get("used_memory"), "connected_clients": info.get("connected_clients"), "keyspace": {key: value for key, value in info.items() if str(key).startswith("db")}}
        return {"engine": instance.engine, "version": instance.version, "status": instance.status}


@router.get("/instances/{instance_id}/databases", dependencies=[Depends(get_current_user)])
def list_databases(instance_id: str):
    instance, server = _load_instance(instance_id)
    if instance.engine == "redis":
        raise HTTPException(status_code=400, detail="Redis 不支持数据库管理")
    with _connect(instance, server) as conn:
        if instance.engine == "postgresql":
            rows = conn.execute("select datname, pg_get_userbyid(datdba) from pg_database where datistemplate = false order by datname").fetchall()
            return [{"name": row[0], "owner": row[1]} for row in rows]
        with conn.cursor() as cursor:
            cursor.execute("show databases")
            return [{"name": row[0]} for row in cursor.fetchall()]


@router.post("/instances/{instance_id}/databases", dependencies=[Depends(get_current_user)])
def create_database(instance_id: str, payload: DatabaseCreate):
    name = _safe_name(payload.name, "数据库名称")
    instance, server = _load_instance(instance_id)
    if instance.engine == "redis":
        raise HTTPException(status_code=400, detail="Redis 不支持创建数据库")
    with _connect(instance, server) as conn:
        if instance.engine == "postgresql":
            from psycopg import sql
            query = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
            if payload.owner:
                query += sql.SQL(" OWNER {} ").format(sql.Identifier(_safe_name(payload.owner, "账号")))
            conn.execute(query)
        else:
            charset = payload.charset if payload.charset in {"utf8mb4", "utf8", "latin1"} else "utf8mb4"
            with conn.cursor() as cursor:
                cursor.execute(f"CREATE DATABASE `{name}` CHARACTER SET {charset}")
    return {"ok": True, "name": name}


@router.delete("/instances/{instance_id}/databases/{name}", dependencies=[Depends(get_current_user)])
def drop_database(instance_id: str, name: str):
    name = _safe_name(name, "数据库名称")
    if name in {"postgres", "mysql", "information_schema", "performance_schema", "sys"}:
        raise HTTPException(status_code=400, detail="系统数据库不允许删除")
    instance, server = _load_instance(instance_id)
    with _connect(instance, server) as conn:
        if instance.engine == "postgresql":
            from psycopg import sql
            conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        elif instance.engine in {"mysql", "mariadb"}:
            with conn.cursor() as cursor:
                cursor.execute(f"DROP DATABASE `{name}`")
        else:
            raise HTTPException(status_code=400, detail="Redis 不支持删除数据库")
    return {"ok": True}


@router.get("/instances/{instance_id}/accounts", dependencies=[Depends(get_current_user)])
def list_accounts(instance_id: str):
    instance, server = _load_instance(instance_id)
    if instance.engine == "redis":
        raise HTTPException(status_code=400, detail="Redis ACL 管理不在首版范围")
    with _connect(instance, server) as conn:
        if instance.engine == "postgresql":
            rows = conn.execute("select rolname, rolsuper, rolcanlogin from pg_roles order by rolname").fetchall()
            return [{"username": row[0], "superuser": row[1], "can_login": row[2]} for row in rows]
        with conn.cursor() as cursor:
            cursor.execute("select User, Host from mysql.user order by User, Host")
            return [{"username": row[0], "host": row[1]} for row in cursor.fetchall()]


@router.post("/instances/{instance_id}/accounts", dependencies=[Depends(get_current_user)])
def create_account(instance_id: str, payload: AccountCreate):
    username = _safe_name(payload.username, "账号")
    instance, server = _load_instance(instance_id)
    if instance.engine == "redis":
        raise HTTPException(status_code=400, detail="Redis ACL 管理不在首版范围")
    databases = [_safe_name(item, "数据库名称") for item in payload.databases]
    with _connect(instance, server) as conn:
        if instance.engine == "postgresql":
            from psycopg import sql
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(sql.Identifier(username)), (payload.password,))
            for database in databases:
                conn.execute(sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {}").format(sql.Identifier(database), sql.Identifier(username)))
        else:
            with conn.cursor() as cursor:
                cursor.execute("CREATE USER %s@'%' IDENTIFIED BY %s", (username, payload.password))
                for database in databases:
                    cursor.execute(f"GRANT ALL PRIVILEGES ON `{database}`.* TO %s@'%'", (username,))
                cursor.execute("FLUSH PRIVILEGES")
    return {"ok": True, "username": username}


@router.put("/instances/{instance_id}/accounts/{username}", dependencies=[Depends(get_current_user)])
def update_account(instance_id: str, username: str, payload: AccountUpdate):
    username = _safe_name(username, "账号")
    instance, server = _load_instance(instance_id)
    if instance.engine == "redis":
        raise HTTPException(status_code=400, detail="Redis ACL 管理不在首版范围")
    with _connect(instance, server) as conn:
        if instance.engine == "postgresql":
            from psycopg import sql
            if payload.password:
                conn.execute(sql.SQL("ALTER ROLE {} PASSWORD %s").format(sql.Identifier(username)), (payload.password,))
            if payload.databases is not None:
                rows = conn.execute("select datname from pg_database where datistemplate = false").fetchall()
                for row in rows:
                    conn.execute(sql.SQL("REVOKE ALL PRIVILEGES ON DATABASE {} FROM {}").format(sql.Identifier(row[0]), sql.Identifier(username)))
                for database in payload.databases:
                    database = _safe_name(database, "数据库名称")
                    conn.execute(sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {}").format(sql.Identifier(database), sql.Identifier(username)))
        else:
            with conn.cursor() as cursor:
                if payload.password:
                    cursor.execute("ALTER USER %s@'%' IDENTIFIED BY %s", (username, payload.password))
                if payload.databases is not None:
                    cursor.execute("REVOKE ALL PRIVILEGES, GRANT OPTION FROM %s@'%'", (username,))
                    for database in payload.databases:
                        database = _safe_name(database, "数据库名称")
                        cursor.execute(f"GRANT ALL PRIVILEGES ON `{database}`.* TO %s@'%'", (username,))
                cursor.execute("FLUSH PRIVILEGES")
    return {"ok": True}


@router.delete("/instances/{instance_id}/accounts/{username}", dependencies=[Depends(get_current_user)])
def delete_account(instance_id: str, username: str):
    username = _safe_name(username, "账号")
    instance, server = _load_instance(instance_id)
    if username in {"root", "postgres"}:
        raise HTTPException(status_code=400, detail="内置管理员账号不允许删除")
    with _connect(instance, server) as conn:
        if instance.engine == "postgresql":
            from psycopg import sql
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(username)))
        elif instance.engine in {"mysql", "mariadb"}:
            with conn.cursor() as cursor:
                cursor.execute("DROP USER %s@'%'", (username,))
        else:
            raise HTTPException(status_code=400, detail="Redis ACL 管理不在首版范围")
    return {"ok": True}
