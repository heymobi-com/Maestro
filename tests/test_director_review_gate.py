"""Stopping before the video, and being able to act while stopped.

Reported from use: "The workflow stops after generating images, but I'm unsatisfied with the result.
The images have errors. I want to correct them by regenerating, but the system doesn't allow it...
when I click regenerate image, it says 'pipeline is still active, try again shortly'."

Three things were true at once and each alone lost the run: the review pause was skipped in auto
mode (the default), a parked pipeline refused every correction (measured: a paused pipeline, a live
thread, and both together each answered 409), and nothing in the interface could leave the pause.
These cases are ours, so they live in their own file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services import director_pipeline as pipeline  # noqa: E402
from services import director_review_gate as gate  # noqa: E402

PID = "abc12345"

PIPELINE_STUB = {
    "version": 1,
    "pipeline_id": PID,
    "status": "paused",
    "clips": [
        {"index": 0, "start_image_filename": "shot-1.png"},
        {"index": 1, "start_image_filename": "shot-2.png"},
    ],
}


class GateTestCase(unittest.TestCase):
    """A real settings path and a real state file, both in a temporary folder."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.config = patch.object(
            gate, "_config_path", return_value=str(self.tmp / "director_review_gate.json"),
        )
        self.config.start()
        self.addCleanup(self.config.stop)
        self.out_dir = self.tmp / "outputs" / "bar"
        self.out_dir.mkdir(parents=True)
        self.state_file = self.out_dir / f"_director_pipeline_{PID}.json"

    def write_state(self, **changes):
        state = dict(PIPELINE_STUB)
        state.update(changes)
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        return state


class TheSettingTests(GateTestCase):
    def test_it_is_on_until_turned_off(self):
        self.assertTrue(gate.enabled())  # no file at all is the default, not an error
        self.assertEqual(gate.state(), {"enabled": True})

    def test_it_remembers_being_turned_off(self):
        gate.set_enabled(False)

        self.assertFalse(gate.enabled())
        self.assertEqual(gate.state(), {"enabled": False})
        self.assertTrue((self.tmp / "director_review_gate.json").is_file())

    def test_a_damaged_file_falls_back_to_on(self):
        (self.tmp / "director_review_gate.json").write_text("not json", encoding="utf-8")

        self.assertTrue(gate.enabled())

    def test_turning_it_off_replaces_a_damaged_file(self):
        (self.tmp / "director_review_gate.json").write_text("not json", encoding="utf-8")

        gate.set_enabled(False)

        self.assertFalse(gate.enabled())

    def test_auto_mode_no_longer_skips_the_gate(self):
        # Auto mode used to mean "no review at all", which is how a run reached the video stage with
        # nobody having looked at the images it had just invented.
        self.assertTrue(gate.should_pause(True))
        gate.set_enabled(False)
        self.assertFalse(gate.should_pause(True))

    def test_a_project_that_never_automates_keeps_upstreams_own_review(self):
        gate.set_enabled(False)

        self.assertTrue(gate.should_pause(False))


class WhatCountsAsParkedTests(unittest.TestCase):
    def test_a_pipeline_waiting_to_be_reviewed_is_parked(self):
        self.assertTrue(gate.parked({"status": "paused", "pause_reason": "review_images"}))
        self.assertTrue(gate.parked({"status": "paused", "pause_reason": "review_prompts"}))

    def test_a_running_pipeline_is_not(self):
        self.assertFalse(gate.parked({"status": "running"}))
        self.assertFalse(gate.parked({"status": "paused"}))
        self.assertFalse(gate.parked({"status": "paused", "pause_reason": "stopping"}))
        self.assertFalse(gate.parked({}))
        self.assertFalse(gate.parked(None))


class TheCorrectionsReachTheRenderTests(GateTestCase):
    """A correction made while the run waits has to be the image the video renders from."""

    def test_a_corrected_image_replaces_the_one_the_run_generated(self):
        self.write_state()
        self.write_state(clips=[
            {"index": 0, "start_image_filename": "shot-1.png"},
            {"index": 1, "start_image_filename": "shot-2-redone.png"},
        ])
        (self.out_dir / "shot-2-redone.png").write_bytes(b"png")

        corrected = gate.reviewed_images(
            PID, ["shot-1.png", "shot-2.png"], str(self.out_dir),
        )

        self.assertEqual(corrected, ["shot-1.png", "shot-2-redone.png"])

    def test_it_says_which_shots_it_corrected(self):
        self.write_state(clips=[
            {"index": 0, "start_image_filename": "shot-1.png"},
            {"index": 1, "start_image_filename": "shot-2-redone.png"},
        ])
        (self.out_dir / "shot-2-redone.png").write_bytes(b"png")

        with patch("builtins.print") as printed:
            gate.reviewed_images(PID, ["shot-1.png", "shot-2.png"], str(self.out_dir))

        line = " ".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertIn("rendering from the corrected image", line)
        self.assertIn("shot 2", line)

    def test_a_name_that_no_longer_exists_is_not_a_correction(self):
        self.write_state()

        corrected = gate.reviewed_images(
            PID, ["shot-1.png", "shot-2-gone.png"], str(self.out_dir),
        )

        self.assertEqual(corrected, ["shot-1.png", "shot-2-gone.png"])

    def test_an_untouched_run_is_left_exactly_as_it_was(self):
        self.write_state()

        self.assertEqual(
            gate.reviewed_images(PID, ["shot-1.png", "shot-2.png"], str(self.out_dir)),
            ["shot-1.png", "shot-2.png"],
        )

    def test_a_shorter_or_missing_state_changes_nothing(self):
        self.write_state()

        self.assertEqual(
            gate.reviewed_images(PID, ["shot-1.png"], str(self.out_dir)), ["shot-1.png"],
        )
        self.assertEqual(
            gate.reviewed_images("nosuch", ["shot-1.png"], str(self.out_dir)), ["shot-1.png"],
        )

    def test_an_absolute_name_is_resolved_where_it_points(self):
        real = self.out_dir / "shot-2.png"
        real.write_bytes(b"png")
        self.write_state(clips=[
            {"index": 0, "start_image_filename": "shot-1.png"},
            {"index": 1, "start_image_filename": str(real)},
        ])

        corrected = gate.reviewed_images(PID, ["shot-1.png", "other.png"], str(self.out_dir))

        self.assertEqual(corrected, ["shot-1.png", str(real)])

    def test_the_run_asks_after_it_waits_and_before_it_renders(self):
        source = (Path(_APP_DIR) / "services" / "director_pipeline.py").read_text(encoding="utf-8")
        pause = source.index('pause_reason="review_images"')

        self.assertLess(pause, source.index("director_review_gate.reviewed_images", pause))
        self.assertLess(
            source.index("director_review_gate.reviewed_images", pause),
            source.index('phase="generating_video"', pause),
        )


class ThePauseIsAskedForTests(unittest.TestCase):
    def test_the_gate_decides_the_pause_the_plan_used_to(self):
        source = (Path(_APP_DIR) / "services" / "director_pipeline.py").read_text(encoding="utf-8")

        self.assertIn(
            "if director_review_gate.should_pause(auto_mode) and requires_shot_images", source,
        )


class TheParkedPipelineCanBeCorrectedTests(unittest.TestCase):
    """The reservation a Dashboard mutation takes must not refuse the pause's own corrections."""

    def setUp(self):
        self.saved = (
            dict(pipeline._pipelines),
            dict(pipeline._pipeline_threads),
            dict(pipeline._pipeline_child_jobs),
            set(pipeline._pipeline_starting),
            set(pipeline._pipeline_operations),
            set(pipeline._pipeline_deleting),
        )
        for holder in (
            pipeline._pipelines, pipeline._pipeline_threads, pipeline._pipeline_child_jobs,
        ):
            holder.clear()
        for holder in (
            pipeline._pipeline_starting, pipeline._pipeline_operations, pipeline._pipeline_deleting,
        ):
            holder.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        (
            pipelines, threads, children, starting, operations, deleting,
        ) = self.saved
        for holder, value in (
            (pipeline._pipelines, pipelines), (pipeline._pipeline_threads, threads),
            (pipeline._pipeline_child_jobs, children), (pipeline._pipeline_starting, starting),
            (pipeline._pipeline_operations, operations), (pipeline._pipeline_deleting, deleting),
        ):
            holder.clear()
            holder.update(value)

    def park(self, parked=True):
        pipeline._pipelines[PID] = {
            "status": "paused" if parked else "running",
            "pause_reason": "review_images" if parked else None,
        }

    def test_a_paused_pipeline_with_a_live_thread_can_be_corrected(self):
        self.park()
        thread = threading.Thread(target=lambda: None)
        thread.start()
        thread.join()
        pipeline._pipeline_threads[PID] = thread

        self.assertTrue(pipeline._claim_pipeline_operation(PID))
        pipeline._release_pipeline_operation(PID)

    def test_a_running_pipeline_still_refuses(self):
        self.park(parked=False)
        pipeline._pipeline_threads[PID] = threading.current_thread()

        self.assertFalse(pipeline._claim_pipeline_operation(PID))

    def test_a_pause_that_is_not_a_review_still_refuses(self):
        pipeline._pipelines[PID] = {"status": "paused", "pause_reason": "stopping"}

        self.assertFalse(pipeline._claim_pipeline_operation(PID))

    def test_a_child_job_still_refuses(self):
        self.park()
        pipeline._pipeline_child_jobs[PID] = {"job": True}

        self.assertFalse(pipeline._claim_pipeline_operation(PID))

    def test_another_mutation_in_flight_still_refuses(self):
        self.park()
        pipeline._pipeline_operations.add(PID)

        self.assertFalse(pipeline._claim_pipeline_operation(PID))

    def test_a_deletion_in_flight_still_refuses(self):
        self.park()
        pipeline._pipeline_deleting.add(PID)

        self.assertFalse(pipeline._claim_pipeline_operation(PID))

    def test_the_correction_the_reviewer_clicks_is_no_longer_refused(self):
        """The reported defect, through its own entry point rather than the claim alone."""

        self.park()
        pipeline._pipeline_threads[PID] = threading.current_thread()
        with patch("builtins.print"):
            with self.assertRaises(ValueError) as raised:
                pipeline.rerun_clip_image("outputs", PID, 0)

        # It got past the reservation and reached the state, which is the point: the answer is about
        # the project rather than about the pipeline being busy.
        self.assertIn("not found", str(raised.exception).lower())


class TheContinuityReadoutTests(GateTestCase):
    def test_it_reports_the_pairs_that_would_render_one_frame(self):
        prompt = (
            "create new scene, warehouse interior at night. A woman in red sits on a piano bench, "
            "legs crossed, holding dark glasses at her chest. The man in black leather stands before "
            "her holding his belt. Stark spotlight illuminates the contact area."
        )
        same = prompt.replace("his belt.", "his belt buckle.")
        self.write_state(clips=[
            {"index": 0, "image_prompt": prompt},
            {"index": 1, "image_prompt": same},
        ])

        answer = gate.continuity(PID, str(self.out_dir))

        self.assertEqual(answer["shots"], 2)
        self.assertEqual(len(answer["duplicates"]), 1)
        self.assertEqual(answer["duplicates"][0]["shot"], 2)

    def test_it_asks_quietly_so_a_poll_is_not_a_log_flood(self):
        self.write_state(clips=[
            {"index": 0, "image_prompt": "a glass greenhouse filled with tropical plants, misty air"},
            {"index": 1, "image_prompt": "a glass greenhouse filled with tropical plants, misty air"},
        ])

        with patch("builtins.print") as printed:
            gate.continuity(PID, str(self.out_dir))

        self.assertEqual(printed.call_args_list, [])

    def test_a_project_with_no_state_is_not_an_error(self):
        self.assertEqual(gate.continuity("nosuch", str(self.out_dir)), {"shots": 0, "duplicates": []})


class TheRoutesTests(GateTestCase):
    def test_the_routes_the_control_calls(self):
        paths = {(route.path, tuple(sorted(route.methods or []))) for route in gate.router.routes}

        self.assertIn(("/api/v1/director/review-gate", ("GET",)), paths)
        self.assertIn(("/api/v1/director/review-gate", ("POST",)), paths)
        self.assertIn(("/api/v1/director/pipelines/{pid}/continuity", ("GET",)), paths)

    def test_the_route_stores_the_choice(self):
        self.assertEqual(gate.review_gate_set({"enabled": False}), {"enabled": False})
        self.assertFalse(gate.enabled())
        self.assertEqual(gate.review_gate_set({"enabled": True}), {"enabled": True})

    def test_a_body_without_the_field_is_refused(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException):
            gate.review_gate_set({"other": 1})
        with self.assertRaises(HTTPException):
            gate.review_gate_set(None)

    def test_the_outputs_folder_comes_from_the_application_config(self):
        (self.tmp / "wgp_config.json").write_text(
            json.dumps({"save_path": "renders"}), encoding="utf-8",
        )

        self.assertEqual(gate._output_dir(), str(self.tmp / "renders"))

    def test_an_absolute_outputs_folder_is_kept(self):
        (self.tmp / "wgp_config.json").write_text(
            json.dumps({"save_path": str(self.tmp)}), encoding="utf-8",
        )

        self.assertEqual(gate._output_dir(), str(self.tmp))

    def test_a_missing_config_uses_the_pipelines_own_default(self):
        self.assertEqual(gate._output_dir(), str(self.tmp / "outputs"))


if __name__ == "__main__":
    unittest.main()
