"""The project brief's story arc must not be performed inside every clip.

Measured on a 27B-planned 14-clip project (``_director_pipeline_a53d2f8b``): every
rendered clip began with both hosts entering the studio and the key lights coming
up, because the 2,159-character brief described the film's whole progression and
was injected into all fourteen prompts:

    The set begins dark and dormant ... A low hum rises and the key lights bloom
    up in stages ... The two participants enter in sequence and settle into their
    chairs ... At the peak ... the hologram begins to contract ...

The block already carried "do not perform its progression or its closing fade
inside this shot". The model performed it anyway — frame checks show the hosts
standing and arriving at 1.0 s of clip 2, whose own action says "seated at the
curved desk ... continues speaking". This is the visual twin of the brief prose
being read aloud, and the fix is the same: remove it, do not forbid it.

A short brief is a setting description and keeps passing through, so
``test_audio_mode_unchanged`` still holds for it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from services.director.h3_dialogue import (  # noqa: E402
    _h3_injected_project_context,
    _world_only_project_context,
    compile_h3_clip_plans,
)

# The shape that broke the run, with its own words.
ARC_BRIEF = (
    "DESCRIPTION: A two-host tech podcast set awakens, conducts its conversation, "
    "and closes, as a single continuous process rather than isolated moments. "
    "The set begins dark and dormant: only the cool blue LED strip along the desk "
    "edge and the faint glow of the holographic display bed are lit, while the "
    "studio sits empty. "
    "A low hum rises and the key lights bloom up in stages, revealing the curved "
    "desk, the two facing chairs, and the floating data-wall behind them as the "
    "set sprouts into working order. "
    "The two participants enter in sequence and settle into their chairs, the boom "
    "mic dipping into frame, the camera dolly easing forward from a wide "
    "establishing shot to a tighter two-shot. "
    "The conversation then unfolds as the central process: as the first host poses "
    "the question, the data-wall behind them stirs, particles of light gathering "
    "into a dormant holographic sphere. "
    "At the peak, when the key insight lands, the hologram reaches full bloom, the "
    "cool blue set light shifts to a warm amber wash. "
    "The hosts then exchange a nod, the hologram begins to contract and fold back "
    "into itself, and the data-wall dims as the segment ends. "
    "The full arc, dormant, awakening, blooming, contracting, resting, mirrors the "
    "conversation's own life cycle."
)

STATIC_BRIEF = (
    "A modern, minimalist podcast studio: a curved black desk with a cool blue LED "
    "strip along its edge, two facing chairs, a floating data-wall behind them, and "
    "a boom mic above the desk. Lighting is cool blue, with a soft key on each host "
    "and a practical lamp at the back shelf. The hosts are a woman in a floral dress "
    "and a man in a dark suit, always seated facing each other across the desk. "
    "Floor-to-ceiling glass on the left wall reflects the data-wall, and a plant "
    "sits in each far corner of the room. The floor is polished concrete with a "
    "charcoal rug under the desk. Wall panels are dark grey acoustic felt, and a "
    "red neon sign reading ON AIR hangs on the back wall. The camera is a single "
    "locked-off wide lens at desk height, and the palette stays cold."
)


def _plan(prompt: str, context: str) -> dict:
    return {
        "video_prompt": prompt,
        "_director_h3_source_prompt": prompt,
        "_director_h3_model_family": "ref2va",
        "_director_h3_prompt_mode": "ref2va",
        "_director_duration_sec": 6,
        "_director_project_context": context,
        "_director_subjects_on_screen": [
            {"visual_description": "the woman from the reference image (S1)"},
            {"visual_description": "the man from the reference image (S2)"},
        ],
        "_director_dialogue_beats": [
            {"speaker_id": "(S1)", "spoken_text": "Hola a todos."},
        ],
        "_director_audio_plan": {"mode": "dialogue_driven", "lip_sync_critical": True},
        "_director_audio_policy": {
            "generated_speech_allowed": True,
            "generated_music_allowed": True,
        },
        "_director_dialogue_manifest_authoritative": True,
        "_director_h3_reference_manifest": [
            {"type": "image", "role": "Bela (S1)", "image_intent": "identity"},
            {"type": "image", "role": "Roman (S2)", "image_intent": "identity"},
        ],
    }


class ArcBriefTests(unittest.TestCase):
    def test_the_arc_is_dropped_rather_than_injected(self):
        self.assertEqual(_h3_injected_project_context(ARC_BRIEF), "")

    def test_a_short_brief_passes_through_untouched(self):
        brief = "A dark studio with a curved desk and cool blue light."

        self.assertEqual(_h3_injected_project_context(brief), brief)

    def test_a_long_static_brief_keeps_its_world_whole(self):
        """A look is a contract, so it is never cut to fit: an earlier 400-character
        budget deleted the voice rules and the ambience that follow the cast."""

        world = _h3_injected_project_context(STATIC_BRIEF)

        self.assertIn("curved black desk", world)
        self.assertIn("cool blue LED strip", world)
        self.assertIn("palette stays cold", world)

    def test_a_labelled_section_is_never_trimmed(self):
        brief = (
            "CHARACTERS AND BINDINGS:\n"
            "<Subject 1> (S1): The woman in the floral dress; identity from <Picture 1>.\n\n"
            "VOICES:\n"
            "S1: a warm alto with long intimate notes and audible breath.\n\n"
            "AMBIENCE:\n"
            "A cavernous studio with a glossy, mirror-like floor and a solitary desk."
        )

        world = _h3_injected_project_context(brief)

        self.assertIn("warm alto", world)
        self.assertIn("glossy, mirror-like floor", world)

    def test_unheaded_prose_keeps_only_its_static_sentences(self):
        mixed = (
            "The walls are dark grey acoustic panels and the carpet is charcoal. "
            "The set begins dark and dormant while the studio sits empty. "
            "A low hum rises and the key lights bloom up in stages. "
            "The two participants enter in sequence and settle into their chairs. "
            "A red neon sign reading ON AIR hangs behind the desk."
        )

        world = _h3_injected_project_context(mixed)

        self.assertIn("acoustic panels", world)
        self.assertIn("neon sign reading ON AIR", world)
        self.assertNotIn("enter in sequence", world)
        self.assertNotIn("bloom up in stages", world)

    def test_one_progression_verb_is_not_a_story(self):
        """A look that happens to use a verb the arc regex knows must survive."""

        brief = "A dark studio where warm light rises along the edge of the curved desk."

        self.assertEqual(_world_only_project_context(brief), brief)


class CompiledPromptTests(unittest.TestCase):
    """The gate that matters: what H3 actually receives."""

    ACTION = (
        "[Shot 1] A medium two-shot of the woman in the floral dress and the man in "
        "the dark suit, seated at the curved desk. The woman continues speaking, "
        "gesturing toward the data-wall. The man leans forward, interested."
    )

    def test_the_entrance_does_not_reach_the_shot(self):
        plan = _plan(self.ACTION, ARC_BRIEF)

        compile_h3_clip_plans([plan])
        compiled = plan["video_prompt"]

        for phrase in (
            "enter in sequence",
            "key lights bloom",
            "settle into their chairs",
            "begins to contract",
            "full arc",
        ):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, compiled)
        # The shot's own direction is untouched.
        self.assertIn("seated at the curved desk", compiled)
        self.assertIn("gesturing toward the data-wall", compiled)

    def test_a_short_setting_brief_is_still_carried(self):
        brief = "A dark studio with a curved desk and cool blue light."
        plan = _plan(self.ACTION, brief)

        compile_h3_clip_plans([plan])

        self.assertIn("Project context (the whole film", plan["video_prompt"])
        self.assertIn("cool blue light", plan["video_prompt"])


if __name__ == "__main__":
    unittest.main()
