"""An honest answer about a remote writer, the message when it is gone, and the run's own address.

Reported from use: a Director run bound to a local writer that had been stopped still showed as
loaded, and the phase died mid-planning with a connection error. The run also had no address of its
own, so it followed whatever the settings said at the time.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import requests  # noqa: E402

from services import llm_service  # noqa: E402
from services import llm_writer_binding  # noqa: E402
from services import llm_writer_health as health  # noqa: E402

LOCAL = "http://127.0.0.1:8081"


class FakeResponse:
    def __init__(self, ok=True, status=200):
        self.ok = ok
        self.status_code = status

    def json(self):
        return {"data": []}


class LocalAddressTests(unittest.TestCase):
    def test_this_machine_and_the_lan_are_local(self):
        for url in ("http://127.0.0.1:8081", "http://localhost:1234", "http://[::1]:8081",
                    "http://192.168.1.50:8081", "http://10.0.0.5:8081", "http://172.16.4.2:8081"):
            with self.subTest(url=url):
                self.assertTrue(health.is_local_endpoint(url))

    def test_a_hosted_or_named_endpoint_is_not(self):
        for url in ("https://api.deepseek.com", "https://api.openai.com/v1", "http://strata.local:8081", ""):
            with self.subTest(url=url):
                self.assertFalse(health.is_local_endpoint(url))


class AnswersTests(unittest.TestCase):
    def setUp(self):
        health.forget()

    def tearDown(self):
        health.forget()

    def test_a_provider_that_is_not_remote_has_its_own_answer_elsewhere(self):
        for provider in ("local", ""):
            with self.subTest(provider=provider):
                self.assertFalse(health.answers(provider, LOCAL, "a-model"))

    def test_an_api_provider_without_an_address_is_taken_at_its_word(self):
        # Anthropic has no base URL to probe: the same rule as a hosted endpoint.
        self.assertTrue(health.answers("anthropic", "", "claude-sonnet-4-6"))

    def test_a_remote_writer_without_a_model_is_not_loaded(self):
        self.assertFalse(health.answers("remote", LOCAL, ""))

    def test_a_local_writer_is_probed(self):
        with patch("requests.get", return_value=FakeResponse()) as get:
            self.assertTrue(health.answers("remote", LOCAL, "m"))
        self.assertEqual(get.call_args.args[0], "http://127.0.0.1:8081/v1/models")

    def test_a_local_writer_that_is_gone_says_so(self):
        with patch("requests.get", return_value=FakeResponse(ok=False, status=503)):
            self.assertFalse(health.answers("remote", LOCAL, "m"))
        with patch("requests.get", side_effect=requests.exceptions.ConnectionError("refused")):
            health.forget()
            self.assertFalse(health.answers("remote", LOCAL, "m"))

    def test_a_hosted_writer_is_taken_at_its_word_and_never_probed(self):
        with patch("requests.get") as get:
            self.assertTrue(health.answers("openai", "https://api.deepseek.com", "deepseek-chat"))
        self.assertFalse(get.called)

    def test_the_answer_is_reused_for_a_few_seconds(self):
        with patch("requests.get", return_value=FakeResponse()) as get:
            health.answers("remote", LOCAL, "m")
            health.answers("remote", LOCAL, "m")
        self.assertEqual(get.call_count, 1)

    def test_the_answer_expires(self):
        with patch("services.llm_writer_health.time.monotonic", return_value=0.0), \
                patch("requests.get", return_value=FakeResponse(ok=False, status=503)) as get:
            self.assertFalse(health.answers("remote", LOCAL, "m"))
        with patch("services.llm_writer_health.time.monotonic", return_value=health._TTL + 1), \
                patch("requests.get", return_value=FakeResponse()) as later:
            self.assertTrue(health.answers("remote", LOCAL, "m"))
            self.assertTrue(later.called, "past the TTL the server is asked again")
        self.assertEqual(get.call_count, 1)


class NotLoadedMessageTests(unittest.TestCase):
    """The engine's own guard: a stopped local writer is not a missing load_model call."""

    def test_a_stopped_local_writer_is_named(self):
        message = health.not_loaded_message("remote", LOCAL)
        self.assertIn(LOCAL, message)
        self.assertIn("not answering", message)

    def test_everything_else_keeps_the_original_wording(self):
        for provider, url in (("local", LOCAL), ("remote", "https://api.deepseek.com"),
                              ("remote", "http://strata.local:8081"), ("remote", "")):
            with self.subTest(provider=provider, url=url):
                self.assertEqual(
                    health.not_loaded_message(provider, url),
                    "LLM not loaded. Call load_model() first.",
                )


class UnreachableHintTests(unittest.TestCase):
    def setUp(self):
        health.forget()

    def tearDown(self):
        health.forget()

    def test_a_lost_local_writer_names_itself(self):
        hint = health.unreachable_hint("remote", LOCAL, requests.exceptions.ConnectionError("refused"))
        self.assertIn(LOCAL, hint)
        self.assertIn("not answering", hint)

    def test_a_timeout_counts_too(self):
        self.assertTrue(health.unreachable_hint("remote", LOCAL, requests.exceptions.ReadTimeout("slow")))

    def test_a_status_code_means_the_server_did_answer(self):
        response = requests.Response()
        response.status_code = 400
        error = requests.exceptions.HTTPError("400 Client Error", response=response)
        self.assertEqual(health.unreachable_hint("remote", LOCAL, error), "")

    def test_nothing_is_claimed_for_other_cases(self):
        for provider, url, exc in (
            ("local", LOCAL, requests.exceptions.ConnectionError("x")),
            ("openai", "https://api.deepseek.com", requests.exceptions.ConnectionError("x")),
            ("remote", "", requests.exceptions.ConnectionError("x")),
            ("remote", "http://strata.local:8081", requests.exceptions.ConnectionError("x")),
            ("remote", LOCAL, ValueError("not a request failure")),
        ):
            with self.subTest(provider=provider, url=url):
                self.assertEqual(health.unreachable_hint(provider, url, exc), "")

    def test_the_stale_answer_is_dropped_so_the_next_reading_is_fresh(self):
        with patch("requests.get", return_value=FakeResponse()):
            self.assertTrue(health.answers("remote", LOCAL, "m"))
        health.unreachable_hint("remote", LOCAL, requests.exceptions.ConnectionError("refused"))
        with patch("requests.get", return_value=FakeResponse(ok=False, status=503)) as get:
            self.assertFalse(health.answers("remote", LOCAL, "m"))
        self.assertTrue(get.called)


class EngineStatusTests(unittest.TestCase):
    """The engine's own answer, with the seam in place."""

    def setUp(self):
        health.forget()
        self.saved = (llm_service._provider, llm_service._model_id, llm_service._remote_url)

    def tearDown(self):
        health.forget()
        llm_service._provider, llm_service._model_id, llm_service._remote_url = self.saved

    def test_a_stopped_local_writer_is_not_reported_as_loaded(self):
        llm_service._provider, llm_service._model_id, llm_service._remote_url = "remote", "m", LOCAL
        with patch("requests.get", side_effect=requests.exceptions.ConnectionError("refused")):
            self.assertFalse(llm_service.is_loaded())
            self.assertFalse(llm_service.get_status()["loaded"])

    def test_a_running_local_writer_is_reported_as_loaded(self):
        llm_service._provider, llm_service._model_id, llm_service._remote_url = "remote", "m", LOCAL
        with patch("requests.get", return_value=FakeResponse()):
            self.assertTrue(llm_service.is_loaded())

    def test_a_hosted_writer_is_unaffected(self):
        llm_service._provider = "openai"
        llm_service._model_id, llm_service._remote_url = "deepseek-chat", ""
        with patch("requests.get") as get:
            self.assertTrue(llm_service.is_loaded())
        self.assertFalse(get.called)


class WriterBindingTests(unittest.TestCase):
    def test_a_run_keeps_the_address_it_started_with(self):
        params = {"llm_remote_url": "http://127.0.0.1:8081"}
        moved = {"llm_remote_url": "http://127.0.0.1:9090"}
        self.assertEqual(llm_writer_binding.remote_url(params, moved), "http://127.0.0.1:8081")

    def test_the_first_phase_records_the_address_in_the_run(self):
        params = {}
        services = {"llm_remote_url": "http://127.0.0.1:8081"}
        self.assertEqual(llm_writer_binding.remote_url(params, services), "http://127.0.0.1:8081")
        self.assertEqual(params["llm_remote_url"], "http://127.0.0.1:8081")
        # From now on the run's own record wins, whatever the settings say.
        services["llm_remote_url"] = "http://127.0.0.1:9090"
        self.assertEqual(llm_writer_binding.remote_url(params, services), "http://127.0.0.1:8081")

    def test_a_run_with_nothing_configured_stays_empty(self):
        params = {}
        self.assertEqual(llm_writer_binding.remote_url(params, {}), "")
        self.assertNotIn("llm_remote_url", params)

    def test_the_module_survives_the_shapes_the_pipeline_can_hand_it(self):
        self.assertEqual(llm_writer_binding.remote_url(None, None), "")
        self.assertEqual(llm_writer_binding.remote_url(None, {"llm_remote_url": "http://x:1"}), "http://x:1")


class PipelineSeamTests(unittest.TestCase):
    """The pipeline must ask for the run's address, not the setting."""

    def test_the_writer_loader_reads_the_run_binding(self):
        source = (ROOT / "app" / "services" / "director_pipeline.py").read_text(encoding="utf-8")
        self.assertIn("from services.llm_writer_binding import remote_url as bound_remote_url", source)
        self.assertIn("desired_remote_url = bound_remote_url(params, services_cfg)", source)
        self.assertNotIn('desired_remote_url = services_cfg.get("llm_remote_url", "")', source)


if __name__ == "__main__":
    unittest.main()
