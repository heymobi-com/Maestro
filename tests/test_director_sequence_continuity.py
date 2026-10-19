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
    carry_from_by_shot,
    continuity_run,
    duplicate_image_prompts,
    hands_over_the_frame,
    keyframe_steps,
    offered_reference_modes,
    place_continuity_groups,
    reference_mode,
    restale_neighbours,
    sequence_continuity_flags,
    shot_setups,
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


def _launch_source() -> str:
    with open(os.path.join(_APP_DIR, "launch.py"), encoding="utf-8") as handle:
        return handle.read()


def ast_unparse(node) -> str:
    import ast

    return ast.unparse(node)


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
        flags = sequence_continuity_flags(
            {OPTION: True}, OMNI_REFERENCE, omni_reference=OMNI_REFERENCE,
        )

        self.assertIs(flags["_omni_sequence_continuity"], True)
        # The second question the batch asks -- which clip a return resumes from -- travels
        # with the first, so one call in the pipeline carries both.
        self.assertIn("_omni_sequence_carry_from", flags)

    def test_a_saved_project_may_hold_the_option_as_a_string(self):
        """A checkbox sends a boolean; a params file on disk may hold text."""

        for value in ("true", "TRUE", "on", "yes", "1"):
            with self.subTest(value=value):
                self.assertIs(
                    sequence_continuity_flags(
                        {OPTION: value}, OMNI_REFERENCE, omni_reference=OMNI_REFERENCE,
                    )["_omni_sequence_continuity"],
                    True,
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

        self.assertEqual(shares["_omni_sequence_continuity"], [False, True, False])
        self.assertEqual(len(shares["_omni_sequence_carry_from"]), len(plans))

    def test_one_shot_still_gets_the_plain_form(self):
        """A caller with no shots in hand means every clip, and reads as it always did."""

        flags = sequence_continuity_flags(
            {OPTION: True}, OMNI_REFERENCE, omni_reference=OMNI_REFERENCE,
        )

        self.assertIs(flags["_omni_sequence_continuity"], True)
        self.assertEqual(flags["_omni_sequence_carry_from"], [])


class TheEngineReadsOneClipAtATimeTests(unittest.TestCase):
    """One clip's manifest is built once, so the decision has to travel per clip.

    The block is executed the way upstream executes it -- lifted out of launch.py by AST and
    run in a bare namespace -- because a name that only exists at module level is exactly what
    a lifted block cannot see. That is not hypothetical: the first version of this change
    called a module-level helper here and broke ``test_director_music_cues``.
    """

    def _manifests(self, flags, carry_from=None):
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
            omni_sequence_carry_from=carry_from,
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

    def test_each_clip_carries_the_clip_it_resumes_from(self):
        """The film position is the engine's own naming: ``carry_from=2`` means clip 2's frame."""

        manifests = self._manifests([True, True, True], carry_from=[None, 2, 1])

        self.assertEqual(
            [manifest["carry_from"] for manifest in manifests],
            [None, 2, 1],
        )

    def test_a_batch_that_states_nothing_carries_from_nowhere(self):
        manifests = self._manifests([True, True, True])

        self.assertEqual(
            [manifest["carry_from"] for manifest in manifests],
            [None, None, None],
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
    """These images are not drawn from each other, and that was measured rather than assumed.

    Reported from use, after the chaining shipped: "en cada imagen metio la misma pose de la
    referencia sin ninguna variacion y la siguiente imagen esta mas distorionada que la anterior,
    como si hicera copia de la copia". Measured on that run, consecutive keyframes differed by 1.8%
    and 4.1% of the frame -- the second merely accumulating on the first -- while the same project
    without chaining differed by 6-13%. A capable model reproduces the reference it is handed, so
    drawing a shot from the frame before it leaves the clip between them nothing to move through.
    """

    def test_a_continuous_shot_is_still_recognised_as_carrying_the_one_before(self):
        plans = [_image_shot("first"), _image_shot("second", "continuous")]

        self.assertEqual(continuity_run(plans), [False, True])

    def test_an_independent_shot_cuts(self):
        plans = [_image_shot("first"), _image_shot("second", "independent")]

        self.assertEqual(continuity_run(plans), [False, False])

    def test_the_image_stage_does_not_draw_a_shot_from_the_one_before_it(self):
        source = _pipeline_source()

        self.assertNotIn("chains = sequence_continuity.continuity_run(clip_plans)", source)
        self.assertNotIn('image_source = "previous" if (i < len(chains)', source)
        self.assertIn('image_source = plan.get("image_source", "original")', source)
        # The notice that two shots would render one frame stays: it is what says so before the
        # renders are paid for.
        self.assertIn("sequence_continuity.duplicate_image_prompts(clip_plans)", source)


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


class TheReferenceModeTests(unittest.TestCase):
    """A reference is there to keep the cast consistent while the text invents the frame.

    Reported from use: "en cada imagen metio la misma pose de la referencia sin ninguna variacion...
    las imagenes de referencia son de referencia para que invente los escenarios y personajes y
    mantenga consistencia pero no para que los copie exactamente". Qwen Image 2.1's own menu offers
    "Main image / landscape first" (KI) and "Reference images" (I), and declares I as its default --
    the Director preferred KI wherever it existed, which is what made every image a copy.
    """

    @staticmethod
    def defs(modes):
        return lambda _model_type: {"image_ref_choices": {"choices": [[m, m] for m in modes]}}

    def resolved(self, chosen):
        return ("some_model", {"image_reference_mode": chosen})

    def test_a_family_that_offers_references_is_asked_for_references(self):
        self.assertEqual(reference_mode(self.resolved("KI"), self.defs(["KI", "I"])), "I")

    def test_a_family_that_offers_only_the_main_image_keeps_it(self):
        # z_image and flux_uso declare no reference mode: handing them letters their own menu does not
        # list is a broken payload rather than a preference.
        self.assertEqual(reference_mode(self.resolved("KI"), self.defs(["KI"])), "KI")
        self.assertEqual(reference_mode(self.resolved("KI"), self.defs(["KIJ"])), "KI")

    def test_a_family_that_already_offers_only_references_is_unchanged(self):
        self.assertEqual(reference_mode(self.resolved("I"), self.defs(["I"])), "I")
        self.assertEqual(reference_mode(self.resolved("I"), None), "I")

    def test_no_reference_format_is_left_alone(self):
        self.assertEqual(reference_mode(self.resolved(""), self.defs(["KI", "I"])), "")

    def test_a_registry_that_cannot_be_read_keeps_the_chosen_format(self):
        self.assertEqual(reference_mode(self.resolved("KI"), None), "KI")

        def broken(_model_type):
            raise RuntimeError("no registry")

        self.assertEqual(reference_mode(self.resolved("KI"), broken), "KI")

    def test_an_assessment_without_the_model_type_still_answers(self):
        # With no model type to look up, the registry answers for nothing, so the chosen format stands.
        def nothing(_model_type):
            return {}

        self.assertEqual(reference_mode({"image_reference_mode": "KI"}, nothing), "KI")
        self.assertEqual(reference_mode(None, self.defs(["I", "KI"])), "")

    def test_the_offered_modes_come_from_the_models_own_menu(self):
        model_def = {"image_ref_choices": {"choices": [
            ["None", ""], ["Main image / landscape first", "KI"], ["Reference images", "I"],
        ]}}

        self.assertEqual(offered_reference_modes(model_def), {"", "KI", "I"})
        self.assertEqual(offered_reference_modes({}), set())
        self.assertEqual(offered_reference_modes(None), set())
        self.assertEqual(offered_reference_modes({"image_ref_choices": "nonsense"}), set())

    def test_the_image_stage_asks_for_the_mode_instead_of_reading_it(self):
        source = _pipeline_source()

        self.assertIn(
            "sequence_continuity.reference_mode(resolved, _wgp.get_model_def)", source,
        )


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


class TheHandoffFindsTheClipItJustRenderedTests(unittest.TestCase):
    """A frame is handed over only if the clip that produced it is found where it was written.

    Measured on the affected project's own render log: ``_omni_sequence_continuity`` was True for
    9 of its 10 clips and did reach the job, yet all ten bound the same four references and no
    ``[H3 Omni Sequence]`` picture was ever added -- and no ``[Continuation]`` frame was ever
    extracted, in any run of any project. One join explains all of it: ``wgp`` registers a render
    the way it printed it (``New video saved to Path: outputs/<workspace>/<file>.mp4``), which is
    relative to the working directory that ``launch.py`` pins to ``app``, while ``out_dir`` spells
    that same workspace. Joining the one onto the other asked for
    ``outputs/<workspace>/outputs/<workspace>/<file>.mp4``, never found the file, and both
    hand-offs fell through in silence.

    The lookup is launch.py's own, lifted out by AST and run the way upstream runs it, so this is
    the shipped rule and not a restatement of it.
    """

    def _loop(self):
        import ast

        tree = ast.parse(_launch_source())
        return next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.For)
            and ast.unparse(node.iter) == "reversed(task_files)"
            and any(
                isinstance(child, ast.Name) and child.id == "candidate"
                for child in ast.walk(node)
            )
        )

    def _lookup(self, out_dir, task_files):
        import ast

        program = compile(
            ast.Module(body=[self._loop()], type_ignores=[]), "<queue omni lookup>", "exec",
        )
        namespace = {
            "os": os, "out_dir": out_dir, "task_files": task_files, "latest_video": None,
        }
        exec(program, namespace)
        return namespace["latest_video"]

    def _workspace(self, root, *names):
        out_dir = os.path.join(root, "outputs", "prueba-2")
        os.makedirs(out_dir, exist_ok=True)
        for name in names:
            Path(os.path.join(out_dir, name)).touch()
        return out_dir

    def test_the_render_is_found_where_wgp_wrote_it(self):
        with tempfile.TemporaryDirectory() as root:
            out_dir = self._workspace(root, "clip1.mp4")
            registered = os.path.join("outputs", "prueba-2", "clip1.mp4")
            previous = os.getcwd()
            os.chdir(root)
            try:
                found = self._lookup(out_dir, [registered])
            finally:
                os.chdir(previous)

            self.assertEqual(found, os.path.realpath(os.path.join(out_dir, "clip1.mp4")))

    def test_the_join_that_used_to_be_there_is_why_nothing_was_found(self):
        """The regression, kept as the failing case it was."""

        with tempfile.TemporaryDirectory() as root:
            out_dir = self._workspace(root, "clip1.mp4")
            registered = os.path.join("outputs", "prueba-2", "clip1.mp4")

            self.assertFalse(os.path.isfile(os.path.join(out_dir, registered)))

    def test_another_projects_media_is_refused(self):
        """A shared-folder diff can register a concurrent dashboard operation's file."""

        with tempfile.TemporaryDirectory() as root:
            out_dir = self._workspace(root)
            other = os.path.join(root, "outputs", "podcast")
            os.makedirs(other)
            Path(os.path.join(other, "clip1.mp4")).touch()

            self.assertIsNone(self._lookup(out_dir, [os.path.join(other, "clip1.mp4")]))

    def test_a_file_that_is_not_a_video_is_skipped(self):
        with tempfile.TemporaryDirectory() as root:
            out_dir = self._workspace(root, "still.png")

            self.assertIsNone(self._lookup(out_dir, [os.path.join(out_dir, "still.png")]))

    def test_the_newest_registered_render_is_the_one_carried(self):
        with tempfile.TemporaryDirectory() as root:
            out_dir = self._workspace(root, "first.mp4", "second.mp4")
            previous = os.getcwd()
            os.chdir(root)
            try:
                found = self._lookup(out_dir, [
                    os.path.join("outputs", "prueba-2", "first.mp4"),
                    os.path.join("outputs", "prueba-2", "second.mp4"),
                ])
            finally:
                os.chdir(previous)

            self.assertEqual(os.path.basename(found), "second.mp4")

    def test_the_engine_resolves_it_as_written_and_not_against_out_dir(self):
        import ast

        self.assertNotIn("os.path.join(out_dir", ast.unparse(self._loop()))
        self.assertIn("os.path.realpath(candidate)", ast.unparse(self._loop()))
        # The rule leans on the working directory being the folder wgp wrote into.
        self.assertIn("os.chdir(_app_dir)", _launch_source())


class ThePlaceDecidesTheGroupTests(unittest.TestCase):
    """One place, stated in the same words, is one continuity group.

    Measured on a real 17-shot project: the whole film happens in one warehouse at night, its
    ``environment`` is byte-identical in all seventeen shots, and the plan still numbered eleven
    groups (``warehouse_piano_1`` ... ``warehouse_piano_11``). Every group boundary forces
    ``independent`` -- a hard cut that carries nothing -- so eleven shots re-staged the same
    tableau from the same four reference photos, which is the reported "los clips siguen siendo
    similares entre si".
    """

    PLACE = "Interior, cavernous industrial warehouse, night."

    def _shots(self, groups, places=None):
        places = places or [self.PLACE] * len(groups)
        return [
            {"continuity_group": group, "environment": place}
            for group, place in zip(groups, places)
        ]

    def test_numbered_groups_over_one_place_become_one_group(self):
        shots = self._shots([f"warehouse_piano_{index + 1}" for index in range(11)])

        place_continuity_groups(shots)

        self.assertEqual(
            [shot["continuity_group"] for shot in shots],
            ["warehouse_piano_1"] * 11,
        )

    def test_whitespace_and_case_do_not_split_a_place(self):
        shots = self._shots(
            ["warehouse_1", "warehouse_2"],
            [self.PLACE, "  interior,   cavernous industrial WAREHOUSE, night. "],
        )

        place_continuity_groups(shots)

        self.assertEqual(shots[1]["continuity_group"], "warehouse_1")

    def test_a_new_place_keeps_its_group(self):
        shots = self._shots(
            ["warehouse_1", "warehouse_2", "street_1"],
            [self.PLACE, self.PLACE, "Exterior, rain-soaked street, dawn."],
        )

        place_continuity_groups(shots)

        self.assertEqual(
            [shot["continuity_group"] for shot in shots],
            ["warehouse_1", "warehouse_1", "street_1"],
        )

    def test_a_project_that_states_no_place_is_left_alone(self):
        shots = self._shots(["warehouse_1", "warehouse_2"], ["", ""])

        place_continuity_groups(shots)

        self.assertEqual([shot["continuity_group"] for shot in shots], ["warehouse_1", "warehouse_2"])

    def test_the_planner_calls_it_before_it_decides_the_strategy(self):
        """The strategy and the blocking hand-off are both read from the group."""

        with open(
            os.path.join(_APP_DIR, "services", "director", "planners", "short_film.py"),
            encoding="utf-8",
        ) as handle:
            source = handle.read()
        body = source.split("def _prepare_h3_prompt_only_continuity", 1)[1]

        self.assertLess(
            body.index("place_continuity_groups(shot_dicts)"),
            body.index("requested ="),
        )


class TheCameraSetupDecidesWhatIsCarriedTests(unittest.TestCase):
    """A return to an earlier shot's setup resumes from that shot, not from its neighbour.

    Measured on a real 17-shot project: within one uninterrupted warehouse the plan cycled
    through ``Wide shot``, ``Hector's subjective POV``, ``Reaction shot`` and ``Close-up``, so
    the POV came back at clips 2, 5, 6, 8, 9, 11 and 13. Handed only the shot immediately before
    it, every one of those returns was composed from the character photographs again and
    re-staged the tableau it had already left -- "avanzan tres centímetros y vuelven al mismo
    loop desde ese otro punto".
    """

    def _plan(self, opening, strategy="continuous"):
        return {
            "environment": "Interior, one warehouse, night.",
            "continuity_strategy": strategy,
            "_director_opening_blocking": opening,
        }

    def test_the_setup_is_read_from_the_opening_label(self):
        setups = shot_setups([
            self._plan("Hector's subjective POV. Roman stands between her legs."),
            self._plan("Reaction shot. Hector tightens his jaw."),
            self._plan("Close-up. Aria's mouth opens."),
        ])

        self.assertEqual(
            setups, ["hector's subjective pov", "reaction shot", "close up"],
        )

    def test_the_structured_camera_plan_wins_when_the_plan_has_one(self):
        setups = shot_setups([{
            "environment": "one warehouse",
            "_director_opening_blocking": "Hector's subjective POV. Roman stands.",
            "_director_camera_plan": {"framing": "medium close-up", "angle": "low"},
        }])

        self.assertEqual(setups, ["medium close-up low"])

    def test_a_return_resumes_from_the_last_shot_of_that_setup(self):
        plans = [
            self._plan("Wide shot. Aria lies across the piano."),
            self._plan("Hector's subjective POV. Roman steps in.", strategy="independent"),
            self._plan("Reaction shot. Hector tightens his jaw.", strategy="independent"),
            self._plan("Hector's subjective POV. Aria's legs open wider."),
        ]

        carries = carry_from_by_shot(plans)

        # Clip 3 returns to the POV, and clip 2 is where that setup was last photographed --
        # clip 3's own neighbour is the reaction shot, and it is the wrong composition.
        self.assertEqual(carries, [None, None, None, 2])

    def test_a_setup_kept_for_a_second_shot_resumes_from_its_neighbour(self):
        plans = [
            self._plan("Close-up. Aria speaks."),
            self._plan("Close-up. Aria answers herself in a whisper."),
            self._plan("Close-up. Aria holds still."),
        ]

        carries = carry_from_by_shot(plans)

        # The ordinary continuation is the shot directly before, which is what the engine does
        # when nothing is named.
        self.assertEqual(carries, [None, 1, 2])

    def test_a_shot_that_cuts_carries_nothing_even_on_a_return(self):
        plans = [
            self._plan("Close-up. Aria speaks."),
            self._plan("Close-up. Aria holds still.", strategy="independent"),
        ]

        carries = carry_from_by_shot(plans)

        self.assertEqual(carries, [None, None])

    def test_a_shot_whose_setup_is_unstated_keeps_the_take_it_is_in(self):
        plans = [
            self._plan("Close-up. Aria speaks."),
            self._plan(""),
        ]

        carries = carry_from_by_shot(plans)

        # ``None`` is the ordinary rule -- resume from the shot directly before -- so an unstated
        # setup needs no special case: the neighbour is already the right answer.
        self.assertEqual(carries, [None, None])


class TheEngineCarriesTheNamedClipTests(unittest.TestCase):
    """The block that hands the frame over, lifted out of launch.py and run.

    A name that only exists at module level is exactly what a lifted block cannot see, so the
    receiving clip's own decision is read from the batch itself, and the frame it names is
    reused rather than re-extracted.
    """

    def _omni_block(self):
        """The whole hand-off, from the batch's own gate down to the reference it appends."""

        import ast

        tree = ast.parse(_launch_source())
        return next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and "sequence_info.get('omni_sequence_continuity')" in ast.unparse(node.test)
            and "carry_from" in ast.unparse(node)
        )

    def test_the_receiving_clip_owns_the_decision(self):
        source = ast_unparse(self._omni_block())

        self.assertIn("queue[task_idx + 1]", source)
        self.assertIn("next_info.get('omni_sequence_continuity')", source)

    def test_the_named_clip_supplies_the_frame(self):
        source = ast_unparse(self._omni_block())

        self.assertIn("next_info.get('carry_from') or task_no", source)
        # The frame already on disk is reused: re-extracting would have to re-read the clip.
        self.assertIn("if not os.path.isfile(continuity_path)", source)
    def _run(self, carry_from, frames, fresh=True):
        """Run the real block over a batch of three, and return the third clip's manifest."""

        import ast

        program = compile(
            ast.Module(body=[self._omni_block()], type_ignores=[]),
            "<queue omni handoff>", "exec",
        )
        namespace = {
            "os": os, "is_multiclip": True, "total_tasks": 3, "task_idx": 1,
            "task_no": 2, "job_id": "job", "_mc_model_def": {"fps": 24},
            "omni_sequence_temp_files": [], "out_dir": frames,
            "sequence_info": {"omni_sequence_continuity": True},
        }
        references = [{"type": "image", "path": os.path.join(frames, "aria.jpg"), "role": "Aria"}]
        queue = [
            {
                "params": {
                    "multi_clip_info": {"omni_sequence_continuity": True},
                    "minimax_h3_references": list(references),
                },
                "prompt": "prompt of clip 1",
            },
            {
                # The clip that just finished, and the only video this task registered.
                "params": {
                    "multi_clip_info": {"omni_sequence_continuity": True},
                    "minimax_h3_references": list(references),
                },
                "prompt": "prompt of clip 2",
            },
            {
                "params": {
                    "multi_clip_info": {
                        "omni_sequence_continuity": True, "carry_from": carry_from,
                    },
                    "minimax_h3_references": list(references),
                },
                "prompt": "Action: Aria waits.",
            },
        ]
        namespace.update(
            queue=queue,
            task_files=[os.path.join(frames, "clip1.mp4")],
        )
        exec(program, namespace)
        return queue[2]["params"]

    def _frames(self, root, *names):
        out_dir = os.path.join(root, "outputs", "prueba-2")
        os.makedirs(out_dir, exist_ok=True)
        Path(os.path.join(out_dir, "clip1.mp4")).touch()
        for name in names:
            Path(os.path.join(out_dir, name)).touch()
        return out_dir

    def test_a_return_reuses_the_named_clips_frame(self):
        with tempfile.TemporaryDirectory() as root:
            out_dir = self._frames(
                root,
                "_omni_sequence_continuity_job_1.png",
                "_omni_sequence_continuity_job_2.png",
            )

            # Clip 3 returns to the setup clip 1 staged, while clip 2 is the shot just finished.
            params = self._run(carry_from=1, frames=out_dir)
            attached = [ref for ref in params["minimax_h3_references"] if ref.get("role", "").startswith("Maestro rolling")]

            self.assertEqual(len(attached), 1)
            self.assertTrue(attached[0]["path"].endswith("_omni_sequence_continuity_job_1.png"))

    def test_the_clip_before_it_is_used_when_no_return_is_named(self):
        with tempfile.TemporaryDirectory() as root:
            out_dir = self._frames(
                root,
                "_omni_sequence_continuity_job_1.png",
                "_omni_sequence_continuity_job_2.png",
            )

            params = self._run(carry_from=None, frames=out_dir)
            attached = [ref for ref in params["minimax_h3_references"] if ref.get("role", "").startswith("Maestro rolling")]

            self.assertEqual(len(attached), 1)
            self.assertTrue(attached[0]["path"].endswith("_omni_sequence_continuity_job_2.png"))

    def test_the_appended_frame_is_bound_in_the_prompt(self):
        """The label reaches the prompt, so the model is told what the picture is for."""

        with tempfile.TemporaryDirectory() as root:
            out_dir = self._frames(root, "_omni_sequence_continuity_job_2.png")
            import ast

            program = compile(
                ast.Module(body=[self._omni_block()], type_ignores=[]),
                "<queue omni handoff>", "exec",
            )
            references = [{"type": "image", "path": os.path.join(out_dir, "aria.jpg")}]
            prompt = (
                "subject_definitions: <Subject 1> is Aria (S1).\n\n"
                "retention_analysis: preserve her wardrobe.\n\n"
                "detailed_description: [Shot 1] Action: Aria waits."
            )
            queue = [
                {"params": {"multi_clip_info": {}}, "prompt": "a"},
                {"params": {"multi_clip_info": {}}, "prompt": "b"},
                {
                    "params": {
                        "multi_clip_info": {
                            "omni_sequence_continuity": True, "carry_from": 2,
                        },
                        "minimax_h3_references": list(references),
                        "prompt": prompt,
                    },
                    "prompt": prompt,
                },
            ]
            namespace = {
                "os": os, "is_multiclip": True, "total_tasks": 3, "task_idx": 1,
                "task_no": 2, "job_id": "job", "_mc_model_def": {"fps": 24},
                "omni_sequence_temp_files": [], "out_dir": out_dir, "queue": queue,
                "task_files": [os.path.join(out_dir, "clip1.mp4")],
                "sequence_info": {"omni_sequence_continuity": True},
            }
            exec(program, namespace)

            updated = queue[2]["params"]["prompt"]
            self.assertIn("<Picture 2>", updated)
            self.assertIn("weak continuity reference", updated)

    def test_a_clean_cut_is_handed_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            out_dir = self._frames(root, "_omni_sequence_continuity_job_2.png")
            import ast

            program = compile(
                ast.Module(body=[self._omni_block()], type_ignores=[]),
                "<queue omni handoff>", "exec",
            )
            references = [{"type": "image", "path": os.path.join(out_dir, "aria.jpg")}]
            queue = [
                {"params": {"multi_clip_info": {}}, "prompt": "a"},
                {"params": {"multi_clip_info": {}}, "prompt": "b"},
                {
                    "params": {
                        "multi_clip_info": {"omni_sequence_continuity": False},
                        "minimax_h3_references": list(references),
                    },
                    "prompt": "a new place",
                },
            ]
            namespace = {
                "os": os, "is_multiclip": True, "total_tasks": 3, "task_idx": 1,
                "task_no": 2, "job_id": "job", "_mc_model_def": {"fps": 24},
                "omni_sequence_temp_files": [], "out_dir": out_dir, "queue": queue,
                "task_files": [os.path.join(out_dir, "clip1.mp4")],
                "sequence_info": {"omni_sequence_continuity": True},
            }
            exec(program, namespace)

            self.assertEqual(len(queue[2]["params"]["minimax_h3_references"]), 1)
            self.assertEqual(queue[2]["prompt"], "a new place")


if __name__ == "__main__":
    unittest.main()

