"""Import an operator-verified recovery drill without inventing a time of day."""
from __future__ import annotations

import argparse
import re
from datetime import date

from storage import PveStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vmid", required=True)
    parser.add_argument("--level", required=True,
                        choices=("archive_verified", "isolated_boot", "business_verified"))
    parser.add_argument("--verified-on", required=True, type=date.fromisoformat)
    parser.add_argument("--source", required=True)
    parser.add_argument("--note", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[1-9]\d{1,8}", args.vmid):
        parser.error("VMID 无效")
    if not args.source.strip() or not args.note.strip():
        parser.error("证据来源与验证边界不能为空")
    store = PveStore()
    store.migrate()
    store.set_evidence(vmid=args.vmid, level=args.level,
                       verified_on=args.verified_on, source=args.source, note=args.note)
    print(f"imported recovery evidence for VM {args.vmid}: {args.level} on {args.verified_on}")


if __name__ == "__main__":
    main()
