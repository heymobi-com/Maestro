"""Shot continuity is an option, and only one of the two mechanisms can serve H3.

Reported from use: "cada clip se genera como una escena unica y no como 'ultimo cuadro clip 1'
'primer cuadro clip 2' (...) esta funcion si existe en modo studio, pero en modo director no
aparenta dar este efecto nunca". Measured on the affected project (30 shots, strategy
``omni_reference``): 0 of 30 shots carried a start image and none declared a continuity
strategy, so every shot was its own scene.

The two mechanisms are not interchangeable. The final-frame handoff needs
``continuity_strategy == "extend_previous"`` **and** a shared ``continuity_group``, and all three
of its call sites sit inside ``BOUNDED_START_END`` -- unreachable for ``omni_reference``, so
declaring it per shot would do nothing. H3's own sequence continuity appends a late frame of the
clip just rendered as a composition-only reference, and its engine path is complete; it only
needs the job flag ``_omni_sequence_continuity``, which nothing in the repository ever set True.

These cases are ours, so they live in their own file.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.sequence_continuity import (  # noqa: E402
    MAX_RUN,
    OPTION,
    continuity_run,
    duplicate_image_prompts,
    hands_over_the_frame,
    keyframe_steps,
    restale_neighbours,
    sequence_continuity_flags,
    starts_to_keep,
    stitches_shots,
)
from services.director_video_strategy import (  # noqa: E402
    BOUNDED_START_END,
    OMNI_REFERENCE,
    ROLLING_WINDOW,
)

OMNI_REFERENCE = "omni_reference"

ROOT = os.path.abspath(os.path.join(_HERE, ".."))

# The two pairs this behaviour was measured on. The first came back as the same frame twice; the
# second rendered differently, which is where the threshold sits between them.
DUPLICATE_A = (
    "create new scene, warehouse interior at night. A woman in red sits on a piano bench, legs "
    "crossed, holding dark glasses at her chest. The man in black leather stands before her holding "
    "his belt. Stark spotlight illuminates the contact area."
)
DUPLICATE_B = (
    "create new scene, warehouse interior at night. A woman in red sits on a piano bench, legs "
    "crossed, holding dark glasses at her chest. The man in black leather stands before her holding "
    "his belt buckle. Stark spotlight illuminates the contact area."
)
DIFFERENT_A = (
    "a glass greenhouse filled with tropical plants, misty air and dappled light, a botanist in a "
    "linen coat"
)
DIFFERENT_B = "a glass greenhouse at dawn, misty air, rows of orchids, a gardener kneeling with a trowel"

# The family whose engine hands the frame over itself: LTX 2 renders a multi-clip batch as windows
# and starts a clip from the previous one's real last frame when the clip carries none of its own.
LTX = {"architecture": "ltx2_22B", "sliding_window": True}


def _pipeline_source() -> str:
    with open(
        os.path.join(ROOT, "app", "services", "director_pipeline.py"), encoding="utf-8",
    ) as handle:
        return handle.read()


def _image_shot(prompt, strategy=None):
    shot = {"image_prompt": prompt}
    if strategy:
        shot["_director_continuity_strategy"] = strategy
    return shot


def _shot(environment, section="verse"):
    return {"environment": environment, "metadata": {"section": section}}


class TheSequenceContinuityOptionTests(unittest.TestCase):
    def test_it_is_off_unless_the_project_asks_for_it(self):
        self.assertEqual(
            sequence_continuity_flags({}, OMNI_REFERENCE, omni_reference=OMNI_REFERENCE),
            {},
        )
        self.assertEqual(
            sequence_continuity_flags(
                {OPTION: False}, OMNI_REFERENCE, omni_reference=OMNI_REFERENCE,
            ),
            {},
        )

    def test_it_reaches_the_engine_when_the_project_asks_for_it(self):
        self.assertEqual(
            sequence_continuity_flags(
                {OPTION: True}, OMNI_REFERENCE, omni_reference=OMNI_REFERENCE,
            ),
            {"_omni_sequence_continuity": True},
        )

    def test_a_saved_project_may_hold_the_option_as_a_string(self):
        """A checkbox sends a boolean; a params file on disk may hold text."""

        for value in ("true", "TRUE", "on", "yes", "1"):
            with self.subTest(value=value):
                self.assertEqual(
                    sequence_continuity_flags(
                        {OPTION: value}, OMNI_REFERENCE, omni_reference=OMNI_REFERENCE,
                    ),
                    {"_omni_sequence_continuity": True},
                )

    def test_it_stays_off_for_a_strategy_whose_mechanism_is_the_other_one(self):
        """The final-frame handoff is BOUNDED_START_END's; this flag is H3's."""

        for strategy in ("bounded_start_end", "rolling_window", ""):
            with self.subTest(strategy=strategy):
                self.assertEqual(
                    sequence_continuity_flags(
                        {OPTION: True}, strategy, omni_reference=OMNI_REFERENCE,
                    ),
                    {},
                )

    def test_the_batch_params_carry_the_decision(self):
        """The wiring, pinned: the generation params are where the engine reads it."""

        import inspect

        from services import director_pipeline

        source = inspect.getsource(director_pipeline._run_video_generation)

        self.assertIn("sequence_continuity_flags(", source)
        self.assertIn("omni_reference=OMNI_REFERENCE", source)


class ThePerShotDecisionTests(unittest.TestCase):
    """Which shots carry the one before -- the point of the whole option."""

    def test_the_first_shot_has_nothing_to_carry(self):
        self.assertEqual(continuity_run([_shot("a warehouse")]), [False])

    def test_a_shot_in_the_same_place_and_section_carries_the_previous_one(self):
        self.assertEqual(
            continuity_run([_shot("a warehouse"), _shot("a warehouse")]),
            [False, True],
        )

    def test_a_change_of_environment_is_a_cut(self):
        """Carrying the frame there would drag the old world into the new shot."""

        self.assertEqual(
            continuity_run([_shot("a warehouse"), _shot("a rainy street")]),
            [False, False],
        )

    def test_a_change_of_section_is_a_cut(self):
        """The song moving is an edit, even in the same place."""

        self.assertEqual(
            continuity_run([_shot("a warehouse", "verse"), _shot("a warehouse", "chorus")]),
            [False, False],
        )

    def test_a_run_is_bounded_so_the_film_keeps_its_edit(self):
        shots = [_shot("a warehouse") for _ in range(MAX_RUN + 2)]

        flags = continuity_run(shots)

        self.assertEqual(flags[0], False)
        self.assertLessEqual(flags.count(True), len(shots) - 1)
        # The run breaks as soon as it reaches the bound, then starts again.
        self.assertEqual(flags, [False] + [True] * (MAX_RUN - 1) + [False, True])

    def test_the_same_place_in_fresh_words_is_not_evidence_enough(self):
        """Measured: 30 shots described one warehouse in new words every time.

        A looser test decided those shots on the length of their prose, which is a coin toss,
        and a wrong carry drags the previous shot's world into the new one.
        """

        plans = [
            _shot("Cavernous, brutalist warehouse with a glossy, mirror-like floor"),
            _shot("Cavernous brutalist warehouse. Glossy, mirror-like floor."),
        ]

        self.assertEqual(continuity_run(plans), [False, False])

    def test_the_same_declared_scene_carries_the_previous_shot(self):
        """A plan's own stable name for place and time is a statement, not a guess."""

        plans = [
            _shot("a bare room with one window"),
            _shot("seen from the doorway, the same single window"),
        ]
        plans[0]["continuity_group"] = "room_dawn_1"
        plans[1]["continuity_group"] = "room_dawn_1"

        self.assertEqual(continuity_run(plans), [False, True])

    def test_a_different_declared_scene_cuts(self):
        """Two scenes described in the same words are still two scenes."""

        plans = [_shot("a bare room"), _shot("a bare room")]
        plans[0]["continuity_group"] = "room_dawn_1"
        plans[1]["continuity_group"] = "roof_noon_1"

        self.assertEqual(continuity_run(plans), [False, False])

    def test_one_side_naming_a_scene_does_not_decide_it(self):
        """A group on one shot only is not an agreement about a place."""

        plans = [_shot("a bare room"), _shot("a bare room")]
        plans[1]["continuity_group"] = "room_dawn_1"

        self.assertEqual(continuity_run(plans), [False, True])

    def test_the_very_same_place_stated_in_one_section_carries(self):
        plans = [
            _shot("the roof of a moving train", "chorus"),
            _shot("the roof of a moving train", "chorus"),
        ]

        self.assertEqual(continuity_run(plans), [False, True])

    def test_a_different_place_in_the_same_section_cuts(self):
        plans = [
            _shot("a bare room with one window"),
            _shot("the roof of a moving train"),
        ]

        self.assertEqual(continuity_run(plans), [False, False])

    def test_a_shot_silent_about_place_cuts(self):
        """Seven of one project's 30 shots named no place at all; none is guessed."""

        self.assertEqual(
            continuity_run([{"metadata": {}}, {"metadata": {}}]),
            [False, False],
        )

    def test_no_plans_is_no_flags(self):
        self.assertEqual(continuity_run([]), [])
        self.assertEqual(continuity_run(None), [])


class ThePlanDecidesTests(unittest.TestCase):
    """The planning chooses the effect that carries the idea, shot by shot."""

    def test_a_shot_that_asks_to_carry_on_carries_the_previous_one(self):
        """Even across a change of place: the plan asked for it."""

        plans = [
            {"environment": "a warehouse", "continuity_strategy": "extend_previous"},
            {"environment": "a rainy street", "continuity_strategy": "extend_previous"},
        ]

        self.assertEqual(continuity_run(plans), [False, True])

    def test_continuous_is_the_ordinary_same_scene_shot_and_carries_the_world(self):
        """Inside one place and one moment this is the normal answer, not a special case."""

        plans = [
            {"environment": "a warehouse", "continuity_strategy": "continuous"},
            {"environment": "a warehouse", "continuity_strategy": "continuous"},
        ]

        self.assertEqual(continuity_run(plans), [False, True])

    def test_continuous_carries_even_when_the_shot_restates_the_place_its_own_way(self):
        """The declaration decides, not the wording of the environment prose."""

        plans = [
            {"environment": "Cavernous, brutalist warehouse with a glossy floor",
             "continuity_strategy": "continuous"},
            {"environment": "Warehouse, center stage, under the stark spotlight",
             "continuity_strategy": "continuous"},
        ]

        self.assertEqual(continuity_run(plans), [False, True])

    def test_a_shot_that_declares_a_cut_cuts_even_in_the_same_place(self):
        """The film needs its edit, and a shared world alone must not overrule it."""

        plans = [
            {"environment": "a warehouse", "continuity_strategy": "independent"},
            {"environment": "a warehouse", "continuity_strategy": "independent"},
        ]

        self.assertEqual(continuity_run(plans), [False, False])

    def test_the_persisted_key_decides_too(self):
        """A resumed batch reads the shot state the plan files under its own key."""

        plans = [
            {"environment": "a warehouse"},
            {"environment": "a rainy street", "_director_continuity_strategy": "extend_previous"},
        ]

        self.assertEqual(continuity_run(plans), [False, True])

    def test_what_the_plan_asks_for_is_not_trimmed_by_the_bound(self):
        """The bound is a floor under the fallback, not a ceiling over the author."""

        plans = [
            {"environment": "a warehouse", "continuity_strategy": "extend_previous"}
            for _ in range(MAX_RUN + 2)
        ]

        self.assertEqual(continuity_run(plans), [False] + [True] * (MAX_RUN + 1))

    def test_an_unknown_strategy_falls_back_to_the_derived_rule(self):
        """A planner's typo must not silently become a cut everywhere."""

        plans = [
            {"environment": "a warehouse", "continuity_strategy": "seamless"},
            {"environment": "a warehouse", "continuity_strategy": "seamless"},
        ]

        self.assertEqual(continuity_run(plans), [False, True])

    def test_the_batch_is_handed_one_flag_per_shot(self):
        plans = [
            {"environment": "a warehouse", "continuity_strategy": "independent"},
            {"environment": "a rainy street", "continuity_strategy": "extend_previous"},
            {"environment": "a rainy street", "continuity_strategy": "independent"},
        ]

        shares = sequence_continuity_flags(
            {OPTION: True}, OMNI_REFERENCE,
            omni_reference=OMNI_REFERENCE, clip_plans=plans,
        )

        self.assertEqual(shares, {"_omni_sequence_continuity": [False, True, False]})

    def test_one_shot_still_gets_the_plain_form(self):
        """A caller with no shots in hand means every clip, and reads as it always did."""

        self.assertEqual(
            sequence_continuity_flags({OPTION: True}, OMNI_REFERENCE, omni_reference=OMNI_REFERENCE),
            {"_omni_sequence_continuity": True},
        )


class TheEngineReadsOneClipAtATimeTests(unittest.TestCase):
    """One clip's manifest is built once, so the decision has to travel per clip.

    The block is executed the way upstream executes it -- lifted out of launch.py by AST and
    run in a bare namespace -- because a name that only exists at module level is exactly what
    a lifted block cannot see. That is not hypothetical: the first version of this change
    called a module-level helper here and broke ``test_director_music_cues``.
    """

    def _manifests(self, flags):
        import ast

        with open(os.path.join(_APP_DIR, "launch.py"), "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        loop = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.For) and any(
                isinstance(child, ast.Assign) and isinstance(child.value, ast.IfExp)
                and any(isinstance(t, ast.Name) and t.id == "clip_frames" for t in child.targets)
                for child in node.body
            )
        )
        start = next(
            index for index, node in enumerate(loop.body)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "clip_frames" for t in node.targets)
        )
        stop = next(
            index for index, node in enumerate(loop.body)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                and t.slice.value == "multi_clip_info"
                for t in node.targets
            )
        )
        program = compile(
            ast.Module(body=loop.body[start:stop + 1], type_ignores=[]),
            "<queue clip timing>", "exec",
        )
        namespace = dict(
            per_clip_frames=[192, 175, 243], per_clip_output_frames=None, sw_size=243,
            _mc_bounded_director=True, _mc_model_def={"frames_maximum": 345},
            _mc_min_f=124, _mc_fs=17, has_end=False, _mc_trim_end_frames=False,
            cumulative_offset=0, total_trimmed_frames=0, group_id="test", clip_count=3,
            multi_clip_audio_start_sec=0, multi_clip_concat_audio="song.wav",
            omni_sequence_continuity=flags, omni_sequence_target_frames=0,
            multi_clip_defer_concat=False,
        )
        manifests = []
        for index in range(3):
            namespace.update(i=index, clip_params={})
            exec(program, namespace)
            manifests.append(namespace["clip_params"]["multi_clip_info"])
        return manifests

    def test_each_clip_carries_its_own_decision(self):
        manifests = self._manifests([False, True, False])

        self.assertEqual(
            [manifest["omni_sequence_continuity"] for manifest in manifests],
            [False, True, False],
        )

    def test_one_plain_flag_still_covers_every_clip(self):
        """A single render and the Studio path hand over one value, not a list."""

        manifests = self._manifests(True)

        self.assertEqual(
            [manifest["omni_sequence_continuity"] for manifest in manifests],
            [True, True, True],
        )


class ThePlannerCanDeclareItTests(unittest.TestCase):
    """A film planner that cannot say where continuity is needed cannot decide."""

    def test_the_music_shot_schema_lets_a_shot_declare_its_strategy(self):
        from services.director.planners import music_video

        schema = music_video._music_shot_schema(2, include_image_fields=True)
        item = schema["items"]

        self.assertIn("continuity_strategy", item["properties"])
        self.assertIn("continuity_strategy", item["required"])

    def test_the_music_planner_stopped_forcing_every_shot_independent(self):
        import inspect

        from services.director.planners import music_video

        source = inspect.getsource(music_video.MusicVideoPlanner)

        self.assertIn('raw.get("continuity_strategy")', source)


class TheShotImageChainTests(unittest.TestCase):
    """A shot that carries the one before it is drawn from it, not from the character anchor.

    Reported from use: "tres de las 5 son exactamente iguales... esas imagenes no sirven ya que deben
    tener una secuencia logica en las escenas del video no repetirse iguales". Every start image was
    regenerated from the same anchor, so consecutive shots re-invented the same frame and the scene
    started over at every clip.
    """

    def test_a_continuous_shot_carries_the_image_before_it(self):
        plans = [_image_shot("first"), _image_shot("second", "continuous")]
        self.assertEqual(continuity_run(plans), [False, True])

    def test_an_independent_shot_is_drawn_from_the_anchor(self):
        plans = [_image_shot("first"), _image_shot("second", "independent")]
        self.assertEqual(continuity_run(plans), [False, False])

    def test_the_image_stage_asks_instead_of_trusting_the_plans_own_field(self):
        # The planner wrote image_source "original" on every shot of the reported project while also
        # declaring them continuous: the declaration is the one that knows the story continues.
        source = _pipeline_source()

        self.assertIn("chains = sequence_continuity.continuity_run(clip_plans)", source)
        self.assertIn(
            'image_source = "previous" if (i < len(chains) and chains[i]) '
            'else plan.get("image_source", "original")',
            source,
        )


class TheStitchTests(unittest.TestCase):
    """Where a shot has to end, so the shot after it starts on that same frame.

    "de otro modo genera siempre un primer cuadro en cada clip que se regresa el video al punto
    inicial perdiendo la coherencia con la imagen del final del clip anterior".
    """

    def test_h3s_bounded_strategy_still_stitches(self):
        self.assertTrue(stitches_shots(BOUNDED_START_END, {}))

    def test_ltx2_stitches_because_its_engine_reads_an_end_frame(self):
        # models/ltx2/ltx2.py carries image_end into its conditioning entries, so the stitch is a
        # frame the model is actually asked to land on rather than one it ignores.
        self.assertTrue(stitches_shots(ROLLING_WINDOW, {"architecture": "ltx2_22B"}))

    def test_a_family_whose_engine_was_not_verified_is_left_alone(self):
        self.assertFalse(stitches_shots(ROLLING_WINDOW, {"architecture": "wan2_2"}))
        self.assertFalse(stitches_shots(ROLLING_WINDOW, {}))
        self.assertFalse(stitches_shots(ROLLING_WINDOW, None))

    def test_the_video_stage_asks_for_the_stitch_by_name(self):
        source = _pipeline_source()

        self.assertIn(
            "and sequence_continuity.stitches_shots(director_strategy, model_def)", source,
        )


class TheDuplicatePromptNoticeTests(unittest.TestCase):
    """Two shots that would render one frame are named before both renders are paid for."""

    def test_the_measured_duplicate_is_reported(self):
        findings = duplicate_image_prompts(
            [{"image_prompt": DUPLICATE_A}, {"image_prompt": DUPLICATE_B}], announce=False,
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["shot"], 2)
        self.assertEqual(findings[0]["after_shot"], 1)
        self.assertGreaterEqual(findings[0]["similarity"], 0.85)

    def test_the_measured_pair_that_rendered_differently_is_not_reported(self):
        findings = duplicate_image_prompts(
            [{"image_prompt": DIFFERENT_A}, {"image_prompt": DIFFERENT_B}], announce=False,
        )

        self.assertEqual(findings, [])

    def test_it_says_so_in_the_log_when_the_pipeline_asks(self):
        with patch("builtins.print") as printed:
            duplicate_image_prompts(
                [{"image_prompt": DUPLICATE_A}, {"image_prompt": DUPLICATE_B}],
            )

        line = " ".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertIn("Shots 1 and 2 carry", line)
        self.assertIn("the same image prompt", line)

    def test_the_control_asks_without_a_line_per_poll(self):
        with patch("builtins.print") as printed:
            duplicate_image_prompts(
                [{"image_prompt": DUPLICATE_A}, {"image_prompt": DUPLICATE_B}], announce=False,
            )

        self.assertEqual(printed.call_args_list, [])

    def test_nothing_to_compare_is_no_finding(self):
        self.assertEqual(duplicate_image_prompts([]), [])
        self.assertEqual(duplicate_image_prompts(None), [])
        self.assertEqual(duplicate_image_prompts([{"image_prompt": ""}, {"image_prompt": "x"}]), [])
        self.assertEqual(duplicate_image_prompts(["not a shot", {}]), [])

    def test_the_image_stage_asks_once_before_rendering(self):
        source = _pipeline_source()

        self.assertIn("sequence_continuity.duplicate_image_prompts(clip_plans)", source)


class TheFrameHandoverTests(unittest.TestCase):
    """The shot before hands over the frame it really ended on, rather than a similar one.

    Reported from use: "no la secuencia que se supone deberia existir para que el video tenga una
    continuidad logica de la ultima escena del clip anterior... no tiene sentido generar todas
    imagenes desde el principio sino generar un clip y de ahi tomar la ultima imagen como referencia
    para el siguiente". The queue already does exactly that; the Director was the reason it never
    fired, because it sent a start image for every shot.
    """

    def test_a_windowed_family_hands_the_frame_over(self):
        self.assertTrue(hands_over_the_frame(ROLLING_WINDOW, LTX))

    def test_a_bounded_family_keeps_its_planned_start(self):
        self.assertFalse(hands_over_the_frame(BOUNDED_START_END, LTX))
        self.assertFalse(hands_over_the_frame(OMNI_REFERENCE, LTX))

    def test_a_family_that_cannot_window_keeps_it_too(self):
        self.assertFalse(hands_over_the_frame(ROLLING_WINDOW, {"architecture": "ltx2_22B"}))
        self.assertFalse(hands_over_the_frame(ROLLING_WINDOW, None))

    def test_the_first_shot_keeps_its_image(self):
        flags = starts_to_keep(
            [_image_shot("a"), _image_shot("b", "continuous")], ROLLING_WINDOW, LTX,
        )

        self.assertEqual(flags, [True, False])

    def test_an_ordinary_cut_keeps_its_image(self):
        flags = starts_to_keep(
            [_image_shot("a"), _image_shot("b", "independent")], ROLLING_WINDOW, LTX,
        )

        self.assertEqual(flags, [True, True])

    def test_a_bounded_family_keeps_every_image(self):
        flags = starts_to_keep(
            [_image_shot("a"), _image_shot("b", "continuous")], BOUNDED_START_END, LTX,
        )

        self.assertEqual(flags, [True, True])

    def test_there_is_one_flag_per_shot_even_when_a_plan_is_unreadable(self):
        # A plan that says nothing is a cut, which keeps its image: nothing is inferred from silence.
        flags = starts_to_keep(
            [_image_shot("a"), "not a plan", _image_shot("c", "continuous")], ROLLING_WINDOW, LTX,
        )

        self.assertEqual(flags, [True, True, False])

    def test_no_plans_is_no_flags(self):
        self.assertEqual(starts_to_keep([], ROLLING_WINDOW, LTX), [])
        self.assertEqual(starts_to_keep(None, ROLLING_WINDOW, LTX), [])

    def test_the_video_stage_asks_instead_of_deciding_it_itself(self):
        source = _pipeline_source()

        self.assertIn(
            "keep_start = sequence_continuity.starts_to_keep(", source,
        )
        self.assertIn("and keep_start[i]", source)
        # The scheduled frames it is arranged around must still exist by the time it is read.
        self.assertLess(
            source.index("keep_start = sequence_continuity.starts_to_keep("),
            source.index("and keep_start[i]"),
        )


class TheKeyframeStepTests(unittest.TestCase):
    """How far each keyframe moved, measured on the images rather than on the prompt that asked.

    Reported from use: "veo imagenes de inicio muy similares entre un clip y otro... genera siempre la
    imagen del primer clip con una minima o ninguna variacion". Measured on that run, consecutive
    keyframes differed by 6-12% of pixels, which is what this readout names.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def image(self, name, left, right=None):
        from PIL import Image

        picture = Image.new("RGB", (64, 64), left)
        if right is not None:
            for x in range(32, 64):
                for y in range(64):
                    picture.putpixel((x, y), right)
        picture.save(self.tmp / name)
        return name

    def test_two_identical_frames_are_a_zero_step(self):
        same = self.image("a.png", (10, 10, 10))

        steps = keyframe_steps([same, same], str(self.tmp))

        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["change"], 0.0)
        self.assertTrue(steps[0]["still"])

    def test_a_half_frame_of_change_is_not_still(self):
        first = self.image("a.png", (10, 10, 10))
        second = self.image("b.png", (10, 10, 10), (250, 250, 250))

        steps = keyframe_steps([first, second], str(self.tmp))

        self.assertGreater(steps[0]["change"], 40)
        self.assertFalse(steps[0]["still"])

    def test_the_step_names_the_shots_it_belongs_to(self):
        same = self.image("a.png", (10, 10, 10))

        steps = keyframe_steps([same, same, same], str(self.tmp))

        self.assertEqual([(step["after_shot"], step["shot"]) for step in steps], [(1, 2), (2, 3)])

    def test_one_frame_is_no_step(self):
        self.assertEqual(keyframe_steps([self.image("a.png", (0, 0, 0))], str(self.tmp)), [])
        self.assertEqual(keyframe_steps([], str(self.tmp)), [])
        self.assertEqual(keyframe_steps(None, str(self.tmp)), [])

    def test_a_frame_that_is_gone_is_not_measured_rather_than_guessed(self):
        # A boundary whose two frames cannot both be read is not reported: an unmeasurable step is not
        # a step, and the pipeline already refuses to render a shot whose start image is missing.
        first = self.image("a.png", (10, 10, 10))

        steps = keyframe_steps([first, "missing.png", first], str(self.tmp))

        self.assertEqual(steps, [])

    def test_an_absolute_path_is_read_where_it_points(self):
        first = self.image("a.png", (10, 10, 10))
        absolute = str(self.tmp / first)

        steps = keyframe_steps([absolute, absolute], str(self.tmp))

        self.assertEqual(steps[0]["change"], 0.0)


class TheNeighbourGoesStaleTests(unittest.TestCase):
    """Replacing a stitched shot's image leaves the shot that ended on it out of date."""

    @staticmethod
    def _project(strategy="continuous", architecture="ltx2_22B"):
        return {
            "video_execution_profile": {
                "video_strategy": ROLLING_WINDOW, "architecture": architecture,
            },
            "clips": [
                {"planned_clip": {"_director_continuity_strategy": "independent"}},
                {"planned_clip": {"_director_continuity_strategy": strategy}},
            ],
        }

    def test_the_shot_that_ended_on_it_is_marked(self):
        self.assertEqual(restale_neighbours(self._project(), 1), [0])

    def test_a_cut_is_not_marked(self):
        self.assertEqual(restale_neighbours(self._project(strategy="independent"), 1), [])

    def test_the_first_shot_has_no_neighbour(self):
        self.assertEqual(restale_neighbours(self._project(), 0), [])

    def test_a_project_that_does_not_stitch_marks_nothing(self):
        self.assertEqual(restale_neighbours(self._project(architecture="wan2_2"), 1), [])

    def test_a_state_that_says_nothing_is_left_alone(self):
        self.assertEqual(restale_neighbours({}, 1), [])
        self.assertEqual(restale_neighbours({"clips": []}, 1), [])
        self.assertEqual(
            restale_neighbours({"video_execution_profile": {}, "clips": [{}]}, 1), [],
        )

    def test_the_rerun_marks_it_alongside_its_own_shot(self):
        source = _pipeline_source()

        self.assertIn(
            "for neighbour in sequence_continuity.restale_neighbours(s, clip_index)", source,
        )


if __name__ == "__main__":
    unittest.main()

