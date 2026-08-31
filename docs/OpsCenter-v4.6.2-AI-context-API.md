# OpsCenter AI Context API v1.0

这组接口面向需要读取 OpsCenter 当前态势的 AI Agent。接口只读取数据库和已持久化健康状态，不执行实时探活、SSH、Docker、进程信号、扫描或其他控制动作。

## 认证

所有接口必须携带 OpsCenter API 密钥：

```http
Authorization: Bearer <OpsCenter read/write API key>
```

密钥通过现有 `POST /api/v2/keys` 创建，建议使用 `read` scope，并为每个 AI Agent 建立独立密钥。明文只在创建响应出现一次；不得写入提示词、代码仓库、日志或知识库。

缺少、无效或停用密钥返回 401。AI 接口即使在工作台免登录模式下也不会匿名放行。

## 稳定响应外层

所有成功响应均使用：

```json
{
  "schema_version": "1.0",
  "opscenter_version": "4.6.2",
  "generated_at": "2026-08-31T12:00:00Z",
  "warnings": [],
  "data": {}
}
```

- `schema_version`：仅在不兼容变更时增加。
- `generated_at`：API 生成响应的 UTC 时间，不代表所有指标都在该时刻采集。
- 指标使用自己的 `collected_at`、`age_seconds` 和 `stale` 表示新鲜度。
- `warnings`：说明缓存、持久化快照等可能影响 AI 判断的限制。

## 接口

### `GET /api/v2/ai/capabilities`

返回接口列表、认证方式、只读保证和数据新鲜度说明。AI 首次接入时应先读取该接口。

### `GET /api/v2/ai/summary`

返回紧凑的整体态势：

- `posture`：`healthy | degraded | critical`。
- 主机总数、在线数、Agent 正常数、指标陈旧主机数。
- 服务总数以及 up/down/degraded/unknown/disabled 数量。
- 活动服务事件与指标告警数量。

该字段是规则聚合，不是 AI 推断结果：存在 down 服务时为 critical；存在活动事件或陈旧指标时为 degraded。

### `GET /api/v2/ai/hosts`

返回主机、Agent、日志 Agent、服务数量、活动告警数和最新持久化指标。

不会返回 SSH 用户、SSH 端口、密码、私钥、Agent Token 或任何凭证存在状态。

### `GET /api/v2/ai/services`

返回服务广场当前可见 Web 服务及其持久化稳定状态、归属主机、最后检测、错误类型、连续失败、活动事件和静默时间。

读取该接口不会触发网络探活；数据可能比真实服务状态稍旧，应结合 `last_checked_at` 判断。
服务 URL 会移除 userinfo、查询参数和 fragment，避免 URL 内嵌账号、令牌或签名泄露。

### `GET /api/v2/ai/incidents`

参数：

- `hours`：1 到 8760，默认 24。
- `active_only`：默认 true。
- `limit`：1 到 500，默认 100。

统一返回服务健康事件和指标告警，使用 `kind=service_health|metric_alert` 区分来源。错误文本最多返回 300 字符。

### `GET /api/v2/ai/snapshot`

一次返回主机、服务和活动事件，适合 AI 建立初始上下文。

参数：

- `incident_hours`：1 到 720，默认 24。
- `incident_limit`：1 到 200，默认 50。

不建议高频调用完整 snapshot；连续轮询优先使用 summary，需要细节时再调用对应接口。

## 调用策略

推荐 AI Agent 使用以下顺序：

1. 启动时读取 capabilities。
2. 每 30 到 60 秒读取 summary。
3. summary 非 healthy 或用户询问具体情况时，读取 hosts/services/incidents。
4. 指标 `stale=true` 时表述为“数据陈旧或采集异常”，不要把旧值描述成当前实时值。
5. AI Context API 只负责观察；任何控制操作必须调用现有受控接口并由用户明确授权。

## 禁止事项

- 不得把 API 密钥放入 URL 查询参数。
- 不得缓存或转发可能包含内网地址的响应到未授权第三方。
- 不得根据 snapshot 自动执行重启、删除、信号、SSH 或防火墙操作。
- 不得把没有 `collected_at` 的指标描述为实时数据。
