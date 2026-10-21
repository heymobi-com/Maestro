"""A whitespace collapse must not delete the Context-IR field boundaries."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from models.minimax_h3.speakers import (  # noqa: E402
    _DIALOGUE_TAG_RE,
    resolve_ref2va_dialogue_owners,
)
from services.director.h3_dialogue import compile_h3_official_prompt  # noqa: E402
from services.director.h3_field_layout import restore_h3_field_layout  # noqa: E402

SUBJECTS = [
    {
        "visual_description": "Valeria (woman in navy blazer, white top)",
        "position_or_relation": "foreground left",
    },
    {
        "visual_description": "Ricardo (man in grey crewneck sweater, glasses)",
        "position_or_relation": "foreground right",
    },
]
REFERENCES = [
    {"type": "image", "role": "Valeria (S1)", "image_intent": "identity"},
    {"type": "audio", "role": "Valeria (S1)", "audio_intent": "voice"},
    {"type": "image", "role": "Ricardo (S2)", "image_intent": "identity"},
    {"type": "audio", "role": "Ricardo (S2)", "audio_intent": "voice"},
]
BEATS = [
    {"speaker_id": "(S2)", "spoken_text": "Entonces la torre habrá ganado."},
    {"speaker_id": "(S1)", "spoken_text": "Aún así quiero intentarlo."},
]


def compiled_prompt() -> str:
    prompt, _ = compile_h3_official_prompt(
        "Valeria and Ricardo stand on a rooftop terrace at night.",
        SUBJECTS,
        BEATS,
        mode="ref2va",
        references=REFERENCES,
        default_language="Spanish",
    )
    return prompt


def flattened(prompt: str) -> str:
    # Exactly what a shot repair does before saving the prompt.
    return re.sub(r"\s+", " ", prompt).strip()


class TestH3FieldLayout(unittest.TestCase):
    def test_a_flattened_prompt_regains_every_field_boundary(self):
        restored = restore_h3_field_layout(flattened(compiled_prompt()))

        for field in (
            "subject_definitions:",
            "summary:",
            "retention_analysis:",
            "detailed_description:",
            "overall_soundscape:",
            "non_diegetic_music:",
        ):
            self.assertRegex(restored, rf"(?m)^\s*{re.escape(field)}")

    def test_the_renderer_can_resolve_speakers_after_the_repair(self):
        """Measured on a real run: every clip failed before the model loaded.

        The shot repair collapsed the prompt onto one line, so the line-anchored
        ``subject_definitions`` head was invisible, the cast declared no
        ``<Subject N> (Sx)`` pair, and each line was refused with "could not
        determine which referenced character speaks".
        """

        prompt = restore_h3_field_layout(flattened(compiled_prompt()))
        spans = [
            (match.start(1), match.end(1), match.group(1).strip())
            for match in _DIALOGUE_TAG_RE.finditer(prompt)
        ]

        self.assertEqual(
            resolve_ref2va_dialogue_owners(
                prompt, spans, {"valeria": 1, "ricardo": 2}, {1, 2},
            ),
            [(2, ""), (1, "")],
        )

    def test_an_already_separated_prompt_is_returned_unchanged(self):
        prompt = compiled_prompt()

        self.assertEqual(restore_h3_field_layout(prompt), prompt)

    def test_ordinary_prose_is_not_split(self):
        text = "A summary of the night: they watch the tower and say nothing."

        self.assertEqual(restore_h3_field_layout(text), text)


if __name__ == "__main__":
    unittest.main()
