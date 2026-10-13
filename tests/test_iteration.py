"""Self-iteration proposals stay suggestions until a user names an id."""

import json
import tempfile
import unittest
from pathlib import Path

from tcm_core.iteration import (
    apply_lessons,
    build_proposals,
    channel_rows,
    open_proposals,
    repro_flags,
    review_stores,
)


def _calls(host, ok_flags, ms=100):
    return [{"type": "db_call", "host": host, "ok": ok, "ms": ms}
            for ok in ok_flags]


class IterationTests(unittest.TestCase):
    def test_dead_end_needs_five_calls_and_low_success(self):
        rows = channel_rows(_calls("down.example", [False] * 4 + [True]))
        proposals = build_proposals(rows, [], [], [])
        self.assertEqual(proposals[0]["id"], "telemetry:dead_end:down.example")
        self.assertEqual(proposals[0]["strength"], "strong")
        self.assertTrue(proposals[0]["requires_confirmation"])
        self.assertEqual(proposals[0]["effect"], "append_lesson")

        short = channel_rows(_calls("down.example", [False] * 4))
        self.assertEqual(build_proposals(short, [], [], []), [])

    def test_single_hit_insight_is_only_a_watch(self):
        weak = build_proposals([], [{
            "id": "INS-1", "category": "dead_end", "hits": 1,
            "title": "一次超时", "detail": "只见到一次", "evidence": "502",
        }], [], [])
        strong = build_proposals([], [{
            "id": "INS-1", "category": "dead_end", "hits": 2,
            "title": "一次超时", "detail": "又见到一次", "evidence": "502",
        }], [], [])
        self.assertEqual(weak[0]["strength"], "watch")
        self.assertEqual(strong[0]["strength"], "strong")

    def test_same_seed_spread_above_point_one_is_flagged(self):
        quiet = repro_flags([
            {"type": "vina_docking", "ligand": "a", "receptor": "b",
             "seed": 42, "best_affinity_kcal_mol": -8.0},
            {"type": "vina_docking", "ligand": "a", "receptor": "b",
             "seed": 42, "best_affinity_kcal_mol": -8.05},
        ])
        noisy = repro_flags([
            {"type": "vina_docking", "ligand": "a", "receptor": "b",
             "seed": 42, "best_affinity_kcal_mol": -8.0},
            {"type": "vina_docking", "ligand": "a", "receptor": "b",
             "seed": 42, "best_affinity_kcal_mol": -8.2},
        ])
        self.assertEqual(quiet, [])
        self.assertEqual(len(noisy), 1)
        proposals = build_proposals([], [], [], noisy)
        self.assertIn("0.2", proposals[0]["evidence"])

    def test_apply_refuses_empty_confirmation_and_repeats(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SKILL.md").write_text("---\nname: t\n---\n", encoding="utf-8")
            state = root / "state"
            state.mkdir()
            proposals = build_proposals(
                channel_rows(_calls("down.example", [False] * 5)), [], [], [])
            refused = apply_lessons(root, state, proposals, [])
            self.assertEqual(refused["action"], "refused")
            self.assertFalse((root / "references" / "runtime_lessons.md").exists())

            pid = proposals[0]["id"]
            first = apply_lessons(root, state, proposals, [pid], "2026-10-13")
            self.assertEqual(first["written"], [pid])
            text = (root / "references" / "runtime_lessons.md").read_text(encoding="utf-8")
            self.assertIn(pid, text)
            self.assertIn("不改变工具行为", text)
            self.assertNotIn("OB/DL 门槛改为", text)

            second = apply_lessons(root, state, proposals, [pid], "2026-10-14")
            self.assertEqual(second["written"], [])
            self.assertEqual(second["skipped"], [pid])
            again = (root / "references" / "runtime_lessons.md").read_text(encoding="utf-8")
            self.assertEqual(again.count(f"## {pid}"), 1)

    def test_unknown_id_does_not_create_a_lesson(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SKILL.md").write_text("skill", encoding="utf-8")
            result = apply_lessons(root, root / "state", [], ["no-such-id"])
            self.assertEqual(result["unknown"], ["no-such-id"])
            self.assertFalse((root / "references").exists())

    def test_reject_hides_a_proposal_from_the_next_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            tcm = Path(tmp) / "tcm"
            dock = Path(tmp) / "dock"
            tcm.mkdir()
            dock.mkdir()
            events = _calls("down.example", [False] * 5)
            (tcm / "call_log.jsonl").write_text(
                "\n".join(json.dumps(ev) for ev in events) + "\n",
                encoding="utf-8")
            packet = review_stores(tcm, dock)
            pid = packet["open"][0]["id"]
            from tcm_core.iteration import reject_proposals
            reject_proposals(tcm, packet["open"], [pid], "2026-10-13")
            again = review_stores(tcm, dock)
            self.assertEqual(again["open"], [])
            self.assertEqual(again["rejected_count"], 1)

    def test_missing_skill_root_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                apply_lessons(Path(tmp), Path(tmp), [{
                    "id": "x", "kind": "insight", "title": "t",
                    "detail": "d", "evidence": "e",
                }], ["x"])

    def test_closed_ids_drop_out_of_open_list(self):
        proposals = [{"id": "a"}, {"id": "b"}]
        opened = open_proposals(proposals, {"applied": {"a": {}}, "rejected": {}})
        self.assertEqual([item["id"] for item in opened], ["b"])


if __name__ == "__main__":
    unittest.main()
