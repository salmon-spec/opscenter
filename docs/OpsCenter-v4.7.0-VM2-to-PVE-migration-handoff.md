# OpsCenter v4.7.0：VM2 全量迁移到 PVE 交接文档

更新时间：2026-09-02（Asia/Shanghai）

## 1. 目标与边界

将当前运行在 VM2 `10.66.66.5` 的运维工作台完整迁移到 PVE `10.66.66.3` 上已经部署好的 Docker 版 OpsCenter，目标访问地址为：

- Web：`http://10.66.66.3:8088`
- 后端：PVE 本机 `127.0.0.1:9091`
- Loki：`http://10.66.66.3:3100`

必须迁移的数据：

- PostgreSQL 全库：主机、服务、服务广场排序/隐藏/详情、多账号凭据密文、监控历史、汇总指标、探测历史、告警、审计、日报、API Key 元数据等。
- VM2 的 `CREDENTIAL_KEY`、JWT/管理员/OIDC 等认证配置。`CREDENTIAL_KEY` 必须与源库同时迁移，否则历史服务密码和数据库凭据无法解密。
- Loki 长期日志数据。
- `groups.json`、`services.json` 等动态配置。

必须保留 PVE 本地部署配置：

- `LOCAL_HOST=10.66.66.3`
- Web 端口 `8088`
- PVE Docker 数据目录与绑定地址。
- PVE 当前宿主机 OpsAgent Token，不能替换成 VM2 的本机 Token。

禁止只复制源码、只导入服务清单或重新生成 `CREDENTIAL_KEY`。本任务是数据全量迁移，不是全新初始化。

## 2. 主机和仓库信息

本地仓库：

```text
C:\Users\dzd\Documents\Codex\2026-08-27\yue\work\opscenter-current
```

SSH 别名：

```text
vm2 -> 10.66.66.5，用户 prod，可 sudo -n
pve -> 10.66.66.3，用户 root
```

代码版本：源端与目标端后端均为 `4.7.0`。PVE 实际部署代码基于提交 `efd3c3b`，仓库当前 `main` 比 `origin/main` ahead 7；不要在迁移过程中执行 reset、checkout 或 pull 覆盖现有工作树。

## 3. 已完成工作

### 3.1 源端数据清点

VM2 PostgreSQL：

- 数据库容器：`ops-db`，PostgreSQL 16。
- 数据库大小约 400 MB。
- 29 张业务表。
- 7 台主机。
- 194 条服务记录。
- `metric_history` 约 2,433,926 条。
- `metric_rollups` 约 79,741 条。
- `plaza_probe_results` 约 52,197 条。
- `service_probe_results` 约 7,836 条。
- `plaza_service_profiles` 21 条。
- `plaza_service_credentials` 10 条。

VM2 Loki：

- 容器：`loki`，镜像 Loki 3.0.0。
- 数据卷约 447 MB。
- 源配置使用 TSDB v13、filesystem、`index_` 前缀。
- PVE Loki 3.7.2 使用相同 TSDB v13/filesystem/index 前缀，历史数据兼容。

### 3.2 PVE 迁移前回滚备份

已经生成并验证：

```text
/root/opscenter-backups/pve-pre-vm2-migration-20260902.tar.gz.enc
SHA256 80f9ac172f05006e9d3873add766d23ba243983eb9453bca2b78f35767105bf6
```

对应密码文件单独保存在：

```text
/root/.opscenter-recovery/pve-pre-migration.key
```

密码文件权限为 0600。不得打印、复制到仓库、写入本文档或与加密包放进同一对外分发包。

该回滚包已经通过 AES 解密、tar 解包、数据库 dump 清单和 Loki 归档存在性检查。

### 3.3 VM2 最终一致性备份

已经停止 VM2 写入：

- `opscenter-backend.service`：`inactive`
- `opscenter-alloy`：`exited`
- `promtail`：`exited`
- `opsagent.service`：仍为 `active`，用于目标端接管后继续管理 VM2。

最终源备份：

```text
/var/backups/opscenter/vm2-final-20260902.tar.gz.enc
大小 487341312 bytes
SHA256 63b30b8192ef5be4ff0e33e90779b39db68dc67d6b607956a877ff4ef4471d8e
```

传输密码只保存在 PVE：

```text
/root/.opscenter-recovery/vm2-migration.key
```

VM2 还有一个便于传输的同内容副本：

```text
/home/prod/vm2-final-20260902.tar.gz.enc
```

### 3.4 传输状态

由于 `10.66.66.*` 是 WireGuard 网段，实测传输约 0.36 MB/s。迁移包现已完整传到 PVE：

```text
源：http://10.66.66.5:28081/vm2-final-20260902.tar.gz.enc
目标：/root/opscenter-backups/vm2-final-20260902.tar.gz.enc
目标大小：487341312 bytes
目标 SHA256：63b30b8192ef5be4ff0e33e90779b39db68dc67d6b607956a877ff4ef4471d8e
```

传输时曾使用以下临时服务：

```text
python3 -m http.server 28081 --bind 10.66.66.5
```

源端与目标端 SHA-256 已完全匹配。临时 HTTP 服务和 28081/28082 防火墙规则均已删除，复查结果为无监听、无规则。PVE 的现有 OpsCenter 四个 Compose 服务仍为 healthy，Web `http://127.0.0.1:8088/` 返回 200；尚未执行数据库或 Loki 覆盖。

### 3.5 已发现并修复的备份缺陷

Loki 3.x 精简镜像内不可执行 `tar`，原 `backup.sh` 使用 `docker exec loki tar` 会失败。已修改：

```text
deploy/product/backup.sh
```

新逻辑优先读取 Loki `/loki` 对应的宿主机挂载目录并从宿主机打包，找不到挂载目录时才回退到容器内 `tar`。该修改已在 PVE 真实备份中验证成功，但目前尚未提交、尚未推送。

## 4. 当前本地未提交内容

```text
M deploy/product/backup.sh
deliverables/vm2-to-pve-cutover.sh
docs/OpsCenter-v4.7.0-VM2-to-PVE-migration-handoff.md
```

其中 `deliverables/vm2-to-pve-cutover.sh` 是本次环境的一次性安全切换脚本，PVE 上已复制为：

```text
/root/vm2-to-pve-cutover.sh
```

已经在 PVE 执行 `bash -n`，语法检查通过。它包含以下硬保护：

- 源包必须能解密且同时包含数据库 dump、Loki 归档和 secrets。
- 数据库 dump 必须通过 `pg_restore -l`。
- 合并后必须为 `LOCAL_HOST=10.66.66.3`。
- 合并后必须为 `OPSCENTER_HTTP_PORT=8088`。
- Loki 清理目标必须精确等于 `/opt/opscenter-data/loki`，其他路径立即退出。
- `10.66.66.3` 与 `10.66.66.5` 两条主机记录必须都存在，否则不执行身份切换。

不要绕过这些保护手工直接运行原始 `deploy/docker/restore.sh`，原始源 secrets 会把目标本机地址覆盖成 VM2 地址。

## 5. 接手后的第一步：确认传输是否完成

先只读检查：

```bash
ssh pve 'stat -c "%s" /root/opscenter-backups/vm2-final-20260902.tar.gz.enc 2>/dev/null || echo 0'
ssh pve 'pgrep -af "curl.*vm2-final-20260902" || true'
```

只有目标文件大小等于 `487341312` 且 SHA-256 完全等于下值，才能进入切换：

```bash
ssh pve 'sha256sum /root/opscenter-backups/vm2-final-20260902.tar.gz.enc'
# 必须为：63b30b8192ef5be4ff0e33e90779b39db68dc67d6b607956a877ff4ef4471d8e
```

如果下载进程已退出但文件大小或 SHA 不正确，删除 PVE 上不完整的目标文件后重新下载。不要删除 VM2 的源备份。

## 6. 临时资源清理复查

该清理已经完成。接手 AI 只需复查，不要在没有重新传输需求时再次开放端口：

```bash
ssh vm2 'ss -lntp | grep -E ":(28081|28082)" || true'
ssh vm2 'sudo ufw status | grep -E "28081|28082" || true'
```

两条命令均应无输出。

## 7. 执行正式切换

满足以下全部条件后执行：

- 源包大小和 SHA 完全匹配。
- PVE 回滚包及两个独立密码文件仍存在且权限为 0600。
- VM2 后端、Alloy、Promtail 仍停止。
- PVE 四个现有 Compose 服务在切换前正常。

执行：

```bash
ssh pve '/root/vm2-to-pve-cutover.sh \
  /root/opscenter-backups/vm2-final-20260902.tar.gz.enc \
  /root/.opscenter-recovery/vm2-migration.key'
```

脚本执行内容：

1. 解密并校验 VM2 备份。
2. 以 VM2 secrets 为基础，继承源 `CREDENTIAL_KEY` 和认证配置。
3. 从 PVE 当前 secrets 覆盖 Docker 路径、绑定地址、8088 端口、`LOCAL_HOST` 和 PVE Agent Token。
4. 生成新的目标专用加密包：

   ```text
   /root/opscenter-backups/vm2-for-pve-20260902.tar.gz.enc
   ```

5. 停止 PVE backend/web/loki；数据库容器保留用于恢复。
6. 清空已验证的 PVE Loki 目标目录，并恢复 VM2 Loki 数据。
7. 使用 `pg_restore --clean --if-exists` 恢复 VM2 全库。
8. 将数据库中的 PVE `10.66.66.3` 主机切换为唯一 local 主机，并写入 PVE 当前 Agent Token。
9. 将 VM2 `10.66.66.5` 从 local 改为 remote，SSH 用户改为 `prod`；若 VM2 原记录无密钥，则复用数据库中已经验证可用的 PVE SSH 私钥记录。
10. 对 `https://10.66.66.3:8006` 的 PVE 服务保留 `probe_verify_tls=false`，避免自签名证书被误判离线。
11. 以 `INSTALL_HOST_AGENT=false` 重新启动 Docker 部署，保留现有 PVE OpsAgent。
12. 输出迁移后的主机、服务、监控明细和服务广场探测记录数，最后必须出现：

   ```text
   cutover_completed=yes
   ```

脚本执行中不得终止终端。若报错，先保存完整错误输出，禁止重复运行或手工补执行剩余 SQL；按第 10 节回滚。

## 8. 正式验收清单

### 8.1 基础运行

```bash
ssh pve 'docker compose --env-file /etc/opscenter/secrets.env -f /opt/opscenter/deploy/docker/compose.yml ps'
ssh pve 'curl -fsS http://127.0.0.1:9091/openapi.json | python3 -c "import json,sys; print(json.load(sys.stdin)[\"info\"][\"version\"])"'
ssh pve 'curl -o /dev/null -sS -w "%{http_code}\n" http://127.0.0.1:8088/'
ssh pve 'curl -fsS http://10.66.66.3:3100/ready'
ssh pve 'systemctl is-active opsagent'
```

要求：四个 Compose 服务均 healthy；版本 4.7.0；Web 200；Loki ready；OpsAgent active。

### 8.2 数据数量

迁移后至少应恢复到源端快照数量，允许启动后的后台任务在此基础上继续增加：

- servers：7
- services：194
- metric_history：不少于 2,433,926
- metric_rollups：不少于 79,741
- plaza_probe_results：不少于 52,197
- service_probe_results：不少于 7,836
- plaza_service_profiles：21
- plaza_service_credentials：10
- audit_logs：不少于 47
- daily_reports：2

检查数据库必须通过容器内 `psql`，不要输出 `ssh_key`、`agent_token`、`secret_ciphertext` 等敏感列。

### 8.3 主机身份与连接

调用：

```bash
curl -fsS http://10.66.66.3:8088/api/v2/servers
```

必须确认：

- 恰好 7 台历史主机均存在。
- 只有 `10.66.66.3` 是 local。
- `10.66.66.5` 是 remote，名称仍为 VM2，SSH 用户为 `prod`。
- PVE、VM1、VM2、VM3、VM4、L1 等在线主机可切换并返回系统摘要。
- 不得通过接口或日志打印凭据明文。

逐台检查 SSH、Agent、系统摘要、容器页和终端。此前 `101.200.91.229` 本身不可达，迁移后仍可能离线，这不是迁移失败。

### 8.4 服务广场

检查：

```bash
curl -fsS http://10.66.66.3:8088/api/v2/services/plaza
```

要求：

- 恢复 VM2 原有排序、隐藏状态、详情和多账号凭据元数据。
- 历史验收状态为 19 个配置条目，其中 `opsbox` 隐藏、可见 18 项；以源库实际输出为准，不能重新扫描后覆盖成大量新条目。
- PVE `https://10.66.66.3:8006/` 探活为 up，不能因自签名证书显示离线。
- 通过前端显式“查看密码”验证至少一个历史凭据可正常解密；不得在命令行或报告中记录明文。

### 8.5 监控和长期日志

验证历史监控时间范围覆盖迁移前数据，并确认新数据继续增长。检查日志采集总览：

```bash
curl -fsS http://10.66.66.3:8088/api/v2/logs/agents/overview
```

迁移数据库只会恢复日志 Agent 状态和配置元数据，不会自动保证所有旧 Alloy 实例已经改投 PVE Loki。必须：

1. 在“管理主机”中检查每台远程主机的日志采集状态。
2. 对缺失或仍指向 VM2 Loki 的主机重新部署日志采集。
3. 可以调用 `POST /api/v2/logs/agents/deploy-missing` 批量安排缺失主机，但调用后必须再次检查 overview。
4. VM2 的 `opscenter-alloy` 当前故意停止。只有确认其目标地址已改为 `http://10.66.66.3:3100/loki/api/v1/push` 后才允许重新启动或重新部署。
5. 在 Loki 查询中分别验证迁移前历史日志和迁移后新日志。

## 9. 成功切换后的源端处理

成功验收后：

- 保持 VM2 `opscenter-backend.service` 停止，避免双写和用户继续访问旧系统。
- VM2 `opsagent.service` 保持 active，供 PVE OpsCenter 管理 VM2。
- VM2 Alloy 按第 8.5 节重新指向 PVE 后再启用。
- 暂时保留 VM2 最终加密备份和 PVE 两个备份至少 7 天。
- 校验确认 `/var/backups/opscenter/vm2-final-20260902.tar.gz.enc` 存在后，可删除 `/home/prod/` 下的传输副本；删除前必须再次比对源备份 SHA。
- 不要立即删除 VM2 的 PostgreSQL/Loki 容器和数据卷，至少等完整验收和一轮重启验证完成。

## 10. 回滚流程

如果切换脚本失败，或验收发现数据库/凭据/日志严重异常，立即回滚 PVE，不要在错误状态继续写入。

先停止 PVE 写入服务：

```bash
ssh pve 'docker compose --env-file /etc/opscenter/secrets.env -f /opt/opscenter/deploy/docker/compose.yml stop backend web loki'
```

确认 Loki 路径后清理目标目录：

```bash
ssh pve 'test "$(sed -n "s/^LOKI_DATA_DIR=//p" /etc/opscenter/secrets.env | tail -1)" = /opt/opscenter-data/loki'
ssh pve 'find /opt/opscenter-data/loki -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +'
```

恢复 PVE 迁移前备份：

```bash
ssh pve 'BACKUP_PASSWORD=$(cat /root/.opscenter-recovery/pve-pre-migration.key) \
  CONFIRM_RESTORE=YES INSTALL_HOST_AGENT=false \
  /opt/opscenter/deploy/docker/restore.sh \
  /root/opscenter-backups/pve-pre-vm2-migration-20260902.tar.gz.enc'
```

回滚后验证四个 Compose 服务、Web 8088、Loki、PVE OpsAgent 和回滚前服务广场。VM2 源后端仍保持停止，除非用户明确决定取消迁移并恢复旧站。

## 11. 安全限制

- 不读取或输出 secrets 的值，只允许检查键名、长度、存在性或布尔匹配结果。
- 不把密码、私钥、Agent Token、JWT、OIDC Secret、`CREDENTIAL_KEY` 写入日志、文档、Git commit 或聊天回复。
- 不重新生成 `CREDENTIAL_KEY`。
- 不运行 `git reset --hard`、`git checkout --`、删除数据库卷或删除 VM2 源数据。
- 不在 SHA 校验前执行恢复。
- 不允许 VM2 与 PVE 两个后端同时写同一业务状态。
- 不把 PVE 的 local 主机身份留在 VM2 `10.66.66.5`。
- 不把 Docker Socket 挂载进后端容器，不使用 privileged 模式。
- 临时 28081/28082 端口传输完成后必须关闭。

## 12. 代码收尾

迁移和回滚验证完成后再处理代码：

1. 审查 `deploy/product/backup.sh` 的 Loki 宿主机卷备份修复。
2. 执行：

   ```bash
   git diff --check
   ssh pve 'bash -n /opt/opscenter/deploy/product/backup.sh'
   ```

3. 至少再生成一次小型验证备份并确认包含 `database/loki-data.tar.gz`。
4. 只提交通用的 `backup.sh` 修复和本文档；一次性 `deliverables/vm2-to-pve-cutover.sh` 是否提交由用户决定，默认不提交。
5. 本任务没有获得推送 GitHub/GitLab 的新授权；完成本地提交后先向用户报告，不要自行推送。

## 13. 完成标准

只有同时满足以下条件才可宣布完成：

- 目标 PVE 四个容器 healthy，PVE OpsAgent active。
- 目标 Web 8088、OpenAPI、Loki ready 正常。
- 7 台主机、194 条服务及关键历史表数量恢复。
- PVE 是唯一 local，VM2 作为 remote 可管理。
- 服务广场排序、隐藏、详情、多账号凭据和 PVE HTTPS 探活正常。
- 历史监控和历史 Loki 日志可查询，新监控和新日志继续写入。
- VM2 旧后端保持停止，不存在双写。
- 两份加密备份、各自独立密码和 SHA 校验均保留。
- 临时 HTTP 服务和防火墙规则已经删除。
- 完成一次 PVE Compose 重启后的恢复验证。
