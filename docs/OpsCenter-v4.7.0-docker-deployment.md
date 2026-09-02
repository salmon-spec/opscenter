# OpsCenter v4.7.0 Docker 部署与迁移手册

> PVE 兼容说明：发布包包含 `frontend-vite/dist` 预构建静态资源。目标主机仅需 Docker，不需要在受 AppArmor 限制的容器内运行 Node、pnpm 或 Vite。

## 1. 支持范围

Docker 版将前端、后端、PostgreSQL 和 Loki 放入 Compose，宿主机保留一个 systemd OpsAgent。正式支持 Ubuntu 22.04/24.04、Debian 12、Docker Engine、Compose v2、systemd、x86_64 和 arm64。

不支持 Docker Desktop、Windows、macOS、无 systemd Linux、Swarm 或 Kubernetes。Docker 版不挂载 `/var/run/docker.sock`，也不使用 `privileged`、宿主机根目录或 systemd DBus。

## 2. 架构与安全边界

| 组件 | 方式 | 持久数据 |
|---|---|---|
| Web/Caddy | 容器 | 镜像内静态文件 |
| FastAPI | 容器 | 无；动态分组写入配置目录 |
| PostgreSQL 16 | 容器 | `/opt/opscenter-data/postgres` |
| Loki 3.7.2 | 容器 | `/opt/opscenter-data/loki` |
| OpsAgent | 宿主机 systemd | `/opt/opsagent`、`/etc/opsagent.env` |
| 动态分组 | bind mount | `/opt/opscenter-data/config` |

远程主机仍通过 SSH 和 Agent 管理。Docker 控制面所在宿主机的指标通过 `host.docker.internal:19100` 读取；文件、终端、进程信号、防火墙、SSH 和 Docker 生命周期操作通过该主机保存的 SSH 凭证执行。这样不需要向后端容器授予宿主机 root 接口。

## 3. 全新安装

```bash
tar -xzf OpsCenter-4.7.0-portable-*.tar.gz
cd OpsCenter-4.7.0-portable-*
sha256sum -c MANIFEST.sha256
sudo ./deploy/docker/install.sh
```

安装器会：

1. 复制产品到 `/opt/opscenter`。
2. 创建或保留 `/etc/opscenter/secrets.env`。
3. 安装宿主机 OpsAgent，并同步 `LOCAL_AGENT_TOKEN`。
4. 构建前后端镜像并启动数据库、Loki、后端和 Web。
5. 等待 OpenAPI 与首页健康检查成功。

如果目标主机已经由另一套 OpsCenter 管理，必须复用现有 Agent，避免改变 Token：

```bash
sudo INSTALL_HOST_AGENT=false LOCAL_HOST=10.66.66.3 ./deploy/docker/install.sh
```

该模式会读取现有 `opsagent.service` 的 Token，只配置新控制面，不覆盖或重启 Agent。

首次安装后记录管理员密码：

```bash
sudo grep '^OPS_ADMIN_PASSWORD=' /etc/opscenter/secrets.env
sudo ./deploy/docker/verify.sh
```

当前 JWT 登录链路尚未完成，`OPS_AUTH_ENABLED=false` 只能用于可信管理网。不要将 80、9091、3100 或 19100 暴露公网。公网前必须使用 HTTPS 和外部认证代理。

## 4. 宿主机管理

安装后进入“管理主机”，为 OpsCenter 本地主机填写 SSH 用户及密码或私钥，并执行 SSH 测试。Docker 模式允许修改本地主机 SSH 凭证，但仍禁止删除本地主机和远程重启/关机。

未保存 SSH 凭证时仍可使用：

- 在线状态与 Agent 指标。
- 服务扫描、端口扫描和系统摘要。
- 历史指标及 Loki 查询。

以下功能会提示缺少 SSH 凭证：终端、文件、进程操作、Docker 生命周期、镜像/网络/卷管理、防火墙和 SSH 管理。

## 5. 网络与端口

- `80/tcp`：Web，默认监听所有地址。
- `9091/tcp`：API，默认只监听 `127.0.0.1`。
- `3100/tcp`：Loki，默认监听安装时探测到的管理地址，供远程 Alloy 写入。
- `19100/tcp`：宿主机 Agent，只允许 Docker 网桥和 `10.66.66.*` 管理网访问。
- PostgreSQL 不发布宿主机端口。

如管理地址探测错误，在 `/etc/opscenter/secrets.env` 同时修正 `LOCAL_HOST`、`LOKI_BIND_IP` 和 `LOKI_PUBLIC_URL`，然后执行：

```bash
sudo docker compose --env-file /etc/opscenter/secrets.env \
  -f /opt/opscenter/deploy/docker/compose.yml up -d --force-recreate
```

## 6. 从 systemd 版迁移

源机创建加密备份：

```bash
export BACKUP_PASSWORD='独立强密码'
sudo -E /opt/opscenter/deploy/product/backup.sh /root/opscenter-migration.tar.gz.enc
```

目标机先执行 Docker 全新安装，再恢复：

```bash
export BACKUP_PASSWORD='同一密码'
export CONFIRM_RESTORE=YES
sudo -E ./deploy/docker/restore.sh data/production-migration.tar.gz.enc
```

恢复脚本会停止写入服务、恢复 PostgreSQL、Loki、动态分组及原生产密钥，随后重新同步宿主机 Agent Token 并重建容器。恢复后必须检查主机数量、服务广场排序、凭证元数据、日志时间范围和所有主机连接。

`CREDENTIAL_KEY`、数据库密码和 Agent Token 不能在迁移时重新生成。否则历史数据库凭证、服务广场账号或 Agent 会失效。

## 7. 备份、升级与回滚

```bash
export BACKUP_PASSWORD='独立强密码'
sudo -E /opt/opscenter/deploy/docker/backup.sh /root/opscenter-backup.tar.gz.enc
```

升级流程：先备份，保留旧源码和镜像，替换 `/opt/opscenter`，再运行 `deploy/docker/install.sh`。安装器保留现有 secrets 和数据目录。

Compose 回滚必须同时回滚前端和后端镜像；涉及数据库结构的版本还必须使用升级前备份恢复。当前项目没有 Alembic 版本化迁移链，禁止跨多个历史版本直接覆盖升级。

## 8. 验收清单

```bash
sudo ./deploy/docker/verify.sh
curl -fsS http://127.0.0.1:9091/openapi.json | grep 4.7.0
curl -fsS http://127.0.0.1/
curl -fsS http://127.0.0.1:3100/ready
systemctl is-active opsagent
docker compose --env-file /etc/opscenter/secrets.env \
  -f /opt/opscenter/deploy/docker/compose.yml ps
```

还需要在界面验证：本地主机 Agent 在线、远程主机切换、容器列表、终端、日志查询、服务广场、服务凭证解密、数据库实例连接和重启后的自动恢复。

## 9. 已知限制

1. 宿主机高权限功能依赖 SSH；OpsAgent 2.5.1 仍是只读采集接口。
2. 后端容器内仍以 root 用户运行，但没有 Docker Socket、privileged 或宿主机目录权限；后续可在完成路径权限整理后改为非 root。
3. PostgreSQL 为单节点，Loki 为文件系统单实例，不提供自动高可用。
4. 镜像构建依赖公网 Node、Python 和容器仓库，尚不是离线镜像包。
5. `/etc/opscenter/secrets.env` 仍是环境文件，不等同于外部密钥管理系统。
6. 当前 JWT 登录不完整，只允许可信内网或外部认证代理后使用。
