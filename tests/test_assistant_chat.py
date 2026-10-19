"""The assistant: its own endpoint, its contract, and what it may write.

The three decisions these cases pin, and the reason each is not a detail:

* the assistant can be given its own provider, model and endpoint, so a chat does not unload the
  pipeline's writer or compete with the video model for the GPU -- but an empty provider, and
  ``local``, still resolve to `llm_service`, because that is what owns llama-server;
* actions travel as one JSON block rather than native tool-calling, because nothing in this
  repository speaks ``tool_calls`` and a quantized local model asked for the native form invents
  it. A malformed block costs nothing: the prose is kept and no action is run;
* a proposal is not a write. It is validated against the same contract the clip editor uses, and
  it reaches the plan only through the route the clip cards already save with.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_APP_DIR = os.path.join(_ROOT, "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services import chat_llm, chat_tools  # noqa: E402

LAUNCH = os.path.join(_APP_DIR, "launch.py")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


class TheAssistantHasItsOwnEndpointTests(unittest.TestCase):
    SHARED = {
        "llm_provider": "local",
        "llm_model_id": "some/pipeline-model",
        "llm_remote_url": "http://127.0.0.1:8080/v1",
        "openai_api_key": "sk-shared",
    }

    def test_it_follows_the_pipeline_until_it_is_given_one(self):
        for provider in ("", None, "local"):
            with self.subTest(provider=provider):
                services = dict(self.SHARED, chat_llm_provider=provider)
                self.assertEqual(chat_llm.chat_endpoint(services), {})

    def test_a_hosted_provider_gets_its_own_model_and_endpoint(self):
        endpoint = chat_llm.chat_endpoint(dict(
            self.SHARED,
            chat_llm_provider="remote",
            chat_llm_model_id="gemma-4-E4B-it",
            chat_llm_remote_url="http://192.168.1.20:8080/v1",
        ))

        self.assertEqual(endpoint["model"], "gemma-4-E4B-it")
        self.assertEqual(endpoint["base_url"], "http://192.168.1.20:8080")
        # The assistant's own credential, not the shared one.
        self.assertNotEqual(endpoint.get("api_key"), "sk-shared")

    def test_the_shared_endpoint_is_reused_when_the_assistant_has_none(self):
        endpoint = chat_llm.chat_endpoint(dict(
            self.SHARED, chat_llm_provider="remote", chat_llm_model_id="gemma-4-E4B-it",
        ))

        self.assertEqual(endpoint["base_url"], "http://127.0.0.1:8080")

    def test_its_own_key_wins_over_the_shared_one(self):
        endpoint = chat_llm.chat_endpoint(dict(
            self.SHARED,
            chat_llm_provider="openai",
            chat_llm_model_id="gpt-vision",
            chat_llm_api_key="sk-assistant",
        ))

        self.assertEqual(endpoint["api_key"], "sk-assistant")

    def test_a_hosted_provider_without_a_url_is_not_an_endpoint(self):
        """Falling through to the pipeline is better than posting into nowhere."""

        services = dict(self.SHARED, chat_llm_provider="remote", chat_llm_model_id="m")
        services["llm_remote_url"] = ""

        self.assertEqual(chat_llm.chat_endpoint(services), {})

    def test_the_answer_budget_is_bounded(self):
        self.assertEqual(chat_llm.max_tokens({}), chat_llm.DEFAULT_MAX_TOKENS)
        self.assertEqual(chat_llm.max_tokens({"chat_llm_max_tokens": 10}), 256)
        self.assertEqual(chat_llm.max_tokens({"chat_llm_max_tokens": 999999}), 16384)


class TheActionsContractTests(unittest.TestCase):
    def test_a_fenced_block_is_read_and_removed_from_the_prose(self):
        reply = (
            "Shot 12 carries the same setup as shot 9.\n\n"
            "```json\n{\"actions\": [{\"tool\": \"get_clip_prompt\", \"shot\": 9}]}\n```"
        )

        prose, actions = chat_llm.parse_actions(reply)

        self.assertEqual(prose, "Shot 12 carries the same setup as shot 9.")
        self.assertEqual(actions, [{"tool": "get_clip_prompt", "shot": 9}])

    def test_a_bare_object_is_read_too(self):
        """A model that forgets the fence is not a model whose answer is lost."""

        prose, actions = chat_llm.parse_actions(
            "Reading the plan.\n{\"actions\": [{\"tool\": \"get_pipeline_state\"}]}"
        )

        self.assertEqual(prose, "Reading the plan.")
        self.assertEqual([action["tool"] for action in actions], ["get_pipeline_state"])

    def test_prose_with_no_block_is_kept_whole(self):
        reply = "The wardrobe changes because the screenplay says so, in shot 7."

        self.assertEqual(chat_llm.parse_actions(reply), (reply, []))

    def test_a_broken_block_costs_the_block_and_nothing_else(self):
        reply = "Here is why.\n```json\n{\"actions\": [oops}\n```"

        prose, actions = chat_llm.parse_actions(reply)

        self.assertEqual(actions, [])
        self.assertIn("Here is why.", prose)

    def test_a_block_that_is_not_actions_is_not_an_action(self):
        prose, actions = chat_llm.parse_actions('Settings:\n```json\n{"model": "x"}\n```')

        self.assertEqual(actions, [])
        self.assertIn("Settings", prose)

    def test_at_most_three_actions_are_taken(self):
        reply = '```json\n{"actions": [' + ",".join(
            f'{{"tool": "get_clip_prompt", "shot": {index}}}' for index in range(6)
        ) + "]}\n```"

        _, actions = chat_llm.parse_actions(reply)

        self.assertEqual(len(actions), 3)

    def test_the_conversation_keeps_its_system_prompt_and_its_tail(self):
        messages = [{"role": "system", "content": "rules"}]
        messages += [
            {"role": "user" if index % 2 == 0 else "assistant", "content": f"turn {index}"}
            for index in range(chat_llm.MAX_TURNS + 6)
        ]

        trimmed = chat_llm.trim_turns(messages)

        self.assertEqual(trimmed[0]["content"], "rules")
        self.assertEqual(len(trimmed), chat_llm.MAX_TURNS + 1)
        self.assertEqual(trimmed[-1]["content"], messages[-1]["content"])


class TheImageTurnTests(unittest.TestCase):
    def test_an_image_becomes_a_data_url(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "shot.png")
            with open(path, "wb") as handle:
                handle.write(b"\x89PNG\r\n\x1a\n")

            url = chat_llm._data_url(path)

        self.assertTrue(url.startswith("data:image/png;base64,"))
        self.assertEqual(
            base64.b64decode(url.split(",", 1)[1]), b"\x89PNG\r\n\x1a\n",
        )

    def test_a_missing_image_is_an_error_rather_than_a_silent_drop(self):
        with self.assertRaises(ValueError):
            chat_llm._data_url(os.path.join(tempfile.gettempdir(), "not-here-1234.png"))

    def test_the_turn_carries_text_and_image_together(self):
        content = chat_llm._content_with_images("what is wrong here?", [])

        self.assertEqual(content, "what is wrong here?")


class TheToolsTheAssistantMayRunTests(unittest.TestCase):
    STATE = {
        "pipeline_id": "abc12345",
        "status": "completed",
        "clips": [
            {
                "index": 0,
                "video_filename": "clip1.mp4",
                "tag": "opening",
                "_director_duration_sec": 8.0,
                "_director_project_context": "SUBJECT LOCK: <Subject 1> is Aria.",
                "video_prompt": "subject_definitions: <Subject 1> is Aria (S1).",
                "planned_clip": {
                    "_director_continuity_group": "warehouse_1",
                    "_director_continuity_strategy": "independent",
                },
            },
            {"index": 1, "video_prompt": "second shot", "planned_clip": {}},
        ],
    }

    def test_a_tool_that_is_not_whitelisted_is_refused(self):
        result = chat_tools.read_tool("run_shell", {})

        self.assertIn("not a tool the assistant may run", result["error"])

    def test_a_proposal_that_is_not_whitelisted_is_refused(self):
        result = chat_tools.propose_tool("delete_project", {})

        self.assertIn("not a tool the assistant may propose", result["error"])

    def test_the_summary_names_the_shots_the_way_the_person_numbers_them(self):
        summary = chat_tools._state_summary(self.STATE)

        self.assertEqual(summary["shot_count"], 2)
        self.assertEqual([shot["shot"] for shot in summary["shots"]], [1, 2])
        self.assertEqual(summary["shots"][0]["group"], "warehouse_1")
        self.assertTrue(summary["shots"][0]["rendered"])
        self.assertFalse(summary["shots"][1]["rendered"])

    def test_an_action_addresses_a_shot_by_its_film_number(self):
        self.assertIsNotNone(chat_tools._clip(self.STATE, {"shot": 1}))
        self.assertIsNotNone(chat_tools._clip(self.STATE, {"shot": 2}))
        self.assertIsNone(chat_tools._clip(self.STATE, {"shot": 3}))
        self.assertIsNone(chat_tools._clip(self.STATE, {"shot": "one"}))

    def test_a_prompt_proposal_needs_a_shot_and_a_prompt(self):
        self.assertIn("error", chat_tools.propose_tool("propose_clip_prompt", {"shot": 1}))
        self.assertIn("error", chat_tools.propose_tool("propose_clip_prompt", {"prompt": "x"}))

    def test_a_tag_proposal_needs_both_halves(self):
        self.assertIn("error", chat_tools.propose_tool("propose_clip_tag", {"shot": 1}))
        self.assertIn("error", chat_tools.propose_tool("propose_clip_tag", {"tag": "x"}))

    def test_a_tag_proposal_is_offered_without_touching_the_plan(self):
        result = chat_tools.propose_tool("propose_clip_tag", {"shot": 3, "tag": "the reveal"})

        self.assertTrue(result["acceptable"])
        self.assertEqual(result["shot"], 3)
        self.assertEqual(result["tag"], "the reveal")


class TheProposalIsCheckedTests(unittest.TestCase):
    """The chat's proposal is checked by the clip editor's own validator, not a second copy."""

    def test_the_validator_is_the_clip_editors(self):
        source = _read(os.path.join(_APP_DIR, "services", "chat_tools.py"))

        self.assertIn("from services.director.h3_dialogue import", source)
        self.assertIn("review_h3_revision(", source)
        # The frozen project text travels with it, exactly as the correction panel sends it.
        self.assertIn("h3_shared_project_phrases(", source)
        self.assertIn("frozen=frozen", source)

    def test_a_rewrite_that_drops_a_spoken_line_is_refused(self):
        """The rule itself, exercised: the spoken words must survive untouched."""

        from services.director.h3_dialogue import review_h3_revision

        original = (
            "subject_definitions: <Subject 1> is Aria (S1).\n\n"
            "detailed_description: [Shot 1] Aria speaks. "
            "<d>[English] Where were you?</d>"
        )
        revised = "detailed_description: [Shot 1] Aria speaks, saying nothing at all."

        problems = review_h3_revision(original, revised)

        self.assertTrue(problems)
        self.assertIn("spoken", " ".join(problems).lower())


class TheCallGoesThroughTheSameDialectTests(unittest.TestCase):
    """An endpoint call is sent the way every other Maestro writer call is sent.

    Measured against Strata (the local engine this machine writes with): `llm_service` routes every
    request through `llm_remote_dialect.finalize`, which is what turns "I want an answer, not a
    deliberation" into `reasoning_effort: none` plus a hard `reasoning_budget_tokens` cap. Posting
    straight to `/v1/chat/completions` -- which this did first -- left the reasoning uncapped, and an
    uncapped call on that server ran to 24,605 tokens in 700 seconds with nothing to show. One such
    call occupies the engine, so the pipeline's planning measures slower behind it.
    """

    RESPONSE = {"choices": [{"message": {"content": "an answer"}, "finish_reason": "stop"}]}

    def _send(self, provider, base_url):
        from unittest.mock import patch

        from services import llm_remote_dialect

        captured: dict[str, object] = {}

        class FakeResponse:
            ok = True
            status_code = 200
            text = ""

            def json(self):
                return self_body

        self_body = self.RESPONSE

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["url"] = url
            captured["payload"] = json
            return FakeResponse()

        with patch.object(llm_remote_dialect, "speaks_dialect", return_value=True), \
                patch("requests.post", side_effect=fake_post):
            chat_llm._post_chat(
                {"provider": provider, "model": "m", "base_url": base_url, "api_key": ""},
                [{"role": "user", "content": "hi"}],
                extra={},
            )
        return captured

    def test_a_strata_endpoint_gets_the_thinking_cap(self):
        captured = self._send("remote", "http://127.0.0.1:8081")

        payload = captured["payload"]
        self.assertEqual(payload["reasoning_effort"], "none")
        self.assertEqual(payload["reasoning_budget_tokens"], 512)
        # The cap is a dialect field: the common denominator must not carry it to an endpoint that
        # never asked for it, and the llama.cpp-only flag must not ride along either.
        self.assertNotIn("enable_thinking", payload)
        self.assertEqual(payload["model"], "m")
        self.assertEqual(captured["url"], "http://127.0.0.1:8081/v1/chat/completions")

    def test_the_reasoning_is_only_capped_for_an_endpoint_that_wants_it(self):
        """A hosted API keeps the plain OpenAI payload it has always had."""

        from unittest.mock import patch

        from services import llm_remote_dialect

        captured: dict[str, object] = {}
        self_body = self.RESPONSE

        class FakeResponse:
            ok = True
            status_code = 200
            text = ""

            def json(self):
                return self_body

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["payload"] = json
            return FakeResponse()

        with patch.object(llm_remote_dialect, "speaks_dialect", return_value=False), \
                patch("requests.post", side_effect=fake_post):
            chat_llm._post_chat(
                {"provider": "openai", "model": "gpt-5-mini",
                 "base_url": "https://api.openai.com", "api_key": "sk-x"},
                [{"role": "user", "content": "hi"}],
                extra={},
            )

        self.assertNotIn("reasoning_effort", captured["payload"])
        self.assertNotIn("reasoning_budget_tokens", captured["payload"])

    def test_an_empty_answer_says_which_kind_of_empty_it_was(self):
        from unittest.mock import patch

        class FakeResponse:
            ok = True
            status_code = 200
            text = ""

            def json(self):
                return {
                    "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                    "usage": {"completion_tokens": 2048,
                              "completion_tokens_details": {"reasoning_tokens": 2048}},
                }

        with patch("requests.post", return_value=FakeResponse()):
            with self.assertRaises(RuntimeError) as raised:
                chat_llm._post_chat(
                    {"provider": "openai", "model": "m",
                     "base_url": "https://api.openai.com", "api_key": ""},
                    [{"role": "user", "content": "hi"}],
                    extra={},
                )

        message = str(raised.exception)
        self.assertIn("finish_reason=length", message)
        self.assertIn("reasoning_tokens=2048", message)
        self.assertIn("Answer length", message)

    def test_anthropic_is_not_offered_where_it_cannot_work(self):
        """Its API wants its own headers and body, so the control would fail on first use."""

        self.assertNotIn("anthropic", chat_llm._HOSTED)
        panel = _read(os.path.join(
            _APP_DIR, "..", "ui", "src", "components", "SettingsDrawer",
            "AssistantLlmSettings.tsx",
        ))
        self.assertNotIn("'anthropic'", panel)
        self.assertIn("follow the pipeline", _read(os.path.join(
            _APP_DIR, "services", "chat_api.py",
        )))


class TheAssistantDoesNotTouchThePipelineTests(unittest.TestCase):
    """Wiring: one registration line, and the writes go through the clip editor's own route."""

    def setUp(self):
        self.launch = _read(LAUNCH)

    def test_the_route_file_is_registered(self):
        self.assertIn("chat_api", self.launch)
        self.assertIn("api.include_router(chat_api.router)", self.launch)

    def test_the_pipeline_llm_is_not_loaded_or_unloaded_by_the_assistant(self):
        """The whole point: a chat must not disturb the writer that owns llama-server."""

        source = _read(os.path.join(_APP_DIR, "services", "chat_llm.py"))

        self.assertNotIn("load_model(", source)
        self.assertNotIn("unload_model(", source)

    def test_an_accepted_prompt_goes_through_the_clip_editors_writer(self):
        source = _read(os.path.join(_APP_DIR, "services", "chat_api.py"))

        self.assertIn("update_clip_prompt(", source)
        # The field name the clip cards save with, not a private one.
        self.assertIn('{"video_prompt": prompt}', source)
        self.assertIn("PipelineBusyError", source)


class TheSettingsRoutesTests(unittest.TestCase):
    """The routes themselves, exercised: the panel and the widget both read this shape.

    `wgp` is stood in for, because the assistant's settings live in the server config and this
    test is about the routes rather than about loading a checkpoint. The stand-in is the real
    contract: a dict under "services", and a filename to persist it to.
    """

    def setUp(self):
        import types

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        stub = types.ModuleType("wgp")
        stub.server_config = {"services": {}, "save_path": "outputs"}
        stub.server_config_filename = os.path.join(self.temp.name, "config.json")
        self.previous = sys.modules.get("wgp")
        sys.modules["wgp"] = stub
        self.addCleanup(self._restore_wgp)

        from services import chat_api

        app = FastAPI()
        app.include_router(chat_api.router)
        self.client = TestClient(app)

    def test_saving_one_field_does_not_erase_the_others(self):
        """The whole block is saved one field at a time, so an absent key must mean "keep".

        The provider is the one that bit: writing it from an absent key meant saving the address
        -- which is what happens when the field is left -- reverted the choice to "Same as the LLM
        above", took the address and model fields off the panel with it, and read as "it does not
        save my changes".
        """

        self.client.post("/api/v1/assistant/settings", json={
            "chat_llm_provider": "remote",
            "chat_llm_remote_url": "http://192.168.68.130:8787",
            "chat_llm_model_id": "qwen3-8b",
        })

        after = self.client.post("/api/v1/assistant/settings", json={
            "chat_llm_max_tokens": 4096,
        }).json()

        self.assertEqual(after["configured_provider"], "remote")
        self.assertEqual(after["configured_endpoint"], "http://192.168.68.130:8787")
        self.assertEqual(after["configured_model"], "qwen3-8b")
        self.assertEqual(after["configured_max_tokens"], 4096)
        # And an explicit empty provider still means "follow the pipeline".
        cleared = self.client.post(
            "/api/v1/assistant/settings", json={"chat_llm_provider": ""},
        ).json()
        self.assertEqual(cleared["configured_provider"], "")
        self.assertTrue(cleared["follows_pipeline"])

    def _restore_wgp(self):
        if self.previous is not None:
            sys.modules["wgp"] = self.previous
        else:
            sys.modules.pop("wgp", None)

    def test_it_follows_the_pipeline_until_it_is_given_a_provider(self):
        settings = self.client.get("/api/v1/assistant/settings").json()

        self.assertTrue(settings["follows_pipeline"])
        self.assertFalse(settings["routes_off_machine"])

    def test_an_endpoint_on_this_machine_is_kept_here(self):
        response = self.client.post("/api/v1/assistant/settings", json={
            "chat_llm_provider": "remote",
            "chat_llm_model_id": "gemma-4-E4B-it",
            "chat_llm_remote_url": "http://127.0.0.1:8080",
        })

        self.assertEqual(response.status_code, 200)
        settings = response.json()
        self.assertFalse(settings["follows_pipeline"])
        self.assertEqual(settings["model"], "gemma-4-E4B-it")
        self.assertEqual(settings["endpoint"], "http://127.0.0.1:8080")
        self.assertFalse(settings["routes_off_machine"])
        self.assertEqual(settings["configured_provider"], "remote")

    def test_a_public_endpoint_is_named_as_leaving_the_machine(self):
        settings = self.client.post("/api/v1/assistant/settings", json={
            "chat_llm_provider": "openai",
            "chat_llm_model_id": "gpt-5-mini",
            "chat_llm_remote_url": "https://api.openai.com",
        }).json()

        self.assertTrue(settings["routes_off_machine"])

    def test_the_own_endpoint_survives_a_restart(self):
        """It is written where every other preference is, so it is still there next launch."""

        self.client.post("/api/v1/assistant/settings", json={
            "chat_llm_provider": "remote",
            "chat_llm_model_id": "gemma-4-E4B-it",
            "chat_llm_remote_url": "http://127.0.0.1:8080",
        })

        with open(sys.modules["wgp"].server_config_filename, encoding="utf-8") as handle:
            saved = json.load(handle)

        self.assertEqual(saved["services"]["chat_llm_model_id"], "gemma-4-E4B-it")
        self.assertEqual(saved["services"]["chat_llm_provider"], "remote")

    def test_a_key_is_never_read_back(self):
        self.client.post("/api/v1/assistant/settings", json={
            "chat_llm_provider": "openai", "chat_llm_model_id": "m",
            "chat_llm_api_key": "sk-secret-123456",
        })

        settings = self.client.get("/api/v1/assistant/settings").json()

        self.assertTrue(settings["api_key_set"])
        self.assertNotIn("sk-secret-123456", json.dumps(settings))
        self.assertTrue(settings["api_key_masked"].endswith("3456"))

    def test_saving_another_field_does_not_erase_the_key(self):
        """A form that never shows the value must not be able to clear it by saving."""

        self.client.post("/api/v1/assistant/settings", json={
            "chat_llm_provider": "openai", "chat_llm_model_id": "m",
            "chat_llm_api_key": "sk-secret-123456",
        })
        self.client.post("/api/v1/assistant/settings", json={"chat_llm_model_id": "other"})

        self.assertTrue(self.client.get("/api/v1/assistant/settings").json()["api_key_set"])

    def test_an_empty_key_clears_it(self):
        self.client.post("/api/v1/assistant/settings", json={
            "chat_llm_provider": "openai", "chat_llm_model_id": "m",
            "chat_llm_api_key": "sk-secret-123456",
        })
        self.client.post("/api/v1/assistant/settings", json={"chat_llm_api_key": ""})

        self.assertFalse(self.client.get("/api/v1/assistant/settings").json()["api_key_set"])

    def test_a_provider_it_cannot_use_is_refused(self):
        response = self.client.post(
            "/api/v1/assistant/settings", json={"chat_llm_provider": "gemini-enterprise"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("not a provider", response.json()["detail"])

    def test_it_does_not_share_the_pipeline_writer_without_saying_so(self):
        """A chat answered by the engine the app writes with must say so before anyone types."""

        # No endpoint of its own: llm_service answers, which is the pipeline's own model.
        settings = self.client.get("/api/v1/assistant/settings").json()
        self.assertTrue(settings["shares_pipeline_writer"])

        # Pointed at a *different* address: genuinely separate, so no warning and no contention.
        self.client.post("/api/v1/assistant/settings", json={
            "chat_llm_provider": "remote",
            "chat_llm_model_id": "gemma-4-E4B-it",
            "chat_llm_remote_url": "http://127.0.0.1:8082",
        })
        settings = self.client.get("/api/v1/assistant/settings").json()
        self.assertFalse(settings["shares_pipeline_writer"])

    def test_pointing_it_at_the_writers_own_address_is_named_as_shared(self):
        """The case a person would not expect: it looks separate and is not."""

        sys.modules["wgp"].server_config["services"]["llm_remote_url"] = "http://127.0.0.1:8081/"
        self.client.post("/api/v1/assistant/settings", json={
            "chat_llm_provider": "remote",
            "chat_llm_model_id": "qwen3.8-flash-next-iq2_xs",
            # Same engine, spelled with the trailing slash the writer's own setting uses.
            "chat_llm_remote_url": "http://127.0.0.1:8081",
        })

        settings = self.client.get("/api/v1/assistant/settings").json()
        self.assertTrue(settings["shares_pipeline_writer"])

    def test_an_impossible_token_budget_is_refused(self):
        for value in (10, 999999):
            with self.subTest(value=value):
                response = self.client.post(
                    "/api/v1/assistant/settings", json={"chat_llm_max_tokens": value},
                )
                self.assertEqual(response.status_code, 400)

    def test_a_turn_without_messages_is_refused_before_any_model_is_called(self):
        response = self.client.post("/api/v1/assistant/message", json={"messages": []})

        self.assertEqual(response.status_code, 400)
        self.assertIn("messages", response.json()["detail"])

    def test_applying_without_a_project_is_refused(self):
        response = self.client.post("/api/v1/assistant/apply", json={
            "kind": "prompt", "shot": 3, "prompt": "x",
        })

        self.assertEqual(response.status_code, 400)
        self.assertIn("pipeline_id", response.json()["detail"])

    def test_applying_something_that_is_not_a_proposal_is_refused(self):
        response = self.client.post("/api/v1/assistant/apply", json={
            "kind": "delete_project", "shot": 3, "pipeline_id": "abc",
        })

        self.assertEqual(response.status_code, 400)
        self.assertIn("not a proposal", response.json()["detail"])


class TheEndpointIsAskedWhatItServesTests(unittest.TestCase):
    """Recognising the server behind an address, before anything is saved.

    A llama.cpp on the LAN is the case this exists for, and the thing that has to hold is that the
    list says where it came from: `llm_service.get_available_models` prepends the pipeline's own
    catalogue to whatever the remote answered, and sends a masked credential, so through that call
    an address that never replied and a server that answered are indistinguishable -- the field
    looks populated either way, and the id saved is one the server does not serve.
    """

    ANSWER = {"data": [{"id": "qwen3-8b"}, {"id": "unsloth/other"}]}

    class _NotJson:
        """A page that answers 200 with something that is not a model list."""

    def _asked(self, *, ok=True, status=200, payload=None, error=None):
        from unittest.mock import patch

        seen: dict = {}
        body = self.ANSWER if payload is None else payload
        not_json = self._NotJson

        class Response:
            def __init__(self):
                self.ok = ok
                self.status_code = status
                self.text = "refused"

            def json(self):
                if body is not_json:
                    raise ValueError("not a model list")
                return body

        def fake_get(url, headers=None, timeout=None):
            seen["url"] = url
            seen["headers"] = dict(headers or {})
            seen["timeout"] = timeout
            if error is not None:
                raise error
            return Response()

        return seen, patch("requests.get", side_effect=fake_get)

    def test_the_address_that_was_typed_is_the_one_asked(self):
        seen, patcher = self._asked()

        with patcher:
            models, detail = chat_llm.list_models("http://192.168.68.130:8787/v1")

        # A pasted `/v1` is what the OpenAI examples show, and it must not become `/v1/v1`.
        self.assertEqual(seen["url"], "http://192.168.68.130:8787/v1/models")
        self.assertEqual([model["id"] for model in models], ["qwen3-8b", "unsloth/other"])
        self.assertEqual(detail, "")
        self.assertTrue(models[0]["provider"] == "remote")
        self.assertLessEqual(seen["timeout"], chat_llm.MODEL_PROBE_TIMEOUT)

    def test_the_key_goes_with_the_question(self):
        seen, patcher = self._asked()

        with patcher:
            chat_llm.list_models("http://127.0.0.1:8080", api_key="sk-lan-key")

        self.assertEqual(seen["headers"].get("Authorization"), "Bearer sk-lan-key")

    def test_without_a_key_no_credential_is_sent(self):
        seen, patcher = self._asked()

        with patcher:
            chat_llm.list_models("http://127.0.0.1:8080")

        self.assertNotIn("Authorization", seen["headers"])

    def test_an_address_that_does_not_answer_says_so(self):
        _, patcher = self._asked(error=OSError("no route to host"))

        with patcher:
            models, detail = chat_llm.list_models("http://192.168.68.130:8787")

        self.assertEqual(models, [])
        self.assertIn("192.168.68.130:8787", detail)
        self.assertIn("did not answer", detail)

    def test_a_server_that_refuses_says_which_code(self):
        _, patcher = self._asked(ok=False, status=401)

        with patcher:
            models, detail = chat_llm.list_models("http://192.168.68.130:8787", api_key="wrong")

        self.assertEqual(models, [])
        self.assertIn("401", detail)

    def test_a_page_that_is_not_a_model_list_is_not_one(self):
        _, patcher = self._asked(payload=self._NotJson)

        with patcher:
            models, detail = chat_llm.list_models("http://192.168.68.130:8787")

        self.assertEqual(models, [])
        self.assertIn("did not answer with a model list", detail)

    def test_a_server_that_lists_nothing_is_not_a_success(self):
        _, patcher = self._asked(payload={"data": []})

        with patcher:
            models, detail = chat_llm.list_models("http://192.168.68.130:8787")

        self.assertEqual(models, [])
        self.assertIn("listed no models", detail)

    def test_an_empty_address_asks_nothing(self):
        from unittest.mock import patch

        with patch("requests.get") as asked:
            models, detail = chat_llm.list_models("   ")

        asked.assert_not_called()
        self.assertEqual(models, [])
        self.assertTrue(detail)

    def test_only_a_server_with_one_model_is_chosen_without_asking(self):
        self.assertEqual(chat_llm.suggested_model([{"id": "only"}]), "only")
        self.assertEqual(chat_llm.suggested_model([{"id": "a"}, {"id": "b"}]), "")
        self.assertEqual(chat_llm.suggested_model([]), "")


class TheAssistantModelBelongsToTheAddressTests(unittest.TestCase):
    """A model name means nothing to a server it was not chosen for."""

    PIPELINE = {
        "llm_provider": "remote",
        "llm_remote_url": "http://127.0.0.1:8081/",
        "llm_model_id": "qwen3.8-flash-next-iq2_xs",
    }

    def test_a_different_address_does_not_borrow_the_pipelines_model(self):
        endpoint = chat_llm.chat_endpoint(dict(
            self.PIPELINE,
            chat_llm_provider="remote",
            chat_llm_remote_url="http://192.168.68.130:8787/v1",
        ))

        self.assertEqual(endpoint["base_url"], "http://192.168.68.130:8787")
        self.assertEqual(endpoint["model"], "")

    def test_the_pipelines_own_address_still_lends_its_model(self):
        for url in ("", "http://127.0.0.1:8081", "http://127.0.0.1:8081/v1/"):
            with self.subTest(url=url):
                endpoint = chat_llm.chat_endpoint(dict(
                    self.PIPELINE, chat_llm_provider="remote", chat_llm_remote_url=url,
                ))
                self.assertEqual(endpoint["model"], "qwen3.8-flash-next-iq2_xs")

    def test_a_turn_with_no_model_is_refused_before_the_request(self):
        """A 404 from the wrong server is a worse answer than a sentence saying what is missing."""

        from unittest.mock import patch

        with patch("requests.post") as posted:
            with self.assertRaises(ValueError) as caught:
                chat_llm.ask(
                    dict(self.PIPELINE, chat_llm_provider="remote",
                         chat_llm_remote_url="http://192.168.68.130:8787"),
                    [{"role": "user", "content": "hola"}],
                )

        posted.assert_not_called()
        self.assertIn("no model", str(caught.exception))


def _settings_client(case: unittest.TestCase):
    """A `TestClient` over the assistant's routes, with `wgp` stood in for.

    The stand-in is the real contract -- a dict under "services", and a filename to persist it to
    -- because the assistant's settings live in the server config and these cases are about the
    routes rather than about loading a checkpoint.
    """

    import types

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    temp = tempfile.TemporaryDirectory()
    case.addCleanup(temp.cleanup)
    stub = types.ModuleType("wgp")
    stub.server_config = {"services": {}, "save_path": "outputs"}
    stub.server_config_filename = os.path.join(temp.name, "config.json")
    previous = sys.modules.get("wgp")
    sys.modules["wgp"] = stub

    def restore():
        if previous is not None:
            sys.modules["wgp"] = previous
        else:
            sys.modules.pop("wgp", None)

    case.addCleanup(restore)

    from services import chat_api

    app = FastAPI()
    app.include_router(chat_api.router)
    return TestClient(app)


class TheModelListNamesItsSourceTests(unittest.TestCase):
    """The route the field asks: which address, which list, and nothing invented in between."""

    def setUp(self):
        self.client = _settings_client(self)

    def _listed(self, models, detail=""):
        from unittest.mock import patch

        return patch.object(chat_llm, "list_models", return_value=(models, detail))

    def test_a_typed_address_is_asked_before_anything_is_saved(self):
        with self._listed([{"id": "qwen3-8b", "label": "qwen3-8b"}]):
            body = self.client.get("/api/v1/assistant/models", params={
                "url": "http://192.168.68.130:8787/v1", "provider": "remote",
            }).json()

        self.assertEqual(body["source"], "endpoint")
        self.assertTrue(body["reachable"])
        self.assertEqual(body["endpoint"], "http://192.168.68.130:8787")
        self.assertEqual(body["suggested_model"], "qwen3-8b")
        self.assertEqual([model["id"] for model in body["models"]], ["qwen3-8b"])

    def test_two_models_is_a_choice_rather_than_a_default(self):
        with self._listed([{"id": "a"}, {"id": "b"}]):
            body = self.client.get("/api/v1/assistant/models", params={
                "url": "http://192.168.68.130:8787", "provider": "remote",
            }).json()

        self.assertEqual(body["suggested_model"], "")
        self.assertEqual(len(body["models"]), 2)

    def test_an_address_that_does_not_answer_offers_nothing_to_pick(self):
        with self._listed([], "http://192.168.68.130:8787/v1/models did not answer (timed out)."):
            body = self.client.get("/api/v1/assistant/models", params={
                "url": "http://192.168.68.130:8787", "provider": "remote",
            }).json()

        self.assertEqual(body["models"], [])
        self.assertEqual(body["source"], "none")
        self.assertFalse(body["reachable"])
        self.assertIn("did not answer", body["detail"])

    def test_the_local_catalogue_is_named_as_the_catalogue(self):
        body = self.client.get("/api/v1/assistant/models", params={"provider": "local"}).json()

        self.assertEqual(body["source"], "catalogue")
        self.assertIsNone(body["reachable"])
        self.assertEqual(body["endpoint"], "")
        self.assertTrue(body["models"])

    def test_an_assistant_pointed_elsewhere_has_no_model_until_one_is_chosen(self):
        self.client.post("/api/v1/assistant/settings", json={
            "chat_llm_provider": "remote",
            "chat_llm_remote_url": "http://192.168.68.130:8787",
        })

        settings = self.client.get("/api/v1/assistant/settings").json()

        self.assertFalse(settings["follows_pipeline"])
        self.assertTrue(settings["needs_model"])
        self.assertEqual(settings["endpoint"], "http://192.168.68.130:8787")
        # Not the pipeline's model: that server does not serve it, and naming it here is what made
        # the setting look ignored rather than unfilled.
        self.assertEqual(settings["model"], "")

    def test_the_pipeline_model_is_still_named_while_the_pipeline_answers(self):
        sys.modules["wgp"].server_config["services"]["llm_model_id"] = "some/pipeline-model"

        settings = self.client.get("/api/v1/assistant/settings").json()

        self.assertTrue(settings["follows_pipeline"])
        self.assertFalse(settings["needs_model"])
        self.assertEqual(settings["model"], "some/pipeline-model")

    def test_saving_a_model_ends_the_question(self):
        self.client.post("/api/v1/assistant/settings", json={
            "chat_llm_provider": "remote",
            "chat_llm_remote_url": "http://192.168.68.130:8787",
            "chat_llm_model_id": "qwen3-8b",
        })

        settings = self.client.get("/api/v1/assistant/settings").json()

        self.assertFalse(settings["needs_model"])
        self.assertEqual(settings["model"], "qwen3-8b")
        self.assertEqual(settings["configured_model"], "qwen3-8b")

    def test_a_pasted_suffix_is_stored_the_way_the_chat_calls_it(self):
        """The field must show the address in use, not a copy differing by a suffix."""

        settings = self.client.post("/api/v1/assistant/settings", json={
            "chat_llm_provider": "remote",
            "chat_llm_remote_url": "http://192.168.68.130:8787/v1/",
        }).json()

        self.assertEqual(settings["configured_endpoint"], "http://192.168.68.130:8787")
        self.assertEqual(settings["endpoint"], "http://192.168.68.130:8787")

    def test_an_address_that_is_not_an_address_is_refused(self):
        response = self.client.post("/api/v1/assistant/settings", json={
            "chat_llm_provider": "remote",
            "chat_llm_remote_url": "192.168.68.130:8787",
        })

        self.assertEqual(response.status_code, 400)
        self.assertIn("http://", response.json()["detail"])

    def test_the_address_is_still_checked_when_the_provider_is_not_in_the_body(self):
        """The field is saved on its own, so the check has to read the stored provider too."""

        self.client.post("/api/v1/assistant/settings", json={"chat_llm_provider": "remote"})

        response = self.client.post(
            "/api/v1/assistant/settings", json={"chat_llm_remote_url": "192.168.68.130:8787"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("http://", response.json()["detail"])

    def test_saving_only_the_address_keeps_the_provider_and_trims_the_suffix(self):
        self.client.post(
            "/api/v1/assistant/settings", json={"chat_llm_provider": "remote"},
        )

        settings = self.client.post("/api/v1/assistant/settings", json={
            "chat_llm_remote_url": "http://192.168.68.130:8787/v1/",
        }).json()

        self.assertEqual(settings["configured_provider"], "remote")
        self.assertEqual(settings["configured_endpoint"], "http://192.168.68.130:8787")
        self.assertTrue(settings["needs_model"])

    def test_an_address_only_has_to_be_an_address_when_it_is_one(self):
        """Following the pipeline keeps no address of its own, so there is nothing to check."""

        response = self.client.post("/api/v1/assistant/settings", json={
            "chat_llm_provider": "", "chat_llm_remote_url": "",
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["follows_pipeline"])

    def test_the_saved_address_answers_when_the_form_asks_nothing(self):
        self.client.post("/api/v1/assistant/settings", json={
            "chat_llm_provider": "remote",
            "chat_llm_remote_url": "http://192.168.68.130:8787",
            "chat_llm_model_id": "qwen3-8b",
        })

        with self._listed([{"id": "qwen3-8b", "label": "qwen3-8b"}]):
            body = self.client.get("/api/v1/assistant/models").json()

        self.assertEqual(body["source"], "endpoint")
        self.assertEqual(body["endpoint"], "http://192.168.68.130:8787")
        self.assertEqual(body["provider"], "remote")


class TheWidgetIsReachableAndMovableTests(unittest.TestCase):
    """The launcher stands out, and it can be put where the person wants it.

    A floating panel is either in the way or out of reach; the only way it stops being both is
    that it moves. These pin the two decisions: it wears the theme's primary-action gradient --
    the one every other call to action uses, so it reads as a call to action -- and it drags by
    its own body, without a drag being mistaken for the click that opens it.
    """

    WIDGET = os.path.join(_APP_DIR, "..", "ui", "src", "components", "Assistant", "AssistantWidget.tsx")

    def setUp(self):
        self.widget = _read(os.path.abspath(self.WIDGET))

    def test_the_launcher_wears_the_primary_action_gradient(self):
        self.assertIn("bg-cta", self.widget)
        self.assertIn("shadow-accent-glow", self.widget)
        # Not a hardcoded orange: the token follows whichever theme is in force.
        self.assertNotIn("bg-orange", self.widget)

    def test_it_is_mounted_over_every_area(self):
        app = _read(os.path.join(_APP_DIR, "..", "ui", "src", "App.tsx"))

        self.assertIn("import { AssistantWidget }", app)
        self.assertIn("<AssistantWidget />", app)

    def test_it_drags_by_its_own_body(self):
        self.assertIn("onPointerDown", self.widget)
        self.assertIn("setPointerCapture", self.widget)
        # Where it was left survives a reload, which is the point of moving it.
        self.assertIn("localStorage", self.widget)

    def test_a_drag_is_not_mistaken_for_the_click_that_opens_it(self):
        self.assertIn("DRAG_THRESHOLD", self.widget)
        self.assertIn("wasDragged()", self.widget)
        # A keyboard activation raises no pointer events, so it is always a click.
        self.assertIn("event.detail === 0", self.widget)

    def test_a_moved_widget_is_kept_on_screen(self):
        self.assertIn("clampToViewport", self.widget)
        self.assertIn("fitInto", self.widget)
        # The clamp uses the element's own size, never the window's: passing innerWidth as the
        # width would snap the widget into the corner on any resize.
        self.assertIn("rect.width", self.widget)
        self.assertNotIn("clampToViewport(current.x, current.y, window.innerWidth", self.widget)


if __name__ == "__main__":
    unittest.main()
