"""OpsCenter Configuration — 集中管理所有常量和环境变量

v3.25 起使用 pydantic-settings 热加载：环境变量优先级高于默认值，
凭证由 systemd EnvironmentFile=/etc/opscenter/secrets.env 注入。
"""

import socket

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

    # ── Auth（免登录默认，OPS_AUTH_ENABLED=true 恢复 JWT） ──
    jwt_secret: str = "opscenter-default-secret-change-me-1234567890"
    admin_user: str = "admin"
    admin_password: str = "OpsCenter@2026"
    auth_enabled: bool = False


_settings = Settings()

# 兼容旧引用（database.py / main.py 使用模块级常量）
DB_URL = _settings.database_url
LOCAL_HOST = _settings.local_host or _detect_local_ip()
LOCAL_DOMAIN = _settings.local_domain
LOCAL_SERVER_NAME = _settings.local_server_name
LOCAL_AGENT_TOKEN = _settings.local_agent_token
JWT_SECRET = _settings.jwt_secret
ADMIN_USER = _settings.admin_user
ADMIN_PASSWORD = _settings.admin_password
OPS_AUTH_ENABLED = _settings.auth_enabled

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
