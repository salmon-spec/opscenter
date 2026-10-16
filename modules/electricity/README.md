# electricity — 国家电网电费监测模块

独立业务模块，把一个户号的电费余额、最近账单和逐日用电纳入运维工作台。
模块自带采集、自带存储、自带页面，不读写其他模块的数据，也不依赖旧 backend。

## 模块契约

| 接口 | 说明 |
|---|---|
| `GET /health/live`、`GET /health/ready` | 进程存活 / 数据目录可写 |
| `GET /api/v1/module` | 模块 key、版本与只读能力清单 |
| `GET /api/v1/summary` | 余额、最近公布日电量、最近账单、7 日均、采集新鲜度 |
| `GET /api/v1/findings` | 低余额、采集过期、凭据缺失、部分数据待重试 |
| `GET /api/v1/overview` | 业务明细（户号、余额、账单、近 30 日用电），需工作台会话 |
| `POST /api/v1/refresh` | 手动触发一轮采集，需工作台会话 |
| `GET /ui` | 自带展示页；在工作台中通过隔离 iframe 的 API 桥接读取数据 |

网关通过 `handle_path /modules/electricity/*` 把前缀剥掉后反向代理到本模块。
工作台扩展模块目录注册 `/ui`，经受限的 API 桥接传递工作台会话，不把 JWT 暴露给 iframe。
直接访问 `<opscenter 入口>/modules/electricity/ui` 仍可使用工作台本地会话。

## 数据与凭据

- 采集内核复用 Codex 主机上已验证运行的 `sgcc-standalone`：`vendor/web_api.py`（国网网页通道）
  与 `vendor/utils/crypt.py`（国密实现），授权见 `LICENSE.upstream`、`LICENSE.hass-state-grid`。
- 凭据由部署注入环境变量，不落盘、不进 Git：
  `ELECTRICITY_ACCOUNT`、`ELECTRICITY_PASSWORD_DIGEST`（32 位 MD5 大写摘要）、
  `ELECTRICITY_TARGET_ID`、`ELECTRICITY_TARGET_METER`（JSON）。
- 会话状态、快照与告警状态写在模块自有数据目录（`ELECTRICITY_DATA_DIR`，默认 `/data`）。
- 采集默认每 12 小时一轮，启动时立即执行一轮；手动刷新走 `POST /api/v1/refresh`。

## 本地测试

```sh
cd modules && python -m pytest electricity/test_main.py -q
```

## 部署要点

1. 构建 `opscenter/module-electricity:<tag>` 并导入承载节点（`imagePullPolicy: Never`）。
2. 新增 Secret：模块凭据（账号 / 摘要 / 目标表计）与数据卷；沿用 `opscenter-jwt-keypair` 只读公钥。
3. 新增 Deployment + Service（与 `module-logs` 同构，`Recreate`、单副本、固定节点）。
4. 在网关 ConfigMap 中追加 `handle_path /modules/electricity/*` 路由块并热加载。
5. 后端启动时登记内置模块目录；前端将其显示在扩展导航、模块管理与值班首页。
6. 不改动其他模块的数据或资源。

## 回滚

删除本模块的 Deployment、Service、Secret、PVC 与网关路由块即可；
其他模块与工作台入口不受影响。
