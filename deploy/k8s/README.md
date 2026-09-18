# OpsCenter v5.0.5 K3s 配置契约

本目录只管理 OpsCenter 如何接入现有 `middleware` 命名空间，不复制或接管
PostgreSQL、Redis、RabbitMQ、Kafka、MongoDB、Nacos、MinIO、ZooKeeper 的生命周期。
这些有状态组件应由独立清单或 Helm 发布，并各自具备 PVC、备份和恢复方案。

## 发布前准备

1. 确认 `middleware` 命名空间中的依赖及 DNS 可用。
2. 创建专用只读 ServiceAccount；它不授予 Secret 读取或工作负载写权限：

   ```sh
   kubectl apply -f deploy/k8s/opscenter-reader-rbac.yaml
   ```

3. 应用 OpsCenter 入站策略。`10.42.0.0/16` 是当前 K3s Pod CIDR；如集群
   Pod CIDR 不同，发布前必须同步修改：

   ```sh
   kubectl apply -f deploy/k8s/opscenter-networkpolicy.yaml
   ```
4. 复制 `opscenter-v5-secrets.env.example` 到仓库外，替换所有 `CHANGE_ME`。
5. 创建或更新中间件连接 Secret（不要把真实值写入 YAML 或 Git）。登录、
   JWT、数据库加密密钥继续由原有 `opscenter-env` Secret 管理，避免重复定义：

   ```sh
   kubectl -n opscenter create secret generic opscenter-middleware-secrets \
     --from-env-file=/secure/path/opscenter-v5-secrets.env \
     --dry-run=client -o yaml | kubectl apply -f -
   ```

6. 如需启用 AI 自动巡检，复制 `opscenter-ai-ops.env.example` 到仓库外，填入
   CommandCode API Key，然后创建或更新独立 Secret：

   ```sh
   kubectl -n opscenter create secret generic opscenter-ai-ops \
     --from-env-file=/secure/path/opscenter-ai-ops.env \
     --dry-run=client -o yaml | kubectl apply -f -
   ```

   该 Secret 在 Deployment 补丁中是可选项；未创建时后端仍可启动，但 AI
   分析与自动巡检不会调用模型。

7. 应用非敏感配置，并把补丁合并到现有 backend Deployment：

   ```sh
   kubectl apply -f deploy/k8s/opscenter-v5-config.yaml
   kubectl patch deployment backend -n opscenter --type strategic \
     --patch-file deploy/k8s/backend-v5.patch.yaml
   ```

8. 后端挂载的 `/opt/opscenter/frontend` 必须来自可写持久卷，否则 SSH
   `known_hosts` 只能在 Pod 生命周期内保存。

## 启用顺序

默认只接入中间件状态页，写入型能力全部关闭。逐项验证健康接口后，再单独开启
`MQ_ENABLED`、`KAFKA_ENABLED`、`MONGO_ENABLED`、`NACOS_ENABLED`、
`MINIO_ENABLED`。最后确认 Redis 稳定后开启 `MW_LEADER_ENABLED`。

开启选主后，所有周期扫描、监控采集、聚合、告警和清理任务必须先取得 Redis
租约；Redis 不可用时这些任务会暂停，HTTP API 继续服务。当前 Deployment 保持
单副本，因为启动阶段的数据库建表与种子数据还没有迁移为独立 Job。完成该拆分前
不要扩容 backend。

## 最小验收

```sh
kubectl -n opscenter rollout status deployment/backend --timeout=180s
kubectl -n opscenter get pod,svc
kubectl -n opscenter logs deployment/backend --tail=200
curl -fsS http://10.66.66.15:30088/openapi.json | grep -q '"version":"5.0.5"'
curl -fsS http://10.66.66.15:30088/api/v2/data-services/overview
```

验收时还需确认：前端登录、服务广场、主机详情、系统监控、终端、K3s 概览均可用；
后台日志中没有重复扫描和重复告警。启用鉴权后，写接口验收必须使用 JWT 或
`OPERATOR_TOKEN`。
