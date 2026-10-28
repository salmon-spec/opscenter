#!/usr/bin/env python3
"""PVE forced-command collector: read only; never accepts remote arguments."""
import hashlib
import ipaddress
import json
import re
import subprocess
import time
from pathlib import Path


def pve(path, **options):
    args = ["pvesh", "get", path, "--output-format", "json"]
    for key, value in options.items():
        args.extend(["--" + key, str(value)])
    return json.loads(subprocess.check_output(args, timeout=15, stderr=subprocess.PIPE))


def collect():
    nodes = pve("/nodes")
    if not nodes or any(n["status"] != "online" for n in nodes):
        raise ValueError("PVE node offline; inventory is incomplete")
    resources = [v for v in pve("/cluster/resources", type="vm") if v["type"] == "qemu"]
    vms = []
    for node in nodes:
        for vm in pve(f"/nodes/{node['node']}/qemu"):
            config = pve(f"/nodes/{node['node']}/qemu/{vm['vmid']}/config")
            ips = []
            for key, value in config.items():
                if re.fullmatch(r"ipconfig\d+", key):
                    for address in re.findall(r"(?:^|,)ip=([^,]+)", value):
                        if address not in {"dhcp", "manual"}:
                            ip = ipaddress.ip_interface(address).ip
                            if ip.version == 4 and not ip.is_loopback and not ip.is_link_local:
                                ips.append(str(ip))
            vms.append({"vmid": int(vm["vmid"]), "node": node["node"],
                        "name": vm.get("name") or f"VM{vm['vmid']}", "status": vm["status"],
                        "template": bool(vm.get("template")), "ips": ips,
                        "cpus": int(config.get("cores", 1)) * int(config.get("sockets", 1)),
                        "memory_mib": int(config.get("memory", 0))})
    if {(v["node"], v["vmid"]) for v in vms} != {(v["node"], v["vmid"]) for v in resources}:
        raise ValueError("PVE inventory changed while collecting; retry later")
    return {"complete": True, "collected_at": time.time(),
            "source_id": hashlib.sha256(Path('/etc/machine-id').read_bytes().strip()).hexdigest()[:20],
            "vms": vms}


if __name__ == "__main__":
    try:
        print(json.dumps(collect(), ensure_ascii=False))
    except Exception as error:
        raise SystemExit("PVE inventory failed: " + type(error).__name__)
