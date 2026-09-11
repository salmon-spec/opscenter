"""OpsCenter Configuration — 集中管理所有常量和环境变量

v3.25 起使用 pydantic-settings 热加载：环境变量优先级高于默认值，
凭证由 systemd EnvironmentFile=/etc/opscenter/secrets.env 注入。
"""

import socket

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _detect_local_ip() -> str:
    """探测本机主 IP（LOCAL_HOST 默认值兜底）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class Settings(BaseSettings):
    """环境配置模型（v3.25 热加载）。字段名 = 环境变量名（大小写不敏感）。"""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    # ── Database ──
    database_url: str = "postgresql+psycopg://opscenter:opscenter123@127.0.0.1:5433/opscenter"

    # ── Server Identity ──
    local_host: str = ""
    local_domain: str = "ops.salmon.xin"
    local_server_name: str = "本机 (OpsCenter)"
    local_agent_token: str = ""
    # Docker 部署时指向宿主机网关；systemd 部署继续使用 127.0.0.1。
    local_agent_host: str = "127.0.0.1"
    # Shared TOFU store: first connection records a host key; later mismatches fail.
    ssh_known_hosts_file: str = "/opt/opscenter/frontend/ssh_known_hosts"

    # ── Auth（免登录默认，OPS_AUTH_ENABLED=true 恢复 JWT） ──
    jwt_secret: str = Field("", validation_alias=AliasChoices("OPS_JWT_SECRET", "JWT_SECRET"))
    admin_user: str = Field("admin", validation_alias=AliasChoices("OPS_ADMIN_USER", "ADMIN_USER"))
    admin_password: str = Field("", validation_alias=AliasChoices("OPS_ADMIN_PASSWORD", "ADMIN_PASSWORD"))
    # Must be overridden in production; deliberately independent from JWT rotation.
    credential_key: str = ""
    auth_enabled: bool = Field(False, validation_alias=AliasChoices("OPS_AUTH_ENABLED", "AUTH_ENABLED"))
    cors_origins: str = ""
    preview_mode: bool = False
    containerized: bool = False

    # ── Alerting (v3.26, F4/F5) ──
    alerting_enabled: bool = True                       # ALERTING_ENABLED=false 一键关停告警引擎
    silence_enabled: bool = True                        # SILENCE_ENABLED=false 跳过静默判断（回滚兜底）
    cert_scan_enabled: bool = True                      # CERT_SCAN_ENABLED=false 关闭证书采集（回滚兜底）
    cert_scan_interval_hours: int = 6                   # 证书探测周期（小时）
    log_scan_enabled: bool = True                        # LOG_SCAN_ENABLED=false 关闭日志异常扫描（回滚兜底）
    backup_check_enabled: bool = True                   # BACKUP_CHECK_ENABLED=false 关闭备份验证（回滚兜底）
    image_check_enabled: bool = True                    # IMAGE_CHECK_ENABLED=false 关闭镜像更新检测（回滚兜底）
    report_enabled: bool = True                          # REPORT_ENABLED=false 关闭巡检日报（回滚兜底）
    report_hour_utc: int = 0                             # 日报生成小时（UTC，默认 0 = 北京 08:00）
    audit_enabled: bool = True                           # AUDIT_ENABLED=false 关闭操作审计（回滚兜底）
    default_notify_webhooks: str = ""                   # 逗号分隔的飞书 webhook URL（全局默认通道，M1 修正：去掉不存在的 settings 表依赖）

    # ── Data Retention (v3.26, F2) ──
    retention_metric_days: int = 30                     # metric_history 保留天数（高频轮询）
    retention_latency_days: int = 7                     # network_latency 保留天数（快照）
    retention_stats_days: int = 180                     # network_stats 保留天数（日归集，低频）
    retention_rollup_5m_days: int = 365                 # 5分钟聚合保留1年
    retention_rollup_1h_days: int = 0                   # 0=小时聚合长期保存
    loki_url: str = ""                                  # 中央 Loki，例如 http://10.66.66.5:3100
    loki_public_url: str = ""                           # 远程 Alloy 可访问的地址；为空时沿用 loki_url
    loki_timeout_seconds: float = 12.0
    loki_retention_days: int = 365
    loki_data_dir: str = "/opt/opscenter-data/loki"
    alloy_version: str = "1.18.0"

    # ── Kubernetes / K3s 只读监控（需求基线 2026-09-10 §4.2） ──
    # 注意：pydantic-settings 环境变量名=字段名大写（K8S_API_URL 等，无 OPS_ 前缀）。
    # 优先级：K8S_API_URL+K8S_TOKEN(/K8S_TOKEN_FILE) > K8S_KUBECONFIG（kubeconfig 文件）> in_cluster（Pod 内 SA）。
    # 凭证只进后端内存，绝不返回前端、绝不写日志。
    k8s_kubeconfig: str = ""            # K8S_KUBECONFIG：kubeconfig 文件路径（vm1 等集群外测试环境用）
    k8s_api_url: str = ""               # K8S_API_URL：显式 API Server 地址（https://host:6443）
    k8s_token: str = ""                 # K8S_TOKEN：与 K8S_API_URL 搭配的 Bearer Token（可选）
    k8s_token_file: str = ""            # K8S_TOKEN_FILE：从文件读取 token（优先于 K8S_TOKEN）
    k8s_ca_path: str = ""               # K8S_CA_PATH：集群 CA 证书路径（env 模式校验 TLS）
    k8s_insecure_tls: bool = False      # K8S_INSECURE_TLS：true 时跳过 CA 校验（仅测试环境）
    k8s_request_timeout: float = 8.0    # 单请求超时（秒），需求基线 §4.2 超时/有限重试
    k8s_max_concurrency: int = 6        # 同时发往 K8s API 的请求并发上限
    k8s_cache_ttl: int = 15             # 集群列表/概览服务端短缓存（秒）

    # ── K8s 中间件接入（v5.0.0，全部可选，未配置即自动降级） ──
    # 字段名 = 环境变量名（无 OPS_ 前缀）。能力开关默认关闭，避免旧部署行为漂移；
    # 状态展示/管理页面只要 URL 配置即可用（只读）。
    mw_enabled: bool = True              # MW_ENABLED=false 一键关停全部中间件接入
    redis_url: str = ""                  # REDIS_URL=redis://:pass@redis.middleware.svc.cluster.local:6379/0
    mq_url: str = ""                     # MQ_URL=amqp://user:pass@rabbitmq.middleware.svc.cluster.local:5672/
    mq_enabled: bool = False             # MQ_ENABLED=true 时 Agent 部署/升级任务改走 RabbitMQ（worker 消费）
    mq_queue: str = "opscenter.tasks"    # MQ_QUEUE 任务主队列
    mq_dlq: str = "opscenter.tasks.dlq"  # MQ_DLQ 死信队列
    mq_management_url: str = ""          # MQ_MANAGEMENT_URL=rabbitmq.middleware...:15672（队列明细；空则从 MQ_URL 推导）
    kafka_bootstrap: str = ""            # KAFKA_BOOTSTRAP=kafka.middleware.svc.cluster.local:9092
    kafka_topic: str = "opscenter.events"  # KAFKA_TOPIC 事件流主题
    kafka_enabled: bool = False          # KAFKA_ENABLED=true 时审计/任务事件发 Kafka
    mongo_url: str = ""                  # MONGO_URL=mongodb://user:pass@mongodb.middleware.svc.cluster.local:27017/?authSource=admin
    mongo_db: str = "opscenter"          # MONGO_DB 文档库名
    mongo_enabled: bool = False          # MONGO_ENABLED=true 时审计/事件文档双写 MongoDB
    nacos_url: str = ""                  # NACOS_URL=http://nacos.middleware.svc.cluster.local:8848
    nacos_group: str = "DEFAULT_GROUP"   # NACOS_GROUP
    nacos_config_data_id: str = "opscenter.dynamic.json"  # NACOS_CONFIG_DATA_ID 动态配置 dataId
    nacos_username: str = ""             # NACOS_USERNAME（开启鉴权后填写）
    nacos_password: str = ""             # NACOS_PASSWORD
    nacos_enabled: bool = False          # NACOS_ENABLED=true 时启动发布实例信息 + 读取动态覆盖
    minio_endpoint: str = ""             # MINIO_ENDPOINT=minio.middleware.svc.cluster.local:9000
    minio_access_key: str = ""           # MINIO_ACCESS_KEY
    minio_secret_key: str = ""           # MINIO_SECRET_KEY
    minio_bucket: str = "opscenter"      # MINIO_BUCKET
    minio_secure: bool = False           # MINIO_SECURE=true 时使用 HTTPS
    minio_enabled: bool = False          # MINIO_ENABLED=true 时报告导出/对象浏览可用（只读仍可视图纸）
    zookeeper_hosts: str = ""            # ZOOKEEPER_HOSTS=zookeeper.middleware.svc.cluster.local:2181
    mw_leader_enabled: bool = False      # MW_LEADER_ENABLED=true 时 MQ worker/周期任务经 Redis 租约选主
    mw_probe_timeout: float = 4.0        # 状态探测/命令超时（秒）

    # ── 运维共享令牌（v5.0.0 高危端点兜底） ──
    # OPERATOR_TOKEN 非空时，新数据服务/高危端点要求 Bearer 携带该令牌；
    # 同时 OPS_AUTH_ENABLED=true 时优先走 JWT。两者都未启用则沿用免登录（现状兼容）。
    operator_token: str = ""


_settings = Settings()

# 兼容旧引用（database.py / main.py 使用模块级常量）
DB_URL = _settings.database_url
LOCAL_HOST = _settings.local_host or _detect_local_ip()
LOCAL_DOMAIN = _settings.local_domain
LOCAL_SERVER_NAME = _settings.local_server_name
LOCAL_AGENT_TOKEN = _settings.local_agent_token
LOCAL_AGENT_HOST = _settings.local_agent_host
SSH_KNOWN_HOSTS_FILE = _settings.ssh_known_hosts_file
JWT_SECRET = _settings.jwt_secret
ADMIN_USER = _settings.admin_user
ADMIN_PASSWORD = _settings.admin_password
CREDENTIAL_KEY = _settings.credential_key
OPS_AUTH_ENABLED = _settings.auth_enabled
CORS_ORIGINS = [item.strip() for item in _settings.cors_origins.split(",") if item.strip()]
PREVIEW_MODE = _settings.preview_mode
CONTAINERIZED = _settings.containerized

# ── Alerting (v3.26) ──
ALERTING_ENABLED = _settings.alerting_enabled
# v3.27 S1 告警静默开关
SILENCE_ENABLED = _settings.silence_enabled
# v3.27 D1 证书监控开关与周期
CERT_SCAN_ENABLED = _settings.cert_scan_enabled
CERT_SCAN_INTERVAL_HOURS = _settings.cert_scan_interval_hours
# v3.27 D2 日志异常扫描开关
LOG_SCAN_ENABLED = _settings.log_scan_enabled
# v3.27 D3 备份验证开关
BACKUP_CHECK_ENABLED = _settings.backup_check_enabled
# v3.27 D4 镜像更新检测开关
IMAGE_CHECK_ENABLED = _settings.image_check_enabled
# v3.28 R1 巡检日报开关与生成时间（UTC 小时）
REPORT_ENABLED = _settings.report_enabled
REPORT_HOUR_UTC = _settings.report_hour_utc
# v3.28 A1 操作审计开关
AUDIT_ENABLED = _settings.audit_enabled
# 全局默认飞书 webhook：per-rule 的 alert_rules.notify_webhooks 优先，为空时回退到此（M1 修正）
DEFAULT_NOTIFY_WEBHOOKS = [u.strip() for u in _settings.default_notify_webhooks.split(',') if u.strip()]

# ── Data Retention (v3.26, F2) ──
RETENTION_METRIC_DAYS = _settings.retention_metric_days
RETENTION_LATENCY_DAYS = _settings.retention_latency_days
RETENTION_STATS_DAYS = _settings.retention_stats_days
RETENTION_ROLLUP_5M_DAYS = _settings.retention_rollup_5m_days
RETENTION_ROLLUP_1H_DAYS = _settings.retention_rollup_1h_days
LOKI_URL = _settings.loki_url.rstrip("/")
LOKI_PUBLIC_URL = (_settings.loki_public_url or _settings.loki_url).rstrip("/")
LOKI_TIMEOUT_SECONDS = _settings.loki_timeout_seconds
LOKI_RETENTION_DAYS = _settings.loki_retention_days
LOKI_DATA_DIR = _settings.loki_data_dir
ALLOY_VERSION = _settings.alloy_version

# ── Kubernetes / K3s（需求基线 §4.2） ──
K8S_KUBECONFIG = _settings.k8s_kubeconfig
K8S_API_URL = _settings.k8s_api_url.rstrip("/") if _settings.k8s_api_url else ""
K8S_TOKEN = _settings.k8s_token
K8S_TOKEN_FILE = _settings.k8s_token_file
K8S_CA_PATH = _settings.k8s_ca_path
K8S_INSECURE_TLS = _settings.k8s_insecure_tls
K8S_REQUEST_TIMEOUT = _settings.k8s_request_timeout
K8S_MAX_CONCURRENCY = _settings.k8s_max_concurrency
K8S_CACHE_TTL = _settings.k8s_cache_ttl

# ── K8s 中间件接入（v5.0.0） ──
MW_ENABLED = _settings.mw_enabled
REDIS_URL = _settings.redis_url
MQ_URL = _settings.mq_url
MQ_ENABLED = _settings.mq_enabled
MQ_QUEUE = _settings.mq_queue
MQ_DLQ = _settings.mq_dlq
MQ_MANAGEMENT_URL = _settings.mq_management_url
KAFKA_BOOTSTRAP = _settings.kafka_bootstrap
KAFKA_TOPIC = _settings.kafka_topic
KAFKA_ENABLED = _settings.kafka_enabled
MONGO_URL = _settings.mongo_url
MONGO_DB = _settings.mongo_db
MONGO_ENABLED = _settings.mongo_enabled
NACOS_URL = _settings.nacos_url.rstrip("/") if _settings.nacos_url else ""
NACOS_GROUP = _settings.nacos_group
NACOS_CONFIG_DATA_ID = _settings.nacos_config_data_id
NACOS_USERNAME = _settings.nacos_username
NACOS_PASSWORD = _settings.nacos_password
NACOS_ENABLED = _settings.nacos_enabled
MINIO_ENDPOINT = _settings.minio_endpoint
MINIO_ACCESS_KEY = _settings.minio_access_key
MINIO_SECRET_KEY = _settings.minio_secret_key
MINIO_BUCKET = _settings.minio_bucket
MINIO_SECURE = _settings.minio_secure
MINIO_ENABLED = _settings.minio_enabled
ZOOKEEPER_HOSTS = _settings.zookeeper_hosts
MW_LEADER_ENABLED = _settings.mw_leader_enabled
MW_PROBE_TIMEOUT = _settings.mw_probe_timeout
OPERATOR_TOKEN = _settings.operator_token

# ── App ──
from app.version import VERSION  # noqa: E402

# ── Service Categorization ──
CATEGORY_TO_GROUP = {
    "代码与CI/CD": "cicd", "CI/CD": "cicd",
    "监控与日志": "monitor", "监控": "monitor",
    "网络与代理": "network",
    "数据存储": "database",
    "消息与注册": "middleware",
    "自动化工作流": "auto_workflow", "自动化": "auto_workflow",
    "运维管理": "ops", "运维面板": "ops",
    "应用服务": "app", "文档工具": "app", "开发工具": "app",
    "数据平台": "app", "前端应用": "app",
    "安全与认证": "security",
}

DEFAULT_GROUPS = [
    {"id": "app", "name": "应用服务", "order": 40, "color": "#f59e0b", "icon": "box"},
    {"id": "ungrouped", "name": "未分组", "order": 999, "color": "#475569", "icon": "inbox"},
]

# ── Systemd Filter ──
_SKIP_SYSTEMD_PREFIXES = (
    "systemd-", "dbus-", "dbus.", "user-", "user@", "session-",
    "getty@", "serial-", "multi-user-", "graphical-", "networkd-",
    "polkit", "udisks", "accounts-daemon", "irqbalance",
    "thermald", "powerd", "fwupd", "packagekit", "snapd.",
    "ModemManager", "NetworkManager", "wpa_supplicant",
    "cron", "atd", "rsyslog", "logrotate",
    "rsync", "chrony", "emergency", "rescue",
    "kmod", "lvm2", "dm-event", "multipathd", "mdmonitor",
    "cloud-", "snapd", "unattended", "apt-daily", "dpkg-",
    "keyboard", "console", "plymouth", "ufw",
    "aliyun", "aegis", "hbrclient", "ssh", "sshd",
    "containerd", "docker", "tuned", "auditd", "fail2ban",
    "opsagent", "opscenter-backend",
    "acpid", "apcupsd", "autofs", "avahi",
    "blk-availability", "brandbot", "cpupower",
    "dmraid", "dracut", "ebtables",
    "fstrim", "gpm", "halt", "init", "ip6tables", "iptables",
    "kdump", "killproc", "kexec", "libvirtd",
    "mcstrans", "messagebus", "microcode",
    "netconsole", "netfs", "nfs", "nfslock", "nscd",
    "portreserve", "postfix", "procps", "quota_nld",
    "rc", "rc-local", "rdisc", "restorecond",
    "rngd", "rpcbind", "rpcidmapd", "saslauthd",
    "smartd", "snmpd", "spice-vdagentd", "ssext",
    "sysstat", "system-setup", "tcsd", "vboxadd",
    "vboxdracf", "vgauthd", "vmtoolsd", "vmware",
    "xen", "yum", "zfs",
)

# ── Port Hints ──
_PORT_SERVICE_HINTS = {
    9100: {"name": "OpsCenter", "category": "运维管理", "icon": "tool",
           "url_tpl": "http://{host}:9100/", "desc": "运维工作台"},
    9091: {"name": "OpsCenter API", "category": "运维管理", "icon": "tool",
           "url_tpl": "http://{host}:9091/docs", "desc": "运维工作台后端API"},
    19100: {"name": "OpsAgent", "category": "运维管理", "icon": "eye",
            "url_tpl": "http://{host}:19100/health", "desc": "监控Agent"},
}
