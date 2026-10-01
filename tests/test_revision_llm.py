"""The editing stage can run on its own endpoint, leaving the pipeline alone.

The point is not that the API is faster on its own -- it is that a correction must
not disturb the pipeline. Through `llm_service`, editing a hosted endpoint unloads
the pipeline's local model, and a local editing model cannot come up while the video
model holds the GPU. With an endpoint of its own, editing touches neither.
"""

from __future__ import annotations

import json
import os
import sys
import types
import unittest
from unittest.mock import patch


_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services import llm_service, revision_llm  # noqa: E402

DEEPSEEK = "https://api.deepseek.com"
HOSTED = {
    "revision_llm_provider": "remote",
    "revision_llm_model_id": "deepseek-flash",
    "llm_remote_url": DEEPSEEK,
    "llm_provider": "local",
    "llm_model_id": "Abhiray/gemma-4-E4B-it-heretic-GGUF",
}


class WhichEndpointTheEditingStageUses(unittest.TestCase):
    def test_no_setting_follows_the_pipeline(self):
        # The default has to be today's behaviour.
        self.assertEqual({}, revision_llm.editing_endpoint({}))
        self.assertEqual({}, revision_llm.editing_endpoint({"revision_llm_provider": ""}))
        self.assertEqual({}, revision_llm.editing_endpoint({"revision_llm_provider": "local"}))

    def test_a_hosted_choice_separates_editing_from_the_pipeline(self):
        endpoint = revision_llm.editing_endpoint(HOSTED)
        self.assertEqual("remote", endpoint["provider"])
        self.assertEqual("deepseek-flash", endpoint["model"])
        self.assertEqual(DEEPSEEK, endpoint["base_url"])
        # The pipeline stays local; only editing moved.
        self.assertEqual("local", HOSTED["llm_provider"])

    def test_the_key_belongs_to_the_editing_provider(self):
        services = dict(HOSTED, llm_remote_api_key="secret-for-the-endpoint")
        self.assertEqual(
            "secret-for-the-endpoint", revision_llm.editing_endpoint(services)["api_key"]
        )

    def test_the_main_model_is_the_fallback_when_no_editing_model_is_named(self):
        services = {k: v for k, v in HOSTED.items() if k != "revision_llm_model_id"}
        services["llm_model_id"] = "deepseek-flash"
        self.assertEqual("deepseek-flash", revision_llm.editing_endpoint(services)["model"])

    def test_an_incomplete_choice_falls_back_rather_than_failing_later(self):
        # A hosted choice with no URL, or with no model at all, cannot work: it is
        # reported as "no endpoint", which keeps the old path rather than a 500.
        self.assertEqual({}, revision_llm.editing_endpoint({
            "revision_llm_provider": "remote", "revision_llm_model_id": "deepseek-flash",
        }))
        self.assertEqual({}, revision_llm.editing_endpoint({
            "revision_llm_provider": "remote", "llm_remote_url": DEEPSEEK,
        }))

    def test_describe_names_what_will_be_used(self):
        endpoint = revision_llm.editing_endpoint(HOSTED)
        self.assertIn("deepseek-flash", revision_llm.describe(endpoint))
        self.assertIn("api.deepseek.com", revision_llm.describe(endpoint))
        self.assertEqual("the pipeline's LLM", revision_llm.describe({}))


class TheRequestItSends(unittest.TestCase):
    def _capture(self, *, ok=True, body=None, status=200):
        captured = {}

        class Response:
            def __init__(self):
                self.ok = ok
                self.status_code = status
                self.text = "refused"

            def json(self):
                return body if body is not None else {
                    "choices": [{"message": {"content": "ANALYSIS: none\nFIXED_PROMPT: x"}}]
                }

        def post(url, json=None, headers=None, timeout=None):
            captured.update(url=url, payload=json, headers=headers, timeout=timeout)
            return Response()

        return captured, patch("requests.post", post)

    def test_it_posts_an_openai_chat_completion(self):
        from services.director.nsfw_guidance import inject_content_guidance

        captured, poster = self._capture()
        with poster:
            answer = revision_llm.complete(
                "SYSTEM", "USER", endpoint=revision_llm.editing_endpoint(HOSTED),
            )
        self.assertEqual(f"{DEEPSEEK}/v1/chat/completions", captured["url"])
        self.assertEqual("deepseek-flash", captured["payload"]["model"])
        self.assertEqual(False, captured["payload"]["stream"])
        # Parity with the pipeline path: the safe-mode guardrails are injected even
        # when mature mode is off, because that is what enhance_prompt does.
        self.assertEqual(
            inject_content_guidance("SYSTEM", False, "enhance"),
            captured["payload"]["messages"][0]["content"],
        )
        self.assertEqual("USER", captured["payload"]["messages"][1]["content"])
        self.assertIn("ANALYSIS", answer)

    def test_a_hosted_endpoint_of_its_own_needs_no_local_server(self):
        """The whole requirement: no model load, no unload, no GPU."""

        captured, poster = self._capture()
        with poster, \
             patch.object(llm_service, "load_model") as load, \
             patch.object(llm_service, "unload_model") as unload:
            revision_llm.complete(
                "SYSTEM", "USER", endpoint=revision_llm.editing_endpoint(HOSTED),
            )
        self.assertFalse(load.called, "the pipeline's model must not be loaded")
        self.assertFalse(unload.called, "the pipeline's model must not be unloaded")
        self.assertTrue(captured["url"].startswith(DEEPSEEK))

    def test_the_content_guidance_is_injected_like_the_pipeline_path(self):
        from services.director.nsfw_guidance import inject_content_guidance

        captured, poster = self._capture()
        with poster:
            revision_llm.complete(
                "SYSTEM", "USER", endpoint=revision_llm.editing_endpoint(HOSTED),
                nsfw=True,
            )
        system = captured["payload"]["messages"][0]["content"]
        self.assertEqual(inject_content_guidance("SYSTEM", True, "enhance"), system)

    def test_a_refusal_names_the_endpoint_and_the_provider_words(self):
        captured, poster = self._capture(ok=False, status=400)
        with poster:
            with self.assertRaises(RuntimeError) as caught:
                revision_llm.complete(
                    "SYSTEM", "USER", endpoint=revision_llm.editing_endpoint(HOSTED),
                )
        message = str(caught.exception)
        self.assertIn("400", message)
        self.assertIn(DEEPSEEK, message)

    def _empty(self, body):
        captured, poster = self._capture(body=body)
        with poster:
            with self.assertRaises(RuntimeError) as caught:
                revision_llm.complete(
                    "SYSTEM", "USER", endpoint=revision_llm.editing_endpoint(HOSTED),
                )
        return str(caught.exception)

    def test_an_empty_answer_says_the_budget_ran_out(self):
        """A reasoning model that thinks past max_tokens sends content back empty."""
        message = self._empty({
            "choices": [{
                "message": {"role": "assistant", "content": "", "reasoning_content": "thinking..."},
                "finish_reason": "length",
            }],
            "usage": {"completion_tokens": 4096, "completion_tokens_details": {"reasoning_tokens": 4096}},
        })
        self.assertIn("empty answer", message)
        self.assertIn("deepseek-flash", message)
        self.assertIn("finish_reason=length", message)
        self.assertIn("reasoning_tokens=4096", message)
        self.assertIn("raise max_tokens", message)

    def test_an_empty_answer_after_reasoning_reads_as_a_decline(self):
        """The shape a provider refusing the content leaves: reasoning, no answer, stop."""
        message = self._empty({
            "choices": [{
                "message": {"role": "assistant", "content": "", "reasoning_content": "I can't help"},
                "finish_reason": "stop",
            }],
            "usage": {"completion_tokens": 25},
        })
        self.assertIn("finish_reason=stop", message)
        self.assertIn("declining the content", message)
        self.assertNotIn("raise max_tokens", message)

    def test_an_empty_answer_with_nothing_reported_still_says_so(self):
        message = self._empty({"choices": [{"message": {"content": ""}}]})
        self.assertIn("no finish_reason or usage reported", message)

    def test_an_endpoint_without_a_url_says_so(self):
        with self.assertRaises(ValueError) as caught:
            revision_llm.complete(
                "S", "U", endpoint={"provider": "openai", "model": "gpt-4o"},
            )
        self.assertIn("needs an endpoint", str(caught.exception))


class ThePipelineModelIsLeftAlone(unittest.TestCase):
    """End to end through the correction pass, which is what the user sees."""

    def _revise(self, live_settings):
        import tempfile

        from services import director_pipeline as pipeline

        state = {
            "pipeline_id": "editor01",
            "status": "completed",
            "video_model": "minimax_h3_ref2va_fused_turbo",
            "clips": [{"index": 0, "video_prompt": "a shot body"}],
            "_params_snapshot": {"scene_description": "PROJECT: a studio"},
        }
        with tempfile.TemporaryDirectory() as out:
            with open(
                os.path.join(out, "_director_pipeline_editor01.json"), "w",
                encoding="utf-8",
            ) as handle:
                json.dump(state, handle)
            wgp = types.SimpleNamespace(server_config={"services": live_settings})
            captured = {}

            def post(url, json=None, headers=None, timeout=None):
                captured["url"] = url
                captured["payload"] = json

                class Response:
                    ok = True
                    status_code = 200

                    def json(self):
                        return {"choices": [{"message": {"content": "ANALYSIS: fine"}}]}

                return Response()

            loader = []
            with patch.object(pipeline, "_wgp", wgp), \
                 patch.object(pipeline, "_ensure_llm_loaded",
                              lambda params: loader.append(params)), \
                 patch("requests.post", post):
                pipeline.revise_clip_prompt(out, "editor01", 0, "fix it")
        return captured, loader

    def test_a_correction_with_its_own_endpoint_never_loads_the_pipeline_model(self):
        captured, loader = self._revise(HOSTED)
        self.assertEqual([], loader, "the pipeline's LLM must not be loaded")
        self.assertIn("/v1/chat/completions", captured["url"])

    def test_without_the_setting_nothing_changes(self):
        import tempfile

        from services import director_pipeline as pipeline

        state = {
            "pipeline_id": "editor01",
            "status": "completed",
            "video_model": "minimax_h3_ref2va_fused_turbo",
            "clips": [{"index": 0, "video_prompt": "a shot body"}],
            "_params_snapshot": {"scene_description": "PROJECT: a studio"},
        }
        with tempfile.TemporaryDirectory() as out:
            with open(
                os.path.join(out, "_director_pipeline_editor01.json"), "w",
                encoding="utf-8",
            ) as handle:
                json.dump(state, handle)
            loader = []
            with patch.object(
                pipeline, "_wgp",
                types.SimpleNamespace(server_config={"services": {
                    "llm_provider": "local",
                    "llm_model_id": "Abhiray/gemma-4-E4B-it-heretic-GGUF",
                }}),
            ), patch.object(
                pipeline, "_ensure_llm_loaded", lambda params: loader.append(params),
            ), patch(
                "services.llm_service.enhance_prompt",
                lambda **kw: "ANALYSIS: fine",
            ), patch("requests.post") as posted:
                pipeline.revise_clip_prompt(out, "editor01", 0, "fix it")
        self.assertEqual(1, len(loader), "the old path must still load a model")
        self.assertFalse(posted.called, "and must not call a hosted endpoint")


if __name__ == "__main__":
    unittest.main()
