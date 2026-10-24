# OpsCenter 运维工作台

> **当前发布索引：v5.5.20 · 2026-10-24**。源码、构建产物、模块、Agent、部署配置与 Git 历史均在本仓库维护。
> 仓库：[GitHub](https://github.com/salmon-spec/opscenter) · Forgejo `root/opscenter`，两端 main 与发布标签同步。

OpsCenter 是中文自托管运维工作台，提供服务广场与管理、主机台账和监控大屏、系统工具、K3s、告警、日志、OSS、模型用量及电费监测。

## 当前版本与代码导航

| 组件 | 当前版本 | 代码/说明 |
| --- | --- | --- |
| 工作台前后端 | 5.5.20 | [Vue/Vite](frontend-vite/)、[发布说明](docs/releases/v5.5.20.md) |
| OpsAgent | 2.6.4 | [源码](agent/opsagent.py)、[升级说明](agent/RELEASE-2.6.4.md) |
| 电费监测 | 0.10.0 | [模块与通知](modules/electricity/README.md) |
| 服务目录 | 0.7.4 | [模块说明](modules/README.md) |
| 模型用量 | 1.1.0 | [多厂商配置](modules/extension_worker/MODEL_USAGE.md) |
| OpenCode Agent | 0.2.0 | [工作区与原生执行环境](modules/opencode_agent/README.md) |
| 自动化作业 | 0.2.0 | [Semaphore 网关与界面](modules/automation/README.md) |
| 告警中心 | 0.6.0 | [规则、事件与通知](modules/alerts/README.md) |

- 核心后端 `backend/`；独立模块 `modules/`；现行前端 `frontend-vite/`；旧版页面 `frontend/`。
- K3s 部署及版本补丁 `deploy/k8s/`；隔离构建候选流水线 `deploy/ci/Jenkinsfile`、`deploy/ci/build.sh`。
- [代码与历史版本索引](docs/releases/2026-10-20-source-index.md) 说明提交范围、标签、验证及生产覆盖文件的构建边界。
- 样例配置入库；真实密码、API Key、私钥、运行时数据库和部署备份保存在各环境的受保护存储中。
- 2026-10-24 生产采用 K3s 与不可变 ConfigMap 源码/静态文件覆盖；完整运行归档和恢复边界见[本次审查修复记录](docs/reviews/2026-10-24-audit-fixes.md)。源码推送不等于部署。VM100 原 OpsCenter 测试栈已于 2026-10-15 删除，之后的模块隔离试部署见各模块说明。

## 历史架构资料

以下保留早期技术栈、systemd/灰度部署与操作说明。现行版本以以上索引及部署资料为准。

## 三、技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3 + FastAPI 0.115.6 + SQLAlchemy 2.0 + Pydantic 2 + PyJWT |
| 数据库 | PostgreSQL 16（`opscenter` 库） |
| 前端 | Vue 3 SPA（单文件 index.html）+ ECharts + Tailwind CSS；**Vite 5 工程化改造中**（frontend-vite → /v3/ 灰度路径） |
| Agent | Python 轻量采集器（v2.6.3，轻量系统摘要 / 指标 / 服务发现 / WireGuard / PVE 来宾 Web 服务发现 / 进程管理） |
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
│  ├── 检测层：alerts 模块 / log_scanner /                │
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

- 一键部署 / 升级 / 卸载远程监控 Agent（v2.6.3）
- Agent 状态监控（运行 / 离线 / 未部署），自动采集远程主机指标
- 新端点：`/api/v1/registry-proxy`（Docker Hub digest 代理，TLS1.2 + IPv4 强制；MFA 安全组放开后生效）

### 5.10 前端 Vite 工程化（v3.28 起步）

- `frontend-vite/` 目录（Vite 5 + Vue 3），承载资源控制台、日志中心、告警与服务广场
- 构建产物 → `frontend/v3/` 兼容入口，路由级按需加载数据库、容器和系统页面
- 部署脚本 `deploy/frontend-vite.sh`，正式入口切换由统一发布流程完成

### 5.11 扩展模块（v5.1.0）

- 后端注册模块清单与启停状态；前端在 `sandboxed-iframe` 中托管模块，模块只能通过受控消息桥访问白名单只读 API
- OSS 文件浏览：按 Bucket / 前缀只读浏览，支持文本、JSON、图片和音频预览与下载
- 模型用量：读取 Command Code / GOAT 套餐、月度 Credits、5 小时 / 周窗口、账期请求、Token 与成本；API Key 只保存在宿主后端加密配置中，不进入 iframe
- Command Code 当前没有稳定公开用量 API，模块适配官方 CLI 使用的内部 `/alpha/*` 只读接口；接口变更会以采集异常展示，不做零值伪装

## 六、API 一览

| 模块 | 端点 |
|---|---|
| 服务器 | `GET/POST /api/v2/servers` · `GET/PUT/DELETE /api/v2/servers/{id}` · `POST /api/v2/servers/{id}/scan\|test` · `POST /api/v2/test-ssh` |
| 服务 | `GET/POST /api/v2/services` · `PUT/DELETE /api/v2/services/{id}` · `PATCH /{id}/pin` · `GET /api/v2/services/all` · `GET /api/v2/services-with-status` |
| 监控 | `GET /api/v2/monitor/{id}` · `/{id}/history` · `/{id}/network` · `/{id}/network/history` · `/{id}/network/latency` · `GET /api/v2/monitor/{id}/health-check` |
| 告警 | `GET/POST /api/v2/alert-rules` · `PUT/DELETE /{rule_id}` · `GET /api/v2/alert-events` · `POST /{event_id}/ack` · `GET/POST/DELETE /api/v2/alert-silences` |
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
│   │   └── version.py            # 后端版本号（本地源码 5.5.0）
│   ├── tests/                    # pytest（428 用例）
│   └── requirements.txt
├── agent/
│   ├── opsagent.py               # 远程采集 Agent（v2.6.3）
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
git clone git@github.com:salmon-spec/opscenter.git /opt/opscenter

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
| `OPS_AUTH_ENABLED` | 管理端登录开关；新安装默认 `true` |
| `OPS_JWT_SECRET` | JWT 密钥 |
| `OPS_ADMIN_USER` / `OPS_ADMIN_PASSWORD` | 管理员账号 |
| `OPERATOR_TOKEN` | 可选的 API/运维共享令牌；配置后兼容模式也会保护管理接口 |
| `OPS_MONITOR_ALLOW_ANONYMOUS` | 仅测试环境可选；`true` 时数据服务只读监控允许匿名访问，默认 `false` |
| `CREDENTIAL_KEY` | 数据库与服务凭据加密密钥，部署后必须稳定保存 |
| `CORS_ORIGINS` | 可选跨域来源，逗号分隔；默认仅允许同源访问 |
| `LOCAL_HOST` | 对外域名（ops.salmon.xin） |
| `REPORT_ENABLED` / `REPORT_HOUR_UTC` | 巡检日报开关 / 生成时间（默认 0 = 北京 08:00） |
| `AUDIT_ENABLED` | 操作审计开关（默认 true） |
| `DEFAULT_NOTIFY_WEBHOOKS` | 飞书 webhook（日报推送复用） |
| `COMMAND_CODE_API_KEY` | 可选；Command Code API Key，也可在模块配置页加密保存后覆盖 |
| `COMMAND_CODE_API_BASE` | 可选；默认 `https://api.commandcode.ai` |
| `COMMAND_CODE_TIMEOUT_SECONDS` | 可选；Command Code 采集超时，默认 12 秒，范围 3-60 秒 |

### 8.4 测试

```bash
cd backend && pytest    # 当前 428/428 全绿
```

## 九、核心能力与亮点

- **服务自动发现**：Docker SDK 发现容器 + Nginx 配置解析 + Agent 远程发现
- **僵尸服务清理**：容器消失时标记 offline，持续离线自动删除
- **多服务器管理**：本地 + 远程服务器，SSH 连接池管理
- **健康检查**：60s 周期自动检测所有服务可达性
- **内置登录与 API 令牌**：新安装默认启用 JWT 管理员登录，也可配置独立运维令牌
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
| **v4.2** | **资源控制台重构：全局主机切换、数据库/容器/系统分域、按需容器统计、进程管理** | ✅ 已交付 |
| **v4.3** | **文件、防火墙、SSH 管理、回收站、本机终端、服务广场加速与 Agent 恢复** | ✅ 已交付 |
| **v4.4** | **长期监控历史、跨主机指标、集中日志、采集覆盖诊断、Loki 存储与写入健康**（pytest 142/142） | ✅ 发布候选 |
| **v4.4.1** | **服务广场完整详情、可编辑资料、加密账号密码与主动显密**（pytest 145/145） | ✅ 已完成 |
| **v4.5.0** | **服务探活策略、24h 可用率、探测历史、凭证查看审计与数据保留**（pytest 147/147） | ✅ 已完成 |
| **v4.5.1** | **服务健康总览、在线状态筛选、24h 综合可用率与无探测聚合接口**（pytest 148/148） | ✅ 已完成 |
| **v4.6.0** | **服务探测排重、持久化健康状态、故障事件、阈值、恢复通知、维护静默与服务健康页面**（pytest 151/151） | ✅ 已完成 |
| **v4.6.1** | **事件服务/主机/入口信息补全，超时、DNS、TLS、拒绝连接与 HTTP 错误分类，GitLab 标签流水线去重**（pytest 154/154） | ✅ 已完成 |
| **v4.6.2** | **稳定性与安全审计、统一数据库连接池、AI 只读态势接口、敏感 URL/凭证隔离与 API 密钥写节流**（pytest 158/158） | ✅ 已完成 |
| **v4.6.3** | **Jenkins 验收改为校验服务广场响应契约，不再依赖可变的服务数量**（pytest 158/158） | ✅ 已完成 |
| **v4.6.4** | **Jenkins 验收去除 Python 运行时依赖，兼容精简构建 Agent**（pytest 158/158） | ✅ 已完成 |
| **v4.6.5** | **Jenkins 验收改用双 HTTP 状态契约，消除动态数据量、额外运行时与 Groovy 转义依赖**（pytest 158/158） | ✅ 已完成 |
| **v4.6.6** | **修复浏览器版本与 OpenAPI 文档入口；补齐通配/具体 IP 绑定端口发现；Agent v2.5.1 增加 PVE QEMU/LXC 来宾 Web 服务扫描并消除启动阻塞；服务结果每 5 分钟自动同步** | ✅ 已完成 |
| **v4.7.0** | **服务广场支持持久化自定义排序；每个服务支持多组加密登录凭证、独立显示审计；详情页凭证移动到服务说明上方** | ✅ 已完成 |
| **v4.8.0** | **主机删除反馈、多标签终端、WireGuard 内网拓扑、统一健康大屏、容器/数据库导航重组与拓扑手动编排** | ✅ 已完成 |
| **v4.8.1** | **修复多终端标签覆盖导致的切换失效；按 WireGuard 官方 dump 格式恢复接口、Peer、握手与流量拓扑** | ✅ 已完成 |
| **v4.8.2** | **监控历史增量汇总与索引查询优化；服务健康 SQL 聚合；终端并行恢复；页面按路由加载与静态缓存** | ✅ 已完成 |
| **v4.8.3** | **统一后端、前端页签、侧栏与兼容入口版本；手动服务卡片恢复统一布局，删除操作移入详情** | ✅ 已完成 |
| **v4.8.4** | **指标改走轻量 Agent 摘要、失败自动恢复、WireGuard 后台缓存、轮询超时与保留任务修复** | ✅ 已完成 |
| **v4.8.5** | **采集快照复用、Agent 故障时短期降级、主机切换竞态修复、趋势首次加载与超时治理** | ✅ 已完成 |
| **v5.0.0** | **K3s 中间件七件套纳管（Redis/RabbitMQ/Kafka/ZooKeeper/Nacos/MinIO/MongoDB）：数据服务只读浏览与探测；Redis 任务持久化与选主；RabbitMQ 异步任务队列；MongoDB 审计事件双写；Kafka 事件流；Nacos 动态配置；MinIO 报告归档；新端点鉴权加固与终端 WS Origin 校验；ECharts 按需引入（体积 -45%）** | ✅ 已完成 |
| **v5.0.1** | **剪贴板 HTTP 兼容、K3s 只读 RBAC、跨节点 NetworkPolicy、版本与部署验收契约统一** | ✅ 已完成 |
| **v5.0.2** | **监控大屏缓存过期后刷新非数据库数据；服务与主机新鲜度可见；容器模式无 Docker socket 时安静降级** | ✅ 已完成 |
| **v5.0.5** | **AI 只读诊断与自动巡检；统一主机侧栏；终端跨页面保持连接；服务入口收敛为服务广场** | ✅ 已完成 |
| **v5.0.6** | **终端目标主机改为下拉选择；选择主机后自动激活已有会话，无会话时自动创建并打开终端** | ✅ 已完成 |
| **v5.0.7** | **终端目标主机改为多开入口；每次选择主机都新建并打开独立会话，同一主机可连续多开** | ✅ 已完成 |
| **v5.1.0** | **新增扩展模块接口与模块管理启停；外部服务通过 Sandbox iframe 隔离接入；阿里云 OSS 只读文件浏览与模型用量监控模块** | ✅ 已完成 |
| **v5.1.1** | **主机监控趋势图补齐数值单位；CPU/内存/磁盘、网络速率与负载按量纲显示，图例、坐标轴与悬浮提示统一格式化** | ✅ 已完成 |
| **v5.1.2** | **修复 K3s Pod 详情 500；集群概览、网络与入口、存储、任务与事件页面补齐筛选与状态过滤** | ✅ 已完成 |
| **v5.2.1** | **修复数据服务监控认证、指标截断、时间聚合、采样重入与采集失败零值问题；完善 RabbitMQ 队列年龄和告警方向兼容** | ✅ 已完成 |
| **v5.2.2** | **移除前端登录界面；新增显式匿名监控开关，仅测试环境启用，生产默认继续 fail-closed** | ✅ 已完成 |
| **v5.2.3** | **新增数据服务连接配置页；七类中间件凭据加密保存、脱敏回显、连接测试与热生效** | ✅ 已完成 |
| **v5.2.4** | **数据服务连接配置页版式重做：七类中间件全宽单列卡片，卡片头合并状态/来源/保存时间，字段网格按 1/2/3 列响应，操作区固定底部（先本地预览确认后再部署）** | ✅ 已完成 |
| **v5.3.0** | **数据服务同类多实例：主实例保留系统集成用途，新增可并存的额外监控实例；支持连接加密保存、独立测试、启停采集、主实例切换、监控详情与趋势按实例筛选** | ✅ 已完成 |
| **v5.3.1** | **中间件监控界面优化：实例与健康概览、关键/全部/关注指标视图、语义分组与搜索、趋势单位格式化、快速指标切换和响应式诊断卡片** | ✅ VM100 测试环境 |

## 十一、已知限制与遗留事项

- **registry-proxy 暂不可用**：MFA 出站安全组白名单极严（Docker Hub 不通），proxy 代码保留，环境放开即生效
- **日报推送需配置**：当前无 webhook 配置则推送跳过（配置飞书机器人后即生效）
- **前端入口兼容**：Vite 版本继续保留 `/v3/` 入口，正式切换由发布流程统一执行
- **docker-compose.yml 已废弃**：实际由 systemd 运行（文件标注 DEPRECATED）
- **运维规范**：本机无 docker；生产配置修改需走部署脚本 + 自动备份
- **本地 v5.5.0 未发布**：工作台已移除内置数据服务控制台并封锁旧主机写操作；模块化迁移尚未完成，发布前须补齐隔离模块、核对版本链并完成真实入口验收。
- **版本标签补齐**：本轮按 `v5.1.1`、`v5.1.2`、`v5.2.0`、`v5.2.1`、`v5.2.2`、`v5.2.3`、`v5.2.4`、`v5.3.0`、`v5.3.1` 顺序补打并同步 GitHub 与 Forgejo。

## 十二、参考资料

- 飞书知识库：`agent` → `5_项目/OpsCenter/`（v3.25–v3.28 迭代开发方案与交付文档）
- GitHub：https://github.com/salmon-spec/opscenter（v4.4 发布分支与 GitLab 双仓同步）
- GitLab：ssh://git@10.66.66.4:2224/root/opscenter.git（v4.4 发布分支与 GitHub 双仓同步）
- 本地文档：`docs/OpsCenter功能详细报告.md`（v3.15 详细报告）
