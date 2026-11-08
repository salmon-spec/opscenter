# 运维门户模块宿主

0.5.0：门户直接承载已注册扩展模块。门户会话由统一认证服务验证；模块页面通过消息桥使用业务 API，模块不接收登录令牌。

注册模块的 `entry_url` 使用 `/modules/<key>/ui/` 或 `/v3/extension-modules/<key>/index.html`，`manifest.api_prefixes` 只能授权模块自己的 `/modules/<key>/` 路径。页面发送 `ops-module-ready`，接收宿主的 `ops-module-info`（module、nonce），每次请求回传 nonce。宿主按窗口身份及模块前缀检查请求；服务端再次读取注册目录、验证路径和权限，上游继续执行原有角色鉴权。下载使用 `ops-module-download-request`。

生活类页面共用现有 Vue 业务组件，通过独立门户入口和消息桥运行，包含上传、下载、幂等键及跨模块导航。构建：在 `frontend-vite` 执行 `node node_modules/vite/bin/vite.js build --config vite.portal.config.js`，将 `dist-portal` 连同模块源码加入 Docker 构建上下文。仅模块镜像更新即可发布；无需替换工作台前端。

工作台侧的隐藏入口设置保留。门户单独维护自己的应用显示设置。门户中的“门户管理 → 门户配置管理”和原工作台扩展管理入口共用同一目录及版本冲突保护。


## 外部应用的门户入口（0.5.1）

独立 `portal-app-gateway` 仅代理固定的 6 个内网服务：Jenkins 30093、Forgejo 30094、Nexus 30095、SonarQube 30096、Grafana 30097、MinIO 30098。每个端口是独立浏览器来源，与门户 30092 及其他应用隔离。应用仍使用原有认证、CSRF、角色和资源权限。

新入口的响应只允许门户 `http://10.66.66.13:30092` 及自身来源作为框架祖先；保留上游 CSP 的其他指令。原始服务及其 X-Frame-Options 不改动。转发 Host/协议/端口及原地址重定向，保留登录会话与实时连接。网关无门户 API 和登录令牌，网络策略仅允许 WireGuard/指定内网来源。原地址保存在目录 aliases 中。
