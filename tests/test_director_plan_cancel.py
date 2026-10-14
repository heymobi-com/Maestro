"""A Stop reaches the writer, so an abandoned generation stops holding the engine.

Measured before this existed: of the 109 requests the writer finished, one carried ``cancel=True``.
``stop_pipeline`` writes ``cancelled`` and aborts the generation jobs, but a plan in flight is a
blocking HTTP stream nobody interrupts, and the engine serves requests in order -- so the next call
waited behind work no one was waiting for.

What these tests pin is the binding itself: that the pipeline's own status is the only source of
truth, that only the bound thread becomes cancellable, and that the status is read per check instead
of captured once.
"""
from __future__ import annotations

import os
import sys
import threading
import types
import unittest
from unittest.mock import patch

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

from services import director_plan_cancel as plan_cancel  # noqa: E402
from services import llm_service  # noqa: E402
from services.studio_enhancement import check_cancelled, is_cancellable  # noqa: E402

PIPELINE = "pipe-1"


def _pipeline_module(**overrides):
    """A stand-in for services.director_pipeline, so the real one is never imported."""

    module = types.ModuleType("services.director_pipeline")
    module.get_pipeline = overrides.get("get_pipeline", lambda pid: {"status": "planning"})
    return module


class StatusTests(unittest.TestCase):
    def setUp(self):
        plan_cancel.release()

    def tearDown(self):
        plan_cancel.release()

    def test_a_running_pipeline_is_not_cancelled(self):
        with patch.dict(sys.modules, {"services.director_pipeline": _pipeline_module()}):
            self.assertFalse(plan_cancel.cancelled(PIPELINE))

    def test_the_status_is_the_only_source_of_truth(self):
        module = _pipeline_module(get_pipeline=lambda pid: {"status": "cancelled"})
        with patch.dict(sys.modules, {"services.director_pipeline": module}):
            self.assertTrue(plan_cancel.cancelled(PIPELINE))

    def test_a_pipeline_that_is_gone_is_not_cancelled(self):
        module = _pipeline_module(get_pipeline=lambda pid: None)
        with patch.dict(sys.modules, {"services.director_pipeline": module}):
            self.assertFalse(plan_cancel.cancelled(PIPELINE))

    def test_a_lookup_that_fails_is_not_a_cancellation(self):
        def explode(pid):
            raise RuntimeError("pipeline state unavailable")

        module = _pipeline_module(get_pipeline=explode)
        with patch.dict(sys.modules, {"services.director_pipeline": module}):
            self.assertFalse(
                plan_cancel.cancelled(PIPELINE),
                "a broken lookup must not look like a Stop",
            )


class BindingTests(unittest.TestCase):
    def setUp(self):
        plan_cancel.release()
        self.module = _pipeline_module()
        self.entered = patch.dict(sys.modules, {"services.director_pipeline": self.module})
        self.entered.start()

    def tearDown(self):
        plan_cancel.release()
        self.entered.stop()

    def test_binding_makes_this_thread_cancellable(self):
        self.assertFalse(is_cancellable(), "nothing is cancellable until a pipeline binds")
        self.assertTrue(plan_cancel.bind(PIPELINE))
        self.assertTrue(is_cancellable())

    def test_a_bound_thread_stops_once_the_pipeline_is_cancelled(self):
        plan_cancel.bind(PIPELINE)
        check_cancelled()  # while planning, a check is a no-op
        self.module.get_pipeline = lambda pid: {"status": "cancelled"}
        with self.assertRaises(InterruptedError):
            check_cancelled()

    def test_the_status_is_read_at_every_check(self):
        # The binding must not capture the answer: a plan that binds early has to see a Stop later.
        plan_cancel.bind(PIPELINE)
        estados = [{"status": "planning"}]
        self.module.get_pipeline = lambda pid: estados[0]
        check_cancelled()
        estados[0] = {"status": "cancelled"}
        with self.assertRaises(InterruptedError):
            check_cancelled()

    def test_another_thread_is_not_cancellable(self):
        plan_cancel.bind(PIPELINE)
        visto = []

        def otra():
            visto.append(is_cancellable())

        hilo = threading.Thread(target=otra)
        hilo.start()
        hilo.join()
        self.assertEqual(visto, [False], "a context is per thread; another pipeline must be unaffected")

    def test_binding_twice_on_one_thread_does_nothing(self):
        self.assertTrue(plan_cancel.bind(PIPELINE))
        self.assertFalse(
            plan_cancel.bind("pipe-2"),
            "the first binding already answers for this thread; a second would silently win",
        )

    def test_release_undoes_the_binding(self):
        plan_cancel.bind(PIPELINE)
        plan_cancel.release()
        self.assertFalse(is_cancellable())
        self.module.get_pipeline = lambda pid: {"status": "cancelled"}
        check_cancelled()  # released: a cancelled pipeline no longer interrupts this thread

    def test_release_after_a_stop_still_unbinds(self):
        # The context re-checks cancellation as it closes, so releasing after a Stop raises on the
        # way out. That must not leave the thread bound to a finished pipeline.
        plan_cancel.bind(PIPELINE)
        self.module.get_pipeline = lambda pid: {"status": "cancelled"}
        plan_cancel.release()
        self.assertFalse(is_cancellable())
        check_cancelled()

    def test_release_without_a_binding_is_harmless(self):
        plan_cancel.release()
        self.assertFalse(is_cancellable())

    def test_a_cancelled_plan_never_sends_the_writer_a_request(self):
        # The decisive property: the writer is what Stop has to release, and the streaming path
        # checks cancellation before it builds or sends anything. This is also why the planner's
        # retry-after-failure path cannot quietly issue a second generation.
        plan_cancel.bind(PIPELINE)
        self.module.get_pipeline = lambda pid: {"status": "cancelled"}
        with patch("requests.post") as post:
            with self.assertRaises(InterruptedError):
                llm_service.generate_streaming(prompt="plan", system_prompt="system")
        self.assertFalse(post.called, "a stopped plan must not reach the writer")

    def test_a_running_plan_still_calls_the_writer(self):
        # The other half: binding while planning must not block ordinary work.
        plan_cancel.bind(PIPELINE)
        with patch.object(llm_service, "is_loaded", return_value=True), \
                patch("requests.post", side_effect=RuntimeError("sent")) as post:
            with self.assertRaises(RuntimeError):
                llm_service.generate_streaming(prompt="plan", system_prompt="system")
        self.assertTrue(post.called, "a running plan still sends its request")


if __name__ == "__main__":
    unittest.main()
