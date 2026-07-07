"""OpsCenter v4.0 - Multi-source service auto-discovery engine."""
import docker, re, os, json, socket
from typing import List, Dict, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from app.models import Service, Server, ServiceSource, ServiceStatus

# Image -> category
IMAGE_CATEGORIES = {
    "代码与CI/CD": ["gitea", "gitlab", "jenkins", "drone", "runner", "woodpecker"],
    "监控与日志": ["prometheus", "grafana", "loki", "promtail", "alertmanager", "node-exporter", "cadvisor", "kibana"],
    "网络与代理": ["nginx", "traefik", "caddy"],
    "数据存储": ["postgres", "mysql", "redis", "mongo", "mariadb", "registry", "elasticsearch", "minio"],
    "消息与注册": ["rabbitmq", "nacos", "zookeeper", "consul", "kafka"],
    "自动化工作流": ["n8n", "airflow", "temporal"],
    "运维管理": ["1panel", "portainer"],
    "前端应用": ["mall-admin-web", "mall-app-web"],
    "应用服务": ["s-pdf", "frooodle", "it-tools", "corentinth", "mall-admin", "mall-search", "mall-portal", "mall-gateway", "mall-auth", "mall-monitor"],
}

IMAGE_ICONS = {
    "gitea": "fa-git-alt", "gitlab": "fa-gitlab", "jenkins": "fa-infinity",
    "prometheus": "fa-fire", "grafana": "fa-chart-area", "loki": "fa-database",
    "nginx": "fa-server", "postgres": "fa-database", "redis": "fa-bolt",
    "n8n": "fa-network-wired", "1panel": "fa-gauge-high", "s-pdf": "fa-file-pdf",
    "frooodle": "fa-file-pdf", "it-tools": "fa-wrench", "corentinth": "fa-wrench",
    "alertmanager": "fa-bell", "node-exporter": "fa-microchip", "promtail": "fa-arrow-right",
    "registry": "fa-cubes", "harbor": "fa-anchor", "trivy": "fa-shield-halved",
    "kibana": "fa-chart-bar", "elasticsearch": "fa-search", "rabbitmq": "fa-envelope",
    "nacos": "fa-sitemap", "mysql": "fa-database", "mongo": "fa-leaf",
    "mall-admin": "fa-cogs", "mall-search": "fa-search", "mall-portal": "fa-store",
    "mall-gateway": "fa-door-open", "mall-auth": "fa-key", "mall-monitor": "fa-heartbeat",
    "mall-admin-web": "fa-desktop", "mall-app-web": "fa-mobile-screen",
}

IMAGE_DESCS = {
    "gitea": "代码仓库", "jenkins": "CI/CD流水线", "prometheus": "指标采集",
    "grafana": "监控仪表盘", "loki": "日志聚合", "nginx": "反向代理",
    "n8n": "自动化工作流", "s-pdf": "PDF工具箱", "it-tools": "开发者工具集",
    "1panel": "运维管理面板", "alertmanager": "告警管理", "node-exporter": "主机指标采集",
    "promtail": "日志采集代理", "registry": "镜像仓库", "harbor": "容器镜像仓库",
    "postgres": "PostgreSQL数据库", "redis": "Redis缓存", "trivy": "漏洞扫描器",
    "kibana": "ES可视化平台", "elasticsearch": "搜索引擎", "rabbitmq": "消息队列",
    "nacos": "服务注册与配置中心", "mysql": "MySQL数据库", "mongo": "MongoDB文档数据库",
    "mall-admin": "后台管理服务", "mall-search": "商品搜索服务",
    "mall-portal": "会员门户服务", "mall-gateway": "API网关服务",
    "mall-auth": "认证授权服务", "mall-monitor": "服务监控",
    "mall-admin-web": "管理后台前端", "mall-app-web": "商城顾客端前端",
}

NAME_URLS = {
    "nginx": "/", "gitea": "/gitea/", "jenkins": "/jenkins/",
    "prometheus": "/prometheus/", "grafana": "/grafana/",
    "stirling-pdf": "/pdf/", "it-tools": "http://{host}:8443", "n8n": "/n8n/",
    "ai-frontend": "/datahub/", "ai-api": "/datahub/",
    "harbor-nginx": "https://{host}:8891",
    "1panel-hermes-agent": "http://{host}:9999/ops123",
}


def classify_image(image_name: str) -> str:
    img_lower = image_name.lower()
    for cat, keywords in IMAGE_CATEGORIES.items():
        for kw in keywords:
            if kw in img_lower:
                return cat
    return "未分类"

def get_icon(image_name: str) -> str:
    img_lower = image_name.lower()
    for kw, icon in IMAGE_ICONS.items():
        if kw in img_lower:
            return icon
    return "fa-cube"

def get_desc(image_name: str, container_name: str) -> str:
    for kw, desc in IMAGE_DESCS.items():
        if kw in image_name.lower() or kw in container_name.lower():
            return desc
    return ""

def get_url(container_name: str, host: str) -> Optional[str]:
    if container_name in NAME_URLS:
        url = NAME_URLS[container_name]
        return url.replace("{host}", host)
    for name, url in NAME_URLS.items():
        if container_name.startswith(name):
            return url.replace("{host}", host)
    return None


# === Multi-source Discovery ===

def discover_docker_services(server: Server, db: Session, host: str = "39.99.139.131") -> List[Service]:
    """Discover services from Docker containers (local)."""
    try:
        client = docker.from_env() if server.is_local else None
        if not client:
            return []
        containers = client.containers.list()
        discovered = []
        now = datetime.utcnow()
        active_container_names = {ctr.name for ctr in containers}

        for ctr in containers:
            raw_name = ctr.name
            labels = ctr.labels or {}
            image_name = ctr.image.tags[0] if ctr.image.tags else str(ctr.image.id[:20])
            short_image = image_name.split(":")[0].split("/")[-1]

            # Skip low-level containers
            if labels.get("opscenter.enable", "").lower() != "true" and not labels.get("opscenter.name"):
                skip_images = ["pause", "kindnet", "kube-proxy", "coredns", "etcd", "apiserver"]
                if any(s in short_image for s in skip_images):
                    continue

            svc_name = labels.get("opscenter.name", raw_name.replace("-", " ").replace("_", " ").title())
            svc_url = labels.get("opscenter.url", get_url(raw_name, host) or "")
            svc_category = labels.get("opscenter.category", classify_image(short_image))
            svc_icon = labels.get("opscenter.icon", get_icon(short_image))
            svc_desc = labels.get("opscenter.desc", labels.get("opscenter.description", get_desc(short_image, raw_name)))
            svc_health = labels.get("opscenter.health", None)
            source = ServiceSource.docker_label.value if labels.get("opscenter.name") else ServiceSource.docker_auto.value
            discovery_type = "docker"

            if not svc_url:
                ports = ctr.ports
                for p in (ports or {}):
                    if isinstance(p, tuple) and len(p) == 2:
                        host_port = p[1] if p[0] == "0.0.0.0" or p[0] == "" else None
                        if host_port:
                            svc_url = f"http://{host}:{host_port}"
                            break
            if not svc_url:
                continue

            ports_str = ", ".join([f"{k}->{v}" for k, v in (ctr.ports or {}).items() if v]) if ctr.ports else ""

            # Extract public port for service.port field
            svc_port = None
            port_matches = re.findall(r'0\.0\.0\.0:(\d+)->', ports_str)
            if port_matches:
                svc_port = int(port_matches[0])

            # Upsert
            existing = db.query(Service).filter(
                Service.server_id == server.id, Service.container_name == raw_name
            ).first()

            if existing:
                for field, val in [
                    ("name", svc_name), ("url", svc_url), ("category", svc_category),
                    ("icon", svc_icon), ("description", svc_desc), ("image", image_name),
                    ("ports", ports_str), ("health_path", svc_health), ("source", source),
                    ("container_id", ctr.id[:12]), ("last_seen_at", now), ("port", svc_port),
                ]:
                    if val is not None and getattr(existing, field) != val:
                        setattr(existing, field, val)
                if existing.status == ServiceStatus.missing.value:
                    existing.status = ServiceStatus.up.value
                discovered.append(existing)
            else:
                svc = Service(
                    server_id=server.id, name=svc_name, url=svc_url,
                    category=svc_category, icon=svc_icon, description=svc_desc,
                    source=source, status=ServiceStatus.unknown.value,
                    health_path=svc_health, container_id=ctr.id[:12],
                    container_name=raw_name, image=image_name, ports=ports_str,
                    discovery_type=discovery_type, discovered_at=now, last_seen_at=now,
                    port=svc_port,
                )
                db.add(svc)
                discovered.append(svc)

        # Mark stale docker_auto services
        stale = db.query(Service).filter(
            Service.server_id == server.id,
            Service.source == ServiceSource.docker_auto.value,
            Service.status != ServiceStatus.down.value,
            Service.status != ServiceStatus.missing.value,
        ).all()
        for svc in stale:
            if svc.container_name and svc.container_name not in active_container_names:
                svc.status = ServiceStatus.down.value
                discovered.append(svc)

        # Mark as missing after continuous absence (3+ consecutive misses detected by background task)
        # For now, keep down services - they will be cleaned up by the missing marker later

        db.commit()
        return discovered
    except Exception as e:
        db.rollback()
        print(f"Docker discovery error: {e}")
        return []


def discover_listening_ports(server: Server, db: Session, host: str = "39.99.139.131") -> List[Service]:
    """Discover services from listening TCP ports (local only)."""
    discovered = []
    now = datetime.utcnow()
    try:
        # Read listening ports from /proc or ss
        import subprocess
        result = subprocess.run(['ss', '-tlnp'], capture_output=True, text=True, timeout=5)
        ports_info = []
        for line in result.stdout.strip().split('\n')[1:]:
            parts = line.split()
            if len(parts) >= 4:
                local_addr = parts[3]
                match = re.search(r':(\d+)$', local_addr)
                if match:
                    port_num = int(match.group(1))
                    # Skip very common / internal ports
                    if port_num in (22, 25, 80, 443):
                        continue
                    process = parts[-1] if len(parts) >= 5 else ""
                    ports_info.append((port_num, process))

        for port_num, process in ports_info:
            # Check if already discovered
            existing = db.query(Service).filter(
                Service.server_id == server.id, Service.port == port_num,
                Service.source == ServiceSource.port.value,
            ).first()
            if existing:
                existing.last_seen_at = now
                existing.status = ServiceStatus.up.value
                discovered.append(existing)
            else:
                svc = Service(
                    server_id=server.id,
                    name=f"Port {port_num}",
                    url=f"http://{host}:{port_num}",
                    source=ServiceSource.port.value,
                    status=ServiceStatus.unknown.value,
                    discovery_type="port",
                    discovered_at=now, last_seen_at=now,
                    port=port_num,
                    category="未分类", icon="fa-plug",
                    description=f"监听端口 {port_num}",
                )
                db.add(svc)
                discovered.append(svc)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Port discovery error: {e}")
    return discovered


def discover_prometheus_targets(server: Server, db: Session, host: str = "39.99.139.131") -> List[Service]:
    """Discover services from Prometheus targets."""
    discovered = []
    now = datetime.utcnow()
    try:
        import requests as req
        prom_url = os.getenv("PROMETHEUS_URL", "http://127.0.0.1:9090")
        resp = req.get(f"{prom_url}/api/v1/targets", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            targets = data.get("data", {}).get("activeTargets", [])
            seen_jobs = set()
            for t in targets:
                job = t.get("labels", {}).get("job", "")
                if not job or job in seen_jobs:
                    continue
                seen_jobs.add(job)
                health = t.get("health", "unknown")

                existing = db.query(Service).filter(
                    Service.server_id == server.id,
                    Service.source == ServiceSource.prometheus.value,
                    Service.name == f"Prometheus: {job}",
                ).first()
                if existing:
                    existing.last_seen_at = now
                    existing.status = ServiceStatus.up.value if health == "up" else ServiceStatus.down.value
                    discovered.append(existing)
                else:
                    svc = Service(
                        server_id=server.id,
                        name=f"Prometheus: {job}",
                        url=f"/prometheus/",
                        source=ServiceSource.prometheus.value,
                        status=ServiceStatus.up.value if health == "up" else ServiceStatus.down.value,
                        discovery_type="prometheus",
                        discovered_at=now, last_seen_at=now,
                        category="监控与日志", icon="fa-fire",
                        description=f"Prometheus target: {job}",
                    )
                    db.add(svc)
                    discovered.append(svc)
            db.commit()
    except Exception as e:
        print(f"Prometheus discovery error: {e}")
    return discovered


def discover_systemd_services(server: Server, db: Session, host: str = "39.99.139.131") -> List[Service]:
    """Discover systemd services (local only, for non-Docker services)."""
    discovered = []
    now = datetime.utcnow()
    try:
        import subprocess
        # List enabled systemd services that are running
        result = subprocess.run(
            ['systemctl', 'list-units', '--type=service', '--state=running', '--no-pager', '--no-legend'],
            capture_output=True, text=True, timeout=5
        )
        important_services = []  # Filter for ops-relevant services
        for line in result.stdout.strip().split('\n'):
            parts = line.split()
            if len(parts) >= 4:
                name = parts[0]
                active = parts[2]
                # Only track opscenter-backend and other key services
                if any(kw in name for kw in ['opscenter', 'docker', 'fail2ban', 'nginx', 'ssh']):
                    important_services.append((name, active))

        for svc_name, active in important_services:
            existing = db.query(Service).filter(
                Service.server_id == server.id,
                Service.source == ServiceSource.systemd.value,
                Service.name == svc_name.replace('.service', ''),
            ).first()
            if existing:
                existing.last_seen_at = now
                existing.status = ServiceStatus.up.value
                discovered.append(existing)
            else:
                svc = Service(
                    server_id=server.id,
                    name=svc_name.replace('.service', '').replace('-', ' ').title(),
                    url="",
                    source=ServiceSource.systemd.value,
                    status=ServiceStatus.up.value,
                    discovery_type="systemd",
                    discovered_at=now, last_seen_at=now,
                    category="运维管理", icon="fa-gear",
                    description=f"systemd service: {svc_name}",
                )
                db.add(svc)
                discovered.append(svc)
        db.commit()
    except Exception as e:
        print(f"Systemd discovery error: {e}")
    return discovered


def parse_nginx_config(config_path: str = "/etc/nginx-source/nginx.conf", host: str = "39.99.139.131") -> List[Dict]:
    """Parse Nginx config to discover service routes."""
    if not os.path.exists(config_path):
        return []
    try:
        with open(config_path, "r") as f:
            content = f.read()
        routes = []
        pattern = r'location\s+([~^]*)\s*(/\w+/?)\s*\{'
        for match in re.finditer(pattern, content):
            path = match.group(2)
            if path in ["/", "/api/", "/ws/"]:
                continue
            name = path.strip("/").replace("-", " ").replace("/", " ").title()
            routes.append({"name": name, "url": path, "source": "nginx"})
        return routes
    except Exception as e:
        print(f"Nginx parse error: {e}")
        return []


def run_full_discovery(server: Server, db: Session, host: str = "39.99.139.131") -> Dict:
    """Run all discovery sources for a server. Returns summary."""
    results = {"docker": 0, "port": 0, "prometheus": 0, "systemd": 0, "nginx": 0}

    if server.docker_available and server.is_local:
        discovered = discover_docker_services(server, db, host)
        results["docker"] = len(discovered)

    if server.is_local:
        discovered = discover_listening_ports(server, db, host)
        results["port"] = len(discovered)

        discovered = discover_prometheus_targets(server, db, host)
        results["prometheus"] = len(discovered)

        discovered = discover_systemd_services(server, db, host)
        results["systemd"] = len(discovered)

        # Nginx routes
        nginx_routes = parse_nginx_config(host=host)
        for route in nginx_routes:
            existing = db.query(Service).filter(
                Service.server_id == server.id,
                Service.url == route["url"],
                Service.source != ServiceSource.docker_label.value,
                Service.source != ServiceSource.docker_auto.value,
            ).first()
            if not existing:
                svc = Service(
                    server_id=server.id, name=route["name"], url=route["url"],
                    source=ServiceSource.nginx.value, status=ServiceStatus.unknown.value,
                    category="未分类", icon="fa-globe",
                    discovery_type="nginx", discovered_at=datetime.utcnow(),
                    last_seen_at=datetime.utcnow(),
                )
                db.add(svc)
                results["nginx"] += 1
        db.commit()

    return results
