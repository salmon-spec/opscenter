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
        wg_path = ROOT / 'wireguard-forward.json'
        wg_old = json.loads(wg_path.read_text()) if wg_path.exists() else {}
        wg_targets = dict(wg_old)
        if len(sys.argv) == 3:
            vmid, address = sys.argv[1:]
            rules(vmid, address)
            targets[str(int(vmid))] = str(ipaddress.IPv4Address(address))
        elif len(sys.argv) == 6 and sys.argv[1] == '--wireguard':
            vmid, address, endpoint, port = sys.argv[2:]
            wg_rules(vmid, address, endpoint, port)
            wg_targets[str(int(vmid))] = [address, endpoint, int(port)]
        elif len(sys.argv) != 1:
            raise ValueError("usage: opscenter-agent-forward [VMID IPv4 | --wireguard VMID IPv4 Endpoint Port]")
        added = []
        prefix = ["iptables", "-w", "30"]
        try:
            sets = [rules(vmid, address) for vmid, address in targets.items()]
            sets += [wg_rules(vmid, *values) for vmid, values in wg_targets.items()]
            for rule_set in sets:
                for rule in rule_set:
                    if subprocess.run([*prefix, "-C", "K8S-VM-FORWARD", *rule], stderr=subprocess.DEVNULL).returncode:
                        subprocess.run([*prefix, "-I", "K8S-VM-FORWARD", "1", *rule], check=True)
                        added.append(rule)
            temporary = ROOT / "agent-forward.json.tmp"
            temporary.write_text(json.dumps(targets))
            temporary.chmod(0o600)
            os.replace(temporary, path)
            if wg_targets != wg_old:
                temporary = ROOT / 'wireguard-forward.json.tmp'
                temporary.write_text(json.dumps(wg_targets)); temporary.chmod(0o600)
                os.replace(temporary, wg_path)
        except Exception:
            for rule in reversed(added):
                subprocess.run([*prefix, "-D", "K8S-VM-FORWARD", *rule], check=False)
            raise
        for vmid, address in old.items():
            if targets.get(vmid) != address:
                for rule in rules(vmid, address):
                    if subprocess.run([*prefix, "-C", "K8S-VM-FORWARD", *rule], stderr=subprocess.DEVNULL).returncode == 0:
                        subprocess.run([*prefix, "-D", "K8S-VM-FORWARD", *rule], check=True)
        for vmid, values in wg_old.items():
            if wg_targets.get(vmid) != values:
                for rule in wg_rules(vmid, *values):
                    if subprocess.run([*prefix, '-C', 'K8S-VM-FORWARD', *rule], stderr=subprocess.DEVNULL).returncode == 0:
                        subprocess.run([*prefix, '-D', 'K8S-VM-FORWARD', *rule], check=True)


def wg_rules(vmid, address, endpoint, port):
    rules(vmid, address)
    endpoint = str(ipaddress.IPv4Address(endpoint))
    if not ipaddress.ip_address(endpoint).is_global or not 1 <= int(port) <= 65535:
        raise ValueError('WireGuard endpoint must be public IPv4 and a valid UDP port')
    comment = ['-m', 'comment', '--comment', 'opscenter-wg:' + str(int(vmid)), '-j', 'ACCEPT']
    return [
        ['-s', address, '-d', endpoint, '-p', 'udp', '--dport', str(int(port)), '-m', 'physdev', '--physdev-out', 'nic0', '--physdev-is-bridged', *comment],
        ['-s', endpoint, '-d', address, '-p', 'udp', '--sport', str(int(port)), '-m', 'conntrack', '--ctstate', 'ESTABLISHED', *comment],
    ]


if __name__ == "__main__":
    main()
