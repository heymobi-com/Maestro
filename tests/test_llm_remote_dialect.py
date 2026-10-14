"""The dialect a local OpenAI-compatible server gets, and the one everyone else keeps.

Measured against Strata on 127.0.0.1:8081: a tight allowance went entirely to `reasoning_content`
and the answer came back empty, and a schema sent in llama.cpp's form came back as the answer. Both
are fixable with fields Maestro already sends (`reasoning_effort`, and the OpenAI standard
`response_format`), but only for an endpoint that can use them -- so the endpoint is asked who it
is, and only a local one is asked at all.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from services import llm_remote_dialect as dialect  # noqa: E402
from services import llm_service  # noqa: E402

STRATA_URL = "http://127.0.0.1:8081"
SCHEMA = {"type": "object", "properties": {"shots": {"type": "array"}}, "required": ["shots"]}


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status
        self.ok = 200 <= status < 300

    def json(self):
        return json.loads(self._body)


def health(service="strata", status=200):
    return FakeResponse(json.dumps({"status": "ok", "service": service, "loaded": True}), status)


def routes(health_body=None, models_body=None):
    """Answer /health and /v1/models the way a server would, and refuse anything else."""

    def get(url, **kwargs):
        if url.endswith("/health") and health_body is not None:
            return FakeResponse(json.dumps(health_body))
        if url.endswith("/v1/models") and models_body is not None:
            return FakeResponse(json.dumps(models_body))
        return FakeResponse("{}", status=404)

    return get


class DetectionTests(unittest.TestCase):
    def setUp(self):
        dialect.forget()

    def tearDown(self):
        dialect.forget()

    def test_a_local_server_that_names_itself_is_recognised(self):
        with patch("requests.get", return_value=health()) as get:
            self.assertTrue(dialect.speaks_dialect(STRATA_URL))
        self.assertEqual(get.call_args.args[0], "http://127.0.0.1:8081/health")

    def test_another_local_server_is_not(self):
        with patch("requests.get", return_value=health(service="llama.cpp")):
            self.assertFalse(dialect.speaks_dialect(STRATA_URL))

    def test_a_hosted_api_is_never_probed(self):
        with patch("requests.get") as get:
            self.assertFalse(dialect.speaks_dialect("https://api.deepseek.com"))
            self.assertFalse(dialect.speaks_dialect("https://api.openai.com/v1"))
        self.assertFalse(get.called, "a hosted endpoint must not receive a probe")

    def test_a_lan_address_may_be_probed(self):
        with patch("requests.get", return_value=health()) as get:
            self.assertTrue(dialect.speaks_dialect("http://192.168.1.50:8081"))
        self.assertTrue(get.called)

    def test_an_unreachable_server_falls_back_to_the_common_denominator(self):
        with patch("requests.get", side_effect=OSError("connection refused")):
            self.assertFalse(dialect.speaks_dialect(STRATA_URL))

    def test_a_server_without_health_is_not_ours(self):
        with patch("requests.get", return_value=FakeResponse("{}", status=404)):
            self.assertFalse(dialect.speaks_dialect(STRATA_URL))

    def test_the_answer_is_cached_so_a_probe_is_not_paid_per_request(self):
        with patch("requests.get", return_value=health()) as get:
            self.assertTrue(dialect.speaks_dialect(STRATA_URL))
            self.assertTrue(dialect.speaks_dialect(STRATA_URL))
            self.assertTrue(dialect.applies("remote", STRATA_URL))
        self.assertEqual(get.call_count, 1)

    def test_the_cache_expires(self):
        with patch("requests.get", return_value=health(service="other")) as get:
            self.assertFalse(dialect.speaks_dialect(STRATA_URL, now=0.0))
            # Within the TTL the earlier answer stands, even if the server changed.
            with patch("requests.get", return_value=health()) as later:
                self.assertFalse(dialect.speaks_dialect(STRATA_URL, now=dialect._PROBE_TTL - 1))
                self.assertFalse(later.called)
            # Past it, the server is asked again and the new answer is believed.
            with patch("requests.get", return_value=health()) as again:
                self.assertTrue(dialect.speaks_dialect(STRATA_URL, now=dialect._PROBE_TTL + 1))
                self.assertTrue(again.called)
        self.assertEqual(get.call_count, 1)

    def test_only_remote_providers_use_it(self):
        with patch("requests.get", return_value=health()):
            self.assertTrue(dialect.applies("remote", STRATA_URL))
            self.assertTrue(dialect.applies("openai", STRATA_URL))
            self.assertFalse(dialect.applies("local", STRATA_URL))
            self.assertFalse(dialect.applies("anthropic", STRATA_URL))

    def test_an_empty_url_is_never_a_server(self):
        with patch("requests.get") as get:
            self.assertFalse(dialect.applies("remote", ""))
        self.assertFalse(get.called)


class FinalizeTests(unittest.TestCase):
    def setUp(self):
        dialect.forget()

    def tearDown(self):
        dialect.forget()

    def finalize(self, *, provider="remote", url=STRATA_URL, raw=None, prepared=None):
        with patch("requests.get", return_value=health()):
            return dialect.finalize(provider, url, raw or {}, prepared or {})

    def test_without_the_dialect_nothing_is_added(self):
        raw = {"enable_thinking": False, "chat_template_kwargs": {"enable_thinking": False}}
        prepared = {"messages": [], "max_tokens": 128}
        with patch("requests.get") as get:
            self.assertEqual(dialect.finalize("remote", "https://api.deepseek.com", raw, prepared), prepared)
        self.assertFalse(get.called)

    def test_a_requested_schema_is_still_dropped_for_everyone_else(self):
        raw = {"response_format": {"type": "json_object", "schema": SCHEMA}}
        prepared = {"messages": [], "response_format": dict(raw["response_format"])}
        with patch("requests.get", return_value=health(service="llama.cpp")):
            out = dialect.finalize("remote", STRATA_URL, raw, prepared)
        self.assertNotIn("response_format", out, "an unknown endpoint must not see the grammar")

    def test_the_schema_is_sent_in_the_openai_form(self):
        raw = {"response_format": {"type": "json_object", "schema": SCHEMA}}
        prepared = {"messages": [], "response_format": dict(raw["response_format"])}
        out = self.finalize(raw=raw, prepared=prepared)
        self.assertEqual(out["response_format"], {
            "type": "json_schema",
            "json_schema": {"name": "maestro_response", "strict": True, "schema": SCHEMA},
        })

    def test_an_array_root_travels_inside_an_object(self):
        # Measured: Strata refuses an array root outright -- "response_format schema must accept only
        # JSON objects at its root" -- before it generates a token, and the whole call is lost. Left
        # unconstrained, a request for 16-22 shots came back as 37 and was cut at the token ceiling.
        # Moving the array under an object root is what lets the endpoint state the schema, maxItems
        # included, and check the answer against it.
        array_schema = {"type": "array", "items": {"type": "object"}, "minItems": 16, "maxItems": 22}
        raw = {"response_format": {"type": "json_object", "schema": array_schema}}
        prepared = {"messages": [], "response_format": dict(raw["response_format"])}
        with patch("builtins.print"):
            out = self.finalize(raw=raw, prepared=prepared)
        enviado = out["response_format"]["json_schema"]["schema"]
        self.assertEqual(enviado["type"], "object")
        self.assertEqual(enviado["required"], [dialect.ENVELOPE_KEY])
        self.assertEqual(enviado["properties"][dialect.ENVELOPE_KEY], array_schema)
        self.assertTrue(
            dialect._object_rooted(enviado), "what is sent must be what the endpoint accepts"
        )

    def test_the_envelope_key_is_the_one_the_parser_unwraps(self):
        # The whole trick rests on this name: every branch of the planner's _parse_json_response
        # reads the list under "shots", so the caller still receives the array it asked for.
        self.assertEqual(dialect.ENVELOPE_KEY, "shots")

    def test_a_wrapped_schema_still_asks_for_no_reasoning(self):
        # Wrapping must not cost the other half of the dialect: reasoning off is what made this
        # endpoint answer at all.
        raw = {
            "enable_thinking": False,
            "response_format": {"type": "json_object", "schema": {"type": "array", "items": {}}},
        }
        prepared = {"messages": [], "response_format": {"type": "json_object", "schema": {"type": "array", "items": {}}}}
        with patch("builtins.print"):
            out = self.finalize(raw=raw, prepared=prepared)
        self.assertEqual(out["reasoning_effort"], "none")

    def test_an_array_root_is_reported_once_and_not_once_per_call(self):
        raw = {"response_format": {"type": "json_object", "schema": {"type": "array", "items": {}}}}
        prepared = {"messages": [], "response_format": {"type": "json_object", "schema": {"type": "array", "items": {}}}}
        with patch("builtins.print") as said:
            self.finalize(raw=raw, prepared=prepared)
            self.finalize(raw=raw, prepared=prepared)
        avisos = [c for c in said.call_args_list if "array" in str(c)]
        self.assertEqual(len(avisos), 1, "a plan makes many of these calls")

    def test_forgetting_an_endpoint_lets_the_notice_be_said_again(self):
        raw = {"response_format": {"type": "json_object", "schema": {"type": "array", "items": {}}}}
        prepared = {"messages": [], "response_format": {"type": "json_object", "schema": {"type": "array", "items": {}}}}
        with patch("builtins.print") as said:
            self.finalize(raw=raw, prepared=prepared)
            dialect.forget(STRATA_URL)
            self.finalize(raw=raw, prepared=prepared)
        avisos = [c for c in said.call_args_list if "array" in str(c)]
        self.assertEqual(len(avisos), 2)

    def test_a_refused_thinking_request_silences_the_reasoning(self):
        raw = {"enable_thinking": False, "chat_template_kwargs": {"enable_thinking": False}}
        self.assertEqual(self.finalize(raw=raw)["reasoning_effort"], "none")

    def test_thinking_that_was_asked_for_is_left_alone(self):
        raw = {"enable_thinking": True, "reasoning_effort": "medium"}
        self.assertEqual(self.finalize(raw=raw, prepared=raw), raw)

    def test_every_added_field_is_one_an_openai_endpoint_accepts(self):
        # The dialect must not smuggle a llama.cpp-only field back into the payload.
        raw = {"enable_thinking": False, "response_format": {"type": "json_object", "schema": SCHEMA}}
        prepared = {"messages": [], "response_format": dict(raw["response_format"])}
        out = self.finalize(raw=raw, prepared=prepared)
        self.assertTrue(set(out).issubset(llm_service._OPENAI_CHAT_FIELDS))

    def test_the_caller_is_never_mutated(self):
        raw = {"enable_thinking": False, "response_format": {"type": "json_object", "schema": SCHEMA}}
        prepared = {"messages": [], "response_format": dict(raw["response_format"])}
        before = json.dumps([raw, prepared], sort_keys=True)
        self.finalize(raw=raw, prepared=prepared)
        self.assertEqual(json.dumps([raw, prepared], sort_keys=True), before)


class SchemaRootTests(unittest.TestCase):
    """The classification that decides whether a grammar crosses over, on its own."""

    def test_an_object_is_a_root_this_endpoint_accepts(self):
        self.assertTrue(dialect._object_rooted({"type": "object", "properties": {}}))

    def test_an_array_is_not(self):
        self.assertFalse(dialect._object_rooted({"type": "array", "items": {"type": "object"}}))

    def test_a_union_of_objects_is(self):
        schema = {"anyOf": [{"type": "object"}, {"type": "object"}]}
        self.assertTrue(dialect._object_rooted(schema))
        self.assertTrue(dialect._object_rooted({"oneOf": [{"type": "object"}]}))

    def test_a_union_that_admits_an_array_is_not(self):
        # One branch the server cannot hold a root for is still a 400, so all of them must qualify.
        schema = {"anyOf": [{"type": "object"}, {"type": "array"}]}
        self.assertFalse(dialect._object_rooted(schema))

    def test_a_schema_without_a_root_type_is_not(self):
        self.assertFalse(dialect._object_rooted({"items": {"type": "object"}}))
        self.assertFalse(dialect._object_rooted({"anyOf": []}))

    def test_a_real_shot_list_root_is_an_array(self):
        # The shape the planner really asks for, so the rule cannot drift away from it.
        shot_list = {
            "type": "array",
            "items": {"type": "object", "properties": {"shot_number": {"type": "integer"}}},
            "minItems": 5,
            "maxItems": 9,
        }
        self.assertFalse(dialect._object_rooted(shot_list))


class EngineSeamTests(unittest.TestCase):
    """The payload the engine actually builds, with the seam in place."""

    def setUp(self):
        dialect.forget()
        self.saved = (llm_service._provider, llm_service._model_id, llm_service._remote_url)

    def tearDown(self):
        dialect.forget()
        llm_service._provider, llm_service._model_id, llm_service._remote_url = self.saved

    def finalize(self, url):
        llm_service._provider, llm_service._model_id, llm_service._remote_url = "remote", "m", url
        return llm_service._finalize_payload({
            "messages": [{"role": "user", "content": "plan"}],
            "max_tokens": 256,
            "enable_thinking": False,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_object", "schema": SCHEMA},
        })

    def test_a_strata_endpoint_receives_the_schema_and_no_reasoning(self):
        with patch("requests.get", return_value=health()):
            out = self.finalize(STRATA_URL)
        self.assertEqual(out["response_format"]["type"], "json_schema")
        self.assertEqual(out["response_format"]["json_schema"]["schema"], SCHEMA)
        self.assertEqual(out["reasoning_effort"], "none")
        self.assertNotIn("chat_template_kwargs", out, "llama.cpp fields stay out of the request")

    def test_any_other_endpoint_keeps_the_previous_behaviour(self):
        with patch("requests.get", return_value=health(service="llama.cpp")):
            out = self.finalize("http://127.0.0.1:1234")
        self.assertNotIn("response_format", out)
        self.assertNotIn("reasoning_effort", out)
        self.assertEqual(out["model"], "m")
        self.assertEqual(out["max_tokens"], 256)

    def test_a_local_writer_keeps_its_array_grammar(self):
        # llama.cpp compiles an array root happily, so the refusal is the remote endpoint's alone:
        # a shot list still constrains the bundled writer, which is what it always did.
        array_schema = {"type": "array", "items": {"type": "object"}, "minItems": 5}
        llm_service._provider, llm_service._model_id, llm_service._remote_url = "local", "m", ""
        grammar = {"type": "json_object", "schema": array_schema}
        out = llm_service._finalize_payload({"messages": [], "response_format": grammar})
        self.assertEqual(out["response_format"], grammar)


class ImageCapabilityTests(unittest.TestCase):
    """Whether a writer can be asked to read a reference image at all.

    Measured against Strata: an image block comes back 400 Bad Request, mid-plan, on a reference the
    writer was supposed to read. The remote path assumed vision for every remote writer and left it
    to the provider to complain, so the server's own answer is read instead -- and a server that says
    nothing keeps the optimistic behaviour, because a hosted API cannot be asked.
    """

    def setUp(self):
        dialect.forget()

    def tearDown(self):
        dialect.forget()

    def test_a_server_that_reports_no_images_is_believed(self):
        # Strata's own /health, verbatim.
        with patch("requests.get", side_effect=routes(
            health_body={
                "status": "ok", "max_context": 65536, "model": "qwen3.8-flash-next-iq2_xs",
                "images": False, "api_key": False, "loaded": True, "service": "strata",
            },
        )):
            self.assertFalse(dialect.takes_images(STRATA_URL))

    def test_a_server_that_reports_images_keeps_them(self):
        with patch("requests.get", side_effect=routes(health_body={"service": "x", "images": True})):
            self.assertTrue(dialect.takes_images(STRATA_URL))

    def test_a_model_listing_without_an_image_modality_is_a_no(self):
        with patch("requests.get", side_effect=routes(
            health_body={"service": "llama.cpp"},
            models_body={"data": [{"id": "m", "architecture": {"input_modalities": ["text"]}}]},
        )):
            self.assertFalse(dialect.takes_images(STRATA_URL))

    def test_a_model_listing_with_an_image_modality_is_a_yes(self):
        with patch("requests.get", side_effect=routes(
            health_body={"service": "llama.cpp"},
            models_body={"data": [{"id": "m", "architecture": {"input_modalities": ["text", "image"]}}]},
        )):
            self.assertTrue(dialect.takes_images(STRATA_URL))

    def test_a_server_that_says_nothing_keeps_the_optimistic_answer(self):
        with patch("requests.get", side_effect=routes(health_body={"service": "llama.cpp"})):
            self.assertTrue(dialect.takes_images(STRATA_URL))

    def test_an_unreachable_server_keeps_the_optimistic_answer(self):
        with patch("requests.get", side_effect=OSError("connection refused")):
            self.assertTrue(dialect.takes_images(STRATA_URL))

    def test_a_hosted_api_is_never_probed(self):
        with patch("requests.get") as get:
            self.assertTrue(dialect.takes_images("https://api.openai.com/v1"))
        get.assert_not_called()

    def test_the_answer_is_cached(self):
        with patch("requests.get", side_effect=routes(health_body={"images": False})) as get:
            self.assertFalse(dialect.takes_images(STRATA_URL))
            self.assertFalse(dialect.takes_images(STRATA_URL))
        self.assertEqual(get.call_count, 1)

    def test_the_two_probes_do_not_share_a_cache_entry(self):
        # A server may answer one question and not the other, so a detection answer must not decide
        # what the image question reads.
        with patch("requests.get", side_effect=routes(
            health_body={"service": "strata", "images": False},
        )):
            self.assertTrue(dialect.speaks_dialect(STRATA_URL))
            self.assertFalse(dialect.takes_images(STRATA_URL))


class EngineVisionTests(unittest.TestCase):
    """The engine's own flag, which is what decides whether an image is attached at all."""

    def setUp(self):
        dialect.forget()
        self.saved = (
            llm_service._provider,
            llm_service._model_id,
            llm_service._remote_url,
            llm_service._vision_available,
        )

    def tearDown(self):
        dialect.forget()
        (
            llm_service._provider,
            llm_service._model_id,
            llm_service._remote_url,
            llm_service._vision_available,
        ) = self.saved

    def test_connecting_to_a_text_only_writer_disables_images(self):
        with patch("requests.get", side_effect=routes(
            health_body={"service": "strata", "images": False},
        )):
            llm_service.load_model(model_id="qwen3.8-flash-next-iq2_xs", provider="remote", remote_url=STRATA_URL)
        self.assertFalse(llm_service._vision_available)

    def test_connecting_to_a_writer_that_says_nothing_keeps_them(self):
        with patch("requests.get", side_effect=routes(
            health_body={"service": "llama.cpp"},
        )):
            llm_service.load_model(model_id="m", provider="remote", remote_url=STRATA_URL)
        self.assertTrue(llm_service._vision_available)

    def test_a_reference_image_is_then_left_out_of_the_request(self):
        # The degrade path the codebase already had for a local model with no mmproj: the prompt
        # goes as plain text instead of as an image block the writer would refuse.
        llm_service._vision_available = False
        message = llm_service._user_message("plan the shot", ["C:/refs/martha.png"])
        self.assertEqual(message, {"role": "user", "content": "plan the shot"})


if __name__ == "__main__":
    unittest.main()
