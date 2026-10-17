"""State Grid balance / bill / daily-usage collection core.

Reused from the collector that already ran on the Codex host
(`/home/ubuntu/sgcc-standalone`), including the vendored web channel and the
national-crypto helpers it depends on. Only the scheduling, storage wrapper and
the optional introspection (raw capture / extra-API probe) differ.

v0.3.0 additions:
* opt-in raw payload capture (redacted) so field shapes can be verified instead
  of guessed — the repository had zero real fixtures before this.
* a probe for the two `AUTH_APIS` endpoints the module never called, to find out
  whether tiered (阶梯) / time-of-use (分时) data is reachable at all.
* heuristic parsers for those two datasets: they only produce data when a
  clearly-shaped structure is present, otherwise everything degrades to None and
  the flat-rate forecast keeps working.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from datetime import datetime, timedelta, timezone

from aiohttp import ClientSession

from storage import ElectricityStore, SessionStore
from vendor.web_api import WebChannel

BALANCE_API = "/osg-open-bc0001/member/c05/f01"
BILL_API = "/osg-open-bc0001/member/c01/f02"
DAILY_API = "/osg-web0004/member/c24/f01"

# Registered in web_api.AUTH_APIS but never called by this module. The probe uses
# them to answer "is tiered / time-of-use data available at all?".
EXTRA_APIS = {
    "uc-c9f02": "/osg-open-uc0001/member/c9/f02",
    "bc-c04f03": "/osg-open-bc0001/member/c04/f03",
}

OK = {"1", "0000", "000000"}
ACCOUNT_CODES = {
    "order": "0101154", "uploadPic": "0101296", "pauseSCode": "0101250",
    "pauseTCode": "0101251", "listconsumers": "0101093", "messageList": "0101343",
    "submit": "0101003", "sbcMsg": "0101210", "powercut": "0104514",
    "BkAuth01": "f15", "BkAuth02": "f18", "BkAuth03": "f02", "BkAuth04": "f17",
    "BkAuth05": "f05", "BkAuth06": "f16", "BkAuth07": "f01", "BkAuth08": "f03",
}

TZ = dt.timezone(dt.timedelta(hours=8))
DAILY_WINDOW_DAYS = 40
DAILY_KEEP = 30
BILL_KEEP = 3

LOGIN_WINDOW_HOURS = 12
_login_events: list[datetime] = []

# Field-name hints. The upstream names were never confirmed, so matching is
# deliberately loose; a structure must look unmistakable to be accepted.
STEP_HINT = re.compile(r"(step|stage|ladder|tier|jie|阶梯)", re.IGNORECASE)
STEP_STAGE = re.compile(r"(step|stage|level|no|index|阶梯|档)", re.IGNORECASE)
STEP_USED = re.compile(r"(used|history|already|done|consumed|已用|使用)", re.IGNORECASE)
STEP_REMAIN = re.compile(r"(remain|left|rest|surplus|balance|剩余)", re.IGNORECASE)
STEP_PRICE = re.compile(r"(price|dj|rate|cost|money|amt|fee|价)", re.IGNORECASE)
# Value-bearing keys must never be mistaken for the stage number.
STEP_NON_STAGE = re.compile(r"(used|remain|left|rest|surplus|price|money|amt|cost|fee|pq|kwh)", re.IGNORECASE)
TOU_KEYS = {
    "tip": re.compile(r"(^|_)(tip|jian|xian)|尖", re.IGNORECASE),
    "peak": re.compile(r"(^|_)(peak|feng)|峰", re.IGNORECASE),
    "flat": re.compile(r"(^|_)(flat|ping)|平", re.IGNORECASE),
    "valley": re.compile(r"(^|_)(valley|gu)|谷", re.IGNORECASE),
}


def business(response):
    if str(response.get("code")) not in OK:
        raise RuntimeError(f"接口返回 {response.get('code', '空响应')}")
    data = response.get("data") or {}
    return data.get("bizrt", data)


def money(value):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def balance_of(value):
    """Retain the source integration's postpaid debt sign rule."""
    total = money(value.get("sumMoney"))
    if total is None:
        return None
    if value.get("consType") == "0" and value.get("errmsg") != "1":
        total = -abs(total)
    account = money(value.get("accountBalance"))
    return account if account not in (None, 0) else total


def latest_bills(rows):
    good = [r for r in rows if isinstance(r, dict) and str(r.get("month", "")).isdigit()]
    good.sort(key=lambda r: r["month"], reverse=True)
    return [{"month": r["month"], "cost": money(r.get("monthEleCost")),
             "kwh": money(r.get("monthEleNum"))} for r in good[:BILL_KEEP]]


def daily_usage(rows):
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            day = dt.datetime.strptime(str(row.get("day", "")).replace("-", ""), "%Y%m%d").date()
        except ValueError:
            continue
        kwh = money(row.get("dayElePq"))
        if kwh is not None:
            result.append({"day": day.isoformat(), "kwh": kwh})
    result.sort(key=lambda row: row["day"], reverse=True)
    return result[:DAILY_KEEP]


# --------------------------------------------------------------------------- #
# heuristic extraction (tiered / time-of-use)
# --------------------------------------------------------------------------- #
def _walk(value, depth: int = 0):
    if depth > 7:
        return
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item, depth + 1)


def _pick(mapping: dict, pattern, numeric_only: bool = True, exclude=None):
    """First key matching ``pattern`` (and not ``exclude``) with a usable value.

    ``exclude`` matters for the stage key: "stepUsedPq" also contains "step", so
    the used/remain/price hints have to be filtered out before picking a stage.
    """
    for key, value in mapping.items():
        text = str(key)
        if not pattern.search(text):
            continue
        if exclude is not None and exclude.search(text):
            continue
        if numeric_only:
            try:
                return key, float(value)
            except (TypeError, ValueError):
                continue
        else:
            return key, value
    return None, None


def _step_candidates(payload) -> list[dict]:
    """Lists of dicts whose keys mention "step"/"阶梯" or bar-shaped records."""
    found: list[dict] = []
    for node in _walk(payload):
        if not isinstance(node, list) or len(node) < 2 or len(node) > 6:
            continue
        rows = [item for item in node if isinstance(item, dict)]
        if len(rows) < 2:
            continue
        hit = sum(1 for item in rows if any(STEP_HINT.search(str(key)) for key in item))
        if hit >= 1 and all(len(item) >= 2 for item in rows):
            found.append({"rows": rows, "hint_hits": hit})
    found.sort(key=lambda item: item["hint_hits"], reverse=True)
    return found


def extract_steps(payload) -> list[dict]:
    """Best-effort tiered-tariff extraction.

    Accepts a candidate only when every row carries a stage-like number and at
    least one of used/remain/price, otherwise returns ``[]`` (the caller then
    keeps the flat-rate model).
    """
    for candidate in _step_candidates(payload):
        rows = candidate["rows"]
        normalized: list[dict] = []
        keys_seen: list[str] = []
        for index, row in enumerate(rows):
            stage_key, stage = _pick(row, STEP_STAGE, exclude=STEP_NON_STAGE)
            used_key, used = _pick(row, STEP_USED)
            remain_key, remain = _pick(row, STEP_REMAIN)
            price_key, price = _pick(row, STEP_PRICE)
            for key in (stage_key, used_key, remain_key, price_key):
                if key:
                    keys_seen.append(key)
            if stage is None and remain is None and used is None:
                continue
            normalized.append({
                "stage": int(stage) if stage else index + 1,
                "used": round(used, 2) if used is not None else None,
                "remain": round(remain, 2) if remain is not None else None,
                "price": round(price, 4) if price is not None else None,
                "current": False,
                "source_keys": [key for key in (stage_key, used_key, remain_key, price_key) if key],
            })
        if len(normalized) < 2:
            continue
        if not any(item["remain"] is not None for item in normalized):
            continue
        # The first tier that still has a remaining quota is the active one.
        for item in normalized:
            if item["remain"] and item["remain"] > 0:
                item["current"] = True
                break
        else:
            normalized[-1]["current"] = True
        return normalized
    return []


def extract_tou(payload) -> dict | None:
    """Best-effort time-of-use extraction (尖/峰/平/谷)."""
    best: dict | None = None
    best_hits = 0
    for node in _walk(payload):
        if not isinstance(node, dict):
            continue
        buckets: dict[str, float] = {}
        keys: dict[str, str] = {}
        for name, pattern in TOU_KEYS.items():
            key, value = _pick(node, pattern)
            if value is not None:
                buckets[name] = round(value, 2)
                keys[name] = key
        if len(buckets) < 2:
            continue
        total = sum(buckets.values())
        if total <= 0:
            continue
        if len(buckets) > best_hits:
            best_hits = len(buckets)
            best = {**buckets, "total": round(total, 2), "source_keys": keys}
    return best


# --------------------------------------------------------------------------- #
# payload builders
# --------------------------------------------------------------------------- #
def balance_payload(channel, meter):
    province = meter.get("proNo", meter.get("provinceId"))
    return {"data": {"srvCode": "", "serialNo": "", "channelCode": "0902",
                     "funcCode": "WEBA1007200", "acctId": channel.user_info["userId"],
                     "userName": channel.user_info.get("loginAccount", channel.user_info.get("nickname")),
                     "promotType": "1", "promotCode": "1", "userAccountId": channel.user_info["userId"],
                     "list": [{"consNoSrc": meter["consNo_dst"], "proCode": province,
                               "sceneType": meter.get("consSortCode", meter.get("elecTypeCode")),
                               "consNo": meter["consNo"], "orgNo": meter["orgNo"]}]},
            "serviceCode": "0101143", "source": "SGAPP", "target": province}


def bill_payload(channel, meter, year):
    return {"data": {"acctId": channel.user_info["userId"], "channelCode": "0902",
                     "clearCache": "11", "consType": meter.get("constType"),
                     "funcCode": "WEBALIPAY_01", "orgNo": meter["orgNo"],
                     "proCode": meter["proNo"], "promotCode": "1", "promotType": "1",
                     "serialNo": "", "srvCode": "", "userName": "",
                     "provinceCode": meter["proNo"], "userAccountId": channel.user_info["userId"],
                     "consNo": meter["consNo_dst"], "queryYear": str(year)},
            "serviceCode": "BCP_000026", "source": "app", "target": meter["proNo"]}


def daily_payload(channel, meter, start, end):
    user_id = channel.user_info["userId"]
    account = {"serviceCode": ACCOUNT_CODES, "source": "SGAPP", "target": "32101",
               "uscInfo": {"member": "0902", "devciceIp": "", "devciceId": "", "tenant": "state_grid"},
               "quInfo": {"userId": user_id}, "token": channel.token}
    data = {"acctId": user_id, "consNo": meter["consNo_dst"], "consType": "01",
            "endTime": end.isoformat(), "orgNo": meter["orgNo"], "queryYear": end.year,
            "proCode": meter["proNo"], "serialNo": "", "srvCode": "",
            "startTime": start.isoformat(), "userName": channel.user_info.get("loginAccount", ""),
            "funcCode": "WEBALIPAY_01", "channelCode": "0902", "clearCache": "11",
            "promotCode": "1", "promotType": "1"}
    return {"params1": account,
            "params3": {"data": data, "serviceCode": "BCP_000026", "source": "app",
                        "target": meter["proNo"]}, "params4": "010103"}


def probe_payload(channel, meter, year):
    """Neutral payload for the unknown business-center endpoints.

    Mirrors the bill payload, which is the same interface family (bc0001).
    """
    return bill_payload(channel, meter, year)


def summarize(meter, balance, bills, steps=None, tou=None):
    row = {"id": str(meter["consNo_dst"]),
           "name": str(meter.get("consName_dst") or meter.get("consName") or ""),
           "address": str(meter.get("elecAddr_dst") or meter.get("elecAddr") or ""),
           "balance": balance_of(balance), "due": money(balance.get("estiAmt")),
           "arrears": money(balance.get("historyOwe")), "bills": latest_bills(bills)}
    if steps:
        row["steps"] = steps
    if tou:
        row["tou"] = tou
    return row


def month_cost_of(bills: list, now: datetime) -> tuple[str, float | None]:
    """Cost billed so far for the current month (published bills only)."""
    label = now.astimezone(TZ).strftime("%Y%m")
    for item in bills or []:
        if str(item.get("month")) == label:
            return label, item.get("cost")
    return label, None


def relogin_count() -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOGIN_WINDOW_HOURS)
    return sum(1 for stamp in _login_events if stamp >= cutoff)


def _note_login() -> None:
    now = datetime.now(timezone.utc)
    _login_events.append(now)
    cutoff = now - timedelta(hours=LOGIN_WINDOW_HOURS * 2)
    while _login_events and _login_events[0] < cutoff:
        _login_events.pop(0)


# --------------------------------------------------------------------------- #
# collection
# --------------------------------------------------------------------------- #
async def collect(store: ElectricityStore) -> dict:
    """One collection round. Returns the snapshot that was persisted.

    A failure inside a single data slice (daily usage / tiered / time-of-use) is
    tolerated and reported through `errors`; a failure to log in or to obtain the
    balance aborts the round and keeps the previous snapshot untouched.
    """
    creds = store.credentials()
    if not store.configured():
        raise RuntimeError("国网账号或采集户号尚未配置")

    meter = creds["target_meter"]
    ident = str(meter.get("consNo_dst") or "")
    previous_rows = (store.snapshot() or {}).get("households") or []
    previous = {row.get("id"): row for row in previous_rows if isinstance(row, dict)}
    errors: list[str] = []

    async with ClientSession() as session:
        channel = WebChannel(None, session, creds["account"], creds["password_digest"],
                             SessionStore(store.session_path))
        if not await channel.async_login():
            raise RuntimeError(f"国网登录失败：{channel.last_error or '未知错误'}")
        _note_login()

        households: list[dict] = []
        try:
            request = balance_payload(channel, meter)
            bal = await channel.async_call(BALANCE_API, request)
            store.save_raw("balance", request, bal)
            bal_rows = business(bal).get("list") or []
            if not bal_rows:
                raise RuntimeError("余额为空")

            year = dt.datetime.now(TZ).year
            bill_request = bill_payload(channel, meter, year)
            bill_response = await channel.async_call(BILL_API, bill_request)
            store.save_raw("bill", bill_request, bill_response)
            bill = business(bill_response)
            month_rows = bill.get("mothEleList") or []
            row = summarize(meter, bal_rows[0], month_rows)

            # tiered tariff / time-of-use live in the bill or daily payloads;
            # try both, tolerate misses.
            steps = extract_steps(bill) or extract_steps(bal)
            if steps:
                row["steps"] = steps
            tou = extract_tou(bill) or extract_tou(bal)
            if tou:
                row["tou"] = tou

            try:
                end = dt.datetime.now(TZ).date() - dt.timedelta(days=1)
                daily_request = daily_payload(channel, meter,
                                              end - dt.timedelta(days=DAILY_WINDOW_DAYS - 1), end)
                daily_response = await channel.async_call(DAILY_API, daily_request)
                store.save_raw("daily", daily_request, daily_response)
                daily_root = business(daily_response)
                row["daily"] = daily_usage(daily_root.get("sevenEleList") or [])
                if not row["daily"]:
                    raise RuntimeError("逐日用电为空")
                if not row.get("steps"):
                    steps = extract_steps(daily_root)
                    if steps:
                        row["steps"] = steps
                if not row.get("tou"):
                    tou = extract_tou(daily_root)
                    if tou:
                        row["tou"] = tou
            except Exception as exc:  # noqa: BLE001 - keep the previously known series
                errors.append("逐日用电：" + str(exc))
                row["daily"] = previous.get(ident, {}).get("daily", [])

            label, cost = month_cost_of(row.get("bills") or [], datetime.now(timezone.utc))
            households.append(row)
        except Exception as exc:  # noqa: BLE001
            errors.append(ident[-4:] + "：" + str(exc))
            label, cost = None, None
            if ident in previous:
                households.append(previous[ident])

        if not households:
            raise RuntimeError("本轮没有取得任何户号数据")

    snapshot = {
        "updated_at": dt.datetime.now(TZ).isoformat(timespec="seconds"),
        "households": households,
        "errors": errors,
        "relogin_count": relogin_count(),
    }
    if label:
        snapshot["month_label"] = label
        snapshot["month_cost"] = cost
    else:
        previous_snapshot = store.snapshot()
        snapshot["month_label"] = previous_snapshot.get("month_label")
        snapshot["month_cost"] = previous_snapshot.get("month_cost")

    # long-term history (append-only; the reader dedupes)
    primary = households[0]
    if primary.get("daily"):
        store.append_daily_history([{"day": item["day"], "kwh": item["kwh"],
                                     "collected_at": snapshot["updated_at"]}
                                    for item in primary["daily"][:DAILY_KEEP]])
    if primary.get("bills"):
        store.append_monthly_history([{"month": item["month"], "kwh": item.get("kwh"),
                                       "cost": item.get("cost"),
                                       "collected_at": snapshot["updated_at"]}
                                      for item in primary["bills"]])

    store.save_snapshot(snapshot)
    store.trim_history()
    return snapshot


def describe_error(exc: Exception) -> str:
    """Never leak credentials or raw upstream payloads into the module API."""
    if isinstance(exc, RuntimeError):
        return str(exc)
    logging.warning("electricity collection failed: %s", type(exc).__name__)
    return "采集失败，请稍后重试"


# --------------------------------------------------------------------------- #
# introspection probe
# --------------------------------------------------------------------------- #
async def probe(store: ElectricityStore, apis: list[str] | None = None) -> dict:
    """Call the uncalled AUTH_APIS endpoints once and record what comes back.

    Read-only with respect to upstream state: only GET-like business queries.
    Answers "does tiered / time-of-use data exist?" so the parsers can be
    tightened with evidence instead of guesses.
    """
    creds = store.credentials()
    if not store.configured():
        raise RuntimeError("国网账号或采集户号尚未配置")
    wanted = {name: path for name, path in EXTRA_APIS.items() if not apis or name in apis}
    if not wanted:
        raise RuntimeError("没有匹配的探针接口")

    meter = creds["target_meter"]
    year = dt.datetime.now(TZ).year
    results: list[dict] = []

    async with ClientSession() as session:
        channel = WebChannel(None, session, creds["account"], creds["password_digest"],
                             SessionStore(store.session_path))
        if not await channel.async_login():
            raise RuntimeError(f"国网登录失败：{channel.last_error or '未知错误'}")
        _note_login()

        for name, path in wanted.items():
            request = probe_payload(channel, meter, year)
            entry = {"name": name, "api": path}
            try:
                response = await channel.async_call(path, request)
                file_name = store.save_raw(name, request, response)
                entry["code"] = response.get("code")
                entry["ok"] = str(response.get("code")) in OK
                try:
                    root = business(response)
                except RuntimeError as exc:
                    root = None
                    entry["error"] = str(exc)
                if root is not None:
                    entry["top_keys"] = list(root.keys())[:30] if isinstance(root, dict) else []
                    entry["records"] = _count_records(root)
                    steps = extract_steps(root)
                    tou = extract_tou(root)
                    entry["steps_found"] = bool(steps)
                    entry["steps"] = steps[:4]
                    entry["tou_found"] = bool(tou)
                    entry["tou"] = tou
                entry["raw_file"] = file_name
            except Exception as exc:  # noqa: BLE001 - one probe must not stop the rest
                entry["ok"] = False
                entry["error"] = f"{type(exc).__name__}: {exc}"[:300]
            results.append(entry)

    return {"probed_at": datetime.now(TZ).isoformat(timespec="seconds"), "results": results}


def _count_records(root) -> dict:
    counts = {}
    if isinstance(root, dict):
        for key, value in root.items():
            if isinstance(value, list):
                counts[key] = len(value)
    return counts
