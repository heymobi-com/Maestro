"""A stopped batch resumes where it stopped, because each clip is recorded as it finishes.

Reported from use: "resume goes back to generating from clip 1 instead of picking the batch up
where it left off". Measured on the affected project: 55 planned clips, **19 mp4 files in the
folder, and a saved state that named none of them** -- so the resume had nothing to read and
started at the first frame again.

Two independent halves were missing, and neither is enough alone:

* the reader only looked at process memory, which a restart empties (``resume_prefix``), and
* the renderer kept the finished clips in a local variable and put them on the job only after
  the last clip, so a Stop mid-batch saved a state that named nothing at all
  (``clip_publish``).

These cases are ours, so they live in their own file: the cancellation tests they belong with
are upstream's, and every line we add there is a line that can conflict.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services import director_pipeline as pipeline  # noqa: E402
from services.director.clip_publish import publish_finished_clip  # noqa: E402


class FinishedClipIsRecordedAtOnceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self._saved_pipelines = pipeline._pipelines
        pipeline._pipelines = {}

    def tearDown(self):
        pipeline._pipelines = self._saved_pipelines
        self.temp_dir.cleanup()

    def _add_pipeline(self, pid, clip_count):
        pipeline._pipelines[pid] = {
            "id": pid,
            "status": "running",
            "phase": "generating_video",
            "progress": {"current": 1, "total": clip_count},
            "clip_plans": [{}] * clip_count,
            "clip_images": [],
            "output_files": [],
            "created_at": time.time(),
            "params": {"pipeline_type": "music_video"},
            "out_dir": self.temp_dir.name,
        }

    def _write_media(self, filename):
        with open(os.path.join(self.temp_dir.name, filename), "wb") as handle:
            handle.write(b"media")

    def _finish_clip(self, pid, clip_index, filename, slots):
        """The chain the render itself runs: publish, then mirror into the state."""

        job = {"params": {}, "output_files": []}
        publish_finished_clip(job, clip_index, filename, slots)
        job["output_files"] = [filename]
        pipeline._persist_finished_clips(pid, job)
        return job

    def test_a_finished_clip_is_put_on_the_job_at_once(self):
        """The map must not wait for the last clip of the batch."""

        job = {"params": {}, "output_files": []}
        slots: dict[int, str] = {}

        self.assertTrue(
            publish_finished_clip(job, 0, "clip0.mp4", slots),
        )

        self.assertEqual(slots, {0: "clip0.mp4"})
        self.assertEqual(dict(job["clip_output_files"]), {"0": "clip0.mp4"})

    def test_a_later_clip_extends_the_record_instead_of_replacing_it(self):
        job = {"params": {}, "output_files": []}
        slots: dict[int, str] = {}

        publish_finished_clip(job, 0, "clip0.mp4", slots)
        publish_finished_clip(job, 2, "clip2.mp4", slots)

        self.assertEqual(dict(job["clip_output_files"]), {
            "0": "clip0.mp4", "2": "clip2.mp4",
        })
        self.assertEqual(slots, {0: "clip0.mp4", 2: "clip2.mp4"})

    def test_an_empty_filename_is_not_a_clip(self):
        job = {"params": {}, "output_files": []}
        slots: dict[int, str] = {}

        self.assertFalse(publish_finished_clip(job, 1, "", slots))

        self.assertEqual(slots, {})
        self.assertNotIn("clip_output_files", job)

    def test_the_render_publishes_each_clip_through_this_call(self):
        """A refactor must not move the record back to the end of the batch."""

        with open(
            os.path.join(_APP_DIR, "launch.py"), "r", encoding="utf-8",
        ) as handle:
            launch_source = handle.read()

        self.assertIn("publish_finished_clip(", launch_source)
        self.assertIn(
            "from services.director.clip_publish import publish_finished_clip",
            launch_source,
        )

    def test_two_finished_clips_survive_a_restart_and_resume_at_the_third(self):
        """The exact failure: the state named nothing, so clip 1 was rendered again."""

        pid = "pipe-recorded"
        self._add_pipeline(pid, 3)
        self._write_media("clip0.mp4")
        self._write_media("clip1.mp4")
        slots: dict[int, str] = {}

        self._finish_clip(pid, 0, "clip0.mp4", slots)
        self._finish_clip(pid, 1, "clip1.mp4", slots)

        # A restart empties the process memory the record used to live in.
        pipeline._pipelines = {}

        completed = pipeline._completed_clip_video_prefix(
            pid, 3, self.temp_dir.name,
        )

        self.assertEqual(completed, ["clip0.mp4", "clip1.mp4", None])
        self.assertEqual(
            next(index for index, name in enumerate(completed) if not name),
            2,
        )

    def test_a_clip_that_is_not_recorded_is_not_invented_from_the_folder(self):
        """Two clips are on disk, but nothing says which shots they are.

        A resumed batch renumbers its own clips from zero, so a filename can never be turned
        into a film position: guessing here is what would file a real shot under the wrong
        clip. The record is the source of truth, and an empty one says "nothing to reuse".
        """

        pid = "pipe-unrecorded"
        self._add_pipeline(pid, 3)
        self._write_media("clip0.mp4")
        self._write_media("clip1.mp4")

        self.assertEqual(
            pipeline._completed_clip_video_prefix(pid, 3, self.temp_dir.name),
            [],
        )

    def test_a_record_whose_media_is_gone_is_not_trusted(self):
        """A state file can outlive the videos it names."""

        pid = "pipe-gone"
        self._add_pipeline(pid, 2)
        self._write_media("clip0.mp4")
        self._write_media("clip1.mp4")
        slots: dict[int, str] = {}
        self._finish_clip(pid, 0, "clip0.mp4", slots)
        self._finish_clip(pid, 1, "clip1.mp4", slots)
        os.remove(os.path.join(self.temp_dir.name, "clip1.mp4"))
        pipeline._pipelines = {}

        self.assertEqual(
            pipeline._completed_clip_video_prefix(pid, 2, self.temp_dir.name),
            ["clip0.mp4", None],
        )


if __name__ == "__main__":
    unittest.main()
