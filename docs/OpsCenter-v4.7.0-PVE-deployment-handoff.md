# OpsCenter v4.7.0 PVE Docker 部署接手文档

更新日期：2026-09-02
目标主机：PVE `10.66.66.3`
目标访问地址：`http://10.66.66.3:8088`

## 最终状态更新

部署已于 2026-09-02 完成并通过验收，最终部署提交为 `efd3c3b`。四个 Compose 服务均为 `healthy`；Web、静态资源、OpenAPI、Loki、OpsAgent 和系统摘要均正常。本文后续保留完整接手与故障恢复步骤，便于维护人员排障和复验。

## 1. 文档用途

本文档用于将 OpsCenter v4.7.0 在 PVE 主机上的 Docker 部署、验证和收尾工作交给其他 AI 或维护人员继续执行。

本次部署必须满足：

- Web、API、PostgreSQL 和 Loki 正常运行。
- 不影响 PVE 管理服务和现有虚拟机。
- 不覆盖、不重启 PVE 上已经运行的 OpsAgent。
- 完成页面、API、Agent、数据库、日志和重启恢复验证。
- 所有验证必须基于实际命令结果，未验证前不得宣称部署成功。

## 2. 代码与制品信息

本地仓库：

```text
C:\Users\dzd\Documents\Codex\2026-08-27\yue\work\opscenter-current
```

Git 信息：

```text
分支：main
提交：baf42da
```

本次 Docker 部署相关提交：

```text
8b5d410 feat: add safe Docker control-plane deployment
472da08 fix: preserve an existing host agent in Docker installs
baf42da fix: honor Docker deployment port overrides
```

本地部署归档：

```text
C:\Users\dzd\Documents\Codex\2026-08-27\yue\work\opscenter-current\deliverables\OpsCenter-4.7.0-docker-baf42da.tar.gz
```

归档 SHA256：

```text
3E12D1F4CF43F95EE96F10DB80DDC7177558FD171AFE4BAB9A4292B688CE6AA6
```

PVE 上的部署路径：

```text
上传归档：/root/OpsCenter-4.7.0-docker-baf42da.tar.gz
解压目录：/root/opscenter-docker-baf42da
安装目录：/opt/opscenter
数据目录：/opt/opscenter-data
敏感配置：/etc/opscenter/secrets.env
```

`/etc/opscenter/secrets.env` 包含敏感数据。禁止显示其内容、复制到聊天、写入日志或提交到 Git。

## 3. PVE 环境基线

PVE 管理地址：

```text
10.66.66.3
```

关键服务：

- `pveproxy`
- `pvedaemon`
- `pvestatd`
- `opsagent`
- `alloy`
- `docker`
- `containerd`

部署前运行中的虚拟机：

| VMID | 名称 | 状态 |
| --- | --- | --- |
| 100 | ubuntu-CICD | running |
| 101 | ubuntu-prod | running |
| 102 | resolver | running |
| 104 | vm4 | running |

现有 OpsAgent：

```text
监听地址：0.0.0.0:19100
部署前 MainPID：1085736
部署前 ActiveEnterTimestamp：Tue 2026-09-01 09:03:41 CST
```

现有 OpsAgent 正被其他线上 OpsCenter 使用。不得替换其 Token、服务文件或启动参数。

## 4. 已完成事项

- 已安装 Debian 官方 Docker 26.1.5。
- 已安装 Docker Compose 2.26.1。
- Docker 和 containerd 已启动。
- PVE 直接连接 Docker Hub 超时，已配置 Docker 镜像加速。
- 已下载 PostgreSQL 和 Loki 基础镜像。
- 部署代码已复制到 `/opt/opscenter`。
- `/opt/opscenter-data` 已创建。
- `/etc/opscenter/secrets.env` 已生成。
- 已使用 `INSTALL_HOST_AGENT=false` 启动首次构建。
- PVE 核心服务和四台虚拟机在最后一次检查时均正常运行。

Docker 镜像加速配置位于：

```text
/etc/docker/daemon.json
```

当前配置的镜像源：

```text
https://docker.1ms.run
https://docker.m.daocloud.io
```

## 5. 当前部署状态

最后一次检查时，首次构建仍在后台进行：

```text
bash ./deploy/docker/install.sh
docker compose ... up -d --build
pip install --no-cache-dir ... -r requirements.txt
```

当时尚未创建或启动 OpsCenter 容器，端口 `8088`、`9091`、`3100` 尚未监听。

接手后首先执行只读检查：

```bash
ssh pve
pgrep -af 'docker compose|buildkit|pip install|install.sh'
docker ps -a
docker images
ss -lntp | grep -E ':(8088|9091|3100)[[:space:]]' || true
```

处理原则：

- 如果构建仍在运行，等待构建结束，不要重复运行安装脚本。
- 如果构建已经完成，直接进入验收。
- 如果构建失败，先查看实际错误，再使用第 6 节命令恢复。
- 不要删除或重新生成 `/etc/opscenter/secrets.env`。

## 6. 构建失败后的恢复方式

只有确认旧构建进程已经退出后，才允许重新执行：

```bash
cd /root/opscenter-docker-baf42da

INSTALL_HOST_AGENT=false \
LOCAL_HOST=10.66.66.3 \
OPSCENTER_HTTP_PORT=8088 \
./deploy/docker/install.sh
```

必须保留：

```text
INSTALL_HOST_AGENT=false
```

不得同时启动多个 `install.sh` 或 `docker compose up --build` 进程。

## 7. 容器健康验收

执行：

```bash
docker compose \
  --env-file /etc/opscenter/secrets.env \
  -f /opt/opscenter/deploy/docker/compose.yml \
  ps
```

预期以下服务均为 `healthy`：

- `db`
- `loki`
- `backend`
- `web`

如果服务异常，检查日志：

```bash
docker compose \
  --env-file /etc/opscenter/secrets.env \
  -f /opt/opscenter/deploy/docker/compose.yml \
  logs --tail=200
```

报告日志时必须遮蔽密码、Token、私钥和数据库连接串。

## 8. Web、API 和 Loki 验收

在 PVE 本机执行：

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8088/
curl -fsS http://127.0.0.1:9091/openapi.json
curl -fsS http://10.66.66.3:3100/ready
```

验收标准：

- Web 返回 HTTP `200`。
- OpenAPI 文档能够读取。
- OpenAPI 中的应用版本为 `4.7.0`。
- Loki 返回 `ready`。

随后从 Windows 或另一台 `10.66.66.*` 主机访问：

```text
http://10.66.66.3:8088
```

还需要从首页 HTML 中提取至少一个 `/v3/assets/...` 静态资源地址并请求，确认返回 HTTP `200`，避免出现首页可访问但 JavaScript 或 CSS 加载失败。

如果 PVE 本机可以访问但远程无法访问，只允许添加一条范围明确的防火墙规则：

```text
来源：10.66.66.0/24
协议：TCP
端口：8088
```

禁止关闭整个 PVE 防火墙。

## 9. OpsAgent 验收

执行：

```bash
systemctl is-active opsagent
systemctl show opsagent.service -p MainPID -p ActiveEnterTimestamp
ss -lntp | grep ':19100'
```

验收标准：

- `opsagent` 状态为 `active`。
- Agent 仍监听 `0.0.0.0:19100`。
- 部署过程没有替换 Agent 配置或 Token。
- 新 OpsCenter 后端能够通过 `host.docker.internal:19100` 访问 Agent。
- OpsCenter 能读取 PVE 的 CPU、内存、磁盘和系统摘要。

如果 PID 或启动时间发生变化，需要查明原因并报告，不得直接宣称“没有影响”。

## 10. 主机数据和 SSH 功能验收

调用：

```text
GET /api/v2/servers
```

验收标准：

- 存在本地主机记录。
- 本地主机地址为 `10.66.66.3`。
- Agent 状态正常。
- CPU、内存、磁盘等摘要可获取。

容器化控制面执行终端、容器生命周期等高权限操作时需要 SSH。首次部署可能尚未为本地主机保存 SSH 凭证：

- Agent 指标和扫描功能应先完成验收。
- 高权限功能需要在界面中保存 PVE SSH 凭证后测试。
- 私钥不得出现在命令行输出、代码、Git 或聊天记录中。

## 11. PVE 和虚拟机安全验收

执行：

```bash
systemctl is-active \
  docker containerd \
  pveproxy pvedaemon pvestatd \
  opsagent alloy

qm list
```

所有关键服务必须为 `active`，VM 100、101、102、104 必须继续为 `running`。

PVE API 连通性检查：

```bash
curl -k -o /dev/null -w '%{http_code}\n' \
  https://127.0.0.1:8006/api2/json/version
```

未认证状态返回 `401` 属于正常，表示 PVE 代理可达。

还需要确认以下管理地址的 SSH 连通性没有受到影响：

```text
10.66.66.4
10.66.66.5
10.66.66.6
10.66.66.12
```

## 12. 重启恢复验证

禁止为本次验证重启整台 PVE。只重启 OpsCenter Compose 服务：

```bash
docker compose \
  --env-file /etc/opscenter/secrets.env \
  -f /opt/opscenter/deploy/docker/compose.yml \
  restart
```

等待容器重新变为健康后，重复验证：

- Web 返回 HTTP 200。
- OpenAPI 版本为 4.7.0。
- Loki 返回 ready。
- 主机列表正常。
- Agent 指标正常。
- 数据库中的配置仍然存在。

## 13. 性能与稳定性检查

执行：

```bash
docker stats --no-stream

docker inspect \
  --format '{{.Name}} {{.RestartCount}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' \
  $(docker ps -q)

docker compose \
  --env-file /etc/opscenter/secrets.env \
  -f /opt/opscenter/deploy/docker/compose.yml \
  logs --since=10m backend web loki db
```

验收标准：

- 首页和核心 API 连续访问无 5xx。
- 容器没有反复重启。
- 后端没有持续异常堆栈。
- CPU、内存和磁盘使用合理。
- 页面刷新后仍能正常打开。
- PVE 服务和虚拟机状态无变化。

## 14. 强制安全限制

接手人员或 AI 必须遵守：

- 不修改 PVE 的网桥、WireGuard、管理地址或虚拟机网络。
- 不关闭 PVE 防火墙。
- 不重启 PVE 主机。
- 不重新安装、重启或覆盖现有 OpsAgent。
- 不修改现有 OpsAgent Token。
- 不删除或覆盖 `/etc/opscenter/secrets.env`。
- 不运行 `docker compose down -v`。
- 不删除 `/opt/opscenter-data`。
- 不把 Docker Socket 挂载给后端。
- 不给后端容器增加 `privileged: true`。
- 不向容器挂载宿主机根目录。
- 不把旧线上数据库恢复到本次独立实例。
- 不在 Git 中提交密码、Token、私钥或环境密钥。
- 不在未验证的情况下修改产品代码掩盖部署问题。
- 不在没有实际证据时宣称部署成功。

## 15. 最终交付报告要求

完成部署后，报告必须包含：

1. 实际访问地址。
2. 部署提交号 `baf42da`。
3. 四个容器的健康状态。
4. Web、静态资源、API 和 Loki 验证结果。
5. PVE OpsAgent 状态以及是否发生重启。
6. PVE 核心服务状态。
7. VM 100、101、102、104 的运行状态。
8. Compose 重启恢复结果。
9. 是否添加了防火墙规则及规则范围。
10. 当前遗留问题和功能限制。

管理员密码只允许说明保存位置：

```text
/etc/opscenter/secrets.env
```

不得在报告中展示密码明文。

## 16. 当前结论

最终验收结果：

- PVE 核心服务正常。
- 四台虚拟机正常运行。
- Docker 运行正常。
- OpsCenter 的 Web、后端、PostgreSQL 和 Loki 均为 `healthy`。
- OpsAgent 版本为 2.5.1，部署过程没有重启或覆盖原 Agent。
- 20 次管理网访问零失败，平均响应约 263ms，P95 约 299ms。
- 最终 Git 归档已在干净目录中重新安装成功，安装脚本返回 0。
- 当前状态可以认定为部署完成。
