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
    "2fauth": "http://{host}",
    "vaultwarden": "http://{host}:8090",
    "hbbs": "http://{host}:21115",
    "hbbr": "http://{host}:21117",
    "ops-db": "postgresql://{host}:5432"
}

# 镜像名前缀映射
IMAGE_PREFIX_URLS = {
    'redis': {'url': 'redis://{host}:6379', 'category': '数据存储', 'name': 'Redis'},
    'mysql': {'url': 'mysql://{host}:3306', 'category': '数据存储', 'name': 'MySQL'},
    'postgres': {'url': 'postgresql://{host}:5432', 'category': '数据存储', 'name': 'PostgreSQL'},
    'mongo': {'url': 'mongodb://{host}:27017', 'category': '数据存储', 'name': 'MongoDB'},
    'rabbitmq': {'url': 'http://{host}:15672', 'category': '消息与注册', 'name': 'RabbitMQ'},
    'nacos': {'url': 'http://{host}:8848/nacos', 'category': '消息与注册', 'name': 'Nacos'},
    'elasticsearch': {'url': 'http://{host}:9200', 'category': '数据存储', 'name': 'Elasticsearch'},
    'minio': {'url': 'http://{host}:9001', 'category': '数据存储', 'name': 'MinIO'},
}

# 端口协议推断
PORT_PROTOCOL_HINTS = {
    80: 'http', 443: 'https', 8080: 'http', 8443: 'https',
    3000: 'http', 5000: 'http', 9000: 'http', 9090: 'http',
    3306: 'mysql', 5432: 'postgresql', 5433: 'postgresql', 6379: 'redis',
    27017: 'mongodb', 9200: 'http', 15672: 'http',
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
        return url.replace("{host}", _normalize_host(host))
    # Prefix match
    for name, url in NAME_URLS.items():
        if container_name.startswith(name):
            return url.replace("{host}", _normalize_host(host))
    return None


def _normalize_host(host: str) -> str:
    """Normalize loopback addresses for URL generation.

    Both 'localhost' and '127.0.0.1' are loopback addresses that produce
    URLs inaccessible from remote browsers. This function attempts to
    resolve them to the machine's externally reachable IP so that
    generated service URLs work from any client.
    """
    if host not in ('localhost', '127.0.0.1', '0.0.0.0'):
        return host
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and ip not in ('127.0.0.1', '0.0.0.0'):
            return ip
    except Exception:
        pass
    return host


def _get_host_network_ports(container_name: str) -> Dict[str, List[int]]:
    """For containers using network_mode=host, discover their listening ports
    by finding the container's main PID and then using `ss -tlnp` to filter
    only the ports bound by processes whose PGID matches the container's PID.
    Falls back to /proc/{pid}/net/tcp for PID-scoped discovery.
    Returns dict like {'0.0.0.0': [21115, 21116], ...}
    """
    try:
        r = subprocess.run(
            ['docker', 'inspect', '--format', '{{.State.Pid}}', container_name],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode != 0:
            return {}
        pid = r.stdout.strip()
        if not pid or pid == '0':
            return {}

        ports_by_bind = {}
        
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
                    r3 = subprocess.run(
                        ['pgrep', '-P', child_pid],
                        capture_output=True, text=True, timeout=2
                    )
                    if r3.returncode == 0:
                        for gc_pid in r3.stdout.strip().split('\n'):
                            gc_pid = gc_pid.strip()
                            if gc_pid:
                                container_pids.add(gc_pid)

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
                
                matched = False
                if 'users:((' in process_info:
                    for cpid in container_pids:
                        if f'pid={cpid},' in process_info or f'pid={cpid})' in process_info:
                            matched = True
                            break
                
                if not matched:
                    continue
                
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
                
                if bind_ip in ('0.0.0.0', '*', '::', ':::'):
                    bind_ip = '0.0.0.0'
                elif bind_ip.startswith('127.'):
                    bind_ip = '127.0.0.1'
                
                if port > 0:
                    ports_by_bind.setdefault(bind_ip, []).append(port)

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
    # Normalize loopback addresses so generated URLs are externally accessible
    host = _normalize_host(host)
    try:
        client = docker.from_env() if server.is_local else None
        if not client:
            return []
        
        containers = client.containers.list()
        discovered = []
        
        for ctr in containers:
            name = ctr.name.replace("-", " ").replace("_", " ").title().replace(" ", "") 
            raw_name = ctr.name
            labels = ctr.labels or {}
            image_name = ctr.image.tags[0] if ctr.image.tags else str(ctr.image.id[:20])
            short_image = image_name.split(":")[0].split("/")[-1]
            
            if labels.get("opscenter.enable", "").lower() != "true" and not labels.get("opscenter.name"):
                skip_images = ["pause", "kindnet", "kube-proxy", "coredns", "etcd", "apiserver"]
                if any(s in short_image for s in skip_images):
                    continue
            
            svc_name = labels.get("opscenter.name", raw_name.replace("-", " ").replace("_", " ").title())
            svc_url = labels.get("opscenter.url", get_url(raw_name, host) or "")
            svc_category = labels.get("opscenter.category", classify_image(short_image))
            svc_icon = labels.get("opscenter.icon", get_icon_for_container(short_image, raw_name))
            svc_desc = labels.get("opscenter.desc", labels.get("opscenter.description", get_desc(short_image, raw_name)))
            svc_health = labels.get("opscenter.health", None)
            source = ServiceSource.docker_label.value if labels.get("opscenter.name") else ServiceSource.docker_auto.value
            
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
                for p in (ports or {}):
                    if isinstance(p, tuple) and len(p) == 2:
                        host_port = p[1] if p[0] == "0.0.0.0" or p[0] == "" else None
                        if host_port:
                            svc_url = f"http://{host}:{host_port}"
                            break

            if not svc_url and is_host_network:
                host_net_ports = _get_host_network_ports(raw_name)
                public_ports = host_net_ports.get('0.0.0.0', [])
                if public_ports:
                    svc_url = get_url(raw_name, host) or ""
                    if not svc_url:
                        main_port = min(public_ports)
                        svc_url = f"http://{host}:{main_port}"
            
            if is_host_network and not host_net_ports:
                host_net_ports = _get_host_network_ports(raw_name)
            
            # 镜像名前缀推断
            if not svc_url and image_name:
                for prefix, info in IMAGE_PREFIX_URLS.items():
                    if image_name.split('/')[-1].split(':')[0].lower().startswith(prefix):
                        svc_url = info['url'].format(host=host)
                        if not svc_category or svc_category == '未分类':
                            svc_category = info['category']
                        break

            # 端口协议推断（兜底）
            if not svc_url:
                port_num = None
                try:
                    port_bindings = ctr.attrs.get('HostConfig', {}).get('PortBindings', {})
                    if port_bindings:
                        for container_port, bindings in port_bindings.items():
                            if bindings and bindings[0].get('HostPort'):
                                port_num = int(bindings[0]['HostPort'])
                                break
                except Exception:
                    pass
                if port_num:
                    proto = PORT_PROTOCOL_HINTS.get(port_num, 'http')
                    if proto in ('http', 'https'):
                        svc_url = f"{proto}://{host}:{port_num}/"

            if not svc_url:
                svc_url = "#none"  # 无URL服务保留为纯信息卡片
            
            # Build ports_str: for host network containers, use discovered ports
            if is_host_network and host_net_ports:
                port_parts = []
                for bind, plist in sorted(host_net_ports.items()):
                    for p in plist:
                        port_parts.append(f"{bind}:{p}")
                ports_str = ", ".join(port_parts)
            else:
                ports_str = ", ".join([f"{k}->{v}" for k, v in (ctr.ports or {}).items() if v]) if ctr.ports else ""
            
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
                try:
                    from app.main import _auto_assign_group
                    _auto_assign_group(str(server.id), str(svc.id), svc.category or '')
                except Exception:
                    pass

        # 扫描已停止的容器
        try:
            all_containers = client.containers.list(all=True)
            stopped = [c for c in all_containers if c.status != 'running']
            for container in stopped:
                name = container.name
                image = str(container.image.tags[0]) if container.image.tags else str(container.image.id[:19])
                existing = next((s for s in discovered if s.container_name == name), None)
                if not existing:
                    existing_db = db.query(Service).filter(
                        Service.server_id == server.id,
                        Service.container_name == name
                    ).first()
                    if existing_db:
                        existing = existing_db
                if not existing:
                    short_img = image.split(":")[0].split("/")[-1]
                    svc = Service(
                        server_id=server.id,
                        name=f"{name} [已停止]",
                        url='#none',
                        category=classify_image(short_img),
                        icon=get_icon_for_container(short_img, name),
                        description=get_desc(short_img, name),
                        source=ServiceSource.docker_auto.value,
                        container_name=name,
                        image=image,
                        status=ServiceStatus.down.value,
                        ports='',
                    )
                    db.add(svc)
                    discovered.append(svc)
        except Exception as e:
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
    """Parse Nginx config to discover service routes.
    Scans /etc/nginx-source/nginx.conf, /etc/nginx/sites-enabled/, and /etc/nginx/conf.d/.
    Extracts server_name, location, and proxy_pass to generate service entries.
    """
    if host is None:
        host = "127.0.0.1"
    routes = []

    def _parse_config_content(content: str) -> List[Dict]:
        found = []
        server_blocks = re.findall(r'server\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}', content, re.DOTALL)
        if not server_blocks:
            server_blocks = [content]
        for block in server_blocks:
            server_names = re.findall(r'server_name\s+([^;]+);', block)
            sname = server_names[0].strip().split()[0] if server_names else host
            if sname == '_':
                sname = host
            locations = re.findall(r'location\s+([~^*]*)\s*(/\S*)\s*\{', block)
            proxy_passes = re.findall(r'proxy_pass\s+([^;]+);', block)
            filtered_locations = []
            for loc_match in locations:
                path = loc_match[1].rstrip()
                if path in ['/api/', '/ws/']:
                    continue
                if path == '/' and not server_names:
                    continue
                filtered_locations.append((loc_match, path))
            for (loc_match, path) in filtered_locations:
                if path == '/' :
                    name = sname.split('.')[0].replace('-', ' ').replace('_', ' ').title()
                else:
                    name = path.strip('/').replace('-', ' ').replace('/', ' ').title()
                proxy = ''
                for pp in proxy_passes:
                    proxy = pp.strip()
                found.append({
                    'name': name,
                    'url': f'http://{sname}{path}',
                    'source': 'nginx',
                    'proxy_pass': proxy,
                })
            if not locations:
                for pp in proxy_passes:
                    found.append({
                        'name': pp.strip().split('//')[-1].split('/')[0].split(':')[0] if '//' in pp else host,
                        'url': f'http://{sname}/',
                        'source': 'nginx',
                        'proxy_pass': pp.strip(),
                    })
        return found

    # Parse main nginx config
    nginx_search_paths = [
        config_path,
        '/etc/nginx-source/nginx.conf',
        '/etc/nginx/nginx.conf',
    ]
    parsed_main = set()
    for npath in nginx_search_paths:
        if os.path.exists(npath) and npath not in parsed_main:
            try:
                with open(npath, 'r') as f:
                    content = f.read()
                routes.extend(_parse_config_content(content))
                parsed_main.add(npath)
                includes = re.findall(r'include\s+([^;]+);', content)
                for inc in includes:
                    inc = inc.strip()
                    if os.path.exists(inc):
                        with open(inc, 'r') as f:
                            routes.extend(_parse_config_content(f.read()))
            except Exception as e:
                print(f'Nginx parse error for {npath}: {e}')

    # Scan sites-enabled and conf.d directories
    nginx_dirs = ['/etc/nginx/sites-enabled/', '/etc/nginx/conf.d/', '/etc/nginx-source/conf.d/']
    seen_paths = set()
    for ndir in nginx_dirs:
        if not os.path.isdir(ndir):
            continue
        for fname in sorted(os.listdir(ndir)):
            fpath = os.path.join(ndir, fname)
            if fpath in parsed_main or fpath in seen_paths or not os.path.isfile(fpath):
                continue
            seen_paths.add(fpath)
            try:
                with open(fpath, 'r') as f:
                    content = f.read()
                routes.extend(_parse_config_content(content))
            except Exception as e:
                print(f'Nginx parse error for {fpath}: {e}')

    # Deduplicate by url
    seen_urls = set()
    unique_routes = []
    for r in routes:
        if r['url'] not in seen_urls:
            seen_urls.add(r['url'])
            unique_routes.append(r)
    return unique_routes
