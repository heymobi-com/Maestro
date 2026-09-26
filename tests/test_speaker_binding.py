"""Regressions for deterministic per-line speaker binding."""

from __future__ import annotations

import json
import glob
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.speaker_binding import (  # noqa: E402
    SPEAKER_LABEL_RULES,
    bind_dialogue_speakers,
    canonical_speaker_label,
    cast_vocabulary,
    describe_cast,
    format_speaker,
    normalize_line,
    speaker_cast_block,
    speaker_label_order,
    speaker_label_sort_key,
    strip_speaker_prefix,
)

def _discover_plan() -> str:
    """Newest saved pipeline plan that actually contains rendered clips.

    The plan id changes on every run, so the fixture is discovered instead of
    hard-coded; a planning-phase plan with no clips cannot exercise binding.
    """

    base = os.path.abspath(os.path.join(_HERE, "..", "app", "outputs"))
    candidates = sorted(
        glob.glob(os.path.join(base, "*", "_director_pipeline_*.json")),
        key=os.path.getmtime,
        reverse=True,
    )
    for candidate in candidates:
        try:
            with open(candidate, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        if not data.get("clips"):
            continue
        # The regressions below resolve TWO raw ids to their stable labels. The
        # playlist in app/outputs keeps growing, and a newer single-speaker project
        # used to become the fixture: measured 2026-09-25 the newest plan on disk was
        # a one-speaker project and the two-speaker assertions failed for a reason
        # that had nothing to do with the code under test.
        params = data.get("_params_snapshot") or {}
        speakers = {
            str(row.get("speaker")) for row in (params.get("lyrics") or [])
            if isinstance(row, dict) and row.get("speaker")
        }
        if len(speakers) >= 2:
            return candidate
    return ""


_REAL_PLAN = _discover_plan()


class TestNormalizeLine(unittest.TestCase):
    def test_strips_h3_tags_and_accents(self):
        self.assertEqual(
            normalize_line("<d>Pues a ver, as\u00ed en general,</d>"),
            "pues a ver asi en general",
        )

    def test_repairs_the_observed_mojibake_variant(self):
        self.assertEqual(
            normalize_line("Claro, s\u00ed. Un ej\u00e9rcito"),
            "claro si un ejercito",
        )


class TestSpeakerLabelOrder(unittest.TestCase):
    def test_assigns_labels_by_first_seen_order(self):
        lyrics = [
            {"speaker": "SPEAKER_01", "start": 0, "end": 1},
            {"speaker": "SPEAKER_00", "start": 1, "end": 2},
            {"speaker": "SPEAKER_01", "start": 2, "end": 3},
        ]

        self.assertEqual(
            speaker_label_order(lyrics),
            {"SPEAKER_01": "(S1)", "SPEAKER_00": "(S2)"},
        )


class TestCanonicalSpeakerLabel(unittest.TestCase):
    def test_adds_missing_parentheses(self):
        self.assertEqual(canonical_speaker_label("S2"), "(S2)")

    def test_keeps_canonical_form(self):
        self.assertEqual(canonical_speaker_label("(S1)"), "(S1)")

    def test_resolves_raw_diarization_ids_through_the_order(self):
        order = {"SPEAKER_00": "(S1)", "SPEAKER_01": "(S2)"}
        self.assertEqual(canonical_speaker_label("SPEAKER_01", order), "(S2)")

    def test_returns_empty_for_blank_input(self):
        self.assertEqual(canonical_speaker_label(None), "")


class TestBindDialogueSpeakers(unittest.TestCase):
    def setUp(self):
        self.lyrics = [
            {"speaker": "SPEAKER_00", "start": 0.0, "end": 5.0,
             "text": "Buenos dias a todos."},
            {"speaker": "SPEAKER_01", "start": 5.0, "end": 10.0,
             "text": "Hoy hablamos de tecnologia."},
        ]

    def test_binds_each_beat_to_its_transcript_speaker(self):
        clips = [{
            "_director_dialogue_beats": [
                {"spoken_text": "Buenos dias a todos.", "delivery": "warm"},
                {"spoken_text": "Hoy hablamos de tecnologia.", "delivery": "calm"},
            ],
            "_director_subjects_on_screen": [],
        }]

        stats = bind_dialogue_speakers(
            clips,
            [{"start": 0.0, "end": 10.0}],
            self.lyrics,
        )

        beats = clips[0]["_director_dialogue_beats"]
        self.assertEqual(beats[0]["speaker_id"], "(S1)")
        self.assertEqual(beats[1]["speaker_id"], "(S2)")
        self.assertEqual(stats["beats_bound"], 2)
        self.assertEqual(stats["beats_unresolved"], 0)

    def test_h3_wrapped_beat_text_still_matches(self):
        clips = [{
            "_director_dialogue_beats": [
                {"spoken_text": "<d>Buenos dias a todos.</d>"},
            ],
            "_director_subjects_on_screen": [],
        }]

        bind_dialogue_speakers(clips, [{"start": 0.0, "end": 10.0}], self.lyrics)

        self.assertEqual(
            clips[0]["_director_dialogue_beats"][0]["speaker_id"], "(S1)"
        )

    def test_falls_back_to_the_windows_dominant_speaker(self):
        clips = [{
            "_director_dialogue_beats": [
                {"spoken_text": "Una linea que no esta en la transcripcion."},
            ],
            "_director_subjects_on_screen": [],
        }]

        stats = bind_dialogue_speakers(
            clips,
            [{"start": 5.0, "end": 10.0, "dominant_speaker": "SPEAKER_01"}],
            self.lyrics,
        )

        self.assertEqual(
            clips[0]["_director_dialogue_beats"][0]["speaker_id"], "(S2)"
        )
        self.assertEqual(stats["beats_fallback"], 1)

    def test_keeps_an_existing_speaker_id(self):
        clips = [{
            "_director_dialogue_beats": [{"spoken_text": "Ya asignada.", "speaker_id": "(S2)"}],
            "_director_subjects_on_screen": [],
        }]

        bind_dialogue_speakers(clips, [{"start": 0.0, "end": 10.0}], self.lyrics)

        self.assertEqual(
            clips[0]["_director_dialogue_beats"][0]["speaker_id"], "(S2)"
        )

    def test_labels_a_lone_subject_with_its_only_speaker(self):
        clips = [{
            "_director_dialogue_beats": [{"spoken_text": "Buenos dias a todos."}],
            "_director_subjects_on_screen": [
                {"visual_description": "Bela, a woman in her 20s"},
            ],
        }]

        stats = bind_dialogue_speakers(clips, [{"start": 0.0, "end": 5.0}], self.lyrics)

        self.assertEqual(
            clips[0]["_director_subjects_on_screen"][0]["character_id"], "(S1)"
        )
        self.assertEqual(stats["subjects_relabelled"], 1)

    def test_noop_without_diarized_lyrics(self):
        clips = [{
            "_director_dialogue_beats": [{"spoken_text": "Sin hablante."}],
            "_director_subjects_on_screen": [],
        }]

        stats = bind_dialogue_speakers(clips, [{"start": 0.0, "end": 5.0}], [])

        self.assertEqual(stats["beats_total"], 0)
        self.assertNotIn("speaker_id", clips[0]["_director_dialogue_beats"][0])


class TestInlineLabelDoesNotBreakMatching(unittest.TestCase):
    """The planner writes the label inside the line, which used to make the
    line unmatchable and silently hand it the window's dominant voice.

    Real case: window 7.292-14.583 s is dominated by SPEAKER_00 (female), but
    the line "Claro, s\u00ed. Un ej\u00e9rcito..." belongs to SPEAKER_01 (male).
    """

    def setUp(self):
        self.lyrics = [
            {"speaker": "SPEAKER_00", "start": 0.0, "end": 5.0,
             "text": "Buenos dias a todos."},
            {"speaker": "SPEAKER_01", "start": 5.0, "end": 10.0,
             "text": "Hoy hablamos de tecnologia."},
        ]
        self.window = {
            "start": 0.0, "end": 10.0, "dominant_speaker": "SPEAKER_00",
        }

    def _bind(self, spoken_text, **beat_extra):
        beat = {"spoken_text": spoken_text}
        beat.update(beat_extra)
        clips = [{
            "_director_dialogue_beats": [beat],
            "_director_subjects_on_screen": [],
        }]
        stats = bind_dialogue_speakers(clips, [self.window], self.lyrics)
        return clips[0]["_director_dialogue_beats"][0], stats

    def test_binds_the_minority_speaker_despite_the_dominant_voice(self):
        beat, stats = self._bind("(S2): Hoy hablamos de tecnologia.")

        self.assertEqual(beat["speaker_id"], "(S2)")
        self.assertEqual(stats["beats_bound"], 1)

    def test_binds_when_the_language_tag_is_still_present(self):
        beat, _ = self._bind("<d>[Spanish] Hoy hablamos de tecnologia.</d>")

        self.assertEqual(beat["speaker_id"], "(S2)")

    def test_binds_when_the_label_follows_the_opening_tag(self):
        beat, _ = self._bind("<d>(S2): Hoy hablamos de tecnologia.</d>")

        self.assertEqual(beat["speaker_id"], "(S2)")

    def test_corrects_a_persisted_label_that_contradicts_the_audio(self):
        beat, stats = self._bind(
            "Hoy hablamos de tecnologia.", speaker_id="(S1)",
        )

        self.assertEqual(beat["speaker_id"], "(S2)")
        self.assertEqual(stats["beats_corrected"], 1)

    def test_removes_the_inline_prefix_from_the_saved_line(self):
        beat, _ = self._bind("<d>(S2): Hoy hablamos de tecnologia.</d>")

        # The wrapper is kept; only the contradictory label is removed.
        self.assertEqual(beat["spoken_text"], "<d>Hoy hablamos de tecnologia.</d>")
        self.assertNotIn("(S2)", beat["spoken_text"])

    def test_uses_the_inline_label_when_the_transcript_cannot_match(self):
        beat, stats = self._bind("(S2): Una frase que no esta transcrita.")

        self.assertEqual(beat["speaker_id"], "(S2)")
        self.assertEqual(stats["beats_fallback"], 1)

    def test_never_invents_a_label_outside_the_vocabulary(self):
        beat, _ = self._bind("(S7): Una frase que no esta transcrita.")

        self.assertNotEqual(beat["speaker_id"], "(S7)")


class TestStripSpeakerPrefix(unittest.TestCase):
    def test_strips_a_leading_label(self):
        self.assertEqual(strip_speaker_prefix("(S2): Hola."), "Hola.")
        self.assertEqual(strip_speaker_prefix("S1. Hola."), "Hola.")

    def test_strips_a_label_after_the_opening_tag(self):
        self.assertEqual(
            strip_speaker_prefix("<d>(S2): Hola.</d>"), "<d>Hola.</d>",
        )

    def test_leaves_a_plain_line_untouched(self):
        self.assertEqual(strip_speaker_prefix("Hola a todos."), "Hola a todos.")


class TestCastVocabulary(unittest.TestCase):
    """The planner must hand the model labels it can actually reuse."""

    def setUp(self):
        self.mappings = [
            {"speakerId": "(S1)", "name": "woman in cream a-dress", "role": "speaking"},
            {"speakerId": "(S2)", "name": "man in gray suite", "role": "speaking"},
        ]
        # Real transcripts label lines with raw pyannote ids, never (Sx).
        self.lyrics = [
            {"speaker": "SPEAKER_00", "start": 0, "end": 5, "text": "Hola."},
            {"speaker": "SPEAKER_01", "start": 5, "end": 9, "text": "Adios."},
        ]

    def test_builds_the_vocabulary_from_the_user_mapping(self):
        self.assertEqual(
            cast_vocabulary(self.mappings, self.lyrics),
            {"(S1)": "woman in cream a-dress", "(S2)": "man in gray suite"},
        )

    def test_lists_diarized_speakers_missing_from_the_mapping(self):
        vocabulary = cast_vocabulary([], self.lyrics)

        self.assertEqual(vocabulary, {"(S1)": "(S1)", "(S2)": "(S2)"})

    def test_ignores_garbage_mapping_keys(self):
        vocabulary = cast_vocabulary(
            [{"speakerId": ",", "name": "oops"}], self.lyrics,
        )

        self.assertNotIn(",", vocabulary)

    def test_raw_pyannote_ids_resolve_to_canonical_labels(self):
        # This is the regression: the mapping is keyed by (S1) but the
        # transcript says SPEAKER_00, so a naive lookup returned the raw id.
        order = speaker_label_order(self.lyrics)
        vocabulary = cast_vocabulary(self.mappings, self.lyrics)

        self.assertEqual(order.get("SPEAKER_00"), "(S1)")
        self.assertEqual(
            format_speaker(order["SPEAKER_00"], vocabulary.get(order["SPEAKER_00"])),
            "(S1) woman in cream a-dress",
        )

    def test_formats_a_label_with_its_name(self):
        self.assertEqual(format_speaker("(S1)", "Bela"), "(S1) Bela")
        self.assertEqual(format_speaker("(S1)", ""), "(S1)")
        self.assertEqual(format_speaker("(S1)", "(S1)"), "(S1)")

    def test_cast_block_is_sorted_and_carries_gender(self):
        block = speaker_cast_block(
            {"(S2)": "man in gray suite", "(S1)": "woman in cream a-dress"},
            {"(S1)": "female", "(S2)": "male"},
        )

        self.assertEqual(
            block.splitlines(),
            [
                "  (S1) woman in cream a-dress — female voice",
                "  (S2) man in gray suite — male voice",
            ],
        )

    def test_cast_block_is_empty_without_a_vocabulary(self):
        self.assertEqual(speaker_cast_block({}), "")

    def test_sort_key_orders_numerically(self):
        labels = ["(S10)", "(S2)", "(S1)"]

        self.assertEqual(
            sorted(labels, key=speaker_label_sort_key),
            ["(S1)", "(S2)", "(S10)"],
        )

    def test_rules_forbid_inventing_labels(self):
        self.assertIn("Never invent a label", SPEAKER_LABEL_RULES)
        self.assertIn("(S3)", SPEAKER_LABEL_RULES)
        self.assertIn("Never write notes", SPEAKER_LABEL_RULES)

    def test_describe_cast_reports_the_raw_mapping(self):
        text = describe_cast(cast_vocabulary(self.mappings, self.lyrics), self.lyrics)

        self.assertIn("(S1) woman in cream a-dress", text)
        self.assertIn("SPEAKER_00->(S1)", text)


@unittest.skipUnless(
    os.path.isfile(_REAL_PLAN),
    "real podcast-5 pipeline plan not present on this machine",
)
class TestRealProjectRegression(unittest.TestCase):
    """Run against the exact project that shipped the broken plan."""

    def setUp(self):
        with open(_REAL_PLAN, "r", encoding="utf-8") as handle:
            plan = json.load(handle)
        self.clips = plan.get("clips") or []
        params = plan.get("_params_snapshot") or {}
        self.lyrics = params.get("lyrics") or []
        self.mappings = params.get("speaker_mappings") or []
        self.windows = [clip.get("planned_clip") or {} for clip in self.clips]

    def test_planner_vocabulary_resolves_the_real_raw_ids(self):
        order = speaker_label_order(self.lyrics)
        vocabulary = cast_vocabulary(self.mappings, self.lyrics)

        self.assertEqual(sorted(vocabulary), ["(S1)", "(S2)"])
        self.assertTrue(set(order.values()).issubset(set(vocabulary)))

        # Every transcript line must render with a canonical label, never with
        # the raw pyannote id the planner used to hand the model.
        for row in self.lyrics:
            label = order.get(str(row.get("speaker") or "").strip().upper(), "")
            self.assertTrue(label, f"unresolved speaker {row.get('speaker')!r}")
            display = format_speaker(label, vocabulary.get(label))
            self.assertNotIn("SPEAKER_", display)

    def _beats(self):
        return [
            beat
            for clip in self.clips
            for beat in clip.get("_director_dialogue_beats") or []
            if isinstance(beat, dict)
        ]

    def test_every_dialogue_beat_ends_up_with_a_speaker(self):
        beats = self._beats()
        self.assertTrue(beats, "fixture has no dialogue beats")

        stats = bind_dialogue_speakers(self.clips, self.windows, self.lyrics)

        with_speaker = [beat for beat in self._beats() if beat.get("speaker_id")]
        self.assertEqual(len(with_speaker), len(beats))
        self.assertEqual(stats["beats_total"], len(beats))
        self.assertEqual(stats["beats_unresolved"], 0)

    def test_only_the_two_real_speakers_are_assigned(self):
        bind_dialogue_speakers(self.clips, self.windows, self.lyrics)

        labels = {str(beat.get("speaker_id")) for beat in self._beats()}
        self.assertTrue(labels)
        for label in labels:
            self.assertRegex(label, r"^\(S[12]\)$")


if __name__ == "__main__":
    unittest.main()
