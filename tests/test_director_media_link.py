"""A render with no sidecar still knows which project made it.

The gallery learns a video's project from the sidecar written beside it. When that file is
missing the video stays visible -- its prompt travels inside the mp4 -- but nothing links it
to a project, which is what "the videos are there but the Dashboard does not connect them"
describes. Measured on this install: 89 videos have no sidecar, 87 of them name their
project in their own parameters, and 38 name a project that still exists.

Director stages each project's audio and references under `_director_assets/<pid>/`, so the
paths recorded in the parameters are the signal. `_director_pipeline_id` inside the file was
present in 0 of those 38, and `multi_clip_info` in 35, so neither can be the rule.
"""

import os
import sys
import tempfile
import unittest


_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director_media_link import (  # noqa: E402
    attach_project_link,
    project_id_from_params,
    project_state_exists,
)

_ROOT = r"C:\pinokio\api\Maestro.git\app\outputs\la-brecha-de-ia"


def _asset(pid: str, name: str) -> str:
    return rf"{_ROOT}\_director_assets\{pid}\{name}"


OWN_VIDEO = {
    "prompt": "subject_definitions: <Subject 1> is subject 1: Rudo ...",
    "multi_clip_info": {
        "group_id": "mc_1789350352_-1",
        "index": 16,
        "total": 123,
        "concat_audio_path": _asset("4fd0ad38", "audio_path_7eb4ec9a.wav"),
    },
    "minimax_h3_references": [
        {"path": _asset("4fd0ad38", "minimax_h3_references_1_visual.jpg"), "role": "Bela"},
        {"path": _asset("4fd0ad38", "minimax_h3_references_2_visual.jpg"), "role": "Rudo"},
    ],
}


class ReadingTheProjectFromTheFile(unittest.TestCase):
    def test_the_asset_folder_names_the_project(self):
        self.assertEqual(project_id_from_params(OWN_VIDEO), "4fd0ad38")

    def test_both_path_separators_count(self):
        """Windows writes backslashes and a hand-edited or POSIX-written one uses "/"."""
        mixed = {
            "a": "outputs/la-brecha-de-ia/_director_assets/4fd0ad38/audio.wav",
            "b": _asset("4fd0ad38", "audio.wav"),
        }
        self.assertEqual(project_id_from_params(mixed), "4fd0ad38")

    def test_the_owner_outvotes_a_borrowed_path(self):
        """A reused reference adds one path; the project that staged the render adds many."""
        borrowed = {
            "reference": {"path": "outputs/x/_director_assets/bbbbbbbb/ref.jpg"},
            "other": {"path": "outputs/x/_director_assets/bbbbbbbb/ref2.jpg"},
            "audio": _asset("4fd0ad38", "audio.wav"),
            "joined": _asset("4fd0ad38", "joined.wav"),
            "vocals": _asset("4fd0ad38", "vocals.wav"),
        }
        self.assertEqual(project_id_from_params(borrowed), "4fd0ad38")

    def test_a_tie_keeps_the_first_project_named(self):
        tied = {
            "first": {"path": _asset("aaaaaaaa", "one.wav")},
            "second": {"path": _asset("bbbbbbbb", "two.wav")},
        }
        self.assertEqual(project_id_from_params(tied), "aaaaaaaa")

    def test_a_recorded_id_wins_over_the_paths(self):
        """When the generator did record it, that is the project, whatever the paths say."""
        recorded = {**OWN_VIDEO, "_director_pipeline_id": "07d75278"}
        self.assertEqual(project_id_from_params(recorded), "07d75278")

    def test_a_longer_hex_run_is_not_read_as_an_id(self):
        """Ids are exactly as long as the folder Director creates; a ninth digit disqualifies."""
        odd = {"path": _asset("4fd0ad38a", "audio.wav")}
        self.assertEqual(project_id_from_params(odd), "")

    def test_an_unrelated_parameter_tree_names_no_project(self):
        self.assertEqual(project_id_from_params({"prompt": "a studio render"}), "")
        self.assertEqual(project_id_from_params({"d": {"n": 3, "l": [1, None, True]}}), "")

    def test_nothing_to_read_is_not_an_error(self):
        for value in (None, {}, [], "text", 7, {"params": None}):
            self.assertEqual(project_id_from_params(value), "")

    def test_a_cycle_stops_instead_of_walking_forever(self):
        """Parameters are JSON today, but a gallery request must not be able to hang."""
        cyclic: dict = {"path": _asset("4fd0ad38", "audio.wav")}
        cyclic["self"] = cyclic
        self.assertEqual(project_id_from_params(cyclic), "4fd0ad38")


class AttachingTheLink(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _project_on_disk(self, pid="4fd0ad38"):
        with open(os.path.join(self.tmp.name, f"_director_pipeline_{pid}.json"), "w", encoding="utf-8") as handle:
            handle.write("{}")
        return self.tmp.name

    def test_a_gap_is_filled(self):
        metadata = {"source": "embedded", "params": OWN_VIDEO}
        self.assertIs(attach_project_link(metadata, self._project_on_disk()), metadata)
        self.assertEqual(metadata["director_pipeline_id"], "4fd0ad38")

    def test_a_deleted_project_is_not_linked(self):
        """The crux: this id moves the Load settings button, so a dead one must not be added.

        Without the check these renders -- whose parameters are perfectly loadable -- would
        have that button taken over by a project that no longer exists.
        """
        metadata = {"source": "embedded", "params": OWN_VIDEO}
        attach_project_link(metadata, self.tmp.name)
        self.assertNotIn("director_pipeline_id", metadata)

    def test_the_sidecar_is_never_overwritten(self):
        """The sidecar is the record of record; this only fills a gap."""
        metadata = {"source": "sidecar", "director_pipeline_id": "f177abab", "params": OWN_VIDEO}
        attach_project_link(metadata, self._project_on_disk())
        self.assertEqual(metadata["director_pipeline_id"], "f177abab")

    def test_an_empty_id_is_a_gap(self):
        metadata = {"director_pipeline_id": "", "params": OWN_VIDEO}
        attach_project_link(metadata, self._project_on_disk())
        self.assertEqual(metadata["director_pipeline_id"], "4fd0ad38")

    def test_metadata_without_a_project_is_left_alone(self):
        metadata = {"source": "embedded", "params": {"prompt": "a studio render"}}
        attach_project_link(metadata, self._project_on_disk())
        self.assertNotIn("director_pipeline_id", metadata)

    def test_a_non_dict_passes_through(self):
        for value in (None, [], "text"):
            self.assertIs(attach_project_link(value, self.tmp.name), value)


class KnowingWhetherTheProjectIsStillThere(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_the_state_file_beside_the_render_is_the_evidence(self):
        path = os.path.join(self.tmp.name, "_director_pipeline_4fd0ad38.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{}")
        self.assertTrue(project_state_exists(self.tmp.name, "4fd0ad38"))
        self.assertFalse(project_state_exists(self.tmp.name, "deadbeef"))

    def test_a_missing_folder_or_id_is_not_a_project(self):
        for out_dir, pid in (("", "4fd0ad38"), (self.tmp.name, ""), ("", "")):
            self.assertFalse(project_state_exists(out_dir, pid))

    def test_a_backup_copy_is_not_the_project(self):
        """A `.bak-...` file is history, not a project the dashboard can open."""
        path = os.path.join(self.tmp.name, "_director_pipeline_4fd0ad38.json.bak-20260922-121626")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{}")
        self.assertFalse(project_state_exists(self.tmp.name, "4fd0ad38"))


class TheEndpointUsesIt(unittest.TestCase):
    """The module is only useful if the metadata route actually calls it."""

    def setUp(self):
        launch = os.path.join(_APP_DIR, "launch.py")
        with open(launch, "r", encoding="utf-8") as handle:
            self.launch = handle.read()

    def test_the_route_attaches_the_link(self):
        route = self.launch.index('@api.get("/api/v1/outputs/{name}/metadata")')
        handler = self.launch[route : route + 2400]
        self.assertIn("attach_project_link(metadata, out_dir)", handler)
        self.assertIn("from services.director_media_link import attach_project_link", handler)

    def test_every_return_path_goes_through_the_helper(self):
        """Sidecar, embedded and none all return through _with_file_details."""
        route = self.launch.index('@api.get("/api/v1/outputs/{name}/metadata")')
        handler = self.launch[route : route + 3600]
        self.assertEqual(handler.count("_with_file_details("), 4)
        self.assertEqual(handler.count("return enrich_metadata("), 1)


if __name__ == "__main__":
    unittest.main()
