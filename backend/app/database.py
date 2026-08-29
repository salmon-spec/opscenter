"""OpsCenter Database"""
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
from app.config import DB_URL
from app.models import Base

_engine_options = {"poolclass": QueuePool, "pool_size": 5, "max_overflow": 10}
if DB_URL.startswith("sqlite"):
    _engine_options["connect_args"] = {"check_same_thread": False}
engine = create_engine(DB_URL, **_engine_options)
SessionLocal = sessionmaker(bind=engine)

@contextmanager
def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def get_session():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def init_db():
    """初始化数据库表结构（User 模型 v3.25 已移除，仅建表）。"""
    Base.metadata.create_all(bind=engine)
