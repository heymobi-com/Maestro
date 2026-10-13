"""Handing the writer's memory to the renderer, and saying so when it comes back.

The reported problem: "el tema de que strata no haga load and reload automatico con maestro causa
ciertos problemas de coordinacion y ademas confunde al usuario". Measured on this machine with Strata
loaded: 32.7 GiB of RAM and 14915 MiB of VRAM in use; after ``POST /v1/unload``: 8.9 GiB and 5416 MiB.
The server loads itself again on the next request -- 22.0 s warm, 52.7 s cold with streaming -- so the
hand-over costs one slow call and buys the render the difference.

Two halves have to stay honest: a release that cannot happen must never fail a render (a hosted API
has no such switch), and it must say so rather than pass in silence -- a hand-over that never happened
is what a render reports as an out-of-memory error with the GPU idle. A call that is about to pay for
the reload says so too, instead of pausing unexplained.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
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
        # The tests must not read the real settings (or reach the real server): the configured
        # writer is set per test.
        self.configured = patch.object(llm_writer_supervision, "_configured", return_value=("", ""))
        self.configured.start()
        self.addCleanup(self.configured.stop)
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

    def test_the_queue_hand_over_releases_a_loaded_writer(self):
        with patch("requests.get", return_value=listing()), \
             patch("requests.post", return_value=unloaded_response()), \
             patch("builtins.print") as printed:
            self.assertTrue(llm_writer_supervision.release_before_render(pid="job-1"))
        line = " ".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertIn("writer released for the render", line)

    def test_the_queue_hand_over_names_the_reason_when_nothing_came_back(self):
        # Reported from use: the render failed out of memory with the GPU idle and free VRAM, and
        # nothing in the log said whether the writer had been given back. Now it does.
        llm_writer_health._writer = ("", "")          # nothing connected and nothing configured
        with patch.object(llm_writer_supervision, "_configured", return_value=("", "")), \
             patch("builtins.print") as printed:
            self.assertFalse(llm_writer_supervision.release_before_render(pid="job-1"))
        line = " ".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertIn("the writer was not released before the render", line)
        self.assertIn("no writer on this machine is configured", line)

    def test_the_queue_hand_over_never_raises(self):
        with patch("requests.get", side_effect=OSError("no route to host")), \
             patch("builtins.print"):
            self.assertFalse(llm_writer_supervision.release_before_render(pid="job-1"))

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
        llm_writer_health._writer = ("", "")          # nothing has connected yet
        with patch.object(llm_writer_supervision, "_configured", return_value=("", "")), \
             patch("requests.post") as post:
            self.assertFalse(llm_writer_supervision.release(pid="p1"))
        post.assert_not_called()

    def test_the_configured_writer_is_used_before_anything_has_connected(self):
        # The control in Settings is opened before the first planning call, so the writer named there
        # has to answer for itself -- otherwise the button reads "unknown" on a server that is
        # configured and simply has not planned yet.
        llm_writer_health._writer = ("", "")
        with patch.object(llm_writer_supervision, "_configured", return_value=("remote", STRATA)), \
             patch("requests.get", return_value=listing()) as get, \
             patch("requests.post", return_value=unloaded_response()) as post:
            self.assertTrue(llm_writer_supervision.release(pid="p1"))
        get.assert_called()
        post.assert_called_once()

    def test_a_configured_writer_that_is_not_ours_is_left_alone(self):
        llm_writer_health._writer = ("", "")
        with patch.object(llm_writer_supervision, "_configured", return_value=("openai", "https://api.openai.com/v1")), \
             patch("requests.post") as post:
            self.assertFalse(llm_writer_supervision.release(pid="p1"))
        post.assert_not_called()

    def test_the_configured_address_is_acted_on_even_when_another_provider_is_selected(self):
        # Measured in a real install: the provider field said "local" (a Gemma GGUF) while the remote
        # URL was Strata's. The memory worth releasing belongs to the server, so the address decides
        # and the provider field is not consulted -- otherwise the control would do nothing on the
        # one machine that has a writer worth releasing.
        llm_writer_health._writer = ("", "")
        with patch.object(llm_writer_supervision, "_configured", return_value=("local", STRATA)), \
             patch("requests.get", return_value=listing()), \
             patch("requests.post", return_value=unloaded_response()) as post:
            self.assertTrue(llm_writer_supervision.release(pid="p1"))
        post.assert_called_once()


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


class MemoryControlTests(unittest.TestCase):
    """What the control in Settings reads, and the two things it can ask for."""

    def setUp(self):
        llm_writer_health.forget()
        llm_writer_supervision._forget_notes()
        self.configured = patch.object(llm_writer_supervision, "_configured", return_value=("", ""))
        self.configured.start()
        self.addCleanup(self.configured.stop)
        with patch("requests.get", return_value=listing()):
            llm_writer_health.answers("remote", STRATA, "m")

    def test_memory_reports_what_the_writer_holds(self):
        with patch("requests.get", return_value=listing()):
            self.assertEqual(llm_writer_supervision.memory()["state"], "loaded")
        with patch("requests.get", return_value=listing("unloaded")):
            answer = llm_writer_supervision.memory()
        self.assertEqual(answer["state"], "unloaded")
        self.assertIn("loads it back", answer["detail"])

    def test_memory_admits_it_when_the_writer_is_not_ours(self):
        with patch("requests.get", return_value=listing()):
            llm_writer_health.answers("remote", HOSTED, "m")
        self.assertEqual(llm_writer_supervision.memory()["state"], "unknown")

    def test_loading_now_asks_the_writer_and_says_so(self):
        loaded = FakeResponse(json.dumps({"status": "loaded"}))
        with patch("requests.post", return_value=loaded) as post:
            self.assertTrue(llm_writer_supervision.load())
        self.assertEqual(post.call_args[0][0], f"{STRATA}/v1/load")

    def test_a_load_that_is_refused_is_reported_not_raised(self):
        busy = FakeResponse(json.dumps({"error": {"message": "another program holds the GPU"}}), status=503)
        with patch("requests.post", return_value=busy), patch("builtins.print") as printed:
            self.assertFalse(llm_writer_supervision.load())
        self.assertIn("another program holds the GPU", " ".join(str(c.args[0]) for c in printed.call_args_list))

    def test_the_routes_the_control_calls(self):
        paths = sorted((route.path, tuple(sorted(route.methods))) for route in llm_writer_supervision.router.routes)
        self.assertEqual(paths, [
            ("/api/v1/llm/writer/autostart", ("GET",)),
            ("/api/v1/llm/writer/autostart", ("POST",)),
            ("/api/v1/llm/writer/memory", ("GET",)),
            ("/api/v1/llm/writer/memory/load", ("POST",)),
            ("/api/v1/llm/writer/memory/release", ("POST",)),
            ("/api/v1/llm/writer/start", ("POST",)),
        ])

    def test_the_release_route_answers_with_the_new_state(self):
        state = {"value": "loaded"}

        def after_release(url, **kwargs):
            state["value"] = "unloaded"   # the server reports what it holds after the call
            return listing(state["value"])

        with patch("requests.get", side_effect=lambda url, **kwargs: listing(state["value"])), \
             patch("requests.post", side_effect=after_release):
            answer = llm_writer_supervision.writer_release()
        self.assertTrue(answer["released"])
        self.assertEqual(answer["state"], "unloaded")

    def test_the_load_route_answers_with_the_new_state(self):
        with patch("requests.post", return_value=FakeResponse(json.dumps({"status": "loaded"}))), \
             patch("requests.get", return_value=listing()):
            answer = llm_writer_supervision.writer_load()
        self.assertTrue(answer["loaded"])
        self.assertEqual(answer["state"], "loaded")

    def test_the_application_mounts_the_router(self):
        source = (ROOT / "app" / "launch.py").read_text(encoding="utf-8")
        self.assertIn("api.include_router(llm_writer_supervision.router)", source)
        # Imported and mounted: the count survives another feature joining the same import line.
        self.assertGreaterEqual(source.count("llm_writer_supervision"), 2, "imported and mounted")

    def test_the_panel_shows_the_control_for_the_strata_entry(self):
        panel = (ROOT / "ui" / "src" / "components" / "SettingsDrawer" / "ServicesSettingsPanel.tsx").read_text(
            encoding="utf-8")
        self.assertIn("import { WriterReleaseControl } from './WriterReleaseControl'", panel)
        # The control is told to refresh the model list: a writer that was down is started here, and
        # the list it fills is what makes the model selectable at all.
        self.assertIn("<WriterReleaseControl url={servicesConfig.llm_remote_url} onStarted={loadLlmModels} />", panel)
        # The LM Studio control stays for every other remote provider.
        self.assertIn("<RemoteModelMemoryControls key={servicesConfig.llm_remote_url}", panel)

    def test_the_control_asks_maestro_and_not_the_writer(self):
        # A model server accepts its control endpoints only from its own page; the browser is not it,
        # so the request goes to Maestro's own route.
        api = (ROOT / "ui" / "src" / "api" / "writerRelease.ts").read_text(encoding="utf-8")
        self.assertIn("fetch('/api/v1/llm/writer/memory", api)
        self.assertIn("'/api/v1/llm/writer/memory/release'", api)
        self.assertIn("'/api/v1/llm/writer/memory/load'", api)
        self.assertNotIn("8081", api)


class AutostartTests(unittest.TestCase):
    """Starting a writer that is not running, which is the half that was missing.

    Reported from use: "quiero decir que si no lo arranco primero nunca va a iniciar? aqui es donde
    estoy confundido" -- with the server stopped the model list was empty and generating did nothing.
    """

    def setUp(self):
        llm_writer_health.forget()
        llm_writer_supervision._forget_notes()
        llm_writer_supervision._last_attempt = 0.0
        llm_writer_supervision._starting = False
        self.config = Path(tempfile.mkdtemp()) / "writer_supervision.json"
        self.path = patch.object(llm_writer_supervision, "_config_path", return_value=str(self.config))
        self.path.start()
        self.addCleanup(self.path.stop)
        self.configured = patch.object(llm_writer_supervision, "_configured", return_value=("", ""))
        self.configured.start()
        self.addCleanup(self.configured.stop)
        with patch("requests.get", return_value=listing()):
            llm_writer_health.answers("remote", STRATA, "m")

    def test_a_test_never_launches_a_process(self):
        # The suite must not start a server on the machine it runs on, whatever the config says.
        llm_writer_supervision.set_autostart("start-strata")
        with patch.object(llm_writer_supervision, "_in_application", return_value=False), \
             patch("subprocess.Popen") as popen:
            self.assertFalse(llm_writer_supervision.start())
        popen.assert_not_called()

    def test_nothing_is_started_without_a_command(self):
        with patch.object(llm_writer_supervision, "_in_application", return_value=True), \
             patch.object(llm_writer_supervision, "_answers", return_value=False), \
             patch("subprocess.Popen") as popen:
            self.assertFalse(llm_writer_supervision.start())
        popen.assert_not_called()

    def test_a_writer_that_already_answers_is_not_started(self):
        llm_writer_supervision.set_autostart("start-strata")
        with patch.object(llm_writer_supervision, "_in_application", return_value=True), \
             patch.object(llm_writer_supervision, "_answers", return_value=True), \
             patch("subprocess.Popen") as popen:
            self.assertTrue(llm_writer_supervision.start())
        popen.assert_not_called()

    def test_the_configured_command_is_run_and_waited_for(self):
        llm_writer_supervision.set_autostart("start-strata")
        answers = iter([False, False, True])          # down, then up

        def probe(_address):
            return next(answers, True)

        with patch.object(llm_writer_supervision, "_in_application", return_value=True), \
             patch.object(llm_writer_supervision, "_answers", side_effect=probe), \
             patch("time.sleep", return_value=None), \
             patch("subprocess.Popen", return_value=object()) as popen, patch("builtins.print") as printed:
            self.assertTrue(llm_writer_supervision.start(wait_seconds=30))
        popen.assert_called_once()
        self.assertIn("start-strata", popen.call_args[0][0])
        line = " ".join(str(c.args[0]) for c in printed.call_args_list)
        self.assertIn("is up after", line)

    def test_a_start_that_does_not_come_up_gives_up_and_says_so(self):
        llm_writer_supervision.set_autostart("start-strata")
        with patch.object(llm_writer_supervision, "_in_application", return_value=True), \
             patch.object(llm_writer_supervision, "_answers", return_value=False), \
             patch("time.sleep", return_value=None), \
             patch("subprocess.Popen", return_value=object()), patch("builtins.print") as printed:
            self.assertFalse(llm_writer_supervision.start(wait_seconds=9))
        self.assertIn("did not come up", " ".join(str(c.args[0]) for c in printed.call_args_list))

    def test_a_status_poll_cannot_start_a_stream_of_launches(self):
        llm_writer_supervision.set_autostart("start-strata")
        with patch.object(llm_writer_supervision, "_in_application", return_value=True), \
             patch.object(llm_writer_supervision, "_answers", side_effect=[False, False, False, False]), \
             patch("time.sleep", return_value=None), \
             patch("subprocess.Popen", return_value=object()) as popen:
            self.assertFalse(llm_writer_supervision.start(wait_seconds=3))
            self.assertFalse(llm_writer_supervision.start(wait_seconds=3))   # inside the cooldown
        self.assertEqual(popen.call_count, 1)

    def test_the_command_is_saved_and_read_back(self):
        self.assertEqual(llm_writer_supervision.autostart()["start"], "")
        llm_writer_supervision.set_autostart('start-strata -NoBrowser', 120)
        saved = llm_writer_supervision.autostart()
        self.assertEqual(saved["start"], "start-strata -NoBrowser")
        self.assertEqual(saved["wait_seconds"], 120)
        llm_writer_supervision.set_autostart("")                          # cleared: never start it
        self.assertEqual(llm_writer_supervision.autostart()["start"], "")

    def test_the_settings_control_reports_a_stopped_writer_as_down(self):
        with patch.object(llm_writer_supervision, "_answers", return_value=False), \
             patch("requests.get", side_effect=OSError("connection refused")):
            answer = llm_writer_supervision.memory()
        self.assertEqual(answer["state"], "unknown")
        self.assertEqual(answer["address"], STRATA)

    def test_a_stopped_writer_is_reported_in_plain_words(self):
        # The panel shows this line, so it must not be the connection pool's stack of retries.
        refused = ConnectionRefusedError("Failed to establish a new connection: [WinError 10061]")
        with patch.object(llm_writer_supervision, "_state", side_effect=refused):
            answer = llm_writer_supervision.memory()
        self.assertEqual(answer["detail"], "not running")


class AutostartRouteTests(unittest.TestCase):
    def setUp(self):
        self.config = Path(tempfile.mkdtemp()) / "writer_supervision.json"
        self.path = patch.object(llm_writer_supervision, "_config_path", return_value=str(self.config))
        self.path.start()
        self.addCleanup(self.path.stop)

    def test_the_route_saves_the_command(self):
        answer = llm_writer_supervision.writer_set_autostart({"start": "start-strata -NoBrowser"})
        self.assertEqual(answer["start"], "start-strata -NoBrowser")
        self.assertEqual(llm_writer_supervision.writer_autostart()["start"], "start-strata -NoBrowser")

    def test_the_route_clears_it(self):
        llm_writer_supervision.writer_set_autostart({"start": "start-strata"})
        llm_writer_supervision.writer_set_autostart({"start": ""})
        self.assertEqual(llm_writer_supervision.writer_autostart()["start"], "")

    def test_the_start_route_reports_whether_it_came_up(self):
        with patch.object(llm_writer_supervision, "start", return_value=True) as started, \
             patch.object(llm_writer_supervision, "memory", return_value={"state": "loaded", "address": STRATA}):
            answer = llm_writer_supervision.writer_start()
        started.assert_called_once()
        self.assertTrue(answer["started"])
        self.assertEqual(answer["state"], "loaded")


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

    def test_the_queue_hands_the_writer_over_between_writing_and_staging(self):
        """The path a render actually takes: after the last prompt it owes, before the checkpoint.

        Reported from use: a render started from the film tab never enters the Director pipeline, so
        the writer stayed resident, the video checkpoint tried to pin its weights into memory the
        writer was holding, and the render failed with "CUDA reported an out-of-memory error" while
        VRAM was free and the GPU idle.
        """

        source = (ROOT / "app" / "launch.py").read_text(encoding="utf-8")
        generation = source.index("def _run_generation(")
        hand_over = source.index("llm_writer_supervision.release_before_render", generation)
        self.assertLess(
            source.index("_apply_deferred_prompt_enhancement(job, raw_params)", generation),
            hand_over,
        )
        self.assertLess(hand_over, source.index("_apply_per_job_coefficient(job)", generation))


class TheArenaForecastTests(unittest.TestCase):
    """What a writer started right now will be able to hold resident.

    Reported from use: "me llama la atencion que varie tanto, cuando lo pare estaba generando solo 5
    o 6 tok/sec". The engine's own log explains it: it sizes its page-locked expert cache from the RAM
    it finds, and when only 14.70 GiB were free it capped the cache at 10.45 GiB and read the rest of
    the 27.41 GiB of experts from disk -- 4.9-16.5 tok/s against 31-60 for the full 24 GiB.
    """

    def test_the_arithmetic_matches_the_engines_own_reading(self):
        # Its line: "--resident-budget-gib 24.00 exceeds available RAM (14.70 GiB minus 4 GiB headroom
        # and a 0.25 GiB margin): 10.45 GiB"
        forecast = llm_writer_supervision.arena_forecast(14.70)

        self.assertAlmostEqual(forecast["arena_gib"], 10.45, places=2)
        self.assertTrue(forecast["short"])
        self.assertIn("10.4 GiB", forecast["warning"])

    def test_a_free_machine_is_not_a_warning(self):
        forecast = llm_writer_supervision.arena_forecast(30.0)

        self.assertEqual(forecast["short"], False)
        self.assertEqual(forecast["warning"], "")

    def test_the_warning_carries_the_measured_comparison(self):
        forecast = llm_writer_supervision.arena_forecast(14.70)

        self.assertIn("31-60 tok/s", forecast["warning"])
        self.assertIn("4.9-16.5 tok/s", forecast["warning"])

    def test_a_machine_that_cannot_report_is_not_warned_about(self):
        with patch.object(llm_writer_supervision, "available_ram_gib", return_value=0.0):
            forecast = llm_writer_supervision.arena_forecast()

        self.assertFalse(forecast["known"])
        self.assertFalse(forecast["short"])
        self.assertEqual(forecast["warning"], "")

    def test_it_reads_the_machine_when_asked_for_nothing(self):
        with patch.object(llm_writer_supervision, "available_ram_gib", return_value=30.0):
            forecast = llm_writer_supervision.arena_forecast()

        self.assertTrue(forecast["known"])
        self.assertAlmostEqual(forecast["free_gib"], 30.0, places=2)

    def test_the_reading_is_taken_from_the_operating_system(self):
        # Not a stub: on this machine the figure has to be a plausible amount of free RAM.
        free = llm_writer_supervision.available_ram_gib()

        self.assertGreater(free, 0.0)
        self.assertLess(free, 4096.0)


class TheForecastReachesTheOperatorTests(unittest.TestCase):
    """The warning is useless if it only exists when someone reads the engine's log."""

    def setUp(self):
        llm_writer_health.forget()
        llm_writer_supervision._forget_notes()
        llm_writer_supervision._last_attempt = 0.0
        llm_writer_supervision._starting = False
        self.config = Path(tempfile.mkdtemp()) / "writer_supervision.json"
        self.path = patch.object(llm_writer_supervision, "_config_path", return_value=str(self.config))
        self.path.start()
        self.addCleanup(self.path.stop)
        llm_writer_supervision.set_autostart("start-strata")

    def test_starting_a_writer_that_would_be_capped_says_so(self):
        with patch.object(llm_writer_supervision, "_in_application", return_value=True), \
             patch.object(llm_writer_supervision, "_answers", return_value=False), \
             patch.object(llm_writer_supervision, "arena_forecast",
                          return_value={"known": True, "free_gib": 14.7, "arena_gib": 10.45,
                                        "short": True, "warning": "CAPPED AT TEN"}), \
             patch.object(llm_writer_supervision, "_free_local_writer", return_value=""), \
             patch("subprocess.Popen"), patch("builtins.print") as printed:
            llm_writer_supervision.start(wait_seconds=1)

        line = " ".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertIn("CAPPED AT TEN", line)

    def test_a_local_writer_is_unloaded_before_the_one_being_started(self):
        # The mirror of the hand-over the render path does: a local llama-server holds host RAM too.
        freed = []
        with patch.object(llm_writer_supervision, "_in_application", return_value=True), \
             patch.object(llm_writer_supervision, "_answers", return_value=False), \
             patch.object(llm_writer_supervision, "arena_forecast",
                          return_value={"known": True, "free_gib": 14.7, "arena_gib": 10.45,
                                        "short": True, "warning": "CAPPED"}), \
             patch.object(llm_writer_supervision, "_free_local_writer",
                          side_effect=lambda: freed.append(True) or "unloaded the local writer first"), \
             patch("subprocess.Popen"), patch("builtins.print") as printed:
            llm_writer_supervision.start(wait_seconds=1)

        self.assertEqual(freed, [True])
        self.assertIn("unloaded the local writer first",
                      " ".join(str(call.args[0]) for call in printed.call_args_list))

    def test_a_healthy_forecast_does_not_touch_the_local_writer(self):
        with patch.object(llm_writer_supervision, "_in_application", return_value=True), \
             patch.object(llm_writer_supervision, "_answers", return_value=False), \
             patch.object(llm_writer_supervision, "arena_forecast",
                          return_value={"known": True, "free_gib": 30.0, "arena_gib": 25.75,
                                        "short": False, "warning": ""}), \
             patch.object(llm_writer_supervision, "_free_local_writer") as free, \
             patch("subprocess.Popen"):
            llm_writer_supervision.start(wait_seconds=1)

        free.assert_not_called()

    def test_a_test_never_unloads_the_operators_writer(self):
        # The same isolation the start command keeps: outside the running application, this must not
        # touch a real local writer. Measured cost of getting this wrong was thirty broken tests the
        # first time; this one would unload the operator's own server instead.
        with patch.object(llm_writer_supervision, "_in_application", return_value=False), \
             patch("services.llm_service.unload_model") as unload:
            self.assertEqual(llm_writer_supervision._free_local_writer(), "")

        unload.assert_not_called()

    def test_the_panel_sees_it_only_while_the_writer_is_down(self):
        # A writer that answers has already built its cache: warning about a start that is not going to
        # happen is what made this a false alarm the first time it was tried, on a running writer.
        down = patch.object(llm_writer_supervision, "_state", side_effect=OSError("refused"))
        with patch.object(llm_writer_supervision, "_ours", return_value=STRATA), down, \
             patch.object(llm_writer_supervision, "arena_forecast",
                          return_value={"known": True, "free_gib": 14.7, "arena_gib": 10.45,
                                        "short": True, "warning": "CAPPED"}):
            self.assertTrue(llm_writer_supervision.memory()["arena"]["short"])

        with patch.object(llm_writer_supervision, "_ours", return_value=STRATA), \
             patch.object(llm_writer_supervision, "_state", return_value="loaded"), \
             patch.object(llm_writer_supervision, "arena_forecast",
                          return_value={"known": True, "free_gib": 14.7, "arena_gib": 10.45,
                                        "short": True, "warning": "CAPPED"}):
            answer = llm_writer_supervision.memory()

        self.assertFalse(answer["arena"]["short"])
        self.assertEqual(answer["arena"]["warning"], "")
        self.assertAlmostEqual(answer["arena"]["free_gib"], 14.7, places=2)


if __name__ == "__main__":
    unittest.main()
