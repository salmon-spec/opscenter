from sqlalchemy import Column, String, Integer, Boolean, Text, DateTime, ForeignKey, Float, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime
import uuid, enum

Base = declarative_base()

class ServerStatus(str, enum.Enum):
    online = "online"
    offline = "offline"
    unknown = "unknown"

class ServiceStatus(str, enum.Enum):
    up = "up"
    down = "down"
    unknown = "unknown"

class ServiceSource(str, enum.Enum):
    docker_label = "docker_label"
    docker_auto = "docker_auto"
    nginx = "nginx"
    manual = "manual"
    agent = "agent"

class Server(Base):
    __tablename__ = "servers"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(50), nullable=False)
    host = Column(String(100), nullable=False)
    ssh_port = Column(Integer, default=22)
    ssh_user = Column(String(50), default="ops")
    ssh_key = Column(Text, nullable=True)
    tags = Column(JSONB, default=list)
    status = Column(String(20), default=ServerStatus.unknown.value)
    docker_available = Column(Boolean, default=False)
    is_local = Column(Boolean, default=False)
    last_seen = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    enabled = Column(Boolean, default=True)
    last_check_at = Column(DateTime, nullable=True)
    last_online_at = Column(DateTime, nullable=True)
    fail_count = Column(Integer, default=0)
    last_error = Column(Text, nullable=True)
    remark = Column(Text, nullable=True)
    auth_type = Column(String(20), default="password")
    agent_status = Column(String(20), default="not_deployed")
    agent_port = Column(Integer, default=19100)
    agent_token = Column(Text, nullable=True)
    agent_version = Column(String(20), nullable=True)
    agent_type = Column(String(20), default="remote")  # remote=SSH部署, local=本机内置
    services = relationship("Service", back_populates="server", cascade="all, delete-orphan")

    # ── is_local / agent_type 一致性约束 ──
    # is_local=True  必须对应 agent_type='local'  (本机内置)
    # is_local=False 必须对应 agent_type='remote' (SSH远程部署)
    # 修改任一字段后调用 sync_local_state() 同步，或使用 effective_is_local 属性获取权威值
    @property
    def effective_is_local(self) -> bool:
        """从 agent_type 推导的权威 is_local 值，用于运行时判断。"""
        return self.agent_type == 'local'

    def sync_local_state(self):
        """同步 is_local 与 agent_type，修改任一字段后调用此方法。"""
        self.is_local = (self.agent_type == 'local')

class Service(Base):
    __tablename__ = "services"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id = Column(UUID(as_uuid=True), ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    url = Column(Text, nullable=False)
    category = Column(String(50), default="未分类")
    icon = Column(String(50), default="server")
    description = Column(Text, default="")
    source = Column(String(20), default=ServiceSource.docker_auto.value)
    status = Column(String(20), default=ServiceStatus.unknown.value)
    pinned = Column(Boolean, default=False)
    health_path = Column(Text, nullable=True)
    container_id = Column(String(64), nullable=True)
    container_name = Column(String(100), nullable=True)
    image = Column(String(200), nullable=True)
    ports = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0)
    hidden = Column(Boolean, default=False)
    account = Column(String(100), nullable=True)
    password = Column(String(200), nullable=True)
    last_scanned_at = Column(DateTime, nullable=True)
    url_overridden = Column(Boolean, default=False)  # deprecated: 不再使用，扫描始终覆盖URL
    port = Column(Integer, nullable=True)  # 监听端口号（端口驱动扫描唯一键之一）
    proto = Column(String(10), default="tcp")  # 协议 tcp/udp
    host_ip = Column(String(100), nullable=True)  # 服务所在主机IP
    host_domain = Column(String(200), nullable=True)  # 服务域名（若有）
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    server = relationship("Server", back_populates="services")

class MetricHistory(Base):
    """Historical metrics for remote servers (collected from Agent)."""
    __tablename__ = "metric_history"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id = Column(UUID(as_uuid=True), ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    metric = Column(String(20), nullable=False)
    value = Column(Float, nullable=False)

class User(Base):
    """OpsCenter 登录用户（v3.23.0 新增，用于资源管理页鉴权）。"""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(100), nullable=True)
    role = Column(String(20), default="admin")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)

