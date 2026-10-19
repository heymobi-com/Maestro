"""A fine-tuned H3 checkpoint keeps its 8-step LoRA.

Reported from use: "algunos modelos H3 no aceptan el turbo". Five attempts with a CivitAI H3
fine-tune and the Alibaba 8-step Acc LoRA all ended at

    MiniMax H3 ref2va checkpoint AdaLN table is incompatible with the canonical LoRA map
    (relative error 0.000372)

The conversion already fits its map from whichever AdaLN table the checkpoint carries -- that is
the correct basis for that checkpoint -- so the residual only says the fine-tune is not the
canonical table, not that the map is wrong. At the previous 1e-5 ceiling a 3.7e-4 drift refused
the conversion outright, and there is no other way to use a width-2688 adapter on a pruned model.

Measured on the operator's checkpoint (`ckpts/h3ErosMax_beta5_3185154.safetensors`): table
1025x8, fit rank 9, relative error 3.72e-4, worst element 9.88e-4.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest

_APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

try:
    import torch
    from models.minimax_h3 import lora_affine
    from safetensors import safe_open
except Exception:  # pragma: no cover - torch or safetensors is absent
    torch = None
    lora_affine = None

_MAP_PATH = os.path.join(
    _APP_DIR, "ckpts", "minimax_h3", "lora_affine_maps", "ref2va_rank8.sft",
)
_FINETUNE_PATH = os.path.join(_APP_DIR, "ckpts", "h3ErosMax_beta5_3185154.safetensors")


def _canonical_table():
    with safe_open(_MAP_PATH, framework="pt") as handle:
        return handle.get_tensor("adaln_t_table").float()


@unittest.skipUnless(
    torch is not None and lora_affine is not None and os.path.exists(_MAP_PATH),
    "the AdaLN fit package is not installed here",
)
class AdaLnToleranceTests(unittest.TestCase):
    def setUp(self):
        # The locator searches its configured roots, which default to "ckpts" relative to the
        # process working directory; tests run from the repository root.
        from shared.utils import files_locator

        self._previous_paths = list(files_locator._checkpoints_paths)
        files_locator.set_checkpoints_paths([os.path.join(_APP_DIR, "ckpts")])

    def tearDown(self):
        from shared.utils import files_locator

        files_locator.set_checkpoints_paths(self._previous_paths)

    def _convert(self, table):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            affine = lora_affine._aligned_affine_map("ref2va", table)
        return affine, out.getvalue()

    def test_the_canonical_table_maps_without_comment(self):
        affine, printed = self._convert(_canonical_table())

        self.assertEqual(tuple(affine.shape), (lora_affine._PRUNED_WIDTHS[1] + 1, lora_affine.FULL_TIME_DIM))
        self.assertEqual(printed, "")

    def test_a_finetuned_table_converts_and_says_what_it_saw(self):
        """The reported case: same shape, 3.7e-4 of drift, adapters still converted.

        The drift has to be non-linear, which is what a fine-tune produces: a plain scaling of
        the canonical table is itself a linear map and fits exactly, so it would not exercise
        the tolerance at all.
        """

        canonical = _canonical_table()
        rows = torch.arange(canonical.shape[0], dtype=torch.float32).unsqueeze(1)
        drifted = canonical * (1.0 + 3.72e-4 * torch.sin(rows / 37.0))
        affine, printed = self._convert(drifted)

        self.assertEqual(affine.shape[1], lora_affine.FULL_TIME_DIM)
        self.assertIn("differs from the", printed)
        self.assertIn("converting the adapters", printed)

    def test_a_table_that_cannot_become_the_basis_is_still_refused(self):
        """Guard intact: a table whose columns cannot express the basis is not converted."""

        table = _canonical_table()
        degenerate = table.clone()
        degenerate[:, 3] = degenerate[:, 2]

        with self.assertRaises(ValueError) as caught:
            self._convert(degenerate)
        self.assertIn("AdaLN table", str(caught.exception))

    @unittest.skipUnless(
        os.path.exists(_FINETUNE_PATH),
        "the reported fine-tuned checkpoint is not installed here",
    )
    def test_the_reported_checkpoint_now_accepts_its_lora(self):
        """The exact table that refused five real jobs."""

        with safe_open(_FINETUNE_PATH, framework="pt") as handle:
            table = handle.get_tensor("adaln_t_table").float()

        affine, printed = self._convert(table)

        self.assertEqual(affine.shape[1], lora_affine.FULL_TIME_DIM)
        self.assertIn("relative error", printed)


if __name__ == "__main__":
    unittest.main()
