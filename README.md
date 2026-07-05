# OpsCenter v2.0

运维导航平台 - 统一管理和导航所有运维服务

## 技术栈

- **后端**: FastAPI + SQLAlchemy + PostgreSQL 16 + Docker SDK
- **前端**: Vue 3 SPA + ECharts + Tailwind CSS
- **部署**: Systemd + Docker Compose + Nginx 反向代理

## 功能

- **服务导航**: 自动发现 Docker 容器服务，分类展示，一键跳转
- **系统监控**: Prometheus 实时指标 (CPU/内存/磁盘/网络) + 24h 历史趋势图表
- **容器列表**: 31 个 Docker 容器状态、镜像、端口一览
- **终端模拟**: Phase 1 模拟模式，支持基础 Linux 命令
- **工具箱**: 时间戳转换、Base64 编解码、JSON 格式化、密码生成器
- **多服务器管理**: 支持添加和管理多台服务器

## 访问地址

- 页面: http://39.99.157.36/ops/
- API: http://39.99.157.36/ops/api/v2/

## 项目结构

```
/opt/opscenter/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI 应用 (API 端点)
│   │   ├── models.py        # SQLAlchemy 数据模型
│   │   └── discovery.py     # Docker 服务发现
│   ├── requirements.txt     # Python 依赖
│   └── Dockerfile           # (未使用，通过 venv 运行)
├── frontend/
│   ├── index.html           # Vue 3 SPA 前端 (单文件)
│   ├── nginx.conf           # 前端 nginx 配置
│   └── Dockerfile           # (未使用，通过宿主 nginx 服务)
├── deploy/
│   ├── opscenter-backend.service  # systemd 服务配置
│   └── restore-docker-user-rules.sh  # Docker iptables 规则
├── docker-compose.yml       # 数据库容器定义
└── .env                     # 环境变量 (不入库)
```

## 部署信息

| 组件 | 方式 | 端口 |
|------|------|------|
| 后端 API | systemd (uvicorn) | 0.0.0.0:9091 |
| 数据库 | Docker 容器 ops-db | 127.0.0.1:5433 |
| 前端 | Nginx 容器静态文件 | /usr/share/nginx/html/ops/ |

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/v2/servers | 服务器列表 |
| POST | /api/v2/servers | 添加服务器 |
| GET | /api/v2/services | 服务列表 |
| POST | /api/v2/scan | 全局服务扫描 |
| GET | /api/v2/monitor/{id} | 监控数据 |
| GET | /api/v2/monitor/{id}/history | 历史趋势 |
| GET | /api/v2/health | 健康检查 |

## 已管理资源

- 2 台服务器 (CI/CD 主机 + Mall-Swarm)
- 12 个服务 (6 个分类)
- 31 个 Docker 容器
