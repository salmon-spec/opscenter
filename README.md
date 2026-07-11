# OpsCenter v3.8.0

运维工作台 — 统一管理服务器、服务、监控与资源的运维导航平台

## 技术栈

- **后端**: FastAPI + SQLAlchemy + PostgreSQL 16 + Docker SDK
- **前端**: Vue 3 SPA + ECharts + Tailwind CSS
- **部署**: Systemd + Docker Compose + Nginx 反向代理

## 功能页面

### 服务导航
- 服务器 Tab 筛选，分组展示
- 服务卡片点击一键跳转
- 多维搜索，在线状态实时检测（60s 轮询）
- 分组管理：创建/修改/删除分组，服务拖拽移动

### 监控中心
- 统一头部：服务器选择器 + 状态标签
- 主指标行：CPU / 内存 / 磁盘（3 列 Gauge 卡片）
- 次指标行：网络速率 / 系统负载 / 磁盘 IO（5 列）
- ECharts 趋势图：8 项指标 24h 时间轴
- 容器列表：30+ Docker 容器状态、镜像、端口
- 指标变色：>80% 橙色警告，>90% 红色告警 + 脉冲动画
- 性能优化：Prometheus 21 次并行查询，monitor API 3.2s → 0.35s

### 资源管理
- 服务器卡片：header + body 分区布局，信息 2×2 网格展示
- 手动添加/删除服务器（SSH 密码/密钥认证）
- SSH 测试连接
- 服务列表管理：编辑名称/描述/隐藏/显示
- 凭证管理：账号密码掩码显示 + 一键复制，SVG 图标（用户/密钥）
- Tab 切换持久化（localStorage）

### Agent 管理
- 一键部署/卸载远程监控 Agent
- Agent 状态监控（运行/离线/未部署）
- 自动采集远程主机指标

## 核心能力

- **服务自动发现**: Docker SDK 发现容器 + Nginx 配置解析
- **僵尸服务清理**: 容器消失时标记 offline，持续离线自动删除
- **多服务器管理**: 本地 + 远程服务器，SSH 连接管理
- **健康检查**: 60s 周期自动检测所有服务可达性
- **明暗主题**: 支持跟随系统/手动切换，8 种配色方案
- **侧边栏折叠**: 一键收起，节省屏幕空间

## 访问地址

- 页面: http://39.99.130.145/ops/
- API: http://39.99.130.145/ops/api/v2/

## 项目结构

```
opscenter/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI 应用（全部 API 端点）
│   │   ├── models.py          # SQLAlchemy 数据模型
│   │   ├── discovery.py       # Docker 服务发现 + Nginx 解析
│   │   ├── ssh_manager.py     # SSH 连接管理 + 远程指标采集
│   │   └── agent_manager.py   # Agent 部署/卸载/状态管理
│   ├── requirements.txt       # Python 依赖
│   └── Dockerfile
├── frontend/
│   ├── index.html             # Vue 3 SPA 前端（单文件）
│   ├── assets/js/
│   │   ├── app.js             # 主应用逻辑
│   │   ├── api.js             # API 请求封装
│   │   └── config.js          # 配置
│   ├── groups.json            # 分组配置
│   └── services.json          # 服务配置
├── agent/
│   ├── opsagent.py            # 远程监控 Agent
│   └── scanner.py             # 主机扫描器
├── deploy/
│   ├── opscenter-backend.service  # systemd 服务配置
│   └── restore-docker-user-rules.sh
├── docker-compose.yml         # 数据库容器定义
└── README.md
```

## 部署信息

| 组件 | 方式 | 端口 |
|------|------|------|
| 后端 API | systemd (uvicorn) | 0.0.0.0:9091 |
| 数据库 | Docker 容器 ops-db | 127.0.0.1:5433 |
| 前端 | Nginx 容器静态文件 | /usr/share/nginx/html/ops/ |

## API 端点

### 服务器管理
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/v2/servers | 服务器列表 |
| POST | /api/v2/servers | 添加服务器 |
| DELETE | /api/v2/servers/{id} | 删除服务器 |
| POST | /api/v2/test-ssh | SSH 测试连接 |

### 服务管理
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/v2/services | 服务列表（不含隐藏） |
| GET | /api/v2/services/all | 全部服务（含隐藏） |
| POST | /api/v2/scan | 全局服务扫描 |
| PUT | /api/v2/services/{id} | 编辑服务 |
| POST | /api/v2/services/{id}/pin | 置顶/取消置顶 |
| POST | /api/v2/services/{id}/toggle-hidden | 切换隐藏 |

### 监控
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/v2/monitor/{id} | 实时监控数据 |
| GET | /api/v2/monitor/{id}/history | 24h 历史趋势 |

### Agent
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v2/servers/{id}/agent/deploy | 部署 Agent |
| POST | /api/v2/servers/{id}/agent/uninstall | 卸载 Agent |
| GET | /api/v2/servers/{id}/agent/status | Agent 状态 |

### 其他
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/v2/health | 健康检查 |
| WS | /ws/agents/{id} | Agent WebSocket |

## 已管理资源

- 2 台服务器（CI/CD 主机 + Mall-Swarm）
- 12 个可见服务（8 个分类）
- 30+ Docker 容器

## 版本历史

| 版本 | 主要变更 |
|------|----------|
| v3.8.0 | Agent 管理 + 网络/磁盘速率修复 |
| v3.7.1 | 资源管理样式美化 + 凭证徽章 SVG 图标 |
| v3.7.0 | 服务凭证管理（账号密码掩码+复制） |
| v3.6.0 | Agent 部署/卸载/状态管理 |
| v3.5.3 | 监控中心性能优化（API 3.2s→0.35s） |
| v3.5.0 | 服务列表管理（编辑/隐藏/显示） |
| v3.4.0 | 服务导航仅显示有 Web 地址的服务 |
| v3.3.0 | 资源管理新增手动添加服务器 |
| v3.2.1 | 监控中心布局改版 |
| v3.2.3 | 修复趋势图 8 小时时区偏移 |
