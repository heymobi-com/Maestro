"""A clip whose planning batch failed must not be given the film's own text as its prompt.

Measured on a real 30-shot project: one failed batch left 10 of 30 clips carrying the identical
3050-character prompt, which was the project briefing word for word (``scene_description``,
starting ``CHARACTERS AND BINDINGS:``). Those clips rendered the briefing as if it were a shot,
so the film repeated the same instructions, had no per-shot design in those slots, and could not
continue from a neighbour.

These cases are ours, so they live in their own file.
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.planner_fallback import shot_placeholder  # noqa: E402

# The shape of the briefing that used to end up in the prompt: long, and opening with the
# subject bindings the film's own context parser looks for.
_BRIEF = (
    "CHARACTERS AND BINDINGS:\n<Subject 1> (S1): a young woman in a red dress, red shoes; "
    "natural clear color fingernails, slim body build.\n" + ("AUDIO-DRIVEN GENERATION: " * 60)
)


class ThePlaceholderNeverCarriesTheFilmTests(unittest.TestCase):
    def test_the_films_text_does_not_become_the_shot(self):
        for label in ("verse", "chorus", "instrumental", None):
            with self.subTest(label=label):
                row = shot_placeholder(8, {"label": label, "video_prompt": _BRIEF})

                self.assertNotIn("CHARACTERS AND BINDINGS", row["video_prompt"])
                self.assertNotIn("AUDIO-DRIVEN GENERATION", row["video_prompt"])
                self.assertLess(len(row["video_prompt"]), 300)

    def test_a_clip_that_is_itself_the_briefing_still_yields_a_short_shot(self):
        """The clip dict is read for its section only, never copied into the prompt."""

        row = shot_placeholder(9, _BRIEF)

        self.assertLess(len(row["video_prompt"]), 300)

    def test_it_does_not_claim_to_be_a_planned_shot(self):
        row = shot_placeholder(8, {"label": "chorus"})

        self.assertIn("Continue the chorus section", row["scene_goal"])
        self.assertIn("global clip 9", row["scene_goal"])


class ThePlaceholderIsStillRenderableTests(unittest.TestCase):
    def test_it_has_what_a_shot_needs_to_render(self):
        row = shot_placeholder(0, {"label": "verse"})

        self.assertTrue(str(row["video_prompt"]).strip())
        self.assertTrue(str(row["ending_beat"]).strip())
        self.assertTrue(str(row["camera_plan"]["framing"]).strip())
        self.assertEqual(row["window_prompts"], [])
        self.assertEqual(row["action_beats"], [])

    def test_an_instrumental_section_is_atmospheric(self):
        self.assertEqual(
            shot_placeholder(0, {"label": "instrumental"})["scene_type"], "atmospheric"
        )
        self.assertEqual(
            shot_placeholder(0, {"label": "verse"})["scene_type"], "performance"
        )

    def test_a_clip_with_no_label_is_still_a_section(self):
        self.assertIn("music section", shot_placeholder(0, {})["scene_goal"])

    def test_a_clip_that_is_not_a_mapping_does_not_raise(self):
        self.assertIn("music section", shot_placeholder(0, None)["scene_goal"])


class ThePlannerUsesItTests(unittest.TestCase):
    def test_the_music_video_planner_hands_over_the_honest_placeholder(self):
        with open(
            os.path.join(_APP_DIR, "services", "director", "planners", "music_video.py"),
            "r", encoding="utf-8",
        ) as handle:
            source = handle.read()

        self.assertIn("fallback_factory=shot_placeholder,", source)
        self.assertNotIn('"video_prompt": scene_description,', source)


if __name__ == "__main__":
    unittest.main()
