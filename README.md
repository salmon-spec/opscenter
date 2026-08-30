# OpsCenter 运维工作台

> **当前版本：v4.3.0** · 更新于 2026-08-30
> 访问：https://ops.salmon.xin/ · 状态页：https://ops.salmon.xin/status/ · Vite 灰度页：https://ops.salmon.xin/v3/

面向 DevOps/SRE 的自托管**统一运维工作台**：管理服务器、服务、监控、告警、证书、日志、备份、镜像与巡检日报，中文界面，免登录访问，支持 SSH 终端直连与远程 Agent 采集。

---

## 一、项目简介

OpsCenter 定位为「运维导航 + 监控中心 + 告警生态 + 数据价值化」一体化平台：

- **统一导航**：服务发现（Docker SDK + Nginx 解析 + Agent 采集），服务卡片一键跳转，分组自由配置
- **监控中心**：CPU / 内存 / 磁盘 / 网络 / 负载 / IO 实时指标 + 24h 趋势图 + 容器列表
- **告警生态**：告警规则 / 事件 / 静默 + 多源检测（证书 / 日志 / 备份 / 镜像）
- **数据价值化**：巡检日报自动生成与飞书推送，7 天可用性状态页
- **操作审计**：写操作自动记录，90 天 TTL，可追溯
- **远程能力**：Agent 一键部署采集 + WebSocket SSH 终端 + 文件管理
- **资源控制台**：全局主机上下文；数据库、容器、系统独立分域；容器统计按需采集；系统监控、终端与进程管理独立加载

## 二、当前状态（2026-08-28）

| 项 | 状态 |
|---|---|
| 后端测试 | **pytest 130/130 全绿**（0 失败 0 错误，含主机/数据库/容器/系统/文件/防火墙/SSH/监控历史/日志中心契约） |
| API | 11 大模块全 200（health/servers/alert-rules/alert-silences/cert-checks/log-rules/backup-checks/images/reports/audit-logs/status-page） |
| Agent | v2.4.0 |
| 版本里程碑 | **v4.3.0**（文件、防火墙、SSH 管理、回收站、本机终端与服务广场加速） |
| 系统文件管理 | **v4.3 已交付**（本机/SFTP 浏览、编辑、上传下载、改名、可恢复删除） |
| 系统防火墙 / SSH | **v4.3 已交付**（UFW/Firewalld、防失联保护、SSH 配置/会话/登录日志） |
| 监控历史 | **v4.4 开发中**（按主机和时间段查询，5 分钟/1 小时分层汇总，CSV 导出） |
| 日志中心 | **v4.4 开发中**（Loki + Alloy，按主机/来源/服务/时间段检索，默认保留 365 天） |
| 部署 | VM2（192.168.1.153 / 10.66.66.5）systemd + Caddy 运行中 |
| 仓库 | GitLab = GitHub = 本地 = `77ca809` |

## 三、技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3 + FastAPI 0.115.6 + SQLAlchemy 2.0 + Pydantic 2 + PyJWT |
| 数据库 | PostgreSQL 16（`opscenter` 库） |
| 前端 | Vue 3 SPA（单文件 index.html）+ ECharts + Tailwind CSS；**Vite 5 工程化改造中**（frontend-vite → /v3/ 灰度路径） |
| Agent | Python 轻量采集器（v2.2.0，指标 / 服务发现 / registry-proxy） |
| 部署 | systemd（`opscenter-backend` :9091）+ Caddy 反代（:80）+ venv |
| CI/CD | Jenkins（Jenkinsfile）+ GitLab CI（.gitlab-ci.yml） |

## 四、系统架构

### 4.1 部署拓扑

```
┌─────────────────────────────────────────────────────────┐
│  浏览器 → https://ops.salmon.xin/  (Caddy :80)           │
│    ├── /            → frontend/index.html (Vue SPA)      │
│    ├── /api/v2/*    → reverse_proxy localhost:9091       │
│    ├── /ws/*        → WebSocket (SSH 终端)               │
│    ├── /status/     → status.html (状态页，匿名放行)      │
│    └── /v3/         → frontend-vite 构建产物（灰度）      │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│  opscenter-backend (systemd, uvicorn :9091)             │
│  FastAPI app/main.py + routers/{servers,services,       │
│  monitor,terminal}                                       │
│  ├── 采集层：Docker SDK 发现 / SSH 采集 / Agent 远程      │
│  ├── 检测层：alerting / cert_scanner / log_scanner /     │
│  │           backup_scanner / image_scanner             │
│  ├── 产品层：report_engine(巡检日报) / audit(操作审计)    │
│  └── 持久层：PostgreSQL 16（15 张表）                    │
└─────────────────────────────────────────────────────────┘
```

### 4.2 后端模块（backend/app/）

| 模块 | 职责 |
|---|---|
| `main.py` | FastAPI 应用：全部 /api/v2/* 端点 + 中间件（AuditMiddleware）+ 定时任务 |
| `routers/` | 分组路由：servers（服务器）、services（服务）、monitor（监控）、terminal（SSH 终端） |
| `alerting.py` | 告警引擎：规则 / 事件 / 静默 / 通知（飞书 webhook） |
| `cert_scanner.py` | 证书检测：SSL 证书有效期巡检 |
| `log_scanner.py` | 日志检测：系统日志规则匹配 |
| `backup_scanner.py` | 备份检测：备份任务状态巡检 |
| `image_scanner.py` | 镜像检测：Docker 镜像更新状态 |
| `report_engine.py` | 巡检日报引擎：7 类聚合 + Markdown 模板 |
| `audit.py` | 操作审计：写操作自动记录（中间件拦截） |
| `agent_manager.py` | Agent 部署 / 卸载 / 状态 / 服务发现 |
| `ssh_manager.py` / `ssh_terminal.py` | SSH 连接池 + WebSocket 终端 + 文件管理 |
| `discovery.py` | 服务自动发现（Docker + Nginx + Agent） |
| `models.py` | SQLAlchemy 模型（含数据库实例、主机、服务、监控、告警与审计） |

### 4.3 数据模型（15 张表）

| 表 | 说明 |
|---|---|
| servers / services | 服务器与可见服务 |
| metric_history / network_stats / network_latency | 监控历史与网络指标 |
| alert_rules / alert_events / alert_silences | 告警规则 / 事件 / 静默 |
| cert_checks / log_rules / log_matches / backup_checks / image_status | 多源检测结果 |
| **daily_reports** | 巡检日报（v3.28，90 天 TTL） |
| **audit_logs** | 操作审计（v3.28，90 天 TTL） |

## 五、功能清单

### 5.1 服务导航

- 服务器 Tab 筛选，分组展示（per-server 分组配置 + 主页面 Chip 筛选）
- 服务卡片点击一键跳转，多维搜索，在线状态实时检测（60s 轮询）
- 分组管理：创建 / 修改 / 删除分组，服务拖拽移动，自定义 URL

### 5.2 监控中心

- 统一头部：服务器选择器 + 状态标签
- 主指标行：CPU / 内存 / 磁盘（3 列 Gauge 卡片）
- 次指标行：网络速率 / 系统负载 / 磁盘 IO
- ECharts 趋势图：8 项指标 24h 时间轴
- 容器列表：30+ Docker 容器状态、镜像、端口
- 指标变色：>80% 橙色警告，>90% 红色告警 + 脉冲动画
- 性能优化：Prometheus 并行查询，monitor API 3.2s → 0.35s

### 5.3 告警生态（v3.27 补全）

| 检测源 | 内容 |
|---|---|
| 告警规则 | 自定义规则 + 事件确认 + 静默管理 + 飞书 webhook 通知 |
| 证书检查 | SSL 证书有效期巡检（cert_checks） |
| 日志规则 | 系统日志关键字匹配（log_rules / log_matches） |
| 备份检查 | 备份任务状态巡检（backup_checks） |
| 镜像检查 | Docker 镜像更新检测（image_status） |

### 5.4 巡检日报（v3.28 新增）

- 日报引擎：`daily_reports` 表 + report_engine 7 类聚合（在线/离线/告警/容器/证书/日志/备份）
- Markdown 模板 + reports API（列表 / 详情 / 手动生成，幂等覆盖）
- 定时推送：report_loop 每日 UTC 0 点（= 北京 08:00）+ 飞书交互卡片（复用全局 webhook）
- 前端日报中心：告警中心「巡检日报」Tab，7 天列表 + Markdown 渲染 + 手动生成

### 5.5 操作审计（v3.28 新增）

- `audit_logs` 表 + AuditMiddleware 写操作自动记录（创建/删除/修改）
- 白名单防递归 + `AUDIT_ENABLED` 开关（默认 true，false 即回滚）
- 审计 API + 前端审计日志 Tab（筛选 / 分页 / 90 天 TTL）

### 5.6 状态页

- 独立状态页 `/status/`（匿名放行）：摘要 / 告警中 / 服务器 / 服务 / **7 天可用性趋势条**（availability_7d，v3.28 新增）
- 状态页 API `/api/v2/status-page`

### 5.7 资源与凭证管理

- 服务器卡片：header + body 分区布局，信息 2×2 网格
- 手动添加 / 删除服务器（SSH 密码 / 密钥认证），SSH 测试连接
- 服务列表管理：编辑名称 / 描述 / URL / 隐藏 / 显示
- 凭证管理：账号密码掩码显示 + 一键复制，SVG 图标

### 5.8 远程终端（SSH Terminal）

- WebSocket SSH 终端（`/ws/terminal/{session_id}`，30s 重连宽限期）
- 文件管理：列表 / 下载 / 上传 / mkdir / 重命名 / 删除

### 5.9 Agent 管理

- 一键部署 / 卸载远程监控 Agent（v2.2.0）
- Agent 状态监控（运行 / 离线 / 未部署），自动采集远程主机指标
- 新端点：`/api/v1/registry-proxy`（Docker Hub digest 代理，TLS1.2 + IPv4 强制；MFA 安全组放开后生效）

### 5.10 前端 Vite 工程化（v3.28 起步）

- `frontend-vite/` 目录（Vite 5 + Vue 3.4），StatCard / StatusBadge 组件试点
- 构建产物 → `frontend/v3/` 灰度路径，构建后 JS 62.97KB / gzip 25KB
- 部署脚本 `deploy/frontend-vite.sh`；全量迁移顺延 v3.29

## 六、API 一览

| 模块 | 端点 |
|---|---|
| 服务器 | `GET/POST /api/v2/servers` · `GET/PUT/DELETE /api/v2/servers/{id}` · `POST /api/v2/servers/{id}/scan\|test` · `POST /api/v2/test-ssh` |
| 服务 | `GET/POST /api/v2/services` · `PUT/DELETE /api/v2/services/{id}` · `PATCH /{id}/pin` · `GET /api/v2/services/all` · `GET /api/v2/services-with-status` |
| 监控 | `GET /api/v2/monitor/{id}` · `/{id}/history` · `/{id}/network` · `/{id}/network/history` · `/{id}/network/latency` · `GET /api/v2/monitor/{id}/health-check` |
| 告警 | `GET/POST /api/v2/alert-rules` · `PUT/DELETE /{rule_id}` · `GET /api/v2/alert-events` · `POST /{event_id}/ack` · `GET/POST/DELETE /api/v2/alert-silences` |
| 证书 | `GET/POST /api/v2/cert-checks` · `POST /scan` · `DELETE /{check_id}` |
| 日志 | `GET/POST /api/v2/log-rules` · `POST /scan` · `GET /api/v2/log-matches` |
| 备份 | `GET/POST /api/v2/backup-checks` · `POST /scan` · `DELETE /{check_id}` |
| 镜像 | `GET /api/v2/images` · `POST /api/v2/images/scan` |
| 日报 | `GET /api/v2/reports` · `GET /{report_id}` · `POST /api/v2/reports/generate` |
| 审计 | `GET /api/v2/audit-logs`（筛选 / 分页） |
| 状态页 | `GET /api/v2/status-page`（含 availability_7d） |
| 分组配置 | `GET/PUT /api/v2/group-config` · `PATCH /service-map` · `POST/PUT/DELETE /groups/*` · `GET /merged` · `POST /apply-default` |
| 终端 | `POST /api/v2/terminal/sessions` · `GET /{session_id}/status\|files` · `POST /{session_id}/files/upload\|mkdir\|rename\|delete` · `WS /ws/terminal/{session_id}` |
| Agent | `POST /{server_id}/deploy-agent` · `GET /{server_id}/agent-status\|agent-metrics\|agent-history` · `DELETE /{server_id}/agent` |
| 其他 | `GET /api/v2/health` · `/stats` · `/categories` · `/health-check-url` · `POST /api/v2/scan` · `POST /api/v2/health-check` |

## 七、目录结构

```
OpsCenter/
├── backend/
│   ├── app/
│   │   ├── main.py               # FastAPI 应用（全部端点 + 中间件 + 定时任务）
│   │   ├── routers/              # servers / services / monitor / terminal
│   │   ├── alerting.py           # 告警引擎
│   │   ├── cert_scanner.py       # 证书检测
│   │   ├── log_scanner.py        # 日志检测
│   │   ├── backup_scanner.py     # 备份检测
│   │   ├── image_scanner.py      # 镜像检测
│   │   ├── report_engine.py      # 巡检日报引擎（v3.28）
│   │   ├── audit.py              # 操作审计（v3.28）
│   │   ├── agent_manager.py      # Agent 管理
│   │   ├── ssh_manager.py        # SSH 连接管理
│   │   ├── ssh_terminal.py       # WebSocket 终端 + 文件管理
│   │   ├── discovery.py          # 服务自动发现
│   │   ├── models.py             # SQLAlchemy 模型（15 表）
│   │   └── version.py            # 版本号（4.3.0，单一来源）
│   ├── tests/                    # pytest（52 用例）
│   └── requirements.txt
├── agent/
│   ├── opsagent.py               # 远程采集 Agent（v2.2.0）
│   └── scanner.py                # 主机扫描器
├── frontend/                     # Vue 3 SPA（index.html 单文件）
│   ├── index.html                # 主工作台
│   ├── status.html               # 状态页
│   ├── network.html              # 网络监控页
│   ├── services.json / groups.json
│   └── nginx.conf / Dockerfile
├── frontend-vite/                # Vite 5 工程化前端（v3.28 起步，灰度 /v3/）
├── deploy/
│   ├── deploy.sh                 # 部署脚本（backend/frontend/full + 回滚）
│   ├── rollback.sh               # 回滚脚本
│   ├── frontend-vite.sh          # Vite 构建部署脚本
│   └── caddy/Caddyfile.tmpl      # Caddy 反代模板
├── docker-compose.yml            # DEPRECATED（实际由 systemd 运行）
├── Jenkinsfile                   # Jenkins 流水线
├── .gitlab-ci.yml                # GitLab CI
└── README.md
```

## 八、快速开始

### 8.1 部署（生产，VM2）

```bash
# 1. 拉取代码
git clone git@github.com:fenda1217/OpsCenter.git /opt/opscenter

# 2. 安装依赖 + 启动后端（systemd）
cd /opt/opscenter
python3 -m venv venv && ./venv/bin/pip install -r backend/requirements.txt
systemctl enable --now opscenter-backend     # uvicorn :9091

# 3. 前端静态文件
cp -r frontend /opt/opscenter/frontend       # Caddy 托管

# 4. Caddy 反代（见 deploy/caddy/Caddyfile.tmpl）
```

### 8.2 部署脚本

```bash
./deploy/deploy.sh full <commit_hash>    # 全量部署（自动备份 + 重启 + 验证）
./deploy/deploy.sh backend <hash>        # 仅后端
./deploy/deploy.sh frontend <hash>       # 仅前端
./deploy/rollback.sh                     # 回滚到最近备份
```

### 8.3 环境变量（backend/app/config.py）

| 变量 | 说明 |
|---|---|
| `DATABASE_URL` | PostgreSQL 连接串 |
| `OPS_JWT_SECRET` | JWT 密钥 |
| `OPS_ADMIN_USER` / `OPS_ADMIN_PASSWORD` | 管理员账号 |
| `LOCAL_HOST` | 对外域名（ops.salmon.xin） |
| `REPORT_ENABLED` / `REPORT_HOUR_UTC` | 巡检日报开关 / 生成时间（默认 0 = 北京 08:00） |
| `AUDIT_ENABLED` | 操作审计开关（默认 true） |
| `DEFAULT_NOTIFY_WEBHOOKS` | 飞书 webhook（日报推送复用） |

### 8.4 测试

```bash
cd backend && pytest    # 52/52 全绿
```

## 九、核心能力与亮点

- **服务自动发现**：Docker SDK 发现容器 + Nginx 配置解析 + Agent 远程发现
- **僵尸服务清理**：容器消失时标记 offline，持续离线自动删除
- **多服务器管理**：本地 + 远程服务器，SSH 连接池管理
- **健康检查**：60s 周期自动检测所有服务可达性
- **免登录访问**：v3.28 起去除 Caddy 全站 basic_auth（运维工作台免密码，内网环境）
- **明暗主题**：支持跟随系统 / 手动切换，8 种配色方案
- **侧边栏折叠**：一键收起，节省屏幕空间

## 十、里程碑进度

| 版本 | 内容 | 状态 |
|---|---|---|
| ≤ v3.17 | 服务导航 / 监控中心 / 资源管理 / Agent 管理 / 分组自由设置 | ✅ |
| v3.24.1 | 免登录访问 | ✅ |
| v3.25 | 告警中心初版 | ✅ |
| v3.26 | 告警与监控增强 | ✅ |
| v3.27 | 告警生态补全 + 多源检测（证书/日志/备份/镜像） | ✅ |
| **v3.28** | **巡检日报 + 操作审计 + 前端 Vite 工程化 + 状态页可用性**（pytest 52/52） | ✅ 已交付 |
| v3.29 | RBAC（admin/operator/viewer）、Prometheus 指标导出（/metrics）、状态页公网版、前端全量 Vite 迁移、日报订阅 | ⬜ 规划中 |
| v3.30 | 多云主机接入（自动发现）、告警升级链路（P1 15min 未确认→升级通知）、SLA 报表、操作回滚（基于审计日志） | ⬜ 规划中 |

## 十一、已知限制与遗留事项

- **registry-proxy 暂不可用**：MFA 出站安全组白名单极严（Docker Hub 不通），proxy 代码保留，环境放开即生效
- **日报推送需配置**：当前无 webhook 配置则推送跳过（配置飞书机器人后即生效）
- **前端全量迁移顺延**：Vite 工程化仅 /v3/ 灰度，全量迁移排入 v3.29
- **docker-compose.yml 已废弃**：实际由 systemd 运行（文件标注 DEPRECATED）
- **运维规范**：本机无 docker；生产配置修改需走部署脚本 + 自动备份

## 十二、参考资料

- 飞书知识库：`agent` → `5_项目/OpsCenter/`（v3.25–v3.28 迭代开发方案与交付文档）
- GitHub：https://github.com/fenda1217/OpsCenter（main = 77ca809，tag v3.28.0）
- GitLab：ssh://git@10.66.66.4:2224/root/opscenter.git（main = 77ca809）
- 本地文档：`docs/OpsCenter功能详细报告.md`（v3.15 详细报告）
