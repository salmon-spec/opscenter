# 生活模块

`subscriptions`、`home_inventory`、`life_reminders`各自部署，复用本目录的固定上游传输、工作台认证和幂等记录。业务数据分别由Wallos、Homebox、Vikunja拥有，适配元数据使用各自独立PostgreSQL逻辑库。

前端由`LifeWorkspace.vue`、`LifeResources.vue`和`LifeCalendar.vue`提供工作台原生页面，通过同源模块API调用三个上游；上游仅作为业务服务运行。浏览器只持有工作台会话，专用上游API凭据保留服务端。工作台入口沿用当前管理员身份；上游团队/项目权限仍由上游账号体系执行。

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

## 业务管理接口（0.3.0）

`GET /api/v1/workspace/catalog`返回业务表单字段；`GET /api/v1/workspace/{resource}`读取记录，作用域参数只接受正整数；`POST`只允许create/update/delete三个命名操作，字段使用固定白名单与类型校验，编辑/删除必须带读取时的revision，写入进入既有幂等记录与目标锁。浏览器不能指定上游URL、任意请求头或凭据。

- Wallos：分类、付款方式、家庭成员、币种与汇率、预算；主页面提供订阅增改删、费用汇总、下次续费日历、CSV预览导入与导出。
- Homebox：标签、物品类型、物品模板、维护记录、集合设置与成员；主页面提供物品/位置增改删、复制、投保/转售、附件与维护、保修日历和清单导出。
- Vikunja：项目、标签、团队、评论、负责人、项目成员/团队权限及视图；主页面提供任务增改删、子任务、日期/进度/收藏、附件、日历、看板和统一提醒。

项目配置使用原来的上游数据卷和数据库。Vikunja服务API Key需要上述资源的精确业务权限；新Key必须在受保护Secret中更新，旧Key保留以供配置回滚。认证凭据管理不作为业务表单字段暴露。邮件、外部通知和第三方账号导入须单独配置与验收。
