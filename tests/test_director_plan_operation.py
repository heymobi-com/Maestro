"""Progress and Stop for a planning pass the user started interactively.

Planning a long timeline takes batches, and the Director pipeline has always
published its batch counters while the one-shot planning endpoint stayed silent.
The same ten-minute pass was therefore invisible on the main screen and could not
be stopped: the only way out was restarting the backend, which discarded the plan.

These tests pin both halves of the fix — the registry the UI polls, and the
planner callbacks that feed it — so the counters shown to the user and the Stop
that interrupts them cannot silently disappear again.
"""

from __future__ import annotations

import os
import sys
import unittest


_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director import plan_operation  # noqa: E402
from services.director.planners.base import BasePlanner  # noqa: E402


class _BatchOnlyPlanner(BasePlanner):
    """A planner that exists only to drive the long-form batching helper."""

    def plan(self, **kwargs):  # pragma: no cover - the driver is what is tested
        raise NotImplementedError


def _planner(publish, cancelled) -> _BatchOnlyPlanner:
    """Attach the same two callbacks the planning endpoint attaches."""

    planner = _BatchOnlyPlanner()
    planner._configure_planning_runtime(
        {
            "_planning_progress_callback": publish,
            "_planning_cancelled_callback": cancelled,
        },
        kind="short_film_audio_batches",
        fingerprint_payload={"items": 24},
    )
    return planner


def _rows(number: int, start: int, batch: list, previous) -> list[dict]:
    return [{"index": start + offset} for offset in range(len(batch))]


def _run_batches(planner: _BatchOnlyPlanner, call_batch=None, items: int = 24):
    return planner._run_checkpointed_json_batches(
        items=list(range(items)),
        batch_size=12,
        checkpoint_key="short_film_audio_batches",
        stage="short_film_audio_batch",
        progress_label="audio-film",
        call_batch=call_batch or _rows,
        fallback_factory=lambda index, item: {"fallback": index},
    )


class PlanOperationRegistryTests(unittest.TestCase):
    """The record the main screen reads while a planning pass is running."""

    def setUp(self):
        plan_operation.reset()

    def tearDown(self):
        plan_operation.reset()

    def test_an_idle_server_reports_no_operation(self):
        self.assertIsNone(plan_operation.active())

    def test_the_planners_own_counters_are_what_the_ui_reads(self):
        operation = plan_operation.begin("plan", label="short_film", total=59)
        plan_operation.publish(operation, {
            "message": "Planning audio-film batch 2/5...",
            "current": 12,
            "total": 59,
            "stage": "short_film_audio_batch",
            "batch": 2,
            "batch_count": 5,
        })
        snapshot = plan_operation.active()
        self.assertEqual(snapshot["id"], operation)
        self.assertEqual(snapshot["label"], "short_film")
        self.assertEqual(snapshot["stage"], "short_film_audio_batch")
        self.assertEqual(snapshot["message"], "Planning audio-film batch 2/5...")
        self.assertEqual((snapshot["current"], snapshot["total"]), (12, 59))
        self.assertFalse(snapshot["cancelling"])
        self.assertGreaterEqual(snapshot["elapsed_seconds"], 0.0)

    def test_a_quiet_stage_does_not_reset_the_counters_on_screen(self):
        operation = plan_operation.begin("plan", total=59)
        plan_operation.publish(operation, {"current": 24, "total": 59})
        plan_operation.publish(operation, {"stage": "polish"})
        snapshot = plan_operation.active()
        self.assertEqual((snapshot["current"], snapshot["total"]), (24, 59))
        self.assertEqual(snapshot["stage"], "polish")

    def test_the_newest_pass_is_the_one_reported(self):
        first = plan_operation.begin("plan", label="podcast")
        second = plan_operation.begin("plan", label="short_film")
        self.assertEqual(plan_operation.active()["id"], second)
        plan_operation.finish(second)
        self.assertEqual(plan_operation.active()["id"], first)

    def test_a_finished_pass_leaves_nothing_for_the_card_to_show(self):
        operation = plan_operation.begin("plan")
        plan_operation.finish(operation)
        self.assertIsNone(plan_operation.active())
        self.assertIsNone(plan_operation.snapshot(operation))

    def test_a_stop_is_visible_before_the_batch_finishes(self):
        operation = plan_operation.begin("plan")
        self.assertTrue(plan_operation.request_cancel(operation))
        snapshot = plan_operation.active()
        self.assertTrue(snapshot["cancelling"])
        self.assertEqual(snapshot["stage"], "cancelling")
        self.assertIn("batch", snapshot["message"])
        self.assertTrue(plan_operation.is_cancelled(operation))

    def test_a_stop_does_not_cross_into_another_pass(self):
        first = plan_operation.begin("plan")
        second = plan_operation.begin("plan")
        self.assertTrue(plan_operation.request_cancel(first))
        self.assertFalse(plan_operation.is_cancelled(second))

    def test_stopping_nothing_reports_that_there_was_nothing_to_stop(self):
        self.assertFalse(plan_operation.request_cancel())
        self.assertFalse(plan_operation.request_cancel("missing-operation"))

    def test_an_unknown_operation_never_cancels_itself(self):
        # A planner driven outside the endpoint must keep planning.
        self.assertFalse(plan_operation.is_cancelled("never-opened"))

    def test_impossible_counters_cannot_break_the_progress_bar(self):
        operation = plan_operation.begin("plan", total=-5)
        plan_operation.publish(operation, {"current": -3, "total": "not a number"})
        snapshot = plan_operation.active()
        self.assertEqual((snapshot["current"], snapshot["total"]), (0, 0))

    def test_publishing_to_a_finished_operation_is_harmless(self):
        operation = plan_operation.begin("plan")
        plan_operation.finish(operation)
        plan_operation.publish(operation, {"current": 5})
        self.assertIsNone(plan_operation.active())


class PlannerProgressTests(unittest.TestCase):
    """The planner publishes the counters the registry then hands to the UI."""

    def setUp(self):
        plan_operation.reset()

    def tearDown(self):
        plan_operation.reset()

    def test_each_batch_is_announced_with_its_place_in_the_pass(self):
        events: list = []
        planner = _planner(events.append, lambda: False)
        self.assertEqual(len(_run_batches(planner)), 24)
        starting = [event for event in events if "Planning audio-film batch" in event["message"]]
        self.assertEqual(len(starting), 2)
        self.assertEqual(starting[0]["message"], "Planning audio-film batch 1/2...")
        self.assertEqual((starting[0]["current"], starting[0]["total"]), (0, 24))
        self.assertEqual(starting[1]["message"], "Planning audio-film batch 2/2...")
        self.assertEqual(starting[1]["current"], 12)
        finished = [event for event in events if event["message"].startswith("Planned")]
        self.assertEqual(finished[-1]["current"], 24)

    def test_the_registry_receives_those_events_unchanged(self):
        operation = plan_operation.begin("plan", label="short_film", total=24)
        planner = _planner(
            lambda event: plan_operation.publish(operation, event),
            lambda: plan_operation.is_cancelled(operation),
        )
        _run_batches(planner)
        snapshot = plan_operation.active()
        self.assertEqual(snapshot["current"], 24)
        self.assertEqual(snapshot["total"], 24)
        self.assertIn("audio-film", snapshot["message"])

    def test_a_stop_between_batches_interrupts_the_pass(self):
        events: list = []
        planner = _planner(events.append, lambda: True)
        with self.assertRaises(InterruptedError):
            _run_batches(planner)
        self.assertEqual([event for event in events if "Planning" in event["message"]], [])

    def test_a_stop_arriving_mid_pass_stops_the_next_batch(self):
        operation = plan_operation.begin("plan", total=24)
        planner = _planner(
            lambda event: plan_operation.publish(operation, event),
            lambda: plan_operation.is_cancelled(operation),
        )

        def first_batch_then_stop(number: int, start: int, batch: list, previous):
            # The user clicks Stop while the first batch is being planned.
            plan_operation.request_cancel(operation)
            return _rows(number, start, batch, previous)

        with self.assertRaises(InterruptedError):
            _run_batches(planner, call_batch=first_batch_then_stop)

    def test_a_batch_stopped_mid_way_is_never_filled_with_fallbacks(self):
        events: list = []
        planner = _planner(events.append, lambda: False)

        def stop_inside_batch(number: int, start: int, batch: list, previous):
            raise InterruptedError("Director planning cancelled")

        with self.assertRaises(InterruptedError):
            _run_batches(planner, call_batch=stop_inside_batch)
        # Fallbacks would have looked like finished work, and a resumed pass
        # would then skip shots the user explicitly stopped.
        self.assertEqual(
            [event for event in events if event["message"].startswith("Planned")],
            [],
        )


if __name__ == "__main__":
    unittest.main()
