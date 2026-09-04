# OpsCenter v4.8.0 体验、WireGuard 拓扑与健康大屏开发交接计划

> 文档用途：交给后续 AI 或开发者直接实施。本文基于 2026-09-03 的本地源码和 PVE 生产环境只读核验编写。
>
> 当前基线：Git commit `5a5f357`（`fix(stability): finish PVE migration cleanup`），线上版本 v4.7.0，PVE 工作台地址 `http://10.66.66.3:8088`。
>
> 本文只定义 v4.8.0 候选范围，当前阶段不得提前修改版本号、推送仓库或部署生产。完成全部验收后才统一升级为 v4.8.0。

## 1. 用户目标与锁定范围

本期完成五组需求：

1. 删除云主机时立即显示操作反馈，并让删除接口快速返回。
2. 系统终端支持多标签同时打开，延长临时断线后的可重连时间。
3. 拓扑架构新增 WireGuard 内网视图，展示 WG IP 分布、主机映射、链路健康与内网流量。
4. 调整左侧导航层级：容器、数据库进入“系统”；日志中心移动到导航最下方；服务健康进入“服务广场”下拉菜单。
5. 优化健康大屏，统一展示主机、容器、数据库、服务、日志、WG 和告警状态，减少重复请求与无意义刷新。

本期不做：WireGuard 配置编辑、Peer 增删、自动改路由、数据库备份恢复、容器实时 `docker stats`、每个 WG Peer 的长期历史入库、告警规则重构。后续若要做，必须另立需求。

## 2. 已核验的当前情况

### 2.1 主机删除并非失败，而是反馈缺失

- 前端入口：`frontend-vite/src/components/HostManagerDrawer.vue`。
- 当前实现使用 `window.prompt` 确认，点击后直接等待 `DELETE /api/v2/servers/{id}`；没有行级 loading、按钮禁用或开始提示。
- 活跃后端实现位于 `backend/app/main.py`，删除数据库记录前会同步调用 `uninstall_agent(srv)`，该 SSH 操作会阻塞 HTTP 请求。
- 生产审计显示云主机 `101.200.91.229` 已在 2026-09-03 13:41:08（北京时间）删除成功，接口返回 200，但总耗时约 `5653.61 ms`。因此用户看到的是约 5.65 秒无反馈，并非删除没有发生。
- 已有独立 Agent 卸载接口：`DELETE /api/v2/servers/{id}/agent`。资产删除不应再次隐式承担远端软件卸载。
- 当前生产主机共 6 台且均在线：L1 `10.66.66.1`、PVE `10.66.66.3`、VM1 `10.66.66.4`、VM2 `10.66.66.5`、VM3 `10.66.66.6`、VM4 `10.66.66.12`。

### 2.2 终端后端已有多会话能力，前端没有使用

- 活跃 HTTP 路由在 `backend/app/main.py`，不要把 `backend/app/routers/terminal.py` 当作实际入口。
- 会话实现位于 `backend/app/ssh_terminal.py`。
- 当前后端限制为每台主机最多 5 个会话，空闲超时 3600 秒，WebSocket 断开后的重连宽限期 30 秒。
- `frontend-vite/src/views/SystemTerminal.vue` 只维护一个 `sessionId`；切换主机会丢弃当前引用。
- `frontend-vite/src/components/TerminalPanel.vue` 已具备 xterm、重连和 SFTP 能力，应复用，不重写终端组件。

### 2.3 当前拓扑没有 WireGuard 场景

- 后端 `backend/app/topology.py` 只允许 `cicd`、`monitoring`、`gateway` 三种场景，内容是服务关系图。
- 前端 `frontend-vite/src/views/Topology.vue` 也只有对应三个按钮。
- Agent 已采集 `/proc/net/dev` 中的所有接口，`wg0` 已包含在网络接口数据里；`NetworkStats` 也已按接口保存每日历史。因此 WG 总流量和历史应复用现有网络监控，不新建重复采集链路。
- 当前 Agent 版本为 2.5.1；新增 WG 只读接口后建议升级到 2.6.0，但必须保持旧 Agent 可用。

生产环境只读观察到的 WireGuard 结构：

| WG IP | 当前识别 | 当前健康情况（观察时刻） |
|---|---|---|
| `10.66.66.1` | L1，中心节点，公网端点 `182.92.223.237:51820` | 正常 |
| `10.66.66.2` | 管理端设备，未登记为工作台主机 | 最近约 2 分钟有握手 |
| `10.66.66.3` | PVE | 最近约 2 分钟有握手 |
| `10.66.66.4` | VM1 | 最近约 2 分钟有握手 |
| `10.66.66.5` | VM2 | 最近约 2 分钟有握手 |
| `10.66.66.6` | VM3 | 最近约 2 分钟有握手 |
| `10.66.66.7` | 未纳管 Peer | 最近握手约 7.26 天前 |
| `10.66.66.8` | 未纳管 Peer | 从未握手 |
| `10.66.66.9` | 未纳管 Peer | 从未握手 |
| `10.66.66.10` | 未纳管 Peer | 最近握手约 5.31 天前 |
| `10.66.66.11` | 未纳管 Peer | 从未握手 |
| `10.66.66.12` | VM4 | 最近约 2 分钟有握手 |
| 无 Allowed IP | 未配置完整的 Peer | 从未握手 |

以上只是实施前快照，不能硬编码进代码。运行时必须以 L1 Hub 的只读 WireGuard 状态为准，并按 IP 动态映射资产表。

### 2.4 导航层级仍是平铺结构

当前 `frontend-vite/src/App.vue` 中：服务广场、服务健康、数据库、容器和日志中心均为一级菜单；只有“系统”使用硬编码的 `systemOpen` 展开状态。路由已经存在，调整菜单时不应改变原 URL，也不应新增页面副本。

### 2.5 健康大屏请求过重且会重叠

- 页面：`frontend-vite/src/views/Screen.vue`。
- 当前每 5 秒调用一次 `/servers`、对每台主机调用 `/servers/{id}/monitor`、再调用服务健康和告警接口。
- 每 30 秒又对多台主机请求历史数据。页面没有可见性判断、统一 AbortController 和请求互斥；慢请求时可能叠加。
- 页面把部分失败转换成空值，但仍更新时间，容易把“无数据”误展示成“正常或 0”。
- 已有聚合接口 `/api/v2/metrics/hosts/overview`，也已有 `/api/v2/screen/summary`，后者当前仍逐主机逐指标查询，尚未被前端有效利用。
- Agent 实时数据已经包含 `container_running`、`container_stopped`，健康大屏不需要请求容器详情，更不应调用 `docker stats`。
- 生产现状：6 台主机在线；日志采集 6/6 新鲜；服务广场 18 项中 17 在线、1 离线，当前离线项是 VM2 旧地址 `http://10.66.66.5:9090/` 的 Prometheus（连接被拒绝）；数据库实例为 0。实现时应如实显示，不得为了大屏全绿而隐藏异常。

## 3. 实施原则与禁止事项

后续 AI 必须遵守：

- 先在独立分支开发，先测试后改版本号；不得直接在生产数据库试删主机。
- 只修改真正挂载的代码。主机删除和终端活跃入口在 `backend/app/main.py`；不要误改未挂载的 `backend/app/routers/servers.py` 或 `backend/app/routers/terminal.py` 后宣称完成。
- 复用现有 `Modal.vue`、`TerminalPanel.vue`、ECharts、网络历史、指标聚合、服务广场健康接口；不得新增终端、图表或图数据库依赖。
- 不复制 1Panel 的 GPL 源码，只参考信息架构与交互层级。
- 任何 API、日志、审计、缓存、测试快照都不得返回或记录 WireGuard 私钥、预共享密钥、SSH 密码或 Token。
- WireGuard 只读采集必须通过 Agent 完成；浏览器不能直接探测内网 Peer，也不能执行任意 shell 命令。
- 健康大屏禁止调用 `docker stats`，禁止每轮对所有主机逐一请求容器详情，禁止隐藏异常或把缺失数据当 0。
- 不改变既有路由 URL：`/`、`/service-health`、`/container`、`/database`、`/logs` 等继续有效，避免书签失效。
- 不在本期建立 WG Peer 历史表；现有接口和 30 秒内存缓存足以满足实时拓扑。若未来明确要求逐 Peer 长期趋势，再单独设计。
- Agent 升级必须采用兼容顺序：先部署能兼容旧 Agent 的后端和前端，再分批升级 Agent；单台升级失败不得拖垮拓扑页面。
- 不删除现有服务记录或修改生产 WG 配置来“修测试”。Prometheus 离线是待处理的真实告警。

## 4. 详细开发方案

### 4.1 P0：主机删除反馈与快速返回

#### 后端

修改 `backend/app/main.py` 中实际的 `DELETE /api/v2/servers/{server_id}`：

1. 保留本地主机禁止删除、UUID 校验、404、审计和关联数据清理。
2. 删除资产时不再调用 `uninstall_agent(srv)`。该行为已有独立接口，应由用户明确触发。
3. 在一个短数据库事务中删除主机及依赖记录，并清理主机组映射。
4. 若非关键的主机组 JSON 清理失败，应返回 `warnings` 并记录错误，而不是让已经提交的数据库删除显示失败。
5. 返回明确结果，例如：

```json
{
  "ok": true,
  "message": "主机已从资产中删除；远端 Agent 未卸载",
  "deleted": {"servers": 1, "services": 3},
  "agent_uninstalled": false,
  "warnings": []
}
```

目标：正常删除接口服务端耗时小于 500 ms。远端 Agent 卸载继续通过 `DELETE /api/v2/servers/{id}/agent` 单独执行。

#### 前端

修改 `frontend-vite/src/components/HostManagerDrawer.vue`：

- 用现有通用 `Modal.vue` 替换 `window.prompt`，要求输入主机名称确认。
- 增加 `deletingId`；确认后立即显示“正在删除主机…”，当前行按钮显示“删除中…”并禁用，防止双击。
- 成功提示必须使用后端 `message`，同时刷新主机列表；若删的是当前主机，切换到本地主机或列表第一台。
- 失败时恢复按钮、保留列表并显示后端错误；网络超时不能乐观移除。
- 文案必须说明“删除资产不会卸载远端 Agent；如需卸载请先执行 Agent 卸载”。

### 4.2 P0：导航重排

修改 `frontend-vite/src/App.vue`，目标结构如下：

```text
服务广场
  ├─ 服务列表          /
  └─ 服务健康          /service-health
系统
  ├─ 监控              /system/monitor
  ├─ 容器              /container
  ├─ 数据库            /database
  ├─ 文件              /system/files
  ├─ 终端              /system/terminal
  ├─ 防火墙            /system/firewall
  ├─ SSH 管理          /system/ssh
  └─ 进程管理          /system/processes
健康大屏               /screen
拓扑架构               /topology
告警中心               /alerts
开放 API               /api-keys
日志中心               /logs            （最下方）
```

实现要求：

- 从一级菜单移除容器、数据库和服务健康，但只移菜单项，不删路由。
- 将当前单一 `systemOpen` 改成可复用的分组展开状态，如 `openGroups = reactive({ plaza: true, system: true })`。
- 路由变化时自动展开所属分组；根路径 `/` 必须精确匹配，不能让所有路径都激活服务列表。
- 桌面折叠与移动端导航都要保持可用；页面刷新后仍能正确高亮。
- 不建立新的布局组件或状态库，仅在现有导航数据结构上做最小调整。

### 4.3 P1：多标签终端和更长重连时间

#### 前端会话模型

修改 `frontend-vite/src/views/SystemTerminal.vue`：

```ts
type TerminalTab = {
  sessionId: string
  serverId: string
  serverName: string
  title: string
  status: 'connecting' | 'connected' | 'disconnected' | 'closed' | 'error'
  createdAt: string
}
```

- 用 `sessions[]` 和 `activeSessionId` 代替单个 `sessionId`。
- 顶部显示终端标签、“+ 新建终端”和关闭按钮；默认标题为“主机名 · 终端 N”，允许当前会话内改名。
- 新建会话使用顶栏当前选中的主机；切换全局主机只影响下一次新建，不关闭已经打开的会话。
- 多个 `TerminalPanel` 保持挂载，以 `v-show` 切换；为组件增加激活通知并调用 xterm `fit()`，避免切回标签尺寸错误。
- 使用 `sessionStorage` 仅保存会话 ID 和标签元数据。页面重载后先查询状态，再尝试重连；不得把 WebSocket 内容、密码或 Token 持久化。
- 关闭标签时调用后端显式销毁接口；关闭一个标签不得影响其他标签。

#### 后端会话生命周期

修改 `backend/app/ssh_terminal.py` 和 `backend/app/main.py`：

- 保持 `MAX_SESSIONS_PER_SERVER = 5`，不盲目提高资源上限。
- 将 `RECONNECT_GRACE` 从 30 秒改为 300 秒。
- 将 `SESSION_TIMEOUT` 从 3600 秒改为 14400 秒（4 小时无活动）。
- WebSocket 支持应用层 `{"type":"ping"}`；收到后仅更新 `last_activity`，不能把字符串写入 shell。前端每 25 秒发一次 ping。
- 新增幂等接口 `DELETE /api/v2/terminal/sessions/{session_id}`，调用现有会话清理函数；不存在或已关闭时也返回可理解结果。
- 状态接口应返回 `server_id`、`created_at`、`last_activity`、`reconnect_deadline` 和状态，不返回任何凭证。
- 页面卸载只断开 WebSocket，不自动销毁可重连会话；用户点击标签关闭才显式销毁。到达空闲超时由服务端清理。

#### 终端安全边界

- 保留现有容器终端参数校验；任何容器名、会话 ID 都不能拼接进未经验证的 shell 命令。
- 服务端断连、SSH 断连和会话超时需要不同提示。
- 不在浏览器控制台输出 WebSocket 数据帧。

### 4.4 P1：WireGuard 内网拓扑

#### Agent 2.6.0 只读接口

在现有 Agent 中新增 `GET /api/v1/wireguard`，继续使用 Bearer Token。使用 Python 标准库 `subprocess` 调用只读命令并设置 3 秒超时；优先解析稳定的机器格式，不解析面向人的本地化文本。

建议响应：

```json
{
  "supported": true,
  "generated_at": "2026-09-03T07:30:00Z",
  "interfaces": [{
    "name": "wg0",
    "addresses": ["10.66.66.1/24"],
    "listen_port": 51820,
    "public_key_fingerprint": "sha256:ab12cd34ef56",
    "peers": [{
      "public_key_fingerprint": "sha256:98ab76cd54ef",
      "endpoint": "203.0.113.10:52128",
      "allowed_ips": ["10.66.66.3/32"],
      "latest_handshake_at": "2026-09-03T07:29:15Z",
      "latest_handshake_age_seconds": 45,
      "rx_bytes": 123456,
      "tx_bytes": 654321
    }]
  }]
}
```

严格要求：

- 若读取 `wg show all dump`，第一列可能含接口私钥，Peer 行可能含预共享密钥；解析器必须立刻丢弃，绝不能进入返回对象、日志或异常文本。
- 公钥默认只返回不可逆短指纹，不返回完整公钥；前端只用指纹做识别。
- 未安装 `wg`、权限不足或没有接口时返回 `supported: false` 和结构化原因，不得 500。
- 地址读取失败不影响 Peer 列表返回。

#### 后端聚合

扩展现有 `GET /api/v2/topology?scenario=wireguard`，不要建立第二套拓扑路由：

- 在 `backend/app/topology.py` 增加 `wireguard` 场景分支。
- 从资产表读取主机快照后关闭 DB 会话，再使用标准库 `ThreadPoolExecutor` 并发请求各主机 Agent；最大 6 个 worker，单主机超时 5 秒。
- 按主机分别缓存 30 秒。某台超时只能标记该节点数据陈旧，不能阻塞或清空整个拓扑。
- 以 Hub 的 `AllowedIPs` 为权威 IP 分布，将精确 `/32` IP 与 `Server.host` 匹配；匹配不到的显示“未纳管 Peer”。不要根据主机名猜测。
- 节点类型只需 `hub`、`managed_host`、`unregistered_peer`；节点 ID 使用服务器 UUID 或安全指纹，保持刷新前后稳定。
- 响应沿用现有 `nodes`、`edges`，增加 `summary`、`generated_at`、`cached`、`partial_errors`，保持其他三种场景兼容。
- 复用现有 `wg0` 网卡历史接口显示总体内网 RX/TX；当前 Peer 累计流量来自 WG 接口，可利用相邻 30 秒缓存快照计算临时速率。必须标注“累计值在接口重启后可能归零”。

健康规则锁定为：

| 条件 | 已纳管 Peer 状态 | 未纳管 Peer 展示 |
|---|---|---|
| 最近握手 `<= 180s` | 健康 | 活跃、未纳管 |
| `181–600s` | 警告 | 不活跃、未纳管 |
| `> 600s` 或从未握手 | 离线 | 不活跃、未纳管 |
| Agent 不支持/超时 | 未知 | 未知 |

未纳管 Peer 默认不生成告警事件，避免 `.7-.11` 等历史配置造成持续噪音；但必须在拓扑和汇总中可见。

#### 前端拓扑

修改 `frontend-vite/src/views/Topology.vue`：

- 新增“WireGuard 内网”场景按钮，继续使用现有 ECharts 图，不引入图组件。
- 顶部汇总：Peer 总数、已纳管、健康、警告、离线、未纳管、当前 WG RX/TX。
- 图中 Hub 居中，健康/警告/离线/未知分别使用现有语义色；线宽可按近期流量分级，但不能造成动画持续高占用。
- 右侧或抽屉详情显示：WG IP、资产主机、端点、最后握手、距今时间、Allowed IP、累计 RX/TX、当前估算速率、数据时间。
- 提供“全部 / 已纳管 / 异常 / 未纳管”过滤；默认全部。
- 默认不显示完整公钥，不提供私钥、配置下载或编辑入口。
- 旧 Agent 显示“当前 Agent 不支持 WG 详情，请升级”，其他拓扑仍正常。

### 4.5 P1：健康大屏资源总览与请求治理

#### 聚合接口

重构已有 `backend/app/topology.py` 中的 `GET /api/v2/screen/summary`，保留旧字段以兼容现有调用方，同时新增：

```json
{
  "generated_at": "2026-09-03T07:30:00Z",
  "freshness": {"metrics_at": "...", "services_at": "...", "wireguard_at": "..."},
  "partial_errors": [],
  "hosts_summary": {"total": 6, "online": 6, "offline": 0, "stale": 0},
  "containers_summary": {"running": 0, "stopped": 0, "unknown_hosts": 0},
  "databases_summary": {"total": 0, "connected": 0, "pending": 0, "error": 0},
  "services_summary": {"total": 18, "up": 17, "down": 1, "incidents": 1},
  "logs_summary": {"total": 6, "fresh": 6, "stale": 0, "abnormal": 0},
  "wireguard_summary": {"managed": 6, "healthy": 6, "warning": 0, "offline": 0, "unmanaged": 6},
  "alerts_summary": {"firing": 1, "acknowledged": 0},
  "servers": [],
  "services": [],
  "active_alerts": [],
  "trends": {}
}
```

实现约束：

- 主机最新 CPU、内存和磁盘必须用分组查询或复用 `metrics_history` 的聚合帮助函数，不得在循环内每指标执行一次 SQL。
- 容器计数来自最近 Agent 指标或缓存；如当前历史表未持久化容器计数，可在聚合缓存中保留最近一次 Agent 摘要。不得为了大屏调用容器列表或 SSH 回退。
- 服务汇总复用服务广场稳定健康快照，不主动执行探活。
- 日志汇总复用日志 Agent overview，不在大屏触发探测。
- 数据库只做实例元数据状态聚合；无实例时 `total: 0` 是正常状态，不是错误。若现有数据库模块没有聚合帮助函数，仅新增一个简单 SQL 聚合函数，不逐主机调用 HTTP。
- WG 汇总复用 30 秒拓扑缓存，不重复访问 Agent。
- 数据新鲜度超过 30 秒时标记 `stale`；数据缺失返回 `null` 或 `unknown`，绝不能伪造为 0。
- 保持当前 `require_api_key("read")` 兼容语义：工作台无令牌可访问，带无效令牌仍返回 401；不得为大屏削弱 `/ai/context` 的强制密钥要求。
- 聚合接口总耗时目标：缓存命中 `< 300 ms`，冷查询 `< 1.5 s`；慢子模块通过 `partial_errors` 降级。

#### 前端刷新协调

重构 `frontend-vite/src/views/Screen.vue`：

- 首屏使用一次 `/api/v2/screen/summary`，替换当前“主机列表 + N 个 monitor + 多个 overview”的浏览器扇出。
- 核心摘要每 10 秒刷新；趋势每 60 秒刷新，或只在时间范围/主机选择变化时请求。
- 使用单个 AbortController 和 `requestInFlight` 锁：新一轮不得与上一轮重叠；组件卸载立即取消。
- `document.visibilityState !== 'visible'` 时停止定时请求，恢复可见后立即刷新一次。
- 只有成功收到核心响应才更新“最后刷新时间”；局部失败显示 `partial_errors` 提示，不清空上一份可用数据。
- 趋势仅展示用户选中的主机或风险最高的最多 3 台，提供 1h/6h/24h 与 CPU/内存/磁盘/网络切换，复用现有 `/servers/{id}/metrics/timeseries`。

大屏资源区至少包含：

- 主机：在线/总数/数据陈旧，CPU、内存、磁盘风险前三。
- 容器：运行、停止、未知主机；点击进入 `/container`。
- 数据库：已连接、待接入、异常；点击进入 `/database`。
- 服务：在线、离线、活跃事件；点击进入 `/service-health`。
- 日志：采集正常、陈旧、异常；点击进入 `/logs`。
- WG：已纳管健康/警告/离线、未纳管；点击进入 `/topology?scenario=wireguard`。
- 告警：触发中、已确认；点击进入 `/alerts`。

布局使用现有卡片与 ECharts，桌面最多三列并响应式降列。异常必须优先排序且附带数据时间；没有配置数据库时显示“暂无实例”，不能显示红色故障。

## 5. 分阶段实施顺序

### 阶段 0：安全基线

1. 从 `5a5f357` 创建功能分支。
2. 执行现有后端测试、前端生产构建并记录基线。
3. 备份测试数据库；所有删除测试使用临时数据库和伪造 SSH 函数。
4. 记录线上 6 台主机、18 个服务、Agent 版本和 WG 快照，只读即可。

### 阶段 1：快速体验修复

1. 主机删除改为快速库存删除。
2. 增加前端删除进度和结构化确认。
3. 完成导航重排与路由高亮测试。

阶段验收通过后单独提交，例如：`fix(ui): improve host deletion feedback and navigation`。

### 阶段 2：终端多标签

1. 先补服务端 ping、关闭接口和生命周期测试。
2. 再将前端改为标签模型并加 sessionStorage 恢复。
3. 用同主机双终端、不同主机双终端验证隔离。

提交建议：`feat(terminal): support reconnectable multi-tab sessions`。

### 阶段 3：WireGuard

1. 先开发和测试 Agent 只读解析器，确认所有敏感字段测试为不存在。
2. 后端增加兼容旧 Agent 的聚合、缓存和映射。
3. 前端接入 WG 场景。
4. 测试环境部署顺序：后端/前端 → L1 Agent → PVE/VM Agent 分批升级。

提交建议：`feat(topology): add secure WireGuard health view`。

### 阶段 4：健康大屏

1. 优化已有 summary SQL 和聚合缓存。
2. 接入服务、日志、数据库、容器和 WG 汇总。
3. 前端替换 N+1 请求并加入可见性/取消/互斥控制。
4. 使用浏览器网络面板完成 60 秒请求预算验收。

提交建议：`perf(screen): consolidate resource health overview`。

### 阶段 5：统一验收与发布

1. 后端完整测试、前端生产构建、OpenAPI 契约测试、敏感信息扫描。
2. 在独立预览环境验收；不得先在生产试错。
3. 验收通过后统一更新所有可见版本位置、包版本和 Agent 版本：工作台 v4.8.0，Agent v2.6.0。
4. 推送 Git 仓库，监督 CI；按“数据库备份 → 后端兼容版本 → 前端 → Agent 分批 → 冒烟测试”部署。
5. 出现严重问题只回滚应用镜像；数据库变更应保持向后兼容。本期原则上无需新表迁移。

## 6. 必须完成的测试与验收

### 6.1 主机删除

- 点击确认后 100 ms 内出现“删除中”反馈，按钮不可重复点击。
- 正常远程主机删除 API `< 500 ms`；验证未调用 `uninstall_agent`。
- 本地主机返回 400；不存在或非法 ID 返回清晰 404/422，不得 500。
- 关联服务、数据库实例和主机组映射按既有约束清理；审计不含凭证。
- 接口失败时前端保留主机，成功删除当前主机时自动切换上下文。

### 6.2 终端

- 同一主机和不同主机各开两个标签，输入输出互不串线。
- 单台第 6 个会话被明确拒绝；其他主机不受影响。
- 断网后 5 分钟内可重连，超过宽限期显示会话已过期。
- 连续使用 4 小时不应被清理；真正 4 小时无活动才超时。
- ping 只更新活动时间，不得出现在 shell 输入或输出。
- 关闭一个标签只销毁对应会话；刷新页面能恢复仍在宽限期内的标签。
- 前端 build 通过，切回隐藏标签后终端尺寸正确。

### 6.3 WireGuard

- 单元测试输入包含私钥和预共享密钥，序列化响应、日志捕获和异常文本中均不得出现原值。
- `/32` 与资产 IP 精确匹配；重复/非 `/32` Allowed IP 不得随意认领主机。
- 180 秒、181 秒、600 秒、601 秒和从未握手边界状态正确。
- 一台 Agent 超时或版本过低时，其余节点 5 秒内正常返回。
- 30 秒缓存生效，缓存命中不重复请求 Agent。
- 未纳管 Peer 可见但默认不生成事故；`.7-.11` 的历史 Peer 不得让全站告警永久爆红。
- `wg0` 历史流量与系统监控一致；接口重启导致累计值下降时速率按 0 处理，不产生负数。

### 6.4 导航

- 容器、数据库仅出现在系统分组；服务健康仅出现在服务广场分组；日志中心是最后一个一级菜单。
- 所有旧 URL 可直接打开，刷新后分组自动展开且高亮准确。
- 桌面折叠和移动端导航不遮挡内容。

### 6.5 健康大屏

- 页面停留 60 秒时无重叠请求、无 `docker stats`、无 N 台主机容器详情请求。
- 页面隐藏 60 秒不产生周期请求；恢复后只补一次刷新。
- 温缓存首屏 `< 1 s`，冷查询 `< 2 s`；summary 服务端目标见前文。
- 某台主机或某子模块失败时保留其他数据，显示“部分数据异常”和时间戳。
- 陈旧数据标记 stale，缺失指标显示 `--`，不得显示成 0%。
- 数据库实例为 0 时显示“暂无实例”；当前 Prometheus 离线仍应显示 1 项服务异常。

### 6.6 全量发布门禁

- 后端：全部 pytest 通过。
- 前端：生产构建通过，无新增高危依赖。
- OpenAPI：新增/修改接口契约测试通过，旧字段仍存在。
- 安全扫描：响应、日志和审计中无 WG 私钥、PSK、SSH 密码、Agent Token。
- 冒烟：主机切换、服务广场、服务健康、系统监控、容器、数据库、终端、拓扑、健康大屏、日志中心全部可访问。
- Agent：6 台主机逐台确认版本和状态；失败一台时停止继续批量升级并排查，不回滚已兼容的后端。

## 7. 建议修改文件清单

| 文件 | 任务 |
|---|---|
| `frontend-vite/src/components/HostManagerDrawer.vue` | 删除确认、行级进度、错误与成功反馈 |
| `frontend-vite/src/App.vue` | 两个下拉分组及导航顺序 |
| `frontend-vite/src/views/SystemTerminal.vue` | 多标签会话模型与恢复 |
| `frontend-vite/src/components/TerminalPanel.vue` | 激活后 fit、ping、状态回传和关闭协作 |
| `frontend-vite/src/views/Topology.vue` | WG 场景、过滤、汇总和详情 |
| `frontend-vite/src/views/Screen.vue` | 聚合加载、刷新协调、资源卡片和趋势 |
| `backend/app/main.py` | 活跃主机删除、终端会话 API |
| `backend/app/ssh_terminal.py` | 超时、重连、ping 与显式清理 |
| `backend/app/topology.py` | WG 聚合、缓存、优化 screen summary |
| Agent 当前实现文件 | `/api/v1/wireguard` 只读采集与版本 2.6.0 |
| 后端/前端现有测试目录 | 按第 6 节补齐测试 |

不要为了“整理”同时迁移 `main.py` 里其他路由，也不要在本期清除重复的未挂载 router 文件；这是独立技术债，混入本次会扩大回归面。

## 8. 完成定义（Definition of Done）

只有同时满足以下条件，后续 AI 才能声明任务完成：

1. 五组需求均有实际代码、测试和可访问预览，不是只有静态页面。
2. 删除反馈和接口耗时达到目标，且资产删除不再隐式 SSH 卸载 Agent。
3. 终端多标签真实并发可用，断线 5 分钟内可重连，4 小时无活动才清理。
4. WG 拓扑动态展示 `.1-.12` 实际分布及健康状态，不硬编码、不泄露密钥。
5. 新导航层级正确，旧链接不失效。
6. 健康大屏不再产生 N+1 轮询和重叠请求，缺失/陈旧/异常语义准确。
7. 全部自动化测试、构建、安全检查和 PVE 冒烟通过。
8. 最后才将工作台版本更新为 v4.8.0、Agent 更新为 v2.6.0，并提交发布说明、commit、部署结果和回滚点。

## 9. 后续 AI 每次汇报必须包含

- 当前分支和 commit。
- 本轮实际修改文件。
- 执行过的测试命令与真实结果；未执行的测试必须直说。
- 当前环境（本地、预览、生产）以及是否产生数据变更。
- 剩余项和阻塞项。
- 若部署：镜像/版本、6 台 Agent 状态、健康大屏关键数据、回滚点。

禁止使用“应该正常”“大概完成”“理论可用”代替验证证据。
