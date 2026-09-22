"""Regressions for H3 spoken-line, language, and label sanitizing.

Kept in its own module so the large planner/dialogue regression file keeps its
class structure untouched.
"""

from __future__ import annotations

import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.h3_dialogue import (  # noqa: E402
    _align_h3_time_markers,
    _clean_h3_metadata,
    _clean_malformed_subject_tags,
    _clean_subject_text,
    _detect_dialogue_language,    _H3_CONTEXT_BUDGET,    _dialogue_payload,
    _drop_silent_dialogue_beats,
    _format_h3_timestamp,
    _normalize_speaker_brackets,
    _split_multi_speaker_beats,
    _strip_speaker_prefixes,
    compile_h3_clip_plans,
    drop_planner_reasoning,
    drop_reasoning_sentences,
    h3_dialogue_tag,
    is_silent_dialogue,
    looks_like_planner_reasoning,
    pack_project_context,
    strip_dialogue_markup,
    validate_h3_prompt_contract,
)
from services.director.schema import DialogueBeat  # noqa: E402


class TestSpokenLineSanitizer(unittest.TestCase):
    """A speaker marker inside the spoken line contradicted the outer label."""

    def test_removes_a_leading_parenthesised_label(self):
        self.assertEqual(
            _strip_speaker_prefixes("(S2): Claro, s\u00ed. Un ej\u00e9rcito."),
            "Claro, s\u00ed. Un ej\u00e9rcito.",
        )

    def test_removes_square_bracket_and_bare_prefixes(self):
        self.assertEqual(_strip_speaker_prefixes("[S1]: Hola."), "Hola.")
        self.assertEqual(_strip_speaker_prefixes("S2: Hola."), "Hola.")

    def test_removes_an_inline_marker_from_a_merged_turn(self):
        cleaned = _strip_speaker_prefixes("uno. (S2): dos.")

        self.assertNotIn("(S2)", cleaned)
        self.assertIn("dos", cleaned)

    def test_strips_a_wrapping_quote_pair(self):
        self.assertEqual(_strip_speaker_prefixes('"Hola."'), "Hola.")

    def test_the_h3_tag_never_contains_a_speaker_marker(self):
        tag = h3_dialogue_tag('(S1): "Buenos d\u00edas."')

        # The Spanish tag is the corrected reading of an untagged Spanish line
        # (it used to be hard-coded to English). The point of the test is the
        # speaker marker: Maestro renders the speaker itself, so a second,
        # contradictory label inside the block must never survive.
        self.assertEqual(tag, "<d>[Spanish] Buenos d\u00edas.</d>")
        self.assertNotIn("(S1)", tag)

    def test_strips_stray_non_latin_noise_from_a_latin_line(self):
        _, words = _dialogue_payload("de\u0e04\u0e34\u0e14 dopamina")

        self.assertEqual(words, "de dopamina")

    def test_keeps_a_non_latin_project_intact(self):
        _, words = _dialogue_payload("\u3053\u3093\u306b\u3061\u306f\u4e16\u754c")

        self.assertEqual(words, "\u3053\u3093\u306b\u3061\u306f\u4e16\u754c")


class TestLanguageTag(unittest.TestCase):
    def test_detects_spanish_from_accents(self):
        self.assertEqual(
            _detect_dialogue_language("Un ej\u00e9rcito en formaci\u00f3n"),
            "Spanish",
        )

    def test_detects_spanish_from_function_words(self):
        self.assertEqual(
            _detect_dialogue_language("la ventaja de la que hablamos es un hecho"),
            "Spanish",
        )

    def test_defaults_to_english_for_english_text(self):
        self.assertEqual(
            _detect_dialogue_language("The advantage belongs to whoever moves fast"),
            "English",
        )

    def test_an_explicit_project_language_beats_the_planner_tag(self):
        language, words = _dialogue_payload(
            "[English] Hola, \u00bfqu\u00e9 tal?", "Spanish",
        )

        self.assertEqual(language, "Spanish")
        self.assertEqual(words, "Hola, \u00bfqu\u00e9 tal?")

    def test_inline_tag_is_honoured_without_a_project_language(self):
        language, words = _dialogue_payload("[French] Bonjour.")

        self.assertEqual(language, "French")
        self.assertEqual(words, "Bonjour.")

    def test_an_untagged_line_is_read_instead_of_defaulting_to_english(self):
        # Hard-coding English here tagged Spanish lines wrong whenever nothing
        # else had decided, which is what a hand-edited prompt looks like.
        language, words = _dialogue_payload("Es una precisi\u00f3n brutal.")

        self.assertEqual(language, "Spanish")
        self.assertEqual(words, "Es una precisi\u00f3n brutal.")


class TestEditedPromptLanguage(unittest.TestCase):
    """A prompt the user edits must keep the language tags they wrote.

    Saving an edit clears the clip's beat cache on purpose, so a one-clip rerun
    arrives with nothing to sample. The project language then fell back to
    English and the compiler rewrote the user's own ``<d>[Spanish] ...</d>``
    into ``[English]`` -- the edit looked like it had been ignored.
    """

    def _plan(self, prompt, **overrides):
        plan = {
            "video_prompt": prompt,
            "image_prompt": "",
            "_director_h3_source_prompt": prompt,
            "_director_h3_compiled_prompt": "",
            "_director_dialogue_beats": [],
        }
        plan.update(overrides)
        return plan

    def test_a_user_written_language_tag_survives_a_single_clip_rerun(self):
        prompt = (
            "Dialogue: (S1) speaks: <d>[Spanish] Es una precisi\u00f3n "
            "brutal.</d>. While speaking, a decisive hand gesture."
        )
        plan = self._plan(prompt, _director_prompt_user_edited=True)

        compile_h3_clip_plans([plan], prompt_modes=["ref2va"], durations=[12.0])

        self.assertEqual(
            re.findall(r"<d>\[([^\]]*)\]", plan["video_prompt"]),
            ["Spanish"],
        )

    def test_english_is_still_the_answer_for_english_text(self):
        plan = self._plan("Dialogue: <d>The advantage belongs to whoever moves fast.</d>")

        compile_h3_clip_plans([plan], prompt_modes=["t2va"], durations=[10.0])

        self.assertEqual(
            re.findall(r"<d>\[([^\]]*)\]", plan["video_prompt"]),
            ["English"],
        )

    def test_an_empty_beat_sample_falls_back_to_the_prompt_text(self):
        # No beats anywhere: the prompts are the only evidence left, so the run
        # must not be declared English just because the cache was cleared.
        plan = self._plan(
            "Dos personas conversan sobre la pr\u00e1ctica y la teor\u00eda.",
            _director_dialogue_beats=[],
        )
        plan["video_prompt"] = (
            "Dialogue: <d>Es una precisi\u00f3n brutal.</d>"
        )
        plan["_director_h3_source_prompt"] = plan["video_prompt"]

        compile_h3_clip_plans([plan], prompt_modes=["t2va"], durations=[10.0])

        self.assertEqual(
            re.findall(r"<d>\[([^\]]*)\]", plan["video_prompt"]),
            ["Spanish"],
        )


class TestLabelSanitizer(unittest.TestCase):
    def test_converts_square_bracket_labels(self):
        self.assertEqual(
            _normalize_speaker_brackets("[S2] is still explaining"),
            "(S2) is still explaining",
        )

    def test_drops_a_corrupted_label_group(self):
        cleaned = _normalize_speaker_brackets(
            "<Subject 2> (S\u03c0\u00fc\u00c4), a man",
        )

        self.assertNotIn("\u03c0", cleaned)
        self.assertNotIn("(S\u03c0", cleaned)

    def test_keeps_valid_labels(self):
        self.assertEqual(
            _normalize_speaker_brackets("<Subject 1> (S1) nods"),
            "<Subject 1> (S1) nods",
        )

    def test_keeps_ordinary_prose_that_starts_with_s(self):
        self.assertEqual(
            _normalize_speaker_brackets("They meet (Saturday) at noon"),
            "They meet (Saturday) at noon",
        )

    def test_subject_text_drops_planner_label_scaffolding(self):
        cleaned = _clean_subject_text("<Subject 1> (S1), an elegant young woman")

        self.assertNotIn("<Subject", cleaned)
        self.assertIn("an elegant young woman", cleaned)

    def test_subject_text_drops_a_corrupted_subject_tag(self):
        cleaned = _clean_subject_text("<Subject \u0e04\u0e34\u0e14/S1>, who nods")

        self.assertNotIn("\u0e04", cleaned)
        self.assertIn("who nods", cleaned)


class TestH3TimeMarkers(unittest.TestCase):
    """The planner copied the literal "MM:" placeholder and could not do the
    arithmetic, so every cut marker is written from the real audio window."""

    def test_formats_the_official_marker_shape(self):
        self.assertEqual(_format_h3_timestamp(7.292), "00:07.292")
        self.assertEqual(_format_h3_timestamp(58.0), "00:58.000")
        self.assertEqual(_format_h3_timestamp(125.5), "02:05.500")
        self.assertEqual(_format_h3_timestamp(0), "00:00.000")

    def test_rewrites_a_placeholder_ending_in_a_period(self):
        text, count = _align_h3_time_markers(
            "[Shot 2] At MM:07.300. The two subjects continue.", 7.292,
        )

        self.assertEqual(count, 1)
        self.assertEqual(
            text, "[Shot 2] At 00:07.292. The two subjects continue.",
        )

    def test_rewrites_a_marker_ending_in_a_comma(self):
        text, count = _align_h3_time_markers(
            "[Shot 4] At MM:19.700, the conversation continues.", 21.875,
        )

        self.assertEqual(count, 1)
        self.assertIn("At 00:21.875,", text)

    def test_corrects_a_cumulative_time_that_disagrees_with_the_window(self):
        # The real defect: clip 4 starts at 21.875s but the prompt said 19.700.
        text, _ = _align_h3_time_markers("[Shot 4] At 0:19.700, x", 21.875)

        self.assertNotIn("19.700", text)
        self.assertIn("00:21.875", text)

    def test_leaves_text_without_a_marker_untouched(self):
        original = "A shot with no cut marker at all."

        text, count = _align_h3_time_markers(original, 12.0)

        self.assertEqual(count, 0)
        self.assertEqual(text, original)

    def test_is_a_noop_without_a_real_window(self):
        original = "[Shot 2] At MM:07.300. Text"

        text, count = _align_h3_time_markers(original, None)

        self.assertEqual(count, 0)
        self.assertEqual(text, original)

    def test_is_idempotent(self):
        first, _ = _align_h3_time_markers("[Shot 2] At MM:07.300. Text", 7.292)
        second, count = _align_h3_time_markers(first, 7.292)

        self.assertEqual(second, first)
        self.assertEqual(count, 1)


class TestPackProjectContext(unittest.TestCase):
    """The injected project context is cut to a character budget. A raw cut
    deleted the voice-rules section on real projects, which is the only part of
    the context that assigns each subject a voice.
    """

    def setUp(self):
        self.header = "Proyecto: Podcast de dos participantes sobre IA."
        self.restrictions = "RESTRICCIONES GLOBALES DEL PROYECTO (critico):\n" + "\n".join(
            f"- Prohibicion detallada numero {n} que ocupa bastante espacio."
            for n in range(1, 9)
        )
        self.voice_rules = (
            "REGLAS DE VOZ (critico, no negociable):\n"
            "- <Subject 1> (S1) habla SIEMPRE con voz aguda, femenina.\n"
            "- <Subject 2> (S2) habla SIEMPRE con voz grave, masculina."
        )
        self.trailing = "ESCENARIO:\n- Un estudio moderno con luz ambiental azul."
        self.context = "\n\n".join(
            [self.header, self.restrictions, self.voice_rules, self.trailing],
        )

    def test_returns_a_short_context_unchanged(self):
        self.assertEqual(pack_project_context("Corto."), "Corto.")

    def test_returns_empty_for_empty_input(self):
        self.assertEqual(pack_project_context(""), "")
        self.assertEqual(pack_project_context(None), "")

    def test_a_raw_cut_would_lose_the_voice_rules(self):
        # Establishes that the fixture really exercises the reported defect.
        raw_cut = self.context[:360]

        self.assertNotIn("voz aguda", raw_cut)
        self.assertGreater(len(self.context), 360)

    def test_keeps_the_voice_rules_within_the_budget(self):
        packed = pack_project_context(self.context)

        self.assertLessEqual(len(packed), _H3_CONTEXT_BUDGET)
        self.assertIn("REGLAS DE VOZ", packed)
        self.assertIn("voz aguda, femenina", packed)
        self.assertIn("voz grave, masculina", packed)

    def test_a_bigger_restriction_section_does_not_evict_the_voice_rules(self):
        # The restriction section is longer AND comes first in the document.
        # Exercised at an explicit small budget, because the default budget is
        # meant to be large enough that a normal context needs no selection.
        packed = pack_project_context(self.context, 360)

        self.assertNotIn("Prohibicion detallada numero 8", packed)
        self.assertIn("voz aguda", packed)

    def test_a_structured_context_survives_in_full(self):
        """A real project context is ~2.6k characters and was being destroyed.

        At the old 360-character budget this shape came out as two bare
        headings, so the injected prompt lost the subject definitions, the
        gender lock, the audio-driven rules and the setting all at once.
        """

        context = "\n\n".join([
            "PROJECT: Two-person AI podcast. EXACTLY TWO speakers visible.",
            "SUBJECT DEFINITIONS:\n"
            "- <Subject 1> (S1): FEMALE. Must match <Picture 1> exactly.\n"
            "- <Subject 2> (S2): MALE. Must match <Picture 2> exactly.",
            "GENDER LOCK:\n"
            "- <Subject 1> is FEMALE. No masculine features.\n"
            "- <Subject 2> is MALE. No makeup, no female hair.",
            "AUDIO-DRIVEN GENERATION:\n"
            "- Sync lip movements to the audio exactly.\n"
            "- Only the person currently speaking moves their lips.",
            "SETTING \u2014 FUTURISTIC TV STUDIO:\n"
            "- Background panels react to the conversation topic.",
            "DIALOGUE FORMAT:\n"
            "<Subject 1> (S1) says: <d>[Spanish] ...</d>",
        ])
        self.assertGreater(len(context), 360)

        packed = pack_project_context(context)

        self.assertEqual(packed, context)
        for section in (
            "SUBJECT DEFINITIONS", "GENDER LOCK",
            "AUDIO-DRIVEN GENERATION", "SETTING", "DIALOGUE FORMAT",
        ):
            self.assertIn(section, packed)

    def test_never_keeps_a_heading_without_its_rules(self):
        """A bare heading reads as a complete section with its rules missing."""

        packed = pack_project_context(self.context, 360)

        for index, line in enumerate(packed.splitlines()):
            stripped = line.strip()
            if stripped.endswith(":"):
                following = packed.splitlines()[index + 1 :]
                self.assertTrue(
                    following and following[0].strip().startswith("-"),
                    f"{stripped!r} was kept with no rule under it",
                )

    def test_a_section_that_does_not_fit_is_dropped_whole(self):
        packed = pack_project_context(self.context, 360)

        self.assertIn("REGLAS DE VOZ", packed)
        self.assertNotIn("ESCENARIO", packed)

    def test_never_emits_a_partial_line(self):
        packed = pack_project_context(self.context)
        source_lines = {
            line.strip().rstrip(".")
            for line in self.context.splitlines()
            if line.strip()
        }

        for line in packed.splitlines():
            if line.strip():
                # The caller re-terminates the injected block, so the very last
                # line may have lost its period; no line may lose characters.
                self.assertIn(line.strip().rstrip("."), source_lines)

    def test_ends_on_a_complete_line_without_a_dangling_period_run(self):
        packed = pack_project_context(self.context)

        self.assertNotIn("....", packed)
        self.assertFalse(packed.endswith("..."))

    def test_unstructured_text_is_cut_at_a_sentence(self):
        prose = "Una frase completa. " * 40

        packed = pack_project_context(prose, 360)

        self.assertLessEqual(len(packed), 360)
        self.assertTrue(packed.endswith("."))
        self.assertNotIn("....", packed)

    def test_a_short_structured_context_is_returned_verbatim(self):
        prose = "Una frase completa. " * 40

        self.assertEqual(pack_project_context(prose), prose.strip())

    def test_an_explicit_budget_is_respected(self):
        packed = pack_project_context(self.context, 120)

        self.assertLessEqual(len(packed), 120)


class TestSilentDialogueBeats(unittest.TestCase):
    """A "no speech here" marker used to be read as a language tag, which left
    an empty line and aborted the whole render with
    "MiniMax H3 dialogue contains an empty line".
    """

    def test_recognises_the_observed_marker(self):
        self.assertTrue(is_silent_dialogue("<d>[silent]</d>"))
        self.assertTrue(is_silent_dialogue("[silent]"))
        self.assertTrue(is_silent_dialogue("(silent)"))
        self.assertTrue(is_silent_dialogue("[Silencio]"))
        self.assertTrue(is_silent_dialogue(""))

    def test_does_not_flag_real_dialogue(self):
        self.assertFalse(is_silent_dialogue("Claro, s\u00ed. Un ej\u00e9rcito."))
        self.assertFalse(is_silent_dialogue("<d>[Spanish] Hola.</d>"))

    def test_recognises_an_ellipsis_placeholder(self):
        """An ellipsis is not a spoken line, nor is an ellipsis plus a marker.

        The planner emits "..." and "... (silent)" for a stretch with nothing
        audible. Only a lone marker counted as silence, so these reached the
        video model as dialogue blocks on 9 clips of a real 150-shot run.
        """

        for placeholder in (
            "...", "\u2026", "... (silent)", "...(silent)",
            "... [silencio]", "<d>[Spanish] ...</d>", "<d> ... </d>",
            "(pausa) ...",
        ):
            self.assertTrue(
                is_silent_dialogue(placeholder),
                f"{placeholder!r} was treated as a spoken line",
            )

    def test_an_ellipsis_around_real_words_is_not_silence(self):
        self.assertFalse(is_silent_dialogue("...la seguridad. ..."))
        self.assertFalse(is_silent_dialogue("Espera... \u00bfqu\u00e9?"))

    def test_drops_silent_beats_and_keeps_real_lines(self):
        plans = [{
            "_director_dialogue_beats": [
                {"spoken_text": "Hola."},
                {"spoken_text": "<d>[silent]</d>"},
                {"spoken_text": "   "},
                {"spoken_text": "Adi\u00f3s."},
            ],
        }]

        dropped = _drop_silent_dialogue_beats(plans)

        self.assertEqual(dropped, 2)
        self.assertEqual(
            [beat["spoken_text"] for beat in plans[0]["_director_dialogue_beats"]],
            ["Hola.", "Adi\u00f3s."],
        )

    def test_compiling_a_silent_beat_no_longer_raises(self):
        # The exact shape that failed a real 150-shot run.
        plans = [{
            "planned_clip": {"start": 0.0, "end": 5.0},
            "video_prompt": "A calm studio shot.",
            "_director_project_context": "Proyecto: podcast.",
            "_director_dialogue_beats": [
                {"spoken_text": "Una linea real.", "delivery": "calm"},
                {"spoken_text": "<d>[silent]</d>", "delivery": ""},
            ],
        }]

        compiled = compile_h3_clip_plans(plans)

        prompt = compiled[0]["_director_h3_compiled_prompt"]
        self.assertIn("Una linea real.", prompt)
        self.assertNotIn("[silent]", prompt)


class TestMalformedSubjectTags(unittest.TestCase):
    """Corrupted <Subject ...> tags reached the prompt through three different
    paths: an unclosed tag, a beat metadata field, and the sound fields.
    """

    def test_removes_an_empty_tag(self):
        self.assertNotIn("<Subject", _clean_malformed_subject_tags("<Subject > raises a hand."))

    def test_removes_a_misspelled_tag(self):
        cleaned = _clean_malformed_subject_tags("<Subjectra 2> nods.")

        self.assertNotIn("<Subjectra", cleaned)
        self.assertIn("nods", cleaned)

    def test_removes_an_unclosed_tag_and_keeps_the_prose(self):
        cleaned = _clean_malformed_subject_tags(
            "Beside him, <Subject /{1} (S1) remains still, her eyes fixed on him. "
            "By the final beat, <Subject 2> leans forward.",
        )

        self.assertNotIn("/{1}", cleaned)
        self.assertIn("remains still", cleaned)
        self.assertIn("<Subject 2>", cleaned)

    def test_keeps_a_well_formed_tag(self):
        self.assertEqual(
            _clean_malformed_subject_tags("<Subject 1> speaks."),
            "<Subject 1> speaks.",
        )

    def test_beat_metadata_is_cleaned(self):
        cleaned = _clean_h3_metadata("<Subject > raises one hand as if grasping an idea.")

        self.assertNotIn("<Subject", cleaned)
        self.assertIn("raises one hand", cleaned)

    def test_subject_description_is_cleaned(self):
        cleaned = _clean_subject_text("<Subject /{1} (S1) a woman in a cream dress")

        self.assertNotIn("<Subject", cleaned)
        self.assertIn("cream dress", cleaned)


class TestMultiSpeakerBeatSplit(unittest.TestCase):
    """One beat becomes one <d> block, and a block gets one voice, so a beat
    that packed several speakers gave every line the first speaker's voice.
    """

    def test_splits_a_fused_line_into_one_beat_per_speaker(self):
        plans = [{
            "_director_dialogue_beats": [{
                "spoken_text": (
                    "esta dicotomia opera a una velocidad que da miedo. "
                    "(S2): Esa sombrosa. (S2): Porque un modelo no entiende."
                ),
                "speaker_id": "(S1)",
                "delivery": "thoughtful",
            }],
        }]

        added = _split_multi_speaker_beats(plans)

        beats = plans[0]["_director_dialogue_beats"]
        self.assertEqual(added, 2)
        self.assertEqual(
            [beat["speaker_id"] for beat in beats], ["(S1)", "(S2)", "(S2)"],
        )
        self.assertEqual(
            beats[0]["spoken_text"],
            "esta dicotomia opera a una velocidad que da miedo.",
        )
        self.assertEqual(beats[1]["spoken_text"], "Esa sombrosa.")
        self.assertEqual(
            beats[2]["spoken_text"], "Porque un modelo no entiende.",
        )

    def test_leaves_a_normal_beat_untouched(self):
        plans = [{"_director_dialogue_beats": [{"spoken_text": "Hola a todos."}]}]

        self.assertEqual(_split_multi_speaker_beats(plans), 0)
        self.assertEqual(len(plans[0]["_director_dialogue_beats"]), 1)

    def test_leaves_a_single_leading_label_untouched(self):
        # A leading label is the normal spelling and is stripped later.
        plans = [{"_director_dialogue_beats": [{"spoken_text": "(S2): Hola."}]}]

        self.assertEqual(_split_multi_speaker_beats(plans), 0)
        self.assertEqual(len(plans[0]["_director_dialogue_beats"]), 1)

    def test_later_segments_do_not_reuse_the_fused_delivery(self):
        plans = [{
            "_director_dialogue_beats": [{
                "spoken_text": "Uno. (S2): Dos.",
                "delivery": "warm",
                "physical_cue": "nods",
            }],
        }]

        _split_multi_speaker_beats(plans)

        beats = plans[0]["_director_dialogue_beats"]
        self.assertEqual(beats[0]["delivery"], "warm")
        self.assertEqual(beats[1]["delivery"], "")
        self.assertEqual(beats[1]["physical_cue"], "")

    def test_never_emits_a_beat_without_words(self):
        # The text before the first label is just the opening tag, which used
        # to be emitted as a wordless beat and failed the dialogue contract.
        plans = [{
            "_director_dialogue_beats": [{
                "spoken_text": "<d>(S1): qu\u00e9 pasar\u00e1. (S1): como necesitar dormir.",
                "speaker_id": "(S1)",
            }],
        }]

        _split_multi_speaker_beats(plans)

        beats = plans[0]["_director_dialogue_beats"]
        for beat in beats:
            self.assertFalse(
                is_silent_dialogue(beat["spoken_text"]),
                "every emitted beat must carry words: %r" % beat["spoken_text"],
            )
            _dialogue_payload(beat["spoken_text"])  # must not raise


class TestPlannerReasoningLeak(unittest.TestCase):
    """A JSON grammar stops prose around the payload but not reasoning escaped
    into a string value: ``ending_beat = "Wait, there should be 12 total
    entries in the JSON array."``.
    """

    def test_detects_the_observed_leaks(self):
        for value in (
            "Wait, there should be 12 total entries in the JSON array.",
            "But wait, let me recalculate the timing.",
            "Let's proceed through all 12 segments sequentially.",
            'mode = "}, // Wait - manual structure check',
            "Placeholder for brevity",
            "The user asked for exactly 12 shots.",
            "end of thought; generating full list below.",
        ):
            with self.subTest(value=value):
                self.assertTrue(looks_like_planner_reasoning(value))

    def test_does_not_flag_real_scene_prose(self):
        for value in (
            "(S1) waits for his reaction.",
            "She waits with curiosity.",
            "(S2) gazes toward (S1), waiting for her reaction.",
            "He pauses, considering the question.",
            "La c\u00e1mara se acerca lentamente.",
        ):
            with self.subTest(value=value):
                self.assertFalse(looks_like_planner_reasoning(value))

    def test_drops_a_field_that_is_entirely_reasoning(self):
        self.assertEqual(
            drop_planner_reasoning(
                "Wait, there should be 12 total entries in the JSON array.",
            ),
            "",
        )

    def test_keeps_a_normal_field(self):
        self.assertEqual(
            drop_planner_reasoning("  (S1) waits for his reaction.  "),
            "(S1) waits for his reaction.",
        )

    def test_removes_only_the_reasoning_sentence(self):
        cleaned, dropped = drop_reasoning_sentences(
            "La escena contin\u00faa. Wait, there should be 12 total entries in "
            "the JSON array. Dialogue timing: mouths stay closed.",
        )

        self.assertEqual(dropped, 1)
        self.assertNotIn("12 total entries", cleaned)
        self.assertIn("La escena contin\u00faa.", cleaned)
        self.assertIn("Dialogue timing", cleaned)

    def test_never_touches_a_sentence_with_dialogue_markup(self):
        original = '<d>Espera, no puede ser.</d>'

        cleaned, dropped = drop_reasoning_sentences(original)

        self.assertEqual(dropped, 0)
        self.assertEqual(cleaned, original)


class TestStripDialogueMarkup(unittest.TestCase):
    """Every planner beat crosses ``DialogueBeat.from_dict``. Normalizing there
    is what keeps the H3 wrapper and the repeated speaker out of the saved plan
    and the review UI, instead of every consumer having to remember to clean it.
    """

    def test_removes_the_wrapper_and_the_repeated_speaker(self):
        self.assertEqual(
            strip_dialogue_markup(
                "<d>(S1): Qu\u00e9 loco, eso suena desconectado.</d>"
            ),
            "Qu\u00e9 loco, eso suena desconectado.",
        )

    def test_removes_the_wrapper_alone(self):
        self.assertEqual(
            strip_dialogue_markup("<d>nos imaginamos cosas tangibles.</d>"),
            "nos imaginamos cosas tangibles.",
        )

    def test_is_idempotent_on_a_clean_line(self):
        clean = "Claro, s\u00ed. Un ej\u00e9rcito en formaci\u00f3n."

        self.assertEqual(strip_dialogue_markup(clean), clean)
        self.assertEqual(strip_dialogue_markup(strip_dialogue_markup(clean)), clean)

    def test_a_silence_marker_becomes_empty(self):
        self.assertEqual(strip_dialogue_markup("<d>[silent]</d>"), "")

    def test_the_beat_boundary_normalizes_the_stored_line(self):
        beat = DialogueBeat.from_dict({
            "spoken_text": "<d>(S2): S\u00ed, s\u00ed, s\u00ed.</d>",
            "speaker_id": "(S2)",
        })

        self.assertEqual(beat.spoken_text, "S\u00ed, s\u00ed, s\u00ed.")
        self.assertEqual(beat.speaker_id, "(S2)")


class TestValidatorSkipsWordlessBeats(unittest.TestCase):
    """"Shot 144: MiniMax H3 dialogue contains an empty line" aborted a whole
    render because the validator only checked for whitespace, and a bare "<d>"
    is not whitespace.
    """

    PROMPT = (
        "integrated_multimodal_description: [Shot 1] The two hosts talk. "
        "(S1) says: <d>[Spanish] Hola.</d>\n\n"
        "overall_soundscape: Studio ambience.\n\n"
        "non_diegetic_music: N/A"
    )

    def _errors(self, beats):
        return validate_h3_prompt_contract(self.PROMPT, beats)

    def test_a_bare_markup_beat_is_not_an_empty_line_error(self):
        errors = self._errors([
            {"spoken_text": "Hola.", "speaker_id": "(S1)"},
            {"spoken_text": "<d>", "speaker_id": "(S1)"},
            {"spoken_text": "</d>", "speaker_id": "(S2)"},
            {"spoken_text": "   ", "speaker_id": "(S2)"},
            {"spoken_text": "<d>[silent]</d>", "speaker_id": "(S2)"},
        ])

        self.assertFalse(
            any("empty line" in error for error in errors), errors,
        )

    def test_the_real_line_is_still_counted(self):
        # The guard must skip only the wordless beats, never a real one.
        errors = self._errors([
            {"spoken_text": "Hola.", "speaker_id": "(S1)"},
            {"spoken_text": "<d>", "speaker_id": "(S1)"},
            {"spoken_text": "Adi\u00f3s.", "speaker_id": "(S2)"},
        ])

        self.assertTrue(
            any("expected 2 dialogue block" in error for error in errors), errors,
        )


if __name__ == "__main__":
    unittest.main()
