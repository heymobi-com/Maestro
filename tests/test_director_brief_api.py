"""The brief routes, exercised through ASGI without booting the application.

The brief is the one input a person writes freely and the input the pipeline reads
structurally, so its check has to work on its own: a prose brief must come back with the
faults named, the mechanical pass must produce something the planner can read, and the
LLM pass must never be able to make things worse by returning prose or nothing.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import httpx
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.director.brief_api import create_router, strip_code_fences  # noqa: E402

PROSE = (
    "Abuela Rosa is an old woman who waits in a stone kitchen. One night the children "
    "do not come home. She walks a corridor where a tall figure in a black shroud waits. "
    "The shroud opens and a priest is revealed. She kneels in the church and the bell rings."
)


def _client(generate=None) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(create_router(generate=generate))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    )


REVIEW_BODY = {
    "brief": PROSE,
    "pipeline_type": "short_film_audio",
    "clip_count": 26,
    "transcript_speakers": ["S1", "S2"],
    "has_audio": True,
}


class ReviewRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_prose_brief_is_reported_with_its_faults(self):
        async with _client() as client:
            response = await client.post("/api/v1/director/brief/review", json=REVIEW_BODY)

        self.assertEqual(response.status_code, 200)
        report = response.json()
        codes = {finding["code"] for finding in report["findings"]}
        self.assertIn("no-subject-lock", codes)
        self.assertIn("speakers-unbound", codes)
        self.assertEqual(report["kind"], "short_film_audio")

    async def test_the_route_needs_no_model(self):
        async with _client(generate=None) as client:
            response = await client.post("/api/v1/director/brief/review", json=REVIEW_BODY)

        self.assertEqual(response.status_code, 200)


class StructureRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_without_a_model_the_mechanical_pass_still_fixes_the_shape(self):
        async with _client(generate=None) as client:
            response = await client.post(
                "/api/v1/director/brief/structure", json=REVIEW_BODY,
            )

        result = response.json()
        self.assertEqual(result["method"], "normalized")
        self.assertIn("Stage 1", result["brief"])
        self.assertIn("no-subject-lock", result["remaining"])

    async def test_a_model_answer_replaces_the_proposal_and_resolves_faults(self):
        def fake_generate(prompt, system_prompt, max_new_tokens):
            return (
                "SUBJECT LOCK:\n"
                "<Subject 1> is ALWAYS Abuela Rosa.\n"
                "<Subject 2> is ALWAYS la Figura del Purgatorio.\n\n"
                "CHARACTERS:\nAbuela Rosa: woman, 78, black headscarf.\n"
                "La Figura del Purgatorio: tall figure in a black shroud.\n"
                "Don Aurelio: man, 60, worn alb.\n\n"
                "AMBIENCE:\nStone mountain village in fog.\n\n"
                "DESCRIPTION:\n"
                "Stage 1 (kitchen): Abuela Rosa waits at the table.\n"
                "Stage 2 (corridor): la Figura del Purgatorio enters behind her.\n"
                "Stage 3 (church): Don Aurelio revealed, Abuela Rosa kneels.\n\n"
                "NEGATIVE:\nNo extra characters, no gore.\n"
            )

        async with _client(generate=fake_generate) as client:
            response = await client.post(
                "/api/v1/director/brief/structure", json=REVIEW_BODY,
            )

        result = response.json()
        self.assertEqual(result["method"], "llm")
        self.assertIn("no-subject-lock", result["resolved"])
        self.assertNotIn("no-subject-lock", result["remaining"])

    async def test_a_model_that_answers_prose_does_not_replace_the_brief(self):
        async with _client(generate=lambda *_: "I cannot help with that.") as client:
            response = await client.post(
                "/api/v1/director/brief/structure", json=REVIEW_BODY,
            )

        result = response.json()
        self.assertEqual(result["method"], "normalized")
        self.assertIn("Stage 1", result["brief"])

    async def test_a_model_that_raises_falls_back_instead_of_failing(self):
        def explode(*_args):
            raise RuntimeError("model not loaded")

        async with _client(generate=explode) as client:
            response = await client.post(
                "/api/v1/director/brief/structure", json=REVIEW_BODY,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["method"], "normalized")

    async def test_the_model_can_be_skipped_on_request(self):
        called = {"n": 0}

        def fake_generate(*_args):
            called["n"] += 1
            return "x" * 400 + ": y"

        body = dict(REVIEW_BODY, use_llm=False)
        async with _client(generate=fake_generate) as client:
            response = await client.post("/api/v1/director/brief/structure", json=body)

        self.assertEqual(called["n"], 0)
        self.assertEqual(response.json()["method"], "normalized")


    async def test_a_model_answer_that_leaks_the_template_is_cleaned(self):
        def fake_generate(prompt, system_prompt, max_new_tokens):
            return (
                "SUBJECT LOCK:\n"
                "<Subject 1> is ALWAYS Abuela Rosa.\n"
                "<every other character>: named as presences and objects that never become S1 or S2.\n"
                "\n"
                "CHARACTERS:\nAbuela Rosa: woman, 78, black headscarf.\n\n"
                "DESCRIPTION:\n"
                "Stage 1 (kitchen): Abuela Rosa walks to the table and lifts the cup.\n"
                "Stage 2 (corridor): Abuela Rosa crosses the corridor and turns.\n"
            )

        async with _client(generate=fake_generate) as client:
            response = await client.post(
                "/api/v1/director/brief/structure", json=REVIEW_BODY,
            )

        result = response.json()
        self.assertEqual(result["method"], "llm")
        self.assertNotIn("<every other character>", result["brief"])
        self.assertNotIn("template-placeholder", result["remaining"])


SECTIONED = (
    "SUBJECT LOCK:\n<Subject 1> is ALWAYS Abuela Rosa.\n\n"
    "CHARACTERS:\nAbuela Rosa: old woman, black headscarf.\n\n"
    "DESCRIPTION:\n"
    "Stage 1 (kitchen): Abuela Rosa waits at the wooden table and lifts the cup.\n"
    "Stage 2 (corridor): the tall figure in the shroud enters behind her.\n"
    "Stage 3 (church): Abuela Rosa kneels in the church as the bell rings.\n\n"
    "NEGATIVE:\nNo gore, no extra characters.\n"
)

REWRITTEN_FILM = (
    "SUBJECT LOCK:\n<Subject 1> (S1) is ALWAYS Abuela Rosa.\n\n"
    "CHARACTERS:\nAbuela Rosa (S1): old woman, black headscarf, grey skirt.\n"
    "Don Aurelio: man, 60, worn alb.\n\n"
    "DESCRIPTION:\n"
    "An epic montage of biblical battles unfolds in slow motion, with armies clashing "
    "on a dusty plain while a narrator describes the scale of the conflict.\n\n"
    "NEGATIVE:\nNo gore, no extra characters.\n"
)


class StructureKeepsTheStoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_rewritten_description_does_not_replace_the_persons_film(self):
        async with _client(generate=lambda *_: REWRITTEN_FILM) as client:
            response = await client.post(
                "/api/v1/director/brief/structure",
                json=dict(REVIEW_BODY, brief=SECTIONED),
            )

        result = response.json()
        self.assertIn("Abuela Rosa waits at the wooden table", result["brief"])
        self.assertNotIn("biblical battles", result["brief"])
        self.assertIn("DESCRIPTION", result["kept"])

    async def test_the_model_may_still_fix_the_lock_and_add_a_missing_row(self):
        async with _client(generate=lambda *_: REWRITTEN_FILM) as client:
            response = await client.post(
                "/api/v1/director/brief/structure",
                json=dict(REVIEW_BODY, brief=SECTIONED),
            )

        brief = response.json()["brief"]
        self.assertIn("<Subject 1> (S1) is ALWAYS Abuela Rosa.", brief)
        self.assertIn("Don Aurelio", brief)
        self.assertIn("Abuela Rosa: old woman, black headscarf.", brief)

    async def test_a_freeform_brief_lets_the_model_supply_the_structure(self):
        async with _client(generate=lambda *_: REWRITTEN_FILM) as client:
            response = await client.post(
                "/api/v1/director/brief/structure", json=REVIEW_BODY,
            )

        result = response.json()
        self.assertEqual(result["method"], "llm")
        self.assertEqual(result["kept"], [])


class CodeFenceTests(unittest.TestCase):
    def test_a_fenced_brief_is_unwrapped(self):
        text = "Here you go:\n```text\nSUBJECT LOCK:\n<Subject 1> is ALWAYS Rosa.\n```\n"

        self.assertIn("Subject 1", strip_code_fences(text))
        self.assertNotIn("```", strip_code_fences(text))

    def test_plain_text_is_untouched(self):
        self.assertEqual(strip_code_fences("SUBJECT LOCK: x"), "SUBJECT LOCK: x")


if __name__ == "__main__":
    unittest.main()
