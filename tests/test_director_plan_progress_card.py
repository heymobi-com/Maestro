"""The planning progress card on the main screen, and its Stop button.

Planning a long timeline takes batches of about a dozen shots, and the only sign
of it used to be raw planner text inside the Director chat. The main screen showed
nothing and offered no way to stop the pass, so an unwanted one had to be waited
out. These checks keep the card wired to the planner's real counters, keep its
Stop reachable and single-shot, and keep a stopped pass from replacing the plans
already under review.
"""

from __future__ import annotations

import os
import unittest


_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
_UI_DIR = os.path.join(_ROOT, "ui", "src")
_APP_DIR = os.path.join(_ROOT, "app")


def _read(*parts: str) -> str:
    with open(os.path.join(*parts), encoding="utf-8", newline="") as handle:
        # Multi-line assertions must not depend on the checkout's line endings:
        # this repository is checked out with CRLF on Windows.
        return handle.read().replace("\r\n", "\n").replace("\r", "\n")


class PlanProgressCardTests(unittest.TestCase):
    """What the user sees while the planner is working."""

    def setUp(self):
        self.card = _read(_UI_DIR, "components", "MainContent", "PlanProgressCard.tsx")
        self.main = _read(_UI_DIR, "components", "MainContent", "MainContent.tsx")
        self.client = _read(_UI_DIR, "api", "client.ts")
        self.store = _read(_UI_DIR, "stores", "useStore.ts")

    def test_the_card_shows_the_planners_own_counters(self):
        # `current`/`total` are the planner's batch counters, so the bar is real
        # progress rather than an animation standing in for it.
        self.assertIn("operation.current", self.card)
        self.assertIn("operation.total", self.card)
        self.assertIn("operation.message", self.card)

    def test_the_card_sits_on_the_main_screen_beside_the_pipeline_card(self):
        self.assertIn("import { PlanProgressCard } from './PlanProgressCard'", self.main)
        self.assertIn("<PlanProgressCard />", self.main)
        self.assertLess(
            self.main.index("<PlanProgressCard />"),
            self.main.index("<PipelinePlaceholder />"),
        )

    def test_a_reloaded_window_still_finds_a_running_pass(self):
        # A reload cannot know whether a pass is running, so the first poll must
        # always ask the server instead of waiting for a local loading flag.
        self.assertIn("void tick()", self.card)
        self.assertIn("let keep = true", self.card)

    def test_the_card_goes_away_when_the_pass_ends(self):
        self.assertIn("if (!operation) return null", self.card)
        self.assertIn("setOperation(next)", self.card)

    def test_an_unreachable_backend_does_not_look_like_a_finished_pass(self):
        # Clearing the card on a failed poll would tell the user the pass had
        # ended; holding the last reading is the honest answer.
        self.assertIn("} catch {", self.card)
        self.assertIn("keep = directorLoading", self.card)

    def test_stop_asks_the_server_and_cannot_be_pressed_twice(self):
        self.assertIn("cancelDirectorPlanOperation(operation.id)", self.card)
        self.assertIn("stoppingRef.current", self.card)
        self.assertIn("if (!operation || stoppingRef.current) return", self.card)
        self.assertIn("disabled={stopping}", self.card)

    def test_stop_admits_that_the_batch_in_progress_finishes_first(self):
        # Cancellation lands between batches, so the card must not promise an
        # instant stop it cannot deliver.
        self.assertIn("the batch in progress finishes first", self.card)
        self.assertIn("Stopping…", self.card)

    def test_the_card_is_announced_to_a_screen_reader(self):
        self.assertIn('role="status"', self.card)
        self.assertIn('aria-live="polite"', self.card)

    def test_the_client_can_read_and_stop_the_pass(self):
        self.assertIn("export async function fetchDirectorPlanOperation()", self.client)
        self.assertIn("export async function cancelDirectorPlanOperation(", self.client)
        self.assertIn("/api/v1/director/plan-operation", self.client)

    def test_a_stopped_pass_does_not_replace_the_plans_under_review(self):
        # The endpoint answers a Stop with an empty plan, so the store has to
        # bail out before that empty result overwrites the reviewed plan. All
        # three v2 planning actions need it.
        self.assertEqual(self.store.count("if (result.cancelled) {"), 3)
        self.assertIn("directorLoading: false, directorError: null", self.store)


class PlanOperationEndpointWiringTests(unittest.TestCase):
    """The planning endpoint must publish progress and honour a Stop."""

    def setUp(self):
        self.launch = _read(_APP_DIR, "launch.py")

    def test_the_planner_gets_the_progress_and_cancellation_callbacks(self):
        self.assertIn(
            'planner_kwargs["_planning_progress_callback"] = _publish_plan_progress',
            self.launch,
        )
        self.assertIn(
            'planner_kwargs["_planning_cancelled_callback"] = _plan_was_cancelled',
            self.launch,
        )

    def test_the_operation_is_retired_whatever_happens_to_the_request(self):
        # A failed or cancelled pass must not leave a card polling forever, so
        # the plan endpoint has to retire its operation from a finally block.
        start = self.launch.index("async def director_v2_plan(request: Request):")
        body = self.launch[start:]
        body = body[:body.index("\n@api.", 1)]
        self.assertIn("\n    finally:\n", body)
        after_finally = body.split("\n    finally:\n", 1)[1]
        self.assertIn("plan_operation.finish(operation)", after_finally[:300])

    def test_a_stop_answers_as_cancelled_rather_than_failing(self):
        self.assertIn("except InterruptedError:", self.launch)
        self.assertIn('"cancelled": True', self.launch)

    def test_the_ui_has_a_route_to_read_and_to_stop_the_pass(self):
        self.assertIn('@api.get("/api/v1/director/plan-operation")', self.launch)
        self.assertIn(
            '@api.post("/api/v1/director/plan-operation/cancel")', self.launch,
        )

    def test_stopping_nothing_is_reported_instead_of_silently_ignored(self):
        self.assertIn('detail="No Director planning pass is running"', self.launch)


if __name__ == "__main__":
    unittest.main()
