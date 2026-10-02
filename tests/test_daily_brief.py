"""Daily-run and installed-bundle behavior, with synthetic calendar evidence."""

import contextlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))
import daily_brief as daily
import query_events as radar


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.state = self.root / "state"
        self.cfg = radar.load_config()
        self.now = "2026-10-02T12:00:00Z"

    def invoke(self, *options, now=None):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = daily.main(["--state-dir", str(self.state), "--now", now or self.now, *options])
        return code, output.getvalue()

    def prepare(self, *options, now=None, verified=True):
        code, output = self.invoke("--prepare", *options, now=now)
        self.assertEqual(code, 0, output)
        manifest = json.loads(output)
        path = Path(manifest["input_path"])
        payload = radar.read_json(str(path))
        if verified:
            for source in payload["coverage"]:
                source.update(status="checked", checked_at=payload["query_started_at"], note="合成测试，非真实日历")
            self.save(path, payload)
        return manifest, path, payload

    def save(self, path, payload):
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def event(self, payload, kind="cpi", **fields):
        rule = self.cfg["event_types"][kind]
        symbol = fields.get("symbol", "NVDA")
        source_id = f"earnings:{symbol}" if rule["category"] == "earnings" else rule["source_id"]
        event = {"type": kind, "precision": "exact", "at": radar.iso(radar.timestamp(self.now, "now") + timedelta(hours=1)),
                 "source_id": source_id, "url": radar.all_sources(self.cfg)[source_id]["urls"][0],
                 "checked_at": payload["query_started_at"]}
        if rule["category"] == "earnings":
            event["symbol"] = symbol
        event.update(fields)
        if event["precision"] != "exact":
            event.pop("at", None)
            event.setdefault("date", payload["range"]["start_date"])
            event.setdefault("timezone", "America/New_York")
        return event

    def render(self, path, **kwargs):
        code, output = self.invoke("--input", str(path), "--format", "json", **kwargs)
        return code, json.loads(output)

    def test_each_prepare_has_unique_paths_even_at_same_instant(self):
        first, first_path, first_payload = self.prepare()
        second, second_path, _ = self.prepare()
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertNotEqual(first_path, second_path)
        self.assertEqual(radar.read_json(str(first_path)), first_payload)
        self.assertEqual(first["collection_deadline_at"], "2026-10-02T12:08:00Z")

    def test_next_day_prepare_recomputes_range_without_overwriting_yesterday(self):
        _, old, before = self.prepare()
        _, new, after = self.prepare(now="2026-10-03T12:00:00Z")
        self.assertEqual((before["range"]["start_date"], before["range"]["end_date"]), ("2026-10-02", "2026-10-09"))
        self.assertEqual((after["range"]["start_date"], after["range"]["end_date"]), ("2026-10-03", "2026-10-10"))
        self.assertNotEqual(old, new)

    def test_yesterday_input_is_rejected_even_if_source_checks_are_refreshed(self):
        _, path, payload = self.prepare()
        for source in payload["coverage"]:
            source["checked_at"] = "2026-10-03T12:00:00Z"
        self.save(path, payload)
        code, report = self.render(path, now="2026-10-03T12:00:00Z")
        self.assertEqual(code, 2)
        self.assertIn("重新 --prepare", report["error"])
        self.assertFalse((path.parent / "report.json").exists())

    def test_request_timestamp_cannot_be_refreshed_in_place(self):
        _, path, payload = self.prepare()
        payload["query_started_at"] = "2026-10-03T12:00:00Z"
        self.save(path, payload)
        code, report = self.render(path, now="2026-10-03T12:00:00Z")
        self.assertEqual(code, 2)
        self.assertIn("请求条件已被修改", report["error"])

    def test_same_day_stale_run_is_rejected_at_age_boundary(self):
        _, path, _ = self.prepare()
        self.assertEqual(self.render(path, now="2026-10-02T13:00:00Z")[0], 0)
        code, report = self.render(path, now="2026-10-02T13:00:01Z")
        self.assertEqual(code, 2)
        self.assertIn("过期", report["error"])

    def test_new_york_day_controls_query_across_beijing_midnight(self):
        _, path, payload = self.prepare(now="2026-10-02T02:00:00Z")
        self.assertEqual(payload["daily"]["date_et"], "2026-10-01")
        code, report = self.render(path, now="2026-10-02T02:01:00Z")
        self.assertEqual(code, 0)
        self.assertIn("北京 2026-10-02 10:01", report["summary_zh"])

    def test_midnight_rollover_requires_new_run_even_with_fresh_checks(self):
        _, path, _ = self.prepare(now="2026-10-02T03:59:00Z")
        self.assertEqual(self.render(path, now="2026-10-02T04:01:00Z")[0], 2)

    def test_preview_uses_calendar_days_over_spring_and_autumn_dst(self):
        for now, end, hours in [("2026-03-07T12:00:00Z", "2026-03-09", 71), ("2026-10-31T12:00:00Z", "2026-11-02", 73)]:
            with self.subTest(now=now):
                _, path, payload = self.prepare("--preview-days", "2", now=now)
                args = daily.parser().parse_args(["--input", str(path), "--state-dir", str(self.state), "--now", now])
                query = daily.daily_query(args, self.cfg, payload)
                self.assertEqual(str(query.end), end)
                start_at, end_at = query.bounds(self.cfg)
                self.assertEqual((end_at - start_at).total_seconds() / 3600, hours)

    def test_custom_preview_and_filters_survive_render(self):
        _, path, _ = self.prepare("--preview-days", "2", "--instruments", "GC", "--categories", "expiry", "--contracts", "GCZ26")
        code, report = self.render(path)
        self.assertEqual(code, 0)
        self.assertEqual(report["range"]["end_date"], "2026-10-04")
        self.assertEqual(report["filters"]["contracts"], ["GCZ26"])

    def test_zero_preview_is_valid_and_hides_preview_section(self):
        _, path, _ = self.prepare("--preview-days", "0")
        code, report = self.render(path)
        self.assertEqual(code, 0)
        self.assertEqual(report["range"]["start_date"], report["range"]["end_date"])
        self.assertNotIn("重点预告", report["summary_zh"])

    def test_summary_discloses_filtered_scope_and_contract(self):
        _, path, _ = self.prepare("--categories", "earnings", "expiry", "--companies", "AMD", "--instruments", "GC", "--contracts", "GCZ26")
        code, report = self.render(path)
        self.assertEqual(code, 0)
        self.assertIn("查询类别：财报 / 到期节点；公司 AMD；合约 GCZ26", report["summary_zh"])

    def test_render_cannot_change_query_filters_or_configuration(self):
        _, path, _ = self.prepare()
        code, _ = self.invoke("--input", str(path), "--instruments", "GC")
        self.assertEqual(code, 2)
        cfg_path = self.root / "custom.json"
        self.cfg["imminent_hours"] = 2
        self.save(cfg_path, self.cfg)
        code, output = self.invoke("--input", str(path), "--config", str(cfg_path))
        self.assertEqual(code, 2)
        self.assertIn("配置与准备查询时不一致", output)

    def test_empty_verified_day_sends_summary_and_archives_matching_json(self):
        _, path, _ = self.prepare()
        code, text = self.invoke("--input", str(path))
        self.assertEqual(code, 0)
        report = radar.read_json(str(path.parent / "report.json"))
        self.assertEqual(text.strip(), report["summary_zh"])
        self.assertEqual(text, (path.parent / "summary.md").read_text(encoding="utf-8"))
        self.assertIn("没有符合筛选条件的已知事件", text)
        self.assertNotIn('"schema_version"', text)
        self.assertNotIn("```json", text)

    def test_not_announced_is_healthy_but_not_complete_calendar(self):
        _, path, payload = self.prepare("--categories", "earnings", "--companies", "AMD")
        payload["coverage"][0]["status"] = "not_announced"
        self.save(path, payload)
        code, report = self.render(path)
        self.assertEqual((code, report["run_status"], report["coverage"]["status"]), (0, "ok", "partial"))
        self.assertEqual(report["source_health"]["issue_source_ids"], [])
        self.assertIn("日期尚未宣布", report["summary_zh"])
        self.assertNotIn("没有符合筛选条件的已知事件", report["summary_zh"])

    def test_noncritical_outage_is_partial_and_preserves_verified_events(self):
        _, path, payload = self.prepare()
        next(s for s in payload["coverage"] if s["source_id"] == "earnings:AMD").update(status="unavailable", checked_at=None, note="读取超时")
        payload["events"] = [self.event(payload)]
        self.save(path, payload)
        code, report = self.render(path)
        self.assertEqual((code, report["run_status"]), (3, "partial"))
        self.assertEqual(len(report["events"]), 1)
        self.assertEqual(report["source_health"]["critical_issue_source_ids"], [])
        self.assertIn("读取超时", report["summary_zh"])

    def test_critical_partial_or_unavailable_source_marks_failed_and_saves_report(self):
        for status in ["partial", "unavailable"]:
            with self.subTest(status=status):
                _, path, payload = self.prepare()
                next(s for s in payload["coverage"] if s["source_id"] == "fed").update(status=status, note="记者会时间未能核验")
                payload["events"] = [self.event(payload)]
                self.save(path, payload)
                code, report = self.render(path)
                self.assertEqual((code, report["run_status"]), (4, "failed"))
                self.assertEqual(report["source_health"]["critical_issue_source_ids"], ["fed"])
                self.assertEqual(len(report["events"]), 1)
                self.assertTrue((path.parent / "report.json").exists())
                self.assertLess(report["summary_zh"].index("关键来源缺失"), report["summary_zh"].index("今天全部事件"))

    def test_all_unavailable_fails_even_in_noncritical_earnings_query(self):
        _, path, _ = self.prepare("--categories", "earnings", "--companies", "AMD", verified=False)
        code, report = self.render(path)
        self.assertEqual((code, report["run_status"], report["coverage"]["status"]), (4, "failed", "unavailable"))
        self.assertIn("不能据此判断", report["summary_zh"])

    def test_critical_policy_is_configurable_and_old_configs_get_defaults(self):
        old_cfg = dict(self.cfg)
        del old_cfg["daily"]
        self.assertEqual(radar.daily_settings(old_cfg)["preview_days"], 7)
        self.cfg["daily"]["critical_categories"] = []
        config_path = self.root / "config.json"
        self.save(config_path, self.cfg)
        _, path, payload = self.prepare("--config", str(config_path))
        next(s for s in payload["coverage"] if s["source_id"] == "fed").update(status="unavailable")
        self.save(path, payload)
        code, output = self.invoke("--input", str(path), "--config", str(config_path), "--format", "json")
        self.assertEqual(code, 3, output)

    def test_today_is_never_truncated_and_preview_only_limits_display(self):
        _, path, payload = self.prepare()
        for symbol in self.cfg["companies"]:
            for kind in ("earnings_release", "earnings_call"):
                payload["events"].append(self.event(payload, kind, symbol=symbol))
        for offset in range(1, 8):
            for kind in ("cpi", "pce", "nonfarm_payrolls"):
                payload["events"].append(self.event(payload, kind, at=radar.iso(radar.timestamp(self.now, "now") + timedelta(days=offset))))
        self.save(path, payload)
        code, report = self.render(path)
        self.assertEqual(code, 0)
        self.assertEqual(len(report["events"]), 39)
        self.assertEqual(report["daily"]["today_event_count"], 18)
        self.assertEqual(report["daily"]["preview_event_count"], 21)
        self.assertEqual(report["daily"]["preview_shown_count"], 12)
        today_section = report["summary_zh"].split("今天全部事件：")[1].split("重点预告")[0]
        for symbol in self.cfg["companies"]:
            self.assertIn(symbol, today_section)
        self.assertIn("另有 9 项", report["summary_zh"])

    def test_daily_keeps_passed_and_unknown_times_with_correct_precision(self):
        _, path, payload = self.prepare()
        payload["events"] = [self.event(payload, at="2026-10-02T11:59:00Z"),
                             self.event(payload, "earnings_release", precision="session", session="after_close"),
                             self.event(payload, "gc_first_notice", precision="date", contract="GCZ26", note="核对持仓合约月份")]
        self.save(path, payload)
        code, report = self.render(path)
        self.assertEqual(code, 0)
        self.assertFalse(report["imminent_event_ids"])
        self.assertIn("计划时间已过", report["summary_zh"])
        self.assertIn("盘后", report["summary_zh"])
        self.assertIn("GCZ26，2026-12", report["summary_zh"])
        self.assertIn("核对持仓合约月份", report["summary_zh"])
        for event in report["events"]:
            if event["precision"] != "exact":
                self.assertIsNone(event["minutes_to_event"])

    def test_io_failure_is_not_reported_as_success(self):
        _, path, _ = self.prepare()
        with mock.patch.object(daily, "atomic_write", side_effect=PermissionError("模拟只读存储")):
            code, report = self.render(path)
        self.assertEqual(code, 2)
        self.assertIn("只读存储", report["error"])

    def test_invalid_state_directory_and_preview_are_rejected(self):
        for raw in ["relative/state", str(SKILL / "state")]:
            with self.subTest(raw=raw), self.assertRaises(radar.InputError):
                daily.storage_path(raw)
        self.assertEqual(self.invoke("--prepare", "--preview-days", "-1")[0], 2)

    def test_generic_query_retains_exit_zero_for_partial_coverage(self):
        _, path, _ = self.prepare(verified=False)
        process = subprocess.run([sys.executable, "-B", str(SKILL / "scripts/query_events.py"), "--input", str(path),
                                  "--now", self.now, "--format", "json"], capture_output=True, text=True, cwd=self.root)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(json.loads(process.stdout)["run_status"], "failed")

    def test_installed_bundle_runs_with_only_directly_linked_support_files(self):
        # Exercise the documented installation contract, without installing Hermes globally.
        bundle = self.root / "installed" / "market-event-radar"
        bundle.mkdir(parents=True)
        content = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        paths = set(re.findall(r"\]\(((?:scripts|assets|references|examples|templates)/[^)]+)\)", content))
        self.assertIn("assets/config.json", paths)
        self.assertIn("scripts/query_events.py", paths)
        self.assertIn("scripts/daily_brief.py", paths)
        for name in paths | {"SKILL.md"}:
            destination = bundle / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SKILL / name, destination)
        self.assertFalse((bundle / "config.json").exists())
        self.assertFalse((bundle / "tests").exists())
        command = [sys.executable, "-B", str(bundle / "scripts/daily_brief.py"), "--state-dir", str(self.state), "--now", self.now]
        prepare = subprocess.run([*command, "--prepare"], capture_output=True, text=True, cwd=self.root)
        self.assertEqual(prepare.returncode, 0, prepare.stdout + prepare.stderr)
        path = json.loads(prepare.stdout)["input_path"]
        render = subprocess.run([*command, "--input", path, "--format", "json"], capture_output=True, text=True, cwd=self.root)
        self.assertEqual(render.returncode, 4, render.stdout + render.stderr)
        self.assertEqual(json.loads(render.stdout)["run_status"], "failed")
        self.assertTrue(Path(path).with_name("report.json").is_file())


if __name__ == "__main__":
    unittest.main()
