"""Synthetic boundary fixtures, deliberately separate from live calendars."""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))
import query_events as radar


class RadarTests(unittest.TestCase):
    def setUp(self):
        self.cfg = radar.load_config()
        self.now = "2026-09-30T12:00:00Z"

    def case(self, options=(), now=None):
        args = radar.parser().parse_args(["--prepare", "--now", now or self.now, *options])
        query = radar.make_query(args, self.cfg)
        payload = radar.prepare(query, self.cfg)
        payload["query_started_at"] = radar.iso(query.now - timedelta(minutes=10))
        for source in payload["coverage"]:
            source.update(status="checked", checked_at=radar.iso(query.now - timedelta(minutes=5)), note="合成测试夹具")
        return query, payload

    def event(self, query, event_type="cpi", **fields):
        canonical = self.cfg.get("type_aliases", {}).get(event_type, event_type)
        rule = self.cfg["event_types"][canonical]
        source_id = f"earnings:{fields.get('symbol', 'NVDA')}" if rule["category"] == "earnings" else rule["source_id"]
        event = {"type": event_type, "precision": "exact", "at": radar.iso(query.now + timedelta(hours=1)),
                 "source_id": source_id, "url": radar.all_sources(self.cfg)[source_id]["urls"][0],
                 "checked_at": radar.iso(query.now - timedelta(minutes=5))}
        if rule["category"] == "earnings":
            event["symbol"] = "NVDA"
        event.update(fields)
        if event["precision"] != "exact":
            event.pop("at", None)
            event.setdefault("timezone", "America/New_York")
            event.setdefault("date", query.start.isoformat())
        return event

    def report(self, query, payload):
        return radar.build_report(payload, query, self.cfg)

    def test_default_range_uses_new_york_not_beijing_date(self):
        query, _ = self.case(now="2026-10-01T02:00:00Z")
        self.assertEqual(str(query.start), "2026-09-30")

    def test_week_is_monday_sunday_even_at_beijing_monday(self):
        query, _ = self.case(["--period", "week"], now="2026-10-05T02:00:00Z")
        self.assertEqual((str(query.start), str(query.end)), ("2026-09-28", "2026-10-04"))

    def test_next_includes_today_and_explicit_range_is_inclusive(self):
        query, _ = self.case(["--period", "next", "--days", "30"])
        self.assertEqual((str(query.start), str(query.end)), ("2026-09-30", "2026-10-29"))
        query, payload = self.case(["--start", "2026-09-29", "--end", "2026-09-30"])
        payload["events"] = [self.event(query, at="2026-10-01T03:59:59Z"), self.event(query, "pce", at="2026-10-01T04:00:00Z")]
        self.assertEqual([e["type"] for e in self.report(query, payload)["events"]], ["cpi"])

    def test_dst_conversion_winter_and_summer(self):
        for now, at, expected in [("2026-01-05T12:00:00Z", "2026-01-05T08:30:00-05:00", "21:30:00"),
                                  ("2026-07-06T11:00:00Z", "2026-07-06T08:30:00-04:00", "20:30:00")]:
            with self.subTest(at=at):
                query, payload = self.case(now=now)
                payload["events"] = [self.event(query, at=at)]
                event = self.report(query, payload)["events"][0]
                self.assertIn(expected, event["time_beijing"])

    def test_dst_transition_week_has_correct_real_duration(self):
        for now, hours in [("2026-03-08T12:00:00Z", 167), ("2026-11-01T12:00:00Z", 169)]:
            with self.subTest(now=now):
                query, _ = self.case(["--period", "week"], now=now)
                start, end = query.bounds(self.cfg)
                self.assertEqual((end - start).total_seconds() / 3600, hours)

    def test_dst_repeat_hour_is_unambiguous_with_offset(self):
        query, payload = self.case(now="2026-11-01T04:00:00Z")
        payload["events"] = [self.event(query, at="2026-11-01T01:30:00-04:00"),
                             self.event(query, "pce", at="2026-11-01T01:30:00-05:00")]
        events = self.report(query, payload)["events"]
        self.assertEqual([e["minutes_to_event"] for e in events], [90, 150])

    def test_beijing_crosses_midnight_without_changing_et_day(self):
        query, payload = self.case()
        payload["events"] = [self.event(query, "fomc_decision", at="2026-09-30T14:00:00-04:00")]
        event = self.report(query, payload)["events"][0]
        self.assertEqual(event["date_et_candidates"], ["2026-09-30"])
        self.assertEqual(event["date_beijing_candidates"], ["2026-10-01"])

    def test_four_hour_window_is_inclusive_but_excludes_past(self):
        query, payload = self.case()
        offsets = [-1, 0, 4 * 3600, 4 * 3600 + 1]
        types = ["cpi", "pce", "nonfarm_payrolls", "fomc_decision"]
        payload["events"] = [self.event(query, t, at=radar.iso(query.now + timedelta(seconds=s))) for t, s in zip(types, offsets)]
        report = self.report(query, payload)
        imminent_types = [e["type"] for e in report["events"] if e["id"] in report["imminent_event_ids"]]
        self.assertEqual(imminent_types, ["pce", "nonfarm_payrolls"])
        self.assertEqual(len(report["events"]), 4)
        self.assertEqual(report["events"][0]["schedule_status"], "time_passed")
        self.assertIn("计划时间已过", report["summary_zh"])

    def test_date_and_session_have_no_exact_countdown(self):
        query, payload = self.case()
        payload["events"] = [self.event(query, "earnings_release", precision="session", session="after_close"),
                             self.event(query, "gc_first_notice", precision="date", contract="GCV26")]
        report = self.report(query, payload)
        self.assertFalse(report["imminent_event_ids"])
        for event in report["events"]:
            self.assertIsNone(event["at_utc"])
            self.assertIsNone(event["minutes_to_event"])
            self.assertIn("未确认", event["time_beijing"])
        self.assertIn("盘后", next(e for e in report["events"] if e["type"] == "earnings_release")["time_et"])

    def test_fomc_decision_and_press_conference_stay_separate(self):
        query, payload = self.case()
        payload["events"] = [self.event(query, "fomc_decision"), self.event(query, "fomc_press_conference")]
        report = self.report(query, payload)
        self.assertEqual(len(report["events"]), 2)
        self.assertEqual(len({e["id"] for e in report["events"]}), 2)

    def test_core_inflation_merges_and_collects_source_links(self):
        query, payload = self.case()
        payload["events"] = [self.event(query), self.event(query, "core_cpi", url="https://www.bls.gov/schedule/2026/09_sched.htm"),
                             self.event(query, "pce"), self.event(query, "core_pce")]
        report = self.report(query, payload)
        self.assertEqual({e["type"] for e in report["events"]}, {"cpi", "pce"})
        self.assertEqual(len(next(e for e in report["events"] if e["type"] == "cpi")["sources"]), 2)

    def test_release_and_call_are_independent(self):
        query, payload = self.case(["--categories", "earnings", "--companies", "NVDA"])
        payload["events"] = [self.event(query, "earnings_release", precision="session", session="after_close"),
                             self.event(query, "earnings_call", at="2026-09-30T17:00:00-04:00")]
        events = self.report(query, payload)["events"]
        self.assertEqual(len(events), 2)
        self.assertIsNone(next(e for e in events if e["type"] == "earnings_release")["minutes_to_event"])

    def test_gc_earnings_are_background_and_do_not_trigger_imminent(self):
        query, payload = self.case(["--instruments", "GC", "--categories", "earnings", "--companies", "NVDA"])
        payload["events"] = [self.event(query, "earnings_release")]
        report = self.report(query, payload)
        self.assertEqual(report["events"][0]["levels"], {"GC": "background"})
        self.assertFalse(report["imminent_event_ids"])

    def test_gold_contract_nodes_and_contracts_are_not_merged(self):
        query, payload = self.case(["--instruments", "GC", "--categories", "expiry"])
        payload["events"] = [self.event(query, t, precision="date", contract=c) for c in ["GCV26", "GCZ26"]
                             for t in ["gc_options_expiry", "gc_first_notice", "gc_last_trade"]]
        report = self.report(query, payload)
        self.assertEqual(len(report["events"]), 6)
        self.assertEqual(len({e["id"] for e in report["events"]}), 6)
        for event in report["events"]:
            self.assertIn(event["contract"], event["title"])

    def test_contract_filter_normalizes_four_digit_year(self):
        query, payload = self.case(["--instruments", "GC", "--categories", "expiry", "--contracts", "GCZ2026"])
        payload["events"] = [self.event(query, "gc_last_trade", precision="date", contract=c) for c in ["GCV26", "GCZ26"]]
        self.assertEqual([e["contract"] for e in self.report(query, payload)["events"]], ["GCZ26"])

    def test_contract_month_is_explicit_and_option_series_is_preserved(self):
        query, payload = self.case(["--instruments", "GC", "--categories", "expiry"])
        payload["events"] = [self.event(query, "gc_options_expiry", contract="GCZ2026", option_contract="OGX26"),
                             self.event(query, "gc_first_notice", precision="date", contract="GCV26")]
        events = self.report(query, payload)["events"]
        options = next(e for e in events if e["type"] == "gc_options_expiry")
        self.assertEqual((options["contract_month"], options["option_contract"]), ("2026-12", "OGX26"))
        self.assertIn("2026-12", options["title"])
        self.assertEqual(next(e for e in events if e["type"] == "gc_first_notice")["contract_month"], "2026-10")

    def test_changing_contract_does_not_reuse_old_complete_coverage(self):
        query, payload = self.case(["--instruments", "GC", "--categories", "expiry", "--contracts", "GCZ26"])
        payload["events"] = [self.event(query, "gc_first_notice", precision="date", contract="GCZ26")]
        args = radar.parser().parse_args(["--now", self.now, "--contracts", "GCV26"])
        changed = radar.make_query(args, self.cfg, payload)
        report = self.report(changed, payload)
        self.assertFalse(report["events"])
        self.assertEqual(report["coverage"]["status"], "partial")
        self.assertTrue(all(s["status"] == "partial" for s in report["coverage"]["sources"]))

    def test_narrowing_contract_scope_preserves_coverage(self):
        query, payload = self.case(["--instruments", "GC", "--categories", "expiry", "--contracts", "GCZ26", "GCV26"])
        payload["events"] = [self.event(query, "gc_first_notice", precision="date", contract=c) for c in ["GCZ26", "GCV26"]]
        args = radar.parser().parse_args(["--now", self.now, "--contracts", "GCZ26"])
        report = self.report(radar.make_query(args, self.cfg, payload), payload)
        self.assertEqual([event["contract"] for event in report["events"]], ["GCZ26"])
        self.assertEqual(report["coverage"]["status"], "complete")

    def test_source_date_preserved_and_never_recomputed_as_third_friday(self):
        query, payload = self.case(["--start", "2026-06-18"], now="2026-06-18T12:00:00Z")
        payload["events"] = [self.event(query, "us_monthly_opex", precision="date", date="2026-06-18")]
        self.assertEqual(self.report(query, payload)["events"][0]["event_date"], "2026-06-18")

    def test_chicago_date_only_keeps_timezone_and_unknown_et_time(self):
        query, payload = self.case(["--instruments", "GC", "--categories", "expiry"])
        payload["events"] = [self.event(query, "gc_last_trade", precision="date", timezone="America/Chicago", contract="GCV26")]
        event = self.report(query, payload)["events"][0]
        self.assertIn("America/Chicago", event["time_et"])
        self.assertEqual(event["date_et_candidates"], ["2026-09-30", "2026-10-01"])
        self.assertIsNone(event["minutes_to_event"])

    def test_missing_sources_are_not_clean_no_event_result(self):
        query, payload = self.case()
        payload["coverage"] = []
        report = self.report(query, payload)
        self.assertEqual(report["coverage"]["status"], "unavailable")
        self.assertIn("不能据此判断", report["summary_zh"])
        self.assertNotIn("指定范围内未找到", report["summary_zh"])

    def test_checked_empty_source_differs_from_not_announced(self):
        query, payload = self.case(["--categories", "earnings", "--companies", "AMD"])
        clean = self.report(query, payload)
        self.assertEqual(clean["coverage"]["status"], "complete")
        payload["coverage"][0]["status"] = "not_announced"
        unknown = self.report(query, payload)
        self.assertEqual(unknown["coverage"]["status"], "partial")
        self.assertIn("日期尚未宣布", unknown["summary_zh"])

    def test_failed_source_preserves_other_verified_events(self):
        query, payload = self.case()
        payload["events"] = [self.event(query)]
        payload["coverage"][0].update(status="unavailable", checked_at=None, note="官方页面超时")
        report = self.report(query, payload)
        self.assertEqual(report["coverage"]["status"], "partial")
        self.assertEqual(len(report["events"]), 1)
        self.assertIn("官方页面超时", report["summary_zh"])

    def test_stale_verification_does_not_trigger_imminent(self):
        query, payload = self.case()
        event = self.event(query)
        event["checked_at"] = radar.iso(query.now - timedelta(minutes=20))
        payload["events"] = [event]
        report = self.report(query, payload)
        self.assertEqual(report["events"][0]["verification_status"], "stale")
        self.assertFalse(report["imminent_event_ids"])
        self.assertEqual(report["coverage"]["status"], "partial")

    def test_max_age_marks_even_old_query_coverage_as_stale(self):
        query, payload = self.case()
        payload["query_started_at"] = radar.iso(query.now - timedelta(hours=2))
        for source in payload["coverage"]:
            source["checked_at"] = radar.iso(query.now - timedelta(minutes=61))
        self.assertEqual(self.report(query, payload)["coverage"]["status"], "unavailable")

    def test_conflicting_exact_times_are_not_an_imminent_alert(self):
        query, payload = self.case()
        payload["events"] = [self.event(query), self.event(query, at=radar.iso(query.now + timedelta(hours=2)))]
        report = self.report(query, payload)
        self.assertEqual(len(report["events"]), 1)
        event = report["events"][0]
        self.assertEqual(event["confirmation_status"], "unconfirmed")
        self.assertIsNone(event["at_utc"])
        self.assertFalse(report["imminent_event_ids"])
        self.assertEqual(report["coverage"]["status"], "partial")

    def test_fresh_date_is_preferred_to_stale_exact_time(self):
        query, payload = self.case()
        old = self.event(query, checked_at=radar.iso(query.now - timedelta(hours=2)))
        payload["events"] = [old, self.event(query, precision="date")]
        event = self.report(query, payload)["events"][0]
        self.assertEqual(event["precision"], "date")
        self.assertEqual(event["verification_status"], "verified")

    def test_source_host_spoof_is_rejected(self):
        query, payload = self.case()
        payload["events"] = [self.event(query, url="https://www.bls.gov.example.com/schedule")]
        with self.assertRaises(radar.InputError):
            self.report(query, payload)

    def test_wrong_source_and_naive_datetime_are_rejected(self):
        query, payload = self.case()
        for fields in [{"source_id": "bea"}, {"at": "2026-09-30T09:00:00"}, {"checked_at": "2026-09-30T13:00:00Z"}]:
            with self.subTest(fields=fields), self.assertRaises(radar.InputError):
                payload["events"] = [self.event(query, **fields)]
                self.report(query, payload)

    def test_gold_node_requires_matching_contract(self):
        query, payload = self.case()
        for contract in [None, "ESZ26", "GCQ", "GCZ1999"]:
            with self.subTest(contract=contract), self.assertRaises(radar.InputError):
                payload["events"] = [self.event(query, "gc_first_notice", precision="date", contract=contract)]
                self.report(query, payload)

    def test_query_expansion_downgrades_coverage(self):
        query, payload = self.case()
        args = radar.parser().parse_args(["--period", "next", "--days", "7", "--now", self.now])
        expanded = radar.make_query(args, self.cfg, payload)
        self.assertEqual(self.report(expanded, payload)["coverage"]["status"], "partial")

    def test_filters_survive_prepare_and_render_from_another_cwd(self):
        query, payload = self.case(["--period", "week", "--categories", "earnings", "--companies", "AMD", "SPCX"])
        with tempfile.TemporaryDirectory() as working_directory:
            result = subprocess.run([sys.executable, str(SKILL / "scripts/query_events.py"), "--input", "-", "--format", "json", "--now", self.now],
                                    input=json.dumps(payload), capture_output=True, text=True, cwd=working_directory)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["filters"]["companies"], ["AMD", "SPCX"])
        self.assertEqual(report["range"]["start_date"], "2026-09-28")

    def test_custom_window_and_levels_are_used(self):
        self.cfg["imminent_hours"] = 2
        self.cfg["event_types"]["cpi"]["instruments"]["GC"] = "background"
        query, payload = self.case(["--instruments", "GC"])
        payload["events"] = [self.event(query), self.event(query, "pce", at=radar.iso(query.now + timedelta(hours=3)))]
        self.assertFalse(self.report(query, payload)["imminent_event_ids"])

    def test_invalid_cli_returns_json_error_with_exit_two(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = radar.main(["--prepare", "--period", "next", "--days", "0", "--now", self.now])
        self.assertEqual(code, 2)
        self.assertIn("error", json.loads(output.getvalue()))

    def test_unknown_company_invalid_date_and_duplicate_json_keys(self):
        for options in [["--companies", "UNKNOWN"], ["--start", "2026-02-30"], ["--contracts", "GCZ26", "--instruments", "ES"]]:
            with self.subTest(options=options), self.assertRaises(radar.InputError):
                self.case(options)
        with self.assertRaises(radar.InputError):
            radar.json_object([("type", "cpi"), ("type", "pce")])

    def test_malformed_event_and_precision_mix_are_rejected(self):
        query, payload = self.case()
        for item in [None, {"type": []}, {**self.event(query, precision="date"), "at": self.now}]:
            with self.subTest(item=item), self.assertRaises(radar.InputError):
                payload["events"] = [item]
                self.report(query, payload)


if __name__ == "__main__":
    unittest.main()
