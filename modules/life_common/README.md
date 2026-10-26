# 生活模块

`subscriptions`、`home_inventory`、`life_reminders`各自部署，复用本目录的固定上游传输、工作台认证和幂等记录。业务数据分别由Wallos、Homebox、Vikunja拥有，适配元数据使用各自独立PostgreSQL逻辑库。

前端默认通过`LifeModule.vue`展示三个固定版本上游的完整界面；第二个页签保留原生`LifeWorkspace.vue`及聚合提醒。工作台API Key与登录令牌不下发上游界面；浏览器在独立来源使用各上游的原生会话。工作台入口沿用当前管理员身份。完整应用的用户/团队管理属于上游账号体系，不等同于工作台多用户权限。

## 构建

```sh
docker build --build-arg BACKEND_IMAGE=opscenter/backend:<commit> --build-arg MODULE_NAME=subscriptions -f modules/life_common/Dockerfile -t opscenter/module-subscriptions:<commit> .
python deploy/k8s/life_modules.py <commit> > life-modules.json
```

其他两个镜像将MODULE_NAME替换为`home_inventory`/`life_reminders`。生产清单固定三上游digest，单副本Recreate、独立PVC、内部Service与受限网络策略；新命名空间不改变原运维模块配额。

每个模块Secret为`module-<key>`，包含`db-url`、`upstream-token`、`jwt-public.pem`、`internal-token`、`credential-key`、`admin-user`；Homebox另需`homebox-pepper`，Vikunja另需`vikunja-jwt`，提醒模块另需两个只读来源Token。凭据仅在部署主机生成/保存，不能提交到Git。首次初始化须在内部临时打开上游注册，创建专用账户和API Key后关闭注册；生产入口最后接通。

## 操作与提醒

- 写入要求`Idempotency-Key` UUID。超时后操作记录保留pending，阻止盲目重复创建或顺延；用户先刷新核对业务数据，再发起新操作。创建物品涉及两次上游API，失败时`pending-item:<操作ID>`可定位已创建的物品。
- 订阅CSV先预览重名/错误，每批500条；导入可能部分完成，失败后核对清单再重新预览。导出处理CSV公式前缀。
- Homebox列表未包含保修信息，按上游updatedAt缓存详情，最多512条、并发4。大规模物品场景应另行评测；响应/附件/导出包分别限制12MiB/5MiB/50MiB。
- 重复日期由Vikunja推进；按月/年任务在完成时按上海时区与原日/月日锚点计算本周期秒数，月底与闰年钳制到有效日期。该间隔交给上游完成操作推进，不另设任务生成器。
- 提醒每分钟扫描；日期型按上海09:00，22:00–08:00外部免打扰。首次不补发激活前历史提醒。来源失败时暂缓该来源投递，保留其他来源。
- 默认仅站内。Webhook由用户在通知设置中配置，URL加密保存；只向目标发送标题、来源、时间和稳定投递ID。HTTPS目标经过DNS/IP检查和固定地址连接；内网目标必须部署时显式允许。外部投递至少一次，超时可能重复，最多3次重试。
- 完成/停用/来源改期会取消旧待投递计划；稍后提醒不更改原业务日期。“创建处理待办”按来源与发生日期幂等。

## 验证与恢复

单测：`PYTHONPATH=modules python -m pytest modules/life_common`，前端`node --test frontend-vite/tests/life.test.mjs`；CI另构建并以10001用户导入三个镜像。

恢复必须同时包含三个上游SQLite及文件、三份适配库、Secret/加密密钥、版本digest和配置。备份时使用上游在线备份/SQLite backup或短暂停模块，不能直接复制正在写入的SQLite文件。上游迁移回滚须恢复配套数据；回退界面不能替代数据库回滚。PVC和用户数据不随隐藏入口或缩容删除。

## 上游与许可

- [Wallos v5.10.2](https://github.com/ellite/Wallos/tree/v5.10.2)：GPL-3.0，使用官方API。
- [Homebox v0.27.1](https://github.com/sysadminsmedia/homebox/tree/v0.27.1)：AGPL-3.0，使用`/api/v1/entities`等官方API。
- [Vikunja v2.7.1](https://github.com/go-vikunja/vikunja/tree/v2.7.1)：AGPL-3.0，使用`/api/v1`官方API。

本项目不复制修改这三个上游的业务源码；镜像保留上游声明。首次部署与升级仍应检查固定版本的许可、安全和兼容性。

## 完整应用网关（0.2.0）

HTTP节点端口31191/31192/31193，HTTPS节点端口31591/31592/31593，依次对应Wallos/Homebox/Vikunja。仅允许配置的工作台来源内嵌，60秒一次性启动凭证经POST交换，不放入URL。HttpOnly网关会话期限不超过原工作台JWT且最多8小时。原版会话独立于工作台；网关只向本上游转发其Cookie，写入需匹配应用来源。Caddy承载静态文件、流式下载和WebSocket，应用原版路径保持完整。

模块Secret另需native-login JSON（原版username/password），命名空间另需life-native-tls服务器证书与私钥。凭据只保留服务端。管理员可在原版管理注册/账号；所有原版路径仍受工作台派生会话保护。HTTP用于既有VPN内HTTP工作台，HTTPS页面选择HTTPS应用入口；摄像头/剪贴板等浏览器安全上下文能力须HTTPS与客户端正常证书信任。外部邮件/通知/AI/SSO/第三方导入须配置各自服务与网络，不由界面接入自动启用。

Vikunja的PUBLICURL使用HTTP原版入口，完整界面启动时按当前应用来源选择API；HTTPS内嵌继续使用HTTPS同源API。对外分享/CalDAV链接如需优先HTTPS可将PUBLICURL调整为HTTPS入口并验收客户端。共享链接仍处于工作台网关保护范围，不自动匿名公开到互联网。
