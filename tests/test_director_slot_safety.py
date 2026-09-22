"""Fixing a broken shot slot must not mean re-rendering the film.

Deleting a take from the gallery can remove the very file a slot is using for a
shot. The slot then keeps a stale filename, and the rejoin refused the entire
run with "Regenerate missing or invalid video clip(s) 46 before rejoining" -- on
a 150-shot project that reads as "render that shot again" for what is only a
name that moved. Every clip sidecar records the slot it belongs to, so a
surviving take of the same shot can stand in and nothing is rendered.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director_pipeline import (  # noqa: E402
    apply_slot_repoints,
    plan_slot_repoints,
    surviving_takes_by_slot,
)

_LAUNCH = Path(_APP_DIR) / "launch.py"
_CLIENT = Path(_HERE).parent / "ui" / "src" / "api" / "client.ts"
_STORE = Path(_HERE).parent / "ui" / "src" / "stores" / "useStore.ts"
_MAIN_CONTENT = (
    Path(_HERE).parent / "ui" / "src" / "components" / "MainContent" / "MainContent.tsx"
)
_BLOCKED_DIALOG = (
    Path(_HERE).parent / "ui" / "src" / "components" / "MainContent" / "BlockedDeleteDialog.tsx"
)


class SlotRepointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = self.temp.name

    def take(self, name: str, slot: int | None, *, size: int = 2048, stamp: float = 100.0):
        """Write a media file plus the sidecar that records its slot."""

        path = os.path.join(self.folder, name)
        with open(path, "wb") as handle:
            handle.write(b"\0" * size)
        os.utime(path, (stamp, stamp))
        sidecar: dict = {"generation_mode": "video"}
        if slot is not None:
            sidecar["director_clip_index"] = slot
        with open(os.path.join(self.folder, name[: -len(".mp4")] + ".meta.json"), "w", encoding="utf-8") as handle:
            json.dump(sidecar, handle)
        return name

    def state(self, names: list[str]) -> dict:
        return {
            "pipeline_id": "p1",
            "clips": [
                {"index": index, "video_filename": name}
                for index, name in enumerate(names)
            ],
            "_clip_video_files": list(names),
            "output_files": list(names),
        }

    def test_a_deleted_take_is_replaced_by_a_surviving_one_of_the_same_shot(self):
        # Slot 2's recorded file is gone; an earlier take of slot 2 survives.
        survivor = self.take("older-take.mp4", slot=2, stamp=50)
        self.take("other-shot.mp4", slot=9, stamp=90)
        state = self.state(["a.mp4", "b.mp4", "gone.mp4", "d.mp4"])

        plan = plan_slot_repoints(state, self.folder)

        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["index"], 2)
        self.assertEqual(plan[0]["replacement"], survivor)
        self.assertIn("Shot 3", plan[0]["message"])

    def test_nothing_is_planned_when_every_recorded_file_exists(self):
        for index, name in enumerate(("a.mp4", "b.mp4", "c.mp4")):
            self.take(name, slot=index)
        state = self.state(["a.mp4", "b.mp4", "c.mp4"])

        self.assertEqual(plan_slot_repoints(state, self.folder), [])

    def test_a_shot_with_no_surviving_take_is_left_to_fail_loudly(self):
        # Nothing to substitute: the rejoin must still refuse, not guess.
        self.take("other-shot.mp4", slot=7)
        state = self.state(["a.mp4", "gone.mp4"])

        self.assertEqual(plan_slot_repoints(state, self.folder), [])

    def test_the_newest_surviving_take_wins(self):
        self.take("old.mp4", slot=1, stamp=50)
        newer = self.take("new.mp4", slot=1, stamp=500)
        state = self.state(["a.mp4", "gone.mp4"])

        plan = plan_slot_repoints(state, self.folder)

        self.assertEqual(plan[0]["replacement"], newer)

    def sidecar_only(self, name: str, slot: int):
        """A sidecar whose media file is gone, as a delete leaves behind."""

        with open(os.path.join(self.folder, name[: -len(".mp4")] + ".meta.json"), "w", encoding="utf-8") as handle:
            json.dump({"director_clip_index": slot}, handle)

    def test_a_sidecar_without_its_media_is_not_a_candidate(self):
        # A delete can leave the sidecar behind. A name with no media on disk
        # must never be chosen, or the re-point would swap one dead file for
        # another and the rejoin would refuse again.
        self.sidecar_only("gone.mp4", slot=1)
        state = self.state(["a.mp4", "gone.mp4"])

        self.assertEqual(surviving_takes_by_slot(self.folder), {})
        self.assertEqual(plan_slot_repoints(state, self.folder), [])

    def test_a_zero_byte_take_is_not_a_candidate(self):
        self.take("truncated.mp4", slot=1, size=0)
        state = self.state(["a.mp4", "gone.mp4"])

        self.assertEqual(plan_slot_repoints(state, self.folder), [])

    def test_the_dead_name_is_never_chosen_as_its_own_replacement(self):
        # A candidate always has media on disk while the recorded name does not,
        # so they cannot collide -- and the plan says so for every fix.
        self.take("older-take.mp4", slot=2, stamp=50)
        state = self.state(["a.mp4", "b.mp4", "gone.mp4", "d.mp4"])

        plan = plan_slot_repoints(state, self.folder)

        self.assertTrue(plan)
        for fix in plan:
            self.assertNotEqual(fix["replacement"], fix["previous"])
            self.assertNotEqual(fix["previous"], "")

    def test_applying_updates_every_place_the_old_name_appeared(self):
        survivor = self.take("older-take.mp4", slot=1, stamp=50)
        state = self.state(["a.mp4", "gone.mp4", "c.mp4"])
        plan = plan_slot_repoints(state, self.folder)

        apply_slot_repoints(state, plan)

        self.assertEqual(state["clips"][1]["video_filename"], survivor)
        self.assertFalse(state["clips"][1]["video_stale"])
        self.assertEqual(state["_clip_video_files"][1], survivor)
        self.assertNotIn("gone.mp4", state["output_files"])
        self.assertIn(survivor, state["output_files"])

    def test_an_empty_sidecar_slot_is_ignored(self):
        # A file the gallery never tied to a shot cannot stand in for one.
        self.take("unrelated.mp4", slot=None)
        state = self.state(["a.mp4", "gone.mp4"])

        self.assertEqual(plan_slot_repoints(state, self.folder), [])


class RejoinSelfHealWiringTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = (
            Path(_APP_DIR) / "services" / "director_pipeline.py"
        ).read_text(encoding="utf-8")

    def test_the_rejoin_repoints_before_it_refuses(self):
        start = self.pipeline.index("def _rejoin_clips_impl(")
        body = self.pipeline[start:start + 2000]

        self.assertIn("plan_slot_repoints(state, clip_out_dir)", body)
        # Before the check that used to raise about missing clips.
        self.assertLess(
            body.index("plan_slot_repoints(state, clip_out_dir)"),
            body.index("invalid_video_numbers"),
        )

    def test_the_repoint_is_persisted_and_the_state_reloaded(self):
        start = self.pipeline.index("def _rejoin_clips_impl(")
        body = self.pipeline[start:start + 2000]

        self.assertIn("_update_saved_pipeline(out_dir, pid, _apply_repoints)", body)
        self.assertIn("state = load_pipeline_state(out_dir, pid)", body)

    def test_the_repoint_says_what_it_did(self):
        start = self.pipeline.index("def _rejoin_clips_impl(")
        body = self.pipeline[start:start + 2000]

        self.assertIn("fix['message']", body)


class DeleteGuardTests(unittest.TestCase):
    """Deleting the take a shot is using must ask first."""

    def setUp(self):
        self.launch = _LAUNCH.read_text(encoding="utf-8")

    def test_the_endpoint_can_be_forced_but_is_not_by_default(self):
        self.assertIn('def delete_output(name: str, workspace: str = "", force: bool = False):', self.launch)

    def test_an_in_use_file_is_refused_with_the_shot_named(self):
        start = self.launch.index("def delete_output(")
        body = self.launch[start:start + 1800]

        self.assertIn("in_use = _director_slot_using(out_dir, name)", body)
        self.assertIn("if in_use and not force:", body)
        self.assertIn("status_code=409", body)
        self.assertIn("for shot {index + 1}", body)

    def test_the_usage_lookup_covers_clips_and_start_images(self):
        start = self.launch.index("def _director_slot_using(")
        body = self.launch[start:start + 1600]

        self.assertIn('clip.get("video_filename") == name', body)
        self.assertIn('clip.get("start_image_filename") == name', body)

    def test_the_client_passes_force_and_surfaces_the_reason(self):
        client = _CLIENT.read_text(encoding="utf-8")

        self.assertIn("export async function deleteOutput(name: string, workspace?: string, force = false)", client)
        self.assertIn("params.set('force', 'true')", client)

    def test_the_refused_delete_is_held_for_the_users_answer(self):
        store = _STORE.read_text(encoding="utf-8")

        self.assertIn("/is using for shot/.test(message)", store)
        self.assertIn("blockedDelete: { output, message }", store)
        # The file is only removed once the user answered in the app's dialog.
        self.assertIn(
            "api.deleteOutput(blocked.output.name, blocked.output.workspace, true)", store,
        )

    def test_the_question_is_not_a_native_dialog(self):
        store = _STORE.read_text(encoding="utf-8")

        # A native confirm() can answer itself: a service-worker PWA and several
        # webviews dismiss it and return true, so pressing cancel still deleted
        # the take with force. The guard has to own the question.
        self.assertNotIn("window.confirm(", store)
        self.assertNotIn("window.confirm(", _BLOCKED_DIALOG.read_text(encoding="utf-8"))

    def test_cancelling_can_only_keep_the_clip(self):
        store = _STORE.read_text(encoding="utf-8")
        # The implementation, not the interface declaration above it.
        start = store.index("cancelBlockedDelete: () => {")
        body = store[start:start + 260]

        self.assertIn("set({ blockedDelete: null })", body)
        # Nothing on the cancel path may reach the API.
        self.assertNotIn("deleteOutput", body)

    def test_a_refused_delete_does_not_touch_the_gallery(self):
        store = _STORE.read_text(encoding="utf-8")
        start = store.index("deleteSelectedOutput: async (target) => {")
        body = store[start:start + 1500]

        # The blocked branch returns before the local list is trimmed, so a
        # refused delete cannot make the clip look gone while its file remains.
        self.assertLess(
            body.index("set({ blockedDelete: { output, message } })"),
            body.index("get()._forgetDeletedOutput(output)"),
        )


class BlockedDeleteDialogTests(unittest.TestCase):
    """Closing the question must keep the clip, however it is closed."""

    def setUp(self):
        self.dialog = _BLOCKED_DIALOG.read_text(encoding="utf-8")
        self.main = _MAIN_CONTENT.read_text(encoding="utf-8")

    def test_the_app_owns_the_dialog(self):
        self.assertIn("createPortal", self.dialog)
        self.assertIn('role="dialog"', self.dialog)
        self.assertIn('aria-modal="true"', self.dialog)

    def test_the_safe_answer_holds_focus_so_enter_cannot_delete(self):
        self.assertIn("keepRef.current?.focus()", self.dialog)

    def test_escape_and_the_backdrop_both_keep_the_clip(self):
        self.assertIn("event.key !== 'Escape'", self.dialog)
        self.assertIn("if (event.target === event.currentTarget) keep()", self.dialog)

    def test_deleting_needs_a_deliberate_click(self):
        self.assertIn("onClick={() => void confirmBlockedDelete()}", self.dialog)
        self.assertIn("Delete it anyway", self.dialog)
        self.assertIn("Keep the clip", self.dialog)

    def test_the_dialog_is_mounted_on_the_main_screen(self):
        self.assertIn("from './BlockedDeleteDialog'", self.main)
        self.assertIn("<BlockedDeleteDialog />", self.main)


if __name__ == "__main__":
    unittest.main()
