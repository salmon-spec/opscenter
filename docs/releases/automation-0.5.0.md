# 自动化作业 0.5.0

脚本与参数同屏编辑，桌面双栏、窄屏纵向排列；支持文本、数字、开关、主机和选项类型，默认值和运行表单保持同步。Tab缩进、Ctrl/⌘+S保存、未保存提示、参数增删及字段校验。

参数定义存储在主playbook首行注释中，和所选脚本一起进行Git提交。多个文件保存采用版本核对、仓库写锁及失败恢复，避免出现脚本与参数只保存一部分。参数头无效时编辑器仍可打开修复；旧模板可继续使用JSON运行参数。

执行概览、模板执行记录入口、历史模板/状态/时间筛选，以及日志搜索、跟随、复制、文本下载。下载复用工作台已有消息桥与认证接口。

参考调研（2026-10-28页面显示的约数）：Semaphore约14.3k星、AWX约15.6k星、Rundeck约6.4k星；参考其参数表单、模板/任务分区和日志交互，自行实现当前工作台适用功能。

- [Semaphore 任务模板](https://semaphoreui.com/docs/user-guide/task-templates)
- [AWX 界面](https://docs.ansible.com/projects/awx/en/24.6.1/userguide/main_menu.html)
- [AWX 模板与默认参数](https://docs.ansible.com/projects/awx/en/24.6.1/userguide/job_templates.html)
- [Rundeck 日志API](https://docs.rundeck.com/docs/api/)
