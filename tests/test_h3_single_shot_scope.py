"""A Director shot must not perform the whole film inside itself.

A real project's context described the film's entire camera arc and its closing
fade:

    "La iluminacion y el encuadre evolucionan progresivamente con el tono
     narrativo: comienza en planos medios ... pasa a close-ups ... vuelve a plano
     abierto ... alcanza primeros planos extremos ... y cierra en plano abierto
     acogedor con un fade lento en el outro."

That context is injected into every shot, and 106 of that run's 177 clips carried
the sentence verbatim. A Director clip is ONE continuous shot, so the model
performed the whole arc -- and the film's closing fade -- inside each clip, which
is the reported "sudden camera changes of seconds and constant fade-outs".
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.h3_dialogue import (  # noqa: E402
    _looks_like_a_multi_shot_body,
    _source_prompt_parts,
    compile_h3_clip_plans,
)

# Stated as rules, so the packer always injects it rather than deciding the body
# already carries the context.
FILM_CONTEXT = (
    "PROYECTO: video-podcast de dos personas.\n"
    "- Tono: documental intimo, 35mm, calido.\n"
    "- La camara se acerca progresivamente y cierra con un fade lento en el outro.\n"
    "- No anadas texto ni logotipos."
)


def _plan(context: str, prompt: str = "[Shot 1] Valeria (S1) habla a camara.") -> dict:
    return {
        "video_prompt": prompt,
        "image_prompt": "",
        "_director_h3_source_prompt": prompt,
        "_director_h3_compiled_prompt": "",
        "_director_dialogue_beats": [
            {"speaker_id": "(S1)", "spoken_text": "Hablemos del tema."},
        ],
        "_director_subjects_on_screen": [
            {"visual_description": "Valeria (S1), a young woman in a navy blazer."},
        ],
        "_director_project_context": context,
        "_director_h3_prompt_mode": "ref2va",
        "_director_h3_model_family": "ref2va",
        "_director_duration_sec": 8.0,
    }


class SingleShotScopeTests(unittest.TestCase):
    def test_a_shot_is_told_it_may_not_cut_or_fade(self):
        plan = _plan(FILM_CONTEXT)

        compile_h3_clip_plans([plan], prompt_modes=["ref2va"], durations=[8.0])

        self.assertIn("one continuous shot", plan["video_prompt"].lower())
        self.assertIn("do not cut", plan["video_prompt"].lower())
        self.assertIn("do not fade to black", plan["video_prompt"].lower())

    def test_the_project_context_is_labelled_as_the_whole_film(self):
        plan = _plan(FILM_CONTEXT)

        compile_h3_clip_plans([plan], prompt_modes=["ref2va"], durations=[8.0])

        self.assertIn("the whole film", plan["video_prompt"])
        self.assertIn(
            "do not perform its progression or its closing fade inside this shot",
            plan["video_prompt"],
        )

    def test_the_context_is_still_injected(self):
        # The fix must scope the context, not drop the user's rules.
        plan = _plan(FILM_CONTEXT)

        compile_h3_clip_plans([plan], prompt_modes=["ref2va"], durations=[8.0])

        self.assertIn("video-podcast de dos personas", plan["video_prompt"])

    def test_the_scope_line_does_not_need_a_context_to_apply(self):
        # The guard was once tied to context injection, on the theory that a
        # project without a whole-film context could not tempt a shot into
        # performing the arc. It is a property of the format either way -- a
        # clip is one continuous shot because the film is joined from clips --
        # and coupling the two cost a real run the guard on 113 of 177 shots.
        plan = _plan("")

        compile_h3_clip_plans([plan], prompt_modes=["ref2va"], durations=[8.0])

        self.assertIn("one continuous shot", plan["video_prompt"].lower())

    def test_the_scope_line_is_not_used_as_the_shot_summary(self):
        # The summary is the shot's one-line brief; a constraint belongs in the
        # visual description, not in the field that says what the shot shows.
        plan = _plan("")

        compile_h3_clip_plans([plan], prompt_modes=["ref2va"], durations=[8.0])
        summary = str(plan["video_prompt"]).split("summary:", 1)[1].split("\n", 1)[0]

        self.assertNotIn("one continuous shot", summary.lower())

    def test_a_compiled_body_is_not_mistaken_for_a_storyboard(self):
        # The compiled Context-IR body marks its shot as "[Shot 1]", which must
        # not read as "this body contains several shots".
        self.assertFalse(_looks_like_a_multi_shot_body("[Shot 1] Valeria speaks."))

    def test_a_storyboard_body_keeps_its_cuts(self):
        # Multi-shot LoRA mode emits one prompt holding several shots, where
        # cuts inside the clip are the point. The scope line must not forbid them.
        storyboard = (
            "Shot 1 (Medium, 4s): Valeria speaks.\n"
            "Shot 2 (Close-up, 4s): Ricardo answers.\n"
        )

        self.assertTrue(_looks_like_a_multi_shot_body(storyboard))
        plan = _plan(FILM_CONTEXT, prompt=storyboard)

        compile_h3_clip_plans([plan], prompt_modes=["ref2va"], durations=[8.0])

        body, _soundscape, _music, _blocks = _source_prompt_parts(
            storyboard, project_context=FILM_CONTEXT,
        )
        self.assertNotIn("one continuous shot", body.lower())


# Descriptive, so the packer is free to decide the body already carries it.
OVERLAPPING_CONTEXT = (
    "Valeria and Ricardo rehearse a warm conversation in a quiet loft studio."
)


class ScopeIsNotConditionalOnTheContextTests(unittest.TestCase):
    """The guard must not ride on the context-injection decision.

    Both lines used to be written inside one branch, so a shot whose body looked
    like it already carried the context lost the guard with it. On a real run
    that silently removed the guard from 113 of 177 shots -- a two-person
    project whose restrictions block mentions both names, so a handful of shared
    words made the whole context look redundant.
    """

    def test_a_body_that_appears_to_carry_the_context_still_gets_the_guard(self):
        plan = _plan(OVERLAPPING_CONTEXT, prompt=OVERLAPPING_CONTEXT)

        compile_h3_clip_plans(
            [plan], prompt_modes=["ref2va"], durations=[8.0],
        )
        out = str(plan["video_prompt"])

        self.assertIn("one continuous shot", out.lower())
        # ...and the context itself is still left out, so the decoupling is
        # precise rather than a blanket change of behaviour.
        self.assertNotIn("Project context (the whole film", out)

    def test_a_context_in_another_language_still_counts_as_rules(self):
        # Normalisation folds the bullets onto one line, which is how the
        # project's own Spanish restrictions block escaped the bullet test.
        normalised = (
            "RESTRICCIONES GLOBALES DEL PROYECTO (critico, no negociable): "
            "- Este proyecto tiene EXACTAMENTE DOS participantes. "
            "- NUNCA se genera (S3), (S4) ni ningun otro speaker ID adicional."
        )

        body, _soundscape, _music, _blocks = _source_prompt_parts(
            "[Shot 1] Valeria speaks.", project_context=normalised,
        )

        self.assertIn("Project context (the whole film", body)
        self.assertIn("no negociable", body)

    def test_a_long_context_is_not_redundant_on_a_few_shared_words(self):
        context = (
            "The documentary follows Valeria and Ricardo through a long "
            "evening in the studio while they rehearse, argue about timing, "
            "rework the lighting, and settle into the warm rhythm of the "
            "interview they came to record together over many hours."
        )
        body = "Valeria and Ricardo rehearse in the studio."

        _body, _soundscape, _music, _blocks = _source_prompt_parts(
            f"[Shot 1] {body}", project_context=context,
        )
        self.assertIn(
            "Project context (the whole film",
            _body,
            "four shared words must not stand in for a whole context",
        )


if __name__ == "__main__":
    unittest.main()
