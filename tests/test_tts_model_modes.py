"""`model_mode` means different things to different models, and the value has to survive that.

The bug these pin, measured on this machine: a YuE2 song was generated in the session, then the
user opened Studio -> Audio -> Speech and tried voice cloning with Qwen3 Base. Generating music
had written `model_mode: 2` into the one `params` object the Studio keeps across model switches,
nothing reset it for the TTS model, and Qwen3 reads that field as a *language*: `(2 or
"auto").lower()` raised `AttributeError: 'int' object has no attribute 'lower'` in eight
consecutive attempts, across Qwen3 Base, Voice Design and CustomVoice.

So two things are pinned here, and both are needed:

* a model whose control holds numbers keeps its number (YuE2 would break if it were "repaired"),
  and a model whose control holds text never sees a value it cannot read;
* the qwen3 pipeline normalises at the line it reads the control, so no caller -- a saved queue,
  an API client, a script -- can hand it a number and lose the attempt.
"""

from __future__ import annotations

import os
import sys
import types
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_APP_DIR = os.path.join(_ROOT, "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services import tts_model_modes  # noqa: E402

# The shapes as the handlers really declare them, read from the running app.
QWEN3_BASE = {
    "model_modes": {
        "choices": [
            ["Auto", "auto"], ["Chinese", "chinese"], ["English", "english"],
            ["Japanese", "japanese"], ["Spanish", "spanish"],
        ],
        "default": "auto",
        "label": "Language",
    },
}
QWEN3_CUSTOMVOICE = {
    "model_modes": {
        "choices": [["Aiden", "aiden"], ["Bella", "bella"]],
        "default": "aiden",
        "label": "Speaker",
    },
}
# yue2_handler: {"choices": [("Direct generation", 2), ("Melody and chords", 0), ("Melody only", 1)]}
YUE2 = {
    "model_modes": {
        "choices": [["Direct generation", 2], ["Melody and chords", 0], ["Melody only", 1]],
        "default": 2,
        "label": "Composition Planning",
    },
}
# flux2_klein_9b, as the model-options endpoint reports it: a numeric control with visibility.
NUMERIC_WITH_VISIBILITY = {
    "model_modes": {"choices": [["Balanced", 0], ["Quality", 2]], "default": 0, "image_modes": [2]},
}

PIPELINE = os.path.join(_APP_DIR, "models", "TTS", "qwen3", "pipeline.py")
WGP = os.path.join(_APP_DIR, "wgp.py")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


class TheMusicNumberIsWhatTheUserActuallySentTests(unittest.TestCase):
    """The failing value, from the source that produced it."""

    def test_the_number_that_crashed_the_clone_is_repaired_for_a_language_control(self):
        self.assertEqual(tts_model_modes.settings_value(2, QWEN3_BASE), "auto")

    def test_it_is_also_repaired_for_a_speaker_control(self):
        self.assertEqual(tts_model_modes.settings_value(2, QWEN3_CUSTOMVOICE), "aiden")

    def test_the_other_case_of_the_same_crash_is_covered(self):
        """Voice Design and CustomVoice failed the same way in the same session."""

        for value in (2, 0, 1, 2.0, True, False, [], {}, ("auto",)):
            with self.subTest(value=value):
                self.assertEqual(tts_model_modes.settings_value(value, QWEN3_BASE), "auto")


class AModelThatWantsANumberKeepsItTests(unittest.TestCase):
    """The half that must not change: repairing every model would break the ones that need it."""

    def test_yue2s_composition_mode_survives(self):
        for value in (0, 1, 2):
            with self.subTest(value=value):
                self.assertEqual(tts_model_modes.settings_value(value, YUE2), value)

    def test_a_numeric_control_with_visibility_survives(self):
        self.assertEqual(tts_model_modes.settings_value(2, NUMERIC_WITH_VISIBILITY), 2)

    def test_a_model_with_no_such_control_is_untouched(self):
        for model_def in ({}, {"audio_only": True}, {"model_modes": {"image_modes": [0, 1, 2]}},
                          {"model_modes": None}, None, "not a definition"):
            with self.subTest(model_def=model_def):
                self.assertEqual(tts_model_modes.settings_value(2, model_def), 2)

    def test_the_control_is_only_text_when_every_choice_is_text(self):
        self.assertTrue(tts_model_modes.is_text_control(QWEN3_BASE))
        self.assertFalse(tts_model_modes.is_text_control(YUE2))
        self.assertFalse(tts_model_modes.is_text_control(NUMERIC_WITH_VISIBILITY))
        self.assertFalse(tts_model_modes.is_text_control({}))


class AValueTheModelDeclaresIsKeptTests(unittest.TestCase):
    def test_a_language_it_declares_is_kept(self):
        self.assertEqual(tts_model_modes.settings_value("spanish", QWEN3_BASE), "spanish")

    def test_the_models_own_spelling_wins(self):
        """The UI may send any case; what goes to the model is what the model published."""

        self.assertEqual(tts_model_modes.settings_value("SPANISH", QWEN3_BASE), "spanish")
        self.assertEqual(tts_model_modes.settings_value(" spanish ", QWEN3_BASE), "spanish")

    def test_a_language_it_does_not_declare_falls_back(self):
        """A stringified number is the same mistake as a number, one step later."""

        for value in ("2", "klingon", "", "   "):
            with self.subTest(value=value):
                self.assertEqual(tts_model_modes.settings_value(value, QWEN3_BASE), "auto")

    def test_auto_is_the_default_when_the_model_does_not_say(self):
        undeclared = {"model_modes": {"choices": [["English", "english"], ["Japanese", "japanese"]]}}

        self.assertEqual(tts_model_modes.settings_value("spanish", undeclared), "english")
        self.assertEqual(tts_model_modes.declared(undeclared)[1], "english")


class TheModelSideGuardTests(unittest.TestCase):
    """`.lower()` needs a string, and this is the line that guarantees one."""

    def test_a_non_string_becomes_empty_so_the_models_own_default_applies(self):
        for value in (2, 0, 2.5, True, ["auto"], {"language": "auto"}, object()):
            with self.subTest(value=value):
                self.assertEqual(tts_model_modes.text_value(value), "")

    def test_a_string_is_kept(self):
        self.assertEqual(tts_model_modes.text_value("spanish"), "spanish")
        self.assertEqual(tts_model_modes.text_value(" spanish "), "spanish")
        self.assertEqual(tts_model_modes.text_value("SPANISH"), "SPANISH")

    def test_empty_reads_as_the_callers_default(self):
        """`(model_mode or "auto").lower()` is why empty is the right answer, not "auto"."""

        self.assertEqual(tts_model_modes.text_value(None) or "auto", "auto")

    def test_the_pipeline_normalises_before_it_lowers(self):
        source = _read(PIPELINE)
        guard = source.index("model_mode = tts_model_modes.text_value(model_mode)")

        for site in ("language = (model_mode or \"auto\").lower()",
                     "segment_speaker = model_mode or self.supported_speakers[0]",
                     "speaker = model_mode or self.supported_speakers[0]"):
            with self.subTest(site=site):
                self.assertGreater(source.index(site), guard)


class TheSettingsBoundaryRepairsItTests(unittest.TestCase):
    """Where the value is validated, so the whole run sees the repaired one and not just the model."""

    def test_the_boundary_asks_for_the_repair(self):
        source = _read(WGP)
        self.assertIn('model_mode = tts_model_modes.settings_value(inputs["model_mode"], model_def)',
                      source)

    def test_the_repair_reads_the_definition_of_the_model_being_validated(self):
        """`model_def` is in scope there and is the definition that declares the control."""

        source = _read(WGP)
        boundary = source.index("def validate_settings(")
        self.assertLess(boundary, source.index("tts_model_modes.settings_value"))
        self.assertLess(source.index("model_def = get_model_def(model_type)", boundary),
                        source.index("tts_model_modes.settings_value"))


class ThePipelineSurvivesTheValueItWasHandedTests(unittest.TestCase):
    """Upstream's own ``generate``, run with the value that failed, without loading a model.

    The method is lifted out of `pipeline.py` by AST and bound to a line whose engine is a dummy:
    what executes is upstream's branching and the exact line that raised, while the model, the
    tokenizer and the two GPU-only imports its module needs stay out of the way. The helpers the
    method reaches for are supplied here -- they are not what the bug is about, and each one is
    a few lines of arithmetic above this one.
    """

    @classmethod
    def setUpClass(cls):
        import ast

        source = _read(PIPELINE)
        tree = ast.parse(source)
        wanted = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "Qwen3TTSPipeline":
                for child in node.body:
                    if isinstance(child, ast.FunctionDef) and child.name == "generate":
                        wanted = child
        if wanted is None:
            raise AssertionError("the qwen3 pipeline no longer defines generate()")
        cls.method_source = ast.get_source_segment(source, wanted)
        # The annotations are read with `from __future__ import annotations` in that file, so the
        # extracted source needs the same promise or the def statement would evaluate them.
        cls.method_globals = {
            "__future__": None,
            "annotations": None,
        }

    def _generate_method(self):
        namespace: dict = {"__name__": "qwen3_generate_under_test"}

        class _Torch:
            @staticmethod
            def from_numpy(array):
                return array

        namespace.update({
            "math": __import__("math"),
            "torch": _Torch,
            # The module the pipeline imports, the same object, so patching it here is patching
            # what the extracted method reads.
            "tts_model_modes": tts_model_modes,
            "_read_text_or_file": lambda value, label=None: str(value or ""),
            "_seed_everything": lambda seed: None,
            "_is_abort_exception": lambda exc: False,
        })
        exec("from __future__ import annotations\n" + self.method_source, namespace)  # noqa: S102
        return namespace["generate"]

    # --- the line the method is bound to, with the helpers it expects -------------------------

    def _line(self, base_model_type):
        outer = self

        class _Criteria:
            def close(self):
                pass

        class _Engine:
            generate_defaults: dict = {}
            supported_speakers = ["aiden", "bella"]

            def _record(self, **kwargs):
                outer.spoken.append(kwargs)
                return [outer.audio], 24000

            def generate_voice_clone(self, **kwargs):
                return self._record(**kwargs)

            def generate_voice_design(self, **kwargs):
                return self._record(**kwargs)

            def generate_custom_voice(self, **kwargs):
                return self._record(**kwargs)

        line = types.SimpleNamespace(
            base_model_type=base_model_type,
            lm_decoder_engine="legacy",
            tts=_Engine(),
            supported_speakers=["aiden", "bella"],
            sample_rate=24000,
            _interrupt=False,
            _early_stop=False,
        )
        line.generate = self._generate_method().__get__(line)
        line._abort_requested = lambda: False
        line._early_stop_requested = lambda: False
        line._build_stopping_criteria = lambda *a, **k: (_Criteria(), [])
        line._get_tokens_per_second = lambda: 12.5
        line._get_seconds_per_token = lambda: 1.0 / line._get_tokens_per_second()
        line._resolve_max_new_tokens = lambda duration_seconds, kwargs: 100
        line._split_text_sequence = lambda text, tokens: [text]
        line._resolve_auto_split_seconds = lambda kwargs: None
        return line

    def setUp(self):
        self.spoken: list[dict] = []
        self.audio = [0.0] * 240

    def _generate(self, base_model_type, model_mode, **kwargs):
        line = self._line(base_model_type)
        audio_prompt_type = kwargs.pop("audio_prompt_type", "A")
        return line.generate(
            "En nuestro capitulo de hoy.",
            model_mode,
            "reference.wav",
            alt_prompt="",
            audio_prompt_type=audio_prompt_type,
            **kwargs,
        )

    def test_the_value_that_failed_now_generates(self):
        """`model_mode: 2` from the music session, on each variant that reads it as a language."""

        for base_model_type in ("qwen3_tts_base", "qwen3_tts_voicedesign"):
            with self.subTest(model=base_model_type):
                self.spoken.clear()
                result = self._generate(base_model_type, 2)

                self.assertIsNotNone(result)
                self.assertEqual(self.spoken[0]["language"], "auto")

    def test_a_language_it_can_read_is_still_passed_through(self):
        self.spoken.clear()
        self._generate("qwen3_tts_base", "spanish")

        self.assertEqual(self.spoken[0]["language"], "spanish")

    def test_the_repaired_language_is_the_one_the_model_asked_to_use(self):
        """Uppercase from a form still reaches the model as the model's own spelling."""

        self.spoken.clear()
        self._generate("qwen3_tts_base", "SPANISH")

        self.assertEqual(self.spoken[0]["language"], "spanish")

    def test_customvoice_falls_to_its_own_first_speaker(self):
        """Its control is a speaker name, and an empty one is what that code already expects."""

        self.spoken.clear()
        self._generate("qwen3_tts_customvoice", 2)

        self.assertEqual(self.spoken[0]["speaker"], "aiden")

    def test_without_the_guard_it_is_the_reported_crash(self):
        """The same call, guard disabled: this is what the user saw, eight attempts in a row."""

        from unittest.mock import patch

        with patch.object(tts_model_modes, "text_value", side_effect=lambda value: value):
            with self.assertRaises(AttributeError) as caught:
                self._generate("qwen3_tts_base", 2)

        self.assertIn("'int' object has no attribute 'lower'", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
