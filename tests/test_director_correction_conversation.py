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
    _build_stable_speaker_registry,
    _declared_shot_numbers,
    _single_shot_body,
    _speaker_registry_entry,
    compile_h3_clip_plans,
    diagnose_h3_clip_prompt,
    h3_dialogue_blocks,
    reconcile_audio_plan_with_dialogue,
    retain_dialogue_beats,
    review_h3_revision,
)
from services.director_pipeline import (  # noqa: E402
    _NO_OP_CHANGE_CHARS,
    _NO_OP_CHANGE_WORDS,
    _changed_characters,
    _changed_words,
    _parse_revision_envelope,
    _retained_h3_beats,
    _revise_problem_nudge,
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
        end = self.pipeline.index("def _record_regenerated_clip(", start)
        body = self.pipeline[start:end]
        self.assertIn('result["errors"] = [', body)
        # The errors are recorded and the turn ends there: the rewrite is only
        # assigned after the checks passed.
        self.assertLess(
            body.index('result["errors"] = ['),
            body.index('result["video_prompt"] = parts["prompt"]'),
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

    def test_a_gesture_rewrite_is_not_a_no_op(self):
        """Deleting the competing gesture is small but real.

        "While speaking, nodding slowly." is 38 characters, under the character
        rule, so a character count alone would have thrown away the only edit that
        could have helped. Words keep it.
        """

        asking = PROBLEM_PROMPT.replace(
            "Valeria speaks, with Ricardo visible in the periphery.",
            "Valeria speaks, with Ricardo visible in the periphery. "
            "While speaking, nodding slowly.",
        )
        fixed = asking.replace(" While speaking, nodding slowly.", "")

        self.assertLess(_changed_characters(asking, fixed), _NO_OP_CHANGE_CHARS)
        self.assertGreaterEqual(_changed_words(asking, fixed), 3)

    def test_moving_a_comma_is_a_no_op(self):
        asking = PROBLEM_PROMPT
        fixed = asking.replace("Valeria speaks,", "Valeria speaks")

        # One word out, one word in: still a reshuffle, never a correction.
        self.assertLess(_changed_words(asking, fixed), _NO_OP_CHANGE_WORDS)

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
    """The plan is made to agree with the dialogue the shot carries.

    Clip 13's plan said "ambient_only" while its prompt carried four spoken
    lines, and the orchestrator selects the audio-to-video path only for an
    audio/dialogue-driven shot marked lip-sync critical -- so nothing drove the
    mouths from the voice track. 104 of that project's 177 clips were in that
    state, 275 of 1422 across all projects. No prompt edit could bring the path
    back, which is why three notes on that clip changed 2, 55 and 1 characters.
    """

    def test_a_plan_that_skips_the_audio_path_is_named(self):
        diagnosis = diagnose_h3_clip_prompt(
            _prompt("Valeria speaks to camera."),
            duration_seconds=7.29,
            audio_plan={"mode": "ambient_only", "lip_sync_critical": True},
        )

        joined = " ".join(diagnosis["findings"])
        self.assertIn("ambient_only", joined)
        self.assertIn("reconciles that before choosing the source mode", joined)
        self.assertIn("stale", joined)

    def test_a_dialogue_driven_plan_raises_nothing(self):
        diagnosis = diagnose_h3_clip_prompt(
            _prompt("Valeria speaks to camera."),
            duration_seconds=7.29,
            audio_plan={"mode": "dialogue_driven", "lip_sync_critical": True},
        )

        self.assertNotIn("stored audio plan", " ".join(diagnosis["findings"]))

    def test_a_plan_without_lip_sync_critical_is_named_too(self):
        diagnosis = diagnose_h3_clip_prompt(
            _prompt("Valeria speaks to camera."),
            duration_seconds=7.29,
            audio_plan={"mode": "dialogue_driven", "lip_sync_critical": False},
        )

        self.assertIn("stored audio plan", " ".join(diagnosis["findings"]))

    def test_a_silent_clip_raises_nothing(self):
        silent = _prompt("Valeria looks at the camera.").replace(
            "Dialogue: <d>[Spanish] Hola.</d>. ", "",
        )
        diagnosis = diagnose_h3_clip_prompt(
            silent,
            duration_seconds=7.29,
            audio_plan={"mode": "ambient_only", "lip_sync_critical": False},
        )

        self.assertNotIn("stored audio plan", " ".join(diagnosis["findings"]))

    def test_an_ambient_plan_with_dialogue_becomes_a_dialogue_shot(self):
        resolved = reconcile_audio_plan_with_dialogue(
            {"mode": "ambient_only", "timing_anchor": "audio", "lip_sync_critical": True},
            prompt=_prompt("Valeria speaks to camera."),
        )

        self.assertEqual(resolved["mode"], "dialogue_driven")
        self.assertTrue(resolved["lip_sync_critical"])
        self.assertEqual(resolved["timing_anchor"], "audio")

    def test_the_dialogue_beats_alone_are_enough(self):
        # A prompt the director edited can be saved before the beats are rebuilt,
        # and the beats can be cleared while the prompt still speaks.
        resolved = reconcile_audio_plan_with_dialogue(
            {"mode": "ambient_only"},
            dialogue_beats=[{"speaker_id": "(S1)", "spoken_text": "Hola."}],
        )

        self.assertEqual(resolved["mode"], "dialogue_driven")

    def test_a_dialogue_plan_without_lip_sync_critical_is_marked(self):
        resolved = reconcile_audio_plan_with_dialogue(
            {"mode": "dialogue_driven", "lip_sync_critical": False},
            prompt=_prompt("Valeria speaks to camera."),
        )

        self.assertTrue(resolved["lip_sync_critical"])

    def test_a_silent_plan_is_left_alone(self):
        plan = {"mode": "ambient_only", "timing_anchor": "video"}
        # _prompt() always writes one spoken line, so the silent case removes it.
        silent = _prompt("Valeria looks at the camera.").replace(
            "Dialogue: <d>[Spanish] Hola.</d>. ", "",
        )

        self.assertEqual(
            reconcile_audio_plan_with_dialogue(plan, prompt=silent),
            plan,
        )

    def test_a_music_plan_is_left_alone(self):
        # generated_audio synthesizes its own speech, and music_driven is a
        # deliberate workflow whose audio drives everything: neither is a
        # contradiction to repair.
        for mode in ("music_driven", "generated_audio"):
            with self.subTest(mode=mode):
                plan = {"mode": mode}
                self.assertEqual(
                    reconcile_audio_plan_with_dialogue(
                        plan, prompt=_prompt("Valeria sings."),
                    ),
                    plan,
                )

    def test_the_render_mode_choice_reconciles_before_deciding(self):
        """The fix has to sit where the audio-to-video path is selected."""

        with open(
            os.path.join(_APP_DIR, "services", "director", "orchestrator.py"),
            encoding="utf-8",
        ) as handle:
            source = handle.read()

        start = source.index("def _choose_video_mode(")
        body = source[start:source.index('return "a2v"', start)]
        self.assertIn("reconcile_audio_plan_with_dialogue(", body)
        self.assertIn("shot.audio_plan.mode =", body)


class GestureFindingTests(unittest.TestCase):
    """Name what competes with the mouth, or a lip-sync note has nothing to change.

    Clip 13's prompt already told every speaker to lip-sync their lines, so three
    notes asking for exactly that came back as a 2-character edit, then 55, then 1,
    and the render never changed: the part that had to move was "While speaking,
    nodding slowly" on Valeria's single line, and nothing measured it.
    """

    def test_the_head_movement_on_a_line_is_named(self):
        diagnosis = diagnose_h3_clip_prompt(
            _prompt(
                "Valeria speaks to camera. While speaking, nodding slowly.",
            ),
            duration_seconds=7.29,
        )

        joined = " ".join(diagnosis["findings"])
        self.assertIn("nodding slowly", joined)
        self.assertIn("competes with the mouth", joined)

    def test_a_hand_gesture_is_not_reported(self):
        # A hand gesture does not stop anyone from forming words.
        diagnosis = diagnose_h3_clip_prompt(
            _prompt(
                "Valeria speaks to camera. While speaking, gesturing slightly.",
            ),
            duration_seconds=7.29,
        )

        self.assertNotIn("competes with the mouth", " ".join(diagnosis["findings"]))

    def test_the_finding_says_what_to_do_about_it(self):
        diagnosis = diagnose_h3_clip_prompt(
            _prompt("Valeria speaks to camera. While speaking, looking down."),
            duration_seconds=7.29,
        )

        joined = " ".join(diagnosis["findings"])
        self.assertIn("let the speaking mouth do the work", joined)
        self.assertIn("move the gesture to a beat where that person is silent", joined)


# From the real shot: three lines the plan wrote, and one the editor removed.
REVIEWED_LINES = [
    ("S2", "with a heavy sigh", "Me invadio una sensacion de obsolescencia."),
    ("S1", "calmly", "Es una reaccion muy natural."),
    ("S2", "earnestly", "Es que piensalo."),
]

# The plan's own ids were misaligned with the text, and it still lists a line the
# editor deleted -- both are exactly what the retention has to resolve.
PLAN_BEATS = [
    {"spoken_text": "Me invadio una sensacion de obsolescencia.",
     "delivery": "with a heavy sigh", "speaker_id": "(S1)"},
    {"spoken_text": "Es una reaccion muy natural.",
     "delivery": "calmly", "speaker_id": "(S2)"},
    {"spoken_text": "Es que piensalo.",
     "delivery": "earnestly", "speaker_id": "(S1)"},
    {"spoken_text": "A line the editor deleted.",
     "delivery": "flat", "speaker_id": "(S1)"},
]

REVIEWED_SUBJECTS = [
    {"character_id": "(S1)", "speaker_name": "Valeria",
     "visual_description": "Valeria, a woman in a navy blazer.",
     "position_or_relation": "foreground left"},
    {"character_id": "(S2)", "speaker_name": "Ricardo",
     "visual_description": "Ricardo, a man with grey hair and glasses.",
     "position_or_relation": "across the table"},
]


def _reviewed_prompt(lines, *, contract: bool = True) -> str:
    """A compiled prompt that came back through the editor."""

    body = " ".join(
        f"({speaker}) speaks {delivery}: <d>[Spanish] {text}</d>."
        for speaker, delivery, text in lines
    )
    tail = (
        "Only the tagged lines are spoken, once each in order. After the final "
        "tagged line, every character remains silent with their mouth closed; no "
        "invented dialogue, muttering, gibberish, speech-like vocalization, or "
        "background voice occurs. "
        if contract else ""
    )
    return (
        "subject_definitions: <Subject 1> (S1): Valeria, a woman in a navy blazer.\n"
        "<Subject 2> (S2): Ricardo, a man with grey hair and glasses.\n\n"
        "summary: [reference generation] Valeria speaks to Ricardo across a table.\n\n"
        "retention_analysis: Preserve the described identities and wardrobe.\n\n"
        "detailed_description: The target video keeps its style. [Shot 1] "
        f"{body} {tail}\n\n"
        "overall_soundscape: Natural ambience.\n\n"
        "non_diegetic_music: N/A\n"
    )


class SpeechPlanSurvivesTheEdit(unittest.TestCase):
    """Saving reviewed text must not cost the shot its speech plan.

    ``update_clip_prompt`` cleared the beat cache outright. That is the one
    input this compiler needs to rebuild a shot's dialogue and timing contract,
    and a reviewed compiled prompt is rendered verbatim, so nothing downstream
    restored it: the shot kept rendering with closed mouths however often the
    director note was rewritten.
    """

    def test_kept_lines_keep_their_plan_metadata(self):
        kept = retain_dialogue_beats(PLAN_BEATS, _reviewed_prompt(REVIEWED_LINES))

        self.assertEqual(
            [beat["delivery"] for beat in kept],
            ["with a heavy sigh", "calmly", "earnestly"],
        )

    def test_the_prompt_binding_wins_over_a_stale_plan_speaker(self):
        kept = retain_dialogue_beats(PLAN_BEATS, _reviewed_prompt(REVIEWED_LINES))

        self.assertEqual(
            [beat["speaker_id"] for beat in kept], ["(S2)", "(S1)", "(S2)"],
        )

    def test_a_line_the_editor_deleted_does_not_come_back(self):
        kept = retain_dialogue_beats(PLAN_BEATS, _reviewed_prompt(REVIEWED_LINES))

        self.assertNotIn(
            "A line the editor deleted.",
            [beat["spoken_text"] for beat in kept],
        )

    def test_a_rerun_override_drops_the_beat_of_a_line_it_removed(self):
        clip = {
            "video_prompt": _reviewed_prompt(REVIEWED_LINES),
            "_director_dialogue_beats": PLAN_BEATS,
        }

        kept = _retained_h3_beats(clip, _reviewed_prompt(REVIEWED_LINES[:2]))

        self.assertEqual(len(kept), 2)
        self.assertEqual(kept[-1]["spoken_text"], REVIEWED_LINES[1][2])

    def test_a_prompt_with_no_spoken_lines_keeps_no_beats(self):
        self.assertEqual(
            retain_dialogue_beats(PLAN_BEATS, "subject_definitions: none."), [],
        )


class ReviewedPromptKeepsItsShape(unittest.TestCase):
    """A reviewed prompt is rendered as it was saved, not wrapped again.

    Rebuilding it wrapped a SECOND six-field Context-IR prompt around the first:
    one real shot reached 17,331 characters with its lines present twice and its
    speaker no longer resolvable. The text the director approved is authoritative.
    """

    def _compile(self, prompt: str, beats) -> dict:
        plan = {
            "video_prompt": prompt,
            "_director_prompt_user_edited": True,
            "_director_h3_source_prompt": prompt,
            "_director_h3_compiled_prompt": "",
            "_director_dialogue_beats": beats,
            "_director_subjects_on_screen": REVIEWED_SUBJECTS,
            "_director_duration_sec": 7.29,
            "_director_h3_prompt_mode": "ref2va",
            "_director_h3_model_family": "ref2va",
            "_director_speaker_registry": {},
            "_director_audio_plan": {"mode": "dialogue_driven", "lip_sync_critical": True},
        }

        compiled = compile_h3_clip_plans(
            [plan], prompt_modes=["ref2va"], durations=[7.29],
        )

        return dict(compiled[0])

    def test_the_reviewed_text_reaches_the_model_unchanged(self):
        prompt = _reviewed_prompt(REVIEWED_LINES, contract=False)
        beats = retain_dialogue_beats(PLAN_BEATS, prompt)

        result = self._compile(prompt, beats)

        self.assertEqual(result["video_prompt"], prompt)
        self.assertEqual(
            h3_dialogue_blocks(result["video_prompt"]),
            h3_dialogue_blocks(prompt),
        )

    def test_the_six_field_prompt_is_not_wrapped_twice(self):
        prompt = _reviewed_prompt(REVIEWED_LINES)
        beats = retain_dialogue_beats(PLAN_BEATS, prompt)

        result = self._compile(prompt, beats)

        self.assertEqual(result["video_prompt"].count("subject_definitions:"), 1)
        self.assertEqual(result["video_prompt"].count("[Shot 1]"), 1)


class SavedPromptKeepsTheSpeechPlan(unittest.TestCase):
    """The pipeline write path, not just the helper."""

    def setUp(self):
        self.originals = {
            "threads": pipeline._pipeline_threads,
            "child_jobs": pipeline._pipeline_child_jobs,
            "starting": pipeline._pipeline_starting,
            "operations": pipeline._pipeline_operations,
            "deleting": pipeline._pipeline_deleting,
        }
        self.temp_dir = tempfile.TemporaryDirectory()
        pipeline._pipeline_threads = {}
        pipeline._pipeline_child_jobs = {}
        pipeline._pipeline_starting = set()
        pipeline._pipeline_operations = set()
        pipeline._pipeline_deleting = set()

    def tearDown(self):
        pipeline._pipeline_threads = self.originals["threads"]
        pipeline._pipeline_child_jobs = self.originals["child_jobs"]
        pipeline._pipeline_starting = self.originals["starting"]
        pipeline._pipeline_operations = self.originals["operations"]
        pipeline._pipeline_deleting = self.originals["deleting"]
        self.temp_dir.cleanup()

    def _save(self, pid: str, prompt: str) -> None:
        path = os.path.join(self.temp_dir.name, f"_director_pipeline_{pid}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(
                {"clips": [{
                    "video_prompt": prompt,
                    "_director_dialogue_beats": PLAN_BEATS,
                }]},
                handle,
            )

    def test_the_saved_edit_keeps_the_beats_of_the_lines_it_kept(self):
        pid = "pipe-speech"
        prompt = _reviewed_prompt(REVIEWED_LINES)
        self._save(pid, prompt)
        edited = prompt.replace(
            " (S2) speaks earnestly: <d>[Spanish] Es que piensalo.</d>.", "",
        )

        self.assertTrue(
            pipeline.update_clip_prompt(
                self.temp_dir.name, pid, 0, {"video_prompt": edited},
            )
        )

        saved = pipeline.load_pipeline_state(self.temp_dir.name, pid)
        beats = saved["clips"][0]["_director_dialogue_beats"]
        self.assertEqual(
            [beat["spoken_text"] for beat in beats],
            [line[2] for line in REVIEWED_LINES[:2]],
        )
        self.assertEqual(
            [beat["speaker_id"] for beat in beats], ["(S2)", "(S1)"],
        )


class SpeakerAliasesShareOneLabel(unittest.TestCase):
    """One label per participant, not one per key.

    A saved registry can carry a number per key: a two-person project stored
    ``director_identity_s1 -> (S1)``, ``(s1) -> (S2)``, ``(s2) -> (S3)`` and
    ``director_identity_s2 -> (S4)``. Every line then reached the model with the
    next participant's face -- and because a beat whose key did not resolve was
    numbered by its position in the shot, the third line of a 2-person shot
    became ``(S3)``. Measured on one real project: 135 of its 177 clips had a
    line bound to a speaker the cast does not contain.
    """

    CORRUPT = {
        "director_identity_s1": {"stable_id": "(S1)", "speaker_name": "(S1)"},
        "(s1)": {"stable_id": "(S2)", "speaker_name": "(S1)"},
        "(s2)": {"stable_id": "(S3)", "speaker_name": "(S2)"},
        "director_identity_s2": {"stable_id": "(S4)", "speaker_name": "(S2)"},
    }

    def _registry(self, beats, subjects):
        return _build_stable_speaker_registry([{
            "_director_dialogue_beats": beats,
            "_director_subjects_on_screen": subjects,
            "_director_speaker_registry": self.CORRUPT,
        }])

    def test_the_aliases_of_one_participant_collapse_to_one_label(self):
        registry = self._registry([], [])

        self.assertEqual(registry["director_identity_s1"]["stable_id"], "(S1)")
        self.assertEqual(registry["(s1)"]["stable_id"], "(S1)")
        self.assertEqual(registry["(s2)"]["stable_id"], "(S2)")
        self.assertEqual(registry["director_identity_s2"]["stable_id"], "(S2)")

    def test_every_beat_resolves_to_the_speaker_its_plan_named(self):
        beats = [
            {"spoken_text": "Hace unos dias me paso algo.", "speaker_id": "(S1)"},
            {"spoken_text": "A ver, que te paso?", "speaker_id": "(S2)"},
            {"spoken_text": "Pues tuve una crisis.", "speaker_id": "(S1)"},
        ]

        registry = self._registry(beats, [])

        resolved = [
            _speaker_registry_entry(registry, beat["speaker_id"])[0]
            for beat in beats
        ]
        self.assertEqual(resolved, ["(S1)", "(S2)", "(S1)"])

    def test_a_beat_key_that_spells_out_a_label_is_that_participant(self):
        beats = [{"spoken_text": "Hola.", "speaker_id": "(s2)"}]

        registry = self._registry(beats, [])

        self.assertEqual(
            _speaker_registry_entry(registry, "(s2)")[0], "(S2)",
        )


class ShotReferencesAreNotDeclarations(unittest.TestCase):
    """A retention row names the shot it keeps; that is not a second shot.

    ``<Subject 2> (appears in [Shot 1]): fully_preserved`` is how the format
    records which shot keeps a reference. Counting those made a one-shot body read
    as a three-shot body, so the assistant was told to fix a defect that did not
    exist, rewrote the dialogue lines while trying, and had its answer refused --
    the loop that looked like "the assistant answers with an error and the shot
    never changes".
    """

    REVIEWED = (
        "subject_definitions: <Subject 1> (S1): Valeria.\n\n"
        "summary: [reference generation] Valeria speaks.\n\n"
        "retention_analysis: <Subject 2> (appears in [Shot 1]): fully_preserved. "
        "<Subject 1> (appears in [Shot 1]): fully_preserved.\n\n"
        "detailed_description: The style holds. [Shot 1] Valeria speaks to "
        "camera. <d>[Spanish] Hola.</d>\n\n"
        "overall_soundscape: Room tone.\n\n"
        "non_diegetic_music: N/A\n"
    )

    def test_a_retention_reference_is_not_counted_as_a_shot(self):
        self.assertEqual(_declared_shot_numbers(self.REVIEWED), [1])

    def test_the_measurement_does_not_call_two_references_two_shots(self):
        diagnosis = diagnose_h3_clip_prompt(self.REVIEWED, duration_seconds=7.29)

        self.assertEqual(diagnosis["shots"], [1])
        joined = " ".join(diagnosis["findings"])
        self.assertNotIn("declares 3 shot(s)", joined)
        self.assertNotIn("ONE continuous shot", joined)

    def test_the_normalizer_keeps_a_reference_and_drops_a_later_shot(self):
        fixed = _single_shot_body(
            "She speaks. (appears in [Shot 1]) [Shot 1] The style holds. [Shot 2] Later."
        )

        self.assertIn("(appears in [Shot 1])", fixed)
        self.assertNotIn("[Shot 2]", fixed)

    def test_a_repeated_marker_is_named_as_a_repeat_not_as_two_shots(self):
        repeated = self.REVIEWED.replace(
            "The style holds. [Shot 1]", "The style holds. [Shot 1] [Shot 1]",
        )

        joined = " ".join(
            diagnose_h3_clip_prompt(repeated, duration_seconds=7.29)["findings"],
        )

        self.assertIn("repeats the", joined)
        self.assertNotIn("ONE continuous shot", joined)


class RefusalNamesTheExactLines(unittest.TestCase):
    """A refused rewrite is told which lines it has to copy."""

    def test_the_retry_sees_the_lines_it_must_copy(self):
        nudge = _revise_problem_nudge(
            ["The rewrite changed the spoken words."],
            ["<d>[Spanish] Hola.</d>", "<d>[Spanish] Adios.</d>"],
        )

        self.assertIn("The lines to copy exactly, in order:", nudge)
        self.assertIn("1. <d>[Spanish] Hola.</d>", nudge)
        self.assertIn("2. <d>[Spanish] Adios.</d>", nudge)

    def test_a_nudge_without_lines_still_explains_itself(self):
        nudge = _revise_problem_nudge(["The rewrite changed the spoken words."])

        self.assertIn("The rewrite changed the spoken words.", nudge)
        self.assertNotIn("copy exactly, in order", nudge)


if __name__ == "__main__":
    unittest.main()
