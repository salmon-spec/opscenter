# OpsCenter 长期日志栈

中心节点运行 Loki 3.7.2，各 Linux 主机运行 Alloy 1.18.0，采集 systemd journal 和 Docker 容器日志。日志默认保留 365 天，OpsCenter 后端是唯一查询入口。

## 中心节点

1. 从 `.env.example` 创建 `.env`，将 `OPSCENTER_SERVER_ID` 改为 OpsCenter 中本地主机的 UUID。
2. 运行 `sh install.sh`。脚本会校验 Compose 配置、拉取固定版本、启动 Loki 与中心 Alloy，并等待 Loki 健康。
3. 后端环境增加 `LOKI_URL=http://10.66.66.5:3100` 和 `LOKI_RETENTION_DAYS=365` 后重启 `opscenter-backend`。
4. 只对 `10.66.66.0/24` 开放 3100/TCP；不要把未开启认证的 Loki 暴露到公网。

中心 Alloy 以只读方式挂载宿主机 journal 与 Docker socket，采集 VM2 自身日志；远程主机仍由“管理主机”抽屉后台部署 Alloy。

## 每台被管主机

1. 安装 Grafana Alloy 1.18.0，将 `alloy.example.alloy` 复制为 Alloy 配置。
2. 给 Alloy 的 systemd 环境注入：
   - `OPSCENTER_SERVER_ID`：OpsCenter 主机 UUID。
   - `OPSCENTER_HOST_NAME`：主机显示名。
   - `LOKI_PUSH_URL=http://10.66.66.5:3100/loki/api/v1/push`。
3. 将 Alloy 用户加入 `adm`、`systemd-journal` 和 `docker` 组，然后重启 Alloy。

当 OpsCenter 已设置 `LOKI_URL` 且主机已保存 SSH 凭证时，也可在“管理主机”抽屉中单台或批量部署。部署在后台执行，完成后会写回版本、运行状态和最近错误。自动安装目前支持 Debian/Ubuntu amd64 和 arm64，下载后会核对官方 SHA-256。

生产环境下 Loki 数据卷必须纳入备份和磁盘容量告警。单机文件系统方案适合当前内网规模；数据量增大后将 chunks 迁移至 S3 兼容对象存储。
