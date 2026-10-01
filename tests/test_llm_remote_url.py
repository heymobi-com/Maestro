"""Every shape of a pasted remote URL resolves to the same endpoint.

The reported symptom was "I entered the DeepSeek OpenAI-compatible API and Maestro
does not seem to pick it up". It was not the key and it was not a missing feature:
the field is a *base*, so an entry that already ended in ``/v1`` -- which is what
DeepSeek's own documentation shows -- asked for ``/v1/v1/chat/completions``.

These tests pin the convention, the exact URL actually called, and the fact that a
failure now names the URL it tried instead of the one that was typed.
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout


_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services import llm_service  # noqa: E402
from services.llm_endpoint import chat_url, models_url, remote_base  # noqa: E402

DEEPSEEK = "https://api.deepseek.com"
CORRECT_CHAT = "https://api.deepseek.com/v1/chat/completions"
CORRECT_MODELS = "https://api.deepseek.com/v1/models"


class RemoteBaseTests(unittest.TestCase):
    def test_every_shape_a_user_can_paste_resolves_the_same(self):
        for entered in (
            DEEPSEEK,
            "https://api.deepseek.com/",
            "https://api.deepseek.com/v1",
            "https://api.deepseek.com/v1/",
            "  https://api.deepseek.com/v1/  ",
            "https://api.deepseek.com/chat/completions",
            "https://api.deepseek.com/v1/chat/completions",
        ):
            with self.subTest(entered=entered):
                self.assertEqual(DEEPSEEK, remote_base(entered))
                self.assertEqual(CORRECT_CHAT, chat_url(entered))
                self.assertEqual(CORRECT_MODELS, models_url(entered))

    def test_a_self_hosted_server_keeps_its_port_and_path(self):
        for entered, base in (
            ("http://192.168.1.100:1234/v1", "http://192.168.1.100:1234"),
            ("http://localhost:11434/v1/", "http://localhost:11434"),
            ("http://127.0.0.1:8080/proxy/llm", "http://127.0.0.1:8080/proxy/llm"),
        ):
            with self.subTest(entered=entered):
                self.assertEqual(base, remote_base(entered))
                self.assertEqual(f"{base}/v1/chat/completions", chat_url(entered))

    def test_an_empty_value_is_not_a_url(self):
        self.assertEqual("", remote_base(""))
        self.assertEqual("", remote_base(None))


class ServerUrlTests(unittest.TestCase):
    """The base the request layer really uses, not just the helper in isolation."""

    def setUp(self):
        self.saved = (
            llm_service._provider, llm_service._remote_url, llm_service._server_port,
        )

    def tearDown(self):
        (
            llm_service._provider, llm_service._remote_url, llm_service._server_port,
        ) = self.saved

    def test_the_reported_entry_now_calls_the_correct_endpoint(self):
        llm_service._provider = "remote"
        llm_service._remote_url = "https://api.deepseek.com/v1/"
        self.assertEqual(DEEPSEEK, llm_service._server_url())
        self.assertEqual(
            CORRECT_CHAT, f"{llm_service._server_url()}/v1/chat/completions"
        )

    def test_local_is_untouched(self):
        llm_service._provider = "local"
        llm_service._remote_url = "https://api.deepseek.com/v1/"
        llm_service._server_port = 12345
        self.assertEqual("http://127.0.0.1:12345", llm_service._server_url())

    def test_a_remote_provider_with_no_url_still_falls_back_to_loopback(self):
        llm_service._provider = "remote"
        llm_service._remote_url = ""
        llm_service._server_port = 12345
        self.assertEqual("http://127.0.0.1:12345", llm_service._server_url())


class FailureMessageTests(unittest.TestCase):
    def test_a_failed_query_names_the_url_it_tried(self):
        # Port 9 is the discard port: refused immediately, so this needs no network.
        captured = io.StringIO()
        with redirect_stdout(captured):
            models = llm_service.get_available_models(
                provider="remote",
                remote_url="http://127.0.0.1:9/v1/",
                api_key="",
            )
        output = captured.getvalue()
        self.assertIn("http://127.0.0.1:9/v1/models", output)
        self.assertNotIn("http://127.0.0.1:9/v1/v1/", output)
        # A failed remote lookup must still leave the local catalog usable.
        self.assertTrue(models)


if __name__ == "__main__":
    unittest.main()
