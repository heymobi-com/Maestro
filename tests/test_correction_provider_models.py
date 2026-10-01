"""The correction model list must belong to the correction's provider.

Why this file exists. `/api/v1/llm/models` answers for the *configured* provider by
default, which is the pipeline's. The correction control used to reuse the list the
panel had loaded, so with the pipeline on a local model the dropdown held the six
local catalog entries and nothing else: a hosted model could only be chosen by first
switching the main LLM to that host -- which is the coupling the editing stage exists
to remove.

Measured against the live endpoint:
    ?provider=local  -> 6 models, 0 hosted
    ?provider=remote -> 8 models, of which deepseek-flash and deepseek-v4-pro

So the capability was already there and the request was simply addressed to the wrong
provider. The pins below keep it addressed to the correction's own one, and keep the
stored model visible while it does.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "ui" / "src" / "components" / "SettingsDrawer" / "CorrectionLlmSettings.tsx"
FETCH = ROOT / "ui" / "src" / "api" / "correctionModels.ts"
LAUNCH = ROOT / "app" / "launch.py"
SERVICE = ROOT / "app" / "services" / "llm_service.py"

MODELS_ROUTE = '@api.get("/api/v1/llm/models")'
PROVIDER_PARAM = "provider: str ="


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class CorrectionModelListTest(unittest.TestCase):
    """One endpoint, asked about the provider that will answer."""

    def setUp(self) -> None:
        self.settings = _read(SETTINGS)
        self.fetch = _read(FETCH)
        self.launch = _read(LAUNCH)
        self.service = _read(SERVICE)

    def test_the_request_is_scoped_to_a_provider(self):
        """The call carries the provider, so the server can answer for that one."""
        self.assertIn("/api/v1/llm/models?provider=", self.fetch)
        self.assertIn("encodeURIComponent(provider)", self.fetch)

    def test_the_provider_asked_about_is_the_corrections(self):
        """It is read from the correction's setting, never the pipeline's."""
        self.assertIn("servicesConfig.revision_llm_provider", self.settings)
        self.assertIn("fetchCorrectionModels(provider)", self.settings)
        self.assertNotIn(
            "fetchCorrectionModels(pipelineProvider)",
            self.settings,
            "asking about the pipeline's provider is the coupling this decouples",
        )
        # Spelled with its updateConfig prefix on purpose: a bare "llm_provider:" also
        # matches inside "revision_llm_provider:", which is the setting this block owns.
        self.assertNotIn(
            "updateConfig({ llm_provider:",
            self.settings,
            "the correction block must not write the pipeline's provider",
        )

    def test_the_list_is_not_the_shared_one(self):
        """The panel's provider-scoped list is not reused for the second choice."""
        self.assertIn("const offered = hosted ? endpointModels", self.settings)
        self.assertIn("llmModels.filter(m => modelProvider(m) === 'local')", self.settings)
        self.assertNotIn("fetchLlmModels", self.settings)

    def test_a_stored_model_stays_visible(self):
        """A value the endpoint does not list still shows, instead of reading as unset.

        This is the state the report came from: `deepseek-flash` was saved while the
        dropdown was showing local models only.
        """
        self.assertIn("size_hint: 'saved'", self.settings)
        self.assertIn("!offered.some(option => option.id === model)", self.settings)

    def test_the_endpoint_honours_a_provider_override(self):
        """The backend side of the same contract, so the UI cannot outrun it."""
        route = self.launch.index(MODELS_ROUTE)
        handler = self.launch[route : route + 700]
        self.assertIn(PROVIDER_PARAM, handler)
        self.assertIn('provider or services.get("llm_provider", "local")', handler)
        self.assertIn('if provider in ("remote", "openai") and remote_url:', self.service)
        self.assertIn('if provider == "anthropic" and api_key:', self.service)


if __name__ == "__main__":
    unittest.main()
