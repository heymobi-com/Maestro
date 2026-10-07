"""A staged process is told across shots, not repeated inside every shot.

Reported from use: a dramatic instruction (plants growing, blooming and drying; rain
falling) "lo hace en un solo clip y lo repite mas delante como si ese clip fuera una sola
transicion creando efectos super rapidos que pierden su intencion". The director asked for
the obvious thing: a story is not told in seven seconds repeated over and over, it
transitions from one shot to the next, sometimes slowly enough for the detail to read.

Measured on the affected project's own plan (30 clips, 2.6s to 9.6s each): the complete
plant process -- sprouting, blooming, branches, leaves, drying -- appeared with all six
terms inside EACH of clips 8 to 15, and again at 23 and 29. Rain or lightning appeared in
26 of the 30 shots.

The planner is told each video_prompt must be self-contained, which is right for setting,
wardrobe and identity, and the H3 guide says nothing about advancing a process across
shots. So the contract that was missing is stated once, in a guide of ours, and attached to
the shot-writing guides by the loader -- upstream's guide files stay byte-identical on disk.
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.guide_loader import load_guide  # noqa: E402

ADDENDUM_MARKER = "STAGED PROGRESSION"


def flat(text):
    """Guide files are wrapped prose; compare it with the wrapping removed."""

    return " ".join(str(text or "").split())


class TheStagedProgressionContractTests(unittest.TestCase):
    def test_the_h3_shot_guide_keeps_its_own_text_and_gains_the_contract(self):
        guide = load_guide("minimax_h3_shot_breakdown.md")

        # Upstream's own rules are still there, untouched.
        self.assertIn("CONTEXT-IR FORMAT", guide)
        self.assertIn("continuity_group", guide)
        # And the contract that was missing.
        self.assertIn(ADDENDUM_MARKER, guide)

    def test_the_ltx_music_video_guide_gains_the_contract(self):
        """The guide that bans continuity wording needs the exception most."""

        guide = flat(load_guide("ltx2_music_video_rules.md"))

        self.assertIn("NEVER use continuity words", guide)
        self.assertIn(ADDENDUM_MARKER, guide)
        self.assertIn("continuity reference is required rather than forbidden", guide)

    def test_the_contract_states_one_stage_per_shot(self):
        guide = flat(load_guide("minimax_h3_shot_breakdown.md"))

        self.assertIn(
            "Never write the whole process into a single shot",
            guide,
        )
        self.assertIn("Give each shot ONE stage", guide)

    def test_the_contract_fits_the_stage_to_the_shot_duration(self):
        """A three-second shot holding three stages is the reported fast flashing."""

        guide = flat(load_guide("minimax_h3_shot_breakdown.md"))

        self.assertIn("a three-second shot holds one small visible change", guide)

    def test_a_guide_that_does_not_write_shots_is_left_alone(self):
        """The contract belongs where shots are decided, not in every guide."""

        guide = load_guide("video_prompt_rules.md")

        self.assertIn("SINGLE CONTINUOUS SHOT", guide)
        self.assertNotIn(ADDENDUM_MARKER, guide)

    def test_an_unknown_guide_is_still_an_empty_string(self):
        self.assertEqual(load_guide("no-such-guide.md"), "")


if __name__ == "__main__":
    unittest.main()
