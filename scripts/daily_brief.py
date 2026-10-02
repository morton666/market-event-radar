#!/usr/bin/env python3
"""Prepare a fresh daily run and archive a concise agent-verified briefing."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

import query_events as radar

EXIT_CODES = {"ok": 0, "partial": 3, "failed": 4}


def atomic_write(path: Path, content: str) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def json_text(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def config_digest(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def storage_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise radar.InputError("--state-dir 必须为绝对路径")
    path = path.resolve()
    if path.is_relative_to(Path(__file__).resolve().parents[1]):
        raise radar.InputError("--state-dir 必须位于技能目录之外，避免升级时覆盖日报")
    return path


def daily_query(args, cfg: dict, payload: dict | None = None) -> radar.Query:
    saved = (payload or {}).get("daily", {})
    if not isinstance(saved, dict):
        raise radar.InputError("daily 必须为对象")
    days = args.preview_days if args.preview_days is not None else saved.get("preview_days", radar.daily_settings(cfg)["preview_days"])
    if isinstance(days, bool) or not isinstance(days, int) or days < 0:
        raise radar.InputError("--preview-days 必须为非负整数")
    # An explicit relative range prevents yesterday's input from deciding today's query.
    options = argparse.Namespace(now=args.now, start=None, end=None, period="next", days=days + 1,
                                 **{field: getattr(args, field) for field in ("instruments", "categories", "companies", "contracts")})
    return radar.make_query(options, cfg, payload)


def prepare_run(args, cfg: dict, state: Path) -> dict:
    query = daily_query(args, cfg)
    payload = radar.prepare(query, cfg)
    payload["daily"] = {"run_id": uuid.uuid4().hex, "date_et": query.start.isoformat(),
                        "preview_days": (query.end - query.start).days}
    directory = state / "runs" / payload["daily"]["date_et"] / payload["daily"]["run_id"]
    directory.mkdir(parents=True, mode=0o700)
    request = {field: payload[field] for field in ("query_started_at", "range", "query", "daily")}
    request["config_sha256"] = config_digest(cfg)
    atomic_write(directory / "request.json", json_text(request))
    atomic_write(directory / "input.json", json_text(payload))
    return {"run_id": payload["daily"]["run_id"], "input_path": str(directory / "input.json"),
            "range": payload["range"], "query_started_at": payload["query_started_at"],
            "collection_deadline_at": radar.iso(query.now + timedelta(seconds=radar.daily_settings(cfg)["collection"]["total_budget_seconds"])),
            "collection_policy": radar.daily_settings(cfg)["collection"],
            "required_source_ids": list(payload["required_sources"])}


def validate_run(payload: dict, query: radar.Query, cfg: dict, args, state: Path) -> Path:
    daily = payload.get("daily")
    if not isinstance(daily, dict) or not re.fullmatch(r"[a-f0-9]{32}", str(daily.get("run_id", ""))):
        raise radar.InputError("日报输入缺少有效 run_id，请先执行 daily_brief.py --prepare")
    prepared_date = radar.calendar_date(daily.get("date_et"), "daily.date_et")
    directory = state / "runs" / prepared_date.isoformat() / daily["run_id"]
    if Path(args.input).expanduser().resolve() != directory / "input.json":
        raise radar.InputError("请使用 --prepare 返回的 input_path，以及同一个 --state-dir")
    request = radar.read_json(str(directory / "request.json"))
    if any(payload.get(field) != request.get(field) for field in ("query_started_at", "range", "query", "daily")):
        raise radar.InputError("日报请求条件已被修改，请重新 --prepare；仅填写 events 和 coverage")
    if request.get("config_sha256") != config_digest(cfg):
        raise radar.InputError("配置与准备查询时不一致，请使用同一 --config 或重新 --prepare")
    started = radar.timestamp(payload.get("query_started_at"), "query_started_at")
    if (prepared_date != query.start or started.astimezone(radar.zone(cfg["calendar_timezone"])).date() != query.start
            or radar.input_range(payload, cfg) != (query.start, query.end) or payload.get("query") != query.filters()):
        raise radar.InputError("日报输入不是本次美东日期和查询范围，请重新 --prepare 并联网核验")
    if started > query.now or query.now - started > timedelta(minutes=cfg["verification_max_age_minutes"]):
        raise radar.InputError("日报输入已过期或时间无效，请重新 --prepare 并联网核验")
    return directory


def event_line(event: dict, cfg: dict, imminent: bool = False) -> str:
    state = {"scheduled": "计划时间未到", "time_passed": "计划时间已过", "date_passed": "计划日期已过",
             "time_unknown": "时刻未确认", "unconfirmed": "待核验"}[event["schedule_status"]]
    if event["verification_status"] == "stale":
        state += "；核验过期"
    if imminent:
        state = f"距离计划时间 {event['minutes_to_event']:g} 分钟"
    source = event["sources"][0]
    line = (f"- {cfg['levels'][event['priority']]['marker']} {event['title']}｜美东 {event['time_et']}；"
            f"北京 {event['time_beijing']}｜{state} [来源](<{source['url']}>)")
    if event["notes"]:
        line += "；" + "；".join(radar.markdown_cell(note) for note in event["notes"])
    return line


def daily_summary(report: dict, cfg: dict) -> str:
    now = radar.timestamp(report["generated_at"], "generated_at")
    today = report["daily"]["date_et"]
    events = report["events"]
    imminent = [event for event in events if event["id"] in report["imminent_event_ids"]]
    todays = [event for event in events if today in event["date_et_candidates"]]
    future = [event for event in events if event not in todays and event["priority"] in cfg["imminent_levels"]]
    # Choose important preview items first, then display that selection chronologically.
    ranked = sorted(future, key=lambda e: (-cfg["levels"][e["priority"]]["rank"], e["date_et_candidates"][0], e["at_utc"] or ""))
    selected = {event["id"] for event in ranked[:radar.daily_settings(cfg)["preview_limit"]]}
    preview = [event for event in future if event["id"] in selected]
    report["daily"].update(today_event_count=len(todays), preview_event_count=len(future), preview_shown_count=len(preview))
    sources = report["coverage"]["sources"]
    by_id = {source["source_id"]: source for source in sources}
    health = report["source_health"]
    labels = {"macro": "宏观", "earnings": "财报", "expiry": "到期节点"}
    scope = "查询类别：" + " / ".join(labels[c] for c in report["filters"]["categories"])
    if "earnings" in report["filters"]["categories"] and set(report["filters"]["companies"]) != set(cfg["companies"]):
        scope += "；公司 " + " / ".join(report["filters"]["companies"])
    if report["filters"]["contracts"]:
        scope += "；合约 " + " / ".join(report["filters"]["contracts"])
    lines = [f"交易盘前简报｜{today}（美东日期）",
             f"查询：{now.astimezone(radar.zone(cfg['calendar_timezone'])).strftime('%H:%M %Z')} / 北京 {now.astimezone(radar.zone(cfg['display_timezone'])).strftime('%Y-%m-%d %H:%M')}；关注 {' / '.join(report['filters']['instruments'])}",
             scope,
             "核验运行：" + {"ok": "正常", "partial": "部分来源缺失", "failed": "失败，日历覆盖不完整"}[report["run_status"]]]
    if health["critical_issue_source_ids"]:
        lines.append("⚠️ 关键来源缺失或待核验：" + "、".join(by_id[s]["name"] for s in health["critical_issue_source_ids"]))
    lines.extend(["", f"未来 {cfg['imminent_hours']:g} 小时："])
    lines.extend(event_line(event, cfg, imminent=True) for event in imminent)
    if not imminent:
        lines.append("- 已核验事件中暂无可置顶的精确时间提醒。")
    lines.extend(["", "今天全部事件："])
    lines.extend(event_line(event, cfg) for event in todays)
    if not todays:
        if report["coverage"]["status"] == "complete":
            lines.append("- 已核验来源，今天没有符合筛选条件的已知事件。")
        elif report["run_status"] == "ok":
            lines.append("- 已核验页面暂无可列出的今日事件；部分财报日期尚未宣布。")
        else:
            lines.append("- 未获得可列出的今日事件；不能据此判断今天没有事件。")
    if report["daily"]["preview_days"]:
        lines.extend(["", f"未来 {report['daily']['preview_days']} 天重点预告（不含今天）："])
        lines.extend(event_line(event, cfg) for event in preview)
        if not preview:
            lines.append("- 已核验信息中暂无可列出的重点事件；仍须结合下方来源状态。")
        if len(future) > len(preview):
            lines.append(f"- 另有 {len(future) - len(preview)} 项重点事件保存在完整 JSON 中。")
    checked = sum(source["status"] == "checked" for source in sources)
    lines.extend(["", f"来源：{checked}/{len(sources)} 完整核验；{len(health['not_announced_source_ids'])} 个日期未宣布；{len(health['issue_source_ids'])} 个缺失或待核验。"])
    if health["not_announced_source_ids"]:
        lines.append("日期尚未宣布：" + "、".join(by_id[s]["name"] for s in health["not_announced_source_ids"]))
    if health["issue_source_ids"]:
        lines.extend(["", "缺失范围："])
        for source_id in health["issue_source_ids"]:
            source = by_id[source_id]
            lines.append(f"- {source['name']}：{radar.COVERAGE_LABELS[source['status']]}；{radar.markdown_cell(source['note'])} [来源](<{source['url']}>)")
    return "\n".join(lines)


def render_run(args, cfg: dict, state: Path) -> dict:
    payload = radar.read_json(args.input)
    query = daily_query(args, cfg, payload)
    directory = validate_run(payload, query, cfg, args, state)
    report = radar.build_report(payload, query, cfg)
    report["daily"] = dict(payload["daily"])
    report["daily"]["exit_code"] = EXIT_CODES[report["run_status"]]
    report["artifacts"] = {"input": str(directory / "input.json"), "report_json": str(directory / "report.json"),
                           "summary_markdown": str(directory / "summary.md")}
    report["summary_zh"] = daily_summary(report, cfg)
    atomic_write(directory / "report.json", json_text(report))
    atomic_write(directory / "summary.md", report["summary_zh"] + "\n")
    return report


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="每日盘前查询：新建运行、核验后生成精简摘要并留存 JSON。")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--input", help="本次 --prepare 返回的 input_path")
    p.add_argument("--state-dir", required=True, help="技能目录外保存运行记录的绝对路径")
    p.add_argument("--config", default=str(radar.DEFAULT_CONFIG))
    p.add_argument("--now", help="仅测试复现使用；实时查询省略")
    p.add_argument("--preview-days", type=int, help="预告天数，不含今天；默认读取配置")
    for field in ("instruments", "categories", "companies", "contracts"):
        p.add_argument("--" + field, nargs="+")
    p.add_argument("--format", choices=["markdown", "json"], default="markdown")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        cfg = radar.load_config(args.config)
        state = storage_path(args.state_dir)
        if args.prepare:
            print(json_text(prepare_run(args, cfg, state)), end="")
            return 0
        report = render_run(args, cfg, state)
        print(json_text(report) if args.format == "json" else report["summary_zh"])
        return EXIT_CODES[report["run_status"]]
    except (radar.InputError, OSError, OverflowError, KeyError, TypeError, AttributeError) as exc:
        print(json_text({"error": str(exc), "run_status": "failed", "coverage": {"status": "unknown"}}), end="")
        return 2


if __name__ == "__main__":
    sys.exit(main())
