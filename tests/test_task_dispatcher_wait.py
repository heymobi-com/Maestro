"""The task dispatcher must sleep until work arrives.

``Listener`` is the single long-lived thread that runs generation workers. It
used to poll its queue with ``time.sleep(0.001)``, so an idle Maestro woke the
scheduler a thousand times a second for the whole life of the process. On a
host that is already short of memory that constant churn is real pressure, so
the queue now waits on a condition and is notified on every submission.
"""
import os
import sys
import threading
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from shared.utils.thread_utils import Listener, async_run  # noqa: E402


class ListenerDispatchTests(unittest.TestCase):
    def setUp(self):
        self._queue = list(Listener.task_queue)
        Listener.task_queue.clear()

    def tearDown(self):
        Listener.task_queue.clear()
        Listener.task_queue.extend(self._queue)

    def test_submitted_task_runs_and_thread_names_are_restored(self):
        done = threading.Event()
        names = []

        def work():
            names.append(threading.current_thread().name)
            done.set()

        async_run(work, thread_name="Generation")
        self.assertTrue(done.wait(timeout=5.0))
        self.assertEqual(names, ["Generation"])
        # The dispatcher keeps its own name for the next worker.
        self.assertNotEqual(Listener.thread.name, "Generation")

    def test_tasks_run_in_submission_order(self):
        seen = []
        done = threading.Event()

        def make(index):
            def task():
                seen.append(index)
                if index == 3:
                    done.set()
            return task

        for index in range(1, 4):
            async_run(make(index))
        self.assertTrue(done.wait(timeout=5.0))
        self.assertEqual(seen, [1, 2, 3])

    def test_idle_dispatcher_does_not_poll(self):
        async_run(lambda: None)
        # Let the dispatcher reach its idle wait.
        deadline = time.monotonic() + 2.0
        while Listener.task_queue and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.2)

        before = time.process_time()
        time.sleep(0.5)
        idle_cpu = time.process_time() - before
        self.assertLess(
            idle_cpu,
            0.2,
            "an idle dispatcher must block instead of polling its queue",
        )

    def test_a_failing_task_does_not_stop_the_dispatcher(self):
        done = threading.Event()

        def boom():
            raise RuntimeError("task failure")

        async_run(boom)
        async_run(done.set)
        self.assertTrue(done.wait(timeout=5.0), "later tasks must still run")


if __name__ == "__main__":
    unittest.main()
