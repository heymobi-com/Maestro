"""The job stream must wake on push instead of polling at 1 kHz.

A stopped render leaves ``_run_generation`` waiting for its worker's ``exit``
event. The old ``FIFOQueue.next`` spun on ``time.sleep(0.001)``, which burns a
core beside the draining GPU work and raised ``OSError: [WinError 1450]
Insufficient system resources`` on a memory-starved Windows host. These tests
pin the blocking contract, the optional timeout, and the wake-on-push path.
"""
import threading
import time
import unittest

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from shared.utils.thread_utils import AsyncStream, FIFOQueue  # noqa: E402


class FifoQueueWaitTests(unittest.TestCase):
    def test_next_returns_pushed_event_in_order(self):
        queue = FIFOQueue()
        queue.push("progress", [1, 2])
        queue.push("exit", None)

        self.assertEqual(queue.next(), ("progress", [1, 2]))
        self.assertEqual(queue.next(), ("exit", None))

    def test_next_blocks_until_push_and_wakes_immediately(self):
        queue = FIFOQueue()
        results = []

        def reader():
            results.append(queue.next())

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        time.sleep(0.15)
        self.assertEqual(results, [], "next() must wait for an event")

        queue.push("exit", None)
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive(), "push must wake the waiting reader")
        self.assertEqual(results, [("exit", None)])

    def test_timeout_returns_none_without_consuming_events(self):
        queue = FIFOQueue()
        started = time.monotonic()
        self.assertIsNone(queue.next(timeout=0.12))
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 0.10)
        self.assertLess(elapsed, 1.0)

        queue.push("exit", None)
        self.assertEqual(queue.next(timeout=0.12), ("exit", None))

    def test_waiting_reader_does_not_burn_cpu(self):
        queue = FIFOQueue()
        thread = threading.Thread(target=lambda: queue.next(timeout=0.5), daemon=True)
        baseline = time.process_time()
        thread.start()
        thread.join(timeout=2.0)
        consumed = time.process_time() - baseline
        self.assertFalse(thread.is_alive())
        self.assertLess(
            consumed,
            0.25,
            "a waiting reader must sleep, not poll the queue",
        )

    def test_stream_pairs_input_and_output_queues(self):
        stream = AsyncStream()
        stream.output_queue.push("progress", "half")
        self.assertEqual(stream.output_queue.next(timeout=0.1), ("progress", "half"))
        self.assertIsNone(stream.input_queue.pop())


if __name__ == "__main__":
    unittest.main()
