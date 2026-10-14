"""The H3 native shot plan is grammar-enforced, and the grammar needs thinking off.

Measured, on a real run: the shot count is only ever enforced by the JSON grammar. Asked for 16-22
shots without one, the writer produced 37 and stopped on the 32000-token ceiling (finish=length,
148,599 characters), the last shot was cut mid-object, json_repair salvaged it as an empty item, and
the plan was rejected: "returned 37 shots; expected 16-30".

The grammar reaches a writer only when the call asks for no reasoning -- `BasePlanner._call_llm_json`
attaches the schema on the first attempt just for `thinking_budget == 0` (see
tests/test_call_llm_json_grammar.py). That makes the argument below load-bearing: with
`thinking_budget=None` a gemma-style writer resolves to 4096 and the first attempt goes out
unconstrained, which is the run described above. The other half of the problem lives with the writer,
not here: an endpoint without a grammar decoder (Strata) cannot enforce a schema at all.
"""
from __future__ import annotations

import os
import re
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

from services.director.planners.short_film import _shot_list_schema  # noqa: E402

PLANNER = os.path.join(ROOT, "app", "services", "director", "planners", "short_film.py")


def _body(source: str, name: str) -> str:
    """One method's source, from its def to the next method at the same indent."""

    start = source.index(f"def {name}(")
    rest = source[start + 1:]
    following = re.search(r"\n    def ", rest)
    return source[start:start + 1 + (following.start() if following else len(rest))]


def _calls(body: str) -> list[str]:
    """The argument text of every self._call_llm_json(...) call in a body."""

    found: list[str] = []
    for match in re.finditer(r"self\._call_llm_json\(", body):
        index, depth = match.end(), 1
        while depth and index < len(body):
            if body[index] == "(":
                depth += 1
            elif body[index] == ")":
                depth -= 1
            index += 1
        found.append(body[match.end():index - 1])
    return found


class H3NativePlanGrammarTests(unittest.TestCase):
    def setUp(self):
        with open(PLANNER, encoding="utf-8") as handle:
            self.body = _body(handle.read(), "_plan_story_h3_native")
        self.calls = _calls(self.body)

    def test_the_native_plan_asks_for_the_grammar(self):
        with_schema = [c for c in self.calls if "json_schema=schema" in c]
        self.assertTrue(with_schema, "the native plan must carry its shot-list schema")
        for args in with_schema:
            self.assertIn("thinking_budget=0", args)
            self.assertNotIn(
                "thinking_budget=None",
                args,
                "None resolves to 4096 on a gemma-style writer, which drops the grammar and with it "
                "the 16-22 shot window: measured at 37 shots and a 32000-token truncation",
            )

    def test_the_whole_plan_repair_asks_for_the_grammar_too(self):
        repairs = [c for c in self.calls if "_configure_native_schema" in c]
        self.assertTrue(repairs, "the repair pass must carry its schema")
        for args in repairs:
            self.assertIn("thinking_budget=0", args)

    def test_no_schema_carrying_call_in_this_function_leaves_thinking_on(self):
        for args in self.calls:
            if "json_schema" in args:
                self.assertNotIn(
                    "thinking_budget=None",
                    args,
                    "a schema sent with thinking on is a schema the writer never sees",
                )

    def test_the_window_the_grammar_enforces_is_really_there(self):
        # The grammar is only worth forcing if it carries the count it enforces.
        schema = _shot_list_schema(
            min_items=16,
            max_items=22,
            required=["shot_number", "video_prompt"],
            include_image_fields=False,
        )
        self.assertEqual(schema["type"], "array")
        self.assertEqual(schema["minItems"], 16)
        self.assertEqual(schema["maxItems"], 22)
        self.assertFalse(schema["items"]["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
