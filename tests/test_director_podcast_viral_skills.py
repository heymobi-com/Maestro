"""The Video Podcast and Viral Video skills are selectable and reach their planner.

Both planners have existed since the planner layer was written, but the Director
sidebar offered them as disabled cards marked "Coming Soon" whose id pointed at the
music video, and the capability table the model picker reads had no entry for them.
Choosing one was therefore impossible, and enabling the card as it stood would have
planned a music video without saying so: ``skill_map.get(pipeline_type,
"music_video")`` falls back silently.

These tests pin the whole path: the card, the plan request, the generation request,
the pipeline routing and the model requirements.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.orchestrator import _PLANNER_MAP  # noqa: E402
from services.director_model_compat import (  # noqa: E402
    DIRECTOR_PIPELINE_TYPES,
    assess_director_model,
)


ROOT = Path(__file__).resolve().parents[1]
SKILL_LABELS = ("Video Podcast", "Viral Video")


def _chat_source() -> str:
    return (
        ROOT / "ui/src/components/Sidebar/DirectorChat.tsx"
    ).read_text(encoding="utf-8")


def _store_source() -> str:
    return (ROOT / "ui/src/stores/useStore.ts").read_text(encoding="utf-8")


def _skill_card_line(label: str) -> str:
    for line in _chat_source().splitlines():
        if f"label: '{label}'" in line:
            return line
    raise AssertionError(f"the Director skill selector offers no {label} card")


class SkillSelectorTests(unittest.TestCase):
    def test_every_skill_card_is_selectable(self):
        for label in SKILL_LABELS:
            with self.subTest(label=label):
                self.assertIn("active: true", _skill_card_line(label))

    def test_the_cards_name_their_own_skill_instead_of_the_music_video(self):
        self.assertIn("id: 'podcast' as DirectorSkill", _skill_card_line("Video Podcast"))
        self.assertIn("id: 'viral_video' as DirectorSkill", _skill_card_line("Viral Video"))

    def test_no_card_is_left_as_a_coming_soon_placeholder(self):
        chat = _chat_source()
        self.assertNotIn("desc: 'Coming Soon'", chat)

    def test_the_chat_names_the_running_skill(self):
        chat = _chat_source()
        # The header and the skill chip used to say "Music Video" for every skill that
        # was not a short film, which a podcast would have inherited.
        self.assertIn("const skillLabel = skill === 'podcast' ? 'Video Podcast'", chat)
        self.assertGreaterEqual(chat.count("<SkillGlyph skill={skill}"), 2)


class RequestRoutingTests(unittest.TestCase):
    def test_the_plan_request_sends_the_selected_skill(self):
        store = _store_source()
        self.assertIn(
            "const skillType = directorSkill === 'podcast' || directorSkill === 'viral_video'",
            store,
        )
        self.assertIn("skill_type: skillType,", store)

    def test_the_viral_plan_request_carries_its_concept_and_style(self):
        store = _store_source()
        self.assertIn("concept: directorSceneDescription,", store)
        self.assertIn("platform: 'general',", store)
        self.assertIn("style: 'cinematic',", store)

    def test_the_generation_request_sends_the_selected_pipeline_type(self):
        store = _store_source()
        self.assertIn(
            "if (state.directorSkill === 'podcast') pipelineType = 'podcast'",
            store,
        )
        self.assertIn(
            "else if (state.directorSkill === 'viral_video') pipelineType = 'viral_video'",
            store,
        )

    def test_the_model_picker_asks_about_the_running_skill(self):
        chat = _chat_source()
        self.assertIn(": directorSkill === 'podcast'", chat)
        self.assertIn(": directorSkill === 'viral_video'", chat)


class PlannerRoutingTests(unittest.TestCase):
    def test_podcast_and_viral_have_their_own_planners(self):
        for skill in ("podcast", "viral_video"):
            with self.subTest(skill=skill):
                self.assertEqual(_PLANNER_MAP[skill].skill_type, skill)

    def test_the_capability_table_lists_every_skill_of_the_type_union(self):
        for skill in ("podcast", "viral_video"):
            self.assertIn(skill, DIRECTOR_PIPELINE_TYPES)
        # The model picker reads one entry per workflow; a missing key is what made
        # the two skills unselectable, so the empty-model fallback must carry them too.
        unavailable = assess_director_model("some_model", None)["video"]
        for skill in DIRECTOR_PIPELINE_TYPES:
            self.assertIn(skill, unavailable)

    def test_podcast_needs_the_soundtrack_to_drive_it_exactly_as_a_music_video(self):
        video = assess_director_model("minimax_h3_omni", {"architecture": "minimax_h3"})["video"]
        self.assertEqual(video["podcast"], video["music_video"])
        self.assertEqual(video["podcast"], video["short_film_audio"])

    def test_viral_needs_generated_dialogue_exactly_as_a_story_driven_short(self):
        video = assess_director_model("minimax_h3_omni", {"architecture": "minimax_h3"})["video"]
        self.assertEqual(video["viral_video"], video["short_film_story"])


if __name__ == "__main__":
    unittest.main()
