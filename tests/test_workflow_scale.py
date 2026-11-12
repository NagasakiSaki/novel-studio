import copy
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from novel_studio.core import Core, CoreError
from novel_studio.workflow_scale import derive, validate_scale, chapter_id
from novel_studio.workflow_store import DEFAULT_SCALE
from novel_studio.lengths import DEFAULT_GOAL
from test_workflow_planning import fixture


class ScaleTests(unittest.TestCase):
    def test_conflicting_inputs_stay_unchanged_and_show_calculation(self):
        scale = {**DEFAULT_SCALE, "chapter_target": 5000, "chapter_minimum": 4000, "chapter_maximum": 6000,
            "total_words": 100000, "total_chapters": 20, "total_volumes": 10}
        before = copy.deepcopy(scale)
        result = derive(scale)
        self.assertEqual(scale, before)
        self.assertEqual(result["estimated_volumes"], 1)
        self.assertTrue(result["warnings"])
        self.assertIn("20", result["warnings"][0])

    def test_last_volume_may_be_short(self):
        result = derive({**DEFAULT_SCALE, "total_chapters": 43, "total_volumes": 3})
        self.assertEqual(result["last_volume_chapters"], 3)
        self.assertEqual(result["estimated_volumes"], 3)
        self.assertFalse(result["warnings"])

    def test_words_and_volumes_can_supply_estimates_without_chapter_input(self):
        a = derive({**DEFAULT_SCALE, "total_chapters": None, "total_words": 9000})
        b = derive({**DEFAULT_SCALE, "total_chapters": None, "total_volumes": 2})
        self.assertEqual(a["estimated_chapters"], 3)
        self.assertEqual(b["estimated_chapters"], 40)

    def test_invalid_ranges_booleans_and_unbounded_values_are_rejected(self):
        for change in ({"chapter_minimum": 4000}, {"total_chapters": True}, {"total_chapters": 1001}, {"chapters_per_volume": 0}):
            with self.subTest(change=change), self.assertRaises(CoreError):
                validate_scale({**DEFAULT_SCALE, **change})

    def test_existing_length_plan_and_per_chapter_override_are_reused(self):
        with tempfile.TemporaryDirectory(prefix="novel-scale-synthetic-") as folder:
            core = Core.create(Path(folder), "合成篇幅", ROOT)
            core.workflow.scale.save({**DEFAULT_SCALE, "total_chapters": 2})
            row = core.workflow.planning.save(fixture())
            preview = core.workflow.planning.preview(row["version_id"])
            core.author_commit(preview["handle"], True)
            core.workflow.scale.sync()
            cid = chapter_id(core.project_id, 1, 1)
            self.assertEqual(core.workflow.scale.goal(cid)["target"], 3000)
            core.lengths.goal(cid, {**DEFAULT_GOAL, "minimum": 80, "target": 100, "maximum": 120})
            core.workflow.scale.save({**DEFAULT_SCALE, "total_chapters": 2, "chapter_target": 3200})
            self.assertEqual(core.workflow.scale.goal(cid)["target"], 100)
            self.assertEqual(core.workflow.scale.goal(chapter_id(core.project_id, 1, 2))["target"], 3200)
            self.assertEqual(len([n for n in core.lengths.plan()["nodes"].values() if n["kind"] == "chapter"]), 2)


if __name__ == "__main__":
    unittest.main()
