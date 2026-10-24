"""A download started by a queued job must be cancellable, and leave no bytes.

Measured on a real run: a job submitted with the wrong model began fetching a
22 GB checkpoint. The job could be cancelled, but the transfer had no owner of
its own, so nothing could stop it, the banner offered no Cancel action and the
queue waited for it to finish.

These cases drive huggingface_hub's own download entry point, so they exercise
the same wrapper the app installs at startup.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest

_APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

try:
    import huggingface_hub.file_download as hf_file_download
except ImportError:  # pragma: no cover - the app venv always ships it
    hf_file_download = None

from services import download_control  # noqa: E402


@unittest.skipIf(hf_file_download is None, "huggingface_hub is not installed")
class JobDownloadCancellationTests(unittest.TestCase):
    def setUp(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.tmpdir = tempfile.mkdtemp(prefix="maestro-dl-")
        self.partial = os.path.join(self.tmpdir, "weights.safetensors.incomplete")
        with open(self.partial, "wb") as handle:
            handle.write(b"\0" * 4096)
        self.destination = os.path.join(self.tmpdir, "weights.safetensors")
        self.fake_calls = 0

    def _install_fake_transfer(self):
        def fake(incomplete_path, destination_path, url_to_download=None,
                 proxies=None, headers=None, expected_size=None, filename=None,
                 force_download=False, etag=None, xet_file_data=None):
            self.fake_calls += 1
            self.started.set()
            self.release.wait(10)
            raise RuntimeError("connection closed by cancel")

        hf_file_download._maestro_cancellation_patched = False
        hf_file_download._download_to_tmp_and_move = fake
        download_control.install_huggingface_cancellation()
        return hf_file_download._download_to_tmp_and_move

    def _only_job_control(self):
        with download_control._controls_lock:
            active = [
                control for control in download_control._controls.values()
                if control.owner == "job" and control.snapshot()["status"] == "downloading"
            ]
        self.assertEqual(len(active), 1, "expected exactly one job control")
        return active[0]

    def test_a_transfer_started_by_a_job_is_cancellable_and_cleans_up(self):
        wrapped = self._install_fake_transfer()
        errors = []

        def run():
            try:
                with download_control.job_download_scope():
                    wrapped(self.partial, self.destination)
            except BaseException as error:  # noqa: BLE001 - reported below
                errors.append(error)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        self.assertTrue(self.started.wait(10), "transfer never started")

        control = self._only_job_control()
        snapshot = control.snapshot()
        self.assertTrue(
            snapshot["cancellable"],
            "a download started by a job must advertise a Cancel action",
        )

        control.request_cancel()
        self.release.set()
        worker.join(10)
        self.assertFalse(worker.is_alive())

        self.assertEqual(len(errors), 1, errors)
        self.assertIsInstance(errors[0], download_control.DownloadCancelled)
        self.assertFalse(
            os.path.exists(self.partial),
            "the cancelled transfer's partial file must be removed",
        )
        self.assertFalse(os.path.exists(self.destination))

    def test_cancelling_the_job_stops_the_transfer_it_started(self):
        wrapped = self._install_fake_transfer()
        errors = []

        def run():
            try:
                with download_control.job_download_scope():
                    wrapped(self.partial, self.destination)
            except BaseException as error:  # noqa: BLE001 - reported below
                errors.append(error)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        self.assertTrue(self.started.wait(10), "transfer never started")
        self.assertEqual(self._only_job_control().owner, "job")

        # What the queue's Cancel does.
        self.assertGreaterEqual(download_control.cancel_active_downloads("job"), 1)

        self.release.set()
        worker.join(10)
        self.assertEqual(len(errors), 1, errors)
        self.assertIsInstance(errors[0], download_control.DownloadCancelled)
        self.assertFalse(os.path.exists(self.partial))

    def test_a_download_the_user_asked_for_is_not_cancelled_by_a_job(self):
        explicit = download_control.create_download_control()
        try:
            self.assertEqual(download_control.cancel_active_downloads("job"), 0)
            self.assertEqual(explicit.snapshot()["status"], "downloading")
        finally:
            explicit.finish("failed")

    def test_a_cancelled_explicit_download_keeps_its_bytes_for_resume(self):
        wrapped = self._install_fake_transfer()
        errors = []

        def run():
            try:
                with download_control.download_scope(download_control.create_download_control()):
                    wrapped(self.partial, self.destination)
            except BaseException as error:  # noqa: BLE001 - reported below
                errors.append(error)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        self.assertTrue(self.started.wait(10), "transfer never started")
        with download_control._controls_lock:
            explicit = [
                control for control in download_control._controls.values()
                if control.owner == "explicit" and control.snapshot()["status"] == "downloading"
            ]
        self.assertTrue(explicit, "expected the explicit control")
        explicit[0].request_cancel()
        self.release.set()
        worker.join(10)

        self.assertEqual(len(errors), 1, errors)
        self.assertIsInstance(errors[0], download_control.DownloadCancelled)
        self.assertTrue(
            os.path.exists(self.partial),
            "a download the user asked for must resume from its partial file",
        )

    def test_a_job_cancel_ignores_a_control_with_no_transfer_in_flight(self):
        with download_control.job_download_scope() as control:
            # A render that is running, not fetching: cancelling the job must
            # keep using the engine's own cooperative abort instead of turning
            # a sampling loop into a download cancellation.
            self.assertEqual(download_control.cancel_active_downloads("job"), 0)
            self.assertEqual(control.snapshot()["status"], "downloading")

    def test_the_banner_offers_cancel_for_a_job_download(self):
        from tqdm import tqdm

        from services import safe_download  # noqa: F401 - installs the tqdm hook

        with download_control.job_download_scope() as control:
            with control.transfer():
                bar = tqdm(total=1024 * 1024, unit="B", unit_scale=True, desc="weights")
                try:
                    bar.update(1024)
                    rows = [
                        row for row in safe_download.get_active_downloads()
                        if row.get("cancel_id") == control.download_id
                    ]
                finally:
                    bar.close()
        self.assertEqual(len(rows), 1, "the banner must list the job's download")
        self.assertTrue(
            rows[0]["cancellable"],
            "a job's download must offer a Cancel action",
        )

    def test_an_uncancelled_transfer_is_left_alone(self):
        def fake(incomplete_path, destination_path, url_to_download=None,
                 proxies=None, headers=None, expected_size=None, filename=None,
                 force_download=False, etag=None, xet_file_data=None):
            # Same publication step huggingface_hub performs on success.
            os.replace(incomplete_path, destination_path)

        hf_file_download._maestro_cancellation_patched = False
        hf_file_download._download_to_tmp_and_move = fake
        download_control.install_huggingface_cancellation()

        hf_file_download._download_to_tmp_and_move(self.partial, self.destination)

        self.assertFalse(os.path.exists(self.partial))
        self.assertTrue(os.path.exists(self.destination))


if __name__ == "__main__":
    unittest.main()
