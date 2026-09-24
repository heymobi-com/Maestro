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
import re
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
    _ensure_h3_prompt_anchors,
    _h3_anchor_present,
    _single_shot_body,
    _speaker_registry_entry,
    compile_h3_clip_plans,
    diagnose_h3_clip_prompt,
    h3_clip_spans,
    h3_clip_text,
    h3_dialogue_blocks,
    h3_ensure_project_base,
    h3_frozen_problems,
    h3_proposal_warnings,
    h3_shared_line_speaker_problems,
    h3_shared_project_phrases,
    h3_subject_binding_problems,
    h3_unresolved_speaker_cue_problems,
    reconcile_audio_plan_with_dialogue,
    retain_dialogue_beats,
    review_h3_revision,
)
from services.director_pipeline import (  # noqa: E402
    _NO_OP_CHANGE_CHARS,
    _NO_OP_CHANGE_WORDS,
    _apply_revision_edits,
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

    def test_decorated_markers_are_still_markers(self):
        # The refusals measured in a real session -- "expected one overall_soundscape
        # field, found 3" and "The rewrite has 12 spoken line(s) instead of 3" -- were
        # answers like these: the markers were written in bold, were not recognized as
        # markers, and the whole answer (analysis, quoted fields and prompt) was then
        # validated as ONE prompt.
        for decorated in (
            "**ANALYSIS:**",
            "**ANALYSIS**:",
            "## ANALYSIS:",
            "- **ANALYSIS**:",
            "`ANALYSIS`:",
        ):
            parts = _parse_revision_envelope(
                f"{decorated} the blocking is the problem.\n"
                "QUESTION: NONE\n"
                f"FIXED_PROMPT: {FIXED_PROMPT}",
            )
            with self.subTest(marker=decorated):
                self.assertIn("blocking is the problem", parts["analysis"])
                self.assertEqual(parts["prompt"], FIXED_PROMPT.strip())

    def test_prose_before_the_prompt_is_read_as_the_analysis(self):
        parts = _parse_revision_envelope(
            f"The blocking repeats what the previous shot did.\n\n{FIXED_PROMPT}",
        )

        self.assertIn("repeats what the previous shot", parts["analysis"])
        self.assertEqual(parts["prompt"], FIXED_PROMPT.strip())

    def test_alternatives_collapse_into_the_first_prompt(self):
        # "Option A" and "Option B" under one marker put three prompts in one
        # candidate, which the contract reads as three overall_soundscape fields and
        # refuses. The first prompt is the one the note asked for.
        parts = _parse_revision_envelope(
            "ANALYSIS: X\nQUESTION: NONE\n"
            f"FIXED_PROMPT: Opcion A:\n{FIXED_PROMPT}\nOpcion B:\n{FIXED_PROMPT}",
        )

        self.assertEqual(parts["prompt"].count("subject_definitions:"), 1)
        self.assertEqual(parts["prompt"].count("overall_soundscape:"), 1)
        self.assertNotIn("Opcion A", parts["prompt"])

    def test_a_duplicate_field_error_names_where_the_duplicates_are(self):
        # "found 2" alone told the assistant nothing about what to delete.
        problems = review_h3_revision(FIXED_PROMPT, FIXED_PROMPT * 2)

        self.assertTrue(
            any("expected one subject_definitions field, found 2 (lines 1," in problem
                for problem in problems),
            problems,
        )

    def test_options_come_back_as_choices(self):
        # Numbered, bulleted or plain: the director picks one and sends it.
        parts = _parse_revision_envelope(
            "ANALYSIS: the ledger owns the framing, not this shot.\n"
            "QUESTION: NONE\n"
            "OPTIONS:\n"
            "1. Drop the shot sentence that competes with the ledger.\n"
            "- Move the clip's audio window.\n"
            "FIXED_PROMPT: NONE\n",
        )

        self.assertEqual(parts["options"], [
            "Drop the shot sentence that competes with the ledger.",
            "Move the clip's audio window.",
        ])
        self.assertEqual(parts["prompt"], "")
        self.assertEqual(parts["question"], "")

    def test_the_prompt_is_kept_when_the_answer_repeats_its_tail(self):
        # The refusal measured in the logs right after the marker fix: "expected one
        # overall_soundscape field, found 2 (lines 10, 50)". The model wrote the whole
        # prompt and then repeated the last fields, which is an echo of what it had
        # written, not a second prompt.
        echoed = (
            FIXED_PROMPT
            + "\noverall_soundscape: Natural ambience.\n\nnon_diegetic_music: N/A\n"
        )

        parts = _parse_revision_envelope(f"ANALYSIS: X\nFIXED_PROMPT: {echoed}")

        self.assertEqual(parts["prompt"].count("overall_soundscape:"), 1)
        self.assertEqual(parts["prompt"].count("non_diegetic_music:"), 1)
        self.assertEqual(parts["prompt"], FIXED_PROMPT.strip())

    def test_the_candidate_that_differs_is_the_rewrite(self):
        # Two prompts in one answer are a menu, and the rewrite is not the first one by
        # default: it is the one that differs from what is on disk.
        revised = FIXED_PROMPT.replace(
            "Natural ambience", "Room tone with a distant hum",
        )

        parts = _parse_revision_envelope(
            f"ANALYSIS: X\nFIXED_PROMPT: {FIXED_PROMPT}\nOpcion B:\n{revised}",
            FIXED_PROMPT,
        )

        self.assertIn("Room tone with a distant hum", parts["prompt"])
        self.assertEqual(parts["prompt"].count("subject_definitions:"), 1)
        self.assertNotIn("Opcion B", parts["prompt"])

    def test_edits_are_read_as_find_and_set_pairs(self):
        # The cheap answer: point at a sentence and say what it becomes, instead of
        # re-typing six thousand characters for a two-sentence change.
        parts = _parse_revision_envelope(
            "ANALYSIS: two framing statements compete.\n"
            "EDITS:\n"
            "1. FIND: By the final beat, the focus tightens on Ricardo's eyes.\n"
            "   SET: The camera holds a steady medium shot of Ricardo.\n"
            "FIXED_PROMPT: NONE\n",
        )

        self.assertEqual(parts["edits"], [{
            "find": "By the final beat, the focus tightens on Ricardo's eyes.",
            "set": "The camera holds a steady medium shot of Ricardo.",
        }])
        self.assertEqual(parts["prompt"], "")

    def test_two_choices_written_on_one_line_are_still_two_choices(self):
        # Measured: the model answered "OPTIONS: 1. cortar el plano antes del giro.
        # 2. anadir un inserto de la mano" on a single line and the window showed ONE
        # chip whose text was both proposals. A chip carrying two instructions executes
        # neither, which is what the director reported.
        one_line = _parse_revision_envelope(
            "ANALYSIS: the closing beat is missing.\n"
            "OPTIONS: 1. Cortar el plano antes del giro. 2. Anadir un inserto de la mano.\n",
        )

        self.assertEqual(one_line["options"], [
            "Cortar el plano antes del giro.",
            "Anadir un inserto de la mano.",
        ])

    def test_choices_piped_on_one_line_and_three_of_them_are_split_too(self):
        piped = _parse_revision_envelope(
            "ANALYSIS: x.\nOPTIONS:\nCortar el plano | Anadir un inserto\n",
        )
        three = _parse_revision_envelope(
            "ANALYSIS: x.\nOPTIONS:\n1. Cortar el plano. 2. Insertar la mano. "
            "3. Cambiar el encuadre al rostro.\n",
        )

        self.assertEqual(piped["options"], ["Cortar el plano", "Anadir un inserto"])
        self.assertEqual(len(three["options"]), 3)

    def test_one_choice_stays_one_because_a_duration_is_not_a_numbering(self):
        single = _parse_revision_envelope(
            "ANALYSIS: x.\nOPTIONS:\n1. Recortar el plano en 1.5 s para que entre el giro.\n",
        )

        self.assertEqual(single["options"], [
            "Recortar el plano en 1.5 s para que entre el giro.",
        ])


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

    def test_the_turn_hands_the_assistant_the_shot_text_and_the_frozen_project_text(self):
        # Sending the whole prompt invited the model to rewrite it, which is where the
        # damage came from; the frozen project text goes along as read-only context.
        self.assertIn("THIS SHOT'S TEXT", self.pipeline)
        self.assertIn("frozen: it must come back", self.pipeline)
        self.assertIn("h3_clip_text(prompt, frozen)", self.pipeline)

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

    def test_the_correction_may_use_a_smaller_model_and_keep_it_loaded(self):
        # Each correction used to pay for the planning model and for reloading it: the
        # 26B model is 16.8 GB and the idle timer evicted it after 60 seconds. The
        # generation paths release the LLM explicitly when they need the VRAM, so the
        # timer is a safety net and the model is a setting.
        self.assertIn("revision_llm_model_id", self.pipeline)
        self.assertIn("set_idle_timeout(", self.pipeline)
        service = open(
            os.path.join(_APP_DIR, "services", "llm_service.py"), encoding="utf-8",
        ).read()
        self.assertIn("_IDLE_TIMEOUT_DEFAULT: float = 600.0", service)
        self.assertIn("def set_idle_timeout(", service)
        self.assertIn("MAESTRO_LLM_IDLE_SECONDS", service)

    def test_that_smaller_model_is_a_setting_the_director_can_actually_find(self):
        # The pipeline read a key that nothing else knew about: it was not in
        # SETTING_KEYS, so no endpoint saved it, and the settings panel never rendered
        # it. The feature existed and the director could not reach it.
        from services.studio_enhancement import SETTING_KEYS, captured_settings

        self.assertIn("revision_llm_model_id", SETTING_KEYS)
        self.assertEqual(captured_settings({})["revision_llm_model_id"], "")
        self.assertIn('"revision_llm_model_id": services.get', self.launch)
        # The settings endpoint persists every accepted key, so being accepted is
        # enough to round-trip through wgp_config.json.
        block = self.launch.split("ALLOWED_KEYS = {")[-1].split("}")[0]
        self.assertIn('"revision_llm_model_id"', block)

    def test_the_setting_is_visible_where_the_other_llms_are_chosen(self):
        panel_path = os.path.join(
            _ROOT, "ui", "src", "components", "SettingsDrawer", "ServicesSettingsPanel.tsx",
        )
        with open(panel_path, encoding="utf-8") as handle:
            panel = handle.read()
        self.assertIn("revision_llm_model_id", panel)
        # Behind the Experimental toggle the option might as well not exist.
        self.assertLess(
            panel.index("revision_llm_model_id"),
            panel.index("show_experimental"),
        )
        with open(os.path.join(_ROOT, "ui", "src", "types", "index.ts"), encoding="utf-8") as handle:
            self.assertIn("revision_llm_model_id: string", handle.read())

    def test_an_option_chip_runs_the_correction_it_carries(self):
        # The chip only called setFixNote: it filled a box and waited for a second
        # click, so "it offers options and then does nothing" was literally true.
        with open(
            os.path.join(_ROOT, "ui", "src", "components", "DirectorDashboard", "DirectorDashboard.tsx"),
            encoding="utf-8",
        ) as handle:
            dashboard = handle.read()

        self.assertIn("void runFixWithAi(option)", dashboard)
        self.assertIn("const runFixWithAi = async (overrideNote?: string)", dashboard)
        # Passing the handler itself would hand the click's MouseEvent to the note.
        self.assertNotIn("onClick={runFixWithAi}", dashboard)
        self.assertIn("onClick={() => void runFixWithAi()}", dashboard)

    def test_the_shape_of_the_answer_is_recorded(self):
        # Nothing logged how many markers or choices an answer carried, so a collapsed
        # chip could only be guessed at.
        self.assertIn("markers=", self.pipeline)
        self.assertIn("options={len(parsed['options'])}", self.pipeline)

    def test_the_assistant_is_required_to_offer_options(self):
        # "The programming is useless" was fair: the envelope had no place for a
        # choice, so a note the prompt cannot answer came back as a bare error.
        self.assertIn("OPTIONS:", self.pipeline)
        self.assertIn("Never leave the director with nothing to choose", self.pipeline)
        self.assertIn("an error with no options is not an answer", self.pipeline)


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

    def _save(self, prompt: str = PROBLEM_PROMPT):
        state = {
            "pipeline_id": self.pid,
            "status": "completed",
            "video_model": "minimax_h3_ref2va_fused_turbo",
            "clips": [{
                "index": 0,
                "video_prompt": prompt,
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

    def test_a_no_op_without_options_still_says_what_to_do(self):
        self._save()
        self._stub([self._answer(PROBLEM_PROMPT), self._answer(PROBLEM_PROMPT)])

        result = pipeline.revise_clip_prompt(self.out_dir, self.pid, 0, "smooth it out")

        self.assertTrue(result["errors"])
        self.assertIn(
            "the camera, the blocking or the acting words have to move",
            result["errors"][-1],
        )

    def test_options_are_an_answer_when_the_prompt_cannot_fix_it(self):
        # "Then why do I want an AI assistant if not to correct?" -- a note the
        # prompt cannot answer used to come back as an error and nothing else.
        self._save()
        self._stub([
            "ANALYSIS: the lighting arc belongs to the project ledger, not to this "
            "shot, so no rewrite of this prompt removes the conflict.\n"
            "QUESTION: NONE\n"
            "OPTIONS:\n"
            "1. Move the clip's audio window so the first line starts inside it.\n"
            "2. Render this shot without lip-sync and cover it with a hand close-up.\n"
            "FIXED_PROMPT: NONE",
        ])

        result = pipeline.revise_clip_prompt(
            self.out_dir, self.pid, 0, "less abrupt transitions",
        )

        self.assertFalse(result["rewritten"])
        self.assertEqual(result["errors"], [], "options are an answer, not a refusal")
        self.assertEqual(len(result["options"]), 2)
        self.assertIn("Elige una opción", result["note"])
        self.assertEqual(result["video_prompt"], "")

    def test_the_prompt_back_unchanged_with_options_is_not_an_error(self):
        self._save()
        self._stub([
            "ANALYSIS: the ledger owns the framing.\n"
            "OPTIONS:\n"
            "1. Trim the sentence in the shot that competes with the ledger.\n"
            "2. Split the shot in two.\n"
            f"FIXED_PROMPT: {PROBLEM_PROMPT}",
        ])

        result = pipeline.revise_clip_prompt(
            self.out_dir, self.pid, 0, "less abrupt transitions",
        )

        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["options"]), 2)
        self.assertIn("no encontró un cambio", result["note"])

    def test_a_local_note_comes_back_as_an_edit_applied_to_the_saved_prompt(self):
        # Measured cost of the alternative: re-typing the prompt was 1,675 generated
        # tokens per answer (70-110 seconds with the 26B model in use) and came back
        # echoed twice, which the contract refused.
        self._save(FIXED_PROMPT)
        self._stub([
            "ANALYSIS: the desk sentence says nothing about the camera yet.\n"
            "QUESTION: NONE\n"
            "EDITS:\n"
            "FIND: Valeria and Ricardo sit facing each other across the desk, "
            "both in frame.\n"
            "SET: Valeria and Ricardo sit facing each other across the desk, "
            "both in frame, the camera easing in slowly.\n"
            "FIXED_PROMPT: NONE",
        ])

        result = pipeline.revise_clip_prompt(
            self.out_dir, self.pid, 0, "ease the camera in",
        )

        self.assertTrue(result["rewritten"])
        self.assertEqual(len(result["edits"]), 1)
        self.assertIn("the camera easing in slowly", result["video_prompt"])
        # The edit moved one sentence: the fields and the spoken lines are untouched.
        self.assertEqual(result["video_prompt"].count("subject_definitions:"), 1)
        self.assertEqual(
            h3_dialogue_blocks(result["video_prompt"]),
            h3_dialogue_blocks(FIXED_PROMPT),
        )

    def test_an_edit_that_does_not_match_the_prompt_is_refused_with_the_reason(self):
        self._save(FIXED_PROMPT)
        self._stub([
            "ANALYSIS: X\nEDITS:\n"
            "FIND: a sentence the model imagined\n"
            "SET: something else\n"
            "FIXED_PROMPT: NONE",
        ])

        result = pipeline.revise_clip_prompt(self.out_dir, self.pid, 0, "fix it")

        self.assertFalse(result["rewritten"])
        self.assertIn("is not in the current prompt", result["errors"][0])

    def test_an_ambiguous_edit_is_refused(self):
        self._save(FIXED_PROMPT)
        self._stub([
            "ANALYSIS: X\nEDITS:\nFIND: Valeria\nSET: Ricardo\n"
            "FIXED_PROMPT: NONE",
        ])

        result = pipeline.revise_clip_prompt(self.out_dir, self.pid, 0, "fix it")

        self.assertFalse(result["rewritten"])
        self.assertIn("is ambiguous", result["errors"][0])

    def test_the_proposal_carries_warnings_about_damaged_words(self):
        self._save(FIXED_PROMPT)
        self._stub([
            "ANALYSIS: X\nQUESTION: NONE\nEDITS:\n"
            "FIND: Valeria and Ricardo sit facing each other across the desk, "
            "both in frame.\n"
            "SET: Valeria and Ricardo sit facing each other acros the desk, "
            "both in frame, the camera easing in slowly.\n"
            "FIXED_PROMPT: NONE",
        ])

        result = pipeline.revise_clip_prompt(
            self.out_dir, self.pid, 0, "ease the camera in",
        )

        self.assertTrue(result["rewritten"])
        self.assertEqual(
            result["warnings"],
            ["posible errata: 'acros' donde el prompt dice 'across'"],
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


class ReviewedPromptGetsItsCanonicalAnchor(unittest.TestCase):
    """A reviewed prompt that lost a canonical anchor is completed, not refused.

    The compiler writes "Canonical identity and world: ..." into every body it
    builds, but a reviewed prompt is rendered verbatim, so the reviewed prompt was
    the one place the sentence could never be inserted -- while the contract still
    demanded it. The correction assistant was then refused for not inventing the
    sentence ("missing canonical identity/world context: The library/loft
    background"), which is a dead end rather than a correction.
    """

    ENVIRONMENT = "The library/loft background"

    def _compile(self, prompt: str, beats, environment: str = "") -> dict:
        plan = {
            "video_prompt": prompt,
            "_director_prompt_user_edited": True,
            "_director_h3_source_prompt": prompt,
            "_director_h3_compiled_prompt": "",
            "_director_dialogue_beats": beats,
            "_director_subjects_on_screen": REVIEWED_SUBJECTS,
            "_director_environment": environment,
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

    def test_the_rendered_prompt_carries_the_anchor_the_plan_requires(self):
        prompt = _reviewed_prompt(REVIEWED_LINES)
        beats = retain_dialogue_beats(PLAN_BEATS, prompt)

        rendered = self._compile(prompt, beats, self.ENVIRONMENT)["video_prompt"]

        self.assertTrue(_h3_anchor_present(self.ENVIRONMENT, rendered))
        # The reviewed words are still authoritative: one prompt, one shot, and
        # the spoken lines untouched.
        self.assertEqual(rendered.count("subject_definitions:"), 1)
        self.assertEqual(rendered.count("[Shot 1]"), 1)
        self.assertEqual(h3_dialogue_blocks(rendered), h3_dialogue_blocks(prompt))

    def test_the_anchor_lands_inside_the_body_field(self):
        prompt = _reviewed_prompt(REVIEWED_LINES)
        beats = retain_dialogue_beats(PLAN_BEATS, prompt)

        rendered = self._compile(prompt, beats, self.ENVIRONMENT)["video_prompt"]

        anchor = rendered.index("Canonical identity and world:")
        self.assertLess(rendered.index("detailed_description:"), anchor)
        # Appended inside the body, so the next field is still found at a line
        # start: a sentence glued to its label hides the field entirely.
        self.assertLess(anchor, rendered.index("overall_soundscape:"))
        self.assertRegex(rendered, r"(?m)^overall_soundscape:")

    def test_an_anchor_that_is_already_present_is_not_added_again(self):
        prompt = _reviewed_prompt(REVIEWED_LINES)

        once = _ensure_h3_prompt_anchors(prompt, [self.ENVIRONMENT])
        twice = _ensure_h3_prompt_anchors(once, [self.ENVIRONMENT])

        self.assertEqual(twice.count("Canonical identity and world:"), 1)
        self.assertEqual(once, twice)

    def test_a_rewrite_is_not_blamed_for_an_anchor_the_original_lacked(self):
        # Compiling the rewrite is what refused it, so no answer could ever be
        # accepted for this shot: the loop the user reported as "the assistant
        # answers with an error and nothing changes".
        original = _reviewed_prompt(REVIEWED_LINES)
        revised = original.replace("Natural ambience", "Room tone")

        problems = review_h3_revision(
            original, revised, duration_seconds=7.29,
            subjects=REVIEWED_SUBJECTS, context_anchors=[self.ENVIRONMENT],
        )

        self.assertEqual(problems, [])

    def test_a_rewrite_that_drops_an_anchor_is_still_refused(self):
        original = _ensure_h3_prompt_anchors(
            _reviewed_prompt(REVIEWED_LINES), [self.ENVIRONMENT],
        )
        revised = original.replace(
            f"Canonical identity and world: {self.ENVIRONMENT}.", "",
        )

        problems = review_h3_revision(
            original, revised, duration_seconds=7.29,
            subjects=REVIEWED_SUBJECTS, context_anchors=[self.ENVIRONMENT],
        )

        self.assertEqual(
            problems, [f"missing canonical identity/world context: {self.ENVIRONMENT}"],
        )


class ProposalDamageTests(unittest.TestCase):
    """The damage a re-typed prompt does, which the contract cannot see.

    The gate protects the spoken lines, the fields, the shots and the anchors. The rest of
    the prose is free, and that is where a re-typed prompt gets hurt silently. Measured on
    one real proposal, against a saved prompt that was clean in all five places:
    "Vestuario" came back as "Vestología", "focus tightens" as "focus tights", "jeans
    oscuros" as "jeans oscamericanos", the speaker rule "(S1) y (S2)" as "(S1) y (2)", and
    a stretch of the dialogue contract as "i/s/a/f/d...rced/m/b/c/u/t/i/n/g".
    """

    def test_a_mangled_stretch_of_text_is_refused(self):
        problems = review_h3_revision(
            FIXED_PROMPT,
            FIXED_PROMPT.replace(
                "Valeria and Ricardo sit facing each other",
                "i/s/a/f/d rced/m/b/c/u/t/i/n/g Valeria and Ricardo sit facing each other",
            ),
        )

        self.assertTrue(
            any("is not prose" in problem for problem in problems), problems,
        )

    def test_editorial_text_in_the_prompt_is_refused(self):
        problems = review_h3_revision(
            FIXED_PROMPT,
            FIXED_PROMPT.replace(
                "Valeria and Ricardo sit facing each other",
                "The current prompt contains redundant descriptions. "
                "Valeria and Ricardo sit facing each other",
            ),
        )

        self.assertTrue(
            any("editorial text" in problem for problem in problems), problems,
        )

    def test_a_mangled_word_is_warned_rather_than_refused(self):
        proposal = FIXED_PROMPT.replace("across the desk", "acros the desk")

        # Not a refusal: a correction may introduce a word. It just may not do it in
        # silence, six thousand characters into a diff.
        self.assertEqual(review_h3_revision(FIXED_PROMPT, proposal), [])
        self.assertEqual(
            h3_proposal_warnings(FIXED_PROMPT, proposal),
            ["posible errata: 'acros' donde el prompt dice 'across'"],
        )

    def test_a_word_rewritten_on_purpose_is_not_warned_about(self):
        proposal = FIXED_PROMPT.replace(
            "Valeria and Ricardo sit facing each other across the desk, both in frame.",
            "Valeria and Ricardo face each other over the desk, the camera easing in slowly.",
        )

        self.assertEqual(h3_proposal_warnings(FIXED_PROMPT, proposal), [])

    def test_a_clean_edit_has_no_warnings_and_no_refusals(self):
        proposal = FIXED_PROMPT.replace(
            "Valeria and Ricardo sit facing each other across the desk, both in frame.",
            "Valeria and Ricardo sit facing each other across the desk, both in frame, "
            "the camera easing in slowly.",
        )

        self.assertEqual(review_h3_revision(FIXED_PROMPT, proposal), [])
        self.assertEqual(h3_proposal_warnings(FIXED_PROMPT, proposal), [])


class ProjectTextIsSharedTests(unittest.TestCase):
    """The text every clip of a project carries is not any shot's to edit.

    Measured on a real project of 177 clips: the rules, the subject lock, the wardrobe, the
    lighting arc and the specs are the same sentences in every clip -- 2,770 characters of
    one 6,184-character prompt, while the direction that moves the scene is 812. That text
    is where every case of damage was found, and 110 of the 177 clips were missing it
    entirely while their state held it for all of them.
    """

    PROJECT_CONTEXT = (
        "RESTRICCIONES GLOBALES DEL PROYECTO (crítico, no negociable):\n"
        "- EXACTAMENTE DOS speaker IDs existen en todo el proyecto: (S1) y (S2).\n"
        "- Estudio tipo loft íntimo — mesa de madera, luz cálida lateral, fondo desenfocado."
    )

    def _prompt(self, base: bool = True, direction: str = "a steady medium shot") -> str:
        context = (
            "Project context (the whole film; do not perform its progression inside "
            f"this shot): {self.PROJECT_CONTEXT}"
        ) if base else ""
        return (
            "subject_definitions: <Subject 1> (S1): Ricardo.\n\n"
            "summary: [reference generation] Opening composition.\n\n"
            "retention_analysis: Preserve the described identities.\n\n"
            "detailed_description: The target video maintains the requested visual style. "
            f"[Shot 1] Valeria speaks. {direction}. {context} "
            "Dialogue: (S1) speaks: <d>[Spanish] Hola.</d>.\n\n"
            "overall_soundscape: Natural ambience.\n\n"
            "non_diegetic_music: N/A"
        )

    def test_a_shared_sentence_cannot_be_changed_by_one_shot(self):
        original = self._prompt()
        frozen = [self.PROJECT_CONTEXT]

        problems = h3_frozen_problems(
            original,
            original.replace("luz cálida lateral", "luz fría central"),
            frozen,
        )

        self.assertEqual(len(problems), 1)
        self.assertIn("project's shared text", problems[0])

    def test_the_shot_direction_is_free_to_change(self):
        original = self._prompt()

        problems = h3_frozen_problems(
            original,
            original.replace("a steady medium shot", "a slow push into a close-up"),
            [self.PROJECT_CONTEXT],
        )

        self.assertEqual(problems, [])

    def test_a_sentence_most_clips_share_is_recognised(self):
        prompts = [self._prompt(direction=f"direction number {n}") for n in range(6)]

        shared = h3_shared_project_phrases(prompts, minimum_share=0.5)

        joined = " ".join(shared)
        self.assertIn("RESTRICCIONES GLOBALES DEL PROYECTO", joined)
        self.assertNotIn("direction number 3", joined)

    def test_the_project_base_is_added_once_and_never_twice(self):
        without = self._prompt(base=False)

        added, changed = h3_ensure_project_base(without, self.PROJECT_CONTEXT)
        again, changed_again = h3_ensure_project_base(added, self.PROJECT_CONTEXT)

        self.assertTrue(changed)
        self.assertFalse(changed_again, "assembling twice must not duplicate the block")
        self.assertEqual(again, added)
        self.assertIn("RESTRICCIONES GLOBALES DEL PROYECTO", added)
        # Nothing else moves: the fields stay and the spoken lines stay byte for byte.
        self.assertEqual(h3_dialogue_blocks(added), h3_dialogue_blocks(without))
        self.assertEqual(added.count("subject_definitions:"), 1)
        self.assertEqual(added.count("non_diegetic_music:"), 1)

    def test_a_prompt_that_already_has_a_base_is_left_alone(self):
        with_base = self._prompt()

        kept, changed = h3_ensure_project_base(with_base, self.PROJECT_CONTEXT)

        self.assertFalse(changed)
        self.assertEqual(kept, with_base)

    def test_the_comparison_covers_only_the_part_that_varies(self):
        # "The assistant must not change more than the part that varies in each clip, and
        # that part is what the comparison should show": measured, the project text is
        # 2,770 of a clip's 6,184 characters and the direction is 812.
        prompt = self._prompt(direction="a slow push into a close-up")

        clip = h3_clip_text(prompt, [self.PROJECT_CONTEXT])

        self.assertIn("a slow push into a close-up", clip)
        self.assertIn("<d>[Spanish] Hola.</d>", clip, "the spoken lines are the shot's own")
        self.assertNotIn("RESTRICCIONES GLOBALES", clip)
        self.assertNotIn("Estudio tipo loft", clip)
        self.assertLess(len(clip), len(prompt))

    def test_the_project_text_is_what_it_was_and_the_rest_is_the_clip(self):
        prompt = self._prompt()

        clip = h3_clip_text(prompt, [self.PROJECT_CONTEXT])

        # Nothing is invented and nothing important is lost: the shot's text plus the
        # project text is the prompt, less the whitespace that joined the two seams.
        self.assertLessEqual(len(clip) + len(self.PROJECT_CONTEXT), len(prompt))
        self.assertGreaterEqual(
            len(clip) + len(self.PROJECT_CONTEXT), len(prompt) - 40,
        )


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

class LooseEditTests(unittest.TestCase):
    """An edit the assistant mis-transcribes is applied, not turned into an error.

    From the log of a real session: "an edit's FIND text is not in the current prompt,
    character for character, so it could not be applied: '<Subject 1> ((S2)); A cozy
    loft study...'". The prompt spells it "Subject 1 (S2); A cozy loft study...", the
    sentence is in that prompt once, and because the edit could not be applied the
    whole turn ended as an error with nothing to change.
    """

    PROMPT = (
        "subject_definitions\n<Subject 1> Valeria sits by the window.\n"
        "detailed_description\nSubject 1 (S2); A cozy loft study with wooden textures, "
        "books, and warm side lighting. The camera holds a steady medium shot.\n"
    )

    def test_a_find_with_extra_punctuation_is_placed_and_applied(self):
        text, problems = _apply_revision_edits(self.PROMPT, [{
            "find": "<Subject 1> ((S2)); A cozy loft study with wooden textures, books, "
                    "and warm side lighting.",
            "set": "Subject 1 (S2); A tidy loft study with wooden textures.",
        }])

        self.assertEqual(problems, [])
        self.assertIn("A tidy loft study with wooden textures.", text)
        self.assertNotIn("A cozy loft study", text)

    def test_a_find_that_is_nowhere_is_still_refused_with_its_text(self):
        text, problems = _apply_revision_edits(self.PROMPT, [{
            "find": "A sentence that this prompt does not contain at all, anywhere.",
            "set": "anything",
        }])

        self.assertEqual(text, self.PROMPT)
        self.assertEqual(len(problems), 1)
        self.assertIn("not in the current prompt", problems[0])
        self.assertIn("A sentence that this prompt does not contain", problems[0])

    def test_a_short_find_is_never_placed_loosely(self):
        # "cozyy loft" is not in the prompt and never could be, but it is far too
        # short to place after ignoring spelling: doing so would land on "cozy loft".
        text, problems = _apply_revision_edits(self.PROMPT, [{"find": "cozyy loft", "set": "x"}])

        self.assertEqual(text, self.PROMPT)
        self.assertEqual(len(problems), 1)

    def test_a_loose_find_that_could_be_two_places_is_refused_rather_than_guessed(self):
        # Ignoring case and punctuation this sentence sits in the prompt twice, so
        # there is no way to know which one was meant and nothing is changed.
        prompt = "HELLO world, the camera holds a steady shot. hello world!"

        text, problems = _apply_revision_edits(prompt, [{
            "find": "Hello world!",
            "set": "Goodbye world",
        }])

        self.assertEqual(text, prompt)
        self.assertEqual(len(problems), 1)

    def test_the_retry_is_told_to_copy_or_switch_to_the_whole_prompt(self):
        nudge = _revise_problem_nudge([
            "an edit's FIND text is not in the current prompt, character for character, "
            "so it could not be applied: 'x'",
        ])

        self.assertIn("copied from THIS SHOT'S TEXT exactly", nudge)
        self.assertIn("send FIXED_PROMPT with the whole shot text", nudge)

    def test_a_nudge_without_edit_problems_stays_short(self):
        nudge = _revise_problem_nudge(["The rewrite changed the spoken words."])

        self.assertNotIn("send FIXED_PROMPT with the whole shot text", nudge)


class ClipVideoRecoveryTests(unittest.TestCase):
    """A clip's video is found in its sidecar when the state lost the link.

    Measured on a real project of 177 clips: the state carried a video_filename for
    37 of them while 174 sidecars named this pipeline together with their clip index,
    so shots 38 onward showed no play button and the Dashboard counted 140 clips as
    missing. Matching by filename cannot replace this: 65 of those videos open with
    the same words.
    """

    def _write(self, folder, name, payload):
        with open(os.path.join(folder, name), "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
    def test_missing_links_are_read_from_the_sidecars(self):
        with tempfile.TemporaryDirectory() as folder:
            open(os.path.join(folder, "video_a.mp4"), "wb").close()
            open(os.path.join(folder, "video_b.mp4"), "wb").close()
            self._write(folder, "sidecar_a.json", {
                "director_pipeline_id": "abc123",
                "director_clip_index": 4,
                "output_filename": "video_a.mp4",
                "created_at": 10.0,
            })
            self._write(folder, "sidecar_b.json", {
                "director_pipeline_id": "abc123",
                "director_clip_index": 5,
                "output_filename": "video_b.mp4",
                "created_at": 11.0,
            })
            state = {"clips": [
                {"index": 4}, {"index": 5}, {"index": 6},
            ]}

            filled = pipeline._backfill_clip_video_filenames(state, folder, "abc123")

            self.assertEqual(filled["clips"][0]["video_filename"], "video_a.mp4")
            self.assertEqual(filled["clips"][1]["video_filename"], "video_b.mp4")
            self.assertIsNone(filled["clips"][2].get("video_filename"))

    def test_a_recorded_link_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            open(os.path.join(folder, "video_a.mp4"), "wb").close()
            open(os.path.join(folder, "video_new.mp4"), "wb").close()
            self._write(folder, "sidecar.json", {
                "director_pipeline_id": "abc123",
                "director_clip_index": 0,
                "output_filename": "video_new.mp4",
                "created_at": 99.0,
            })
            state = {"clips": [{"index": 0, "video_filename": "video_a.mp4"}]}

            pipeline._backfill_clip_video_filenames(state, folder, "abc123")

            self.assertEqual(state["clips"][0]["video_filename"], "video_a.mp4")

    def test_sidecars_of_another_pipeline_and_missing_files_are_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write(folder, "other.json", {
                "director_pipeline_id": "other",
                "director_clip_index": 0,
                "output_filename": "video_a.mp4",
                "created_at": 5.0,
            })
            self._write(folder, "gone.json", {
                "director_pipeline_id": "abc123",
                "director_clip_index": 0,
                "output_filename": "not_on_disk.mp4",
                "created_at": 5.0,
            })
            state = {"clips": [{"index": 0}]}

            pipeline._backfill_clip_video_filenames(state, folder, "abc123")

            self.assertIsNone(state["clips"][0].get("video_filename"))

    def test_the_newest_sidecar_wins_for_a_regenerated_clip(self):
        with tempfile.TemporaryDirectory() as folder:
            open(os.path.join(folder, "old.mp4"), "wb").close()
            open(os.path.join(folder, "new.mp4"), "wb").close()
            self._write(folder, "old.json", {
                "director_pipeline_id": "abc123",
                "director_clip_index": 7,
                "output_filename": "old.mp4",
                "created_at": 1.0,
            })
            self._write(folder, "new.json", {
                "director_pipeline_id": "abc123",
                "director_clip_index": 7,
                "output_filename": "new.mp4",
                "created_at": 2.0,
            })
            state = {"clips": [{"index": 7}]}

            pipeline._backfill_clip_video_filenames(state, folder, "abc123")

            self.assertEqual(state["clips"][0]["video_filename"], "new.mp4")

    def test_the_exact_count_path_still_works_without_sidecars(self):
        with tempfile.TemporaryDirectory() as folder:
            open(os.path.join(folder, "one.mp4"), "wb").close()
            open(os.path.join(folder, "two.mp4"), "wb").close()
            state = {"output_files": ["one.mp4", "two.mp4"], "clips": [{"index": 0}, {"index": 1}]}

            pipeline._backfill_clip_video_filenames(state, folder, "abc123")

            self.assertEqual(
                [clip["video_filename"] for clip in state["clips"]],
                ["one.mp4", "two.mp4"],
            )

    def test_a_merged_answer_is_split_into_one_chip_per_proposal_in_the_window(self):
        # The backend splits too, but a running server keeps the parser it started
        # with, so the window splits what it is given: two proposals on one line came
        # back as a single chip carrying both, and that chip executes neither.
        with open(
            os.path.join(_ROOT, "ui", "src", "components", "DirectorDashboard", "DirectorDashboard.tsx"),
            encoding="utf-8",
        ) as handle:
            dashboard = handle.read()
        with open(
            os.path.join(_ROOT, "ui", "src", "lib", "revisionOptions.ts"),
            encoding="utf-8",
        ) as handle:
            helper = handle.read()

        self.assertEqual(dashboard.count("splitRevisionOptions(fixAnswer.options)"), 2)
        self.assertNotIn("fixAnswer.options.map", dashboard)
        self.assertIn("export function splitRevisionOptions(", helper)
        self.assertIn("(?<=\\s)(?=\\d{1,2}[.)][ \\t]+\\S)", helper)
        self.assertIn("' | '", helper)

class SpeakerContradictionTests(unittest.TestCase):
    """The measurement names the contradictions a reader sees at once.

    Measured on shot 37 of a real project ("el shot 37 tiene contradicciones de
    genero, ricardo es el speaker sin embargo continua generando a la mujer como
    speaker"): the field head declared "<Subject 1> (S4): Valeria" while every
    binding in the same prompt says only (S1) and (S2) exist, three <scenetrans> tags
    held nothing, and the prose gave the speaking to "she" while all four tagged
    lines belong to (S2) = Ricardo, whose own line says "Hombre maduro (60)". The
    assistant was told the gender was wrong and had no finding that mentioned it.
    """

    def _prompt(self, body: str, extra: str = "") -> str:
        return (
            "subject_definitions: <Subject 1> (S4): Valeria, a young woman with fair skin.\n"
            "summary: Medium shot focusing on Valeria in a loft.\n"
            "retention_analysis: Preserve the identities and the audio roles.\n"
            f"detailed_description: [Shot 1] {body}\n"
            "overall_soundscape: room tone.\n"
            "non_diegetic_music: N/A\n"
            "- <Subject 2> (S2) = Ricardo = [Speaker_02] = <Picture 2> + <Audio 2>.\n"
            "- S2 = Ricardo \u2014 [Speaker_02] | <Picture 2> + <Audio 2> | Hombre maduro "
            "(60), te\u00f3logo laico, gafas de lectura.\n"
            "- NUNCA se genera (S3), (S4) ni ning\u00fan otro speaker ID adicional.\n"
            f"{extra}"
        )

    def _findings(self, prompt: str) -> list[str]:
        return list(diagnose_h3_clip_prompt(prompt).get("findings") or [])

    def test_a_line_given_to_the_wrong_gender_is_named_with_its_sentence(self):
        findings = self._findings(self._prompt(
            "She speaks to camera about human differences being crushed by friction. "
            "(S2) speaks thoughtfully: <d>[Spanish] que importa.</d>. Only the tagged "
            "lines are spoken."
        ))

        joined = " ".join(findings)
        self.assertIn("belong to (S2)", joined)
        self.assertIn("describes the person who speaks as feminine", joined)
        self.assertIn("She speaks to camera about human differences", joined)

    def test_the_correct_attribution_is_left_alone(self):
        findings = self._findings(self._prompt(
            "He speaks to camera about human differences being crushed by friction. "
            "(S2) speaks thoughtfully: <d>[Spanish] que importa.</d>. Only the tagged "
            "lines are spoken."
        ))

        self.assertNotIn("describes the person who speaks", " ".join(findings))

    def test_two_speakers_of_different_genders_are_not_compared(self):
        prompt = self._prompt(
            "She speaks first. (S1) says: <d>[Spanish] hola.</d>. Then (S2) answers: "
            "<d>[Spanish] que importa.</d>."
        ) + "- S1 = Valeria \u2014 [Speaker_01] | <Picture 1> + <Audio 1> | Mujer joven (30).\n"

        findings = self._findings(prompt)

        self.assertNotIn("describes the person who speaks", " ".join(findings))

    def test_an_id_that_no_binding_declares_is_named_and_a_forbidden_one_is_not(self):
        findings = self._findings(self._prompt(
            "(S2) speaks: <d>[Spanish] que importa.</d>."
        ))

        joined = " ".join(findings)
        self.assertIn("speaker id(s) (S4)", joined)
        self.assertIn("it binds (S2)", joined)
        # (S3) is named by the rule that forbids it, never by the shot.
        self.assertNotIn("(S3)", joined)

    def test_a_tag_that_holds_nothing_is_named(self):
        findings = self._findings(self._prompt(
            "(S2) speaks: <scenetrans></scenetrans> <scenetrans></scenetrans> "
            "<d>[Spanish] que importa.</d>."
        ))

        joined = " ".join(findings)
        self.assertIn("2 tag(s) that hold nothing", joined)
        self.assertIn("<scenetrans></scenetrans>", joined)

    def test_a_healthy_single_speaker_shot_gets_no_such_finding(self):
        findings = self._findings(
            "subject_definitions: <Subject 2> (S2): Ricardo, a man in his sixties.\n"
            "summary: Medium shot of Ricardo.\n"
            "retention_analysis: Preserve the identities.\n"
            "detailed_description: [Shot 1] He speaks calmly to camera. (S2) speaks: "
            "<d>[Spanish] que importa.</d>. Only the tagged lines are spoken.\n"
            "overall_soundscape: room tone.\n"
            "non_diegetic_music: N/A\n"
            "- <Subject 2> (S2) = Ricardo = [Speaker_02] = <Picture 2> + <Audio 2>.\n"
            "- S2 = Ricardo \u2014 [Speaker_02] | Hombre maduro (60), gafas de lectura.\n"
        )

        joined = " ".join(findings)
        self.assertNotIn("speaker id(s)", joined)
        self.assertNotIn("hold nothing", joined)
        self.assertNotIn("describes the person who speaks", joined)

class ClipScopedAnswerTests(unittest.TestCase):
    """An answer about the shot's own text is placed back into the prompt.

    Measured on shot 37 of a real project: the assistant is handed THIS SHOT'S TEXT
    (1,963 of the prompt's 7,330 characters, because the other 5,367 are the project's
    shared text), so its FIXED_PROMPT is the shot's own text corrected. Judged against
    the whole prompt that answer came back with 50 problems -- "the proposal changes the
    project's shared text, which every clip carries verbatim" -- and the prompt never
    changed, which is what "el prompt no aparenta haber cambiado" was. Placed back into
    the prompt's own spans, the same answer comes back with no problems at all.
    """

    PROJECT_RULES = (
        "RESTRICCIONES GLOBALES DEL PROYECTO (critico, no negociable):\n"
        "- Este proyecto tiene EXACTAMENTE DOS participantes. No hay un tercero.\n"
    )
    BINDINGS = (
        "- <Subject 1> (S1) = Valeria = [Speaker_01] = <Picture 1> + <Audio 1>.\n"
        "- <Subject 2> (S2) = Ricardo = [Speaker_02] = <Picture 2> + <Audio 2>.\n"
    )

    def setUp(self):
        # The project's text first and the shot's six fields after it. The shape that
        # matters in practice is the one the real project has -- the project's text
        # interleaved through the shot's own -- and that one is pinned by
        # RealPlacementTests below, on the real prompt.
        self.frozen = [self.PROJECT_RULES, self.BINDINGS]
        self.prompt = (
            self.PROJECT_RULES
            + self.BINDINGS
            + "subject_definitions: <Subject 1> (S1): Valeria, a young woman.\n"
            "\n"
            "summary: [reference generation] She speaks to camera in a loft.\n"
            "\n"
            "retention_analysis: Preserve the identities and the audio roles.\n"
            "\n"
            "detailed_description: [Shot 1] She speaks to camera. (S2) speaks "
            "thoughtfully: <d>[Spanish] que importa.</d>.\n"
            "\n"
            "overall_soundscape: room tone.\n"
            "\n"
            "non_diegetic_music: N/A\n"
        )
        # The answer is a rewrite of the shot's own text, which is what the assistant is
        # given and what it answers with.
        shot_text = "".join(
            self.prompt[start:end]
            for start, end in h3_clip_spans(self.prompt, self.frozen)
        )
        self.answer = (
            shot_text
            .replace("She speaks to camera in a loft", "He speaks to camera in a loft")
            .replace("detailed_description: [Shot 1] She speaks", "detailed_description: [Shot 1] He speaks")
        )

    def test_a_clip_scoped_answer_is_placed_and_the_project_text_survives(self):
        placed = pipeline._clip_scoped_rewrite(self.prompt, self.answer, self.frozen)

        self.assertIsNotNone(placed)
        self.assertIn("He speaks to camera", placed)
        self.assertNotIn("She speaks to camera", placed)
        for phrase in self.frozen:
            self.assertIn(phrase, placed)

    def test_the_fields_and_the_spoken_lines_come_through_untouched(self):
        placed = pipeline._clip_scoped_rewrite(self.prompt, self.answer, self.frozen)

        fields = re.findall(
            r"(?mi)^[ \t]*(subject_definitions|summary|retention_analysis|"
            r"detailed_description|overall_soundscape|non_diegetic_music)[ \t]*:",
            placed,
        )
        self.assertEqual(len(set(fields)), 6)
        self.assertEqual(
            h3_dialogue_blocks(placed), h3_dialogue_blocks(self.prompt),
        )

    def test_an_answer_the_model_reflowed_never_loses_a_field(self):
        # Whitespace carries no meaning here and the prompt's structure does. A reflowed
        # answer either comes back with all six fields or is refused; what it must never do
        # is come back as a prompt whose fields have been glued together.
        reflowed = re.sub(r"\s+", " ", self.answer).strip()

        placed = pipeline._clip_scoped_rewrite(self.prompt, reflowed, self.frozen)

        if placed is None:
            return  # refused: the caller judges it as a whole prompt, as before
        fields = re.findall(
            r"(?mi)^[ \t]*(subject_definitions|summary|retention_analysis|"
            r"detailed_description|overall_soundscape|non_diegetic_music)[ \t]*:",
            placed,
        )
        self.assertEqual(len(set(fields)), 6)
        for phrase in self.frozen:
            self.assertIn(phrase, placed)
    def test_an_answer_that_already_carries_the_project_text_is_left_alone(self):
        whole = self.prompt.replace("She speaks to camera", "He speaks to camera")

        self.assertIsNone(
            pipeline._clip_scoped_rewrite(self.prompt, whole, self.frozen),
        )

    def test_an_answer_that_is_not_a_prompt_is_left_alone(self):
        self.assertIsNone(
            pipeline._clip_scoped_rewrite(
                self.prompt, "Una nota, no un prompt.", self.frozen,
            ),
        )

    def test_a_rewrite_that_loses_the_shot_is_left_alone(self):
        self.assertIsNone(
            pipeline._clip_scoped_rewrite(
                self.prompt, "subject_definitions: something else entirely.\n", self.frozen,
            ),
        )

    def test_the_proposal_asks_for_the_placement(self):
        with open(
            os.path.join(_APP_DIR, "services", "director_pipeline.py"), encoding="utf-8",
        ) as handle:
            self.assertIn("_clip_scoped_rewrite(prompt, text, frozen)", handle.read())

    def test_a_placement_that_loses_the_prompt_structure_is_refused(self):
        # The result is checked, not trusted: a field head that goes missing must not be
        # handed back as if the prompt were whole.
        broken = self.answer.replace("retention_analysis:", "notes about retention:")

        self.assertIsNone(
            pipeline._clip_scoped_rewrite(self.prompt, broken, self.frozen),
        )

class RealPlacementTests(unittest.TestCase):
    """The placement on the project's own prompt, where the claim is end to end.

    Measured on shot 37 of magnifica-humanitas: the assistant's answer at the shot's scale
    came back from the review with 50 problems -- every one of them "the proposal changes
    the project's shared text" -- so the prompt never changed. The same answer, placed back
    into the prompt's own spans, comes back with none.
    """

    STATE = os.path.join(
        _ROOT, "app", "outputs", "magnifica-humanitas",
        "_director_pipeline_2d7ed490.json",
    )
    CLIP_INDEX = 36          # shot 37, the one the director reported
    SPEAKS_WRONG = "She speaks to camera"
    SPEAKS_RIGHT = "He speaks to camera"
    FIELD_RE = re.compile(
        r"(?mi)^[ \t]*(subject_definitions|summary|retention_analysis|"
        r"detailed_description|overall_soundscape|non_diegetic_music)[ \t]*:"
    )

    def setUp(self):
        if not os.path.isfile(self.STATE):
            self.skipTest("the real project is not in this workspace")
        with open(self.STATE, encoding="utf-8") as handle:
            self.state = json.load(handle)
        self.clip = next(
            clip for clip in self.state["clips"] if clip["index"] == self.CLIP_INDEX
        )
        self.prompt = self.clip["video_prompt"]
        if self.SPEAKS_WRONG not in self.prompt:
            self.skipTest("this shot does not carry the contradiction any more")
        self.frozen = [
            self.clip.get("_director_project_context") or "",
            *h3_shared_project_phrases(self.state["clips"]),
        ]
        self.answer = h3_clip_text(self.prompt, self.frozen).replace(
            self.SPEAKS_WRONG, self.SPEAKS_RIGHT,
        )

    def test_the_shot_text_the_assistant_is_given_is_a_quarter_of_the_prompt(self):
        clip_text = h3_clip_text(self.prompt, self.frozen)

        self.assertLess(len(clip_text), 0.5 * len(self.prompt))
        self.assertIn(self.SPEAKS_WRONG, clip_text)
        self.assertNotIn(
            "RESTRICCIONES GLOBALES DEL PROYECTO", clip_text,
        )

    def test_the_placement_keeps_everything_that_is_not_the_shot(self):
        placed = pipeline._clip_scoped_rewrite(self.prompt, self.answer, self.frozen)

        self.assertIsNotNone(placed)
        in_prompt = [
            phrase for phrase in self.frozen
            if phrase.strip() and phrase in self.prompt
        ]
        for phrase in in_prompt:
            self.assertIn(phrase, placed)
        self.assertEqual(
            len(self.FIELD_RE.findall(placed)),
            len(self.FIELD_RE.findall(self.prompt)),
        )
        self.assertEqual(
            h3_dialogue_blocks(placed), h3_dialogue_blocks(self.prompt),
        )
        self.assertIn(self.SPEAKS_RIGHT, placed)

    def test_the_raw_answer_would_have_been_refused_and_the_placed_one_is_not(self):
        placed = pipeline._clip_scoped_rewrite(self.prompt, self.answer, self.frozen)
        review = dict(
            duration_seconds=self.clip.get("_director_duration_sec") or 0.0,
            subjects=self.clip.get("_director_subjects_on_screen") or [],
            mode=self.clip.get("_director_h3_prompt_mode") or "ref2va",
            references=self.clip.get("_director_h3_reference_manifest") or [],
            frozen=self.frozen,
        )

        raw_problems = review_h3_revision(self.prompt, self.answer, **review)
        placed_problems = review_h3_revision(self.prompt, placed, **review)

        # The answer alone looked like a prompt that had dropped the project's text.
        self.assertGreater(len(raw_problems), 10)
        self.assertTrue(any("shared text" in problem for problem in raw_problems))
        # Placed back into its own spans, the same correction is accepted.
        self.assertEqual(placed_problems, [])


class ReferenceAndReasoningTests(unittest.TestCase):
    """The assistant is given the neighbours, and its reading is shown.

    "Muchas estan hiladas con la siguiente escena, entonces sin referencia no hay modo
    que el AI entienda... me diga que hay que arreglar y lo razone antes de proceder."

    Measured on magnifica-humanitas: the neighbour digest handed over the first 600
    characters of each neighbour's prompt, which are the same 600 characters in all 177
    shots (the shared project text), so it said nothing about the neighbour. And shot 37
    shares its last line with shot 38 under a different speaker -- "datos, a ordenes y a
    rendimientos." is (S2) in one and (S1) in the other -- which no single shot can show.
    """

    def test_the_neighbour_digest_carries_what_differs_between_shots(self):
        with open(
            os.path.join(_APP_DIR, "services", "director_pipeline.py"), encoding="utf-8",
        ) as handle:
            pipeline_source = handle.read()

        self.assertIn("its own text:", pipeline_source)
        self.assertIn("speaks {heard or '(nobody)'}", pipeline_source)
        self.assertIn("h3_clip_text(prompt,", pipeline_source)
        # The shared 600 characters are not the neighbour's identity.
        self.assertNotIn('str(clips[index].get("video_prompt") or "").split()\n            )[:600]', pipeline_source)

    def test_the_crossed_line_between_shots_is_measured(self):
        clips = [
            {"video_prompt": "x (S2) says: <d>[Spanish] hola.</d>"},
            {"video_prompt": "y (S1) says: <d>[Spanish] hola.</d>"},
        ]

        problems = h3_shared_line_speaker_problems(clips, 0)

        self.assertEqual(len(problems), 1)
        self.assertIn("(S2)", problems[0])
        self.assertIn("shot 2", problems[0])
        self.assertIn("(S1)", problems[0])

    def test_a_line_everyone_gives_to_the_same_speaker_is_left_alone(self):
        clips = [
            {"video_prompt": "x (S2) says: <d>[Spanish] hola.</d>"},
            {"video_prompt": "y (S2) says: <d>[Spanish] hola.</d>"},
        ]

        self.assertEqual(h3_shared_line_speaker_problems(clips, 0), [])

    def test_the_subject_a_shot_declares_is_compared_with_its_own_rule(self):
        prompt = (
            "subject_definitions: <Subject 1> (S1): Ricardo, wearing a sweater.\n"
            "summary: x.\n"
            "- <Subject 1> (S1) = Valeria = [Speaker_01] = <Picture 1> + <Audio 1>.\n"
        )

        problems = h3_subject_binding_problems(prompt)

        self.assertEqual(len(problems), 1)
        self.assertIn("<Subject 1> as Ricardo", problems[0])
        self.assertIn("says <Subject 1> is Valeria", problems[0])

    def test_a_shot_that_agrees_with_its_own_rule_is_left_alone(self):
        prompt = (
            "subject_definitions: <Subject 1> (S1): Valeria, a young woman.\n"
            "summary: x.\n"
            "- <Subject 1> (S1) = Valeria = [Speaker_01] = <Picture 1> + <Audio 1>.\n"
        )

        self.assertEqual(h3_subject_binding_problems(prompt), [])

    def test_the_shot_text_is_numbered_so_an_answer_can_point_at_a_line(self):
        with open(
            os.path.join(_APP_DIR, "services", "director_pipeline.py"), encoding="utf-8",
        ) as handle:
            pipeline_source = handle.read()

        self.assertIn('f"{index + 1}| {line}"', pipeline_source)
        self.assertIn("The 'N| ' prefix is a reference", pipeline_source)

    def test_the_assistant_reading_is_shown_in_the_window(self):
        with open(
            os.path.join(_ROOT, "ui", "src", "components", "DirectorDashboard", "DirectorDashboard.tsx"),
            encoding="utf-8",
        ) as handle:
            dashboard = handle.read()

        self.assertEqual(dashboard.count("Su lectura del shot"), 2)
        self.assertIn("{fixAnswer.analysis}", dashboard)


class UnrenderableShotTests(unittest.TestCase):
    """A spoken line whose cue names nobody is named before a render is attempted.

    Measured on clip 38 of magnifica-humanitas, which could not be generated at all:
    "H3SpeakerBindingError: MiniMax H3 Omni could not determine which referenced character
    speaks '[Spanish] Báslyamente, un proyecto donde importa muchísimo más la...'". Its
    second line is introduced by "His voice carries wisdom as he says:", which names no
    character and carries no tag -- only a pronoun -- and the renderer refuses to hand a
    line to a voice it cannot identify. Two lines in the whole project have this and both
    are in that clip.
    """

    GOOD = (
        "subject_definitions: <Subject 1> (S1): Valeria, a young woman.\n"
        "detailed_description: [Shot 1] (S1) is seen finishing her thought: "
        "<d>[Spanish] hola.</d>. Ricardo says: <d>[Spanish] adios.</d>.\n"
        "- <Subject 1> (S1) = Valeria = [Speaker_01] = <Picture 1> + <Audio 1>.\n"
        "- <Subject 2> (S2) = Ricardo = [Speaker_02] = <Picture 2> + <Audio 2>.\n"
        "non_diegetic_music: N/A\n"
    )

    def test_a_cue_that_names_nobody_is_named_with_its_line(self):
        prompt = (
            "subject_definitions: <Subject 1> (S1): Valeria, a young woman.\n"
            "detailed_description: [Shot 1] His voice carries wisdom as he says: "
            "<d>[Spanish] Báslyamente, un proyecto.</d>.\n"
            "non_diegetic_music: N/A\n"
        )

        problems = h3_unresolved_speaker_cue_problems(prompt)

        self.assertEqual(len(problems), 1)
        self.assertIn("His voice carries wisdom as he says", problems[0])
        self.assertIn("Báslyamente", problems[0])
        self.assertIn("refuses to render the shot", problems[0])

    def test_a_tag_beside_the_line_is_enough(self):
        self.assertEqual(h3_unresolved_speaker_cue_problems(self.GOOD), [])

    def test_the_diagnosis_reports_it(self):
        prompt = (
            "subject_definitions: <Subject 1> (S1): Valeria, a young woman.\n"
            "detailed_description: [Shot 1] His voice carries wisdom as he says: "
            "<d>[Spanish] hola.</d>.\n"
            "non_diegetic_music: N/A\n"
        )

        findings = " ".join(
            str(finding) for finding in diagnose_h3_clip_prompt(prompt)["findings"]
        )

        self.assertIn("names no character", findings)
