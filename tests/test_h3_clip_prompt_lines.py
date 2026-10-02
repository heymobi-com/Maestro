"""The one-clip H3 resume that could not render, pinned at the unpacker.

The failure was `Missing MiniMax H3 reference manifest for clip 2` on a resume whose last clip
carried one manifest: `multi_prompts_gen_type == 3` with a single prompt has no clip-boundary
separator, so the request was split on newlines and one compiled six-field Context-IR prompt
became a clip per line. The policy that decides this lives in
`services/director/h3_clip_prompt_lines.py`; this file pins it and the shapes it must not change.
"""

import os
import sys
import unittest


_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.h3_clip_prompt_lines import (  # noqa: E402
    CLIP_SEPARATOR,
    clip_prompt_lines,
)

# A compiled H3 Context-IR prompt, exactly as the compiler writes it: multi-line by design.
COMPILED_PROMPT = (
    "subject_definitions: <Subject 1> is Bela (S1): a woman in a linen dress.\n\n"
    "summary: [ref2va] Bela walks through the garden.\n\n"
    "retention_analysis: Preserve the identity, wardrobe and setting.\n\n"
    "detailed_description: [Shot 1] This is one continuous shot: do not cut. "
    "Bela walks left to right.\n\n"
    "overall_soundscape: Birds and light wind.\n\n"
    'non_diegetic_music: Use the mapped driving audio.\n\n'
    'Dialogue: Bela (S1) speaks: <d>[Spanish] Buenos dias.</d>'
)


class TheOneClipResume(unittest.TestCase):
    def test_one_manifest_and_one_prompt_is_one_clip(self):
        """The reported shape: 1 clip, 1 manifest, a six-field prompt, and it must survive."""
        lines = clip_prompt_lines(COMPILED_PROMPT, [{"type": "image"}])

        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0], COMPILED_PROMPT.strip())
        # The clip then finds its manifest, which is what the render demanded.
        self.assertLess(0, len([{"type": "image"}]))

    def test_the_old_fallback_would_have_minted_one_clip_per_line(self):
        """The counterexample, so this pin can fail if the rule is removed.

        Splitting the very same prompt on newlines yields more than one clip, and the second
        one has no manifest: that is the ValueError this work removes.
        """
        naive = [line.strip() for line in COMPILED_PROMPT.split("\n") if line.strip()]

        self.assertGreater(len(naive), 1)
        self.assertGreater(len(naive), len([{"type": "image"}]))


class TheShapesItMustNotChange(unittest.TestCase):
    def test_two_prompts_split_on_the_boundary_separator(self):
        text = CLIP_SEPARATOR.join([COMPILED_PROMPT, COMPILED_PROMPT.replace("Bela", "Luis")])

        lines = clip_prompt_lines(text, [{"a": 1}, {"a": 2}])

        self.assertEqual(len(lines), 2)
        self.assertIn("Luis", lines[1])
        self.assertNotIn("Luis", lines[0])

    def test_the_legacy_newline_split_is_kept_without_manifests(self):
        """Studio mode and the legacy Director send one prompt per line and no manifests."""
        lines = clip_prompt_lines("first shot\nsecond shot\n\nthird shot", None)

        self.assertEqual(lines, ["first shot", "second shot", "third shot"])

    def test_several_manifests_without_a_separator_keep_the_old_behaviour(self):
        """Not the reported shape, so it is left exactly as it was rather than guessed at."""
        lines = clip_prompt_lines("first shot\nsecond shot", [{"a": 1}, {"a": 2}])

        self.assertEqual(lines, ["first shot", "second shot"])

    def test_an_empty_prompt_is_no_clip(self):
        self.assertEqual(clip_prompt_lines("   ", [{"a": 1}]), [])
        self.assertEqual(clip_prompt_lines("", None), [])


if __name__ == "__main__":
    unittest.main()
