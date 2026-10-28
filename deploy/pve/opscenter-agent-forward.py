#!/usr/bin/python3
"""Persist narrow SSH/Agent forwarding for managed guests on this PVE host."""
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys

SOURCES = ("192.168.1.155", "192.168.1.157", "192.168.1.158", "192.168.1.159", "192.168.1.160", "192.168.1.163")
ROOT = Path("/var/lib/opscenter")


def rules(vmid, address):
    address = str(ipaddress.IPv4Address(address))
    if int(vmid) < 100 or ipaddress.ip_address(address) not in ipaddress.ip_network("192.168.1.0/24"):
        raise ValueError("Guest identity or subnet invalid")
    result = []
    for source in SOURCES:
        for direction in (["-s", source, "-d", address, "--dports", "22,19100"],
                          ["-s", address, "-d", source, "--sports", "22,19100", "-m", "conntrack", "--ctstate", "ESTABLISHED"]):
            result.append(["-p", "tcp", "-m", "multiport", *direction,
                           "-m", "comment", "--comment", "opscenter-agent:" + str(int(vmid)), "-j", "ACCEPT"])
    return result


def main():
    ROOT.mkdir(mode=0o700, exist_ok=True)
    with open("/run/lock/opscenter-agent-forward.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = ROOT / "agent-forward.json"
        old = json.loads(path.read_text()) if path.exists() else {}
        targets = dict(old)
        if len(sys.argv) == 3:
            vmid, address = sys.argv[1:]
            rules(vmid, address)
            targets[str(int(vmid))] = str(ipaddress.IPv4Address(address))
        elif len(sys.argv) != 1:
            raise ValueError("usage: opscenter-agent-forward [VMID IPv4]")
        added = []
        prefix = ["iptables", "-w", "30"]
        try:
            for vmid, address in targets.items():
                for rule in rules(vmid, address):
                    if subprocess.run([*prefix, "-C", "K8S-VM-FORWARD", *rule], stderr=subprocess.DEVNULL).returncode:
                        subprocess.run([*prefix, "-I", "K8S-VM-FORWARD", "1", *rule], check=True)
                        added.append(rule)
            temporary = ROOT / "agent-forward.json.tmp"
            temporary.write_text(json.dumps(targets))
            temporary.chmod(0o600)
            os.replace(temporary, path)
        except Exception:
            for rule in reversed(added):
                subprocess.run([*prefix, "-D", "K8S-VM-FORWARD", *rule], check=False)
            raise
        for vmid, address in old.items():
            if targets.get(vmid) != address:
                for rule in rules(vmid, address):
                    if subprocess.run([*prefix, "-C", "K8S-VM-FORWARD", *rule], stderr=subprocess.DEVNULL).returncode == 0:
                        subprocess.run([*prefix, "-D", "K8S-VM-FORWARD", *rule], check=True)


if __name__ == "__main__":
    main()
