# 自动化作业 0.4.0

运行表单支持 JSON 参数，键为变量名称、值为字符串/数字/布尔值，最多32项、16KB。参数序列化为 Semaphore task environment，经 Ansible extra vars 传入，不拼接命令行。

WireGuard 模板 wireguard-install.yml 调用可编辑 Python 脚本 install_wireguard.py，参数为 server_id、hub_id、interface、pool、endpoint。页面从正式主机资产源选择主机，Hub/Endpoint 默认值从部署环境读取，可逐次修改。默认检查模式预览，不安装软件或预留地址。

受保护安装接口负责读取已保存凭据、验证 SSH 主机密钥、串行分配、持久配置与在线增加 Hub Peer、握手与连通性验证；原 PVE 同步脚本及历史保持。
