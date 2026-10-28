"""Runbook-owned parameter definitions, stored as a portable comment header."""
import json
import math
import uuid
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

HEADER = '# opscenter-parameters: '


class Parameter(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
    label: str = Field(min_length=1, max_length=100)
    type: Literal['text', 'number', 'boolean', 'host', 'choice'] = 'text'
    default: str | int | float | bool = ''
    required: bool = False
    description: str = Field(default='', max_length=300)
    options: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode='after')
    def valid(self):
        if self.type == 'choice' and (not self.options or len(set(self.options)) != len(self.options)):
            raise ValueError('选择参数需要非空且不重复的选项')
        if any(not v or len(v) > 253 for v in self.options):
            raise ValueError('选项长度为1至253字符')
        validate_value(self, self.default, allow_empty=True)
        return self


def validate_value(parameter, value, allow_empty=False):
    if value == '' and (allow_empty or not parameter.required): return
    valid = (isinstance(value, str) if parameter.type == 'text' else
             isinstance(value, bool) if parameter.type == 'boolean' else
             isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) if parameter.type == 'number' else
             isinstance(value, str) and value in parameter.options if parameter.type == 'choice' else
             isinstance(value, str))
    if parameter.type == 'host' and valid:
        try: uuid.UUID(value)
        except (ValueError, TypeError): valid = False
    if not valid or (parameter.required and value == ''):
        raise ValueError(parameter.label + '的类型、选项或必填值无效')
    if isinstance(value, str) and len(value) > 4000: raise ValueError('参数值过长')


def definitions(values):
    try:
        if not isinstance(values, list) or len(values) > 32: raise ValueError('最多32个参数')
        result = [Parameter.model_validate(v) for v in values]
        if len({p.name for p in result}) != len(result): raise ValueError('参数名称重复')
        if len(json.dumps(values)) > 16000: raise ValueError('参数定义超过16KB')
        return [p.model_dump() for p in result]
    except ValueError:
        raise HTTPException(422, '参数定义无效：请核对名称、类型、默认值、选项及长度') from None


def header_index(lines):
    index = 1 if lines and lines[0].startswith('#!') else 0
    return index if len(lines) > index and lines[index].startswith(HEADER) else None


def read_parameters(content):
    lines = content.splitlines(keepends=True); index = header_index(lines)
    if index is None: return []
    try: return definitions(json.loads(lines[index][len(HEADER):]))
    except (ValueError, TypeError): raise HTTPException(422, '脚本参数头格式无效，请在编辑脚本中修复') from None


def with_parameters(content, parameters):
    values = definitions(parameters); lines = content.splitlines(keepends=True); index = header_index(lines)
    line = HEADER + json.dumps(values, ensure_ascii=False, separators=(',', ':')) + '\n'
    if index is not None: lines[index] = line
    else:
        index = 1 if lines and lines[0].startswith('#!') else 0
        if index and not lines[0].endswith('\n'): lines[0] += '\n'
        lines.insert(index, line)
    return ''.join(lines)


def resolve_parameters(schema, supplied):
    if not schema: return supplied
    if set(supplied) - {p['name'] for p in schema}: raise HTTPException(422, '存在未声明参数，请先在编辑脚本中添加')
    result = {}
    try:
        for value in schema:
            p = Parameter.model_validate(value); selected = supplied.get(p.name, p.default)
            validate_value(p, selected)
            if selected != '': result[p.name] = selected
    except ValueError as error: raise HTTPException(422, str(error)) from None
    return result
