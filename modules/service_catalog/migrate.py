from datetime import datetime, timezone

from main import get
from storage import CatalogStore


def main():
    store = CatalogStore()
    store.migrate()
    snapshots = (
        ("services", "/services/plaza", None),
        ("health-overview:24", "/services/plaza/health-overview", {"hours": 24}),
        ("hidden", "/services/plaza/hidden", None),
    )
    imported = {}
    for key, path, query in snapshots:
        data = get(path, query)
        if not isinstance(data, (list, dict)):
            raise ValueError(f"Invalid service catalog source payload: {key}")
        store.save_snapshot(key, data, datetime.now(timezone.utc))
        imported[key] = len(data) if isinstance(data, list) else len(data.get("items", []))
    print(f"Imported service catalog snapshots: {imported}")


if __name__ == "__main__":
    main()
