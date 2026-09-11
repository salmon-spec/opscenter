# OpsCenter v4.8.5 Stage-0 代码审计报告

- 审计日期：2026-09-10
- 仓库：`C:\Users\dzd\Documents\Codex\2026-08-27\yue\work\opscenter-current`
- 版本：v4.8.5，git `main@baf2af7`（fix: 修复非 root 主机 Agent 状态回退）
- 审计范围：为「工作台页签 / K3s 只读监控 / /hosts 主机管理页 / K3s 优先健康大屏 / Server 模型扩展」五项改造提供现状基线。本文只描述现状，不含需求实现。
- 工作区状态（`git status`）：
  - 未提交改动：`frontend-vite/dist/index.html`（仅空白字符）、`frontend-vite/src/App.vue`（openGroups/isActive/watch 代码块移动）、`frontend-vite/src/router.js`（新增 K3sConsolePreview 路由）
  - 未跟踪：`frontend-vite/src/views/K3sConsolePreview.vue`（原型）、仓库根目录 `nul`（92 字节，来源不明，**本次审计未读取/未触碰**，后续开发严禁读取、删除或重定向使用该文件）
- 以下所有路径均相对仓库根；行号以当前工作区版本为准（App.vue/router.js 与 HEAD 有差异处已标注）。

---

## A. 后端（backend/，Python 3.13 运行时 / Dockerfile 为 3.12-slim）

### A1. Web 框架、入口与路由注册

- 框架：**FastAPI 0.115.6 + uvicorn 0.34.0**（`backend/requirements.txt:1-2`）。不是 Flask。
- 应用实例：`backend/app/main.py:236` `app = FastAPI(title="OpsCenter API", version=VERSION)`。启动方式：`uvicorn app.main:app --host 127.0.0.1 --port 9091`（`deploy/product/opscenter-backend.service:11`；容器内为 0.0.0.0，`backend/Dockerfile:8`）。
- 路由组织：**无集中式 APIRouter 汇总文件**。两种模式并存：
  1. 大量端点直接用 `@app.get/post/...("/api/v2/...")` 装饰器写在 main.py（全文 4401 行）；
  2. 功能模块各自定义 `router = APIRouter(prefix="/api/v2", tags=[...])`，由 main.py 统一 include。
- 注册位置：`backend/app/main.py:279-292`，现有注册代码样例（原文，280-282 行）：
  ```python
  app.include_router(api_keys_router)
  app.include_router(topology_router)
  app.include_router(control_router)
  ```
  完整注册列表（280-292 行）：api_keys、topology、control、plaza、system_control、databases、file_control、firewall_control、ssh_control、metrics_history、log_center、alloy_manager、ai_context（各模块 import 位于 `main.py:27-47`）。
- ⚠️ **遗留死代码**：`backend/app/routers/{servers,services,monitor,terminal}.py` 定义了旧版 router（如 `routers/servers.py:13` `router = APIRouter(tags=["servers"])`，无 `/api/v2` 前缀），但 main.py **从未 import/include 它们**（仅在 `routers/__init__.py:2-3` 被引用，而 `routers/__init__` 本身未被 import）。真实的 servers CRUD / monitor / terminal 实现都在 main.py 与 `control.py` 等模块中。新 K3s/hosts 路由不要复用该目录的旧文件，建议新建模块后加入 main.py:280-292 的 include 列表。
- 中间件：CORS `allow_origins=["*"]`（`main.py:267-273`）、AuditMiddleware（`main.py:276`，`app/audit.py`）、PerformanceMiddleware（`main.py:277`，`app/performance.py`）。
- 生命周期钩子：`@app.on_event("startup")`（`main.py:1190-1305`）——等待 DB → `Base.metadata.create_all` → `_ensure_new_columns()` → 启动 ~15 个 asyncio 后台任务（见 A4/A7）。

### A2. 数据库

- 引擎：**SQLAlchemy 2.0.36** + **psycopg[binary] 3.2.3**（PostgreSQL 3 驱动）；默认 DSN `postgresql+psycopg://opscenter:opscenter123@127.0.0.1:5433/opscenter`（`app/config.py:30`，pydantic-settings 字段 `database_url`，env 变量名 **`DATABASE_URL`**）。SQLite 兼容分支用于测试（`app/database.py:13-14`）。
- 引擎/会话：`app/database.py:9-27`（QueuePool，pool_size=5/max_overflow=10/pool_pre_ping/pool_recycle=300）；`get_db()` 上下文管理器（18-22 行）+ `get_session()` FastAPI Depends 版（24-27 行）。
- Models：单文件 `backend/app/models.py`（542 行），`declarative_base`（第 7 行）；UUID 主键 `Uuid(as_uuid=True)`，JSONB 用 `JSON().with_variant(PostgreSQLJSONB(), "postgresql")`（第 9 行）。
- **建表/迁移机制（无 Alembic、无 schema.sql）**：
  1. 空库：`Base.metadata.create_all(bind=engine)`（`main.py:1196`，`app/database.py:29-31`）；
  2. 已有库加列：**手写 SQL 轻量迁移** `_ensure_new_columns()`（`main.py:1106-1138`），样例（1109 行）：
     ```python
     "ALTER TABLE services ADD COLUMN IF NOT EXISTS deploy_type VARCHAR(20)",
     "ALTER TABLE servers ADD COLUMN IF NOT EXISTS log_agent_status VARCHAR(20) DEFAULT 'unknown'",
     ```
     失败仅打印日志不阻断启动（1136-1138 行）；启动重试 30 次×2s（1193-1201 行）。
  3. 大表在线索引：`app/metrics_history.py:23-39`（`CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_metric_history_latest ...`，startup 后 1s 执行）+ 手动脚本 `backend/scripts/create_performance_indexes.py`。
  4. 数据迁移函数示例：`_migrate_legacy_service_credentials()`（`main.py:1141-1187`）。
- servers 表列清单（`app/models.py:28-58`）：`id, name, host, ssh_port, ssh_user, ssh_key(Text，明文密码以 `__password__` 前缀存此列), tags(JSONB), status, docker_available, is_local, last_seen, created_at, updated_at, enabled, last_check_at, last_online_at, fail_count, last_error, remark, auth_type, agent_status, agent_port(默认19100), agent_token, agent_version, agent_type('remote'/'local'), log_agent_status, log_agent_version, log_agent_error, log_agent_checked_at`。**当前没有 lan_ip / wireguard_ip / 管理通道字段，也没有 Cluster 实体**。
- PG 连接配置的其他入口：`docker-compose.yml:16`（已废弃）与 `deploy/docker/compose.yml:60`（容器内 `DATABASE_URL=...@db:5432/...`）、`.gitlab-ci.yml:20`（测试库 `opscenter_test`）。

### A3. servers API 与鉴权

- 真实实现位置（均在 `backend/app/main.py`）：
  - `GET /api/v2/servers`（1309-1341 行，函数 `list_servers`，返回含 agent/log_agent 状态、service_count、has_credentials）
  - `POST /api/v2/servers`（1415-1438，`create_server`；host+ssh_port 查重 409；密码存 `__password__` 前缀；`auto_deploy_agent` 时 201 响应后用 `BackgroundTasks` 后台部署）
  - `GET/PUT/DELETE /api/v2/servers/{id}`（1440-1453 / 1455-1484 / 1486-1537；PUT 保护本机字段、DELETE 400 本机不可删、不再隐式卸载 Agent——1498-1500 注释、422 非法 UUID）
  - `POST /api/v2/servers/{id}/scan`（1540+）、`/test`（1605+）、`/scan-services`（2413+）、`/ssh-test`（2909）
- Agent 部署/升级：
  - `POST /api/v2/servers/{id}/deploy-agent`（3362-3422，**同步**执行 `agent_manager.deploy_agent`，成功后自动首扫）
  - `POST /api/v2/servers/{id}/upgrade-agent`（3436-3451，**202 异步**：置 `agent_status='deploying'` 后 `background_tasks.add_task(_deploy_agent_background, server_id)`）
  - `POST /api/v2/agents/upgrade-outdated`（3454-3470，批量 202）
  - `GET /api/v2/agents/version`（3425-3433，返回 current_version + outdated_server_ids）
  - `DELETE /api/v2/servers/{id}/agent`（3516-3536，显式卸载）
  - 后台执行体：`_deploy_agent_background()`（1343-1379，更新 agent_status/port/token/version 到 DB）与 `_upgrade_outdated_agents_once()`（1396-1412，startup 线程任务）。
  - **任务模型现状**：没有任务 ID 表/任务状态查询接口；状态仅存在 `servers.agent_status`（deploying/running/error）+ `servers.last_error`，前端靠轮询 `GET /servers` 与 `agent-status`（3473-3513）观察。
- 鉴权（`app/auth.py`）：
  - JWT（PyJWT HS256，24h）+ bcrypt，`get_current_user` Depends（`auth.py:72-118`）。
  - **默认免登录**：env `OPS_AUTH_ENABLED=false`（`auth.py:27`，config `auth_enabled`，`config.py:46`）时返回虚拟 admin。
  - 有鉴权（Depends(get_current_user)）的端点：`control.py` 全部（containers/docker/power/processes，行 388-845 各处 `dependencies=[Depends(get_current_user)]`）、`system_control.py:123+`、`file_control.py`、`firewall_control.py`、`ssh_control.py`、`databases.py`、`api_keys.py`。
  - **无鉴权**：main.py 中的 `/api/v2/servers*` CRUD、monitor、alert-*、cert/log/backup、reports、audit-logs、topology、plaza 等全部端点（依赖默认免登录放行）；`/screen/summary` 用 `require_api_key("read")`（`topology.py:739-741`）——**不带 Bearer 时放行**（`api_keys.py:79-104`），带则校验 ApiKey（read/write scope）。
- WebSocket：`@app.websocket("/ws/terminal/{session_id}")`（`main.py:4206+`），会话管理在 `app/ssh_terminal.py`（`RECONNECT_GRACE` 在 `main.py:19` 导入；断线 5 分钟内可重连，见前端 B11）。

### A4. 主机监控数据流

- 摘要端点：
  - `GET /api/v2/servers/{id}/monitor`（别名 `/api/v2/monitor/{id}`，`main.py:3086-3088`）：Agent HTTP 优先（`fetch_agent_metrics`），失败回退 SSH `collect_remote_metrics`（`app/ssh_manager.py`），无 Agent 返回空 + error。
  - `GET /api/v2/servers/{id}/system/summary`（`app/system_control.py:123`）：走 5s TTL 进程内缓存 `_SUMMARY_CACHE`（`system_control.py:23-25`；写入口 `record_system_summary` 99-102，失效 `forget_system_summary` 105-106）；采集函数 `fetch_agent_system_summary`（`app/agent_manager.py:329`，打 Agent `/api/v1/system/summary`）。
  - `POST /api/v2/monitor/{id}/health-check`（`main.py:2854-2898`）：逐服务 HTTP 探活，SSRF 白名单 `_service_probe_allowed`（2837-2851）。
- 历史指标：
  - `GET /api/v2/servers/{id}/history`（别名 `/api/v2/monitor/{id}/history`，`main.py:3295-3297`）与 `agent-history`（3747-3773），均查 `metric_history` 表并 `_downsample_history`（3253-3292）。
  - 时间范围查询：`GET /api/v2/servers/{id}/metrics/timeseries`（`app/metrics_history.py:224`）、`GET /api/v2/metrics/hosts/overview`（265），自动下钻 5m/1h 聚合表 `metric_rollups`。
- `metric_history` 表（`app/models.py:322-337`）：`id, server_id(FK CASCADE), timestamp(index), metric(String(20)), value(Float)` + 降序组合索引 `ix_metric_history_latest`（332-337）。相关表：`metric_rollups`（340-355）、`network_stats`（359-375）、`network_latency`（378-387）。
- **采集循环**：`_collect_agent_metrics()`（`main.py:3541-3638`，ThreadPoolExecutor≤8 并发拉取全部主机 → 每主机短事务写 MetricHistory + 更新 server 状态）由 `background_agent_collector()`（3641-3647，**每 30s**）驱动；另有 60s 主机连通性检查（328-332）、5 分钟 Agent 状态对账 `_agent_health_check_loop`（395-403 + 335-392）、5m/1h 聚合 `metric_rollup_loop`（`metrics_history.py:174`）、每日网络归集 `daily_network_aggregation`（`main.py:1284`）。全部为 `asyncio.create_task` + `asyncio.to_thread`，**不使用独立线程进程，无 task id**。

### A5. 健康大屏 /api/v2/screen/summary

- 实现：`backend/app/topology.py:739-1050`（`get_screen_summary`，router prefix `/api/v2`，`topology.py:35`）。
- 聚合内容：主机最新 CPU/MEM/DISK（LATERAL SQL 直查 metric_history，761-774）、容器汇总（复用 `_LAST_AGENT_SNAPSHOT` 120s 内快照，807-816）、数据库实例状态（818-827）、服务健康（复用 plaza 持久化状态 PlazaHealthState，829-869）、日志采集器概览（复用 `alloy_manager.alloy_overview(probe=False)`，871-884）、告警计数与 Top10（886-909）、WireGuard 拓扑摘要（`_cached_wireguard_snapshot`，727-736，30s TTL 后台线程刷新 714-724）。
- 缓存机制：**响应级无缓存**（每次请求实时查库），但各子数据自带进程内缓存（Agent 快照 120s、WG 拓扑 30s）；慢子模块失败降级进 `partial_errors`；返回 `freshness.{metrics_at,services_at,wireguard_at}`。
- 前端消费：见 B8。

### A6. Docker 容器 API

- 位置：`backend/app/control.py`（router prefix `/api/v2`，第 28 行）。
- 按主机路由：URL 一律 `/servers/{server_id}/...`：
  - `GET /servers/{id}/containers`（529，含 `include_stats/refresh/status/search/page_size` 参数；进程内缓存 `_CONTAINER_CACHE` 37-39、543-591）
  - `GET .../containers/{cid}/inspect`（604）、`/logs`（644）、`POST .../containers/actions`（673，start/stop/restart 等）、`POST .../containers/prune`（725）
  - `GET /servers/{id}/docker/{resource}`（758，images/networks/volumes）、`/delete`（788）、`/prune`（845）
- 本机/容器化差异：本机走本地 docker socket（`docker==7.2.0` SDK），容器化时本机 Docker 经 SSH 到宿主机（`control.py` + 测试 `test_docker_bundle.py:48-90`）。

### A7. 服务广场探活（plaza_health_loop）

- 位置：`backend/app/plaza.py`（router prefix `/api/v2`，36 行）。
- 探活循环：`plaza_health_loop()`（696-703）：启动延迟 20s，**每 15s** 一轮 `run_plaza_health_cycle`（679-693）→ `_refresh_health_checks`（后台线程 + `_cached_checks` 进程内缓存 TTL 30s，39 行、649-676）→ `_probe`（421-450，`urllib` UrlRequest，支持按 profile 的超时/成功码段/TLS 校验/阈值）→ `_persist_probe_results`（511+）写 `plaza_probe_results`、稳定状态 `plaza_health_states`、事件 `plaza_health_incidents`、静默 `plaza_health_silences`；钉飞书通知 `_send_incident_notification`（476-508）。
- SSRF/URL 白名单：**plaza 本身无白名单**（URL 来自受管目录与 manual 条目，属可信输入）；对“主机服务探活”的白名单在 `main.py:2837-2851 _service_probe_allowed`（仅允许目标服务器登记 host/host_ip/host_domain；本机加 127.0.0.1/localhost/LOCAL_HOST），越界标记 `blocked`（2883-2885）。若 K3s 探活要复用该模式，参考此处。

### A8. 后端配置与版本

- `backend/app/config.py`（189 行，pydantic-settings `BaseSettings`，字段名=环境变量名）：
  - DB：`DATABASE_URL`（30 行默认值）
  - 身份/Agent：`LOCAL_HOST/LOCAL_DOMAIN/LOCAL_SERVER_NAME/LOCAL_AGENT_TOKEN/LOCAL_AGENT_HOST`（33-38；容器内 LOCAL_AGENT_HOST 默认 127.0.0.1，compose 覆盖为 host.docker.internal）
  - Auth：`OPS_JWT_SECRET/OPS_ADMIN_USER/OPS_ADMIN_PASSWORD/CREDENTIAL_KEY/OPS_AUTH_ENABLED/PREVIEW_MODE/CONTAINERIZED`（41-48）
  - 开关类：`ALERTING_ENABLED/SILENCE_ENABLED/CERT_SCAN_*/LOG_SCAN_ENABLED/BACKUP_CHECK_ENABLED/IMAGE_CHECK_ENABLED/REPORT_*/AUDIT_ENABLED/DEFAULT_NOTIFY_WEBHOOKS`（51-61）
  - 保留期/Loki：`RETENTION_*/LOKI_*/ALLOY_VERSION`（64-74）
  - 模块级兼容常量导出（80-126 行）。
- 版本号：`backend/app/version.py:2` `VERSION = "4.8.5"`（单一真源；`main.py:15`、`config.py:129` 引用）。
- 服务分组配置为文件存储 `groups.json`（`GROUPS_JSON_PATH` env，`main.py:1515` 默认 `/opt/opscenter/frontend/groups.json`）。

### A9. 测试

- 目录：`backend/tests/`（33 个 test_*.py + `conftest.py`）。运行：`cd backend && python -m pytest tests/ -v --cov=app --cov-report=term-missing`（`.gitlab-ci.yml:32`）；本地需可连 PG（conftest 默认 `DATABASE_URL=postgresql+psycopg://opscenter:opscenter123@127.0.0.1:5433/opscenter_test`，`tests/conftest.py:12-15`；`OPS_AUTH_ENABLED=false`、`LOCAL_HOST=127.0.0.1`）。
- 复位方式：多数测试文件自带 `@pytest.fixture(autouse=True)` 每 case `Base.metadata.drop_all+create_all`（如 `test_smoke.py:22-27`）；统一 `client = TestClient(app)`（模块级导入 `app.main.app`，注意 import 副作用会触发 config 读取）。
- Mock 风格：**pytest monkeypatch 为主**（约 200+ 处），打桩目标集中在：`monkeypatch.setattr(main, "get_db", fake_db)`、`_deploy_agent_background`、`agent_manager._get_ssh_client/_ssh_exec`、`log_center.requests.get`、`plaza._probe`、模块级常量（如 `alloy_manager.LOKI_URL`）。示例：`test_connectivity_speed.py:57-70`（upgrade-agent 异步）、`test_agent_health_recovery.py:59+`、`test_v48_screen_summary.py`（大屏）、`test_v48_wireguard.py`。
- 覆盖模块：smoke、servers CRUD/删除、agent 健康/采集/升级、metrics_history 聚合、screen/summary、topology（含 wireguard/编辑）、plaza（探活/事件/凭证/静默）、alloy/log_center、file/firewall/ssh control、terminal（v48）、报告/告警/静默、连通性速度。

### A10. 依赖清单（backend/requirements.txt，17 行）

```
fastapi==0.115.6, uvicorn[standard]==0.34.0, sqlalchemy==2.0.36,
psycopg[binary]==3.2.3, docker==7.2.0, asyncssh==2.17.0,
pydantic==2.10.4, pydantic-settings==2.7.1, aiofiles==24.1.0,
requests==2.32.3, paramiko==5.0.0, python-multipart==0.0.32,
httpx==0.28.1, PyJWT==2.10.1, cryptography==43.0.3, PyMySQL==1.1.1, redis==5.2.1
```
- **确认：当前没有任何 Kubernetes 客户端库**（无 kubernetes / pykube-ng / httpx-based k8s 封装）。K3s 只读监控需要新增依赖（如 `kubernetes` 或用现有 httpx 自封装），并同步 `requirements.txt` + `backend/Dockerfile`（pip 层）+ CI。
- Agent 源码：`agent/opsagent.py`、`agent/scanner.py`（部署包，由 `app/agent_manager.py` 经 SSH 上传 systemd 服务）。

---

## B. 前端（frontend-vite/）

### B1. package.json（`frontend-vite/package.json`）

- `name: opscenter-frontend`，`version: "4.8.5"`（第 3 行，**前端版本号定义位置**；`main.js:6-8` 读取并写 `document.title`；App.vue:55,58 侧栏展示）。
- scripts：`dev: vite`、`build: vite build`、`preview: vite preview`（6-10 行）。
- dependencies（11-17）：`vue ^3.4.0`、`vue-router ^4.4.5`、`echarts ^5.5.1`、`@xterm/xterm ^5.5.0`、`@xterm/addon-fit ^0.10.0`。devDependencies：`vite ^5.4.0`、`@vitejs/plugin-vue ^5.0.0`。
- **无 pinia、无 vue query、无 UI 组件库**（自绘组件 + 全局 CSS）；包管理 pnpm（pnpm-lock.yaml/pnpm-workspace.yaml，构建脚本用 npm 亦可）。

### B2. router.js（`frontend-vite/src/router.js`，46 行）

- 模式：`createWebHashHistory()`（44 行）。懒加载：除首页外全部 `const X = () => import('./views/X.vue')`（5-19 行；`ServicePlaza` 为静态 import）。
- 完整路由表（21-41 行，均无 `name` 字段，仅 path/component/meta.title[/meta.standalone]）：

| path | component | meta |
|---|---|---|
| `/` | ServicePlaza（静态） | title 服务广场 |
| `/assets` | redirect → `/system/monitor` | — |
| `/container` | Assets | title 容器 |
| `/database` | Database | title 数据库 |
| `/logs` | LogCenter | title 日志中心 |
| `/service-health` | ServiceHealth | title 服务健康 |
| `/system` | redirect → `/system/monitor` | — |
| `/system/monitor` | SystemMonitor | title 系统 · 监控 |
| `/system/files` | SystemFiles | title 系统 · 文件 |
| `/system/firewall` | SystemFirewall | title 系统 · 防火墙 |
| `/system/ssh` | SystemSSH | title 系统 · SSH 管理 |
| `/system/terminal` | SystemTerminal | title 系统 · 终端 |
| `/system/processes` | SystemProcesses | title 系统 · 进程管理 |
| `/screen` | Screen | title 监控大屏 |
| `/screen-standalone` | Screen | title 监控大屏， standalone:true |
| `/topology` | Topology | title 拓扑架构 |
| `/alerts` | Alerts | title 告警中心 |
| `/api-keys` | ApiKeys | title 开放 API |
| `/preview/k3s-console` | K3sConsolePreview（**未跟踪新文件**） | title K3s 控制台预览， standalone:true（40 行，工作区新增） |

### B3. App.vue（`frontend-vite/src/App.vue`，183 行，**工作区有未提交改动**）

- 布局：`.shell` = 侧栏 `aside.sidebar`（4-27 行，standalone 页隐藏）+ `.main`（顶栏 `header.topbar` 30-36 + `.content` 内 `<router-view />` 38）+ 全局 toast（43-45）+ 全局 `HostManagerDrawer`（46 行，`hostDrawer` ref 61）。
- **navs 数组**：`const navs = [...]`（**62-82 行**）：两个分组（`key:'plaza'` 子项 服务列表/服务健康；`key:'system'` 子项 监控/容器/数据库/文件/终端/防火墙/SSH/进程管理）+ 6 个顶级项（/screen 健康大屏、/topology、/alerts、/api-keys、/logs）。
- **近期“navs 初始化顺序修复”（未提交 diff 的实质）**：HEAD 上 `openGroups/isActive/groupActive/toggleGroup/watch(route.path, immediate:true)` 位于 `const navs` **之前**（immediate watch 立即执行时引用 navs），工作区把该代码块**整体移到 navs 定义之后**（现位于 83-92 行：`openGroups` 84、`isActive` 85、`groupActive` 86、`toggleGroup` 87、`watch` 88-92），消除引用未初始化常量的问题。无逻辑变化，仅顺序调整。
- 主机选择器位置：顶栏 `.global-host`（33 行）：`<select :value="selectedHostId" @change="selectHost(...)">` + “管理主机”按钮打开抽屉；数据来自 `useHostContext()`（60 行）。
- 顶栏时钟 + 主机在线概览（95-113 行）；toast 走 window 事件 `ops-toast`（116-128，`api.js:64-66` 派发）。

### B4. hostContext.js（`frontend-vite/src/hostContext.js`，68 行）

导出的 API（66-68 行 `useHostContext()`）：
- `hosts: Ref<Server[]>`——全局主机列表（模块级单例 18 行；localStorage 缓存 `ops-host-cache-v1`，10 分钟有效期，4-16 行）
- `selectedHostId: Ref<string>`——全局当前主机（localStorage `ops-global-host`，19 行）
- `currentHost: ComputedRef`——当前主机对象（25 行）
- `loading: Ref<boolean>`
- `refreshHosts(force=false)`——去重并发（`refreshPromise`）、AbortController 抢占（27-58；`api.get('/servers')`，选中主机失效时回退：原选中→首台 online→首台）
- `selectHost(id)`——切换并持久化（60-64）
- 无 emits/事件；页面通过 `watch(selectedHostId)` 自行刷新（各 System* 视图均如此）。

### B5. HostManagerDrawer.vue（`frontend-vite/src/components/HostManagerDrawer.vue`，90 行）

- Props：`visible: Boolean`（47 行）；Emits：`close`（48 行）。
- 功能块（行号）：
  - 主机列表 + 行内操作（升级/采集部署/检查采集/编辑/删除）：模板 7-14
  - 主机表单（name/host/ssh_port/ssh_user/remark/tags/auth_type(password|key)/ssh_password/ssh_key/auto_deploy_agent）：模板 15-28，`form` reactive 51 行，`beginAdd/beginEdit/reset` 61-63
  - SSH 测试：`testConnection` 66（`POST /test-ssh`，响应 `success/message`）
  - 保存：`save` 67（`POST /servers` 或 `PUT /servers/{id}`，payload 组装含 `auto_deploy_agent/is_local:false`）
  - 删除（二次确认输入主机名，Modal）：68-70 + 模板 30-36（`DELETE /servers/{id}`，删除后回退选中本机/首台）
  - Agent 版本/Loki 配置加载：`loadAgentVersion` 71（`GET /agents/version`、`GET /logs/agents/version`）
  - Agent 升级：单台 72（`POST /servers/{id}/upgrade-agent`）、批量 73（`POST /agents/upgrade-outdated`）；版本比较工具 53-55
  - 日志采集器（Alloy）：部署 74、检查 75、批量部署 76（`/servers/{id}/logs/agent/{deploy|check}`、`POST /logs/agents/deploy-missing`）
  - 轮询逻辑：`hasBackgroundWork` 77（agent_status==='deploying' 或 alloy deploying/checking）、`startPolling` 78（**2.5s 间隔**，仅抽屉可见且有后台任务时 `refreshHosts(true)`）、watch 79、清理 80
- 抽取评估：**难度中等**。单文件 ~90 行但行密度极高（模板单行长），状态全部内部；依赖 hostContext 单例（天然共享，无需注入）；无 router 依赖；样式 scoped 自包含（83-90）。可作为 /hosts 页与主机详情侧栏的底座直接复用：建议新增 props（如 `embedded`/`initialHostId`）与 emits（`select`、`deleted`）即可，风险点在于表单/删除/轮询三块逻辑与全局 `refreshHosts` 的耦合都在同一 script 块，需要整体搬移而非裁剪。

### B6. 主机选择器组件

- **没有独立组件文件**：就是 `App.vue:33` 顶栏里的原生 `<select>`（`.global-host`），直接调用 `useHostContext().selectHost`。各页面通过 `hostContext.selectedHostId` 间接联动（如 Assets.vue:130 使用 `selectedHostId`，Database.vue:59 `watch(selectedHostId)`）。做“条件式主机/集群选择器”需先把它组件化（当前无 HostSelector.vue）。

### B7. API 客户端（`frontend-vite/src/api.js`，94 行；无 src/api/ 目录，单文件）

- 基座：原生 **fetch**（非 axios），`API_BASE = import.meta.env.VITE_API_BASE || ''`（3 行），统一前缀 `/api/v2`（6 行）。
- 封装：`request(path, {method,body,query,signal,timeoutMs})`（5-47）：query 序列化 7-14；**双 AbortController**——外部 signal 与超时 signal 链接（15-20），超时转 `TimeoutError`（37-41）；非 2xx 抛 `Error(detail||HTTP xxx)` 并挂 `err.status/err.data`（29-34）。
- 导出：`api.get/post/put/patch/del`（49-55）、`wsUrl(path)`（58-61，ws/wss 同源）、`toast(msg,type)`（64-66）、格式化 `fmtBytes/fmtDuration/fmtTime`（69-94）。
- AbortController 用法惯例：页面级 `let controller`，每次请求前 `controller?.abort(); controller = new AbortController()`，竞态用 `if (hostId !== selectedHostId.value) return` 双保险（例：Assets.vue:130、LogCenter.vue:53、Screen.vue:205-227）。

### B8. 健康大屏页面（`frontend-vite/src/views/Screen.vue`，407 行）

- 请求：仅 1 个核心聚合 `GET /screen/summary`（213 行）+ 趋势 `GET /servers/{id}/metrics/timeseries`（261，Top3/单主机×1，最多 3 并发 Promise.allSettled 258-277）。
- 刷新逻辑：核心 **10s setInterval**（326 行 `coreTimer`，`loadSummary` 内有 `requestInFlight` 防重入 + 页面 hidden 跳过 205-208）；趋势 **60s**（327 行 `trendTimer`，`queueTrend` 15s 节流 229-236）；`visibilitychange` 恢复立即拉取（314-321）；AbortController 取消旧请求（209-210、241-243）；失败保留旧数据并把错误追加进 `partial_errors`（219-223）。ECharts 趋势图 `initChart` 291-303；全屏/standalone 305-312。

### B9. Docker 容器页面（`frontend-vite/src/views/Assets.vue`，路由 `/container`）

- 主机切换：顶栏全局选择器（hostContext）+ `watch(selectedHostId)`；`loadContainers(includeStats, refresh)`（130 行）：AbortController、**sessionStorage 缓存** `ops-containers-v2-{hostId}-{filter|search}`（无网络时先渲染缓存并标注 “会话缓存”）、`api.get('/servers/{id}/containers',{status,search,page_size:200,include_stats,refresh})`，响应含 `data_timestamp/duration_ms/source/cached/cache_age_seconds`（对应 control.py 容器缓存）。
- 其余：Docker 资源页签 images/networks/volumes `loadDockerResources`（131）、容器日志 `openContainerLogs`（145）、inspect（146）、批量操作与 prune（模板同文件）；onMounted 149：`refreshHosts(); loadContainers(false,false)`；onUnmounted 150 abort。

### B10. CSS 变量体系（`frontend-vite/src/styles.css:2-21`，`:root` 单层扁平命名，kebab-case）

示例：`--bg:#f4f6fa`、`--sidebar:#0f1b2d`、`--sidebar-text/--sidebar-active`、`--brand:#2563eb`、`--card`、`--text/--muted/--border`、`--ok/--warn/--err`、`--screen-bg/--screen-panel/--screen-border/--screen-text/--screen-muted`（大屏深色系）、`--radius`。注意 K3sConsolePreview 原型使用自有硬编码色板（未接入这些变量）。通用组件类：`.btn/.btn-primary/.btn-danger/.btn-sm/.btn-ghost`（33-47）、`.input/.select/.field`（50-56）、`.card`（59-62）、`.tag-*`（67-74）、`.table`（77-80）。

### B11. 终端页面（WebSocket）

- 页面：`frontend-vite/src/views/SystemTerminal.vue`（148 行）：多标签（`sessions` 30 行）、`createTerminal` 47-71（`POST /terminal/sessions {server_id}` → session_id）、标签持久化 sessionStorage `ops-terminal-tabs`（42-45，只存 id/元数据）、`restore()` 106-123（逐个 `GET /terminal/sessions/{id}/status`，`reconnectable` 才恢复，否则丢弃）。
- 面板：`components/TerminalPanel.vue`：props 38-44（sessionId/title/allowFiles/embedded/active）；`connect()` 72-107（xterm + FitAddon + ResizeObserver，`ws = new WebSocket(wsUrl('/ws/terminal/'+sessionId))` 91）；关闭码 4004=会话过期不可重连，否则可手动 `reconnect()` 109-112；心跳 25s ping（122）；onUnmounted 关 ws + dispose（124-129）。服务端生命周期：会话 5 分钟重连宽限（`main.py:19` RECONNECT_GRACE；`/ws/terminal` 4206-4248）。

### B12. K3sConsolePreview.vue（未跟踪原型，98 行，仅结构分析，不引用其模拟数据）

- 定位：`meta.standalone:true` 的整页原型（自带假侧栏+顶栏，不与真实 App.vue 布局嵌套）。
- 模拟的交互：
  1. **工作台页签**：`tabs` 数组 + `activeTab` + 新建（＋）/关闭（×），切换用 `v-show` 保持组件实例（12-18、20-53 行；`openPage/closeTab` 90-91 行）——对应 KeepAlive 页签需求的状态模型雏形（tab id 即唯一键 `tab.id=item.key`）。
  2. **K3s 集概览页**（tab.kind==='k3s'）：页头**条件式集群选择器**（`select v-model="cluster"`，23 行）、指标卡、节点水位（点击节点打开侧栏 32 行）、异常工作负载表、**服务双层健康表（内部/外部两列）** 35 行、备份与任务、节点表格（39 行）。
  3. **主机管理页**（tab.kind==='hosts'）：主机表格列含 `管理地址=LAN + WG 双行`（45 行），对应 lan_ip/wireguard_ip 展示。
  4. **主机详情侧栏**：`selectedHost` 抽屉（56-65 行），内部再分 4 个侧栏页签：资源趋势（含时间范围切换）、服务情况（内部/外部健康）、连接诊断（LAN 首选/WG 备用与耗时）、Agent 卡（检查更新）——即“复用 HostManagerDrawer 能力 + 详情侧栏”的目标形态。
  5. 顶栏工具区/通知占位（17 行）、`needsHost` 页面显示主机选择器而全局页不显示（49-50 行）——即“条件式选择器”的展示规则原型。
- 全部数据为组件内常量（82-87 行），无 API 调用；样式为独立硬编码（96-98 行）。

---

## C. 部署与版本

### C1. docker-compose.yml（仓库根）

- **已废弃**（1-5 行注释：实际由 systemd 运行）：单服务 `opscenter-backend`，`build: ./backend`，端口 `127.0.0.1:9091:9091`，env 注入 `DATABASE_URL/OPS_JWT_SECRET/OPS_ADMIN_*/CREDENTIAL_KEY`，卷挂载 `/opt/opscenter/frontend:/app/frontend:ro`、`/opt/opscenter/agent:/app/agent:ro`、groups.json/services.json、`/root/.ssh`，`extra_hosts: host.docker.internal:host-gateway`。
- 真正使用的编排：`deploy/docker/compose.yml`（98 行）：`db`（postgres:16-alpine，数据卷 `/opt/opscenter-data/postgres`，AppArmor unconfined）、`loki`（grafana/loki:3.7.2，`127.0.0.1:3100`）、`backend`（`build context=../.. dockerfile=deploy/docker/backend.Dockerfile`，镜像 `opscenter/backend:4.8.5`，env_file `/etc/opscenter/secrets.env`，`CONTAINERIZED=true`，`DATABASE_URL=...@db:5432`，`LOCAL_AGENT_HOST=host.docker.internal`，`LOKI_URL=http://loki:3100`，`GROUPS_JSON_PATH=/opt/opscenter/frontend/groups.json`，端口 9091，healthcheck 打 `/openapi.json`）、`web`（`deploy/docker/frontend.Dockerfile`，镜像 `opscenter/frontend:4.8.5`，80 端口）。
- 产品（systemd）形态：`deploy/product/opscenter-backend.service`（venv + uvicorn 127.0.0.1:9091，EnvironmentFile=/etc/opscenter/secrets.env）、`deploy/product/Caddyfile`（`/api/*`、`/ws/*` 反代 9091；静态根 `/opt/opscenter/frontend/v3`，assets 一年缓存、index no-cache）、`deploy/product/postgres.compose.yml`、`secrets.env.example`、备份/恢复/迁移冒烟脚本。
- 观测栈：`deploy/observability/`（Loki + Alloy 配置/安装脚本）。

### C2. deploy/ 目录与 CI

- 目录清单见 `deploy/`：`deploy.sh`（rsync/tar 备份 + pip install + systemctl restart，21-56 行）、`frontend-vite.sh`（npm install → `npm run build` → `cp -r dist/* ../frontend/v3/`，灰度路径 /v3/）、`rollback.sh`、`docker/`（compose + 前后端 Dockerfile + agent 安装）、`product/`（systemd 生产形态）、`k8s/opscenter-web-nodeport.yaml`（OpsCenter 自身 web 的 NodePort:30088 Service，namespace opscenter——**仓库内唯一 K8s 清单，且只有 Service，无 Deployment**）、`vm3/`、`observability/`。
- `.gitlab-ci.yml`（39 行）：单 stage `test`；触发 MR/main；image `python:3.10-slim` + services `postgres:16-alpine`；变量 `DATABASE_URL=...@postgres:5432/opscenter_test`；脚本 `cd backend && python -m pytest tests/ -v --cov=app --cov-report=term-missing`。**无前端构建/镜像发布 stage**（另见 Jenkinsfile，255 字节级历史文件）。

### C3. 版本号 4.8.5 定义位置

- 后端：`backend/app/version.py:2`（唯一真源）。
- 前端：`frontend-vite/package.json:3`（App.vue 侧栏与 document.title 均读此文件）。
- 镜像 tag：`deploy/docker/compose.yml:50`（backend:4.8.5）、`:86`（frontend:4.8.5）。

### C4. 前端构建产物

- 构建：`npm run build` = `vite build`（package.json:8），输出 `frontend-vite/dist/`。
- `frontend-vite/vite.config.js`：`base: '/v3/'`（灰度路径，Caddy 静态根即 `/opt/opscenter/frontend/v3`）；`outDir:'dist'`、`assetsDir:'assets'`、无 sourcemap；文件名 `assets/[name]-v4-[hash].js`；**manualChunks**：`vendor-vue`(vue,vue-router)、`vendor-echarts`、`vendor-xterm`；dev server 端口 5199，代理 `/api`、`/ws`(ws) → `VITE_PROXY_TARGET||http://10.66.66.5:9091`。
- 产物被 git 跟踪（`frontend-vite/dist/` 在仓库内，且当前有未提交的 index.html 空白 diff）。

---

## D. 风险与契约建议

### D1. 多 agent 并行开发的共享文件清单（高冲突区）

| 文件 | 依赖它的改造方向 | 冲突风险 |
|---|---|---|
| `backend/app/main.py`（4401 行） | 主机管理（servers CRUD/agent 任务）、健康大屏（聚合/startup 循环）、K3s（include_router + 新循环）、模型迁移（_ensure_new_columns） | **极高**：路由注册 279-292、startup 1190-1305、servers CRUD 1309-1537、采集循环 3541-3647、迁移 1106-1138 全在同一文件 |
| `backend/app/models.py` | 模型扩展（Server 新列 + Cluster 实体）、K3s 监控（可能新增表）、健康大屏 | 高（末尾追加类较安全，Server 类内部改动会撞行） |
| `backend/app/config.py` | K3s 集群配置 env、模型扩展 | 中（尾部追加字段） |
| `backend/requirements.txt` / `backend/Dockerfile` | K3s（新增 k8s 客户端） | 中（单行追加） |
| `frontend-vite/src/App.vue` | 页签工作台（布局重构）、/hosts 页（navs）、K3s 控制台（navs/选择器）、主机选择器组件化 | **极高**：且当前已有未提交改动（openGroups 代码块移动），动工前必须先提交或stash |
| `frontend-vite/src/router.js` | 页签、/hosts、K3s、健康大屏路由 | **极高**（当前已有未提交的 K3sConsolePreview 路由行） |
| `frontend-vite/src/hostContext.js` | 页签唯一键/条件选择器、/hosts、K3s（集群上下文可能并入） | 高 |
| `frontend-vite/src/api.js` | K3s 客户端封装、健康大屏 | 中（追加导出） |
| `frontend-vite/src/styles.css` | 全部 UI 改造 | 中（追加变量） |
| `frontend-vite/src/views/Screen.vue` | 健康大屏 K3s 优先改造 | 中（独立文件） |
| `deploy/docker/compose.yml`、`.gitlab-ci.yml` | K3s 网络路径、CI（新测试/构建 stage） | 中 |
| `frontend-vite/dist/` | 任何前端改动后重建 | 低但易忘（产物入库，需约定“代码提交不含 dist 重建”或统一由一人重建） |

建议契约：新后端路由一律新建 `app/<module>.py` + `APIRouter(prefix="/api/v2")`，仅把 include 行加进 `main.py:280-292`（每人 1 行、冲突可自动合并）；`_ensure_new_columns()` 迁移语句按“各自函数/各自 list 追加”拆分（当前集中在一个 list，建议改为每模块一个 `_ensure_*` 函数在 startup 分别调用）；前端 navs/router 改动建立“每人只追加条目”的约定。

### D2. 数据库迁移方式建议

- 现状机制 = `create_all`（新表） + `_ensure_new_columns()`（`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`，main.py:1106-1138） + 少量数据迁移函数 + 在线索引（metrics_history.py:23-39）。**没有 Alembic**，也没有 schema.sql。
- 为 `servers` 增加 `lan_ip/wireguard_ip/preferred_management_channel/management_address_override` 等：沿用现有惯例成本最低——models.py Server 类加列（`String(64)/String(20)`，可空，附 default）+ 在 `_ensure_new_columns` 的 `stmts` 列表追加 4-6 行 `ALTER TABLE servers ADD COLUMN IF NOT EXISTS ...`（注意 PG 9.5+ 支持 IF NOT EXISTS；测试库 SQLite 分支靠 create_all 全新建表，无需 ALTER）。新增 `Cluster` 实体（新表）直接走 `create_all` 即可；如需 servers.cluster_id 外键，注意旧行回填顺序（先加列后建 FK，或用可空 UUID 列不做硬 FK 以匹配现有“轻迁移”风格）。
- 风险提示：`_ensure_new_columns` 失败只打日志不阻断（1136-1138），新列务必可空或带默认值，避免启动后查询 4xx；同时把新列加进 `list_servers` 的返回 dict（main.py:1317-1340）才对前端可见。

### D3. Docker/K3s 中访问外部 K3s API 的网络路径注意点

- 现状网络模型（可复用的结论）：
  1. systemd 形态后端监听 `127.0.0.1:9091`，在宿主机网络命名空间——访问外部 K3s API（通常 `https://<server>:6443`）走主机路由，无额外配置；出网受主机防火墙（`app/firewall_control.py` 管理的就是这台主机）。
  2. 容器形态（deploy/docker/compose.yml）：backend 容器通过 `extra_hosts: host.docker.internal:host-gateway`（64-65 行）+ `LOCAL_AGENT_HOST=host.docker.internal`（62 行）回访宿主机上的 Agent(19100)。同理，K3s API 若在**宿主机同网段**，容器内需经 host-gateway 或把 K3s server IP/ingress 直接作为目标；若 K3s 在远程节点，容器出站默认可达，但需注意 egress 到 6443 的防火墙与 TLS。
  3. 仓库内已有的 K3s 佐证：`deploy/k8s/opscenter-web-nodeport.yaml`（NodePort 30088，namespace `opscenter`）说明 OpsCenter 已在某个 K3s 上以 NodePort 暴露 web——即“OpsCenter 运行在 K3s 内”的场景存在；若后端将来以 Pod 运行，访问**自身所在集群** API 最稳妥的是 in-cluster 模式（ServiceAccount + KUBERNETES_SERVICE_HOST），而访问**外部集群**需要挂载/下发 kubeconfig（config.py 需新增如 `K3S_KUBECONFIG`/`K3S_API_URL/TOKEN` 类字段，当前不存在）。
  4. Agent 通道现状（可类比“首选/备用管理通道”）：`agent_manager.resolve_agent_host`（agent_manager.py:36）决定走本机回环、host.docker.internal 还是 server.host；密码明文前缀存 `servers.ssh_key`（`__password__`）。K3s 双通道（LAN/WG）设计时注意 WG 地址目前**无任何落库字段**（仅 topology wireguard 场景在拓扑数据里出现），需要迁移支持。
- 其他：Caddy（deploy/product/Caddyfile）只反代 `/api/*`、`/ws/*`；若 K3s 代理流量要走前端同源（如经后端反代 kube API），需在 Caddyfile/后端增加路由，当前没有。

### 附录：快速定位索引（主要端点 → 文件:行）

- servers CRUD：main.py 1309/1415/1440/1455/1486；ssh-test 2909；scan 1540
- agent：deploy 3362 / upgrade 3436 / upgrade-outdated 3454 / version 3425 / status 3473 / uninstall 3516 / metrics 3650 / history 3747
- monitor：main.py 3086（实时）、3295（history）、2854（health-check）；system/summary：system_control.py 123；timeseries：metrics_history.py 224/265
- 大屏：topology.py 739；拓扑：topology.py 250/351/364
- 容器：control.py 529/604/644/673/725/758/788/845
- plaza：plaza.py 828/918/1018/1051/1066/1085/1117/1484/1527；探活循环 696
- terminal：main.py 4206（ws）；terminal.py（遗留，未注册）
- 迁移：main.py 1106（列）/1196（create_all）；索引 metrics_history.py 23
