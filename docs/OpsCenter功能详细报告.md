# OpsCenter v3.15.0 功能详细报告

> 生成日期：2026-07-11
> 分析对象：MFA 服务器（101.200.91.229）上部署的 OpsCenter 运维工作台
> 版本：v3.15.0

---

## 目录

- [一、项目概览](#一项目概览)
- [二、整体架构](#二整体架构)
- [三、功能模块详解](#三功能模块详解)
  - [3.1 服务导航](#31-服务导航)
  - [3.2 监控中心](#32-监控中心)
  - [3.3 资源管理](#33-资源管理)
  - [3.4 SSH 终端](#34-ssh-终端)
  - [3.5 SFTP 文件管理](#35-sftp-文件管理)
  - [3.6 Agent 管理](#36-agent-管理)
  - [3.7 服务发现](#37-服务发现)
  - [3.8 分组管理](#38-分组管理)
  - [3.9 健康检查](#39-健康检查)
  - [3.10 凭证管理](#310-凭证管理)
- [四、后端代码详解](#四后端代码详解)
  - [4.1 main.py — 主服务](#41-mainpy--主服务)
  - [4.2 discovery.py — 服务发现引擎](#42-discoverypy--服务发现引擎)
  - [4.3 models.py — 数据模型](#43-modelspy--数据模型)
  - [4.4 ssh_manager.py — SSH管理](#44-ssh_managerpy--ssh管理)
  - [4.5 ssh_terminal.py — SSH终端](#45-ssh_terminalpy--ssh终端)
  - [4.6 agent_manager.py — Agent管理](#46-agent_managerpy--agent管理)
- [五、Agent 代码详解](#五agent-代码详解)
  - [5.1 opsagent.py — Agent主程序](#51-opsagentpy--agent主程序)
  - [5.2 scanner.py — 扫描器](#52-scannerpy--扫描器)
- [六、前端代码详解](#六前端代码详解)
  - [6.1 index.html — 主前端SPA](#61-indexhtml--主前端spa)
  - [6.2 app.js — 旧版主逻辑](#62-appjs--旧版主逻辑)
  - [6.3 api.js — API封装层](#63-apijs--api封装层)
  - [6.4 config.js — 前端配置](#64-configjs--前端配置)
  - [6.5 tools.js — 工具函数库](#65-toolsjs--工具函数库)
  - [6.6 terminal-sim.js — 终端模拟器](#66-terminal-simjs--终端模拟器)
- [七、API 端点完整清单](#七api-端点完整清单)
- [八、数据库设计](#八数据库设计)
- [九、部署架构](#九部署架构)
- [十、版本演进历史](#十版本演进历史)
- [十一、已知问题与改进建议](#十一已知问题与改进建议)

---

## 一、项目概览

### 1.1 项目定位

OpsCenter 是一个**一站式运维工作台**（Operations Center），为运维工程师提供统一的服务导航、服务器监控、资源管理和远程终端能力。它将分散在各台服务器上的 Docker 容器、systemd 服务、Nginx 路由等自动发现并集中展示，实现了"一个入口管理所有服务"的目标。

### 1.2 核心价值

| 能力 | 描述 |
|------|------|
| **自动服务发现** | 三层架构（Agent > SSH > Docker SDK），自动发现 Docker 容器、监听端口、systemd 服务、Nginx 路由 |
| **实时监控** | Agent 每 30 秒采集 CPU/内存/磁盘/网络/磁盘IO/负载等 13 项指标，ECharts 历史趋势图 |
| **远程终端** | xterm.js + WebSocket 双向隧道，支持多标签、断线重连、SFTP 文件管理 |
| **Agent 生命周期** | 一键部署/升级/卸载远程 Agent，自动 SSH 连接 + SFTP 上传 + systemd 注册 |
| **服务凭证管理** | 为每个服务存储登录账号/密码，一键复制 |

### 1.3 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.10 + FastAPI + SQLAlchemy + PostgreSQL 16 |
| 前端 | Vue 3 Composition API + xterm.js + ECharts（零构建单文件 SPA） |
| Agent | Python 3 标准库（零外部依赖），http.server + /proc 文件系统 |
| 部署 | systemd 服务 + Nginx 反向代理 + Docker SDK |
| 通信 | REST API + WebSocket（终端）+ Bearer Token 认证（Agent） |

### 1.4 代码规模

| 模块 | 文件数 | 代码行数 | 核心文件 |
|------|--------|----------|----------|
| 后端（backend） | 6 | ~2,800 行 | main.py(2425), discovery.py(263), models.py(88), ssh_manager.py(242), ssh_terminal.py(287), agent_manager.py(303) |
| 前端（frontend） | 1(active) + 5(legacy) | ~3,200 行 | index.html(2109), app.js(810), api.js(42), config.js(14), tools.js(115), terminal-sim.js(79) |
| Agent | 2 | ~730 行 | opsagent.py(477), scanner.py(252) |
| **总计** | **14** | **~6,730 行** | |

---

## 二、整体架构

### 2.1 系统架构图

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           用户浏览器                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │              index.html (Vue 3 SPA, 2109行)                         │ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐               │ │
│  │  │ 服务导航  │ │ 监控中心  │ │ 资源管理  │ │ SSH终端   │               │ │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘               │ │
│  │       ↑ REST API              ↑ REST API      ↑ WebSocket          │ │
│  └───────┼───────────────────────┼───────────────┼─────────────────────┘ │
└──────────┼───────────────────────┼───────────────┼─────────────────────┘
           │                       │               │
     ┌─────┴───────────────────────┴───────────────┴─────┐
     │              Nginx 反向代理 (:80)                   │
     │  /          → frontend (静态文件)                   │
     │  /api/      → backend (:9091)                      │
     │  /ws/       → backend (:9091, WebSocket)           │
     └─────┬───────────────────────┬───────────────────────┘
           │                       │
┌──────────┼───────────────────────┼──────────────────────────────────────┐
│          │   OpsCenter 后端       │          FastAPI (:9091)             │
│          ▼                       ▼                                      │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐  ┌──────────────┐  │
│  │ 服务器管理   │  │  服务发现    │  │  监控采集    │  │  SSH终端      │  │
│  │ CRUD + 测试 │  │ 3层架构      │  │ Agent优先    │  │ WebSocket    │  │
│  └──────┬──────┘  └──────┬───────┘  └──────┬──────┘  └──────┬───────┘  │
│         │                │                  │                │          │
│         │    ┌───────────┼──────────────────┼────────────────┘          │
│         │    ▼           ▼                  ▼                           │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                    PostgreSQL 16 (:5433)                         │   │
│  │          servers / services / metric_history                     │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                    后台异步任务                                    │   │
│  │  健康检查(60s) + Agent健康(300s) + Agent指标(30s) + 自动分组     │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
                    │                           │
                    │ SSH / HTTP                 │ SSH / HTTP
                    ▼                           ▼
        ┌───────────────────┐       ┌───────────────────┐
        │  远程服务器 A      │       │  远程服务器 B      │
        │  ┌───────────────┐│       │  ┌───────────────┐│
        │  │ OpsAgent      ││       │  │ OpsAgent      ││
        │  │ (:19100)      ││       │  │ (:19100)      ││
        │  │ HTTP API      ││       │  │ HTTP API      ││
        │  └───────────────┘│       │  └───────────────┘│
        │  Docker / systemd │       │  Docker / systemd │
        └───────────────────┘       └───────────────────┘
```

### 2.2 数据流架构

```
服务注册流：
  启动 → Docker SDK 发现本机容器 → Nginx 解析 → 写入DB → 自动分组
  创建远程服务器 → SSH 连接 → 部署 Agent → Agent 扫描 → 同步到 DB

监控采集流：
  后台定时器(30s) → 遍历服务器 → Agent HTTP /metrics → 速率计算 → 写入 MetricHistory → 清理7天前数据

终端数据流：
  浏览器 xterm.js ←→ WebSocket ←→ FastAPI ←→ paramiko SSH ←→ 远程 Shell

扫描数据流：
  触发扫描 → Agent /scan(HTTP POST) → Docker ps + ss -tlnpu + systemctl → 扫描结果缓存
           → 同步到 DB: 容器/端口/systemd三类 → 自动分组分配 → 僵尸服务标记/删除
```

### 2.3 模块依赖关系

```
models.py ◄────── discovery.py ◄────── main.py
    ▲                    ▲                  ▲
    │                    │                  │
    │                    │         ┌────────┤────────┐
    │                    │         │        │        │
    ▼                    ▼         ▼        ▼        ▼
ssh_manager.py ──► agent_manager.py   ssh_terminal.py
    ▲                    │
    │                    ▼
    └──────────── Agent HTTP API ◄──── opsagent.py / scanner.py
```

---

## 三、功能模块详解

### 3.1 服务导航

**页面入口**：左侧导航栏 — "服务导航"

#### 3.1.1 功能描述

服务导航是 OpsCenter 的核心着陆页，将所有已发现的服务按分组展示为卡片网格，提供一站式服务入口。用户可通过服务卡片直接跳转到对应服务的 Web 界面。

#### 3.1.2 实现原理

**数据加载**：
- 页面挂载时调用 `GET /api/v2/services-with-status` 获取所有带状态的服务列表
- 调用 `GET /api/v2/group-config` 获取分组配置（分组定义 + 服务-分组映射）
- 数据存入 Vue 的 `reactive` 对象 `autoServices` 和 `groupConfig`

**分组过滤与排序**：
```
filteredGroups = groupConfig.groups
  .filter(g => g.id !== 'ungrouped')  // 排除"未分组"
  .map(g => ({
    ...g,
    services: autoServices
      .filter(s => getServiceGroup(s) === g.id)  // 映射服务到分组
      .filter(s => !s.hidden)                     // 排除隐藏服务
      .filter(s => matchSearch(s, searchQuery))   // 搜索过滤
      .sort((a,b) => b.pinned - a.pinned)         // 置顶优先
  }))
  .filter(g => g.services.length > 0)  // 排除空分组
```

**服务状态综合判定**：
```
effectiveStatus(service):
  1. 如果服务器离线 → "server_offline"
  2. 如果服务状态 == "down" → "down"  
  3. 如果服务状态 == "up" → "up"
  4. 否则 → "unknown"
```

**URL 生成策略**（`fullUrl(service)`）：
- 绝对路径（`http://` 或 `https://`）→ 直接使用
- 以 `#systemd:` 开头 → 标记为不可访问的 systemd 服务
- 以 `/` 开头 → 拼接服务器 IP（相对路径 Nginx 反代场景）
- 格式 `host:port` → 拼接为 `http://host:port`

**服务器 Tab 筛选**：
- 顶部显示 "全部" + 每台服务器名称的 Tab 栏
- 选择特定服务器后，只展示该服务器上的服务

**搜索功能**：
- 实时搜索，支持服务名、描述、分类的模糊匹配
- 搜索框 debounce 处理，300ms 延迟

#### 3.1.3 UI 设计

- **统计卡片**：页面顶部显示服务总数/在线数/离线数/服务器离线数
- **服务卡片**：圆角卡片，含名称+状态点(脉冲动画)、自动检测徽章、描述、容器名、URL 跳转
- **暗/亮主题**：通过 CSS 变量体系一键切换，14 个变量控制全局配色

#### 3.1.4 API 交互

| API | 用途 | 频率 |
|-----|------|------|
| `GET /api/v2/services-with-status` | 加载服务列表 | 页面切换时 |
| `GET /api/v2/group-config` | 加载分组配置 | 页面切换时 |

---

### 3.2 监控中心

**页面入口**：左侧导航栏 — "监控中心"

#### 3.2.1 功能描述

监控中心提供服务器实时指标查看和历史趋势分析。支持 CPU、内存、磁盘、网络、负载、磁盘 IO 等 13 项指标的实时监控和 ECharts 时序图展示。

#### 3.2.2 实现原理

**数据采集（后端）**：

三层采集策略，按优先级递减：

| 优先级 | 方式 | 端点 | 延迟 |
|--------|------|------|------|
| 1 | Agent HTTP API | `GET /metrics` (Agent) | ~100ms |
| 2 | SSH 远程执行 | `collect_remote_metrics()` | 8-18s |
| 3 | 本机 Docker SDK | `discover_docker_services()` | ~200ms |

**Agent 指标采集详解**（opsagent.py `collect_metrics()`）：

| 指标 | 采集方式 | 计算方法 |
|------|----------|----------|
| CPU 使用率 | 两次读 `/proc/stat`（间隔0.1s） | `(1 - idle_diff/total_diff) * 100` |
| CPU 核数 | `/proc/cpuinfo` | processor 行计数 |
| 内存使用率 | `/proc/meminfo` | `(MemTotal - MemAvailable) / MemTotal * 100` |
| 磁盘使用率 | `os.statvfs('/')` | 已用/总量 |
| 磁盘 IO | `/proc/diskstats` | 512字节扇区 → 字节转换 |
| 网络流量 | `/proc/net/dev` | 匹配 eth/ens/enp/wlan 前缀网卡 |
| 系统负载 | `/proc/loadavg` | 1/5/15 分钟 |
| 运行时间 | `/proc/uptime` | 秒数 |
| 容器统计 | `docker ps -a` | 运行/停止计数 |

**速率计算算法**：

网络和磁盘 IO 的原始值是累计值，需计算为瞬时速率：

```python
# 伪代码
last_raw = query_last_metric(server_id, "net_rx_raw")
elapsed = now - last_raw.timestamp
rate = max(0, (current_value - last_raw.value) / elapsed_seconds)

# 同时存储：
#   net_rx_raw = 当前累计值（保留1小时，用于下次速率计算）
#   net_rx = 计算出的速率值（保留7天，用于图表展示）
```

此逻辑在三个地方实现（后台采集、监控 API、Agent 指标 API），存在代码重复。

**历史趋势图**（前端）：

- 后端 `GET /api/v2/monitor/{server_id}/history?metric=&hours=` 返回 `[timestamp, value]` 数组
- 前端 ECharts 渐变面积折线图，4 种指标颜色
- 支持 8 种指标 Tab：CPU / 内存 / 磁盘 / 网络入 / 网络出 / 负载 / 磁盘读 / 磁盘写
- 支持 7 种时间范围：1h / 3h / 6h / 12h / 24h / 3天 / 7天

**数据缓存双层设计**：
- `monitorCache`：实时数据缓存，页面切换时先展示缓存再异步刷新
- `historyCache`：历史数据缓存，避免重复加载相同条件
- 15 秒自动刷新（当前页面），离开页面停止定时器

#### 3.2.3 UI 设计

- **服务器选择器**：下拉菜单选择目标服务器
- **状态标签**：在线(green)/离线(red)/未知(amber)
- **5个信息标签**：运行时间 / 核心数 / 内存 / 磁盘 / 容器数
- **3个主指标卡片**：CPU/内存/磁盘，含进度条 + 详细数值 + >80%橙色/>90%红色警告
- **5个次指标卡片**：网络入/出/负载/磁盘读/写
- **ECharts 趋势图**：指标Tab + 时间范围选择 + 渐变面积图
- **容器列表表格**：名称/镜像/状态/端口

#### 3.2.4 API 交互

| API | 用途 | 频率 |
|-----|------|------|
| `GET /api/v2/servers/{id}/monitor` | 实时指标 | 15秒轮询 |
| `GET /api/v2/servers/{id}/history` | 历史趋势 | 切换指标/时间时 |
| `GET /api/v2/servers/{id}/agent-metrics` | Agent原始指标 | 实时页面 |

---

### 3.3 资源管理

**页面入口**：左侧导航栏 — "资源管理"

#### 3.3.1 功能描述

资源管理页面是运维管理的中枢，提供服务器管理（增删改查、SSH测试、Agent部署）、服务管理（凭证编辑、可见性控制、分组分配）和批量扫描功能。

#### 3.3.2 实现原理

**服务器管理**：

- **创建服务器**：前端表单提交 `POST /api/v2/servers`，包含主机/端口/用户/认证方式
  - 支持"密码认证"和"密钥认证"两种SSH方式
  - 可选"自动部署Agent"复选框
  - 创建成功后自动触发扫描
- **编辑服务器**：`PUT /api/v2/servers/{id}`，修改名称/主机/端口/用户/认证
- **删除服务器**：`DELETE /api/v2/servers/{id}`，后端自动卸载Agent并清理
- **SSH测试**：创建前预测试 `POST /api/v2/test-ssh`
- **扫描服务**：`POST /api/v2/servers/{id}/scan-services`，Agent优先/SSH回退

**Agent 操作**：

| 操作 | API | 描述 |
|------|-----|------|
| 部署 Agent | `POST /api/v2/servers/{id}/deploy-agent` | SSH连接→SFTP上传→systemd注册→启动验证 |
| 升级 Agent | 同部署接口 | 检测旧版本→停止→覆盖文件→重启 |
| 检查状态 | `GET /api/v2/servers/{id}/agent-status` | SSH执行systemctl查询 |
| 卸载 Agent | `DELETE /api/v2/servers/{id}/agent` | 停止→禁用→删除文件→daemon-reload |

**服务管理**：

- **凭证编辑**：`PUT /api/v2/services/{id}`，修改账号/密码
- **可见性切换**：更新 hidden 字段，控制服务卡片是否在导航页显示
- **分组分配**：`PATCH /api/v2/group-config/service-map`，下拉选择目标分组
- **URL编辑**：手动修改服务的访问地址

**服务列表数据流**：
```
GET /api/v2/services/all?server_id= → 完整服务列表（含隐藏）
                                   → 每行展示：服务名 | 分类 | 凭证(复制) | 分组(下拉) | 可见(开关) | 编辑(按钮)
```

#### 3.3.3 UI 设计

- **服务器 Tab 栏**：每台服务器显示名称+主机+在线状态点+服务数
- **服务器卡片**：2x2信息网格（主机/用户/凭证/服务数）+ Agent状态标签
- **操作按钮行**：监控/终端/扫描/部署Agent/升级Agent/检查Agent/编辑/删除
- **服务列表**：每行含凭证复制按钮、分组下拉、可见性开关、编辑按钮

---

### 3.4 SSH 终端

**页面入口**：左侧导航栏 — "终端"

#### 3.4.1 功能描述

提供基于 Web 的交互式 SSH 终端，支持多标签会话、断线重连、终端尺寸自适应和文件管理功能。使用 xterm.js 渲染终端界面，WebSocket 实现双向数据传输。

#### 3.4.2 实现原理

**会话创建流程**：

```
1. 用户点击"+"按钮 → 弹出服务器选择器
2. 选择服务器 → POST /api/v2/terminal/sessions {server_id, cols, rows}
3. 后端创建 SSHTerminalSession 对象:
   a. 从 DB 获取服务器信息（host/port/user/密码或密钥）
   b. 本机特殊处理：使用 127.0.0.1 替代公网 IP
   c. paramiko 连接 → invoke_shell() → 创建 PTY 通道
   d. 生成 session_id → 存入全局 _sessions 字典
4. 前端收到 session_id → 建立 WebSocket 连接
```

**WebSocket 协议设计**：

```
双向通信协议:
  客户端 → 服务端:
    {"type": "input", "data": "..."}    — 终端键盘输入
    {"type": "resize", "cols": 80, "rows": 24}  — 终端窗口大小调整
    纯文本也兼容，直接发送到 SSH 通道

  服务端 → 客户端:
    纯文本（UTF-8解码的SSH输出）
```

**并发处理**：

```python
async def websocket_terminal(websocket, session_id):
    # 两个协程并发运行:
    recv_task = asyncio.create_task(recv_from_ssh())   # SSH→WebSocket
    send_task = asyncio.create_task(send_to_ssh())     # WebSocket→SSH

    # 任一方向断开即结束
    done, pending = await asyncio.wait(
        [recv_task, send_task], return_when=FIRST_COMPLETED
    )
```

**断线重连机制**（核心设计）：

```
1. WebSocket 断开 → 不立即销毁 SSH 会话
2. 调用 session.mark_pending_reconnect()
   → 启动 30 秒定时器
   → session 进入 "pending_reconnect" 状态
3. 30 秒内新 WebSocket 连接:
   → cancel_pending_reconnect()
   → 验证 SSH 通道仍存活
   → 复用原有 session → 恢复终端
4. 30 秒超时:
   → _reconnect_timeout() → remove_session() → 销毁 session

前端配合:
   sessionStorage 存储标签状态
   页面刷新后检查 /terminal/sessions/{id}/status
   如果返回 reconnectable: true → 自动重建 WebSocket
```

**会话生存期管理**：

| 参数 | 值 | 说明 |
|------|-----|------|
| `MAX_SESSIONS_PER_SERVER` | 5 | 每台服务器最大并发终端数 |
| `SESSION_TIMEOUT` | 3600s | 无活动超时时间 |
| `RECONNECT_GRACE` | 30s | WebSocket 断连重连宽限期 |

**会话存活判定**（优先级从高到低）：
1. 宽限期内的 pending_reconnect → 存活
2. connected 为 False 或 channel 为 None → 死亡
3. channel.exit_status_ready() → 死亡（进程退出）
4. 超过 SESSION_TIMEOUT(1小时) 无活动 → 死亡
5. 以上均不满足 → 存活

**密钥加载策略**：
```
依次尝试 Ed25519 → RSA → ECDSA 三种密钥格式
首次成功即用，全部失败则报错
```

#### 3.4.3 UI 设计

- **多标签页栏**：每标签显示连接状态点(绿色/红色) + 名称 + 关闭按钮
- **xterm.js 终端区**：支持 ANSI 颜色、ASCII 中文、自适应窗口大小
- **+ 号按钮**：弹出服务器选择弹窗
- **文件管理**：文件夹图标切换右侧 SFTP 抽屉

#### 3.4.4 API 交互

| API/WebSocket | 用途 | 时机 |
|---------------|------|------|
| `POST /api/v2/terminal/sessions` | 创建终端会话 | 点击服务器时 |
| `GET /api/v2/terminal/sessions/{id}/status` | 查询会话状态 | 页面刷新后 |
| `WS /ws/terminal/{id}` | 终端双向通信 | 持续 |

---

### 3.5 SFTP 文件管理

**页面入口**：终端页面右侧抽屉

#### 3.5.1 功能描述

基于 SFTP 协议的文件管理器，复用 SSH 终端的已有连接，提供目录浏览、文件上传/下载、新建目录、删除和重命名等操作。

#### 3.5.2 实现原理

**SFTP 通道复用**：
```python
# SSHTerminalSession.get_sftp()
# 懒加载模式：首次调用时创建，后续复用
def get_sftp(self):
    if self._sftp is None:
        self._sftp = self.client.open_sftp()
    return self._sftp
```

**目录列表排序**：目录优先，然后按名称排序

**递归删除**：
```python
def sftp_remove(self, path):
    if self._is_dir(path):
        for item in self.sftp_list(path):
            self.sftp_remove(item.path)  # 递归
        self._sftp.rmdir(path)
    else:
        self._sftp.remove(path)
```

**文件下载**：`sftp.getfo()` → BytesIO → StreamingResponse 流式返回
**文件上传**：UploadFile → BytesIO → `sftp.putfo()` 写入远程

#### 3.5.3 API 交互

| API | 方法 | 功能 |
|-----|------|------|
| `/api/v2/terminal/sessions/{id}/files` | GET | 列出目录内容 |
| `/api/v2/terminal/sessions/{id}/files/download` | GET | 下载文件 |
| `/api/v2/terminal/sessions/{id}/files/upload` | POST | 上传文件 |
| `/api/v2/terminal/sessions/{id}/files/mkdir` | POST | 创建目录 |
| `/api/v2/terminal/sessions/{id}/files/rename` | POST | 重命名 |
| `/api/v2/terminal/sessions/{id}/files/delete` | POST | 删除 |

---

### 3.6 Agent 管理

**页面入口**：资源管理页面 → 服务器操作按钮

#### 3.6.1 功能描述

OpsAgent 是部署在目标服务器上的轻量级监控代理，提供 HTTP API 供 OpsCenter 后端采集指标和扫描服务。Agent 管理功能涵盖完整的生命周期：部署、状态检查、升级、卸载。

#### 3.6.2 实现原理

**Agent 部署流程（8步）**：

```
deploy_agent(server, password, port):
1. SSH 连接 → 检查 Python3 是否可用
2. 检测现有 Agent → systemctl is-active opsagent
   → 如果 v2.0.0+ 已运行 → 跳过部署
   → 如果旧版本 → 停止服务，准备重新部署
3. 创建目录 → mkdir -p /opt/opsagent
4. SFTP 上传文件 → opsagent.py + scanner.py
   → 本地搜索路径: ["agent/", "/opt/opscenter/agent/"]
5. 创建 systemd 服务文件:
   [Unit]
   After=network.target docker.service
   
   [Service]
   ExecStart=/usr/bin/python3 /opt/opsagent/opsagent.py --port {port} --token {token} --bind 0.0.0.0
   Restart=always
   RestartSec=5
   
   [Install]
   WantedBy=multi-user.target
6. 写入配置文件 /opt/opsagent/.agent_config (JSON)
7. 启动服务 → systemctl start opsagent
8. 等待 2 秒 → systemctl is-active 验证
   → 成功: 返回 agent_status="running"
   → 失败: 返回 journalctl 错误日志
9. 自动放行防火墙 → ufw allow {port}/tcp
10. Token 复用: 升级时保留原 Token
```

**Agent 卸载流程（5步）**：
```
1. systemctl stop opsagent
2. systemctl disable opsagent
3. rm /etc/systemd/system/opsagent.service
4. rm -rf /opt/opsagent
5. systemctl daemon-reload
```

**Token 生成**：`secrets.token_hex(16)` → 32 字符十六进制字符串

**Agent HTTP API**：

| 端点 | 方法 | 认证 | 功能 |
|------|------|------|------|
| `/metrics` | GET | Bearer Token | 系统指标采集 |
| `/health` | GET | 无 | 健康检查 |
| `/api/v1/services` | GET | Bearer Token | 缓存扫描结果 |
| `/api/v1/containers` | GET | Bearer Token | 仅容器数据 |
| `/api/v1/ports` | GET | Bearer Token | 仅端口数据 |
| `/api/v1/scan` | POST | Bearer Token | 触发即时扫描 |

---

### 3.7 服务发现

#### 3.7.1 功能描述

服务发现是 OpsCenter 的核心能力，负责自动检测服务器上运行的服务并注册到数据库。采用三层发现策略，支持 Docker 容器、监听端口、systemd 服务和 Nginx 路由四种发现源。

#### 3.7.2 三层发现架构

| 优先级 | 方式 | 适用场景 | 数据源 |
|--------|------|----------|--------|
| 1 | **Agent 扫描** | 所有已部署 Agent 的服务器 | Agent HTTP API: `/api/v1/scan` |
| 2 | **SSH 回退** | 远程服务器（无 Agent） | SSH → `docker ps` |
| 3 | **Docker SDK** | 本机服务器 | Docker SDK 直连 |

#### 3.7.3 Agent 扫描同步逻辑

`_sync_agent_scan_to_db()` 处理容器数据：

```
1. 遍历 scan_result.containers
2. 提取公共端口 (_extract_public_ports):
   - 支持三种格式: "0.0.0.0:3000->3000/tcp" / [8000] / [{"port":8000,"bind":"0.0.0.0"}]
3. 构建 URL (_build_svc_url_for_remote):
   - 先尝试 discovery.get_url() 获取已知服务 URL 模板
   - 如果返回相对路径 → 改用 host:port 格式
4. 按 container_name 去重: 已存在 → 更新字段, 不存在 → 新增
5. 自动分类/图标/描述: classify_image / get_icon / get_desc
6. 自动分配分组: _auto_assign_group
```

`_sync_agent_ports_and_systemd()` 处理端口和 systemd 服务：

**端口服务发现**：
```
1. 收集所有已知容器占用的端口集合 container_ports
2. 遍历监听端口列表，过滤:
   - 非 TCP
   - 系统端口 (_SKIP_PORTS: {22, 25, 53, 68, 323, 5433, 9323})
   - 已被容器覆盖
   - localhost 的 docker-proxy
3. 按进程分组端口 → 选择最有价值的端口（最低公网端口）
4. 使用 _PORT_SERVICE_HINTS 匹配已知端口服务
5. 去重键: port:{process}:{port_num}
```

**Systemd 服务发现**：
```
1. 过滤系统级服务 (_SKIP_SYSTEMD_NAMES, ~40个)
2. 跳过已被容器追踪的服务名
3. 跳过已有端口条目的服务（关键词匹配）
4. URL 标记为 #systemd:{name}（前端特殊处理为不可访问）
5. 去重键: systemd:{name}
```

#### 3.7.4 端口服务提示表

12 个已知端口的预设配置：

| 端口 | 服务名 | URL模板 | 分类 | 图标 |
|------|--------|---------|------|------|
| 9100 | OpsCenter | http://{host}:9100 | 运维工具 | fa-tachometer-alt |
| 9091 | OpsCenter API | http://{host}:9091 | 运维工具 | fa-cogs |
| 19100 | OpsAgent | http://{host}:19100 | 运维工具 | fa-satellite-dish |
| 8000 | 2FAuth | http://{host}:8000 | 安全工具 | fa-shield-alt |
| 8080 | Jenkins | http://{host}:8080 | CI/CD | fa-industry |
| 3000 | Gitea | http://{host}:3000 | CI/CD | fa-code-branch |
| 3001 | Grafana | http://{host}:3001 | 监控 | fa-chart-area |
| 9090 | Prometheus | http://{host}:9090 | 监控 | fa-fire |
| 8848 | Nacos | http://{host}:8848/nacos | 中间件 | fa-cubes |
| 15672 | RabbitMQ | http://{host}:15672 | 中间件 | fa-random |
| 5601 | Kibana | http://{host}:5601 | 监控 | fa-search |
| 9999 | 1Panel | http://{host}:9999 | 运维工具 | fa-th-large |

#### 3.7.5 跳过规则

系统级服务过滤，避免噪声：

**跳过的 systemd 服务**（约 40 个）：`dbus`, `systemd-*`, `cron`, `rsyslog`, `ssh`, `snapd`, `ufw`, `unattended-upgrades`, `polkit`, `accounts-daemon`, `irqbalance` 等

**跳过的端口**：`{22, 25, 53, 68, 323, 5433, 9323}` — SSH、邮件、DNS、NTP、PostgreSQL 内部端口

#### 3.7.6 Docker 标签驱动覆盖

容器支持 `opscenter.*` 系列 Docker 标签覆盖自动推断：

```yaml
labels:
  opscenter.name: "自定义名称"
  opscenter.url: "http://custom-url"
  opscenter.category: "自定义分类"
  opscenter.icon: "fa-custom-icon"
  opscenter.description: "自定义描述"
```

#### 3.7.7 僵尸服务清理

三阶段清理策略：
1. **标记 down**：数据库中状态非 down 但容器已消失的 `docker_auto` 源服务
2. **延迟验证**：下次扫描时仍不存在
3. **永久删除**：状态为 down 且容器不存在的历史条目

#### 3.7.8 服务来源枚举

| 来源 | 值 | 说明 | 生命周期规则 |
|------|-----|------|-------------|
| Docker 标签 | `docker_label` | 容器 `opscenter.*` 标签声明 | 不自动删除 |
| 自动发现 | `docker_auto` | Docker/Agent 扫描发现 | 可被僵尸清理删除 |
| Nginx 解析 | `nginx` | Nginx 配置解析 | 不自动删除 |
| 手动添加 | `manual` | 用户手动创建 | 不自动删除 |
| Agent 上报 | `agent` | Agent 扫描端口/systemd | 可被僵尸清理删除 |

---

### 3.8 分组管理

#### 3.8.1 功能描述

将服务按功能分组展示，支持自定义分组名称、颜色、图标、排序，以及服务在不同分组间的移动。

#### 3.8.2 实现原理

**存储方式**：JSON 文件 `/opt/opscenter/frontend/groups.json`，而非数据库

**文件结构**：
```json
{
  "groups": [
    {"id": "cicd", "name": "CI/CD", "order": 1, "color": "#3B82F6", "icon": "fa-infinity"},
    {"id": "monitor", "name": "监控", "order": 2, "color": "#10B981", "icon": "fa-chart-line"},
    ...
  ],
  "serviceGroupMap": {
    "auto:934025b6-xxxx:abc123-xxxx": "cicd",
    "auto:aadfc37a-xxxx:def456-xxxx": "security"
  }
}
```

**映射键格式**：`auto:{server_id}:{service_id}`

**10 个默认分组**：

| 分组 ID | 名称 | 颜色 | 图标 |
|---------|------|------|------|
| cicd | CI/CD | #3B82F6 | fa-infinity |
| monitor | 监控 | #10B981 | fa-chart-line |
| network | 网络 | #8B5CF6 | fa-network-wired |
| database | 数据库 | #F59E0B | fa-database |
| middleware | 中间件 | #6366F1 | fa-cogs |
| auto_workflow | 自动化工作流 | #EC4899 | fa-magic |
| ops | 运维工具 | #14B8A6 | fa-wrench |
| app | 应用服务 | #EF4444 | fa-rocket |
| security | 安全 | #F97316 | fa-shield-alt |
| ungrouped | 未分组 | #6B7280 | fa-folder |

**自动分组**：分类→分组的映射表

| 服务分类 | 目标分组 |
|----------|----------|
| CI/CD / Git / 代码管理 | cicd |
| 监控 / 日志 | monitor |
| 网络 / 代理 / DNS | network |
| 数据库 | database |
| 消息队列 / 缓存 / 搜索引擎 / 注册中心 | middleware |
| 工作流 / 自动化 | auto_workflow |
| 运维工具 / 容器管理 | ops |
| 应用 / Java | app |
| 安全 / 认证 / 2FA | security |
| 其他 | ungrouped |

#### 3.8.3 API 交互

| API | 方法 | 功能 |
|-----|------|------|
| `/api/v2/group-config` | GET | 读取分组配置 |
| `/api/v2/group-config` | PUT | 全量更新分组配置 |
| `/api/v2/group-config/service-map` | PATCH | 移动服务到其他分组 |
| `/api/v2/group-config/groups` | POST | 新增分组 |
| `/api/v2/group-config/groups/{id}` | PUT | 编辑分组 |
| `/api/v2/group-config/groups/{id}` | DELETE | 删除分组 |

---

### 3.9 健康检查

#### 3.9.1 功能描述

自动监控所有服务的可用性和服务器的可达性，定期更新状态并在前端展示。

#### 3.9.2 实现原理

**服务健康检查**（每 60 秒）：

```python
async def background_health_check():
    while True:
        await asyncio.sleep(60)
        services = db.query(Service).all()
        for svc in services:
            if svc.url and not svc.url.startswith('#'):
                try:
                    url = svc.health_path or svc.url
                    resp = requests.head(url, timeout=5, allow_redirects=True, verify=False)
                    status = "up" if resp.status_code < 500 else "down"
                except:
                    status = "down"
                svc.status = status
```

**服务器可达性检查**：

```python
# TCP 端口连接测试（3秒超时）
import socket
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(3)
result = sock.connect_ex((host, ssh_port))
status = "online" if result == 0 else "offline"
```

- 本机服务器自动标记为 ONLINE

**Agent 健康检查**（每 5 分钟）：

```python
# 检查 Agent 运行状态
agent_manager.check_agent_status(server) → 
  running / stopped / installed_stopped / not_deployed / unreachable / error
```

#### 3.9.3 手动触发

`POST /api/v2/health-check` — 立即执行全量健康检查

---

### 3.10 凭证管理

#### 3.10.1 功能描述

为每个服务存储登录账号和密码，支持一键复制，方便运维人员快速获取服务登录信息。

#### 3.10.2 实现原理

**存储方式**：Service 模型的 `account` 和 `password` 字段（明文存储）

**SSH 密码编码**：
- 密码存储在 `ssh_key` 字段，以 `__password__` 前缀标识
- 分辨逻辑：`ssh_key.startswith("__password__")` → 提取密码；否则 → SSH 密钥

**前端复制功能降级方案**：
```javascript
async function copyText(text) {
    // 优先使用 Clipboard API
    if (navigator.clipboard) {
        await navigator.clipboard.writeText(text);
    } else {
        // HTTP 环境降级到 execCommand
        const textarea = document.createElement('textarea');
        textarea.value = text;
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
    }
}
```

**前端显示**：账号明文显示 + 一键复制；密码掩码 `****` + 一键复制

---

## 四、后端代码详解

### 4.1 main.py — 主服务

**文件**：`/opt/opscenter/backend/app/main.py` | **行数**：2425

#### 4.1.1 文件结构

| 区段 | 行号 | 内容 |
|------|------|------|
| 1-19 | 导入 | FastAPI/SQLAlchemy/paramiko/requests 等 |
| 20-24 | Pydantic 模型 | TerminalCreateRequest |
| 26-128 | 配置/常量 | DB连接/分组映射/端口提示/跳过规则 |
| 130-181 | 请求模型 | ServerCreate/ServiceCreate/PinToggle 等 12个 |
| 183-207 | 应用实例化 | FastAPI + CORS 中间件 |
| 209-406 | 启动事件 | 初始化任务/后台任务/自动分组 |
| 408-628 | 服务器管理 API | CRUD/测试/扫描 |
| 632-993 | Agent 扫描同步 | 容器/端口/systemd 同步到 DB |
| 996-1393 | 服务 API + 扫描 | CRUD/健康检查/全局扫描 |
| 1396-1613 | 监控 API | 实时/历史/Agent指标 |
| 1617-1951 | Agent 管理 API | 部署/状态/卸载/指标 |
| 1953-2122 | 健康检查/统计/分组 | 健康检查/统计/分类/分组 |
| 2125-2197 | 带状态服务列表 | services-with-status |
| 2200-2425 | SSH 终端 + SFTP | WebSocket/会话管理/文件操作 |

#### 4.1.2 数据库配置

```python
DB_URL = "postgresql+psycopg://opscenter:OpsCenter2026@127.0.0.1:5433/opscenter"
LOCAL_HOST = os.getenv("LOCAL_HOST", "101.200.91.229")

engine = create_engine(DB_URL, poolclass=QueuePool, pool_size=5, max_overflow=10)
```

#### 4.1.3 启动事件序列

```
on_startup():
1. 启动 Agent 健康检查循环 (_agent_health_check_loop)
2. 等待数据库就绪（最多重试30次，每次2秒）
3. 自动建表 (Base.metadata.create_all)
4. 自动注册本机服务器（如果不存在）
5. 运行初始服务发现 (discover_docker_services)
6. 解析 Nginx 配置 (parse_nginx_config)
7. 启动后台健康检查 (background_health_check, 60秒)
8. 启动 Agent 指标采集器 (background_agent_collector, 30秒)
9. 自动分配分组 (_auto_assign_all_groups)
```

#### 4.1.4 后台异步任务

| 任务 | 频率 | 实现方式 | 功能 |
|------|------|----------|------|
| `background_health_check` | 60s | `asyncio.to_thread` | HTTP HEAD 检查服务 + TCP 检查服务器 |
| `_agent_health_check_loop` | 300s | `asyncio.create_task` | 检查 Agent 运行状态 |
| `background_agent_collector` | 30s | `asyncio.to_thread` | 采集 Agent 指标 + 速率计算 + 写入历史 |

---

### 4.2 discovery.py — 服务发现引擎

**文件**：`/opt/opscenter/backend/app/discovery.py` | **行数**：263

#### 4.2.1 核心数据结构

| 数据结构 | 类型 | 条目数 | 用途 |
|----------|------|--------|------|
| `IMAGE_CATEGORIES` | Dict[str, List[str]] | 10分类 | 镜像名→分类映射 |
| `IMAGE_ICONS` | Dict[str, str] | ~30 | 镜像名→FontAwesome图标 |
| `IMAGE_DESCS` | Dict[str, str] | ~25 | 镜像名→中文描述 |
| `NAME_URLS` | Dict[str, str] | 12 | 容器名→URL模板(含`{host}`占位符) |

#### 4.2.2 关键函数

**`discover_docker_services(server, db, host)`** — 6阶段流程：

```
1. 连接 Docker: 仅本地服务器(server.is_local) → docker.from_env()
2. 容器遍历: 获取所有运行中容器 → 提取元数据
3. 标签优先: opscenter.* 系列标签覆盖自动推断
4. Upsert 逻辑: server_id + container_name 查重 → 存在则更新, 不存在则新建
5. 僵尸标记: 状态非down但容器消失 → 标记 down
6. 僵尸删除: 状态 down 且容器不存在 → 永久删除
```

**`classify_image(image_name)`**：遍历 IMAGE_CATEGORIES 做小写子串匹配，贪心首次匹配

**`get_url(container_name, host)`**：两阶段匹配——精确名→前缀名，替换 `{host}` 占位符

**`parse_nginx_config(config_path, host)`**：正则提取 Nginx location 块，排除根路径/API/WS

---

### 4.3 models.py — 数据模型

**文件**：`/opt/opscenter/backend/app/models.py` | **行数**：88

#### 4.3.1 枚举类型

| 枚举 | 值 |
|------|----|
| `ServerStatus` | online / offline / unknown |
| `ServiceStatus` | up / down / unknown |
| `ServiceSource` | docker_label / docker_auto / nginx / manual / agent |

#### 4.3.2 Server 表字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| id | UUID | uuid4 | 主键 |
| name | String(50) | — | 服务器名 |
| host | String(100) | — | IP/主机名 |
| ssh_port | Integer | 22 | SSH端口 |
| ssh_user | String(50) | "ops" | SSH用户名 |
| ssh_key | Text | None | SSH密钥或 `__password__` 前缀密码 |
| tags | JSONB | [] | 标签列表 |
| status | String(20) | "unknown" | 在线状态 |
| docker_available | Boolean | False | Docker是否可用 |
| is_local | Boolean | False | 是否本地服务器 |
| last_seen | DateTime | None | 最后上线时间 |
| created_at | DateTime | utcnow | 创建时间 |
| updated_at | DateTime | utcnow | 更新时间 |
| enabled | Boolean | True | 是否启用 |
| last_check_at | DateTime | None | 最后检查时间 |
| last_online_at | DateTime | None | 最后在线时间 |
| fail_count | Integer | 0 | 连续失败次数 |
| last_error | Text | None | 最后错误信息 |
| remark | Text | None | 备注 |
| auth_type | String(20) | "password" | 认证类型 |
| agent_status | String(20) | "not_deployed" | Agent状态 |
| agent_port | Integer | 19100 | Agent端口 |
| agent_token | Text | None | Agent认证Token |
| agent_version | String(20) | None | Agent版本 |

#### 4.3.3 Service 表字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| id | UUID | uuid4 | 主键 |
| server_id | UUID | — | 外键→servers.id (CASCADE) |
| name | String(100) | — | 服务名 |
| url | Text | — | 访问URL |
| category | String(50) | "未分类" | 分类 |
| icon | String(50) | "server" | 图标 |
| description | Text | "" | 描述 |
| source | String(20) | "docker_auto" | 来源 |
| status | String(20) | "unknown" | 状态 |
| pinned | Boolean | False | 是否置顶 |
| health_path | Text | None | 健康检查路径 |
| container_id | String(64) | None | 容器ID |
| container_name | String(100) | None | 容器名 |
| image | String(200) | None | 镜像名 |
| ports | Text | None | 端口映射 |
| sort_order | Integer | 0 | 排序权重 |
| hidden | Boolean | False | 是否隐藏 |
| account | String(100) | None | 登录账号 |
| password | String(200) | None | 登录密码 |

#### 4.3.4 MetricHistory 表字段

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| server_id | UUID | 外键→servers.id (CASCADE) |
| timestamp | DateTime | 采集时间（索引） |
| metric_name | String(50) | 指标名（如 cpu_percent, memory_percent, net_rx 等） |
| metric_value | Float | 指标值 |

---

### 4.4 ssh_manager.py — SSH管理

**文件**：`/opt/opscenter/backend/app/ssh_manager.py` | **行数**：242

#### 4.4.1 关键函数

**`get_ssh_client(server, password)`**：三种认证路径
1. 显式 password 参数 → 直接密码认证
2. `__password__` 前缀 → 从 ssh_key 字段提取密码
3. SSH 密钥 → Ed25519 私钥加载

**`ssh_exec(client, command, timeout)`**：执行SSH命令，返回 (stdout, stderr, exit_code) 三元组

**`discover_remote_docker_services(client, host)`**：SSH 执行 `docker ps --format`，三级 URL 生成（MALL_SWARM_URLS 精确匹配 → mall- 前缀匹配 → 端口推断）

**`collect_remote_metrics(client)`**：通过 8-9 次独立 SSH 命令采集 CPU/内存/磁盘/网络/负载/容器数等指标（**注意**：CPU 使用率基于单次采样，不如 Agent 的两次采样准确）

**`test_ssh_connection(host, port, username, password, ssh_key)`**：尝试 SSH 连接 + echo OK，密钥格式自动探测 Ed25519→RSA

---

### 4.5 ssh_terminal.py — SSH终端

**文件**：`/opt/opscenter/backend/app/ssh_terminal.py` | **行数**：287

#### 4.5.1 SSHTerminalSession 类方法

| 方法 | 功能 | 实现方式 |
|------|------|----------|
| `connect(cols, rows)` | 建立SSH连接 | paramiko → invoke_shell → 非阻塞模式 |
| `resize(cols, rows)` | 终端大小调整 | channel.resize_pty() |
| `send(data)` | 发送用户输入 | channel.send(data) |
| `recv(n)` | 接收终端输出 | channel.recv(4096) |
| `get_sftp()` | 获取SFTP | 懒加载，基于现有SSH连接 |
| `sftp_list(path)` | 列目录 | sftp.listdir_attr()，目录排序在前 |
| `sftp_download(path)` | 下载文件 | sftp.getfo() → BytesIO |
| `sftp_upload(path, data)` | 上传文件 | sftp.putfo()，BytesIO → 远程 |
| `sftp_mkdir(path)` | 创建目录 | sftp.mkdir() |
| `sftp_remove(path)` | 删除 | 递归删除（先删子项再删目录） |
| `sftp_rename(old, new)` | 重命名 | sftp.rename() |
| `close()` | 关闭会话 | 关闭 channel/client/sftp |
| `mark_pending_reconnect()` | 标记等待重连 | 启动30秒定时器 |
| `is_alive` | 会话存活检查 | 综合判断 connected/channel/exit_status/timeout |

---

### 4.6 agent_manager.py — Agent管理

**文件**：`/opt/opscenter/backend/app/agent_manager.py` | **行数**：303

#### 4.6.1 关键常量

| 常量 | 值 | 说明 |
|------|-----|------|
| `AGENT_DIR` | `/opt/opsagent` | Agent 安装目录 |
| `AGENT_SCRIPT` | `opsagent.py` | Agent 主脚本名 |
| `AGENT_SERVICE` | `opsagent.service` | systemd 服务名 |
| `AGENT_DEFAULT_PORT` | 19100 | 默认 HTTP 端口 |

#### 4.6.2 关键函数

**`deploy_agent(server, password, port)`**：8步部署流程（详见[3.6.2](#362-实现原理)）

**`check_agent_status(server, password)`**：返回 running/stopped/installed_stopped/not_deployed/unreachable/error

**`fetch_agent_metrics(host, port, token)`**：HTTP GET /metrics，Bearer Token 认证，5秒超时

**`trigger_agent_scan(host, port, token)`**：HTTP POST /api/v1/scan，30秒超时

**`_normalize_agent_metrics(data)`**：字段名映射表，兼容新旧 Agent 版本

**`uninstall_agent(server, password)`**：5步清理流程

---

## 五、Agent 代码详解

### 5.1 opsagent.py — Agent主程序

**文件**：`/opt/opscenter/agent/opsagent.py` | **行数**：477

#### 5.1.1 核心设计理念

- **零外部依赖**：纯 Python 3 标准库，在任意 Python 3 环境下可直接运行
- **直接读取 /proc**：避免依赖外部命令（除 Docker），采集更快更稳定
- **扫描结果缓存**：5分钟后台扫描一次，API 即时返回缓存

#### 5.1.2 版本与配置

```python
VERSION = "2.1.0"
TOKEN = ""              # 认证Token，启动时从命令行参数设置
_scan_cache = None      # 扫描结果缓存
_scan_lock = threading.Lock()   # 缓存读写锁
_scan_interval = 300    # 后台扫描间隔（秒）
```

#### 5.1.3 扫描函数

**`_scan_docker_containers()`**：执行 `docker ps --format`，解析端口映射为结构化数据

**`_scan_listening_ports()`**：执行 `ss -tlnpu`，解析 IPv4/IPv6 监听端口，提取进程名/PID

**`_scan_systemd_services()`**：执行 `systemctl list-units --type=service --state=running`

**`scan_all()`**：组合三种扫描结果，记录耗时

#### 5.1.4 指标采集函数

**`collect_metrics()`** — 13 项指标：

| 指标 | 采集方式 | 关键实现 |
|------|----------|----------|
| CPU 使用率 | 两次读 `/proc/stat`（间隔0.1s） | `(1 - idle_diff/total_diff) * 100` |
| CPU 核数 | `/proc/cpuinfo` | processor 行计数 |
| 内存 | `/proc/meminfo` | MemTotal - MemAvailable |
| 磁盘 | `os.statvfs('/')` | 总量/已用/可用 |
| 磁盘 IO | `/proc/diskstats` | vd/sd 前缀磁盘，512字节扇区转换 |
| 负载 | `/proc/loadavg` | 1/5/15 分钟 |
| 网络 | `/proc/net/dev` | 匹配 eth/ens/enp/wlan/en 前缀 |
| 运行时间 | `/proc/uptime` | 秒数 |
| 容器数 | `docker ps -a` | 运行/停止计数 |
| 主机信息 | `platform` 模块 | hostname/platform/kernel |

#### 5.1.5 HTTP Handler

`AgentHandler(http.server.BaseHTTPRequestHandler)` 提供以下 API：

| 方法 | 路径 | 认证 | 功能 |
|------|------|------|------|
| GET | `/metrics` | 需要 | 系统指标 |
| GET | `/health` | 不需要 | 健康检查 |
| GET | `/api/v1/services` | 需要 | 缓存扫描结果 |
| GET | `/api/v1/containers` | 需要 | 容器数据 |
| GET | `/api/v1/ports` | 需要 | 端口数据 |
| POST | `/api/v1/scan` | 需要 | 即时扫描 |

#### 5.1.6 启动参数

```
--port        默认 19100
--token       认证Token
--bind        默认 0.0.0.0
--scan-interval  默认 300（秒）
```

#### 5.1.7 启动流程

```
1. 解析命令行参数
2. 启动后台扫描线程 (daemon)
3. 执行一次初始扫描
4. 创建 HTTPServer 并 serve_forever()
```

---

### 5.2 scanner.py — 扫描器

**文件**：`/opt/opscenter/agent/scanner.py` | **行数**：252

#### 5.2.1 与 opsagent.py 的差异

scanner.py 增加了两个功能：
1. **Docker 标签采集**：额外执行 `docker inspect` 获取容器标签
2. **差异对比**：`diff_scans(old, new)` 对比两次扫描结果

#### 5.2.2 diff_scans 差异算法

```
对比维度:
  容器: 以 name 为 key → added/removed/changed（status 或 port_summary 变化）
  端口: 以 (port, proto) 为 key → added/removed
  systemd: 以 name 为 key → added/removed

返回: {added: [{type, name, data}], removed: [...], changed: [{type, name, old, new}]}
```

#### 5.2.3 注意事项

- scanner.py 是**死代码**——被上传到远程服务器但 opsagent.py 从未 import 它
- 所有扫描逻辑在 opsagent.py 中重复实现了一份（约 80% 代码相同）
- `diff_scans` 函数在任何模块中都没有被调用

---

## 六、前端代码详解

### 6.1 index.html — 主前端SPA

**文件**：`/opt/opscenter/frontend/index.html` | **行数**：2109

#### 6.1.1 文件结构

| 区段 | 行号 | 内容 |
|------|------|------|
| `<head>` | 1-359 | CSS 样式(342行) + SVG 图标精灵(16个symbol) |
| `<script>` | 363-1486 | Vue 3 Composition API setup 函数 (全部业务逻辑) |
| `<script>` | 1487-2105 | 内联 Vue 模板字符串 (618行) |

#### 6.1.2 四个功能页面

| 页面 | 行号 | 功能 |
|------|------|------|
| 服务导航 (nav) | 1537-1594 | 分组卡片网格，服务器Tab筛选 |
| 监控中心 (monitor) | 1596-1678 | 实时指标 + ECharts 趋势图 |
| 资源管理 (resources) | 1760-1884 | 服务器+服务管理 |
| 终端 (terminal) | 1684-1758 | xterm.js + WebSocket + SFTP |

#### 6.1.3 CSS 设计系统

- **14 个 CSS 变量**：`--bg`, `--bg2`, `--card`, `--accent`, `--text`, `--border` 等
- **暗/亮主题**：CSS 变量一键切换
- **固定侧边栏**：220px → 可折叠至 60px
- **响应式**：`@media(max-width:768px)` 自动折叠 + 单列网格
- **自定义滚动条**：6px 宽度 WebKit 滚动条
- **动画效果**：卡片 hover 上移 1px + 阴影、状态点脉冲动画、toast 滑入/淡出

#### 6.1.4 技术亮点

1. **零构建单文件架构**：不需要 Webpack/Vite，Nginx alias 直接托管
2. **数据缓存双层设计**：monitorCache + historyCache，页面切换秒开
3. **ECharts 懒加载**：首次进入监控页才动态加载 echarts.min.js
4. **xterm.js + FitAddon + WebLinksAddon**：生产级终端体验
5. **终端持久化**：sessionStorage + 重连宽限期

---

### 6.2 app.js — 旧版主逻辑

**文件**：`/opt/opscenter/frontend/assets/js/app.js` | **行数**：810

**状态**：**旧版遗留，当前未被使用**。对应 v2.5 版本，与 index.html (v3.12.1) 功能差距巨大。

主要差异：
- 使用模拟终端（terminal-sim.js），不连接真实 SSH
- 有"工具箱"页面（时间戳/Base64/JSON/密码生成器），v3.12.1 已移除
- 无资源管理/分组管理/SFTP 功能
- API 路径前缀不同（`/ops/api/v2` vs `/api/v2`）

---

### 6.3 api.js — API封装层

**文件**：`/opt/opscenter/frontend/assets/js/api.js` | **行数**：42

**状态**：**旧版遗留，仅被 app.js (v2.5) 使用**。

封装了 13 个 API 方法，缺少 v3.x 新增的分组/终端/SFTP/Agent 等端点。

---

### 6.4 config.js — 前端配置

**文件**：`/opt/opscenter/frontend/assets/js/config.js` | **行数**：14

**状态**：**旧版遗留，未被 v3.12.1 使用**。

包含 API 路径、导航项、刷新间隔等配置，版本标记为 v2.5。

---

### 6.5 tools.js — 工具函数库

**文件**：`/opt/opscenter/frontend/assets/js/tools.js` | **行数**：115

**状态**：**旧版遗留**。大部分函数（时间戳转换/Base64/JSON格式化/密码生成器）属于"工具箱"页面，v3.12.1 已移除该页面。

仍被使用的函数：
- `formatNetwork()` — 网络流量格式化（app.js 中使用）
- `statusClass()` — 状态 CSS 类名（app.js 中使用）
- `parseSearchQuery()` — 多维搜索（app.js 中使用）

---

### 6.6 terminal-sim.js — 终端模拟器

**文件**：`/opt/opscenter/frontend/assets/js/terminal-sim.js` | **行数**：79

**状态**：**旧版遗留**。纯前端模拟终端，支持 15 个命令（help/ls/pwd/docker ps/top 等），但**不连接真实 SSH**。v3.12.1 已完全弃用，改用 xterm.js + WebSocket。

---

## 七、API 端点完整清单

### 7.1 服务器管理（11个）

| # | 方法 | 路径 | 功能 |
|---|------|------|------|
| 1 | GET | `/api/v2/servers` | 列出所有服务器 |
| 2 | POST | `/api/v2/servers` | 创建服务器（自动部署Agent） |
| 3 | GET | `/api/v2/servers/{id}` | 获取服务器详情 |
| 4 | PUT | `/api/v2/servers/{id}` | 更新服务器信息 |
| 5 | DELETE | `/api/v2/servers/{id}` | 删除服务器（自动卸载Agent） |
| 6 | POST | `/api/v2/servers/{id}/scan` | 扫描服务器（Agent优先/SSH回退） |
| 7 | POST | `/api/v2/servers/{id}/scan-services` | 增强扫描（含端口+systemd） |
| 8 | POST | `/api/v2/servers/{id}/test` | 测试连通性 |
| 9 | POST | `/api/v2/servers/{id}/ssh-test` | SSH测试+自动发现 |
| 10 | GET | `/api/v2/servers/{id}/agent/services` | 预览Agent发现的服务 |
| 11 | POST | `/api/v2/test-ssh` | 预创建SSH测试 |

### 7.2 服务管理（6个）

| # | 方法 | 路径 | 功能 |
|---|------|------|------|
| 12 | GET | `/api/v2/services` | 列出服务（过滤隐藏） |
| 13 | POST | `/api/v2/services` | 创建服务 |
| 14 | PUT | `/api/v2/services/{id}` | 更新服务（含凭证） |
| 15 | DELETE | `/api/v2/services/{id}` | 删除服务 |
| 16 | PATCH | `/api/v2/services/{id}/pin` | 切换钉选状态 |
| 17 | GET | `/api/v2/services-with-status` | 带状态的服务列表 |
| 18 | GET | `/api/v2/services/all` | 全量服务列表（含隐藏） |

### 7.3 监控（6个）

| # | 方法 | 路径 | 功能 |
|---|------|------|------|
| 19 | GET | `/api/v2/servers/{id}/monitor` | 实时监控数据 |
| 20 | GET | `/api/v2/monitor/{id}` | 同上（别名路由） |
| 21 | GET | `/api/v2/servers/{id}/history` | 历史监控数据 |
| 22 | GET | `/api/v2/monitor/{id}/history` | 同上（别名路由） |
| 23 | GET | `/api/v2/servers/{id}/agent-metrics` | Agent实时指标 |
| 24 | GET | `/api/v2/servers/{id}/agent-history` | Agent历史指标 |

### 7.4 Agent管理（5个）

| # | 方法 | 路径 | 功能 |
|---|------|------|------|
| 25 | POST | `/api/v2/servers/{id}/deploy-agent` | 部署Agent |
| 26 | GET | `/api/v2/servers/{id}/agent-status` | 检查Agent状态 |
| 27 | DELETE | `/api/v2/servers/{id}/agent` | 卸载Agent |
| 28 | GET | `/api/v2/servers/{id}/agent-metrics` | Agent指标 |
| 29 | GET | `/api/v2/servers/{id}/agent-history` | Agent历史指标 |

### 7.5 健康检查与统计（5个）

| # | 方法 | 路径 | 功能 |
|---|------|------|------|
| 30 | POST | `/api/v2/health-check` | 手动触发全量健康检查 |
| 31 | GET | `/api/v2/health-check-url` | 检查指定URL可达性 |
| 32 | GET | `/api/v2/health` | 系统健康状态 |
| 33 | GET | `/api/v2/stats` | 统计概览 |
| 34 | GET | `/api/v2/categories` | 分类列表 |

### 7.6 分组配置（6个）

| # | 方法 | 路径 | 功能 |
|---|------|------|------|
| 35 | GET | `/api/v2/group-config` | 读取分组配置 |
| 36 | PUT | `/api/v2/group-config` | 全量更新分组配置 |
| 37 | PATCH | `/api/v2/group-config/service-map` | 移动服务分组 |
| 38 | POST | `/api/v2/group-config/groups` | 新增分组 |
| 39 | PUT | `/api/v2/group-config/groups/{id}` | 更新分组 |
| 40 | DELETE | `/api/v2/group-config/groups/{id}` | 删除分组 |

### 7.7 全量扫描（1个）

| # | 方法 | 路径 | 功能 |
|---|------|------|------|
| 41 | POST | `/api/v2/scan` | 全量扫描所有服务器 |

### 7.8 SSH终端（3个）

| # | 方法 | 路径 | 功能 |
|---|------|------|------|
| 42 | POST | `/api/v2/terminal/sessions` | 创建终端会话 |
| 43 | GET | `/api/v2/terminal/sessions/{id}/status` | 查询会话状态 |
| 44 | GET | `/api/v2/terminal/stats` | 终端活跃会话统计 |

### 7.9 SFTP文件管理（6个）

| # | 方法 | 路径 | 功能 |
|---|------|------|------|
| 45 | GET | `/api/v2/terminal/sessions/{id}/files` | 列出目录 |
| 46 | GET | `/api/v2/terminal/sessions/{id}/files/download` | 下载文件 |
| 47 | POST | `/api/v2/terminal/sessions/{id}/files/upload` | 上传文件 |
| 48 | POST | `/api/v2/terminal/sessions/{id}/files/mkdir` | 创建目录 |
| 49 | POST | `/api/v2/terminal/sessions/{id}/files/rename` | 重命名 |
| 50 | POST | `/api/v2/terminal/sessions/{id}/files/delete` | 删除 |

### 7.10 WebSocket（1个）

| # | 路径 | 功能 |
|---|------|------|
| 51 | `/ws/terminal/{session_id}` | SSH终端双向WebSocket隧道 |

**总计：51个端点**（50个 HTTP + 1个 WebSocket）

---

## 八、数据库设计

### 8.1 PostgreSQL 数据库

- **版本**：PostgreSQL 16
- **连接**：`postgresql+psycopg://opscenter:OpsCenter2026@127.0.0.1:5433/opscenter`
- **连接池**：QueuePool(pool_size=5, max_overflow=10)

### 8.2 表结构关系

```
servers ────1:N──── services
   │                   │
   │                   └─ source: docker_label/docker_auto/nginx/manual/agent
   │
   └───1:N──── metric_history
                   │
                   └─ metric_name: cpu_percent/memory_percent/disk_percent
                                  /net_rx/net_tx/net_rx_raw/net_tx_raw
                                  /load_1/load_5/load_15
                                  /disk_read/disk_write/disk_read_raw/disk_write_raw
```

### 8.3 数据保留策略

| 数据类型 | 保留时间 | 说明 |
|----------|----------|------|
| 常规指标 (net_rx, cpu_percent 等) | 7 天 | 用于图表展示 |
| Raw 指标 (net_rx_raw 等) | 1 小时 | 用于速率计算 |
| 服务/服务器数据 | 永久 | 除非手动删除 |
| 分组配置 | 永久 | JSON 文件 |

---

## 九、部署架构

### 9.1 服务组件

| 组件 | 运行方式 | 端口 | 说明 |
|------|----------|------|------|
| OpsCenter 后端 | systemd 服务 | 9091 | FastAPI + uvicorn |
| OpsCenter 前端 | Nginx alias | 80 | 静态文件 |
| PostgreSQL | systemd 服务 | 5433 | 数据库 |
| Nginx | systemd 服务 | 80 | 反向代理 |
| OpsAgent | systemd 服务 | 19100 | 监控代理（远程服务器） |

### 9.2 Nginx 路由配置

```nginx
location / {
    alias /opt/opscenter/frontend/;
    index index.html;
}

location /api/ {
    proxy_pass http://127.0.0.1:9091/api/;
}

location /ws/ {
    proxy_pass http://127.0.0.1:9091/ws/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}
```

### 9.3 部署位置

| 服务器 | IP | 角色 |
|--------|-----|------|
| MFA 服务器 | 101.200.91.229 | OpsCenter 主服务 |
| CI/CD 服务器 | 39.98.123.190 | 被管理服务器（Agent 部署） |

---

## 十、版本演进历史

| 版本 | 日期 | 主要变更 |
|------|------|----------|
| v2.5 | — | 5模块前端（app.js+api.js+config.js+tools.js+terminal-sim.js），模拟终端，工具箱页面 |
| v3.0 | — | 重构为单文件 SPA，新增 xterm.js 真实终端 |
| v3.1 | — | 新增 SSH 终端 + SFTP 文件管理 |
| v3.2 | — | 新增资源管理页面，服务凭证管理 |
| v3.8 | — | 新增端口和 systemd 服务发现（Agent 扫描增强） |
| v3.13.0 | 2026-07-11 | 新增服务自动分组功能和 Agent 优先扫描 |
| v3.15.0 | 2026-07-11 | 修复服务 URL 生成问题，所有服务 URL 指向正确服务器 IP，支持手动编辑服务地址 |

---

## 十一、已知问题与改进建议

### 11.1 安全问题

| 问题 | 严重程度 | 说明 |
|------|----------|------|
| 密码明文存储 | 高 | Service.account/password 和 Server.ssh_key 均为明文 |
| 无 API 认证 | 高 | 所有 API 完全开放，无认证中间件 |
| CORS 全开放 | 中 | `allow_origins=["*"]`，生产环境应限制域名 |
| SSH AutoAddPolicy | 中 | 自动接受所有主机密钥，有中间人攻击风险 |
| Agent 无 HTTPS | 中 | HTTP API 明文传输，Token 可能被截获 |
| SFTP 递归无深度限制 | 低 | 恶意符号链接可能导致无限递归 |

### 11.2 性能问题

| 问题 | 影响 | 建议 |
|------|------|------|
| SSH 指标采集串行 8+ 命令 | 8-18 秒延迟 | 使用并行执行或 Agent 替代 |
| 健康检查串行 | 所有服务串行 HTTP 请求 | 使用 concurrent.futures 并行化 |
| 无分页查询 | 数据量大时影响性能 | 添加分页参数 |
| http.server 单线程 | Agent 并发请求阻塞 | 替换为 aiohttp |
| Docker 容器双重扫描 | Agent 内重复调用 docker ps | 统一扫描入口 |

### 11.3 代码质量问题

| 问题 | 位置 | 建议 |
|------|------|------|
| 速率计算逻辑重复 | main.py 三处 | 提取为公共函数 |
| scanner.py 死代码 | agent/scanner.py | 让 opsagent.py import 而非内联 |
| groups.json 并发安全 | main.py | 加文件锁或改用数据库 |
| 错误处理不统一 | main.py | 统一使用 HTTPException |
| 前端旧版代码遗留 | 5个 JS 文件 | 从部署中移除 |
| index.html 2109 行 | index.html | 拆分为 Vue SFC 组件 |
| CSS 重复定义 | index.html .term-picker | 清理重复样式 |
| 双 API 调用体系 | index.html vs api.js | 统一 API 封装 |

### 11.4 功能建议

| 建议 | 优先级 | 说明 |
|------|--------|------|
| 添加 API 认证 | P0 | JWT 或 API Key 认证 |
| 密码加密存储 | P0 | AES 或 Fernet 加密敏感字段 |
| 清理旧版前端文件 | P1 | 移除 api.js/config.js/app.js/tools.js/terminal-sim.js |
| Agent HTTPS 支持 | P1 | 增加 TLS 加密通信 |
| SSH 连接池 | P2 | 复用 SSH 连接，减少延迟 |
| WebSocket 终端心跳 | P2 | 主动探活，及时清理死连接 |
| 监控告警 | P2 | CPU/内存/磁盘超过阈值时通知 |
| 多用户支持 | P3 | 用户认证 + 权限管理 |

---

> AI生成
