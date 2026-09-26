"""Regressions for acoustic voice-gender verification."""

from __future__ import annotations

import glob
import json
import os
import sys
import unittest
from dataclasses import asdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.audio_analysis import AudioAnalysis, _voice_profiles  # noqa: E402
from services.director.voice_gender import (  # noqa: E402
    declared_gender_for_labels,
    describe_measurements,
    estimate_gender_from_pitch,
    speaker_turns,
    verify_voice_genders,
)

_REAL_PLAN = ""


def _discover_plan() -> str:
    """Newest saved pipeline plan that still has its diarized transcript."""

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
        params = data.get("_params_snapshot") or {}
        speakers = {
            str(row.get("speaker")) for row in (params.get("lyrics") or [])
            if isinstance(row, dict) and row.get("speaker")
        }
        # Two voices, because that is what these regressions measure. The playlist in
        # app/outputs keeps growing, and a newer single-speaker project used to become
        # the fixture: measured 2026-09-25 the newest plan on disk was a one-speaker
        # project and every two-voice assertion failed for a reason that had nothing to
        # do with the code under test.
        if params.get("lyrics") and len(speakers) >= 2:
            return candidate
    return ""


_REAL_PLAN = _discover_plan()
_REAL_VOCALS = ""
if _REAL_PLAN:
    with open(_REAL_PLAN, "r", encoding="utf-8") as _handle:
        _params = json.load(_handle).get("_params_snapshot") or {}
    for _key in ("audio_vocals_path", "audio_path"):
        _candidate = str(_params.get(_key) or "")
        if _candidate and os.path.isfile(_candidate):
            _REAL_VOCALS = _candidate
            break

# The measurement has to use the turn boundaries that belong to THIS audio.
# The fixture used to hard-code podcast-5's timings and apply them to whichever
# project happened to be newest on disk, so creating a new project silently
# changed what was measured: on the new project's own boundaries its voices
# measure 187.8 Hz (female) and 115.8 Hz (male), but sampling that same file at
# podcast-5's timings reported both as female (~191/~195 Hz).
_REAL_LYRICS = [
    row for row in (_params.get("lyrics") or [])
    if isinstance(row, dict) and row.get("speaker")
] if _REAL_PLAN else []


class TestEstimateGenderFromPitch(unittest.TestCase):
    def test_classifies_a_clear_male_voice(self):
        self.assertEqual(estimate_gender_from_pitch(106.2), "male")

    def test_classifies_a_clear_female_voice(self):
        self.assertEqual(estimate_gender_from_pitch(192.2), "female")

    def test_makes_no_claim_in_the_ambiguous_band(self):
        self.assertEqual(estimate_gender_from_pitch(160.0), "")

    def test_makes_no_claim_without_a_measurement(self):
        self.assertEqual(estimate_gender_from_pitch(None), "")
        self.assertEqual(estimate_gender_from_pitch(0), "")
        self.assertEqual(estimate_gender_from_pitch("abc"), "")


class TestDeclaredGender(unittest.TestCase):
    def test_reads_gender_from_the_speaker_mapping_name(self):
        hints = declared_gender_for_labels(
            "",
            [
                {"speakerId": "(S1)", "name": "woman in cream dress"},
                {"speakerId": "(S2)", "name": "man in gray suite"},
            ],
            ["(S1)", "(S2)"],
        )

        self.assertEqual(hints, {"(S1)": "female", "(S2)": "male"})

    def test_reads_gender_from_the_concept_text(self):
        concept = (
            "Voice Tagging Convention:\n"
            "S1 (Female) = Bela, woman in her 20s.\n"
            "S2 (Male) = Rudo, man in his 50s.\n"
        )

        hints = declared_gender_for_labels(concept, [], ["(S1)", "(S2)"])

        self.assertEqual(hints, {"(S1)": "female", "(S2)": "male"})

    def test_reads_spanish_descriptors(self):
        concept = "S1 es una mujer joven. S2 es un hombre mayor."

        hints = declared_gender_for_labels(concept, [], ["(S1)", "(S2)"])

        self.assertEqual(hints, {"(S1)": "female", "(S2)": "male"})

    def test_returns_nothing_when_no_gender_is_stated(self):
        self.assertEqual(
            declared_gender_for_labels("A podcast about AI.", [], ["(S1)"]),
            {},
        )

    def test_a_label_at_the_end_of_its_line_keeps_its_own_gender(self):
        # The real regression: " (S1)" sits at the END of its line, so a wide
        # look-ahead reached the next line's "is the man", which was nearer than
        # its own "woman" and won -- inverting both genders.
        concept = (
            "subject_definitions:\n"
            "<Subject 1> is the woman whose face, body, and identity come from "
            "<Picture 1>. (S1)\n"
            "<Subject 2> is the man whose face, body, and identity come from "
            "<Picture 2>. (S2)\n"
            "\n"
            "REGLAS DE VOZ:\n"
            "- <Subject 1> (S1) habla SIEMPRE con voz aguda, femenina.\n"
            "- <Subject 2> (S2) habla SIEMPRE con voz grave, masculina.\n"
        )

        hints = declared_gender_for_labels(concept, [], ["(S1)", "(S2)"])

        self.assertEqual(hints, {"(S1)": "female", "(S2)": "male"})

    def test_a_counting_line_makes_no_claim(self):
        # "(S1) y (S2)" names both labels without describing either one, so the
        # scan has to keep looking instead of latching onto the first hit.
        concept = (
            "- EXACTAMENTE DOS speaker IDs existen en todo el proyecto: "
            "(S1) y (S2).\n"
            "- <Subject 1> (S1): Mujer muy joven y esbelta.\n"
            "- <Subject 2> (S2): Hombre maduro de aspecto interesante.\n"
        )

        hints = declared_gender_for_labels(concept, [], ["(S1)", "(S2)"])

        self.assertEqual(hints, {"(S1)": "female", "(S2)": "male"})

    def test_reads_a_gender_stated_before_the_label(self):
        hints = declared_gender_for_labels(
            "<Subject 1> is the woman who hosts. (S1)",
            [],
            ["(S1)"],
        )

        self.assertEqual(hints, {"(S1)": "female"})


class TestSpeakerTurns(unittest.TestCase):
    def test_groups_lines_per_stable_label(self):
        lyrics = [
            {"speaker": "SPEAKER_00", "start": 0, "end": 5},
            {"speaker": "SPEAKER_01", "start": 5, "end": 9},
            {"speaker": "SPEAKER_00", "start": 9, "end": 12},
        ]

        grouped = speaker_turns(lyrics)

        self.assertEqual(sorted(grouped), ["(S1)", "(S2)"])
        self.assertEqual(len(grouped["(S1)"]), 2)
        self.assertEqual(grouped["(S2)"][0], {"start": 5.0, "end": 9.0})

    def test_ignores_lines_without_a_speaker(self):
        self.assertEqual(
            speaker_turns([{"speaker": "", "start": 0, "end": 5}]), {},
        )

    def test_accepts_diarization_segments_with_stable_labels(self):
        # The UI's diarization segments already carry (S1)/(S2) in speaker_id.
        segments = [
            {"speaker_id": "(S1)", "start": 0, "end": 5},
            {"speaker_id": "(S2)", "start": 5, "end": 9},
        ]

        grouped = speaker_turns(segments)

        self.assertEqual(sorted(grouped), ["(S1)", "(S2)"])
        self.assertEqual(grouped["(S2)"][0], {"start": 5.0, "end": 9.0})

    def test_never_renumbers_an_already_stable_label(self):
        # First-seen order must not turn a leading (S2) into (S1).
        segments = [
            {"speaker_id": "(S2)", "start": 0, "end": 5},
            {"speaker_id": "(S1)", "start": 5, "end": 9},
        ]

        self.assertEqual(sorted(speaker_turns(segments)), ["(S1)", "(S2)"])


class TestDescribeMeasurements(unittest.TestCase):
    def test_summarizes_each_speaker(self):
        text = describe_measurements({
            "(S1)": {"median_f0": 192.2},
            "(S2)": {"median_f0": 106.2},
        })

        self.assertIn("(S1)=192Hz (female)", text)
        self.assertIn("(S2)=106Hz (male)", text)

    def test_handles_no_data(self):
        self.assertEqual(
            describe_measurements({}), "no speaker pitch could be measured",
        )


@unittest.skipUnless(
    os.path.isfile(_REAL_PLAN) and os.path.isfile(_REAL_VOCALS),
    "real podcast-5 plan or audio not present on this machine",
)
class TestRealProjectVoiceGender(unittest.TestCase):
    """The project that motivated this check: S1 female, S2 male."""

    def test_measures_and_confirms_the_declared_genders(self):
        with open(_REAL_PLAN, "r", encoding="utf-8") as handle:
            plan = json.load(handle)
        params = plan.get("_params_snapshot") or {}

        measurements, conflicts = verify_voice_genders(
            _REAL_VOCALS,
            params.get("lyrics") or [],
            speaker_mappings=params.get("speaker_mappings") or [],
            concept_text=params.get("scene_description") or "",
        )

        self.assertEqual(sorted(measurements), ["(S1)", "(S2)"])
        self.assertEqual(
            estimate_gender_from_pitch(measurements["(S1)"]["median_f0"]),
            "female",
        )
        self.assertEqual(
            estimate_gender_from_pitch(measurements["(S2)"]["median_f0"]),
            "male",
        )
        self.assertEqual(conflicts, [], "declared genders should match the audio")

    def test_detects_a_deliberately_swapped_mapping(self):
        with open(_REAL_PLAN, "r", encoding="utf-8") as handle:
            plan = json.load(handle)
        params = plan.get("_params_snapshot") or {}

        _, conflicts = verify_voice_genders(
            _REAL_VOCALS,
            params.get("lyrics") or [],
            speaker_mappings=[
                {"speakerId": "(S1)", "name": "man in a gray suit"},
                {"speakerId": "(S2)", "name": "woman in a cream dress"},
            ],
            concept_text="",
            max_seconds_per_label=8.0,
        )

        reported = {row["label"]: row["measured"] for row in conflicts}
        self.assertEqual(reported.get("(S1)"), "female")
        self.assertEqual(reported.get("(S2)"), "male")


@unittest.skipUnless(
    os.path.isfile(_REAL_VOCALS),
    "real podcast-5 audio not present on this machine",
)
class TestAutomaticVoiceProfiles(unittest.TestCase):
    """`/audio/analyze` emits the voice profiles itself, keyed by the stable
    label. The Director UI reads them straight from the analysis, so a second
    diarization pass is no longer needed to name the voices.
    """

    def _lyrics(self):
        # The declared project's own turn boundaries, so the windows describe
        # the audio being measured. The literal list below is only a fallback
        # for a plan that carries no transcript.
        if _REAL_LYRICS:
            return _REAL_LYRICS
        # The transcript the analysis produces carries raw pyannote ids.
        return [
            {"start": 0.0, "end": 5.0, "text": "uno", "speaker": "SPEAKER_00"},
            {"start": 5.2, "end": 10.2, "text": "dos", "speaker": "SPEAKER_00"},
            {"start": 10.4, "end": 13.4, "text": "tres", "speaker": "SPEAKER_01"},
            {"start": 13.6, "end": 19.5, "text": "cuatro", "speaker": "SPEAKER_00"},
            {"start": 19.7, "end": 24.6, "text": "cinco", "speaker": "SPEAKER_01"},
        ]

    def test_profiles_are_keyed_by_the_stable_label(self):
        profiles = _voice_profiles(_REAL_VOCALS, self._lyrics())

        # The UI keys its lookup on "(S1)"/"(S2)", never on "SPEAKER_00".
        self.assertEqual(sorted(profiles), ["(S1)", "(S2)"])

    def test_the_two_voices_measure_as_opposite_genders(self):
        profiles = _voice_profiles(_REAL_VOCALS, self._lyrics())

        self.assertEqual(
            estimate_gender_from_pitch(profiles["(S1)"]["median_f0"]), "female",
        )
        self.assertEqual(
            estimate_gender_from_pitch(profiles["(S2)"]["median_f0"]), "male",
        )

    def test_the_analysis_result_carries_the_profiles(self):
        # /audio/analyze returns asdict(AudioAnalysis), so the field has to
        # survive serialization or the UI silently loses the pre-fill.
        result = AudioAnalysis(
            duration=1.0, sample_rate=44100, bpm=120.0, beats=[],
            downbeats=[], sections=[], onset_envelope=[],
        )
        result.voice_profiles = _voice_profiles(_REAL_VOCALS, self._lyrics())

        payload = asdict(result)

        self.assertIn("voice_profiles", payload)
        self.assertEqual(sorted(payload["voice_profiles"]), ["(S1)", "(S2)"])


if __name__ == "__main__":
    unittest.main()
