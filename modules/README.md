# 独立模块运行约定

## 2026-10-14 用户侧底座结构

导航底座为“服务广场 / 主机管理 / 系统应用”。`系统应用` 是用户侧名称：其指标接口暂保留内部 key `host-monitor`，文件、进程、SSH、防火墙、终端通过同一入口的页签使用工作台现有执行服务，不拆成独立 `ops-tools` 模块。用户侧更名不代表指标存储或后端执行接口已退役。测试 web `5.5.7-test-system-app` 已部署；全量模块数据所有权迁移仍未完成。

每个业务模块独立构建镜像和部署，模块间只通过同源网关调用 HTTP 契约，不导入其他模块代码，也不访问其他模块数据库。`runtime/` 提供共同响应形状和全局会话校验，不持有业务数据。

工作台通过固定同源前缀 `/modules/{module-key}/` 访问模块；网关负责将前缀映射到对应模块服务并移除前缀。模块 key 包括 `service-catalog`、`host-inventory`、`host-monitor`、`k3s`、`pve-backup`、`alerts`、`logs`。

模块最小接口：

- `GET /health/live`：进程存活探针。
- `GET /health/ready`：模块可接收请求的就绪探针。
- `GET /api/v1/module`：模块 key、版本和只读能力清单。
- `GET /api/v1/summary`：含状态、采集时间、过期阈值、业务数据及脱敏错误摘要。
- `GET /api/v1/findings`：含状态、采集时间和有界告警项；每条告警用稳定 ID 去重。

浏览器经 Caddy 固定路径访问 `/modules/{module-key}/*`；各模块在进程内校验工作台签发的 Bearer（Ed25519 公钥，本轮限时兼容旧 HS256 会话），不再逐请求依赖旧 backend。host-inventory 和 service-catalog 的写接口使用同一会话并记录审计。模块没有独立登录入口；缺少验证密钥时业务 API fail closed。网关拒绝公开模块的健康探针、指标和接口文档路径；容器内探针仍直连模块服务。

告警模块使用独立逻辑库 `alerts` 保存事件、规则、静默和证书检查目标；告警评估及证书定时探测均在模块内执行。证书目标必须解析到公网地址，TLS 校验使用系统信任链。其他旧告警写/评估路径与定时任务仍待完成取证和退役。

## 2026-10-12 模块原生新增与测试端 HTTP 写入

> 历史状态：下列模块独立账号方案已于 2026-10-13 统一登录改造中停用，数据库中既有模块账号表未删除。

- host-inventory 与 service-catalog 已实现模块原生个人账号、独立 Bearer 会话、只读/编辑/管理员角色、密码哈希、登录限速和写审计；主机基础资产与手工服务支持新增，写入由各自模块数据库持有，不回写旧 backend。两个模块的 `admin` 管理员已在测试环境交互初始化；未设置默认账号。密码不可为空，但不设最小长度限制。
- VM100 测试入口为 `http://10.66.66.6:8090/v3/`，仅绑定 WireGuard 地址；Caddy HTTPS 试用已撤回，443 未监听。未认证的模块新增请求实测 401，旧 backend 写入口仍由网关返回 423。管理员初始化后的登录及新增流程尚待端到端验证。
- 仅启用基础新增。主机 SSH/Agent/删除及服务隐藏、排序、扫描凭据等操作仍关闭；资产编辑、归档恢复和高危运维操作未纳入本次写权限。
- 变更前数据库与部署配置备份：`/opt/opscenter-test/deploy/docker/pre-module-write-20261012-1925/`；密码策略源码备份：`/opt/opscenter-test/deploy/docker/pre-auth-policy-20261012-2031/`；回滚镜像标签：`opscenter/module-host-inventory:rollback-before-write-20261012`、`opscenter/module-service-catalog:rollback-before-write-20261012`、`opscenter/frontend:rollback-before-write-20261012`。仅重建两个模块，未重建 web/backend/数据库及其他服务，生产未改。
- 验证：host-inventory 9 项、service-catalog 9 项、runtime 6 项测试通过；compileall 通过。入口首页、health 与两个模块 readiness 均 200，两个模块各有一个 `admin` 角色管理员。认证后的新增流程尚未端到端验证。
- 安全边界：测试 HTTP 仅限 WireGuard 监听，不应暴露到不可信网络；个人密码/会话不会被 HTTP 保护。密码强度由用户自行负责，空密码仍禁止。PostgreSQL 仍为共享实例，逻辑数据库不构成实例级故障隔离。

值班首页须并行调用各模块并分别超时；单模块失败只影响对应卡片。告警模块拉取各模块 findings，采集模块不反向依赖告警。健康探针不做外部网络访问，不在启动时迁移共享数据库。

## 当前进度

截至 2026-10-13 17:10 CST，测试端 host-inventory `0.2.4-test` 已通过 backend 单向遥测桥接恢复新鲜 Agent 状态；backend `5.5.4-test-agent-bridge`、web `5.5.3-test-modular-nav` 已部署，详情见下方 17:10 节。主机管理、服务广场和主机监控仍保留为底座；AI 运维、拓扑和内置中间件控制台不再进入导航/路由。旧模块及数据迁移、PVE 只读接入、浏览器视觉验收仍未完成。

16:47 CST 完成主机资产单模块故障隔离复验：短暂停止该容器期间外部首页、backend health 仍 200；service-catalog、host-monitor、alerts、logs、PVE/备份保持 healthy，K3s readiness 200；随后资产模块恢复 healthy。

16:47 CST 只读核对 service-catalog 容器镜像内 `main.py`、`storage.py` 与当前本地源码 SHA256 一致；测试端运行 `0.4.3-test`，服务资料 JSONB 覆盖、首次导入和逐笔审计已部署。完整目录快照仍每 30 秒从旧 backend 刷新，凭证关联、探活执行与健康历史尚未迁移。

远端操作信任说明：VM100 的已确认目标指纹用于严格 SSH 校验；跳板 `10.66.66.3` 的指纹尚未另行确认。本轮远程部署均使用 VM100 直连并显式禁用 ProxyJump；如需经跳板连接，先从可信渠道核验其 host key。

host-inventory 使用独立逻辑数据库；backend 现通过专用单向桥接每 5 分钟推送 Agent 运行态，但资产业务资料、Agent enrollment/扫描仍部分由旧 backend 持有，因此此桥接不是最终所有权切换。service-catalog 将目录快照、资料覆盖、归档等保存在模块逻辑数据库，仍需完成独立刷新与探测所有权；host-monitor 使用模块自己的 Agent sampler、历史表和摘要 API，旧 backend 的指标 API/历史任务仍待取证及退役；alerts 与 logs 仍存在旧 backend/API 依赖。PVE/备份模块已接入只读 PVE/OSS 清单。独立进程/逻辑库不等同于独立 PostgreSQL 故障域。

2026-10-14 12:45 CST 更新：host-monitor `0.2.1-test` 移除未使用的旧 backend 只读适配器，主机管理 CPU/内存/磁盘概览改由 host-monitor 模块查询；模块直连 Agent 并写自己的历史库。VM100 测试端 host-monitor healthy，首页 200、模块匿名 API 401；已登录浏览器核验系统应用实时与历史指标及主机列表指标均可见。旧 backend metric routes/background storage 尚未删除，须先完成调用证据与历史数据退役核对。

仍待完成：最终迁移主机业务数据与 Agent enrollment；PVE 只读凭据配置与真实数据对账；服务目录、告警规则/事件、监控历史的所有权边界与迁移；旧 API/任务的调用证据与分批退役；浏览器视觉验收、剩余模块故障/回滚矩阵以及获授权的生产入口验收。测试入口仅限 WireGuard HTTP、共享 PostgreSQL 实例仍在；生产未改，未 commit/tag/push。

## 2026-10-13 17:10 CST Agent 遥测桥接与模块导航前端部署

- backend `5.5.4-test-agent-bridge` 在 Agent 健康检查成功后，单向向 host-inventory `0.2.4-test` 推送专用遥测。模块仅更新运行态/Agent 字段；用户拥有的资产资料不被覆盖。模块库新增 `telemetry_updated_at` 可空列，无跨库读写；推送失败不阻断 Agent 检查。专用 token 文件为 VM100 root:root、0600，值未保存。模块摘要已恢复 `ok`：1 台主机在线、Agent running；17:07 CST cache age 211 秒。
- 部署前备份 `/opt/opscenter-test/.deploy-backups/host-inventory-agent-bridge-20261013-0857Z/`：Compose、源码、三库 custom dump（均经 `pg_restore --list`），以及旧 backend/host-inventory 镜像 rollback 标签。镜像：backend `sha256:bc04daf02ed436fbb243bad89b416043b5377ccfe45f257a1228f92fa003e27f`；host-inventory `sha256:9e73ca921c69a202e094dc63fb56d0ea095223c2e754af8f49268feaeedb1f97`。未有破坏性数据迁移。
- 前端 `5.5.3` 的 Vite build 成功，测试 Compose 校验通过；删除 AI 运维/拓扑/内置中间件路由与入口，保留服务广场、主机管理、主机监控底座入口。VM100 只更新 web，镜像 ID `sha256:78eec415735ec9ad64ffc0065b923513a2b46e5ec209015147572708ed8ae41c`；备份 `/opt/opscenter-test/.deploy-backups/frontend-modular-nav-20261013-0906Z/` 保存旧 dist、Compose、Caddy，旧镜像标签 `rollback-before-modular-nav-20261013-0906Z`。未改数据库或其他容器。
- 验收：模块测试 65 passed，backend bridge/Agent recovery 8 passed，compileall 与 `git diff --check` 通过；前端仍有 ECharts chunk >500 kB 警告。外部 `/v3/`、backend health 200，静态入口 bundle 200/版本 5.5.3，匿名 host-inventory API 401。未进行浏览器视觉验收；测试地址 `http://10.66.66.6:8090/v3/`。
- 仍待：完整迁移 Agent enrollment/主机资料写入、服务目录凭证/探活/健康历史、告警与监控持久化所有权；配置 PVE 只读 URL/token/CA 才能获取真实摘要；旧 API/任务调用取证与退役、剩余故障/回滚矩阵、浏览器视觉验收和另行授权的生产验收。当前入口仍为仅 WireGuard HTTP，生产未改、未 commit/tag/push。
- VM100 连接采用已核验目标指纹的直连方式并禁用 ProxyJump。跳板 `10.66.66.3` 的 host key 仍未独立验证。
- 17:13 CST 最终复核 host-inventory summary `ok`、1/1 在线、Agent running、cache age 74 秒；web/backend/各模块/DB 容器仍运行，已有 Docker healthcheck 的容器均 healthy；外部入口 200，Caddy 校验成功。

## 2026-10-12 告警与日志模块测试部署（覆盖上方历史阻塞记录）

- SSH 恢复后，测试 backend/PostgreSQL healthy，`/data` I/O PSI avg10 约 3.79%，可用空间约 73 GB。部署前配置副本：`/opt/opscenter-test/deploy/docker/pre-logs-20261012-1200/`、`pre-logs-summary-20261012-1215/`。
- alerts 只读模块已部署 VM100；规则 Webhook 字段不透传，旧规则路由返回 410；页面静默/确认写入口关闭。日志模块 `logs` 通过固定 `/modules/logs/*` 网关提供只读状态、健康、采集概况、查询/统计/趋势；前端 6 类读取调用均切至模块路径。请求只转发当前 Authorization 头，采集器凭据标志及原始错误不返回；摘要探测 Loki `/ready`，避免数据源故障时首页误报正常；旧采集器写入口仍封锁。
- 日志镜像构建上下文约 1.327 MB，前端上下文 1.246 MB；只重建 `module-logs` 与 `web`，未重建 DB/backend/K3s/PVE/Kafka/ZK/RabbitMQ。最终模块镜像 ID `sha256:30445b08f8ff6ed74279240621d31e884a42c48cd860c25dc78e01f1b4ea7ee9`，web ID `sha256:03d9149925e8a5060668e38397ca0c0344c4224d82cf0c5008a8eb08773f02ba`；保留 `rollback-20261012-logs-module` 与 `rollback-20261012-logs` 标签。
- 真实测试入口 `/v3/`、含日志中心卡片的首页 JS、LogCenter JS、backend health、日志 readiness/module/status/health/agents 均 200；真实主机 ID 日志查询成功（当前默认窗口 0 条）。采集概况响应无 `has_credentials` / `collector_error`。单停 logs 时 readiness 502，而 backend、service-catalog、host-monitor 保持 200；重启后 logs healthy，检查恢复 200。当前测试容器均运行，DB/backend healthy，`/data` 约 73 GB 可用。
- 定向测试 24 passed（logs 4、alerts 5、host-inventory 5、host-monitor 3、service-catalog 3、runtime 3、K3s 1）；Vite build 成功，仍有 ECharts 大 chunk 和空 vendor-xterm 提示。未运行浏览器交互/视觉、全后端全量或生产 HTTPS 验收。
- 安全说明：VM100 测试入口仍是 HTTP。更新 alerts 镜像前曾观察到规则响应含 `notify_webhooks` 字段，但没有打印或记录字段值；访问日志不足以确认是否有其他调用者读取。若其中为有效 webhook，建议评估轮换；本轮未改 webhook 配置。
- 未完成：PVE/备份缺经核验只读凭据；数据所有权迁移、身份/HTTPS、审计/CSRF/限速、网络策略、旧 API 访问证据与退役、生产入口验收仍在范围。仅部署 VM100 测试端，生产未改、未 commit/tag/push。Compose 提示的 AKHQ/RabbitMQ orphan 是有意共置服务，本次未删除。

## 2026-10-12 侧栏分组、值班首页与资产快照所有权切换

- 侧栏改为“值班入口 / 底座 / 业务模块 / 扩展模块”；服务广场、主机监控、主机管理保留为底座入口。
- 值班首页对每个已接入模块并行读取 summary/findings，独立超时和错误提示；展示采集时间、最多 3 条待关注项；PVE/备份清晰标注“尚未接入”，不伪造运行状态。
- VM100 测试环境主机资产快照与旧 API 记录数均为 1，状态为 ok；变更前备份 `/opt/opscenter-test/deploy/docker/pre-host-inventory-cutover-20261012/host_inventory.sql`（3726 bytes，SHA256 `7165392daf268ff59daf7bc913c248eccac3260c2bd7e7fdde7b5c98e96ebad7`）及 Compose。测试 `module-host-inventory` 设置 `HOST_INVENTORY_SYNC_ENABLED=false`，停止运行时每 30 秒从旧 backend 拉取；独立库仍只读，用户写入和 Agent 实时上报归属尚未迁完。
- 定向测试 25 passed（host-inventory 6、service-catalog 3、host-monitor 3、alerts 5、K3s 1、logs 4、runtime 3）；Vite build 通过（仍有 ECharts 大 chunk、空 vendor-xterm 提示）。VM100 仅重建 web 与 module-host-inventory；值班首页和 host-inventory、service-catalog、host-monitor、K3s、alerts、logs 的 summary/findings 路由验收 200，web running、资产模块 healthy。回滚镜像标签：`rollback-20261012-duty-home`、`rollback-20261012-ownership`。生产未动。
- 剩余迁移：service-catalog 目录、host-monitor 历史指标、alerts 数据的持久化归属；PVE/备份模块及经核验只读凭据；个人身份/HTTPS/审计/CSRF/限速；旧 API 访问证据、模块故障演练和生产入口验收。测试仍是 HTTP，不可开放写操作；未 commit/tag/push。

## 2026-10-12 service-catalog 快照状态

VM100 测试环境的 service-catalog 已把服务列表、健康概览和隐藏项快照持久化到模块专属逻辑数据库；每 30 秒尝试从旧 backend 刷新，数据源故障时继续服务最近成功快照。readiness 要求数据库可用且目录快照已初始化。该数据库仍与其他模块共享 PostgreSQL 实例；旧 backend 仍是数据源和刷新任务持有者，因此不是数据所有权切换完成。测试计数为 19 项服务、19 项 24 小时健康概览、0 项隐藏服务。VM100 写操作保持关闭。

## 2026-10-13 service-catalog 服务资料写入迁移（历史状态，后续已部署）

> 以下待部署记录反映当时状态；16:47 CST 的只读源码比对确认服务资料覆盖实现已包含在测试端 `0.4.3-test` 镜像中。

- 服务广场现统一经 `/modules/service-catalog/` 调用。扫描、凭证、探活与事件操作使用固定模块代理并转发当前工作台 Bearer；新增手工服务、排序、隐藏状态和归档由模块数据库持有。服务资料编辑新增模块库 JSONB 覆盖层及逐次审计，列表、详情和首页摘要应用覆盖值；首次成功读取时按 `profile_updated_at` 导入旧资料的非敏感字段，冲突不覆盖模块已有值，凭证/账号关联与健康历史暂未迁移。
- 模块镜像源码版本提升至 `0.4.0`；测试环境只读实测仍运行 `0.3.0`，本次变更尚未部署。待数据库只追加建表，无旧表删除或既有数据覆盖；旧资料首次编辑时才保存当前表单值，批量导入尚未实施。
- 本地验证：service-catalog 22 项测试通过，前端 Vite build 成功，登录会话客户端 3 项测试通过。浏览器视觉验收未执行。
- 测试部署待处理：当前入口 HTTP 健康，service-catalog readiness 200/版本 `0.3.0`；SSH 连接出现与本机 `known_hosts` 不一致的新 ED25519 指纹，尚未核验，因此未绕过主机校验、未部署，也未改动 VM100 或生产。

## 2026-10-13 统一工作台登录（历史状态，后续已部署）

> 以下待部署记录反映当时状态；统一模块认证后续已在 14:24 CST 部署并验收，详见同日全局鉴权记忆。

- Caddy 测试网关将所有 `/modules/*` 请求交由 backend `/api/v2/auth/me` 校验，沿用工作台当前 Bearer；资产与服务模块仍在写入口验证同一全局 JWT，保留模块数据写入和逐笔审计。
- 移除模块独立登录、用户创建、独立会话及 bootstrap 命令；缺少全局签名密钥时模块写操作返回 503，不再回退成另一套认证。既有 `module_users` / `module_sessions` 数据表未删除，也不再用于认证。
- 因共享认证运行时变更，host-inventory 升至 `0.2.1`，service-catalog 升至 `0.4.1`；测试 Compose 已对齐镜像标签，当前远端仍是 `0.2.0` / `0.3.0`，待安全门槛解除后部署。
- 未登录模块请求的 401 检查已加入 VM100 只读验收脚本。部署仍待核实 VM100 SSH ED25519 指纹；现有 SSH 指纹不一致，禁止跳过 host-key 校验。代码尚未上 VM100，测试端实际认证行为仍待部署后验证。

### 2026-10-12 VM100 Caddy HTTPS

测试入口另有 Caddy 内部 CA HTTPS：`10.66.66.6:443` 仅监听 WireGuard 地址，HTTP `:8090` 保留回退；客户端须信任导出的根证书才能避免浏览器证书告警。公网可信证书、个人鉴权及写操作仍未启用。

## 2026-10-12 VM100 测试端 Caddy HTTPS 试用

- VM100 使用 Caddy 2.11.4 内置 CA 为 `10.66.66.6` 签发内部证书；Compose 将 443 限定绑定到 WireGuard 地址，未增加公网防火墙规则。Caddy `/data`、`/config` 使用持久化 Docker volume。HTTP `:8090` 保留作回退，不做自动跳转。
- 因 Windows Schannel 对 IP 连接不发送 SNI，Caddy 配置 `default_sni 10.66.66.6`；根证书导出至工作区 `memory/opscenter-test-caddy-root.crt`（SHA256 `E46F3348DCE2A8533A6740E5C3D067BC7331AF989BDDA981C07EFAF4AB74E5DD`）。仅导出公有根证书，未把它安装进 Windows/浏览器信任库。使用此根证书并跳过 Schannel 在线吊销状态检查后，HTTPS 首页和模块请求均 200；无信任安装时浏览器会提示证书不受信任。
- 真实测试入口 `/v3/`、service-catalog readiness/summary、host-inventory、host-monitor、alerts、logs、K3s summary 的 HTTPS 均 200；HTTP 回退 200；受保护 POST 仍为 423；PostgreSQL ready。443 绑定实测为 `10.66.66.6:443`，未绑定 LAN 或 `0.0.0.0`。
- 变更前备份位于 `/opt/opscenter-test/deploy/docker/pre-caddy-https-20261012-1843/`：原 Caddyfile、Compose 和 71,602,845-byte custom-format 测试 DB dump（权限 600）。仅重建测试 web/Caddy 容器；backend、数据库、业务模块和生产未改。公网可信证书仍需真实域名与公网可达的 ACME 验证条件；HTTPS 不改变身份鉴权/审计未完成的状态。
