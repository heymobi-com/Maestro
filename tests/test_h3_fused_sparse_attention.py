"""An INT8 ConvRot label alone must not take sparse attention away.

The fused H3 bundle names itself INT8 ConvRot, but its baked kernels normalise
to BF16 (``shared.qtypes.int8_convrot``), so SLA ran on it: 46 s of denoising
for 124 frames at 1280x704 against 8 m 37 s once the FP32 guard claimed it.
A recipe that asks for SLA by default already knows which dtype it carries.
"""
# ruff: noqa: E402 -- app imports follow the repository path bootstrap.
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

ENGINE = (APP / "wgp.py").read_text(encoding="utf-8")
LAUNCH = (APP / "launch.py").read_text(encoding="utf-8")


def test_the_fused_recipe_asks_for_sparse_attention_by_default():
    data = json.loads(
        (APP / "defaults" / "minimax_h3_ref2va_fused_turbo.json").read_text()
    )

    assert data["model"]["minimax_h3_fused_turbo"] is True
    assert data["model"]["sla_attention_default"] is True
    assert data["override_attention"] == "sla", (
        "the recipe's own default request is why sparse attention must survive"
    )


def test_the_generation_guard_exempts_the_fused_checkpoint():
    assert "minimax_h3_fused_turbo" in ENGINE
    assert "_fused_keeps_sparse" in ENGINE
    assert (
        'if _quant_label == "INT8 ConvRot" and not _fused_keeps_sparse:' in ENGINE
    ), "the FP32 refusal must skip recipes that keep BF16 activations"


def test_the_sidebar_keeps_sparse_engines_listed_for_the_fused_checkpoint():
    assert "_fused_keeps_sparse" in LAUNCH
    assert '"" if _fused_keeps_sparse' in LAUNCH, (
        "the model options must not mark a fused checkpoint as dense-only"
    )


def test_the_int8_convrot_kernel_normalises_to_bfloat16():
    kernel = (APP / "shared" / "qtypes" / "int8_convrot.py").read_text(
        encoding="utf-8"
    )

    assert "return torch.bfloat16" in kernel, (
        "BF16 activations are what make sparse attention legal on INT8 ConvRot"
    )

