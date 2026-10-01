"""Mature mode tells the user when its prompts leave the machine.

The warning rests on one question -- is this endpoint on this machine? -- and it
cannot be answered from the provider name: ``remote`` is LM Studio on loopback in
one install and a hosted gateway in another. Nothing here switches providers, so
this classification is the whole feature: it decides whether the settings screen
warns that mature content is going to a third party.
"""

import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.llm_privacy import (  # noqa: E402
    endpoint_host,
    is_local_endpoint,
    mature_endpoint_is_public,
    mature_mode_enabled,
    routes_off_machine,
)

REPO = Path(_APP_DIR).parent
DEEPSEEK = "https://api.deepseek.com/v1/"


class EndpointClassification(unittest.TestCase):
    def test_this_machine_is_local(self):
        for url in (
            "http://127.0.0.1:1234/v1",
            "http://localhost:11434",
            "http://[::1]:8080",
            "http://192.168.1.50:8080",
            "http://10.0.0.7:5000",
            "http://172.16.4.4:8080",
        ):
            with self.subTest(url=url):
                self.assertTrue(is_local_endpoint(url))

    def test_a_single_label_name_is_not_a_public_host(self):
        # mDNS or a container alias: it cannot be somebody else's service.
        self.assertTrue(is_local_endpoint("http://ollama:11434"))
        self.assertFalse(is_local_endpoint("https://ollama.example.com"))

    def test_a_hosted_endpoint_is_not_local(self):
        for url in (DEEPSEEK, "https://api.openai.com", "https://openrouter.ai/api"):
            with self.subTest(url=url):
                self.assertFalse(is_local_endpoint(url))

    def test_an_empty_or_broken_url_counts_as_public(self):
        # The direction that warns rather than stays quiet.
        for url in ("", None, "   "):
            with self.subTest(url=url):
                self.assertFalse(is_local_endpoint(url))

    def test_host_parsing_survives_userinfo_port_and_scheme(self):
        self.assertEqual("127.0.0.1", endpoint_host("http://user:pw@127.0.0.1:9/v1"))
        self.assertEqual("api.deepseek.com", endpoint_host("https://api.deepseek.com/v1/"))
        # The port is dropped, so a bare "host:port" still classifies as a name.
        self.assertEqual("host", endpoint_host("host:1234"))
        self.assertTrue(is_local_endpoint("host:1234"))
        self.assertFalse(is_local_endpoint("host.example.com:1234"))


class ProviderRouting(unittest.TestCase):
    def test_hosted_providers_always_leave_the_machine(self):
        for provider in ("openai", "anthropic", "OPENAI"):
            with self.subTest(provider=provider):
                self.assertTrue(routes_off_machine(provider, "http://127.0.0.1:1234"))

    def test_remote_is_decided_by_where_it_points(self):
        self.assertTrue(routes_off_machine("remote", DEEPSEEK))
        self.assertFalse(routes_off_machine("remote", "http://127.0.0.1:1234/v1"))

    def test_local_never_leaves(self):
        self.assertFalse(routes_off_machine("local", DEEPSEEK))

    def test_mature_mode_reads_a_settings_mapping(self):
        self.assertTrue(mature_mode_enabled({"nsfw_mode": True}))
        self.assertFalse(mature_mode_enabled({"nsfw_mode": False}))
        self.assertFalse(mature_mode_enabled(None))


class TheWarningCondition(unittest.TestCase):
    """Only the combination earns a warning."""

    def test_mature_mode_with_a_hosted_endpoint_warns(self):
        self.assertTrue(mature_endpoint_is_public({
            "nsfw_mode": True, "llm_provider": "remote", "llm_remote_url": DEEPSEEK,
        }))
        self.assertTrue(mature_endpoint_is_public({
            "nsfw_mode": True, "llm_provider": "openai",
        }))

    def test_mature_mode_with_a_local_endpoint_does_not_warn(self):
        for settings in (
            {"nsfw_mode": True, "llm_provider": "local"},
            {"nsfw_mode": True, "llm_provider": "remote",
             "llm_remote_url": "http://127.0.0.1:1234/v1"},
            {"nsfw_mode": True, "llm_provider": "remote",
             "llm_remote_url": "http://192.168.1.50:8080"},
        ):
            with self.subTest(settings=settings):
                self.assertFalse(mature_endpoint_is_public(settings))

    def test_an_api_without_mature_mode_does_not_warn(self):
        self.assertFalse(mature_endpoint_is_public({
            "nsfw_mode": False, "llm_provider": "remote", "llm_remote_url": DEEPSEEK,
        }))

    def test_missing_settings_never_warn(self):
        self.assertFalse(mature_endpoint_is_public(None))
        self.assertFalse(mature_endpoint_is_public({}))


class NothingIsSwitchedBehindTheUsersBack(unittest.TestCase):
    """The deliberate non-behaviour: the provider the user chose is what runs.

    Downgrading the provider to satisfy mature mode fought every rule keyed on the
    provider, which is why this reports instead.
    """

    def test_loading_a_model_keeps_the_configured_provider(self):
        from services import director_pipeline, llm_service

        wgp = types.SimpleNamespace(server_config={"services": {
            "nsfw_mode": True,
            "llm_provider": "remote",
            "llm_remote_url": DEEPSEEK,
            "llm_model_id": "deepseek-chat",
            "llm_device": "cpu",
        }})
        with patch.object(director_pipeline, "_wgp", wgp), \
             patch.object(llm_service, "is_loaded", return_value=False), \
             patch.object(llm_service, "load_model") as load:
            director_pipeline._ensure_llm_loaded({})
        kwargs = load.call_args.kwargs
        self.assertEqual("remote", kwargs["provider"])
        self.assertEqual(DEEPSEEK, kwargs["remote_url"])
        self.assertEqual("deepseek-chat", kwargs["model_id"])


class TheSettingsEndpointReportsIt(unittest.TestCase):
    """The flag the screen reads, pinned where it is computed."""

    def test_the_key_is_computed_from_the_shared_classification(self):
        import re

        source = (REPO / "app" / "launch.py").read_text(encoding="utf-8")
        self.assertIn('"nsfw_public_endpoint": nsfw_public_endpoint', source)
        self.assertTrue(
            re.search(r"nsfw_public_endpoint = nsfw and routes_off_machine\(", source),
            "the warning flag must come from the shared classification",
        )


class TheScreenUsesIt(unittest.TestCase):
    def test_the_panel_renders_the_alert_from_the_flag(self):
        panel = (
            REPO / "ui" / "src" / "components" / "SettingsDrawer"
            / "ServicesSettingsPanel.tsx"
        ).read_text(encoding="utf-8")
        self.assertIn("servicesConfig.nsfw_public_endpoint", panel)
        self.assertIn('role="alert"', panel)
        # The wording has to say where the content goes and what to do about it.
        for phrase in (
            "public service",
            "over the internet",
            "keep this content at home",
        ):
            self.assertIn(phrase, panel)


if __name__ == "__main__":
    unittest.main()
