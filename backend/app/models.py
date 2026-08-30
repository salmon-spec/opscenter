from sqlalchemy import Column, String, Integer, Boolean, Text, DateTime, ForeignKey, Float, BigInteger, Date, Enum as SAEnum, UniqueConstraint, Index, JSON, Uuid
from sqlalchemy.dialects.postgresql import JSONB as PostgreSQLJSONB
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime
import uuid, enum

Base = declarative_base()
UUID = Uuid
JSONB = JSON().with_variant(PostgreSQLJSONB(), "postgresql")

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
    log_agent_status = Column(String(20), default="unknown")  # unknown/checking/deploying/running/error
    log_agent_version = Column(String(20), nullable=True)
    log_agent_error = Column(Text, nullable=True)
    log_agent_checked_at = Column(DateTime, nullable=True)
    services = relationship("Service", back_populates="server", cascade="all, delete-orphan")
    database_instances = relationship("DatabaseInstance", back_populates="server", cascade="all, delete-orphan")

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
    deploy_type = Column(String(20), nullable=True)  # v3.29: 部署方式 docker/systemd/compose/manual
    started_at = Column(DateTime, nullable=True)     # v3.29: 启动时间（容器/服务扫描回填）
    version = Column(String(60), nullable=True)      # v3.29: 版本（镜像 tag / 服务版本号）
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


class DatabaseInstance(Base):
    """Managed MySQL/PostgreSQL/Redis connection owned by a host context."""
    __tablename__ = "database_instances"
    __table_args__ = (
        UniqueConstraint("server_id", "engine", "host", "port", name="uq_database_instance_target"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id = Column(UUID(as_uuid=True), ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    engine = Column(String(20), nullable=False)
    source = Column(String(20), default="manual", nullable=False)
    connection_mode = Column(String(20), default="direct", nullable=False)
    host = Column(String(200), nullable=False)
    port = Column(Integer, nullable=False)
    username = Column(String(100), nullable=True)
    secret_ciphertext = Column(Text, nullable=True)
    default_database = Column(String(100), nullable=True)
    container_name = Column(String(128), nullable=True)
    version = Column(String(100), nullable=True)
    status = Column(String(20), default="pending", nullable=False)
    last_error = Column(Text, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    server = relationship("Server", back_populates="database_instances")


class ServiceProbeResult(Base):
    """Persistent result of an explicitly configured service probe."""
    __tablename__ = "service_probe_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_id = Column(UUID(as_uuid=True), ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True)
    checked_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    status = Column(String(20), nullable=False)
    http_status = Column(Integer, nullable=True)
    latency_ms = Column(Float, nullable=True)
    error = Column(Text, nullable=True)
    probe_url = Column(Text, nullable=True)


class PlazaServicePreference(Base):
    """Persistent user visibility preference for checked-in plaza entries."""
    __tablename__ = "plaza_service_preferences"

    catalog_key = Column(String(100), primary_key=True)
    hidden = Column(Boolean, default=False, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class PlazaServiceProfile(Base):
    """Editable plaza metadata and encrypted login credentials.

    ``plaza_key`` is either a checked-in catalog key or ``manual-<service uuid>``.
    Keeping credentials outside ``services`` lets catalog entries and manual entries
    share the same safe storage and response rules.
    """
    __tablename__ = "plaza_service_profiles"

    plaza_key = Column(String(140), primary_key=True)
    server_id = Column(UUID(as_uuid=True), ForeignKey("servers.id", ondelete="SET NULL"), nullable=True, index=True)
    name = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)
    category = Column(String(50), nullable=True)
    icon = Column(String(50), nullable=True)
    entry_url = Column(Text, nullable=True)
    health_url = Column(Text, nullable=True)
    username = Column(String(200), nullable=True)
    secret_ciphertext = Column(Text, nullable=True)
    login_notes = Column(Text, nullable=True)
    documentation_url = Column(Text, nullable=True)
    owner = Column(String(100), nullable=True)
    tags = Column(JSONB, default=list)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class ServiceRelation(Base):
    """服务依赖关系（v3.29, 拓扑）：描述服务间数据流/调用/部署/反代关系，驱动拓扑图。"""
    __tablename__ = "service_relations"
    __table_args__ = (
        UniqueConstraint("source_service_id", "target_service_id", "relation_type",
                         name="uq_relation_src_tgt_type"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_service_id = Column(UUID(as_uuid=True), ForeignKey("services.id", ondelete="CASCADE"), nullable=False)
    target_service_id = Column(UUID(as_uuid=True), ForeignKey("services.id", ondelete="CASCADE"), nullable=False)
    relation_type = Column(String(20), default="data_flow")  # data_flow / invoke / deploy / proxy
    label = Column(String(50), nullable=True)                # 连线标签（如 webhook 触发 / 制品推送）
    scenario = Column(String(20), default="cicd")            # cicd / monitoring / gateway
    created_at = Column(DateTime, default=datetime.utcnow)

class MetricHistory(Base):
    """Historical metrics for remote servers (collected from Agent)."""
    __tablename__ = "metric_history"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id = Column(UUID(as_uuid=True), ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    metric = Column(String(20), nullable=False)
    value = Column(Float, nullable=False)


class MetricRollup(Base):
    """Long-term metric buckets generated from high-frequency samples."""
    __tablename__ = "metric_rollups"
    __table_args__ = (
        UniqueConstraint("server_id", "metric", "resolution", "bucket_at", name="uq_metric_rollup_bucket"),
        Index("ix_metric_rollup_lookup", "server_id", "metric", "resolution", "bucket_at"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id = Column(UUID(as_uuid=True), ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    metric = Column(String(20), nullable=False)
    resolution = Column(String(8), nullable=False)  # 5m / 1h
    bucket_at = Column(DateTime, nullable=False)
    value_avg = Column(Float, nullable=False)
    value_min = Column(Float, nullable=False)
    value_max = Column(Float, nullable=False)
    sample_count = Column(Integer, nullable=False, default=1)



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


class AlertSilence(Base):
    """告警静默/维护窗口（v3.27, S1）：命中 rule_id+server_id+时间窗口的规则跳过评估，不产生事件不通知。"""
    __tablename__ = "alert_silences"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id = Column(UUID(as_uuid=True), ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=True)  # NULL=全局静默
    server_id = Column(UUID(as_uuid=True), ForeignKey("servers.id", ondelete="CASCADE"), nullable=True)    # NULL=全部服务器
    starts_at = Column(DateTime, nullable=False)
    ends_at = Column(DateTime, nullable=False)
    reason = Column(String(255), default="")
    created_by = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class CertCheck(Base):
    """SSL 证书监控（v3.27, D1）：server_id 仅表示归属，探测从后端直接发起（目标为公网域名）。"""
    __tablename__ = "cert_checks"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id = Column(UUID(as_uuid=True), ForeignKey("servers.id", ondelete="CASCADE"), nullable=True)  # NULL=不关联主机
    domain = Column(String(255), nullable=False)
    port = Column(Integer, default=443)
    days_left = Column(Integer, nullable=True)          # 剩余天数（负数=已过期）
    not_after = Column(DateTime, nullable=True)         # 证书到期时间
    issuer = Column(String(255), nullable=True)         # 签发机构 CN
    last_error = Column(String(255), nullable=True)     # 最近探测错误（网络不通等）
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LogRule(Base):
    """日志异常检测规则（v3.27, D2）：对服务器指定日志尾部做正则匹配。"""
    __tablename__ = "log_rules"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    server_id = Column(UUID(as_uuid=True), ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    log_path = Column(String(255), nullable=False)
    pattern = Column(String(255), nullable=False)
    tail_lines = Column(Integer, default=200)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class LogMatch(Base):
    """日志命中明细（v3.27, D2）：保留最近命中行用于回溯。"""
    __tablename__ = "log_matches"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id = Column(UUID(as_uuid=True), ForeignKey("log_rules.id", ondelete="CASCADE"), nullable=False)
    server_id = Column(UUID(as_uuid=True), ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    matched_line = Column(Text, nullable=True)
    matched_at = Column(DateTime, default=datetime.utcnow)


class BackupCheck(Base):
    """备份状态验证（v3.27, D3）：检查备份文件新鲜度与大小，超期触发告警。"""
    __tablename__ = "backup_checks"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    server_id = Column(UUID(as_uuid=True), ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    target_path = Column(String(255), nullable=False)
    expected_interval_hours = Column(Integer, default=24)
    min_size_bytes = Column(BigInteger, default=0)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ImageStatus(Base):
    """Docker 镜像更新检测（v3.27, D4）：记录运行容器镜像与远端 digest 对比。"""
    __tablename__ = "image_status"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id = Column(UUID(as_uuid=True), ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    container_name = Column(String(100), nullable=False)
    image = Column(String(200), nullable=False)
    local_digest = Column(String(100), nullable=True)
    remote_digest = Column(String(100), nullable=True)
    outdated = Column(Boolean, default=False)
    checked_at = Column(DateTime, default=datetime.utcnow)


class DailyReport(Base):
    """巡检日报（v3.28, R1）：每日一次聚合多源检测数据，生成结构化摘要 + Markdown 正文。"""
    __tablename__ = "daily_reports"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_date = Column(Date, nullable=False, unique=True)   # 报告日期（UTC 当日）
    title = Column(String(200), nullable=False)
    summary = Column(JSONB, nullable=False)                    # 结构化摘要（服务器/告警/证书/日志/备份/镜像）
    content = Column(Text, nullable=False)                     # Markdown 正文（推送用）
    created_at = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    """操作审计日志（v3.28, A1）：写操作（POST/PUT/DELETE）自动记录，供安全回溯。"""
    __tablename__ = "audit_logs"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ts = Column(DateTime, default=datetime.utcnow)
    username = Column(String(50), default="admin")
    action = Column(String(20), nullable=False)          # create/update/delete/login/scan/generate
    resource = Column(String(50), nullable=False)        # alert-rule/server/silence/cert/log-rule/backup/image/report/status
    resource_id = Column(String(64), nullable=True)
    detail = Column(Text, nullable=True)
    ip = Column(String(45), nullable=True)
    status = Column(String(10), default="success")       # success/failed


class ApiKey(Base):
    """开放 API 密钥（v3.29, 开放 API）：存 SHA-256 哈希，仅向前端展示前缀，scope 区分读写。"""
    __tablename__ = "api_keys"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(50), nullable=False)
    key_hash = Column(String(128), nullable=False, unique=True)
    prefix = Column(String(16), nullable=False)           # 展示前缀，如 oh_rt_a1b2
    scope = Column(String(10), default="read")            # read / write
    enabled = Column(Boolean, default=True)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

