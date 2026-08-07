from sqlalchemy import Column, String, Integer, Boolean, Text, DateTime, ForeignKey, Float, BigInteger, Date, Enum as SAEnum, UniqueConstraint
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



class NetworkStats(Base):
    """每日网络流量累计（v3.25.1）。每日 00:05 由后端归集任务写入。"""
    __tablename__ = "network_stats"
    __table_args__ = (UniqueConstraint("server_id", "date", "interface", name="uq_netstats_server_date_iface"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id = Column(UUID(as_uuid=True), ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False, index=True)
    interface = Column(String(32), nullable=False)
    rx_bytes = Column(BigInteger, default=0)
    tx_bytes = Column(BigInteger, default=0)
    rx_packets = Column(BigInteger, default=0)
    tx_packets = Column(BigInteger, default=0)
    rx_errors = Column(BigInteger, default=0)
    tx_errors = Column(BigInteger, default=0)
    peak_rx_mbps = Column(Float, nullable=True)
    peak_tx_mbps = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class NetworkLatency(Base):
    """延迟/丢包快照（v3.25.1）。后端探活时写入。"""
    __tablename__ = "network_latency"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id = Column(UUID(as_uuid=True), ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True)
    target = Column(String(255), nullable=False)
    latency_ms = Column(Float, nullable=True)
    loss_pct = Column(Float, default=0)
    jitter_ms = Column(Float, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)


class AlertRule(Base):
    """告警规则（v3.26, F4）。规则表 + 状态机，之后证书/日志/备份监控全部复用，只加规则不加逻辑。"""
    __tablename__ = "alert_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)                                  # 规则名，如「CPU 过高」
    server_id = Column(UUID(as_uuid=True), ForeignKey("servers.id", ondelete="CASCADE"), nullable=True)  # NULL=全局
    metric = Column(String(50), nullable=False)                                 # cpu_percent/memory_percent/disk_percent/server_status/agent_status
    value_type = Column(String(10), default="numeric")                          # numeric | string（H2 修正：阈值类型分流）
    operator = Column(String(5), default=">")                                   # > < >= <= == !=
    threshold = Column(String(50), nullable=False)                              # H2 修正：VARCHAR 兼容字符串阈值（如 'online'）
    duration_sec = Column(Integer, default=60)                                  # 持续 N 秒才触发（防抖）
    cooldown_sec = Column(Integer, default=300)                                # 恢复后冷却期，避免抖动重复触发
    notify_webhooks = Column(JSONB, default=lambda: [])                         # per-rule 飞书 webhook（M1：通知来源，去掉 settings 表依赖）
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AlertEvent(Base):
    """告警事件（v3.26, F4）。状态机：pending -> firing -> recovered / acked。"""
    __tablename__ = "alert_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id = Column(UUID(as_uuid=True), ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False)
    server_id = Column(UUID(as_uuid=True), ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(10), default="pending")                             # pending | firing | recovered | acked（H3 修正：新增 pending）
    current_value = Column(String(50), nullable=True)                          # 当前值（数值/字符串统一存文本，便于展示）
    first_breached_at = Column(DateTime, nullable=True)                        # H3：首次越限时间（duration 防抖判定）
    fired_at = Column(DateTime, nullable=True)                                 # 进入 firing 时间
    recovered_at = Column(DateTime, nullable=True)                             # 恢复时间（cooldown 冷却起点）
    last_notified_at = Column(DateTime, nullable=True)                         # 末次通知时间
    notified = Column(Boolean, default=False)
    acked_by = Column(String(50), nullable=True)
    acked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
