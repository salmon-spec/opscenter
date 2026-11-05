# 运维门户模块宿主

0.5.0：门户直接承载已注册扩展模块。门户会话由统一认证服务验证；模块页面通过消息桥使用业务 API，模块不接收登录令牌。

注册模块的 `entry_url` 使用 `/modules/<key>/ui/` 或 `/v3/extension-modules/<key>/index.html`，`manifest.api_prefixes` 只能授权模块自己的 `/modules/<key>/` 路径。页面发送 `ops-module-ready`，接收宿主的 `ops-module-info`（module、nonce），每次请求回传 nonce。宿主按窗口身份及模块前缀检查请求；服务端再次读取注册目录、验证路径和权限，上游继续执行原有角色鉴权。下载使用 `ops-module-download-request`。

生活类页面共用现有 Vue 业务组件，通过独立门户入口和消息桥运行，包含上传、下载、幂等键及跨模块导航。构建：在 `frontend-vite` 执行 `node node_modules/vite/bin/vite.js build --config vite.portal.config.js`，将 `dist-portal` 连同模块源码加入 Docker 构建上下文。仅模块镜像更新即可发布；无需替换工作台前端。

工作台侧的隐藏入口设置保留。门户单独维护自己的应用显示设置。门户中的“门户管理 → 门户配置管理”和原工作台扩展管理入口共用同一目录及版本冲突保护。
