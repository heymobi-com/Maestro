"""A clip that was stopped mid-run still names the film it belongs to.

Measured on a real 26-shot film: the user stopped the run after five clips. Full clip
sidecars are written when a job finishes, so those five mp4s had no ``.meta.json`` at
all. The gallery then showed them as ordinary renders — no Director project, and the
pencil offered nothing — while the same clips from the previous day, whose jobs had
finished, did offer the project. The state file named every one of the five files.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.director.clip_publish import publish_finished_clip  # noqa: E402
from services.gallery_library import GalleryLibrary  # noqa: E402


class GalleryOwnershipWithoutSidecarTests(unittest.TestCase):
    """The gallery recovers the project from the saved plan, not only from a sidecar."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "Valdecuna"
        self.folder.mkdir()
        self.folders = [("Valdecuna", str(self.folder))]
        self.library = GalleryLibrary()

    def _clip(self, name: str, stamp: int, *, sidecar: dict | None = None):
        path = self.folder / name
        path.write_bytes(b"media")
        os.utime(path, (stamp, stamp))
        if sidecar is not None:
            path.with_suffix(".meta.json").write_text(json.dumps(sidecar), encoding="utf-8")
        return path

    def _plan(self, pid: str, filenames: list[str], **extra):
        state = {
            "pipeline_id": pid,
            "status": "cancelled",
            "seamless": False,
            "clips": [
                {"index": index, "video_filename": name, "video_prompt": f"shot {index}"}
                for index, name in enumerate(filenames)
            ],
            "output_files": [],
            **extra,
        }
        (self.folder / f"_director_pipeline_{pid}.json").write_text(
            json.dumps(state), encoding="utf-8",
        )

    def _item(self, name: str) -> dict:
        result = self.library.list(self.folders)
        for item in result["outputs"]:
            if item["name"] == name:
                return item
        self.fail(f"{name} is not in the gallery listing")

    def test_a_clip_with_no_sidecar_still_belongs_to_its_film(self):
        # The exact shape of the stopped run: files on disk, one plan that names them.
        self._plan("99d59032", ["shot0.mp4", "shot1.mp4"])
        self._clip("shot0.mp4", 100)
        self._clip("shot1.mp4", 200)

        item = self._item("shot1.mp4")

        self.assertEqual(item.get("director_pipeline_id"), "99d59032")
        self.assertEqual(item.get("director_clip_index"), 1)

    def test_a_sidecar_that_already_names_the_film_is_left_alone(self):
        self._plan("film1", ["shot0.mp4"])
        self._clip("shot0.mp4", 100, sidecar={
            "generation_mode": "video",
            "director_pipeline_id": "film1",
            "director_clip_index": 0,
            "params": {"prompt": "its own recipe"},
        })

        item = self._item("shot0.mp4")

        self.assertEqual(item.get("director_pipeline_id"), "film1")
        self.assertEqual(item.get("director_clip_index"), 0)

    def test_a_file_the_plan_does_not_name_is_not_claimed(self):
        self._plan("film2", ["shot0.mp4"])
        self._clip("shot0.mp4", 100)
        self._clip("unrelated.mp4", 200)

        item = self._item("unrelated.mp4")

        self.assertIsNone(item.get("director_pipeline_id"))
        self.assertIsNone(item.get("director_clip_index"))

    def test_images_are_not_read_as_unclaimed_clips(self):
        # An image with no sidecar must not make every scan parse the plans.
        self._plan("film3", ["shot0.mp4"])
        self._clip("shot0.mp4", 100)
        self._clip("frame.png", 200)

        self.assertEqual(self._item("shot0.mp4").get("director_pipeline_id"), "film3")
        self.assertIsNone(self._item("frame.png").get("director_pipeline_id"))


class OwnershipSidecarTests(unittest.TestCase):
    """The clip publishes its own ownership the moment its file exists."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = self.temp.name
        self.job = {"params": {"_director_pipeline_id": "abc12345"}}
        self.clips: dict[int, str] = {}

    def _sidecar(self, filename: str) -> dict:
        path = Path(self.dir) / (os.path.splitext(filename)[0] + ".meta.json")
        return json.loads(path.read_text(encoding="utf-8"))

    def test_a_finished_clip_names_its_film_and_its_index(self):
        publish_finished_clip(self.job, 2, "shot2.mp4", self.clips, out_dir=self.dir)

        sidecar = self._sidecar("shot2.mp4")
        self.assertEqual(sidecar["director_pipeline_id"], "abc12345")
        self.assertEqual(sidecar["director_clip_index"], 2)
        self.assertEqual(sidecar["director_artifact_kind"], "clip")
        self.assertTrue(sidecar["director_clip_settings_partial"])

    def test_an_existing_sidecar_is_never_overwritten(self):
        # The completion writer owns the full record; this must not clobber it.
        full = Path(self.dir) / "shot3.meta.json"
        full.write_text(json.dumps({"generation_time": 42, "params": {"prompt": "x"}}),
                        encoding="utf-8")

        publish_finished_clip(self.job, 3, "shot3.mp4", self.clips, out_dir=self.dir)

        self.assertEqual(json.loads(full.read_text(encoding="utf-8"))["generation_time"], 42)

    def test_a_job_without_a_director_project_writes_nothing(self):
        job = {"params": {}}

        publish_finished_clip(job, 0, "plain.mp4", self.clips, out_dir=self.dir)

        self.assertFalse((Path(self.dir) / "plain.meta.json").exists())
        # The clip is still recorded on the job, which is the older contract.
        self.assertEqual(self.clips, {0: "plain.mp4"})

    def test_no_output_folder_is_not_an_error(self):
        recorded = publish_finished_clip(self.job, 0, "shot0.mp4", self.clips)

        self.assertTrue(recorded)
        self.assertEqual(self.clips, {0: "shot0.mp4"})

    def test_an_empty_filename_records_nothing(self):
        self.assertFalse(
            publish_finished_clip(self.job, 0, None, self.clips, out_dir=self.dir),
        )
        self.assertEqual(self.clips, {})


if __name__ == "__main__":
    unittest.main()
