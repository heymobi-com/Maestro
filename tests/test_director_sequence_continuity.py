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
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.sequence_continuity import (  # noqa: E402
    MAX_RUN,
    OPTION,
    continuity_run,
    sequence_continuity_flags,
)

OMNI_REFERENCE = "omni_reference"


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

    def test_a_shot_that_declares_continuity_carries_the_previous_one(self):
        """Even across a change of place: the plan asked for it."""

        plans = [
            {"environment": "a warehouse", "continuity_strategy": "continuous"},
            {"environment": "a rainy street", "continuity_strategy": "continuous"},
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
            {"environment": "a rainy street", "_director_continuity_strategy": "continuous"},
        ]

        self.assertEqual(continuity_run(plans), [False, True])

    def test_what_the_plan_asks_for_is_not_trimmed_by_the_bound(self):
        """The bound is a floor under the fallback, not a ceiling over the author."""

        plans = [
            {"environment": "a warehouse", "continuity_strategy": "continuous"}
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
            {"environment": "a rainy street", "continuity_strategy": "continuous"},
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


if __name__ == "__main__":
    unittest.main()

