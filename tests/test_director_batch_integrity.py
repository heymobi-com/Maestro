"""A planning batch that never reached the writer must not become a video of placeholders.

Measured on a real 14-clip audio film. Batch 0 planned clips 1-8 (its eighth row was
the stub ``"scene_goal": "Shot 8"``), and batch 1 -- clips 9-14 -- failed after 3.1 s:

    LLM request failed: HTTPConnectionPool(host='127.0.0.1', port=58463): Max retries
    exceeded ... WinError 10048: Only one usage of each socket address

That is an ephemeral-port collision, not a planning problem. The six clips it owed
were filled with the placeholder shot and rendered as generic video, and the pipeline
reported ``completed``. The film therefore broke off mid-way with no error anywhere.

These cases pin the three things that changed: a connection failure is retried, a
batch that still yields nothing usable stops the pass, and a placeholder row is
refused instead of accepted.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.director.planners.base import BasePlanner  # noqa: E402
from services.director.planners import base as base_module  # noqa: E402


class _BatchOnlyPlanner(BasePlanner):
    """A planner that exists only to drive the long-form batching helper."""

    def plan(self, **kwargs):  # pragma: no cover - the driver is what is tested
        raise NotImplementedError


SHORT_FILM = APP / "services/director/planners/short_film.py"

# The exact failure recorded in that project's own llm_drafts entry.
DROPPED_SOCKET = (
    "LLM request failed: HTTPConnectionPool(host='127.0.0.1', port=58463): "
    "Max retries exceeded with url: /v1/chat/completions (Caused by "
    "NewConnectionError('<urllib3.connection.HTTPConnection object>: Failed to "
    "establish a new connection: [WinError 10048] Only one usage of each socket "
    "address (protocol/network address/port) is normally permitted'))"
)


from services.director.planners.base import _is_transient_writer_failure  # noqa: E402


def _planner() -> _BatchOnlyPlanner:
    return _BatchOnlyPlanner()


def _rows(number: int, start: int, batch: list, previous) -> list[dict]:
    return [{"index": start + offset} for offset in range(len(batch))]


class TransientFailureTests(unittest.TestCase):
    def test_a_dropped_socket_is_a_transient_failure(self):
        # It is raised as a plain RuntimeError, so the class alone cannot say so.
        self.assertIsInstance(RuntimeError(DROPPED_SOCKET), RuntimeError)
        self.assertTrue(_is_transient_writer_failure(RuntimeError(DROPPED_SOCKET)))
        for text in (
            "ConnectionResetError(104, 'Connection reset by peer')",
            "[WinError 10053] An established connection was aborted",
            "HTTPConnectionPool: Connection refused",
        ):
            with self.subTest(text=text):
                self.assertTrue(_is_transient_writer_failure(RuntimeError(text)))

    def test_a_planning_error_is_not_retried(self):
        for text in (
            "the model returned 6 of 8 requested rows",
            "compact physical-state proposal could not be materialized",
            "clip 3 names no action, dialogue, or on-screen subject",
        ):
            with self.subTest(text=text):
                self.assertFalse(_is_transient_writer_failure(RuntimeError(text)))

    def test_the_batch_is_retried_and_then_succeeds(self):
        calls: list[int] = []
        original = base_module._WRITER_RETRY_DELAYS_SECONDS
        base_module._WRITER_RETRY_DELAYS_SECONDS = (0.0, 0.0)
        self.addCleanup(
            setattr, base_module, "_WRITER_RETRY_DELAYS_SECONDS", original,
        )

        def flaky(batch_number, start, batch, previous):
            calls.append(start)
            if len(calls) == 1:
                raise RuntimeError(DROPPED_SOCKET)
            return _rows(batch_number, start, batch, previous)

        rows = _planner()._run_checkpointed_json_batches(
            items=list(range(4)),
            batch_size=4,
            checkpoint_key="batches",
            stage="test",
            progress_label="audio-film",
            call_batch=flaky,
            fallback_factory=lambda index, item: {"fallback": index},
            require_planned_rows=True,
        )

        self.assertEqual(calls, [0, 0])
        self.assertEqual([row["index"] for row in rows], [0, 1, 2, 3])


class NeverFabricateTests(unittest.TestCase):
    def test_a_dead_writer_stops_the_pass_instead_of_filling_placeholders(self):
        original = base_module._WRITER_RETRY_DELAYS_SECONDS
        base_module._WRITER_RETRY_DELAYS_SECONDS = (0.0,)
        self.addCleanup(
            setattr, base_module, "_WRITER_RETRY_DELAYS_SECONDS", original,
        )

        def dead(*_args):
            raise RuntimeError(DROPPED_SOCKET)

        with self.assertRaises(RuntimeError) as caught:
            _planner()._run_checkpointed_json_batches(
                items=list(range(4)),
                batch_size=4,
                checkpoint_key="batches",
                stage="test",
                progress_label="audio-film",
                call_batch=dead,
                fallback_factory=lambda index, item: {"fallback": index},
                require_planned_rows=True,
            )

        message = str(caught.exception)
        # It names the clips and says what to do, instead of rendering nonsense.
        self.assertIn("global clips 1-4 of 4", message)
        self.assertIn("Retry planning", message)
        self.assertNotIn("fallback", message)

    def test_a_partial_answer_is_reported_not_padded(self):
        def short(batch_number, start, batch, previous):
            return _rows(batch_number, start, batch, previous)[:2]

        with self.assertRaises(RuntimeError) as caught:
            _planner()._run_checkpointed_json_batches(
                items=list(range(4)),
                batch_size=4,
                checkpoint_key="batches",
                stage="test",
                progress_label="audio-film",
                call_batch=short,
                fallback_factory=lambda index, item: {"fallback": index},
                require_planned_rows=True,
            )

        self.assertIn("returned 2 of 4 shot plans", str(caught.exception))

    def test_planners_that_did_not_opt_in_keep_the_old_bounded_fallback(self):
        # Not every caller can stop: the documented contract for the planners that
        # have their own recovery must stay unchanged.
        def dead(*_args):
            raise RuntimeError("planner exploded")

        rows = _planner()._run_checkpointed_json_batches(
            items=list(range(3)),
            batch_size=3,
            checkpoint_key="batches",
            stage="test",
            progress_label="music-video",
            call_batch=dead,
            fallback_factory=lambda index, item: {"fallback": index},
        )

        self.assertEqual(rows, [{"fallback": 0}, {"fallback": 1}, {"fallback": 2}])


class PlaceholderRowTests(unittest.TestCase):
    def test_the_measured_stub_row_is_refused(self):
        from services.director.planners.short_film import _audio_shot_row_issue

        issue = _audio_shot_row_issue(7, {
            "shot_id": "sf_007",
            "index": 7,
            "scene_goal": "Shot 8",
            "scene_type": "dialogue",
            "action_beats": [],
            "dialogue_beats": None,
            "subjects_on_screen": [],
            "spatial_setup": "",
            "environment": "",
            "ending_beat": "",
        }, [])

        self.assertIn("placeholder scene goal", issue)

    def test_the_generic_fallback_shot_is_refused(self):
        from services.director.planners.short_film import _audio_shot_row_issue

        issue = _audio_shot_row_issue(3, {
            "scene_goal": "Continue audio segment 4",
            "action_beats": ["The visible performance follows the audio"],
            "dialogue_beats": [],
            "subjects_on_screen": [],
        }, [])

        self.assertIn("placeholder action beat", issue)

    def test_a_row_with_nothing_to_show_is_refused(self):
        from services.director.planners.short_film import _audio_shot_row_issue

        issue = _audio_shot_row_issue(1, {
            "scene_goal": "They keep talking",
            "action_beats": [],
            "dialogue_beats": [],
            "subjects_on_screen": [],
            "spatial_setup": "",
            "environment": "",
        }, [])

        self.assertIn("names no action, dialogue, or on-screen subject", issue)

    def test_a_described_empty_place_is_a_legitimate_shot(self):
        # An establishing shot with no cast is not a placeholder.
        from services.director.planners.short_film import _audio_shot_row_issue

        issue = _audio_shot_row_issue(0, {
            "scene_goal": "The studio stands empty before the hosts arrive",
            "action_beats": [],
            "dialogue_beats": [],
            "subjects_on_screen": [],
            "spatial_setup": "The curved desk and two empty chairs, lit only by the LED strip",
        }, [])

        self.assertEqual(issue, "")

    def test_a_planned_row_passes(self):
        from services.director.planners.short_film import _audio_shot_row_issue

        issue = _audio_shot_row_issue(0, {
            "scene_goal": "The host names the episode's question",
            "action_beats": ["she leans forward and raises one hand"],
            "dialogue_beats": [{"speaker_id": "(S1)", "spoken_text": "Hola."}],
            "subjects_on_screen": [{"visual_description": "the woman (S1)"}],
        }, [])

        self.assertEqual(issue, "")

    def test_the_audio_film_passes_the_validator_and_opts_into_failing_loudly(self):
        source = SHORT_FILM.read_text(encoding="utf-8")

        self.assertIn("row_validator=audio_row_issue,", source)
        self.assertIn("require_planned_rows=True,", source)

    def test_every_director_skill_that_renders_a_film_opts_in(self):
        """Podcast and Viral Video had the same silent placeholder path."""

        for name in ("short_film.py", "podcast.py", "viral_video.py"):
            with self.subTest(planner=name):
                source = (
                    APP / "services/director/planners" / name
                ).read_text(encoding="utf-8")
                self.assertIn("require_planned_rows=True,", source)


class RepeatedShotTests(unittest.TestCase):
    """Measured on a 26-clip drama: 11 clips repeated an earlier clip's shot.

    The writer returned the same action beats and the same video_prompt for six
    kitchen clips ("Rosa stands before the wooden table", "The table transforms: the
    plate, cup, and knife disappear") and for five church clips. The brief was varied
    and the planner was a 27B; it simply stopped advancing.
    """

    BEATS = [
        "Rosa stands before the wooden table",
        "The table transforms: the plate, cup, and knife disappear",
    ]

    def _row(self, beats, prompt="Static locked-off medium shot in a small stone kitchen."):
        return {
            "scene_goal": "The table becomes an altar",
            "action_beats": list(beats),
            "video_prompt": prompt,
            "dialogue_beats": [{"speaker_id": "(S1)", "spoken_text": "Y en la mesa..."}],
            "subjects_on_screen": [{"visual_description": "Rosa (S1)"}],
            "environment": "Small stone kitchen in Valdecuna.",
        }

    def test_the_measured_repeat_is_refused(self):
        from services.director.planners.short_film import _audio_shot_row_issue

        row = self._row(self.BEATS)
        issue = _audio_shot_row_issue(2, row, [self._row(self.BEATS), self._row(self.BEATS)])

        self.assertIn("repeats clip 2's action", issue)
        self.assertIn("write what happens in these lines", issue)

    def test_the_first_clip_of_a_film_is_never_a_repeat(self):
        from services.director.planners.short_film import _audio_shot_row_issue

        self.assertEqual(_audio_shot_row_issue(0, self._row(self.BEATS), []), "")

    def test_a_new_action_in_the_same_place_passes(self):
        from services.director.planners.short_film import _audio_shot_row_issue

        previous = self._row(self.BEATS)
        current = self._row(["A villager sets a broken bread on the table"])

        self.assertEqual(_audio_shot_row_issue(1, current, [previous]), "")

    def test_the_same_action_with_a_different_prompt_passes(self):
        """Durations and framings differ per clip, so the prose is not identical."""

        from services.director.planners.short_film import _audio_shot_row_issue

        previous = self._row(self.BEATS, "A medium shot in the kitchen.")
        current = self._row(self.BEATS, "A close-up in the kitchen.")

        self.assertEqual(_audio_shot_row_issue(1, current, [previous]), "")

    def test_a_row_with_no_action_beats_is_left_to_the_other_checks(self):
        from services.director.planners.short_film import _audio_shot_row_issue

        empty = self._row([])

        self.assertEqual(_audio_shot_row_issue(1, empty, [self._row([])]), "")


class RepairTests(unittest.TestCase):
    def test_the_measured_stub_is_repaired_with_one_focused_call(self):
        """Seven good rows and a stub is not a failed batch: only the stub is redone."""

        from services.director.planners.short_film import _audio_shot_row_issue

        def good(index: int) -> dict:
            return {
                "scene_goal": f"Host {index + 1} develops the point",
                "action_beats": ["she gestures toward the data-wall"],
                "dialogue_beats": [],
                "subjects_on_screen": [{"visual_description": "the woman (S1)"}],
            }

        calls: list[tuple[int, int]] = []

        def call_batch(batch_number, start, batch, previous):
            calls.append(("plan", len(batch)))
            rows = [good(start + offset) for offset in range(len(batch))]
            # The measured answer: the last row of the batch was the stub.
            rows[-1] = {
                "scene_goal": f"Shot {start + len(batch)}",
                "action_beats": [],
                "dialogue_beats": None,
                "subjects_on_screen": [],
                "spatial_setup": "",
                "environment": "",
            }
            return rows

        def repair_batch(batch_number, start, batch, previous):
            calls.append(("repair", len(batch)))
            return [good(start + offset) for offset in range(len(batch))]

        rows = _planner()._run_checkpointed_json_batches(
            items=list(range(8)),
            batch_size=8,
            checkpoint_key="short_film_audio_batches",
            stage="short_film_audio_batch",
            progress_label="audio-film",
            call_batch=call_batch,
            repair_batch=repair_batch,
            row_validator=_audio_shot_row_issue,
            repair_batch_size=4,
            repair_attempts=2,
            require_planned_rows=True,
        )

        self.assertEqual(len(rows), 8)
        # One full batch, then exactly one repair for the single refused clip.
        self.assertEqual(calls, [("plan", 8), ("repair", 1)])
        self.assertEqual(rows[-1]["scene_goal"], "Host 8 develops the point")


class PipelineGuardTests(unittest.TestCase):
    def test_the_pipeline_refuses_a_plan_with_placeholder_shots(self):
        # The measured prompts of clips 9-14 of the broken film.
        placeholder = {
            "_director_h3_source_prompt": (
                "SETUP: medium shot | ACTION: The visible performance follows "
                "the audio | ENDING: The performance continues."
            )
        }
        planned = {"_director_h3_source_prompt": "[Shot 1] She leans in and asks the question."}

        from services.director_pipeline import (
            _refuse_unplanned_clips,
            _unplanned_placeholder_clips,
        )

        self.assertEqual(_unplanned_placeholder_clips([planned, placeholder]), [2])
        with self.assertRaises(RuntimeError) as caught:
            _refuse_unplanned_clips("pid", [planned, placeholder, placeholder])

        message = str(caught.exception)
        self.assertIn("clips 2, 3", message)
        self.assertIn("Re-plan the project", message)

    def test_a_fully_planned_project_passes_the_guard(self):
        from services.director_pipeline import _refuse_unplanned_clips

        _refuse_unplanned_clips(
            "pid",
            [
                {"_director_h3_source_prompt": "[Shot 1] The host opens the episode."},
                {"video_prompt": "[Shot 1] He answers, turning to the data-wall."},
                {"_director_h3_source_prompt": ""},
            ],
        )


if __name__ == "__main__":
    unittest.main()
