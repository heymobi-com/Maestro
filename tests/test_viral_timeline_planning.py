"""The viral planner plans the audio timeline instead of a fixed six shots.

Measured on the project the user kept re-running: 16 clips on the timeline, the
first six planned with real subjects and the remaining ten arriving with no
subjects, no audio plan and no duration. The cause was here: this planner ignored
the timeline and derived its shot count from the duration alone, so a 30-second
default asked the model for six shots and the rest of the timeline was filled
deterministically. Every model produced the same six good clips because every model
received the same six-shot instruction.

The timeline is now the authority on how many shots there are, a long one is planned
in batches, and each batch sees the whole timeline as context.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services import llm_service  # noqa: E402
from services.director.planners.viral_video import ViralVideoPlanner  # noqa: E402


SMALL_MODEL = {"name": "Abhiray/gemma-4-E4B-it-heretic-GGUF", "thinking_style": "gemma"}
_LARGE_MODEL = {"name": "Abhiray/qwen-3.6-14B", "thinking_style": "gemma"}
_ASKED = re.compile(r"Output exactly (\d+) shot plans")


def _timeline(count: int, seconds_each: float = 8.0) -> list[dict]:
    return [
        {"start": index * seconds_each, "end": (index + 1) * seconds_each, "label": "verse"}
        for index in range(count)
    ]


class _Recorder:
    """Stands in for the model: answers with as many scenes as it was asked for."""

    def __init__(self, subjects: bool = True) -> None:
        self.prompts: list[str] = []
        self.counts: list[int] = []
        self.subjects = subjects

    def __call__(self, **kwargs):
        # The scene count is stated in the system prompt; the user prompt repeats it as
        # "Create N short, punchy shots". Read both so a wording change cannot make this
        # stand-in report a count nobody asked for.
        system = str(kwargs.get("system_prompt") or "")
        user = str(kwargs.get("prompt") or "")
        self.prompts.append(system)
        match = _ASKED.search(system) or re.search(r"Create (\d+) short, punchy shots", user)
        if match is None:
            raise AssertionError("the planner never asked for a number of shots")
        count = int(match.group(1))
        self.counts.append(count)
        return json.dumps([
            {
                "scene_goal": f"Scene {index + 1} of the batch",
                "scene_type": "escalation",
                "duration_sec": 8,
                "subjects_on_screen": (
                    [{"visual_description": "Valeria at the desk"}] if self.subjects else []
                ),
                "environment": "the studio",
                "action_beats": ["she reads the script"],
                "camera_plan": {"framing": "medium shot"},
                "audio_plan": {"mode": "generated_audio"},
                "ending_beat": "she looks up",
            }
            for index in range(count)
        ])


def _plan(recorder: _Recorder, *, clips, target_duration: int = 30):
    planner = ViralVideoPlanner(llm_generate=recorder)
    return planner.plan(
        concept="Un detrás de cámaras del podcast Magnifica Humanitas.",
        target_duration=target_duration,
        platform="general",
        style="cinematic",
        clips=clips,
    )


class TimelineAuthorityTests(unittest.TestCase):
    def test_a_long_timeline_is_planned_shot_for_shot(self):
        recorder = _Recorder()
        plan = _plan(recorder, clips=_timeline(16))
        self.assertEqual(sum(recorder.counts), 16)
        self.assertEqual(len(plan.shots), 16)
        # Nothing was filled generically: the fallback's own wording would show up here.
        for shot in plan.shots:
            self.assertNotIn("Continue the video at global shot", shot.scene_goal)

    def test_the_scene_count_follows_the_clips_not_the_default_duration(self):
        # 30 seconds would have asked for six shots; the timeline has sixteen.
        recorder = _Recorder()
        _plan(recorder, clips=_timeline(16), target_duration=30)
        self.assertNotIn(6, recorder.counts)
        self.assertEqual(sum(recorder.counts), 16)

    def test_a_small_model_plans_the_timeline_in_shorter_batches(self):
        recorder = _Recorder()
        with patch.object(llm_service, "_active_registry_entry", return_value=SMALL_MODEL):
            _plan(recorder, clips=_timeline(16))
        self.assertEqual(recorder.counts, [8, 8])

    def test_a_larger_model_keeps_the_full_batch(self):
        recorder = _Recorder()
        with patch.object(llm_service, "_active_registry_entry", return_value=_LARGE_MODEL):
            _plan(recorder, clips=_timeline(16))
        self.assertEqual(recorder.counts, [12, 4])

    def test_every_batch_sees_the_whole_timeline(self):
        recorder = _Recorder()
        with patch.object(llm_service, "_active_registry_entry", return_value=SMALL_MODEL):
            _plan(recorder, clips=_timeline(16))
        first, second = recorder.prompts[0], recorder.prompts[1]
        for prompt in (first, second):
            self.assertIn("THE WHOLE TIMELINE", prompt)
            self.assertIn("Clip 16:", prompt)
        self.assertIn("plan ONLY shots 1-8", first)
        self.assertIn("plan ONLY shots 9-16", second)
        # The soundtrack stays the authority on timing: the transcript is immutable.
        self.assertIn("its transcription is immutable", first)

    def test_a_timeline_without_timings_still_plans_every_clip(self):
        recorder = _Recorder()
        plan = _plan(recorder, clips=[{"label": "verse"} for _ in range(16)])
        self.assertEqual(sum(recorder.counts), 16)
        self.assertEqual(len(plan.shots), 16)

    def test_no_timeline_keeps_the_short_form_behaviour(self):
        recorder = _Recorder()
        _plan(recorder, clips=None, target_duration=30)
        self.assertEqual(recorder.counts, [6])
        self.assertNotIn("THE WHOLE TIMELINE", recorder.prompts[0])


class SoundtrackTests(unittest.TestCase):
    """A supplied track drives the timing and is not performed by the characters."""

    def _plan_with_audio(self, audio_path):
        recorder = _Recorder()
        planner = ViralVideoPlanner(llm_generate=recorder)
        plan = planner.plan(
            concept="Un detrás de cámaras del podcast.",
            audio_path=audio_path,
            target_duration=30,
            platform="general",
            style="cinematic",
            clips=_timeline(4),
        )
        return recorder, plan

    def test_an_uploaded_track_drives_the_shots(self):
        # The stand-in echoes the schema example, which is what a real model does: the
        # mode it copies says generated_audio and the supplied track must still win.
        recorder, plan = self._plan_with_audio("app/uploads/song.wav")
        for shot in plan.shots:
            self.assertEqual(shot.audio_plan.mode, "audio_driven")
            self.assertEqual(shot.audio_plan.timing_anchor, "audio")
        self.assertIn("characters do not perform it", recorder.prompts[0])
        self.assertIn("do not move mouths to the music", recorder.prompts[0])

    def test_without_a_track_the_model_still_generates_its_audio(self):
        # The stand-in's echoed mode is honoured here: with no track, generated audio is
        # the right answer and nothing overrides it.
        recorder, plan = self._plan_with_audio(None)
        for shot in plan.shots:
            self.assertEqual(shot.audio_plan.mode, "generated_audio")
            self.assertEqual(shot.audio_plan.timing_anchor, "video")
        self.assertNotIn("characters do not perform it", recorder.prompts[0])

    def test_a_model_that_names_its_own_mode_is_respected(self):
        class Named(_Recorder):
            def __call__(self, **kwargs):
                rows = json.loads(super().__call__(**kwargs))
                for row in rows:
                    row["audio_plan"] = {"mode": "ambient_only"}
                return json.dumps(rows)

        recorder = Named()
        planner = ViralVideoPlanner(llm_generate=recorder)
        plan = planner.plan(
            concept="Ambient piece.",
            audio_path="app/uploads/song.wav",
            clips=_timeline(3),
        )
        for shot in plan.shots:
            self.assertEqual(shot.audio_plan.mode, "ambient_only")


if __name__ == "__main__":
    unittest.main()
