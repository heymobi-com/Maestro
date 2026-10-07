"""The film's story must not be repeated inside every clip.

Reported from use: "le doy instrucciones acerca de todo el video pero repite las instrucciones
en cada clip en vez de hacer un plan para todo el video ... hace loops de todas las
instrucciones en cada clip quedando demasiado cargado".

Measured on the affected project (30 clips): each clip's prompt was 7,282-8,010 characters,
and the project's brief travelled inside all of them. The brief splits in two: what a clip
needs in order to render correctly on its own (the cast's faces and wardrobe, who lip-syncs,
the vocal role, the visual world) and the film's own sequence of events. The second part was
being pasted into every clip verbatim -- "at some point it rains, some creeper plants grow
... then she comes back ... there's some slow dancing ... the Camera does an Orbit Shot" --
so the model was asked to perform the whole film inside each shot.

These cases are ours, and they live in their own file: the dialogue suite they belong with is
upstream's, and every line we add there is a line that can conflict.
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director import h3_dialogue  # noqa: E402
from services.director.h3_dialogue import compile_h3_official_prompt  # noqa: E402
from services.director.project_brief import clip_project_context  # noqa: E402

SHOT = "A wide shot of S2 standing at the piano, his mouth remaining closed."
# The mode a music video with reference images actually renders in. The other mode has a
# token-budget fitter, so it would hide the difference this file is about.
REFERENCES = [{"kind": "image", "filename": "ref1.jpg"}]

# The shape of a real brief: contract sections first, the film's story last.
BRIEF = """CHARACTERS AND BINDINGS:
<Subject 1> (S1): The young woman in the red dress; identity comes from <Picture 1>.

<Subject 2> (S2)(SINGER) : The man in the white shirt and leather vest; identity comes from <Picture 2>.

AUDIO-DRIVEN GENERATION:
- Video is driven by the provided audio track. Sync lip movements to the audio exactly.
- Only the man (S2) lipsyncs. The other subject keeps mouth closed.
- Do not generate new dialogue. Do not invent speech.

SINGER:
Lead vocal: a raspy masculine bass voice, tired but emotional, long intimate notes.

DESCRIPTION:
A song about the moment love hurt. At some point it rains, some creeper plants grow green
leaves and blooming branches then dry up dramatically. In some scene the woman is seen
cheating in the far background of the warehouse with random men. Then she comes back to the
singer looking for his loving affirmation. There is some slow dancing and sensual theatrical
entanglement while the Camera does an Orbit Shot.

AMBIENCE:
An emotional masterpiece of love and despair. The setting is a cavernous brutalist warehouse
with a glossy, mirror-like floor and a solitary piano under a stark spotlight."""


class TheFilmStoryStaysOutOfTheClipTests(unittest.TestCase):
    def test_the_films_events_do_not_travel_with_the_clip(self):
        kept = clip_project_context(BRIEF)

        self.assertNotIn("creeper plants", kept)
        self.assertNotIn("Orbit Shot", kept)
        self.assertNotIn("DESCRIPTION:", kept)
        # The contract that makes a clip render correctly on its own does travel.
        self.assertIn("CHARACTERS AND BINDINGS:", kept)
        self.assertIn("<Subject 2> (S2)(SINGER)", kept)
        self.assertIn("AUDIO-DRIVEN GENERATION:", kept)
        self.assertIn("Only the man (S2) lipsyncs", kept)
        self.assertIn("SINGER:", kept)
        self.assertIn("tired but emotional", kept)
        # The visual world is what keeps the look stable between shots.
        self.assertIn("AMBIENCE:", kept)
        self.assertIn("glossy, mirror-like floor", kept)

    def test_the_kept_sections_keep_the_order_their_author_wrote(self):
        kept = clip_project_context(BRIEF)

        self.assertLess(
            kept.index("CHARACTERS AND BINDINGS:"), kept.index("AUDIO-DRIVEN GENERATION:"),
        )
        self.assertLess(kept.index("AUDIO-DRIVEN GENERATION:"), kept.index("SINGER:"))
        self.assertLess(kept.index("SINGER:"), kept.index("AMBIENCE:"))
        # The narrative section sat between them; dropping it must not reorder anything.
        self.assertLess(kept.index("SINGER:"), kept.index("AMBIENCE:"))

    def test_a_brief_with_nothing_to_drop_is_returned_untouched(self):
        brief = "CHARACTERS AND BINDINGS:\n<Subject 1> (S1): a woman in a red dress."

        self.assertEqual(clip_project_context(brief), brief)

    def test_a_narrative_only_brief_leaves_no_context_at_all(self):
        brief = "DESCRIPTION:\nA song about surrender, told across the whole film."

        self.assertEqual(clip_project_context(brief), "")

    def test_a_brief_that_is_only_a_continuation_line_survives(self):
        """A subject row split off by a blank line carries an identity, not a story."""

        brief = "CHARACTERS AND BINDINGS:\n<Subject 1> (S1): a woman in red.\n\n<Subject 2> (S2): a man in a leather vest."

        self.assertIn("<Subject 2> (S2)", clip_project_context(brief))

    def test_an_empty_brief_is_an_empty_context(self):
        self.assertEqual(clip_project_context(None), "")
        self.assertEqual(clip_project_context("   "), "")

    def test_the_compiled_clip_prompt_no_longer_carries_the_film(self):
        """The end of the claim: what the model receives, not what the helper returns."""

        compiled, _ = compile_h3_official_prompt(
            SHOT, [], [], mode="ref2va", references=REFERENCES,
            project_context=BRIEF,
        )

        self.assertNotIn("creeper plants", compiled)
        self.assertNotIn("Orbit Shot", compiled)
        self.assertIn("glossy, mirror-like floor", compiled)
        self.assertIn("Do not invent speech", compiled)
        self.assertIn("<Subject 2> (S2)(SINGER)", compiled)

    def test_the_compiler_is_what_keeps_the_film_out_of_the_clip(self):
        """Bypassing the trim brings the narrative back, so this is the wiring."""

        def compile_once(project_context):
            compiled, _ = compile_h3_official_prompt(
                SHOT, [], [], mode="ref2va", references=REFERENCES,
                project_context=project_context,
            )
            return compiled

        kept = compile_once(BRIEF)
        self.assertNotIn("creeper plants", kept)

        original = h3_dialogue._clip_context
        h3_dialogue._clip_context = lambda value: value
        try:
            untrimmed = compile_once(BRIEF)
        finally:
            h3_dialogue._clip_context = original

        self.assertIn("creeper plants", untrimmed)
        self.assertGreater(len(untrimmed), len(kept))


if __name__ == "__main__":
    unittest.main()
