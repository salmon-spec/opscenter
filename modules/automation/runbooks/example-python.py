#!/usr/bin/env python3
# opscenter-parameters: [{"name":"message","label":"输出内容","type":"text","default":"Hello OpsCenter","required":false,"description":"脚本输出到执行日志","options":[]}]
import json
import sys

parameters = json.loads(sys.argv[-1]) if len(sys.argv) > 1 else {}
print("PYTHON_OK " + json.dumps(parameters, ensure_ascii=False), flush=True)
