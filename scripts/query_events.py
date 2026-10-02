#!/usr/bin/env python3
"""Normalize agent-verified calendars; Python 3.11+, no network or dependencies."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
CONTRACT_MONTHS = {code: month for month, code in enumerate("FGHJKMNQUVXZ", 1)}
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "assets" / "config.json"
DAILY_DEFAULTS = {"preview_days": 7, "preview_limit": 12, "critical_categories": ["macro", "expiry"]}
COLLECTION_DEFAULTS = {"request_timeout_seconds": 20, "attempts_per_source": 2, "total_budget_seconds": 480}
SESSIONS = {"before_open": "盘前", "after_close": "盘后"}
COVERAGE_LABELS = {
    "checked": "已核验", "not_announced": "日期尚未宣布", "partial": "部分核验",
    "unavailable": "来源未读取", "stale": "核验已过期",
}


class InputError(ValueError):
    pass


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def timestamp(value, field: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise InputError(f"{field} 必须为 ISO8601 时间") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise InputError(f"{field} 必须包含 UTC 偏移，例如 -04:00 或 Z")
    return result.astimezone(UTC)


def calendar_date(value, field: str) -> date:
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError()
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise InputError(f"{field} 必须为 YYYY-MM-DD 日期") from exc


def zone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except (TypeError, ValueError, ZoneInfoNotFoundError) as exc:
        raise InputError(f"未知 IANA 时区：{value}") from exc


def contract_code(value: str, required_root: str | None = None) -> str:
    match = re.fullmatch(r"(ES|NQ|GC)([FGHJKMNQUVXZ])(\d{2}|\d{4})", str(value).upper())
    if not match or (required_root and match[1] != required_root):
        raise InputError(f"合约代码无效或品种不符：{value}；使用 GCZ26、ESZ26 等形式")
    year = int(match[3]) + (2000 if len(match[3]) == 2 else 0)
    if not 2000 <= year <= 2099:
        raise InputError("合约年份必须位于 2000 至 2099 年")
    return f"{match[1]}{match[2]}{year % 100:02d}"


def json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError(f"JSON 字段重复：{key}")
        result[key] = value
    return result


def read_json(path: str):
    try:
        raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        def reject_constant(value):
            raise InputError(f"JSON 不允许 {value}")
        value = json.loads(raw, object_pairs_hook=json_object, parse_constant=reject_constant)
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"无法读取 JSON：{exc}") from exc
    if not isinstance(value, dict):
        raise InputError("JSON 根节点必须是对象")
    return value


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    cfg = read_json(str(path))
    try:
        zone(cfg["calendar_timezone"])
        zone(cfg["display_timezone"])
        for field in ("imminent_hours", "verification_max_age_minutes"):
            value = cfg[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise InputError(f"配置 {field} 必须为正数")
        if not cfg["default_instruments"] or not cfg["companies"] or not cfg["event_types"]:
            raise InputError("配置中的品种、公司和事件类型不能为空")
        for name, level in cfg["levels"].items():
            if not isinstance(level["rank"], (int, float)) or isinstance(level["rank"], bool) or not math.isfinite(level["rank"]):
                raise InputError(f"级别 {name} 的 rank 必须为有限数值")
            for field in ("label", "marker"):
                if not isinstance(level[field], str):
                    raise InputError(f"级别 {name} 的 {field} 必须为文本")
        if not set(cfg["imminent_levels"]) <= cfg["levels"].keys():
            raise InputError("imminent_levels 包含未知级别")
        for source in all_sources(cfg).values():
            if not source["urls"] or not source["domains"]:
                raise InputError("来源必须有 urls 和 domains")
            for url in source["urls"]:
                official_url(url, source)
        for rule in cfg["event_types"].values():
            if rule["category"] not in {"macro", "earnings", "expiry"}:
                raise InputError("事件类别必须为 macro、earnings 或 expiry")
            if not rule["instruments"] or not set(rule["instruments"].values()) <= cfg["levels"].keys():
                raise InputError("事件必须包含有效的品种分级")
            if rule["category"] != "earnings" and rule["source_id"] not in cfg["sources"]:
                raise InputError("事件指向未知来源")
        for target in cfg.get("type_aliases", {}).values():
            if target not in cfg["event_types"]:
                raise InputError("事件别名指向未知类型")
        daily_settings(cfg)
    except (KeyError, TypeError, AttributeError) as exc:
        raise InputError(f"配置结构无效：{exc}") from exc
    return cfg


def daily_settings(cfg: dict) -> dict:
    raw = cfg.get("daily", {})
    if not isinstance(raw, dict) or not isinstance(raw.get("collection", {}), dict):
        raise InputError("daily 和 daily.collection 必须为对象")
    settings = {**DAILY_DEFAULTS, **raw}
    settings["collection"] = {**COLLECTION_DEFAULTS, **raw.get("collection", {})}
    for field in ("preview_days", "preview_limit"):
        value = settings[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < (0 if field == "preview_days" else 1):
            raise InputError(f"daily.{field} 必须为{'非负' if field == 'preview_days' else '正'}整数")
    categories = settings["critical_categories"]
    if not isinstance(categories, list) or not all(isinstance(c, str) and c in {"macro", "earnings", "expiry"} for c in categories):
        raise InputError("daily.critical_categories 必须是有效类别列表")
    for field, value in settings["collection"].items():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise InputError(f"daily.collection.{field} 必须为正整数")
    return settings


def all_sources(cfg: dict) -> dict:
    sources = dict(cfg["sources"])
    for symbol, company in cfg["companies"].items():
        sources[f"earnings:{symbol}"] = {**company, "name": f"{symbol} {company['name']} 财报"}
    return sources


def official_url(value, source: dict) -> str:
    if not isinstance(value, str) or any(c.isspace() or c in "<>" for c in value):
        raise InputError("来源 url 必须为有效 HTTPS 链接")
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or not any(host == d or host.endswith("." + d) for d in source["domains"])):
        raise InputError(f"来源链接不属于该来源配置的官方域名：{value}")
    return value


@dataclass(frozen=True)
class Query:
    now: datetime
    start: date
    end: date
    instruments: tuple[str, ...]
    categories: tuple[str, ...]
    companies: tuple[str, ...]
    contracts: tuple[str, ...]

    def filters(self) -> dict:
        return {key: list(getattr(self, key)) for key in ("instruments", "categories", "companies", "contracts")}

    def bounds(self, cfg: dict) -> tuple[datetime, datetime]:
        tz = zone(cfg["calendar_timezone"])
        return (datetime.combine(self.start, time.min, tz).astimezone(UTC),
                datetime.combine(self.end + timedelta(days=1), time.min, tz).astimezone(UTC))


def make_query(args, cfg: dict, payload: dict | None = None) -> Query:
    payload = payload or {}
    saved = payload.get("query", {})
    if not isinstance(saved, dict):
        raise InputError("query 必须为对象")
    now = timestamp(args.now, "--now") if args.now else datetime.now(UTC)
    today = now.astimezone(zone(cfg["calendar_timezone"])).date()
    supplied_range = payload.get("range")
    if args.start:
        if args.period or args.days is not None:
            raise InputError("--start 与 --period / --days 互斥")
        start = calendar_date(args.start, "--start")
        end = calendar_date(args.end, "--end") if args.end else start
    elif args.end:
        raise InputError("--end 需要同时指定 --start")
    elif args.period is None and args.days is None and supplied_range:
        start, end = input_range(payload, cfg)
    else:
        period = args.period or "today"
        if args.days is not None and period != "next":
            raise InputError("--days 仅用于 --period next")
        if period == "week":
            start = today - timedelta(days=today.weekday())
            end = start + timedelta(days=6)
        elif period == "next":
            days = args.days if args.days is not None else 7
            if days <= 0:
                raise InputError("--days 必须大于 0")
            start, end = today, today + timedelta(days=days - 1)
        else:
            start = end = today
    if end < start:
        raise InputError("结束日期不能早于开始日期")
    def selection(field, default, allowed):
        values = getattr(args, field) if getattr(args, field) is not None else saved.get(field, default)
        if not isinstance(values, (list, tuple)) or not values or not all(isinstance(x, str) for x in values):
            raise InputError(f"{field} 必须是非空字符串列表")
        values = tuple(dict.fromkeys(x.lower() if field == "categories" else x.upper() for x in values))
        if allowed is not None and not set(values) <= set(allowed):
            raise InputError(f"{field} 包含未配置的值：{', '.join(sorted(set(values) - set(allowed)))}")
        return values
    known_instruments = set().union(*(r["instruments"] for r in cfg["event_types"].values()))
    instruments = selection("instruments", cfg["default_instruments"], known_instruments)
    categories = selection("categories", ["macro", "earnings", "expiry"], {"macro", "earnings", "expiry"})
    companies = selection("companies", list(cfg["companies"]), cfg["companies"])
    raw_contracts = args.contracts if args.contracts is not None else saved.get("contracts", [])
    if not isinstance(raw_contracts, (list, tuple)):
        raise InputError("contracts 必须为列表")
    contracts = tuple(dict.fromkeys(contract_code(c) for c in raw_contracts))
    if any(c[:-3] not in instruments for c in contracts):
        raise InputError("指定合约必须属于查询品种；例如 GCZ26 配合 --instruments GC")
    return Query(now, start, end, instruments, categories, companies, contracts)


def input_range(payload: dict, cfg: dict) -> tuple[date, date]:
    value = payload.get("range")
    if not isinstance(value, dict) or value.get("timezone") != cfg["calendar_timezone"]:
        raise InputError("range 必须包含与 calendar_timezone 一致的 timezone")
    start = calendar_date(value.get("start_date"), "range.start_date")
    end = calendar_date(value.get("end_date"), "range.end_date")
    if end < start:
        raise InputError("输入覆盖范围的结束日期早于开始日期")
    return start, end


def required_sources(query: Query, cfg: dict) -> dict:
    ids = set()
    for rule in cfg["event_types"].values():
        if rule["category"] not in query.categories or not set(rule["instruments"]) & set(query.instruments):
            continue
        if rule["category"] == "earnings":
            ids.update(f"earnings:{symbol}" for symbol in query.companies)
        else:
            ids.add(rule["source_id"])
    return {key: value for key, value in all_sources(cfg).items() if key in ids}


def prepare(query: Query, cfg: dict) -> dict:
    sources = required_sources(query, cfg)
    return {
        "query_started_at": iso(query.now),
        "range": {"start_date": query.start.isoformat(), "end_date": query.end.isoformat(), "timezone": cfg["calendar_timezone"]},
        "query": query.filters(), "events": [],
        "coverage": [{"source_id": key, "status": "unavailable", "url": source["urls"][0],
                      "checked_at": None, "note": "本次尚未联网核验"} for key, source in sources.items()],
        "required_sources": sources,
    }


def freshness(checked: datetime, started: datetime, query: Query, cfg: dict) -> str:
    if checked > query.now:
        raise InputError("checked_at 不能晚于查询时刻")
    if checked < started or query.now - checked > timedelta(minutes=cfg["verification_max_age_minutes"]):
        return "stale"
    return "verified"


def normalize_coverage(payload: dict, query: Query, cfg: dict, started: datetime) -> list[dict]:
    raw = payload.get("coverage", [])
    if not isinstance(raw, list):
        raise InputError("coverage 必须为列表")
    sources = all_sources(cfg)
    supplied = {}
    start, end = input_range(payload, cfg)
    scope_contracts = payload.get("query", {}).get("contracts", [])
    if not isinstance(scope_contracts, (list, tuple)):
        raise InputError("输入 query.contracts 必须为列表")
    scope_contracts = {contract_code(c) for c in scope_contracts}
    for item in raw:
        if not isinstance(item, dict) or item.get("source_id") not in sources:
            raise InputError("coverage 中存在未知来源或非对象记录")
        source_id = item["source_id"]
        if source_id in supplied:
            raise InputError(f"coverage 来源重复：{source_id}")
        status = item.get("status")
        if status not in {"checked", "not_announced", "partial", "unavailable"}:
            raise InputError(f"coverage 状态无效：{status}")
        if status == "not_announced" and not source_id.startswith("earnings:"):
            raise InputError("not_announced 只用于财报日期尚未宣布")
        url = official_url(item.get("url"), sources[source_id])
        note = str(item.get("note", ""))
        checked = timestamp(item["checked_at"], "coverage.checked_at") if item.get("checked_at") else None
        if status != "unavailable" and checked is None:
            raise InputError("已读取的来源必须提供 checked_at")
        if checked and freshness(checked, started, query, cfg) == "stale":
            status, note = "stale", note + "；未满足本次核验时效"
        if status == "checked" and not (start <= query.start and end >= query.end):
            status, note = "partial", note + "；输入资料未覆盖完整查询日期范围"
        if status == "checked" and scope_contracts:
            roots = {rule["requires_contract"] for rule in cfg["event_types"].values()
                     if rule.get("source_id") == source_id and rule.get("requires_contract")}
            requested = {c for c in query.contracts if c[:-3] in roots}
            collected = {c for c in scope_contracts if c[:-3] in roots}
            if roots and (not query.contracts or not requested <= collected):
                status, note = "partial", note + "；输入资料仅核验了部分合约，未覆盖本次合约范围"
        supplied[source_id] = {"source_id": source_id, "name": sources[source_id]["name"],
                               "status": status, "url": url, "checked_at": iso(checked) if checked else None, "note": note.strip("；")}
    result = []
    for source_id, source in required_sources(query, cfg).items():
        result.append(supplied.get(source_id, {"source_id": source_id, "name": source["name"], "status": "unavailable",
                      "url": source["urls"][0], "checked_at": None, "note": "本次没有该来源的核验记录"}))
    return result


def normalize_event(raw: dict, cfg: dict, query: Query, started: datetime) -> dict:
    if not isinstance(raw, dict):
        raise InputError("events 的每项必须为对象")
    if not isinstance(raw.get("type"), str):
        raise InputError("event.type 必须为字符串")
    event_type = cfg.get("type_aliases", {}).get(raw.get("type"), raw.get("type"))
    if event_type not in cfg["event_types"]:
        raise InputError(f"未知事件类型：{event_type}")
    rule = cfg["event_types"][event_type]
    symbol = None
    if rule["category"] == "earnings":
        symbol = str(raw.get("symbol", "")).upper()
        if symbol not in cfg["companies"]:
            raise InputError(f"财报公司未配置：{symbol}")
    contract = contract_code(raw.get("contract", ""), rule["requires_contract"]) if rule.get("requires_contract") else None
    contract_month = f"20{contract[-2:]}-{CONTRACT_MONTHS[contract[-3]]:02d}" if contract else None
    source_id = f"earnings:{symbol}" if symbol else rule["source_id"]
    if raw.get("source_id") != source_id:
        raise InputError(f"{event_type} 必须使用来源 {source_id}")
    url = official_url(raw.get("url"), all_sources(cfg)[source_id])
    checked = timestamp(raw.get("checked_at"), "event.checked_at")
    verification = freshness(checked, started, query, cfg)
    confirmation = raw.get("status", "confirmed")
    if confirmation not in {"confirmed", "unconfirmed"}:
        raise InputError("event.status 必须为 confirmed 或 unconfirmed")
    precision = raw.get("precision")
    source_zone = zone(raw.get("timezone", cfg["calendar_timezone"]))
    at = None
    session = None
    if precision == "exact":
        at = timestamp(raw.get("at"), "event.at")
        event_date = at.astimezone(zone(cfg["calendar_timezone"])).date()
        start = end = at
    elif precision in {"session", "date"}:
        if raw.get("at") is not None:
            raise InputError("非精确时间事件不能包含 at")
        if "timezone" not in raw:
            raise InputError("日期或时段事件必须提供来源 timezone")
        event_date = calendar_date(raw.get("date"), "event.date")
        if precision == "session":
            session = raw.get("session")
            if session not in SESSIONS:
                raise InputError("session 必须为 before_open 或 after_close")
        start = datetime.combine(event_date, time.min, source_zone).astimezone(UTC)
        end = datetime.combine(event_date + timedelta(days=1), time.min, source_zone).astimezone(UTC)
    else:
        raise InputError("precision 必须为 exact、session 或 date")
    title = rule["title"]
    if symbol:
        title = f"{symbol} {cfg['companies'][symbol]['name']} {title}"
    if contract:
        title += f"（{contract}，{contract_month} 合约）"
    return {"type": event_type, "title": title, "category": rule["category"], "symbol": symbol, "contract": contract,
            "contract_month": contract_month,
            "option_contract": raw.get("option_contract"), "precision": precision, "confirmation_status": confirmation,
            "verification_status": verification, "source_timezone": source_zone.key, "session": session,
            "event_date": event_date.isoformat(), "sources": [{"source_id": source_id, "url": url, "checked_at": iso(checked)}],
            "notes": [str(raw["note"])] if raw.get("note") else [], "_at": at, "_start": start, "_end": end, "_checked": checked}


def merge_events(events: list[dict], cfg: dict) -> list[dict]:
    groups = defaultdict(list)
    for event in events:
        groups[(event["type"], event["symbol"], event["contract"], event["event_date"])].append(event)
    result = []
    precision_rank = {"exact": 3, "session": 2, "date": 1}
    for key, members in groups.items():
        def rank(event):
            return (event["verification_status"] == "verified", event["confirmation_status"] == "confirmed",
                    precision_rank[event["precision"]], event["_checked"])
        chosen = dict(max(members, key=rank))
        sources = {(s["source_id"], s["url"]): s for e in sorted(members, key=lambda e: e["_checked"]) for s in e["sources"]}
        chosen["sources"] = list(sources.values())
        chosen["notes"] = list(dict.fromkeys(n for e in members for n in e["notes"]))
        current = [e for e in members if e["confirmation_status"] == "confirmed" and e["verification_status"] == "verified"]
        times = {e["_at"] for e in current if e["_at"] is not None}
        sessions = {e["session"] for e in current if e["session"]}
        conflict = len(times) > 1 or len(sessions) > 1
        if times and sessions:
            local_time = next(iter(times)).astimezone(zone(cfg["calendar_timezone"])).time()
            conflict |= ("after_close" in sessions and local_time < time(16)) or ("before_open" in sessions and local_time >= time(9, 30))
        if conflict:
            chosen.update(precision="date", confirmation_status="unconfirmed", session=None, _at=None,
                          source_timezone=cfg["calendar_timezone"])
            local_date = date.fromisoformat(chosen["event_date"])
            tz = zone(cfg["calendar_timezone"])
            chosen["_start"] = datetime.combine(local_date, time.min, tz).astimezone(UTC)
            chosen["_end"] = datetime.combine(local_date + timedelta(days=1), time.min, tz).astimezone(UTC)
            chosen["notes"].append("官方资料中的时间或时段信息相冲突，需要重新核对")
        chosen["id"] = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:16]
        result.append(chosen)
    return result


def date_candidates(start: datetime, end: datetime, tz: ZoneInfo) -> list[str]:
    first = start.astimezone(tz).date()
    last = (end - timedelta(microseconds=1)).astimezone(tz).date()
    return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]


def present_event(event: dict, query: Query, cfg: dict) -> tuple[dict, bool]:
    event = dict(event)
    rule = cfg["event_types"][event["type"]]
    event["levels"] = {i: rule["instruments"][i] for i in query.instruments if i in rule["instruments"]}
    event["priority"] = max(event["levels"].values(), key=lambda level: cfg["levels"][level]["rank"])
    at = event["_at"]
    calendar_tz, display_tz = zone(cfg["calendar_timezone"]), zone(cfg["display_timezone"])
    imminent = False
    if at is not None:
        delta = (at - query.now).total_seconds()
        event["at_utc"] = iso(at)
        event["minutes_to_event"] = round(delta / 60, 2)
        event["time_et"] = at.astimezone(calendar_tz).strftime("%Y-%m-%d %H:%M:%S %Z")
        event["time_beijing"] = at.astimezone(display_tz).strftime("%Y-%m-%d %H:%M:%S %Z")
        event["date_et_candidates"] = [at.astimezone(calendar_tz).date().isoformat()]
        event["date_beijing_candidates"] = [at.astimezone(display_tz).date().isoformat()]
        event["schedule_status"] = "time_passed" if delta < 0 else "scheduled"
        imminent = (0 <= delta <= cfg["imminent_hours"] * 3600 and event["confirmation_status"] == "confirmed"
                    and event["verification_status"] == "verified" and event["priority"] in cfg["imminent_levels"])
    else:
        et_dates = date_candidates(event["_start"], event["_end"], calendar_tz)
        bj_dates = date_candidates(event["_start"], event["_end"], display_tz)
        hint = SESSIONS[event["session"]] if event["session"] else "时刻未确认"
        event.update(at_utc=None, minutes_to_event=None, date_et_candidates=et_dates, date_beijing_candidates=bj_dates)
        if event["source_timezone"] == cfg["calendar_timezone"]:
            event["time_et"] = f"{event['event_date']} {hint}（分钟未确认）"
        else:
            event["time_et"] = f"{'～'.join(et_dates)}（官方日期 {event['event_date']} {event['source_timezone']}；{hint}）"
        event["time_beijing"] = f"{'～'.join(bj_dates)}（具体日期／时刻未确认）"
        event["schedule_status"] = "date_passed" if event["_end"] <= query.now else "time_unknown"
    if event["confirmation_status"] == "unconfirmed":
        event["schedule_status"] = "unconfirmed"
    if event["verification_status"] == "stale":
        event["notes"].append("来源未满足本次核验时效，日期需重新核验")
    return {k: v for k, v in event.items() if not k.startswith("_")}, imminent


def build_report(payload: dict, query: Query, cfg: dict) -> dict:
    started = timestamp(payload.get("query_started_at"), "query_started_at")
    if started > query.now:
        raise InputError("query_started_at 不能晚于查询时刻")
    coverage = normalize_coverage(payload, query, cfg, started)
    raw_events = payload.get("events", [])
    if not isinstance(raw_events, list):
        raise InputError("events 必须为列表")
    events = merge_events([normalize_event(e, cfg, query, started) for e in raw_events], cfg)
    range_start, range_end = query.bounds(cfg)
    kept, imminent_ids, warnings = [], [], []
    for event in events:
        if event["category"] not in query.categories or not set(cfg["event_types"][event["type"]]["instruments"]) & set(query.instruments):
            continue
        if event["symbol"] and event["symbol"] not in query.companies:
            continue
        if event["contract"] and query.contracts and event["contract"] not in query.contracts:
            continue
        at = event["_at"]
        overlaps = range_start <= at < range_end if at else event["_start"] < range_end and event["_end"] > range_start
        if not overlaps:
            continue
        displayed, imminent = present_event(event, query, cfg)
        kept.append(displayed)
        if imminent:
            imminent_ids.append(displayed["id"])
        if displayed["confirmation_status"] == "unconfirmed" or displayed["verification_status"] == "stale":
            warnings.append(f"{displayed['title']}：时间或来源尚需核验")
            for source in coverage:
                if source["source_id"] in {s["source_id"] for s in displayed["sources"]} and source["status"] == "checked":
                    source["status"] = "partial"
                    source["note"] += "；包含待核验事件"
    kept.sort(key=lambda e: (e["date_et_candidates"][0], e["at_utc"] is None, e["at_utc"] or "", e["type"], e["symbol"] or "", e["contract"] or ""))
    ordered_ids = [e["id"] for e in kept if e["id"] in imminent_ids]
    for source in coverage:
        if source["status"] != "checked":
            warnings.append(f"{source['name']}：{COVERAGE_LABELS[source['status']]}" + (f"（{source['note']}）" if source["note"] else ""))
    if all(s["status"] == "checked" for s in coverage) and not warnings:
        overall = "complete"
    elif any(s["status"] in {"checked", "partial", "not_announced"} for s in coverage) or kept:
        overall = "partial"
    else:
        overall = "unavailable"
    report = {"schema_version": 1, "generated_at": iso(query.now), "query_started_at": iso(started),
              "range": {"start_date": query.start.isoformat(), "end_date": query.end.isoformat(), "timezone": cfg["calendar_timezone"]},
              "filters": query.filters(), "coverage": {"status": overall, "sources": coverage},
              "imminent_event_ids": ordered_ids, "events": kept, "warnings": list(dict.fromkeys(warnings))}
    report.update(assess_health(coverage, kept, query, cfg))
    report["summary_zh"] = render_summary(report, cfg)
    return report


def assess_health(coverage: list[dict], events: list[dict], query: Query, cfg: dict) -> dict:
    """Retrieval health is distinct from whether a future earnings date is known."""
    critical = set()
    critical_categories = daily_settings(cfg)["critical_categories"]
    for rule in cfg["event_types"].values():
        if (rule["category"] not in critical_categories
                or rule["category"] not in query.categories
                or not set(rule["instruments"]) & set(query.instruments)):
            continue
        critical.update((f"earnings:{s}" for s in query.companies) if rule["category"] == "earnings" else [rule["source_id"]])
    issues = {s["source_id"] for s in coverage if s["status"] not in {"checked", "not_announced"}}
    issues.update(s["source_id"] for e in events
                  if e["verification_status"] != "verified" or e["confirmation_status"] != "confirmed"
                  for s in e["sources"])
    critical_issues = issues & critical
    # Partially read sources can still contribute useful results in a noncritical query.
    has_usable_source = any(s["status"] in {"checked", "not_announced", "partial"} for s in coverage)
    status = "failed" if critical_issues or (coverage and not has_usable_source) else "partial" if issues else "ok"
    return {"run_status": status, "source_health": {
        "issue_source_ids": sorted(issues), "critical_issue_source_ids": sorted(critical_issues),
        "not_announced_source_ids": [s["source_id"] for s in coverage if s["status"] == "not_announced"],
    }}


def markdown_cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_summary(report: dict, cfg: dict) -> str:
    now = timestamp(report["generated_at"], "generated_at")
    value = report["range"]
    label = value["start_date"] if value["start_date"] == value["end_date"] else f"{value['start_date']} 至 {value['end_date']}"
    lines = [f"交易事件摘要｜{label}（美东日期）",
             f"查询时刻：{now.astimezone(zone(cfg['calendar_timezone'])).strftime('%Y-%m-%d %H:%M:%S %Z')} / 北京 {now.astimezone(zone(cfg['display_timezone'])).strftime('%Y-%m-%d %H:%M:%S')}",
             f"关注品种：{'、'.join(report['filters']['instruments'])}；来源覆盖：" + {"complete": "完整", "partial": "部分", "unavailable": "未核验"}[report["coverage"]["status"]], ""]
    imminent = [e for e in report["events"] if e["id"] in report["imminent_event_ids"]]
    if imminent:
        lines.append(f"未来 {cfg['imminent_hours']:g} 小时重点提醒：")
        lines.extend(f"- {cfg['levels'][e['priority']]['marker']} {e['title']}：{e['time_et']} / 北京 {e['time_beijing']}；距离计划时间 {e['minutes_to_event']:g} 分钟" for e in imminent)
        lines.append("")
    elif report["coverage"]["status"] == "complete":
        lines.extend([f"已核验事件中没有未来 {cfg['imminent_hours']:g} 小时内的精确时间重点提醒。", ""])
    else:
        lines.extend(["目前没有可确认的临近重点提醒；来源缺失或未知时间仍需关注。", ""])
    if report["events"]:
        lines.extend(["日历（保留已过计划时间与未知时刻事件）：", "", "| 级别 / 品种 | 事件 | 美东时间 | 北京时间 | 状态 | 来源 |", "|---|---|---|---|---|---|"])
        states = {"scheduled": "计划时间未到", "time_passed": "计划时间已过", "date_passed": "计划日期已过", "time_unknown": "时刻未确认", "unconfirmed": "待重新核验"}
        for event in report["events"]:
            priority = cfg["levels"][event["priority"]]
            associations = "、".join(f"{i} {cfg['levels'][level]['label']}" for i, level in event["levels"].items())
            state = states[event["schedule_status"]] + ("；核验过期" if event["verification_status"] == "stale" else "")
            links = "、".join(f"[{s['source_id']}](<{s['url']}>)" for s in event["sources"])
            cells = [f"{priority['marker']} {associations}", event["title"], event["time_et"], event["time_beijing"], state, links]
            lines.append("| " + " | ".join(markdown_cell(c) for c in cells) + " |")
        for event in report["events"]:
            if event["notes"]:
                lines.extend(["", f"说明（{markdown_cell(event['title'])}）：" + "；".join(markdown_cell(n) for n in event["notes"])])
    elif report["coverage"]["status"] == "complete":
        lines.append("指定范围内未找到符合筛选条件的已知事件。")
    else:
        lines.append("未获得可列出的事件；不能据此判断该范围没有事件。")
    if report["warnings"]:
        lines.extend(["", "待核验范围：", ""])
        lines.extend(f"- {markdown_cell(w)}" for w in report["warnings"])
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="交易事件处理：中文摘要、双时区、JSON、来源覆盖状态。")
    p.add_argument("--prepare", action="store_true", help="生成本次查询的待核验 JSON 骨架")
    p.add_argument("--input", help="已核验的 JSON 文件，- 表示 stdin")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--now", help="带 UTC 偏移的固定查询时刻，实时查询省略")
    p.add_argument("--period", choices=["today", "week", "next"])
    p.add_argument("--days", type=int)
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--instruments", nargs="+")
    p.add_argument("--categories", nargs="+")
    p.add_argument("--companies", nargs="+")
    p.add_argument("--contracts", nargs="+")
    p.add_argument("--format", choices=["both", "json", "markdown"], default="both")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.prepare and args.input:
            raise InputError("--prepare 与 --input 互斥")
        if not args.prepare and args.input is None:
            raise InputError("请先 --prepare 生成查询骨架，联网核验后使用 --input 文件")
        cfg = load_config(args.config)
        payload = read_json(args.input) if args.input else None
        query = make_query(args, cfg, payload)
        if args.prepare:
            print(json.dumps(prepare(query, cfg), ensure_ascii=False, indent=2))
            return 0
        report = build_report(payload, query, cfg)
        if args.format in {"both", "markdown"}:
            print(report["summary_zh"])
        if args.format == "both":
            print("\n```json")
        if args.format in {"both", "json"}:
            print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        if args.format == "both":
            print("```")
        return 0
    except (InputError, OverflowError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps({"error": str(exc), "coverage": {"status": "unknown"}}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
