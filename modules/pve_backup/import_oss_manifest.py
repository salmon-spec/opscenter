"""Import a verified `ossutil ls oss://pve-backup-qiushui/pve/` listing from stdin.

The object listing has metadata only; no OSS credentials are copied to this module.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from storage import PveStore

LINE = re.compile(r"^\d{4}-\d\d-\d\d\s+\d\d:\d\d:\d\d\s+\+\d{4}\s+\S+\s+(\d+)\s+\S+\s+\S+\s+(oss://pve-backup-qiushui/pve/\S+)$")
NAME = re.compile(r"/vzdump-(?:qemu|lxc)-(\d+)-(\d{4}_\d\d_\d\d-\d\d_\d\d_\d\d)\.(?:vma|tar)\.zst$")


def parse(lines: list[str]) -> list[dict]:
    objects = []
    seen = set()
    declared_count = None
    for line in lines:
        if line.startswith("Object Number is:"):
            if declared_count is not None:
                raise ValueError("OSS 清单包含重复计数")
            match_count = re.fullmatch(r"Object Number is:\s*(\d+)", line.strip())
            if not match_count:
                raise ValueError("OSS 清单计数无效")
            declared_count = int(match_count.group(1))
            continue
        if not line.startswith("20"):
            continue
        match = LINE.fullmatch(line.strip())
        if not match:
            raise ValueError("OSS 清单包含无法识别的对象行")
        size, object_name = match.groups()
        name = NAME.search(object_name)
        if not name or object_name in seen or int(size) <= 0:
            raise ValueError("OSS 清单对象格式无效或重复")
        seen.add(object_name)
        created = datetime.strptime(name.group(2), "%Y_%m_%d-%H_%M_%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        objects.append({"object_name": object_name, "vmid": name.group(1),
                        "size_bytes": int(size), "created_at": created.astimezone(timezone.utc)})
    if not objects or declared_count != len(objects):
        raise ValueError("OSS 清单为空或不完整，拒绝覆盖已有快照")
    return objects


if __name__ == "__main__":
    parsed = parse(sys.stdin.read().splitlines())
    store = PveStore()
    store.replace_oss_manifest(parsed, datetime.now(timezone.utc))
    print(f"imported {len(parsed)} OSS objects")
