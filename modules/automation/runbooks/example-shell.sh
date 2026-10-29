#!/bin/bash
# opscenter-parameters: [{"name":"message","label":"输出内容","type":"text","default":"Hello OpsCenter","required":false,"description":"脚本输出到执行日志","options":[]}]
set -eu
parameters="{}"
for parameters do :; done
printf 'SHELL_OK %s\n' "$parameters"
