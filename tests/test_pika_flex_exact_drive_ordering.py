"""The Omni exact-drive soundtrack must reach Pika Flex already marked AD.

A ``drive`` reference is routed to H3's frozen target-audio path by
``split_exact_drive_audio_reference``, which sets ``audio_guide`` and
``audio_prompt_type = "AD"`` on the generation body. Pika Flex rejects an
``audio_guide`` without exact-drive AD, so that routing has to happen before
``validate_pika_flex_single_pass_request`` runs. These tests pin the ordering
that the generate endpoint relies on.
"""
# ruff: noqa: E402 -- app imports follow the repository path bootstrap.
import ast
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from models.minimax_h3.minimax_h3_handler import (
    validate_pika_flex_single_pass_request,
)
from models.minimax_h3.reference_manifest import (
    split_exact_drive_audio_reference,
)


def pika_reference_def():
    data = json.loads(
        (ROOT / "app/defaults/minimax_h3_ref2va_pika_flex.json").read_text()
    )
    return data["model"]


def drive_body():
    return {
        "video_length": 124,
        "audio_guide": "song.wav",
        "audio_prompt_type": "AD",
        "minimax_h3_exact_drive_audio_ordinal": 1,
    }


def test_exact_drive_reference_splits_the_soundtrack_out_of_the_manifest():
    manifest = [
        {"type": "image", "path": "anita.png"},
        {"type": "audio", "path": "song.wav", "audio_intent": "drive"},
    ]

    runtime, drive_path, drive_ordinal = split_exact_drive_audio_reference(
        manifest
    )

    assert drive_path == "song.wav"
    assert drive_ordinal == 1
    assert [item["type"] for item in runtime] == ["image"]


def test_pika_flex_accepts_the_body_the_omni_split_produces():
    body = drive_body()

    validate_pika_flex_single_pass_request(body, pika_reference_def())


def test_pika_flex_still_rejects_an_audio_guide_without_exact_drive_ad():
    body = dict(drive_body())
    body["audio_prompt_type"] = ""

    with pytest.raises(ValueError, match="exact-drive AD"):
        validate_pika_flex_single_pass_request(body, pika_reference_def())


def test_generate_endpoint_routes_the_drive_soundtrack_before_pika_validation():
    source = (ROOT / "app/launch.py").read_text()
    tree = ast.parse(source)

    split_lines = []
    validate_lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if name.endswith("validate_pika_flex_single_pass_request"):
                validate_lines.append(node.lineno)
        # The Omni split is passed to asyncio.to_thread, so it appears as a
        # bare Name; direct calls appear as Calls. Match either form.
        target = node.func if isinstance(node, ast.Call) else node
        if isinstance(target, ast.Name) and target.id == (
            "split_exact_drive_audio_reference"
        ):
            split_lines.append(node.lineno)

    assert split_lines, "the Omni split call is missing"
    assert validate_lines, "the Pika Flex validation is missing"
    # The per-clip multi-clip path splits the manifest again much later in the
    # file; only the first occurrence of each call decides the request order.
    split_line = min(split_lines)
    validate_line = min(validate_lines)
    assert split_line < validate_line, (
        "the Omni drive soundtrack must be routed before Pika Flex validates "
        f"the request (split at {split_line}, validation at {validate_line})"
    )
