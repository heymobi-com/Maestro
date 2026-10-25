"""Which checkpoints of a model family are spare, and which one must stay."""

from __future__ import annotations

import os
import sys
import unittest

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from services.model_variants import build_family_plan, total_reclaimable  # noqa: E402

GB = 1024 ** 3


def _model(filename, *, in_use=False, group="minimax_h3", name=None, modified=1.0, size=19.5):
    return {
        "model_type": filename.replace(".safetensors", ""),
        "model_name": name or filename,
        "group": group,
        "group_label": "MiniMax H3" if group == "minimax_h3" else "Qwen Image 2.1 7B",
        "filename": filename,
        "size_bytes": int(size * GB),
        "path": f"C:/ckpts/{filename}",
        "in_use": in_use,
        "modified": modified,
    }


class FamilyPlanTests(unittest.TestCase):
    def test_the_in_use_checkpoint_is_kept_and_the_rest_are_spare(self):
        groups = build_family_plan([
            _model("h3_pruned_int8.safetensors", in_use=True),
            _model("h3_singularity_int8.safetensors", name="Singularity"),
            _model("h3_eros_int8.safetensors", name="ErosMax"),
        ])

        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group["label"], "MiniMax H3")
        self.assertEqual(group["keep"]["filename"], "h3_pruned_int8.safetensors")
        spare = [v["filename"] for v in group["variants"] if v["removable"]]
        self.assertEqual(
            sorted(spare),
            ["h3_eros_int8.safetensors", "h3_singularity_int8.safetensors"],
        )
        self.assertEqual(group["reclaimable_bytes"], int(39 * GB))
        owners = [owner for variant in group["variants"] for owner in variant["models"]]
        self.assertIn("Singularity", owners)
        self.assertIn("ErosMax", owners)

    def test_a_family_with_one_checkpoint_is_not_offered(self):
        groups = build_family_plan([_model("only.safetensors", in_use=True)])

        self.assertEqual(groups, [])
        self.assertEqual(total_reclaimable(groups), 0)

    def test_without_a_selected_model_the_newest_file_is_kept(self):
        groups = build_family_plan([
            _model("old.safetensors", modified=1.0),
            _model("new.safetensors", modified=99.0),
        ])

        self.assertEqual(groups[0]["keep"]["filename"], "new.safetensors")
        spare = [v["filename"] for v in groups[0]["variants"] if v["removable"]]
        self.assertEqual(spare, ["old.safetensors"])

    def test_two_entries_sharing_one_file_count_it_once(self):
        groups = build_family_plan([
            _model("shared_int8.safetensors", in_use=True),
            _model("shared_int8.safetensors", in_use=True, name="Same file, other entry"),
            _model("spare_int8.safetensors"),
        ])

        self.assertEqual(len(groups[0]["variants"]), 2)
        self.assertEqual(groups[0]["reclaimable_bytes"], int(19.5 * GB))
        self.assertIn("Same file, other entry", groups[0]["keep"]["models"])

    def test_families_are_separate_and_ordered_by_savings(self):
        groups = build_family_plan([
            _model("h3_a.safetensors", in_use=True),
            _model("h3_b.safetensors"),
            _model("qwen_a.safetensors", in_use=True, group="qwen_image_21_7B", size=7),
            _model("qwen_b.safetensors", group="qwen_image_21_7B", size=15),
        ])

        self.assertEqual(
            [group["group"] for group in groups],
            ["minimax_h3", "qwen_image_21_7B"],
        )
        self.assertEqual(total_reclaimable(groups), int(19.5 * GB) + 15 * GB)


if __name__ == "__main__":
    unittest.main()
