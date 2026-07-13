import docker, re, os, subprocess
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from app.models import Service, Server, ServiceSource, ServiceStatus

# Image -> category auto-classification
IMAGE_CATEGORIES = {
    "代码与CI/CD": ["gitea", "gitlab", "jenkins", "drone", "runner", "woodpecker"],
    "监控与日志": ["prometheus", "grafana", "loki", "promtail", "alertmanager", "node-exporter", "cadvisor", "kibana"],
    "网络与代理": ["nginx", "traefik", "caddy"],
    "数据存储": ["postgres", "mysql", "redis", "mongo", "mariadb", "registry", "elasticsearch"],
    "消息与注册": ["rabbitmq", "nacos", "zookeeper", "consul", "kafka"],
    "自动化工作流": ["n8n", "airflow", "temporal"],
    "运维管理": ["1panel", "portainer", "rustdesk"],
    "前端应用": ["mall-admin-web", "mall-app-web"],
    "应用服务": ["s-pdf", "frooodle", "it-tools", "corentinth", "mall-admin", "mall-search", "mall-portal", "mall-gateway", "mall-auth", "mall-monitor"],
    "安全与认证": ["2fauth", "vaultwarden"]
}

# Image -> icon mapping
IMAGE_ICONS = {
    "gitea": "fa-git-alt", "gitlab": "fa-gitlab", "jenkins": "fa-infinity",
    "prometheus": "fa-fire", "grafana": "fa-chart-area", "loki": "fa-database",
    "nginx": "fa-server", "postgres": "fa-database", "redis": "fa-bolt",
    "n8n": "fa-network-wired", "1panel": "fa-gauge-high",
"s-pdf": "fa-file-pdf", "frooodle": "fa-file-pdf",
    "it-tools": "fa-wrench", "corentinth": "fa-wrench",
    "alertmanager": "fa-bell", "node-exporter": "fa-microchip",
    "promtail": "fa-arrow-right", "registry": "fa-cubes",
    "traefik": "fa-route", "portainer": "fa-docker",
    "harbor": "fa-anchor", "trivy": "fa-shield-halved",
    "kibana": "fa-chart-bar", "elasticsearch": "fa-search", "rabbitmq": "fa-envelope",
    "nacos": "fa-sitemap", "mysql": "fa-database",
    "mall-admin": "fa-cogs", "mall-search": "fa-search",
    "mall-portal": "fa-store", "mall-gateway": "fa-door-open",
    "mall-auth": "fa-key", "mall-monitor": "fa-heartbeat",
    "mall-admin-web": "fa-desktop", "mall-app-web": "fa-mobile-screen",
    "mongo": "fa-leaf",
    "2fauth": "fa-shield-halved", "vaultwarden": "fa-key",
    "rustdesk": "fa-desktop", "hbbs": "fa-desktop", "hbbr": "fa-network-wired"
}

# Image -> description mapping
IMAGE_DESCS = {
    "gitea": "代码仓库", "jenkins": "CI/CD流水线", "prometheus": "指标采集",
    "grafana": "监控仪表盘", "loki": "日志聚合", "nginx": "反向代理",
    "n8n": "自动化工作流",
    "s-pdf": "PDF工具箱", "it-tools": "开发者工具集",
    "1panel": "运维管理面板", "alertmanager": "告警管理",
    "node-exporter": "主机指标采集", "promtail": "日志采集代理",
    "registry": "镜像仓库", "harbor": "容器镜像仓库",
    "postgres": "PostgreSQL数据库", "redis": "Redis缓存",
    "trivy": "漏洞扫描器",
    "kibana": "ES可视化平台", "elasticsearch": "搜索引擎", "rabbitmq": "消息队列",
    "nacos": "服务注册与配置中心", "mysql": "MySQL数据库", "mongo": "MongoDB文档数据库",
    "mall-admin": "后台管理服务", "mall-search": "商品搜索服务",
    "mall-portal": "会员门户服务", "mall-gateway": "API网关服务",
    "mall-auth": "认证授权服务", "mall-monitor": "服务监控",
    "mall-admin-web": "管理后台前端", "mall-app-web": "商城顾客端前端",
    "2fauth": "MFA双因素认证管理", "vaultwarden": "密码管理器",
    "rustdesk": "RustDesk远程桌面服务", "hbbs": "RustDesk信令服务器(ID/中继)",
    "hbbr": "RustDesk中继服务器"
}

# Container name -> URL mapping (based on nginx routes)
NAME_URLS = {
    "nginx": "http://{host}/", "gitea": "http://{host}/gitea/", "jenkins": "http://{host}/jenkins/",
    "prometheus": "http://{host}/prometheus/", "grafana": "http://{host}/grafana/",
"stirling-pdf": "http://{host}/pdf/",
    "it-tools": "http://{host}:8443", "n8n": "http://{host}/n8n/",
    "ai-frontend": "http://{host}/datahub/", "ai-api": "http://{host}/datahub/",
    "harbor-nginx": "https://{host}:8891",
    "1panel-hermes-agent": "http://{host}:9999/ops123",
    "2fauth": "http://{host}:8000",
    "vaultwarden": "http://{host}:8090",
    "hbbs": "http://{host}:21115",
    "hbbr": "http://{host}:21117"
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


def get_icon_for_container(image_name: str, container_name: str) -> str:
    """Get icon with container name priority (more specific match first)."""
    name_lower = container_name.lower()
    for kw, icon in IMAGE_ICONS.items():
        if kw in name_lower:
            return icon
    img_lower = image_name.lower()
    for kw, icon in IMAGE_ICONS.items():
        if kw in img_lower:
            return icon
    return "fa-cube"

def get_desc(image_name: str, container_name: str) -> str:
    # Container name match takes priority (more specific, e.g. hbbs > rustdesk)
    for kw, desc in IMAGE_DESCS.items():
        if kw in container_name.lower():
            return desc
    for kw, desc in IMAGE_DESCS.items():
        if kw in image_name.lower():
            return desc
    return ""

def get_url(container_name: str, host: str) -> Optional[str]:
    # Exact name match first
    if container_name in NAME_URLS:
        url = NAME_URLS[container_name]
        return url.replace("{host}", host)
    # Prefix match
    for name, url in NAME_URLS.items():
        if container_name.startswith(name):
            return url.replace("{host}", host)
    return None


def _get_host_network_ports(container_name: str) -> Dict[str, List[int]]:
    """For containers using network_mode=host, discover their listening ports
    by finding the container's main PID and then using `ss -tlnp` to filter
    only the ports bound by processes whose PGID matches the container's PID.
    Falls back to /proc/{pid}/net/tcp for PID-scoped discovery.
    Returns dict like {'0.0.0.0': [21115, 21116], ...}
    """
    try:
        # Get container main PID
        r = subprocess.run(
            ['docker', 'inspect', '--format', '{{.State.Pid}}', container_name],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode != 0:
            return {}
        pid = r.stdout.strip()
        if not pid or pid == '0':
            return {}

        # Use `ss -tlnp` to find listening ports owned by this PID (or child processes)
        # ss output: users:(("process",pid=1234,fd=6))
        # We collect ports where the process PID matches or is a child of container PID
        ports_by_bind = {}
        
        # First, get all child PIDs of the container process
        r2 = subprocess.run(
            ['pgrep', '-P', pid],
            capture_output=True, text=True, timeout=3
        )
        container_pids = {pid}
        if r2.returncode == 0:
            for child_pid in r2.stdout.strip().split('\n'):
                child_pid = child_pid.strip()
                if child_pid:
                    container_pids.add(child_pid)
                    # Also get grandchildren
                    r3 = subprocess.run(
                        ['pgrep', '-P', child_pid],
                        capture_output=True, text=True, timeout=2
                    )
                    if r3.returncode == 0:
                        for gc_pid in r3.stdout.strip().split('\n'):
                            gc_pid = gc_pid.strip()
                            if gc_pid:
                                container_pids.add(gc_pid)

        # Now scan ss output for ports bound by these PIDs
        r4 = subprocess.run(
            ['ss', '-tlnp'],
            capture_output=True, text=True, timeout=5
        )
        if r4.returncode == 0:
            for line in r4.stdout.split('\n')[1:]:
                parts = line.split()
                if len(parts) < 5:
                    continue
                local_addr = parts[3] if len(parts) > 3 else parts[-2]
                process_info = ' '.join(parts[4:]) if len(parts) > 4 else ''
                
                # Check if this socket belongs to one of our container PIDs
                matched = False
                if 'users:((' in process_info:
                    for cpid in container_pids:
                        if f'pid={cpid},' in process_info or f'pid={cpid})' in process_info:
                            matched = True
                            break
                
                if not matched:
                    continue
                
                # Parse bind address and port
                bind_ip = '0.0.0.0'
                port = 0
                if ':' in local_addr:
                    if local_addr.startswith('['):
                        bracket_end = local_addr.index(']')
                        bind_ip = local_addr[1:bracket_end]
                        port_str = local_addr[bracket_end + 2:]
                    else:
                        bind_ip = local_addr.rsplit(':', 1)[0]
                        port_str = local_addr.rsplit(':', 1)[-1]
                    try:
                        port = int(port_str)
                    except ValueError:
                        continue
                
                # Normalize bind_ip
                if bind_ip in ('0.0.0.0', '*', '::', ':::'):
                    bind_ip = '0.0.0.0'
                elif bind_ip.startswith('127.'):
                    bind_ip = '127.0.0.1'
                
                if port > 0:
                    ports_by_bind.setdefault(bind_ip, []).append(port)

        # Deduplicate ports per bind address
        for k in ports_by_bind:
            ports_by_bind[k] = sorted(set(ports_by_bind[k]))
        return ports_by_bind
    except Exception as e:
        print(f"Host network port scan error for {container_name}: {e}")
        return {}


def discover_docker_services(server: Server, db: Session, host: str = None) -> List[Service]:
    """Discover services from Docker containers. Returns list of new/updated services."""
    if host is None:
        host = server.host
    try:
        client = docker.from_env() if server.is_local else None
        if not client:
            return []
        
        containers = client.containers.list()
        discovered = []
        
        for ctr in containers:
            name = ctr.name.replace("-", " ").replace("_", " ").title().replace(" ", "") 
            # Better name from container name
            raw_name = ctr.name
            labels = ctr.labels or {}
            image_name = ctr.image.tags[0] if ctr.image.tags else str(ctr.image.id[:20])
            short_image = image_name.split(":")[0].split("/")[-1]
            
            # Check for opscenter.* labels first
            if labels.get("opscenter.enable", "").lower() != "true" and not labels.get("opscenter.name"):
                # Auto-discover: skip very low-level containers
                skip_images = ["pause", "kindnet", "kube-proxy", "coredns", "etcd", "apiserver"]
                if any(s in short_image for s in skip_images):
                    continue
            
            # Get metadata - labels take priority
            svc_name = labels.get("opscenter.name", raw_name.replace("-", " ").replace("_", " ").title())
            svc_url = labels.get("opscenter.url", get_url(raw_name, host) or "")
            svc_category = labels.get("opscenter.category", classify_image(short_image))
            svc_icon = labels.get("opscenter.icon", get_icon_for_container(short_image, raw_name))
            svc_desc = labels.get("opscenter.desc", labels.get("opscenter.description", get_desc(short_image, raw_name)))
            svc_health = labels.get("opscenter.health", None)
            source = ServiceSource.docker_label.value if labels.get("opscenter.name") else ServiceSource.docker_auto.value
            
            # Determine URL based on ports
            # Also detect host network mode and resolve ports from /proc
            is_host_network = False
            host_net_ports = {}
            try:
                ctr.reload()
                net_mode = ctr.attrs.get('HostConfig', {}).get('NetworkMode', '')
                is_host_network = net_mode == 'host'
            except Exception:
                pass

            if not svc_url:
                ports = ctr.ports
                # First try standard Docker port mapping
                for p in (ports or {}):
                    if isinstance(p, tuple) and len(p) == 2:
                        host_port = p[1] if p[0] == "0.0.0.0" or p[0] == "" else None
                        if host_port:
                            svc_url = f"http://{host}:{host_port}"
                            break

            # Fallback: for host network containers with no mapped ports,
            # probe the container's listening ports from /proc
            if not svc_url and is_host_network:
                host_net_ports = _get_host_network_ports(raw_name)
                public_ports = host_net_ports.get('0.0.0.0', [])
                if public_ports:
                    # Use NAME_URLS first, then lowest public port
                    svc_url = get_url(raw_name, host) or ""
                    if not svc_url:
                        # Pick the lowest public port (usually the main service port)
                        main_port = min(public_ports)
                        svc_url = f"http://{host}:{main_port}"
            
            # For host network containers, always probe ports (even if URL was found via NAME_URLS)
            if is_host_network and not host_net_ports:
                host_net_ports = _get_host_network_ports(raw_name)
            
            if not svc_url:
                continue
            
            # Build ports_str: for host network containers, use discovered ports
            if is_host_network and host_net_ports:
                port_parts = []
                for bind, plist in sorted(host_net_ports.items()):
                    for p in plist:
                        port_parts.append(f"{bind}:{p}")
                ports_str = ", ".join(port_parts)
            else:
                ports_str = ", ".join([f"{k}->{v}" for k, v in (ctr.ports or {}).items() if v]) if ctr.ports else ""
            
            # Upsert service
            existing = db.query(Service).filter(
                Service.server_id == server.id,
                Service.container_name == raw_name
            ).first()
            
            if existing:
                updated = False
                for field, val in [
                    ("name", svc_name), ("url", svc_url), ("category", svc_category),
                    ("icon", svc_icon), ("description", svc_desc), ("image", image_name),
                    ("ports", ports_str), ("health_path", svc_health), ("source", source),
                    ("container_id", ctr.id[:12]),
                ]:
                    if val and getattr(existing, field) != val:
                        setattr(existing, field, val)
                        updated = True
                if updated:
                    discovered.append(existing)
            else:
                svc = Service(
                    server_id=server.id,
                    name=svc_name,
                    url=svc_url,
                    category=svc_category,
                    icon=svc_icon,
                    description=svc_desc,
                    source=source,
                    status=ServiceStatus.unknown.value,
                    health_path=svc_health,
                    container_id=ctr.id[:12],
                    container_name=raw_name,
                    image=image_name,
                    ports=ports_str,
                )
                db.add(svc)
                discovered.append(svc)
                # Auto-assign group
                try:
                    from app.main import _auto_assign_group
                    _auto_assign_group(str(server.id), str(svc.id), svc.category or '')
                except Exception:
                    pass
        
        # Mark stale docker_auto services as down when their container is gone
        active_container_names = {ctr.name for ctr in containers}
        stale_services = db.query(Service).filter(
            Service.server_id == server.id,
            Service.source == ServiceSource.docker_auto.value,
            Service.status != ServiceStatus.down.value,
        ).all()
        for svc in stale_services:
            if svc.container_name and svc.container_name not in active_container_names:
                svc.status = ServiceStatus.down.value
                discovered.append(svc)

        # Permanently remove down docker_auto services whose container has been gone
        # (prevents zombie entries from accumulating indefinitely)
        gone_services = db.query(Service).filter(
            Service.server_id == server.id,
            Service.source == ServiceSource.docker_auto.value,
            Service.status == ServiceStatus.down.value,
            Service.container_name != None,
        ).all()
        for svc in gone_services:
            if svc.container_name not in active_container_names:
                db.delete(svc)

        db.commit()
        return discovered
        
    except Exception as e:
        db.rollback()
        print(f"Docker discovery error: {e}")
        return []


def parse_nginx_config(config_path: str = "/etc/nginx-source/nginx.conf", host: str = None) -> List[Dict]:
    """Parse Nginx config to discover service routes."""
    if not os.path.exists(config_path):
        return []
    
    if host is None:
        host = "127.0.0.1"
    try:
        with open(config_path, "r") as f:
            content = f.read()
        
        routes = []
        # Match location blocks: location /path/ {
        pattern = r'location\s+([~^]*)\s*(/\w+/?)\s*\{'
        for match in re.finditer(pattern, content):
            path = match.group(2)
            if path in ["/", "/api/", "/ws/"]:
                continue
            name = path.strip("/").replace("-", " ").replace("/", " ").title()
            routes.append({
                "name": name,
                "url": f"http://{host}{path}",
                "source": "nginx",
            })
        return routes
    except Exception as e:
        print(f"Nginx parse error: {e}")
        return []
