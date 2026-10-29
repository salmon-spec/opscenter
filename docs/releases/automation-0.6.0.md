# 自动化作业0.6.0

模板可选择Ansible、Python或Shell；Python/Shell由现有Semaphore原生执行，在执行容器本地运行，脚本路径分别为.py与.sh。支持编辑脚本、输入参数、日志与暂停创建的计划。运行参数以单个JSON字符串追加为最后一个命令行参数；没有临时参数的计划运行须由脚本提供默认值。Python/Shell明确直接执行，不提供Ansible检查模式。示例仅输出参数到日志。
