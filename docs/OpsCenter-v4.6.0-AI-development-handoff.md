# OpsCenter v4.6.0 后续开发实施与 AI 交接规范

> 文档状态：待实施基线（不是已完成功能清单）  
> 编制日期：2026-08-31（Asia/Shanghai）  
> 当前代码基线：`main` / `8a1b466` / `v4.5.1`  
> 目标版本：`v4.6.0`  
> 适用对象：接手开发的 AI、代码审查人员、部署人员

## 1. 文档用途与执行原则

本文件是 OpsCenter 下一阶段的唯一实施交接基线。接手 AI 必须先验证代码与环境事实，再按阶段执行；不得把本文件中的“目标设计”描述成“当前已经实现”，不得根据截图、旧飞书文档或记忆臆造接口、数据库字段、部署结果。

执行时按以下词义理解：

- “当前已有”：在基线提交 `8a1b466` 中已经存在，可从源码或测试定位。
- “本期新增”：v4.6.0 必须实现并通过验收。
- “候选后续”：不属于 v4.6.0，未经用户确认不得开发。
- “禁止”：任何情况下都不能自行绕过；如与现场事实冲突，应停止相关改动并报告证据。

## 2. 当前事实基线

### 2.1 代码与发布基线

- 本地仓库：`C:\Users\dzd\Documents\Codex\2026-08-27\yue\work\opscenter-current`
- 当前分支与提交：`main`，`8a1b466 feat: add service plaza health overview`
- 当前版本：后端 `backend/app/version.py` 与前端 `frontend-vite/package.json` 均为 `4.5.1`
- 当前标签：`v4.5.1` 指向基线提交
- GitLab 远端：`origin`，为内部 GitLab 仓库
- GitHub 远端：`github`，`https://github.com/salmon-spec/opscenter.git`
- Jenkins 从 GitLab `main` 直接检出并部署；生产部署目录为 `/opt/opscenter`
- Jenkins 部署目标为 `10.66.66.5`，后端监听 `9091`；管理网络继续使用 `10.66.66.*`
- Jenkins 的 rsync 排除 `.git`，因此不得用生产目录 `/opt/opscenter/.git` 判断线上版本；必须看 Jenkins Checkout 提交、后端版本接口和前端版本。
- GitLab CI 当前只负责测试；Jenkins 负责备份、部署、构建前端、重启后端与健康验证。

### 2.2 已完成能力，不得重复实现

- v4.2：全局主机上下文、主机管理、数据库/容器/系统分域、容器按需资源统计、系统终端和进程管理。
- v4.3：文件、防火墙、SSH 管理；服务广场首屏性能优化；Agent 修复与版本恢复能力。
- v4.4：多主机监控历史、按时间段查询、Loki + Alloy 长期日志、365 天日志保留、存储健康。
- v4.4.1：服务广场信息编辑、完整详情、账号密码加密保存与按需显示、凭证查看审计。
- v4.5.0：每个服务独立探活策略、手动探测、探测历史、24 小时可用率、凭证审计保留。
- v4.5.1：服务健康总览卡片、状态筛选、24 小时聚合接口与响应式界面。

### 2.3 当前相关代码事实

- `backend/app/plaza.py`：服务广场目录、资料覆盖、凭证读取审计、探测调度、历史与健康总览。
- `backend/app/service_health.py`：对 `Service` 表的另一套探测、连续失败计数、告警与恢复通知。
- `backend/app/models.py` 当前已有：`ServiceProbeResult`、`PlazaServicePreference`、`PlazaServiceProfile`、`PlazaProbeResult`、`PlazaCredentialAccess`、`AlertSilence`。
- `PlazaServiceProfile` 当前探测字段：`probe_enabled`、`probe_interval_seconds`、`probe_timeout_seconds`、`probe_success_statuses`、`probe_verify_tls`。
- `PlazaProbeResult` 保存原始探测结果；默认保留 90 天。
- 服务广场探测循环每 15 秒调度一次，但由每项间隔决定是否到期；前端总览读取不能触发强制网络探测。
- `service_health.py` 的连续失败状态保存在进程内存 `_health_state`，重启后会丢失。
- `plaza.py` 与 `service_health.py` 的目标集合存在重叠可能，特别是 `manual-<service uuid>` 项；这是 v4.6.0 首要问题。
- 前端入口：`frontend-vite/src/views/ServicePlaza.vue`；详情：`frontend-vite/src/components/ServiceDetailDrawer.vue`；路由：`frontend-vite/src/router.js`。

## 3. v4.6.0 总目标

版本主题：统一服务可靠性与事件中心。

目标是保留服务广场“点击即开、缓存先显”的体验，同时将原始探测、稳定状态、告警事件、恢复事件和维护静默串成一条可追溯链路，消除重复探测和重复通知。

必须实现的结果：

1. 同一个 Web 服务在一个周期内只由一个调度器发起探测。
2. 原始失败达到连续失败阈值后才进入 down 并开启一条事件。
3. 连续成功达到恢复阈值后关闭原事件并只发送一次恢复通知。
4. 阈值状态持久化，后端重启不会清空连续失败计数或重复告警。
5. 支持按服务静默；静默期间继续探测和记录，但不发送通知。
6. 支持查看活动事件、历史事件、确认事件及当前稳定状态。
7. 健康总览只读缓存/数据库，不在页面请求过程中同步探测外部服务。
8. 所有新增接口、数据迁移、前端交互、性能与安全均有自动化测试或可重复验证证据。

## 4. 范围边界

### 4.1 v4.6.0 范围内

- 双探测治理与目标所有权判定。
- 服务级失败阈值、恢复阈值、通知开关。
- 持久化健康状态。
- 故障事件创建、恢复、确认和查询。
- 服务级维护静默。
- 复用当前全局 Webhook 通知，防重复告警与恢复通知。
- 服务广场总览和详情页展示事件与稳定状态。
- 数据库索引、保留策略、性能和安全测试。
- 版本号统一更新到 4.6.0、双远端推送和 Jenkins 部署验收。

### 4.2 明确不在 v4.6.0

- 不开发短信、电话、邮件、企业微信等新通知渠道。
- 不开发复杂升级策略、值班表、通知路由树或多租户。
- 不自动重启故障服务，不执行自愈命令。
- 不改造现有监控历史、Loki/Alloy 日志、容器统计和数据库管理。
- 不新增 Key 浏览器、备份恢复、慢查询等数据库功能。
- 不照搬 1Panel GPL 源码；只允许参考信息架构和交互层级。
- 不在本期清理或删除旧探测历史表。
- 不因为顺手而重构无关页面、换 UI 框架、升级大版本依赖。

## 5. 核心设计

### 5.1 探测目标所有权

采用渐进式收敛，不一次性删除 `service_health.py`：

- 服务广场可见的目录项及所有 `manual-<service uuid>` 项由 `plaza.py` 的策略引擎负责。
- `service_health.py` 仅负责未被服务广场接管、且拥有有效 HTTP(S) 健康地址的 `Service`。
- 新增一个纯函数用于计算“服务广场已接管的 Service ID 集合”；`service_health._snapshot_targets()` 查询后必须排除该集合。
- 目标所有权判断必须基于稳定主键（Service UUID/`manual-UUID`），禁止只按名称判断。
- 目录服务与 Service 的 URL 关联仅用于展示，不得成为唯一排重依据；URL 可能带尾斜杠、反向代理或不同健康路径。
- 同一轮探测必须有非阻塞周期锁；前一轮未结束时跳过，不堆积任务。

### 5.2 新增数据模型

建议新增两张表，命名可根据现有规范微调，但语义不得改变。

#### `plaza_health_states`

每个 `plaza_key` 仅一行，用于重启安全的状态机：

- `plaza_key`：主键，最长 140。
- `stable_status`：`unknown | up | degraded | down | disabled`。
- `consecutive_failures`：非负整数。
- `consecutive_successes`：非负整数。
- `last_checked_at`、`last_success_at`、`last_failure_at`。
- `last_transition_at`。
- `last_http_status`、`last_latency_ms`、`last_error`。
- `active_incident_id`：可空，指向当前未恢复事件；如外键会增加迁移风险，可只存 UUID 并在业务层校验。
- `created_at`、`updated_at`。

索引：`stable_status`、`last_checked_at`；不得在同一 `plaza_key` 创建多行。

#### `plaza_health_incidents`

- `id`：UUID 主键。
- `plaza_key`：索引。
- `status`：`open | acknowledged | resolved`。
- `opened_at`、`acknowledged_at`、`resolved_at`。
- `acknowledged_by`：可空，只保存用户标识，不保存令牌。
- `first_error`、`last_error`、`last_http_status`。
- `failure_count_at_open`。
- `alert_notified_at`、`recovery_notified_at`：用于幂等。
- `created_at`、`updated_at`。

索引至少覆盖 `(plaza_key, status)` 和 `opened_at`。同一 `plaza_key` 同时最多一条 `open/acknowledged` 事件；业务层和数据库约束至少实现一种可靠保证。

#### 静默模型

优先评估复用现有 `AlertSilence`。如果其 `rule_id/server_id` 语义无法准确关联 `plaza_key`，新增 `plaza_health_silences`，字段为：`id`、`plaza_key`、`starts_at`、`ends_at`、`reason`、`created_by`、`created_at`。`plaza_key` 为空是否表示全局静默，必须由测试明确；本期 UI 默认只创建服务级静默。

### 5.3 现有配置模型扩展

在 `PlazaServiceProfile` 追加：

- `probe_failure_threshold`：1-10，默认 3。
- `probe_recovery_threshold`：1-5，默认 1。
- `probe_notifications_enabled`：布尔，默认 true。

必须同步更新：Pydantic 请求模型、详情与列表响应、默认 profile overlay、数据库启动期兼容迁移、前端表单、API 测试。空值使用服务端默认值，不能让旧记录出现 500。

### 5.4 状态机规则

每次原始探测先写 `PlazaProbeResult`，再在同一数据库事务内更新稳定状态：

- 初始：`unknown`。
- 成功：失败计数归零，成功计数 +1；没有活动事件时稳定状态为 `up`。
- 失败但未达到失败阈值：成功计数归零，失败计数 +1，稳定状态为 `degraded`，不创建事件、不通知。
- 失败达到阈值：稳定状态为 `down`；若无活动事件则创建一条事件；已有事件则只更新最后错误，不新建事件。
- down 后成功但未达到恢复阈值：保持 down，成功计数递增。
- 成功达到恢复阈值：状态变为 `up`，事件变为 `resolved`，设置恢复时间；恢复通知最多一次。
- `probe_enabled=false`：定时调度不探测，稳定状态显示 `disabled`；手动探测仍允许，但手动结果默认不改变禁用状态，除非现有产品约定明确要求改变。
- 静默：继续探测、更新状态和事件；仅抑制告警/恢复通知。界面明确显示“已静默至时间”。
- 确认事件：只把 `open` 改为 `acknowledged`，不改变健康状态、不停止探测。

必须以数据库时间或统一 UTC 写入，API 返回 ISO 8601；前端按本地时区展示。

### 5.5 通知幂等

- 复用 `DEFAULT_NOTIFY_WEBHOOKS` 和现有飞书卡片发送方式，不在新模块中硬编码 Webhook。
- 事件创建事务提交后再发通知；发送成功后记录 `alert_notified_at`。
- 重启、重复周期或并发请求不能对同一事件重复发告警。
- 恢复只针对曾打开的事件发送一次，记录 `recovery_notified_at`。
- 未配置 Webhook 时不能报错；事件仍需正常保存。
- 通知内容可包含服务名、主机名、入口地址、失败次数、HTTP 状态、错误摘要和时间；严禁包含账号、密码、私钥、Token、完整 Authorization 头或数据库凭证。
- 错误摘要限制长度并清理换行/控制字符，防止卡片注入和日志污染。

## 6. API 设计与兼容要求

保留所有现有 `/api/v2/services/plaza` 接口和字段；只能向响应追加字段，不能删除或改名。

### 6.1 扩展现有接口

- `PUT /api/v2/services/plaza/{plaza_key}`：接收三个新策略字段并按当前部分更新语义保存。
- `GET /api/v2/services/plaza`：追加 `stable_status`、连续失败数、活动事件 ID、静默截止时间；列表不得返回凭证明文。
- `GET /api/v2/services/plaza/{plaza_key}/detail`：追加完整健康状态、当前事件摘要、策略字段、静默摘要。
- `GET /api/v2/services/plaza/health-overview?hours=24`：保留现有 summary/items，追加 active incident、degraded、silenced 等计数。聚合只查数据库，不发外部请求。
- `POST /api/v2/services/plaza/{plaza_key}/probe`：继续执行手动探测，响应追加本次原始结果与状态转换结果。

### 6.2 新增接口

- `GET /api/v2/services/plaza/incidents?status=&plaza_key=&hours=&limit=&offset=`：分页事件列表。
- `GET /api/v2/services/plaza/incidents/{incident_id}`：事件详情。
- `POST /api/v2/services/plaza/incidents/{incident_id}/acknowledge`：确认事件；重复确认幂等。
- `GET /api/v2/services/plaza/silences?active=&plaza_key=`：查询静默。
- `POST /api/v2/services/plaza/silences`：创建服务级静默，校验开始/结束时间与原因长度。
- `DELETE /api/v2/services/plaza/silences/{silence_id}`：提前结束/删除静默；优先软结束，避免丢失审计。

### 6.3 错误语义

- 不存在的服务/事件/静默：404。
- 无效阈值、时间范围、状态过滤：422。
- 并发创建相同活动事件：通过事务/唯一约束收敛，API 不应返回 500。
- 事件已恢复后确认：409 或幂等返回当前状态，必须选一种并写测试；推荐幂等返回当前状态。
- 认证沿用当前 `get_current_user`；写操作必须认证，读取权限遵循现有项目规范，不擅自引入新 RBAC。

## 7. 前端实施

### 7.1 服务广场总览

- 保留 `localStorage` 先显和后台校验机制，不清空列表等待 API。
- 现有“服务总数、正常、异常、24h 可用率”等卡片继续保留，追加“波动中/活动事件/已静默”信息。
- 状态筛选支持 `up/down/degraded/unknown/disabled/silenced`，但“silenced”是附加属性，不能覆盖真实健康状态。
- 发生 API 错误时继续保留缓存入口，展示轻量错误提示；不得整页转圈。
- 总览请求不允许触发同步探测，目标：有缓存时 300ms 内可点击服务入口。

### 7.2 服务详情抽屉

在现有 `ServiceDetailDrawer.vue` 中扩展，不另写一个重复详情组件：

- 探测配置区增加失败阈值、恢复阈值、通知开关。
- 展示当前稳定状态、连续失败/成功、最后转换、最后错误、最新延迟。
- 展示最近事件时间线和当前事件状态。
- 提供“立即探测”“确认事件”“创建静默”“提前结束静默”。
- 删除/破坏性操作仍需二次确认；静默时间必须明确显示绝对结束时间。
- 密码显示仍执行现有 60 秒自动隐藏和凭证查看审计；改健康功能时不得破坏。

### 7.3 服务健康页面

可新增 `/service-health` 独立页面并在服务广场附近提供入口，包含：

- 活动事件表格、历史事件表格。
- 服务、主机、状态、时间范围筛选。
- 事件详情与确认操作。
- 当前静默列表与提前结束操作。
- 页面可见时最多每 15 秒刷新一次轻量事件摘要；路由离开、浏览器隐藏或前一请求未完成时停止/跳过。

若本期工作量受限，优先保证详情抽屉和 API 完整，独立页面可作为同版本最后一个前端子阶段，但不能删除事件查询能力。

## 8. 性能与可靠性要求

- 探测并发继续有上限，禁止为每个服务创建常驻线程。
- 网络 I/O 期间不得长期占用数据库连接；先读取标量快照，探测结束后短事务写入。
- `health-overview` 禁止把时间范围内全部记录加载到 Python 后再无界聚合。至少限制时间/记录数；优先 SQL 聚合与“每服务最新记录”查询。
- 新表按查询路径加索引；100 个服务、7 天数据规模下，健康总览后端目标小于 300ms（不含网络探测）。
- 后端重启后首轮必须从 `plaza_health_states` 恢复状态，不把全部服务短暂显示为 unknown。
- 保留 90 天原始 `PlazaProbeResult`；事件至少保留 365 天。将新表加入现有保留清理机制时，不得误删活动事件。
- 前端请求使用既有 API 客户端和取消机制；切换路由/服务时取消旧请求，避免旧详情覆盖新详情。

## 9. 安全限制

- 不读取、打印、提交、复制任何生产密码、SSH 私钥、JWT 密钥、`CREDENTIAL_KEY`、Webhook 或 Token。
- 不修改 `CREDENTIAL_KEY`；否则现有服务广场密码将无法解密。
- API 永不返回存储的密文或凭证明文，只有既有受审计的 reveal 接口可短时返回密码。
- 审计和异常日志必须递归遮蔽 `password`、`secret`、`private_key`、`token`、`authorization`、`cookie` 等字段。
- `plaza_key`、事件 ID、静默 ID 必须严格校验；不得拼接进 SQL、shell 或 URL 命令。
- 探测仅允许 `http://` 和 `https://`；保持当前 TLS 校验默认开启。关闭 TLS 校验必须是单服务显式配置。
- 不把用户可控错误原样写入飞书卡片或 HTML；前端默认转义，后端截断和清理控制字符。
- 不直接在生产数据库执行未评审的删除/重建表操作。

## 10. 兼容迁移策略

项目当前采用 SQLAlchemy `create_all` 加启动期补列模式，接手 AI 必须先确认 `main.py` 现有兼容迁移函数，再遵循同一机制：

1. 新表通过模型定义创建。
2. `PlazaServiceProfile` 新列采用可重复执行的补列逻辑；PostgreSQL 与 SQLite 测试均可启动。
3. 旧记录缺省值按失败阈值 3、恢复阈值 1、通知开启处理。
4. 首次运行时可根据每服务最新 `PlazaProbeResult` 初始化 `plaza_health_states`，但不得伪造连续次数和历史事件；无法确定时使用 unknown。
5. 不删除 `ServiceProbeResult`、`PlazaProbeResult` 或 `_health_state` 相关代码，先通过排重逐步退役。
6. 回滚到 v4.5.1 时新增表和列可保留，不影响旧代码读取。

## 11. 实施阶段与提交拆分

### 阶段 0：基线复核

- 执行 `git status --short --branch`、`git log -1 --oneline`、版本核对。
- 阅读本文件列出的核心代码，不依赖旧对话总结。
- 运行当前后端全量测试和前端生产构建，记录基线。
- 若工作树有用户未提交改动，保留并避开；禁止 reset/checkout 覆盖。

输出：基线证据，不改功能。

### 阶段 1：双探测排重

- 实现稳定的目标所有权函数。
- `service_health.py` 排除 plaza 接管的 Service。
- 添加单元测试证明 manual 服务每周期只被探测一次。
- 不改 API 和 UI。

建议提交：`fix: prevent duplicate plaza service probes`

### 阶段 2：持久化状态机与事件

- 新增模型、兼容迁移、状态转换服务。
- 将 `plaza.py` 的原始结果持久化与状态转换纳入短事务。
- 实现失败/恢复阈值和事件幂等。
- 此阶段先不发送通知。

建议提交：`feat: persist plaza health state and incidents`

### 阶段 3：通知与静默

- 复用现有 Webhook 发送能力。
- 实现告警、恢复通知幂等。
- 实现服务级静默，静默仅抑制通知。
- 添加安全清理与通知失败测试。

建议提交：`feat: add incident notifications and maintenance silences`

### 阶段 4：API 与契约

- 扩展现有策略字段和响应。
- 新增事件、确认和静默接口。
- 检查 OpenAPI，确保路由顺序不会被 `/{plaza_key}` 吞掉；固定路径必须放在动态路径之前或依赖框架明确匹配。
- 添加权限、404、422、幂等、分页测试。

建议提交：`feat: expose service reliability APIs`

### 阶段 5：前端体验

- 扩展总览、详情抽屉。
- 新增服务健康页面或同等事件管理入口。
- 保持缓存先显、取消旧请求、页面隐藏停止刷新。
- 生产构建通过并进行桌面/窄屏视觉检查。

建议提交：`feat: add service incident center UI`

### 阶段 6：性能、安全与回归

- 优化聚合查询和索引。
- 全量测试、前端构建、OpenAPI 契约、安全检查。
- 在测试数据下验证重启恢复、并发周期、通知幂等和保留清理。
- 不在此阶段顺手增加新功能。

建议提交：`test: cover v4.6 service reliability flows`

### 阶段 7：版本、推送与部署

- 所有验收通过后，最后统一把后端与前端版本更新为 `4.6.0`，README/CI 注释中若有旧版本展示一并校正。
- 提交：`release: v4.6.0`，创建 annotated tag `v4.6.0`。
- 推送 GitLab 与 GitHub 的 `main` 和标签；先确认两个远端指向同一提交。
- Jenkins 无需阻塞后续本地工作，但发布结论必须等待该次构建完成后给出。
- 验收 Jenkins Checkout 的 commit 必须等于 GitLab main；查看构建、部署、Verify 与 `HEALTH_OK`。
- 线上核对后端/前端版本、健康接口、服务广场数量、健康总览、事件接口。不得只凭“流水线绿色”认定功能可用。

## 12. 测试矩阵与验收标准

### 12.1 后端自动化

- 未到失败阈值：只进入 degraded，不开事件、不通知。
- 达到阈值：只创建一条活动事件，只发一次告警。
- 继续失败：更新同一事件，不重复通知。
- 后端重启：连续计数和活动事件仍存在，不重复告警。
- 未到恢复阈值：保持 down。
- 达到恢复阈值：关闭事件，只发一次恢复通知。
- 静默期间：继续产生原始结果和事件，不发告警/恢复。
- 静默结束：不补发已被抑制的旧通知；下一次新的状态转换按正常规则处理。
- 禁用探测：定时周期不请求；手动探测行为符合第 5.4 节。
- 401/403 是否成功完全由 `probe_success_statuses` 决定。
- TLS 校验默认开启；显式关闭只影响该服务。
- manual 服务不被两套循环重复探测。
- 并发两个周期不产生双事件。
- 所有 API 的权限、验证、分页、404 与幂等覆盖。
- 响应、日志、审计、通知均不出现凭证。
- 保留任务不删除活动事件。

### 12.2 前端自动/人工

- 有缓存时服务入口立即可见和可点击，不等待健康总览。
- 健康总览失败不阻断服务打开。
- 状态、事件和静默显示一致；刷新后不丢失。
- 快速切换服务时旧请求不能覆盖新详情。
- 浏览器隐藏/离开路由后停止事件轮询。
- 立即探测按钮有 loading、防重复点击和成功/失败反馈。
- 创建静默要求时间与原因，结束时间展示明确。
- 密码显示和 60 秒自动隐藏、查看审计没有回归。
- 桌面宽屏与窄屏均无横向溢出、按钮不可见或遮挡。

### 12.3 发布门槛

- 后端现有测试全部通过，并新增本期测试；不得通过删除/skip 旧测试获得绿色。
- 前端 `pnpm build` 成功，无阻断性警告。
- OpenAPI 可生成，新增路由均存在且无冲突。
- 本地工作树只包含本期相关文件；无密钥、临时输出、数据库备份、构建产物。
- GitLab 与 GitHub main 提交一致，标签指向发布提交。
- Jenkins Checkout、部署和 `HEALTH_OK` 成功。
- 线上服务广场可打开、缓存入口秒开、事件与静默接口可用。

## 13. 允许修改与禁止触碰

### 13.1 预计允许修改

- `backend/app/plaza.py`
- `backend/app/service_health.py`
- `backend/app/models.py`
- `backend/app/main.py`（仅启动、迁移或路由接线所需）
- `backend/app/alerting.py`（仅复用/抽取通用通知与保留策略）
- `backend/tests/test_plaza.py`
- `backend/tests/test_plaza_profile_api.py`
- `backend/tests/test_service_health.py`
- 新增明确命名的 reliability/incident 测试或服务模块
- `frontend-vite/src/views/ServicePlaza.vue`
- `frontend-vite/src/components/ServiceDetailDrawer.vue`
- `frontend-vite/src/router.js`
- 新增服务健康页面与局部样式
- 版本文件、README、必要的发布配置

### 13.2 未经用户确认禁止修改

- `backend/app/service_catalog.json` 中的服务清单和地址。
- 生产凭证、SSH 配置、Webhook、JWT/CREDENTIAL 密钥。
- 生产主机 systemd、Caddy、Docker、Loki、Alloy 配置，除非实现本期功能确实需要且先有明确证据。
- 监控采集频率、日志 365 天保留、容器 stats 的按需策略。
- Jenkins SCM 地址和凭据；当前已修复为从 GitLab main 检出。
- 数据库中现有业务记录和历史探测结果。
- 1Panel 源码或其 GPL 实现代码。

## 14. 接手 AI 强制工作协议

接手 AI 每次开始必须执行：

1. 找到仓库并读取 `git status`、当前提交、版本、远端；不得假设仍是本文基线。
2. 搜索 `AGENTS.md`、项目 README 和相关测试；存在新约束时以更新后的明确指令为准并记录差异。
3. 先搜索再改：任何模型、路由、函数、组件都先用 `rg` 定位；不得凭名称新建重复实现。
4. 列出“已存在/需新增/不在范围”三栏，确认后只实施当前阶段。
5. 每个阶段先写或更新测试，再进行最小范围改动，并运行对应测试。
6. 不确定字段、接口、部署状态时必须查源码、OpenAPI、数据库 schema 或流水线输出；查不到就明确标注未知，不能编造。
7. 保留用户已有改动；不得执行 `git reset --hard`、覆盖式 checkout、清理整个工作树或删除不明文件。
8. 不直接编辑线上文件来代替代码提交和流水线部署。
9. 不宣称“已上线/流水线成功/测试通过”，除非提供命令或流水线证据。
10. 结束时报告：改动文件、关键设计、测试命令和结果、提交哈希、双远端状态、流水线状态、已知限制和下一步。

遇到以下情况必须暂停相关动作并向用户报告：

- 当前 main 已高于 4.5.1 且核心模型/API 与本文冲突。
- 工作树存在与目标文件重叠的未知未提交改动。
- 需要更换/读取/暴露任何密钥。
- 需要破坏性数据库迁移、删除历史数据或修改生产网络。
- 发现必须跨出 v4.6.0 范围才能完成核心验收。

## 15. 回滚方案

- Jenkins 部署前已有按时间创建的生产备份；发布时记录本次备份目录，但不要在公开文档记录凭证。
- 代码回滚目标为上一稳定标签 `v4.5.1` 或发布前 main 的明确提交，禁止模糊使用“上一个版本”。
- 新增表/列为兼容性扩展，代码回滚后保留，不立即删表。
- 如 v4.6.0 仅前端故障，可回滚前端静态产物并保留后端兼容接口。
- 如探测/告警出现风暴，优先关闭 plaza 通知或调度开关，保留事件数据；不要删除历史记录。
- 回滚后重新验证版本接口、服务广场入口、健康接口和 Jenkins `HEALTH_OK`。

## 16. v4.6.0 完成定义

以下全部满足才可宣布完成：

- 双探测已消除，并有测试证明。
- 持久化状态机、事件、阈值、静默、确认和通知幂等全部实现。
- 服务广场总览/详情和事件管理入口可用，入口速度无回退。
- 安全、性能、重启恢复、并发与保留策略均通过验收。
- 全量后端测试、前端生产构建、OpenAPI 检查通过。
- 版本统一为 4.6.0，GitLab/GitHub 同提交，标签正确。
- Jenkins 按正确提交部署并线上核验成功。
- 飞书“OpsCenter 当前状态”另行更新为实际结果；本计划文档保留为实施审计，不把未完成项改写成已完成。

## 17. 后续候选路线（未经确认不得实施）

- v4.7 候选：通知渠道与路由中心（飞书/邮件等）、升级策略、值班与通知模板。
- v4.8 候选：受控自愈编排、处置审批、操作留痕和回滚。
- 更后续：细粒度 RBAC、多租户、SLO/错误预算、容量预测。

这些仅是方向，不是已批准需求。v4.6.0 验收前不得提前开发。

