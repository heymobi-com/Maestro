"""The correction assistant measures the shot before it rewrites it.

Three notes on one shot of a real project never removed a duplicated character,
because the assistant saw only the prompt text and the note: it reasoned about a
symptom it could not see. The prompt had declared two shots inside a 7.29 s clip
and placed the man behind the speaker in both, none of which the notes mentioned
and none of which the assistant could measure.

These tests pin the four parts of the loop: the measurement that names the
defects, the bounded conversation that carries the director's turns, the gate
that keeps a broken rewrite out of the editor, and the answer shape that lets the
assistant ask instead of guessing.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch


_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
_APP_DIR = os.path.join(_ROOT, "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services import director_pipeline as pipeline  # noqa: E402
from services.director.h3_dialogue import (  # noqa: E402
    diagnose_h3_clip_prompt,
    h3_dialogue_blocks,
    review_h3_revision,
)
from services.director_pipeline import (  # noqa: E402
    _NO_OP_CHANGE_CHARS,
    _changed_characters,
    _parse_revision_envelope,
)


def _prompt(body_shots: str, timestamp: str = "") -> str:
    return (
        "subject_definitions: <Subject 1>: Valeria, a woman in a navy blazer.\n"
        "<Subject 2>: Ricardo, a man with grey hair and glasses.\n\n"
        "summary: [reference generation] Over-the-shoulder on Valeria.\n\n"
        "retention_analysis: Preserve the described identities and wardrobe.\n\n"
        f"detailed_description: The target video keeps its style. [Shot 1] {body_shots}"
        f"{timestamp} Dialogue timing: the tagged lines run from 0.00 to 7.29 seconds. "
        "Dialogue: <d>[Spanish] Hola.</d>. Only the tagged lines are spoken, no "
        "background voice occurs.\n\n"
        "overall_soundscape: Natural ambience.\n\n"
        "non_diegetic_music: N/A\n"
    )


# From the real shot: two shots, the man behind the speaker in both, and a
# timestamp of 177.7 s inside a clip that ends at 7.29 s.
PROBLEM_PROMPT = _prompt(
    "Valeria speaks, with Ricardo visible in the periphery. "
    "[Shot 2] Valeria speaks to the man across from her. ",
    timestamp="In the background blur, Ricardo is visible. At 177.7s he nods. ",
)
FIXED_PROMPT = _prompt(
    "Valeria and Ricardo sit facing each other across the desk, both in frame. ",
)

SUBJECTS = [
    {"visual_description": "Valeria, a woman in a navy blazer.",
     "position_or_relation": "foreground left"},
    {"visual_description": "Ricardo, a man with grey hair.",
     "position_or_relation": "background right"},
]


class MeasurementTests(unittest.TestCase):
    """What the assistant is told about the shot, in facts rather than guesses."""

    def setUp(self):
        self.problem = diagnose_h3_clip_prompt(
            PROBLEM_PROMPT, duration_seconds=7.29, subjects=SUBJECTS,
        )
        self.fixed = diagnose_h3_clip_prompt(
            FIXED_PROMPT, duration_seconds=7.29, subjects=SUBJECTS,
        )

    def test_the_two_shots_are_named_with_the_reason_it_matters(self):
        self.assertEqual(self.problem["shots"], [1, 2])
        joined = " ".join(self.problem["findings"])
        self.assertIn("declares 2 shot(s)", joined)
        self.assertIn("ONE continuous shot", joined)

    def test_a_timestamp_past_the_end_of_the_clip_is_reported(self):
        joined = " ".join(self.problem["findings"])
        self.assertIn("177.7s", joined)
        self.assertIn("7.29", joined)

    def test_the_subject_row_placement_is_attributed_to_the_plan(self):
        # The plan row places him behind the speaker. For an edited prompt the
        # text is what the model reads, so the finding must not claim otherwise.
        self.assertEqual(self.problem["behind_speaker"], ["Ricardo"])
        joined = " ".join(self.problem["findings"])
        self.assertIn("Subject row:", joined)
        self.assertIn("recompiled from the plan", joined)

    def test_the_measurement_counts_the_spoken_lines(self):
        self.assertEqual(self.problem["dialogue_blocks"], 1)

    def test_a_single_shot_prompt_is_not_reported_as_multi_shot(self):
        self.assertEqual(self.fixed["shots"], [1])
        joined = " ".join(self.fixed["findings"])
        self.assertNotIn("ONE continuous shot", joined)
        self.assertNotIn("beyond the end of the clip", joined)


class RevisionGateTests(unittest.TestCase):
    """A rewrite reaches the editor only if it survives the checks."""

    def test_the_corrected_prompt_passes(self):
        self.assertEqual(review_h3_revision(PROBLEM_PROMPT, FIXED_PROMPT), [])

    def test_a_rewrite_that_changes_a_spoken_line_is_refused(self):
        changed = FIXED_PROMPT.replace("<d>[Spanish] Hola.</d>", "<d>[Spanish] Adios.</d>")
        problems = review_h3_revision(PROBLEM_PROMPT, changed)
        self.assertTrue(problems)
        self.assertIn("spoken words", " ".join(problems))

    def test_a_rewrite_that_drops_a_spoken_line_is_refused(self):
        dropped = FIXED_PROMPT.replace("<d>[Spanish] Hola.</d>", "")
        self.assertIn("instead of", " ".join(review_h3_revision(PROBLEM_PROMPT, dropped)))

    def test_a_rewrite_that_keeps_a_second_shot_is_refused(self):
        problems = review_h3_revision(
            PROBLEM_PROMPT, FIXED_PROMPT.replace("[Shot 1]", "[Shot 2]", 1),
        )
        self.assertIn("several shots", " ".join(problems))

    def test_a_rewrite_that_loses_the_shot_marker_is_refused(self):
        problems = review_h3_revision(PROBLEM_PROMPT, FIXED_PROMPT.replace("[Shot 1]", ""))
        self.assertIn("lost its [Shot 1]", " ".join(problems))

    def test_the_gate_reads_the_spoken_lines_in_order(self):
        blocks = h3_dialogue_blocks(PROBLEM_PROMPT)
        self.assertEqual(len(blocks), 1)
        self.assertIn("Hola", blocks[0])


class RevisionEnvelopeTests(unittest.TestCase):
    """The answer shape that lets the assistant ask instead of guessing."""

    def test_analysis_question_and_prompt_are_separated(self):
        parts = _parse_revision_envelope(
            "ANALYSIS: the body declares two shots.\n"
            "QUESTION: NONE\n"
            "FIXED_PROMPT: subject_definitions: <Subject 1>: Valeria.\n"
        )
        self.assertIn("two shots", parts["analysis"])
        self.assertEqual(parts["question"], "")
        self.assertIn("subject_definitions", parts["prompt"])

    def test_a_question_does_not_count_as_a_rewrite(self):
        parts = _parse_revision_envelope(
            "ANALYSIS: he is placed behind her twice.\n"
            "QUESTION: should they both sit facing each other?\n"
            "FIXED_PROMPT: NONE\n"
        )
        self.assertIn("facing each other", parts["question"])
        self.assertEqual(parts["prompt"], "")

    def test_a_prompt_is_not_mistaken_for_a_marker(self):
        # `retention_analysis:` starts a line, and must not read as ANALYSIS.
        parts = _parse_revision_envelope(
            "ANALYSIS: reads fine.\n"
            "FIXED_PROMPT: subject_definitions: a\nretention_analysis: b\n"
        )
        self.assertIn("retention_analysis: b", parts["prompt"])

    def test_a_bare_compiled_prompt_still_lands_in_the_editor(self):
        parts = _parse_revision_envelope(FIXED_PROMPT)
        self.assertEqual(parts["prompt"], FIXED_PROMPT.strip())
        self.assertEqual(parts["analysis"], "")

    def test_prose_without_markers_is_read_as_the_analysis(self):
        parts = _parse_revision_envelope("I could not tell which man you meant.")
        self.assertIn("which man", parts["analysis"])
        self.assertEqual(parts["prompt"], "")


class CorrectionConversationWiringTests(unittest.TestCase):
    """The endpoint and the turn must carry the measurement and the turns."""

    def setUp(self):
        with open(
            os.path.join(_APP_DIR, "services", "director_pipeline.py"), encoding="utf-8",
        ) as handle:
            self.pipeline = handle.read()
        with open(os.path.join(_APP_DIR, "launch.py"), encoding="utf-8") as handle:
            self.launch = handle.read()

    def test_the_turn_hands_the_assistant_the_measurement(self):
        self.assertIn("MEASUREMENT OF THE CURRENT PROMPT", self.pipeline)
        self.assertIn("diagnose_h3_clip_prompt(", self.pipeline)

    def test_the_conversation_is_carried_and_bounded(self):
        self.assertIn("history: Optional[list] = None", self.pipeline)
        self.assertIn("][-6:]", self.pipeline)
        self.assertIn('history = body.get("history")', self.launch)

    def test_a_refused_rewrite_never_becomes_the_prompt(self):
        start = self.pipeline.index("def _candidate_problems(")
        tail = self.pipeline[start:start + 2600]
        self.assertIn('result["errors"] = [', tail)
        # The errors are recorded and the turn ends there: the rewrite is only
        # assigned after the checks passed.
        self.assertLess(
            tail.index('result["errors"] = ['),
            tail.index('result["video_prompt"] = parts["prompt"]'),
        )

    def test_the_assistant_is_told_to_explain_and_may_ask(self):
        self.assertIn("ANALYSIS:", self.pipeline)
        self.assertIn("QUESTION:", self.pipeline)
        self.assertIn("FIXED_PROMPT:", self.pipeline)
        self.assertIn("rejected before the director sees it", self.pipeline)


class NoOpRewriteTests(unittest.TestCase):
    """A rewrite that changes nothing is not a correction.

    Three notes on clip 13 of a real project -- "the woman must faithfully
    lip-sync the dialogue assigned to S1" -- came back as 2, 55 and 1 changed
    characters while the render stayed wrong. The assistant explained the problem
    and handed the same prompt back, and the loop offered it as a fix, because
    the gate only checks that a rewrite breaks nothing.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out_dir = self.temp.name
        self.pid = "noop01"

    def _save(self):
        state = {
            "pipeline_id": self.pid,
            "status": "completed",
            "video_model": "minimax_h3_ref2va_fused_turbo",
            "clips": [{
                "index": 0,
                "video_prompt": PROBLEM_PROMPT,
                "_director_duration_sec": 7.29,
                "_director_h3_prompt_mode": "ref2va",
                # Dialogue-driven, so the audio-plan finding stays out of this
                # test: it is covered by its own case below.
                "_director_audio_plan": {
                    "mode": "dialogue_driven",
                    "lip_sync_critical": True,
                },
            }],
            "_params_snapshot": {"scene_description": "PROJECT: a studio"},
        }
        with open(
            os.path.join(self.out_dir, f"_director_pipeline_{self.pid}.json"),
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(state, handle)

    def _stub(self, answers):
        """Answer in order, and hand back the prompts the model received."""

        sent = []

        def fake(**kwargs):
            sent.append(kwargs["prompt"])
            return answers[min(len(sent) - 1, len(answers) - 1)]

        loader = patch.object(pipeline, "_ensure_llm_loaded", lambda params: None)
        completion = patch("services.llm_service.enhance_prompt", fake)
        loader.start()
        completion.start()
        self.addCleanup(loader.stop)
        self.addCleanup(completion.stop)
        return sent

    @staticmethod
    def _answer(body: str) -> str:
        return (
            "ANALYSIS: the prompt already assigns the line to S1.\n"
            "QUESTION: NONE\n"
            f"FIXED_PROMPT: {body}"
        )

    def test_a_no_op_rewrite_is_refused_after_one_retry(self):
        self._save()
        sent = self._stub([
            self._answer(PROBLEM_PROMPT),
            self._answer(PROBLEM_PROMPT),
        ])

        result = pipeline.revise_clip_prompt(
            self.out_dir, self.pid, 0, "the woman must lip-sync her line",
        )

        self.assertFalse(result["rewritten"])
        self.assertEqual(result["video_prompt"], "")
        self.assertTrue(result["errors"], "a two-character edit must not pass as a fix")
        self.assertEqual(len(sent), 2, "the empty rewrite must be retried once")
        self.assertIn(
            "changed almost nothing",
            sent[1],
            "the retry has to say what is missing",
        )

    def test_a_rewrite_that_edits_the_prompt_is_offered(self):
        self._save()
        self._stub([self._answer(PROBLEM_PROMPT), self._answer(FIXED_PROMPT)])

        result = pipeline.revise_clip_prompt(
            self.out_dir, self.pid, 0, "make the two of them face each other",
        )

        self.assertTrue(result["rewritten"])
        # The envelope trims the whitespace around each marker's value, so the
        # prompt comes back without FIXED_PROMPT's trailing newline.
        self.assertEqual(result["video_prompt"], FIXED_PROMPT.strip())
        self.assertEqual(result["errors"], [])

    def test_an_answer_with_no_prompt_and_no_question_is_reported(self):
        self._save()
        self._stub(["ANALYSIS: the shot looks fine to me."])

        result = pipeline.revise_clip_prompt(
            self.out_dir, self.pid, 0, "the woman must lip-sync her line",
        )

        self.assertFalse(result["rewritten"])
        self.assertTrue(result["errors"], "a turn with nothing to show must say so")

    def test_changed_characters_counts_what_moved(self):
        self.assertEqual(_changed_characters(PROBLEM_PROMPT, PROBLEM_PROMPT), 0)
        self.assertGreater(
            _changed_characters(PROBLEM_PROMPT, FIXED_PROMPT),
            _NO_OP_CHANGE_CHARS,
        )

    def test_a_rewrite_that_touches_the_spoken_words_is_retried_then_refused(self):
        # The gate protects the dialogue lines: the words the audio carries
        # cannot change. The retry has to be told that, or it answers with the
        # same rewrite and the director is left with nothing to act on.
        self._save()
        broken = FIXED_PROMPT.replace(
            "<d>[Spanish] Hola.</d>", "<d>[Spanish] Hola, buenos dias.</d>",
        )
        sent = self._stub([self._answer(broken), self._answer(broken)])

        result = pipeline.revise_clip_prompt(
            self.out_dir, self.pid, 0, "the woman must lip-sync her line",
        )

        self.assertFalse(result["rewritten"])
        self.assertIn("spoken", " ".join(result["errors"]).lower())
        self.assertEqual(len(sent), 2, "the refused rewrite must be retried once")
        self.assertIn("byte for byte", sent[1], "the retry names what broke")

    def test_the_retry_can_recover_from_a_gate_failure(self):
        self._save()
        broken = FIXED_PROMPT.replace(
            "<d>[Spanish] Hola.</d>", "<d>[Spanish] Hola, buenos dias.</d>",
        )
        self._stub([self._answer(broken), self._answer(FIXED_PROMPT)])

        result = pipeline.revise_clip_prompt(
            self.out_dir, self.pid, 0, "make the two of them face each other",
        )

        self.assertTrue(result["rewritten"])
        self.assertEqual(result["video_prompt"], FIXED_PROMPT.strip())


class AudioPlanFindingTests(unittest.TestCase):
    """The measurement has to name what stops the mouths moving.

    Clip 13's plan said "ambient_only", so the orchestrator never selected the
    audio-to-video path: no prompt wording could make the woman lip-sync. The
    assistant blamed pronouns and gestures for three turns because nothing told
    it where to look.
    """

    def test_a_plan_that_skips_the_audio_path_is_named(self):
        diagnosis = diagnose_h3_clip_prompt(
            _prompt("Valeria speaks to camera."),
            duration_seconds=7.29,
            audio_plan={"mode": "ambient_only", "lip_sync_critical": True},
        )

        joined = " ".join(diagnosis["findings"])
        self.assertIn("ambient_only", joined)
        self.assertIn("audio-to-video", joined)
        self.assertIn("No wording in this prompt can change that", joined)

    def test_a_dialogue_driven_plan_raises_nothing(self):
        diagnosis = diagnose_h3_clip_prompt(
            _prompt("Valeria speaks to camera."),
            duration_seconds=7.29,
            audio_plan={"mode": "dialogue_driven", "lip_sync_critical": True},
        )

        self.assertNotIn("audio-to-video", " ".join(diagnosis["findings"]))

    def test_a_plan_without_lip_sync_critical_is_named_too(self):
        diagnosis = diagnose_h3_clip_prompt(
            _prompt("Valeria speaks to camera."),
            duration_seconds=7.29,
            audio_plan={"mode": "dialogue_driven", "lip_sync_critical": False},
        )

        self.assertIn("audio-to-video", " ".join(diagnosis["findings"]))

    def test_a_silent_clip_raises_nothing(self):
        silent = _prompt("Valeria looks at the camera.").replace(
            "Dialogue: <d>[Spanish] Hola.</d>. ", "",
        )
        diagnosis = diagnose_h3_clip_prompt(
            silent,
            duration_seconds=7.29,
            audio_plan={"mode": "ambient_only", "lip_sync_critical": False},
        )

        self.assertNotIn("audio-to-video", " ".join(diagnosis["findings"]))


if __name__ == "__main__":
    unittest.main()
