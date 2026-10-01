"""A hosted model name is refused before it becomes a Hugging Face download.

The provider and the model are two fields, and a mismatch between them produced an
error that named neither: a local server asked Hugging Face for
``huggingface.co/deepseek-flash/resolve/main/deepseek-flash-Q4_K_S.gguf`` and
reported "401 Client Error ... Invalid username or password" when DeepSeek had
simply been left in the correction field while the provider was still local.
"""

import os
import sys
import unittest


_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services import llm_service  # noqa: E402
from services.llm_model_choice import (  # noqa: E402
    is_local_repo,
    local_model_problem,
    require_local_repo,
)


class WhatALocalServerCanLoad(unittest.TestCase):
    def test_a_local_repo_or_file_is_accepted(self):
        for model_id in (
            "Abhiray/gemma-4-E4B-it-heretic-GGUF",
            "Jiunsong/supergemma4-26b-uncensored-gguf-v2",
            "C:/models/thing.gguf",
            "C:\\models\\thing.gguf",
            "/models/thing.gguf",
        ):
            with self.subTest(model_id=model_id):
                self.assertTrue(is_local_repo(model_id))
                self.assertEqual("", local_model_problem(model_id))

    def test_a_hosted_model_name_is_refused(self):
        for model_id in ("deepseek-flash", "deepseek-v4-pro", "gpt-4o"):
            with self.subTest(model_id=model_id):
                self.assertFalse(is_local_repo(model_id))
                problem = local_model_problem(model_id)
                self.assertIn(model_id, problem)
                # The message has to name the fields, because the mistake is theirs.
                self.assertIn("LLM Provider", problem)
                self.assertIn("Correction LLM Model", problem)

    def test_an_empty_id_is_not_reported_as_a_problem(self):
        # Callers decide what an empty model means; this rule is not that decision.
        self.assertEqual("", local_model_problem(""))
        self.assertEqual("", local_model_problem(None))

    def test_a_registry_entry_is_accepted_even_without_a_slash(self):
        registry = getattr(llm_service, "MODEL_REGISTRY", {})
        for model_id in registry:
            with self.subTest(model_id=model_id):
                self.assertEqual("", local_model_problem(model_id))


class TheDownloadPathRefusesIt(unittest.TestCase):
    def test_a_hosted_name_never_reaches_hugging_face(self):
        import tempfile

        with tempfile.TemporaryDirectory() as cache:
            with self.assertRaises(RuntimeError) as caught:
                llm_service._download_gguf("deepseek-flash", "x.gguf", cache)
        message = str(caught.exception)
        self.assertIn("cannot be loaded locally", message)
        self.assertIn("deepseek-flash", message)

    def test_require_returns_the_id_it_accepts(self):
        self.assertEqual(
            "owner/model-GGUF", require_local_repo("owner/model-GGUF")
        )


if __name__ == "__main__":
    unittest.main()
