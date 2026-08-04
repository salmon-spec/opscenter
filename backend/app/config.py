"""OpsCenter Configuration — 集中管理所有常量和环境变量"""

import os
import socket

# ── Database ──
DB_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://opscenter:OpsCenter2026@127.0.0.1:5433/opscenter")


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


# ── Server Identity ──
LOCAL_HOST = os.getenv("LOCAL_HOST", _detect_local_ip())
LOCAL_DOMAIN = os.getenv("LOCAL_DOMAIN", "ops.salmon.xin")
LOCAL_SERVER_NAME = os.getenv("LOCAL_SERVER_NAME", "本机 (OpsCenter)")
LOCAL_AGENT_TOKEN = os.getenv("LOCAL_AGENT_TOKEN", "")

# ── Auth ──
JWT_SECRET = os.getenv("OPS_JWT_SECRET", "opscenter-default-secret-change-me-1234567890")
ADMIN_USER = os.getenv("OPS_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("OPS_ADMIN_PASSWORD", "OpsCenter@2026")

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
