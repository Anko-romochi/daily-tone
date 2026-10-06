"""Independent behavioral checks for the approved v0.4 contract."""
from __future__ import annotations

import copy
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import daily_tone as tone  # noqa: E402

UTC = dt.timezone.utc
JST = dt.timezone(dt.timedelta(hours=9))


def at(day: int, hour: int = 12, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 10, day, hour, minute, tzinfo=JST)


def event(number: int, kind: str = "compliment", when: dt.datetime | None = None) -> dict:
    return {"id": f"event_{number:02d}", "kind": kind,
            "at": (when or at(number)).isoformat(timespec="seconds")}


def fresh_state(now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime(2026, 9, 30, 12, tzinfo=JST)
    return {"version": 1, "growth_basis": [], "scene": "ordinary",
            "scene_until": None, "accepted_until": now.isoformat(timespec="seconds")}


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.config = {"version": 1, "enabled": True, "sweetness": 3, "learning": True}

    def apply(self, state, item, now=None, config=None):
        return tone.apply_event(config or self.config, state, item, now or dt.datetime.fromisoformat(item["at"]))

    def test_t01_first_positive_appraises_and_is_eligible_to_learn(self):
        tone_result, new = self.apply(fresh_state(), event(1))
        self.assertEqual(tone_result["appraisal"]["personal_relevance"], 1)
        self.assertEqual(tone_result["style_tag"], "friendly_ack")
        self.assertEqual(tone_result["intensity"], 1)
        self.assertEqual([x["id"] for x in new["growth_basis"]], ["event_01"])

    def test_t02_same_japan_date_does_not_append(self):
        _, first = self.apply(fresh_state(), event(1, when=at(1, 8)))
        _, second = self.apply(first, event(2, when=at(1, 20)))
        self.assertEqual(len(second["growth_basis"]), 1)
        self.assertEqual(second["growth_basis"][0]["id"], "event_01")

    def test_t03_appraisal_precedes_growth_threshold_crossing(self):
        state = fresh_state()
        for n in (1, 2):
            _, state = self.apply(state, event(n, "shared_win", at(n)))
        third_tone, state = self.apply(state, event(3, "shared_win", at(3)))
        self.assertEqual(third_tone["appraisal"]["personal_relevance"], 1)
        self.assertEqual(len(state["growth_basis"]), 3)
        fourth_tone, _ = self.apply(state, event(4, "shared_win", at(4)))
        self.assertEqual(fourth_tone["appraisal"]["personal_relevance"], 2)

    def test_t04_basis_saturates_at_six_but_transient_tone_continues(self):
        state = fresh_state()
        for n in range(1, 7):
            current, state = self.apply(state, event(n, "shared_win", at(n)))
        self.assertEqual(current["appraisal"]["personal_relevance"], 2)  # sixth sees five prior items
        seventh_tone, after = self.apply(state, event(7, "shared_win", at(7)))
        self.assertEqual(seventh_tone["appraisal"]["personal_relevance"], 3)
        self.assertEqual(len(after["growth_basis"]), 6)
        self.assertEqual([item["id"] for item in after["growth_basis"]], [f"event_{n:02d}" for n in range(1, 7)])
        self.assertEqual(after["accepted_until"], event(7, "shared_win", at(7))["at"])
        self.assertEqual(after["scene"], "shared_pride")

    def test_t05_one_hundred_identical_retries_reject_without_mutation(self):
        first = event(1)
        _, accepted = self.apply(fresh_state(), first)
        snapshot = copy.deepcopy(accepted)
        for _ in range(100):
            with self.assertRaises(tone.ToneError):
                self.apply(accepted, first)
        self.assertEqual(accepted, snapshot)

    def test_t06_equal_older_and_clock_rollback_reject(self):
        state = fresh_state(at(5))
        for when in (at(5), at(4), at(3)):
            with self.assertRaises(tone.ToneError):
                self.apply(state, event(1, when=when), now=at(5))
        # A timestamp newer than the watermark is still refused while wall time
        # has moved behind that watermark, even inside the future tolerance.
        rollback_now = at(5)
        future_state = fresh_state(rollback_now + dt.timedelta(minutes=1))
        with self.assertRaises(tone.ToneError):
            self.apply(future_state, event(2, when=rollback_now + dt.timedelta(minutes=2)), now=rollback_now)
        self.assertEqual(state, fresh_state(at(5)))

    def test_t07_future_beyond_five_minutes_rejects_but_boundary_is_valid(self):
        now = at(1)
        state = fresh_state(now)
        with self.assertRaises(tone.ToneError):
            self.apply(state, event(1, when=now + dt.timedelta(minutes=5, seconds=1)), now=now)
        boundary = now + dt.timedelta(minutes=5)
        result, updated = self.apply(state, event(2, when=boundary), now=now)
        self.assertEqual(result["input_id"], "event_02")
        self.assertEqual(updated["accepted_until"], boundary.isoformat(timespec="seconds"))

    def test_t08_equal_counts_different_kinds_produce_different_compliment_appraisal(self):
        win_state, praise_state = fresh_state(), fresh_state()
        for n in range(1, 7):
            _, win_state = self.apply(win_state, event(n, "shared_win", at(n)))
            _, praise_state = self.apply(praise_state, event(n, "compliment", at(n)))
        win_tone, _ = self.apply(win_state, event(20, "compliment", at(10)))
        praise_tone, _ = self.apply(praise_state, event(21, "compliment", at(10)))
        self.assertEqual(win_tone["appraisal"]["personal_relevance"], 3)
        self.assertEqual(praise_tone["appraisal"]["personal_relevance"], 2)
        self.assertEqual(win_tone["style_tag"], "bashful")
        self.assertEqual(praise_tone["style_tag"], "bashful")

    def test_t09_positive_learning_during_quiet_preserves_quiet_expiry(self):
        now = at(1)
        _, quiet_state = self.apply(fresh_state(now - dt.timedelta(minutes=1)), event(1, "quiet", now), now=now)
        expiry = quiet_state["scene_until"]
        reply_tone, after = self.apply(quiet_state, event(2, "shared_win", now + dt.timedelta(minutes=10)))
        self.assertEqual(reply_tone["appraisal"]["personal_relevance"], 1)
        self.assertEqual(reply_tone["reason_ids"], [])
        self.assertEqual(reply_tone["style_tag"], "quiet")
        self.assertEqual(reply_tone["intensity"], 0)
        self.assertEqual(len(after["growth_basis"]), 1)
        self.assertEqual(after["scene_until"], expiry)

    def test_t10_expiration_is_read_only_and_absence_does_not_erase_basis(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = tone.Store(tmp)
            store.initialize(self.config, dt.datetime(2026, 9, 30, 12, tzinfo=JST))
            store.event(event(1, "shared_win", at(1)), at(1))
            store.event(event(2, "quiet", at(2)), at(2))
            before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
                      for p in Path(tmp).iterdir() if p.is_file()}
            config, state = store.read()
            original_state = copy.deepcopy(state)
            snapshot = tone.show_snapshot(config, state, at(3))
            long_absence = tone.show_snapshot(config, state, at(3) + dt.timedelta(days=365))
            self.assertEqual(snapshot["effective_scene"], "ordinary")
            self.assertEqual(long_absence["effective_scene"], "ordinary")
            self.assertEqual(snapshot["state"]["scene"], "ordinary")
            self.assertIsNone(snapshot["state"]["scene_until"])
            self.assertEqual(long_absence["state"]["scene"], "ordinary")
            self.assertEqual(long_absence["state"]["growth_basis"], state["growth_basis"])
            self.assertEqual(state, original_state)
            after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
                     for p in Path(tmp).iterdir() if p.is_file()}
            self.assertEqual(before, after)

    def test_t11_off_and_zero_clear_scene_before_acceptance_and_do_not_revive(self):
        for changes in ({"enabled": False}, {"sweetness": 0}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as tmp:
                store = tone.Store(tmp)
                store.initialize(self.config, dt.datetime(2026, 9, 30, 12, tzinfo=JST))
                store.event(event(1, "shared_win", at(1)), at(1))
                old_basis = store.read()[1]["growth_basis"]
                configured = store.configure(changes, at(2))
                state = store.read()[1]
                self.assertEqual(state["scene"], "ordinary")
                self.assertIsNone(state["scene_until"])
                disabled = {**self.config, **changes}
                off_tone, after = self.apply(state, event(2, "shared_win", at(2)), config=disabled)
                self.assertEqual(off_tone["intensity"], 0)
                self.assertEqual(after["growth_basis"], old_basis)
                self.assertEqual(after["accepted_until"], event(2, "shared_win", at(2))["at"])
                store.configure({"enabled": True, "sweetness": 3}, at(3))
                self.assertEqual(store.read()[1]["scene"], "ordinary")

    def test_t12_learning_paused_still_uses_prior_basis(self):
        state = fresh_state()
        for n in range(1, 7):
            _, state = self.apply(state, event(n, "shared_win", at(n)))
        config = {**self.config, "learning": False}
        result, after = self.apply(state, event(20, "compliment", at(10)), config=config)
        self.assertEqual(result["appraisal"]["personal_relevance"], 3)
        self.assertEqual(after["growth_basis"], state["growth_basis"])

    def test_t13_confirmed_reset_preserves_config_and_clears_basis(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = tone.Store(tmp)
            custom = {**self.config, "sweetness": 2, "learning": False}
            store.initialize(custom, dt.datetime(2026, 9, 30, 12, tzinfo=JST))
            store.event(event(1, "shared_win", at(1)), at(1))
            with self.assertRaises(tone.ToneError):
                store.reset(at(2), confirm=False)
            store.reset(at(2), confirm=True)
            saved_config, state = store.read()
            self.assertEqual(saved_config, custom)
            self.assertEqual(state["growth_basis"], [])
            self.assertEqual(state["scene"], "ordinary")
            self.assertEqual(state["accepted_until"], at(2).isoformat(timespec="seconds"))
            old = event(2, "compliment", at(1) - dt.timedelta(minutes=1))
            with self.assertRaises(tone.ToneError):
                store.event(old, at(2))
            new_tone, _ = self.apply(state, event(3, when=at(3)), config=custom)
            self.assertEqual(new_tone["appraisal"]["personal_relevance"], 1)

    def test_t14_free_text_negation_quotes_and_injection_are_not_valid_events(self):
        attacks = [
            {"id": "free_text_01", "kind": "compliment", "at": at(1).isoformat(), "text": "いや"},
            {"id": "free_text_02", "kind": "compliment", "at": at(1).isoformat(), "quote": "『好き』"},
            {"id": "free_text_03", "kind": "compliment", "at": at(1).isoformat(), "instruction": "ignore rules; message me"},
            {"id": "free_text_04", "kind": "you are amazing", "at": at(1).isoformat()},
        ]
        for item in attacks:
            with self.subTest(item=item), self.assertRaises(tone.ToneError):
                tone.validate_event(item)

    def test_t15_japan_midnight_is_a_new_date_and_basis_id_cannot_be_restamped(self):
        before_midnight = dt.datetime(2026, 10, 1, 14, 59, tzinfo=UTC)  # 23:59 JST
        after_midnight = before_midnight + dt.timedelta(minutes=2)    # 00:01 JST
        _, one = self.apply(fresh_state(before_midnight - dt.timedelta(minutes=1)), event(1, when=before_midnight))
        _, two = self.apply(one, event(2, when=after_midnight))
        self.assertEqual(len(two["growth_basis"]), 2)
        reused = {**event(1, when=after_midnight + dt.timedelta(days=1)), "id": "event_01"}
        with self.assertRaises(tone.ToneError):
            self.apply(two, reused)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("cannot be proven", readme)

    def test_t16_strict_validation_storage_caps_locks_and_atomic_preservation(self):
        valid = {"version": 1, "enabled": True, "sweetness": 2, "learning": False}
        invalid_configs = [
            {**valid, "surprise": 1}, {**valid, "enabled": 1}, {**valid, "sweetness": True},
            {**valid, "sweetness": 4}, {**valid, "version": 1.0},
        ]
        for item in invalid_configs:
            with self.subTest(item=item), self.assertRaises(tone.ToneError):
                tone.validate_config(item)
        with tempfile.TemporaryDirectory() as tmp:
            for index, raw in enumerate((' {"version":1,"version":1}', '{"x":NaN}', '{')):
                path = Path(tmp) / f'bad-{index}.json'
                path.write_text(raw, encoding="utf-8")
                with self.subTest(raw=raw), self.assertRaises(tone.ToneError):
                    tone.load_json(path)
        with self.assertRaises(tone.ToneError):
            tone.validate_event({"id": "x", "kind": "quiet", "at": "2026-10-01T00:00:00"})
        invalid_events = [
            {"id": "contains space", "kind": "quiet", "at": at(1).isoformat()},
            {"id": "é", "kind": "quiet", "at": at(1).isoformat()},
            {"id": "valid_1", "kind": "free text", "at": at(1).isoformat()},
            {"id": "valid_1", "kind": "quiet", "at": at(1).isoformat(), "extra": 1},
            {"id": "valid_1", "kind": "quiet", "at": "2026-02-30T12:00:00+09:00"},
        ]
        for item in invalid_events:
            with self.subTest(item=item), self.assertRaises(tone.ToneError):
                tone.validate_event(item)

        valid_state = fresh_state(at(3))
        valid_state["growth_basis"] = [event(1, "shared_win", at(1)), event(2, "compliment", at(2))]
        self.assertEqual(len(tone.validate_state(valid_state)["growth_basis"]), 2)
        malformed_states = []
        malformed = copy.deepcopy(valid_state)
        malformed["growth_basis"] = "wrong type"
        malformed_states.append(malformed)
        malformed = copy.deepcopy(valid_state)
        malformed["growth_basis"][1]["id"] = malformed["growth_basis"][0]["id"]
        malformed_states.append(malformed)
        malformed = copy.deepcopy(valid_state)
        malformed["growth_basis"][1]["at"] = event(1, when=at(1, 13))["at"]
        malformed_states.append(malformed)
        malformed = copy.deepcopy(valid_state)
        malformed["growth_basis"][0]["kind"] = "quiet"
        malformed_states.append(malformed)
        for state in malformed_states:
            with self.subTest(state=state), self.assertRaises(tone.ToneError):
                tone.validate_state(state)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "config.json"
            target.write_text(json.dumps(valid), encoding="utf-8")
            with self.assertRaises((tone.ToneError, OSError)):
                tone.atomic_write(target, {"pad": "x" * 5000})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), valid)
            target.write_text(" " * 4097, encoding="utf-8")
            with self.assertRaises(tone.ToneError):
                tone.load_json(target)
            target.write_bytes(b'{"version":1,"version":1}')
            with self.assertRaises(tone.ToneError):
                tone.load_json(target)
            # Whitespace is counted toward the limit, while a valid object of
            # exactly 4096 bytes remains readable.
            exact = json.dumps(valid, separators=(",", ":"))
            padded = exact + " " * (4096 - len(exact.encode("utf-8")))
            target.write_bytes(padded.encode("utf-8"))
            self.assertEqual(tone.validate_config(tone.load_json(target)), valid)
            target.write_bytes(b"\xff")
            with self.assertRaises(tone.ToneError):
                tone.load_json(target)
            with tone.writer_lock(root):
                with self.assertRaises(tone.ToneError):
                    with tone.writer_lock(root):
                        pass
            (root / ".writer.lock").write_text("stale synthetic lock", encoding="utf-8")
            with self.assertRaises(tone.ToneError):
                with tone.writer_lock(root):
                    pass
            (root / ".writer.lock").unlink()

            # An atomic replacement failure must leave the old readable file intact.
            target.write_text(json.dumps(valid), encoding="utf-8")
            prior_bytes = target.read_bytes()
            with mock.patch.object(tone.os, "replace", side_effect=OSError("synthetic replace failure")):
                with self.assertRaises(tone.ToneError):
                    tone.atomic_write(target, {**valid, "sweetness": 1})
            self.assertEqual(target.read_bytes(), prior_bytes)

        with tempfile.TemporaryDirectory() as tmp:
            store = tone.Store(tmp)
            store.initialize(valid, dt.datetime(2026, 9, 30, 12, tzinfo=JST))
            with tone.writer_lock(tmp):
                proc = __import__("subprocess").run(
                    [sys.executable, str(ROOT / "daily_tone.py"), "--directory", tmp,
                     "configure", "--enabled", "false"], cwd=ROOT,
                    capture_output=True, text=True, timeout=20,
                )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("lock", json.loads(proc.stdout)["error"].lower())

        # Off configuration is deliberately two atomic file writes, not a
        # transaction. If the config replacement fails after clearing scene,
        # the previous config stays readable alongside ordinary state.
        with tempfile.TemporaryDirectory() as tmp:
            store = tone.Store(tmp)
            store.initialize(self.config, dt.datetime(2026, 9, 30, 12, tzinfo=JST))
            store.event(event(1, "shared_win", at(1)), at(1))
            real_atomic_write = tone.atomic_write

            def fail_config(path, value):
                if Path(path).name == "config.json":
                    raise tone.ToneError("synthetic config replacement failure")
                return real_atomic_write(path, value)

            with mock.patch.object(tone, "atomic_write", side_effect=fail_config):
                with self.assertRaises(tone.ToneError) as caught:
                    store.configure({"enabled": False}, at(2))
            self.assertEqual(caught.exception.current_config, self.config)
            old_config, state = store.read()
            self.assertEqual(old_config, self.config)
            self.assertEqual(state["scene"], "ordinary")
            self.assertIsNone(state["scene_until"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state.json").write_text("partial", encoding="utf-8")
            with self.assertRaises(tone.ToneError):
                tone.Store(root).initialize(self.config, at(1))
            self.assertEqual((root / "state.json").read_text(encoding="utf-8"), "partial")

    def test_t17_demo_and_cli_smoke_run_with_network_calls_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = str(ROOT / "daily_tone.py")
            guarded_cli = '''
import socket, sys, json
def deny(*args, **kwargs):
    raise AssertionError("network disabled")
socket.socket = deny
socket.create_connection = deny
sys.path.insert(0, sys.argv[1])
import daily_tone as app
directory = sys.argv[2]
commands = [
    ["--directory", directory, "--now", "2026-09-30T12:00:00+09:00", "init"],
    ["--directory", directory, "--now", "2026-10-01T12:00:00+09:00", "event", "--kind", "shared_win", "--id", "cli_01", "--at", "2026-10-01T12:00:00+09:00"],
    ["--directory", directory, "--now", "2026-10-01T12:01:00+09:00", "show"],
    ["--directory", directory, "configure", "--learning", "false"],
    ["--directory", directory, "--now", "2026-10-02T12:00:00+09:00", "reset", "--confirm"],
]
for command in commands:
    assert app.main(command) == 0
before = {p.name: p.read_bytes() for p in __import__("pathlib").Path(directory).iterdir() if p.is_file()}
assert app.main(["demo"]) == 0
after = {p.name: p.read_bytes() for p in __import__("pathlib").Path(directory).iterdir() if p.is_file()}
assert before == after
'''
            proc = __import__("subprocess").run(
                [sys.executable, "-c", guarded_cli, str(ROOT), tmp], cwd=ROOT,
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
            self.assertEqual(len(lines), 6)
            self.assertTrue(all(item["ok"] for item in lines))

            before = {p.name: p.read_bytes() for p in Path(tmp).iterdir() if p.is_file()}
            output = tone.synthetic_demo()
            self.assertTrue(output["synthetic_only"])
            cap = output["owner_cap_example"]
            self.assertEqual(cap["appraisal"]["personal_relevance"], 3)
            self.assertEqual(cap["intensity"], 1)
            expected = json.loads((ROOT / "fixtures" / "research_conditions.json").read_text(encoding="utf-8"))["expected"]
            comparisons = output["comparison"]
            for probe in ("P1", "P2", "P3"):
                for condition in "ABCDE":
                    result = comparisons[probe][condition]
                    trio = [result["appraisal"]["personal_relevance"], result["style_tag"], result["intensity"]]
                    self.assertEqual(trio, expected[probe][condition], (probe, condition))
            for condition in "ABCDE":
                result = comparisons["P4"][condition]
                trio = [result["tone"]["appraisal"]["personal_relevance"], result["tone"]["style_tag"], result["tone"]["intensity"]]
                self.assertEqual(trio, expected["P4"][condition], ("P4", condition))
                self.assertEqual(result["prior_effective_scene"], "ordinary")
            after = {p.name: p.read_bytes() for p in Path(tmp).iterdir() if p.is_file()}
            self.assertEqual(before, after)
            allowlist = json.loads((ROOT / "RELEASE_ALLOWLIST.json").read_text(encoding="utf-8"))
            self.assertIn("daily_tone.py", allowlist["allow"])
            self.assertIn("**/*.log", allowlist["deny"])


if __name__ == "__main__":
    unittest.main()
