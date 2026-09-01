# OpsCenter v4.7.0 可移植部署与产品化验收手册

## 1. 交付目标与边界

本交付将 OpsCenter 固化为可重复安装、备份、迁移、恢复和验收的 Linux 产品包。正式支持：

- Ubuntu 22.04/24.04、Debian 12，x86_64 或 arm64。
- systemd、Docker Engine + Compose v2、Python 3.10+、Caddy 2。
- 单机控制平面；被管主机通过 SSH 和 OpsAgent 接入。
- PostgreSQL 16；Loki/Alloy 为可选的长期日志组件。

不宣称直接支持 Windows、macOS、Kubernetes、无 systemd 的发行版或完全离线安装。离线环境需提前准备 apt/pip 包和容器镜像。

## 2. 产品架构

| 组件 | 运行方式 | 默认端口 | 持久数据 |
|---|---|---:|---|
| Vue 3 前端 | Caddy 静态文件 | 80 | `frontend/v3` |
| FastAPI 后端 | systemd + Python venv | 127.0.0.1:9091 | 无 |
| PostgreSQL 16 | Docker Compose | 127.0.0.1:5433 | `/opt/opscenter-data/postgres` |
| Loki 3.x（可选） | Docker Compose | 127.0.0.1:3100 | `/opt/opscenter-data/loki` |
| Alloy 1.18（可选） | Docker Compose/远程 Agent | 12345 | 小型游标卷 |
| OpsAgent 2.5.1 | 被管主机 systemd | 19100 | `/opt/opsagent` |

源码包内 `deploy/product/` 是唯一的标准新装入口；根目录旧 `docker-compose.yml` 只保留历史兼容，不用于生产安装。

## 3. 压缩包内容

- `backend/`、`frontend-vite/`、`frontend-vite/dist/`、`agent/`：源码、锁文件与已构建前端。
- `deploy/product/`：安装、备份、恢复、验证脚本及 systemd/Caddy/PostgreSQL 模板。
- `deploy/observability/`：Loki/Alloy 配置和安装脚本。
- `docs/`：架构、API、安全审计和本手册。
- `data/production-migration.tar.gz.enc`：生产数据库、长期日志、动态分组和生产配置的 AES-256 加密备份。
- `MANIFEST.sha256`：文件完整性校验。

普通源码和配置模板不含生产明文密码。生产密钥只存在于加密迁移备份中，解密密码必须通过独立渠道保存。

## 4. 最低资源与网络

- 最低 2 CPU、4 GiB 内存、20 GiB 可用磁盘；建议 4 CPU、8 GiB、60 GiB。
- 目标主机需要访问系统软件源、Python 包源和 Docker Registry。
- 入站开放 80；若使用 HTTPS，再开放 443。
- 后端到被管主机需访问 SSH 22 和 Agent 19100。
- 数据库默认仅监听 127.0.0.1，不应暴露到公网。

## 5. 全新安装

```bash
tar -xzf OpsCenter-4.7.0-portable-*.tar.gz
cd OpsCenter-4.7.0-portable-*
sha256sum -c MANIFEST.sha256
sudo ./deploy/product/install.sh
```

安装器会保留已有 `/etc/opscenter/secrets.env`；首次安装则生成随机数据库密码、JWT 密钥、管理员密码、凭证加密密钥和 Agent Token。立即执行：

```bash
sudo chmod 600 /etc/opscenter/secrets.env
sudo grep '^OPS_ADMIN_PASSWORD=' /etc/opscenter/secrets.env
curl -fsS http://127.0.0.1:9091/openapi.json >/dev/null
curl -fsS http://127.0.0.1/ >/dev/null
```

`CREDENTIAL_KEY` 用于解密数据库实例和服务广场凭证，迁移后必须保持不变。不能通过简单换值轮换。

## 6. 从现有系统迁移

1. 在源主机生成加密一致性备份：

```bash
cd /opt/opscenter
export BACKUP_PASSWORD='使用独立强密码'
sudo -E DB_CONTAINER=ops-db LOKI_CONTAINER=loki ./deploy/product/backup.sh /root/opscenter-migration.tar.gz.enc
```

若需要一并保存当前 Loki/Alloy 编排文件，可额外设置 `EXTRA_CONFIG_PATHS="/path/to/loki /path/to/alloy"`。这些文件与生产密钥同样只进入加密备份。

2. 将产品压缩包复制到目标主机并完成全新安装。
3. 停止用户访问，在目标主机执行恢复：

```bash
export BACKUP_PASSWORD='与备份时相同'
export CONFIRM_RESTORE=YES
sudo -E ./deploy/product/restore.sh data/production-migration.tar.gz.enc
```

4. 验证主机数量、服务数量、服务广场排序、凭证元数据、监控历史和日志时间范围。
5. 迁移窗口内不要在源端继续写入；正式切换前建议再做一次最终备份。

恢复会覆盖目标数据库，并恢复原 `CREDENTIAL_KEY`。只能在明确选定的目标环境执行。

可在具备空闲 55432、19091、18081 端口的 Linux 验证机运行隔离迁移演练；脚本使用临时 PostgreSQL 容器和 `PREVIEW_MODE`，不会连接被管主机：

```bash
BACKUP_PASSWORD='备份密码' ./deploy/product/migration-smoke.sh data/production-migration.tar.gz.enc
```

## 7. 长期日志组件

```bash
cd /opt/opscenter/deploy/observability
cp .env.example .env
# 设置本机 server UUID、监听地址和数据目录
sudo ./install.sh
```

随后在 `/etc/opscenter/secrets.env` 设置：

```text
LOKI_URL=http://127.0.0.1:3100
LOKI_RETENTION_DAYS=365
LOKI_DATA_DIR=/opt/opscenter-data/loki
```

重启后端：`sudo systemctl restart opscenter-backend`。

## 8. 升级、备份与回滚

- 升级前先运行 `backup.sh`，同时保留旧源码目录。
- 新版本覆盖 `/opt/opscenter` 后重新执行 `install.sh`；已有密钥不会被替换。
- 代码回滚使用 `deploy/rollback.sh`；数据回滚使用加密迁移备份。
- 每日数据库备份、每周恢复演练；备份密码与备份文件分开保存。
- Loki 备份复制期间应暂停日志写入或使用存储层快照，避免文件级快照不一致。

## 9. 验收命令

```bash
./deploy/product/verify.sh
systemctl is-active opscenter-backend caddy
docker compose --env-file /etc/opscenter/secrets.env -f /opt/opscenter/deploy/product/postgres.compose.yml ps
curl -fsS http://127.0.0.1:9091/openapi.json | grep '4.7.0'
curl -o /dev/null -sS -w '%{http_code} %{time_total}\n' http://127.0.0.1/
curl -o /dev/null -sS -w '%{http_code} %{time_total}\n' http://127.0.0.1:9091/api/v2/servers
```

必须检查：

- 前后端 HTTP 200，OpenAPI 版本 4.7.0。
- 数据库恢复后表数量和关键业务记录数量与源端相同。
- 服务广场保持 19 个已配置条目及原隐藏状态（当前 `opsbox` 隐藏时可见列表为 18 项）、排序一致，多凭证列表只返回元数据而不返回明文。
- 主机切换、系统摘要、容器轻量列表、手动刷新资源统计正常。
- Loki `/ready` 成功，历史日志可按主机和时间段查询。
- 重启主机后 PostgreSQL、后端和 Caddy自动恢复。

## 10. 性能基线

在 4 CPU、8 GiB、SSD 和本地 PostgreSQL 条件下，验收目标：

- 前端入口 HTTP 首字节低于 300 ms。
- `/openapi.json` 和主机列表热请求低于 500 ms。
- 有缓存的服务广场和容器基础列表低于 300 ms。
- 冷容器基础列表低于 1.5 s；系统摘要低于 800 ms。
- 连续 100 次只读请求无 5xx；进程 RSS 无持续线性增长。

性能数据必须在目标环境重新测量，不能把当前局域网结果当作所有硬件的保证。

## 11. 安全要求

- 当前内置 JWT 模块没有完整登录入口，产品模板保持 `OPS_AUTH_ENABLED=false`；必须仅部署在可信管理网，或在 Caddy 前增加具备 HTTPS 与身份认证的反向代理。不能直接把 80/9091 暴露公网。
- `/etc/opscenter/secrets.env` 权限必须为 600；不得提交到 Git。
- PostgreSQL 只绑定回环地址；Agent 19100 仅允许管理网访问。
- 迁移包中的生产数据必须保持 AES-256 加密，解密密码不得放进同一压缩包。
- OpsCenter 后端当前以 root 运行，以支持本机 Docker、终端和系统管理；应将控制平面放在隔离管理网。

## 12. 产品化结论与已知限制

当前版本具备完整的安装、运行、备份、恢复、监控和远程管理闭环，可作为受控 Linux 内网环境的完整产品部署。以下条件限制“到处部署”的范围：

1. 数据库迁移仍采用 SQLAlchemy `create_all` 加启动期补列，没有 Alembic 版本化迁移链；跨多个历史版本升级必须先备份并按版本验证。
2. 后端因本机管理能力需要 root，不适合未经隔离直接暴露公网。
3. 内置 JWT 登录链路尚不完整，默认模板只提供内网 HTTP；公网部署必须增加 TLS、域名和外部访问控制。
4. 安装依赖公网软件源和镜像仓库；尚未提供完全离线镜像包。
5. PostgreSQL 为单实例，无自动高可用；大规模环境需外接托管 PostgreSQL。
6. Loki 文件备份不是分布式一致性快照；大日志量环境应使用对象存储或卷快照。

因此，v4.7.0 已达到“可迁移、可重复部署的内网产品”标准，但尚不是无条件跨平台、互联网级高可用的一键产品。

## 13. 本次实测记录（2026-09-01）

- 前端 Vite 生产构建成功，634 个模块完成转换。
- 产品脚本通过 Bash 语法检查，PostgreSQL Compose 通过配置解析。
- 后端 smoke 与可观测性回归：11 项通过；仅存在 FastAPI `on_event` 弃用警告。
- AES-256 迁移备份解密、tar 完整性和 `pg_restore -l` 校验通过。
- 隔离 PostgreSQL 16 完整恢复成功：29 张业务表、7 台主机。
- 隔离后端以 `PREVIEW_MODE=true` 启动成功，OpenAPI 版本为 4.7.0。
- 服务广场恢复为当前状态：19 个配置条目，其中 `opsbox` 隐藏，可见 18 项。
- 连续 100 次 `/api/v2/servers` 请求无 5xx：P50 约 20 ms，P95 约 44 ms。
- 验证环境退出后，临时数据库容器、后端进程和测试端口均已清理。

演练中实际发现并修复：PostgreSQL 官方镜像初始化时的临时实例可能被普通就绪探针误判，以及验证后端子进程可能残留。最终脚本改为 TCP `SELECT 1` 判定最终数据库就绪，并使用可回收的 `exec` 进程。
