# 模型用量 1.1.0

入口保持“模型用量”。“账户配置”可添加、编辑、移除最多 10 个 API 账户，并设置默认账户和顶部展示账户。默认仅显示一个账户；选择多个展示账户后以顶部页签切换，每次只渲染一个账户。

| 厂商 | API Base 默认值 | 实际读取的数据 |
| --- | --- | --- |
| Command Code | https://api.commandcode.ai | 保留账期、请求、Token、额度和本地累计快照差分 |
| OpenCode Go | https://opencode.ai/zen/go/v1 | GET /usage：5 小时、周、月使用率及重置时间 |
| DeepSeek | https://api.deepseek.com | GET /user/balance：分币种的可用、充值、赠送余额 |

接口契约核验于 2026-10-20：[OpenCode 官方路由](https://github.com/anomalyco/opencode/blob/dev/packages/console/app/src/routes/zen/go/v1/usage.ts)、[原始实现](https://github.com/anomalyco/opencode/pull/16513/files)、[现行响应示例](https://github.com/anomalyco/opencode/issues/53701)、[DeepSeek 官方文档](https://api-docs.deepseek.com/api/get-user-balance/)。Go 兼容 usage.rolling/weekly/monthly 及旧 rollingUsage/weeklyUsage/monthlyUsage。未知格式明确报错。

## 配置与兼容

- GET /modules/command-code-usage/config 只返回掩码；POST 同一路由保存 accounts、active_account_id、display_account_ids。GET status 可指定 account_id。沿用现有 JWT/操作员鉴权和模块数据库密文配置，不增加数据库表或外部依赖。
- 旧单账户与环境配置自动以 legacy 账户呈现，原 Key 无需重填。第一次保存多账户时接续原 Command Code 历史。更换凭据、组织或接口地址则分配新的历史作用域，避免跨账户拼接。
- 编辑已保存账户可将 api_key 留空保留。新增或更换厂商/API Base 必须重填 Key，防止旧密钥发送到新地址。请求不跟随重定向。
- 只读调用余额/用量接口，保存时验证新增或变更的凭据，不发送生成请求。采集间隔按账户设置，后台每 15 秒检查到期账户，最多并行 4 个。
- Go 与 DeepSeek 未公开的账期请求、Token、成本或余额字段不推算、不填零；仅显示该厂商实际返回的数据。两者当前不支持本地用量差分历史；Command Code 原有历史继续保留。移除账户不删除历史行。
- 切换页签只改变当前浏览视图；设置默认账户需保存配置。切换时清空旧视图，丢弃切换前未完成请求的响应。

验证：backend/tests/test_model_usage_accounts.py、test_command_code_cache.py、test_extension_modules.py。离线测试使用虚构 Key 与接口响应；真实新厂商连接需在配置界面填写相应 Key 后验证。

## 生产发布 2026-10-20

模型用量 1.1.0、前端 5.5.16。现有镜像保持不变，通过版本化 ConfigMap 覆盖 3 个模块源文件及静态页/前端 bundle；部署补丁在 deploy/k8s/opscenter-model-usage-1.1.0.patch.json 与 opscenter-model-usage-web-1.1.0.patch.json，使用 kubectl patch --type strategic --patch-file 应用。应用前需创建补丁中引用的 ConfigMap（源文件/静态页用 data，dist.tar.gz 用 binaryData；大 bundle 不使用 client-side apply 的 last-applied 注解）。新镜像构建时应包含这些源文件与静态文件。

Command Code 未提供固定账期时不计算小时/日/周差分，近 30 天滚动累计单独标注；未返回的额度使用率显示未提供。扩展宿主页在 iframe 插入并设置沙箱属性后导航，不缓存 iframe 文档，实际生产直接刷新已验。

本次 35 项定向测试、JS 语法、Python 编译与 Vite build 通过。真实 Command Code 采集保留，Go/DeepSeek 解析和页签已用示例响应验证，真实新厂商需配置 Key。数据库备份、部署模板及注册元数据回滚资料：K3s master /home/ubuntu/opscenter-model-usage-1.1.0-20261020/。没有数据库结构变更。
