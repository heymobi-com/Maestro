"""Handing the writer's memory to the renderer, and saying so when it comes back.

The reported problem: "el tema de que strata no haga load and reload automatico con maestro causa
ciertos problemas de coordinacion y ademas confunde al usuario". Measured on this machine with Strata
loaded: 32.7 GiB of RAM and 14915 MiB of VRAM in use; after ``POST /v1/unload``: 8.9 GiB and 5416 MiB.
The server loads itself again on the next request -- 22.0 s warm, 52.7 s cold with streaming -- so the
hand-over costs one slow call and buys the render the difference.

Two halves have to stay honest: a release that cannot happen must be silent (a hosted API has no such
switch, and a render must never fail because of this), and a call that is about to pay for the reload
must say so instead of pausing unexplained.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from services import llm_writer_health, llm_writer_supervision  # noqa: E402

STRATA = "http://127.0.0.1:8081"
HOSTED = "https://api.openai.com/v1"


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status
        self.ok = 200 <= status < 300

    def json(self):
        return json.loads(self._body) if isinstance(self._body, str) else self._body


def listing(state="loaded"):
    return FakeResponse(json.dumps({"object": "list", "data": [
        {"id": "m", "status": {"value": state}}]}))


def unloaded_response():
    return FakeResponse(json.dumps({
        "status": "unloaded",
        "machine": {"ram": {"used_gib": 8.9}, "gpu": {"used_mib": 5416.0}},
    }))


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        llm_writer_health.forget()
        llm_writer_supervision._forget_notes()
        self.remember(STRATA)

    def remember(self, url, provider="remote"):
        with patch("requests.get", return_value=listing()):
            llm_writer_health.answers(provider, url, "m")

    def test_a_loaded_writer_is_asked_to_give_its_memory_back(self):
        with patch("requests.get", return_value=listing()) as get, \
             patch("requests.post", return_value=unloaded_response()) as post:
            self.assertTrue(llm_writer_supervision.release(pid="p1", stage="video render"))
        self.assertEqual(post.call_args[0][0], f"{STRATA}/v1/unload")
        get.assert_called()  # asked before it acted, so an unloaded writer is not unloaded twice

    def test_it_says_what_came_back(self):
        with patch("requests.get", return_value=listing()), \
             patch("requests.post", return_value=unloaded_response()), \
             patch("builtins.print") as printed:
            llm_writer_supervision.release(pid="p1", stage="video render")
        line = " ".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertIn("writer released for the video render", line)
        self.assertIn("8.9 GiB of RAM and 5416 MiB of VRAM still in use", line)

    def test_a_writer_that_already_gave_it_back_is_left_alone(self):
        with patch("requests.get", return_value=listing("unloaded")), \
             patch("requests.post") as post, patch("builtins.print") as printed:
            self.assertTrue(llm_writer_supervision.release(pid="p1"))
        post.assert_not_called()
        self.assertEqual(printed.call_args_list, [])

    def test_a_hosted_api_is_never_asked(self):
        self.remember(HOSTED)
        with patch("requests.get") as get, patch("requests.post") as post:
            self.assertFalse(llm_writer_supervision.release(pid="p1"))
        get.assert_not_called()
        post.assert_not_called()

    def test_an_address_that_is_not_ours_is_never_asked(self):
        self.remember("http://example.com:8081")
        with patch("requests.post") as post:
            self.assertFalse(llm_writer_supervision.release(pid="p1"))
        post.assert_not_called()

    def test_a_server_that_does_not_say_is_left_alone(self):
        with patch("requests.get", return_value=FakeResponse("{}", status=404)), \
             patch("requests.post") as post:
            self.assertFalse(llm_writer_supervision.release(pid="p1"))
        post.assert_not_called()

    def test_a_busy_writer_is_not_fought_for(self):
        # The server answers 409 while a request runs or is queued: the render waits its turn.
        busy = FakeResponse(json.dumps({"error": {"message": "a request is running or queued"}}), status=409)
        with patch("requests.get", return_value=listing()), patch("requests.post", return_value=busy), \
             patch("builtins.print") as printed:
            self.assertFalse(llm_writer_supervision.release(pid="p1"))
        self.assertIn("a request is running or queued", " ".join(str(c.args[0]) for c in printed.call_args_list))

    def test_a_render_never_fails_because_of_this(self):
        with patch("requests.get", side_effect=OSError("no route to host")):
            self.assertFalse(llm_writer_supervision.release(pid="p1"))
        with patch("requests.get", return_value=listing()), \
             patch("requests.post", side_effect=RuntimeError("socket closed")):
            self.assertFalse(llm_writer_supervision.release(pid="p1"))

    def test_a_writer_that_was_never_used_is_not_guessed_at(self):
        llm_writer_health._writer = ("", "")
        with patch("requests.post") as post:
            self.assertFalse(llm_writer_supervision.release(pid="p1"))
        post.assert_not_called()


class NoteTests(unittest.TestCase):
    """The reload is announced once, where the wait happens, instead of being left unexplained."""

    def setUp(self):
        llm_writer_health.forget()
        llm_writer_supervision._forget_notes()

    def test_a_released_writer_still_counts_as_answering(self):
        # It loads itself on the request that follows, so "can be reached" has to stay true: the run
        # must not be refused for a writer that is one call away from being ready.
        with patch("requests.get", return_value=listing("unloaded")):
            self.assertTrue(llm_writer_health.answers("remote", STRATA, "m"))

    def test_the_wait_is_announced(self):
        with patch("requests.get", return_value=listing("unloaded")), patch("builtins.print") as printed:
            llm_writer_health.answers("remote", STRATA, "m")
        line = " ".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertIn("released the model to free memory", line)
        self.assertIn("20-50 s", line)

    def test_it_is_said_once_per_spell_not_once_per_poll(self):
        with patch("requests.get", return_value=listing("unloaded")), patch("builtins.print") as printed:
            llm_writer_health.answers("remote", STRATA, "m")
            llm_writer_health.forget(STRATA)
            llm_writer_health.answers("remote", STRATA, "m")
        said = [call for call in printed.call_args_list if "released the model" in str(call.args[0])]
        self.assertEqual(len(said), 1)

    def test_a_loaded_writer_says_nothing(self):
        with patch("requests.get", return_value=listing()), patch("builtins.print") as printed:
            llm_writer_health.answers("remote", STRATA, "m")
        self.assertEqual(printed.call_args_list, [])

    def test_the_writer_in_use_is_remembered_for_whoever_has_to_act_on_it(self):
        with patch("requests.get", return_value=listing()):
            llm_writer_health.answers("remote", STRATA, "m")
        self.assertEqual(llm_writer_health.last_writer(), ("remote", STRATA))


class WiringTests(unittest.TestCase):
    """The pipeline hands the memory over before it renders, on both stages."""

    def test_an_image_render_releases_the_writer(self):
        source = (ROOT / "app" / "services" / "director_pipeline.py").read_text(encoding="utf-8")
        self.assertIn("from services.llm_writer_supervision import release as release_writer", source)
        self.assertIn('release_writer(pid, "image render")', source)
        self.assertIn('release_writer(pid, "video render")', source)
        self.assertLess(source.index('release_writer(pid, "image render")'),
                        source.index("def _run_video_generation"))

    def test_it_is_released_before_the_render_starts_not_after(self):
        source = (ROOT / "app" / "services" / "director_pipeline.py").read_text(encoding="utf-8")
        video = source.index("def _run_video_generation")
        self.assertLess(source.index('release_writer(pid, "video render")', video),
                        source.index("_apply_h3_music_audio_contract", video))


if __name__ == "__main__":
    unittest.main()
