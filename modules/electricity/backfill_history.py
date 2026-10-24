"""Backfill published monthly bills through the existing read-only year query.

Run in the electricity container, or pipe this file to its Python stdin.
Existing monthly periods are preserved; the normal history reader deduplicates.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime

from aiohttp import ClientSession

from collector import BILL_API, WebChannel, bill_payload, business, latest_bills
from storage import ElectricityStore, SessionStore, TZ


async def backfill(store: ElectricityStore, start: int, end: int) -> dict:
    if not store.configured():
        raise RuntimeError("electricity credentials are not configured")
    creds = store.credentials()
    before = len(store.monthly_history(0))
    results = []
    async with ClientSession() as session:
        channel = WebChannel(None, session, creds["account"], creds["password_digest"],
                             SessionStore(store.session_path))
        if not await channel.async_login():
            raise RuntimeError("history login failed")
        for year in range(end, start - 1, -1):
            try:
                response = await channel.async_call(BILL_API, bill_payload(channel, creds["target_meter"], year))
                rows = latest_bills(business(response).get("mothEleList") or [])
                if any(not str(row["month"]).startswith(str(year)) for row in rows):
                    raise ValueError("unexpected bill year")
                stamp = datetime.now(TZ).isoformat(timespec="seconds")
                store.append_monthly_history([{**row, "collected_at": stamp} for row in rows])
                result = {"year": year, "months": len(rows), "status": "ok" if rows else "empty"}
            except Exception as exc:
                # Do not expose upstream responses, identity or credentials.
                result = {"year": year, "status": "failed", "error": type(exc).__name__}
            results.append(result)
            print(json.dumps(result), flush=True)
    after = store.monthly_history(0)
    return {"before": before, "after": len(after), "added": len(after) - before,
            "oldest_month": after[-1]["month"] if after else None, "results": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=2000)
    parser.add_argument("--end-year", type=int, default=datetime.now(TZ).year - 1)
    args = parser.parse_args()
    if not 2000 <= args.start_year <= args.end_year <= datetime.now(TZ).year:
        parser.error("years must be between 2000 and the current year")
    print(json.dumps(asyncio.run(backfill(ElectricityStore(), args.start_year, args.end_year))))
