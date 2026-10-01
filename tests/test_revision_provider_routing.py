"""A correction runs where the user is now, not where the project was planned.

The saved snapshot records the provider the project was planned with -- all 32
plans in a real installation say `local` -- and `_ensure_llm_loaded` lets the
parameters it is handed outvote the current settings. So an editor pointed at a
hosted API still loaded the local GGUF, and that model cannot even come up while
the video model holds the GPU: the editor looked like it ignored the API, and the
failure arrived as "llama-server did not become ready within 80s".
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services import director_pipeline as pipeline  # noqa: E402

DEEPSEEK = "https://api.deepseek.com"
LOCAL_MODEL = "Abhiray/gemma-4-E4B-it-heretic-GGUF"


class RevisionProviderRouting(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out_dir = self.temp.name
        self.pid = "routing01"

    def _save(self, snapshot):
        state = {
            "pipeline_id": self.pid,
            "status": "completed",
            "video_model": "minimax_h3_ref2va_fused_turbo",
            "clips": [{"index": 0, "video_prompt": "a shot body"}],
            "_params_snapshot": {"scene_description": "PROJECT: a studio", **snapshot},
        }
        with open(
            os.path.join(self.out_dir, f"_director_pipeline_{self.pid}.json"),
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(state, handle)

    def _revise(self, live_settings):
        """Run one correction against these live settings; return what the loader got."""

        captured = []
        wgp = types.SimpleNamespace(server_config={"services": live_settings})
        with patch.object(pipeline, "_wgp", wgp), \
             patch.object(
                 pipeline, "_ensure_llm_loaded",
                 lambda params: captured.append(params),
             ), \
             patch("services.llm_service.enhance_prompt", lambda **kw: "rewritten"):
            pipeline.revise_clip_prompt(self.out_dir, self.pid, 0, "fix it")
        self.assertEqual(1, len(captured), "the model was never loaded")
        return captured[0]

    def test_the_snapshot_does_not_decide_where_the_model_runs(self):
        # Planned locally, edited on the API: the API is what must run.
        self._save({
            "llm_provider": "local",
            "llm_model_id": LOCAL_MODEL,
            "llm_device": "cuda",
        })
        params = self._revise({
            "llm_provider": "remote",
            "llm_remote_url": DEEPSEEK,
            "llm_model_id": "deepseek-chat",
        })
        self.assertNotEqual("local", params.get("llm_provider"), params)
        self.assertNotEqual(LOCAL_MODEL, params.get("llm_model_id"), params)
        self.assertNotEqual("cuda", params.get("llm_device"), params)

    def test_the_separate_correction_model_is_still_the_override(self):
        self._save({"llm_provider": "local", "llm_model_id": LOCAL_MODEL})
        params = self._revise({
            "llm_provider": "remote",
            "llm_remote_url": DEEPSEEK,
            "llm_model_id": "deepseek-chat",
            "revision_llm_model_id": "deepseek-chat",
        })
        self.assertEqual("deepseek-chat", params.get("llm_model_id"))

    def test_nothing_is_sent_when_there_is_no_override(self):
        # An empty mapping makes the loader read the live settings, which is the
        # whole point: no stale routing travels with the request.
        self._save({"llm_provider": "local", "llm_model_id": LOCAL_MODEL})
        params = self._revise({
            "llm_provider": "remote",
            "llm_remote_url": DEEPSEEK,
            "llm_model_id": "deepseek-chat",
        })
        self.assertEqual({}, params)


if __name__ == "__main__":
    unittest.main()
