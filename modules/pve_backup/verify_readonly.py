"""One-off PVE token verification; never prints the credential."""
from urllib.error import HTTPError
from urllib.request import Request

from main import PveClient


def main():
    client = PveClient.from_env()
    if client is None:
        raise RuntimeError("PVE token is not configured")
    guests = client.get("/cluster/resources", {"type": "vm"})
    nodes = client.get("/cluster/resources", {"type": "node"})
    if not isinstance(guests, list) or not isinstance(nodes, list):
        raise RuntimeError("PVE read response is invalid")
    # Never target a real VM: even an overprivileged token cannot stop an absent VM.
    if any(str(item.get("vmid")) == "999999" for item in guests):
        raise RuntimeError("Reserved write-probe VMID is in use")
    request = Request(f"{client.origin}/api2/json/nodes/pve/qemu/999999/status/stop",
                      data=b"", method="POST", headers={"Authorization": client.token})
    try:
        with client.opener.open(request, timeout=client.timeout):
            raise RuntimeError("PVE token unexpectedly accepted a write request")
    except HTTPError as error:
        if error.code != 403:
            raise RuntimeError(f"PVE write probe returned {error.code}, not 403") from error
    print(f"read-only verified: {len(guests)} VM resources, {len(nodes)} nodes, write HTTP 403")


if __name__ == "__main__":
    main()
