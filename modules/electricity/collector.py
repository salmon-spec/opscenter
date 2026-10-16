"""State Grid balance / bill / daily-usage collection core.

Reused verbatim from the collector that already runs on the Codex host
(`/home/ubuntu/sgcc-standalone`), including the vendored web channel and the
national-crypto helpers it depends on. Only the scheduling and storage wrapper
differs: this module owns its own data directory and exposes the OpsCenter
module contract.
"""
from __future__ import annotations

import datetime as dt
import logging

from aiohttp import ClientSession

from storage import ElectricityStore, SessionStore
from vendor.web_api import WebChannel

BALANCE_API = "/osg-open-bc0001/member/c05/f01"
BILL_API = "/osg-open-bc0001/member/c01/f02"
DAILY_API = "/osg-web0004/member/c24/f01"

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


def summarize(meter, balance, bills):
    return {"id": str(meter["consNo_dst"]),
            "name": str(meter.get("consName_dst") or meter.get("consName") or ""),
            "address": str(meter.get("elecAddr_dst") or meter.get("elecAddr") or ""),
            "balance": balance_of(balance), "due": money(balance.get("estiAmt")),
            "arrears": money(balance.get("historyOwe")), "bills": latest_bills(bills)}


async def collect(store: ElectricityStore) -> dict:
    """One collection round. Returns the snapshot that was persisted.

    A failure inside a single data slice (daily usage) is tolerated and reported
    through `errors`; a failure to log in or to obtain the balance aborts the
    round and keeps the previous snapshot untouched.
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

        households: list[dict] = []
        try:
            bal = business(await channel.async_call(BALANCE_API, balance_payload(channel, meter)))
            bal_rows = bal.get("list") or []
            if not bal_rows:
                raise RuntimeError("余额为空")
            year = dt.datetime.now(TZ).year
            bill = business(await channel.async_call(BILL_API, bill_payload(channel, meter, year)))
            row = summarize(meter, bal_rows[0], bill.get("mothEleList") or [])
            try:
                end = dt.datetime.now(TZ).date() - dt.timedelta(days=1)
                daily = business(await channel.async_call(
                    DAILY_API, daily_payload(channel, meter, end - dt.timedelta(days=DAILY_WINDOW_DAYS - 1), end)))
                row["daily"] = daily_usage(daily.get("sevenEleList") or [])
                if not row["daily"]:
                    raise RuntimeError("逐日用电为空")
            except Exception as exc:  # noqa: BLE001 - keep the previously known series
                errors.append("逐日用电：" + str(exc))
                row["daily"] = previous.get(ident, {}).get("daily", [])
            households.append(row)
        except Exception as exc:  # noqa: BLE001
            errors.append(ident[-4:] + "：" + str(exc))
            if ident in previous:
                households.append(previous[ident])

        if not households:
            raise RuntimeError("本轮没有取得任何户号数据")

    snapshot = {
        "updated_at": dt.datetime.now(TZ).isoformat(timespec="seconds"),
        "households": households,
        "errors": errors,
    }
    store.save_snapshot(snapshot)
    return snapshot


def describe_error(exc: Exception) -> str:
    """Never leak credentials or raw upstream payloads into the module API."""
    if isinstance(exc, RuntimeError):
        return str(exc)
    logging.warning("electricity collection failed: %s", type(exc).__name__)
    return "采集失败，请稍后重试"
