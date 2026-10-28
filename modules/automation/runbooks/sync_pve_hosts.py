#!/usr/bin/env python3
"""Reconcile one trusted PVE inventory with OpsCenter; default is preview."""
import argparse
import ipaddress
import json
import re
import subprocess
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

FIELDS = ("name", "host", "ssh_port", "tags", "remark", "lan_ip", "wireguard_ip",
          "preferred_management_channel", "management_address_override", "cluster_id",
          "kubernetes_node_name", "node_role", "runtime_type")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def request(base, path, token=None, payload=None, method=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                  headers=headers, method=method)
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=15) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError("API response too large")
            return json.loads(raw)
    except HTTPError as error:
        raise RuntimeError(f"OpsCenter API HTTP {error.code}; no automatic write retry") from None


def plan(snapshot, hosts, config, allow_empty=False):
    if (snapshot.get("complete") is not True or snapshot.get("source_id") != config["source_id"]
            or not 0 <= time.time() - snapshot.get("collected_at", 0) < 180):
        raise ValueError("PVE inventory incomplete, expired or from the wrong source")
    vms = snapshot["vms"]
    if not isinstance(vms, list) or (not vms and not allow_empty):
        raise ValueError("Empty PVE inventory requires explicit --allow-empty")
    ids = [v["vmid"] for v in vms]
    if any(type(n) is not int or n < 100 for n in ids) or len(ids) != len(set(ids)):
        raise ValueError("Invalid or duplicate PVE VMID")
    source_tag = "pve-source:" + config["source_id"]
    legacy = config.get("legacy_vmids", {})
    indexed = {}
    for host in hosts:
        tags = host.get("tags") or []
        if source_tag in tags:
            matches = [t for t in tags if re.fullmatch(r"pve-vmid:\d+", t)]
            if len(matches) != 1:
                raise ValueError("Invalid PVE host identity")
            vmid = int(matches[0].split(":")[1])
        elif host["id"] in legacy:
            vmid = int(legacy[host["id"]])
        else:
            continue
        if vmid in indexed or host.get("is_local") or host.get("agent_type") == "local":
            raise ValueError("Ambiguous PVE mapping or protected local host")
        indexed[vmid] = host
    actions, claimed = [], {h["id"] for h in indexed.values()}
    for vm in vms:
        if vm.get("template"):
            continue  # Templates stay in the presence set so conversion never causes removal.
        vmid = vm["vmid"]
        ips = [str(ipaddress.IPv4Address(ip)) for ip in vm["ips"]]
        current = indexed.get(vmid)
        if current is None:
            matches = [h for h in hosts if h["id"] not in claimed and set(ips).intersection(
                {h.get("host"), h.get("lan_ip"), h.get("wireguard_ip")})]
            if len(matches) > 1 or any(any(t.startswith("pve-source:") for t in h.get("tags", [])) for h in matches):
                raise ValueError("IP matches multiple hosts or another PVE source")
            current = matches[0] if matches else None
        if current:
            if current.get("is_local") or current.get("agent_type") == "local":
                raise ValueError("Protected local host matched a guest IP")
            claimed.add(current["id"])
            values = {key: current[key] for key in FIELDS if current.get(key) is not None}
        else:
            address = ips[0] if ips else f"pve-{config['source_id']}-vm{vmid}.invalid"
            values = {"name": vm["name"][:50], "host": address, "lan_ip": ips[0] if ips else "",
                      "ssh_port": 22, "tags": [], "remark": ""}
        tags = [t for t in values.get("tags", []) if not t.startswith("pve-vmid:")]
        values["tags"] = list(dict.fromkeys([*tags, source_tag, f"pve-vmid:{vmid}"]))
        note = f"[PVE sync] VM{vmid} / {vm['name']} / {vm['cpus']} vCPU / {vm['memory_mib']} MiB"
        if not ips and values["host"].endswith(".invalid"):
            note += " / 待补管理IP"
        remark = re.sub(r"(?:\n)?\[PVE sync\][^\n]*", "", values.get("remark", ""))
        values["remark"] = (remark + "\n" + note).strip()
        if len(values["remark"]) > 500 or len(values["tags"]) > 30:
            raise ValueError("Host notes or tags would exceed the supported limit")
        host_id = current["id"] if current else str(uuid.uuid4())
        if not current or any(values[k] != current.get(k) for k in values):
            actions.append({"action": "update" if current else "create", "vmid": vmid,
                            "id": host_id, "values": values})
    for vmid, host in indexed.items():
        if vmid not in ids:
            actions.append({"action": "archive", "vmid": vmid, "id": host["id"]})
    return actions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-empty", action="store_true")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    base = config["opscenter_url"].rstrip("/")
    url = urlsplit(base)
    if url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"web", "localhost", "127.0.0.1"}):
        raise ValueError("OpsCenter requires HTTPS, or the internal web service")
    if url.username or url.password or url.query or url.fragment:
        raise ValueError("Invalid OpsCenter URL")
    raw = subprocess.check_output(["ssh", "-F", "/dev/null", "-T", "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10", "-o", "IdentitiesOnly=yes",
        "-o", "UserKnownHostsFile=" + config["known_hosts"], "-i", config["ssh_key"],
        config["pve_ssh"]], timeout=120, stderr=subprocess.PIPE)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("PVE inventory too large")
    snapshot = json.loads(raw)
    login = {"username": Path(config["username_file"]).read_text().strip(),
             "password": Path(config["password_file"]).read_text().strip()}
    token = request(base, "/api/v2/auth/login", payload=login)["access_token"]
    result = request(base, "/modules/host-inventory/api/v1/hosts", token)
    if result.get("status") not in {"ok", "degraded"} or not isinstance(result.get("items"), list):
        raise ValueError("Host inventory unavailable; no changes applied")
    actions = plan(snapshot, result["items"], config, args.allow_empty)
    print(json.dumps({"mode": "apply" if args.apply else "preview", "pve_vms": len(snapshot["vms"]),
        "actions": [{k: v for k, v in a.items() if k != "values"} for a in actions]}, ensure_ascii=False), flush=True)
    if not args.apply:
        return
    for action in actions:
        host_id = action["id"]
        prefix = "/modules/host-inventory/api/v1/hosts"
        if action["action"] == "archive":
            request(base, prefix + f"/{host_id}/archive", token, {}, "POST")
        elif action["action"] == "create":
            request(base, prefix, token, {**action["values"], "registration_id": host_id}, "POST")
        else:
            request(base, prefix + f"/{host_id}", token, action["values"], "PUT")
        print(f"DONE {action['action']} VM{action['vmid']} {host_id}", flush=True)
    after = request(base, "/modules/host-inventory/api/v1/hosts", token)
    if after.get("status") not in {"ok", "degraded"} or plan(snapshot, after["items"], config, args.allow_empty):
        raise RuntimeError("Post-sync verification failed; inspect completed actions before retry")
    ensure_agents(base, token, snapshot, after["items"])
    print("SYNC_OK: host inventory matches PVE", flush=True)


def ensure_agents(base, token, snapshot, hosts):
    source_tag = "pve-source:" + snapshot["source_id"]
    running = {f"pve-vmid:{v['vmid']}" for v in snapshot["vms"] if v["status"] == "running" and not v.get("template")}
    for host in hosts:
        tags = set(host.get("tags") or [])
        if source_tag not in tags or not tags.intersection(running):
            continue
        server_id = host["id"]
        if host.get("agent_status") != "running":
            if host.get("agent_status") != "deploying":
                request(base, f"/api/v2/servers/{server_id}/upgrade-agent", token, {}, "POST")
            deadline = time.monotonic() + 240
            while True:
                status = request(base, f"/api/v2/servers/{server_id}/agent/upgrade/status", token)
                task_status = (status.get("task") or {}).get("status")
                if task_status == "success" and status.get("agent_status") == "running":
                    break
                if task_status == "failed" or time.monotonic() >= deadline:
                    print(f"AGENT_FAILED {server_id}: check the host Agent task", flush=True)
                    raise RuntimeError("Agent installation failed or timed out")
                time.sleep(2)
        metrics = request(base, f"/api/v2/servers/{server_id}/agent-metrics", token)
        if not any((metrics.get("metrics") or {}).get(key) is not None for key in ("cpu", "memory", "disk")):
            raise RuntimeError("Agent monitoring metrics unavailable")
        print(f"AGENT_OK {server_id}: authenticated monitoring metrics received", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Avoid leaking credentials or response bodies into the job log.
        raise SystemExit("SYNC_FAILED: " + type(error).__name__ + "; inspect inventory and configuration")
