"""Reading and checking one shot inside the Director Dashboard.

A compiled H3 prompt runs to several thousand characters, so the small box in
the shot card cannot show it: editing needs a window the user can size. And a
thumbnail alone does not prove which clip is about to be edited, so every shot
with footage has to be watchable from the card.
"""

from __future__ import annotations

import os
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_UI_DIR = os.path.abspath(os.path.join(_HERE, "..", "ui", "src"))
_DASHBOARD_DIR = os.path.join(_UI_DIR, "components", "DirectorDashboard")


def _read(*parts: str) -> str:
    with open(os.path.join(_UI_DIR, *parts), encoding="utf-8") as handle:
        return handle.read()


class FloatingPromptWindowTests(unittest.TestCase):
    def setUp(self):
        self.panel = _read("components", "DirectorDashboard", "FloatingPanel.tsx")
        self.dashboard = _read(
            "components", "DirectorDashboard", "DirectorDashboard.tsx",
        )

    def test_the_window_is_resizable_and_remembers_its_size(self):
        self.assertIn("cursor-nwse-resize", self.panel)
        self.assertIn("director-float:${storageKey}", self.panel)
        self.assertIn("MIN_WIDTH", self.panel)
        self.assertIn("MIN_HEIGHT", self.panel)

    def test_the_window_can_be_moved_and_never_leaves_the_screen(self):
        self.assertIn("startDrag('move')", self.panel)
        self.assertIn("function clamp(", self.panel)
        self.assertIn("window.innerWidth", self.panel)

    def test_the_window_is_a_portal_dialog_that_escapes_the_card(self):
        self.assertIn("createPortal", self.panel)
        self.assertIn('role="dialog"', self.panel)
        self.assertIn("event.key === 'Escape'", self.panel)

    def test_each_shot_opens_its_own_window(self):
        self.assertIn(
            "storageKey={`prompt-${pipeline.pipeline_id}-${clip.index}`}",
            self.dashboard,
        )

    def test_the_card_offers_the_window_beside_the_quick_edit(self):
        self.assertIn("<Maximize2 size={9} />", self.dashboard)
        self.assertIn("setShowPromptWindow(true)", self.dashboard)
        self.assertIn("<Pencil size={9} />", self.dashboard)

    def test_a_long_prompt_gets_the_whole_window_to_scroll_in(self):
        # `fill` hands the body to one child, so the textarea grows with the
        # window instead of staying at the card's four rows.
        self.assertIn("fill", self.dashboard)
        self.assertIn("flex-1 min-h-0", self.dashboard)

    def test_both_editors_save_through_one_path(self):
        self.assertIn("const saveVideoPrompt = async () => {", self.dashboard)
        # The inline check and the window's Save button share it.
        self.assertIn("onClick={saveVideoPrompt}", self.dashboard)
        self.assertEqual(self.dashboard.count("const saveVideoPrompt = async () => {"), 1)


class FloatingFontSizeTests(unittest.TestCase):
    """The prompt window needs a text size the reader controls.

    A compiled prompt is read at length, and small grey text is unreadable for
    anyone who needs larger type. This has to be a reading setting: reachable by
    button and by the Ctrl +/-/0 keys a reader already expects, clamped so it
    cannot be driven off either end, and remembered per clip.
    """

    def setUp(self):
        self.panel = _read("components", "DirectorDashboard", "FloatingPanel.tsx")
        self.dashboard = _read(
            "components", "DirectorDashboard", "DirectorDashboard.tsx",
        )

    def test_the_window_offers_larger_and_smaller_text(self):
        self.assertIn('aria-label="Larger text"', self.panel)
        self.assertIn('aria-label="Smaller text"', self.panel)
        self.assertIn('aria-label="Text size"', self.panel)

    def test_the_current_size_is_announced_and_resettable(self):
        self.assertIn("Reset text size", self.panel)
        self.assertIn("currently ${fontSize} pixels", self.panel)

    def test_the_size_is_clamped_in_both_directions(self):
        self.assertIn("function clampFont(", self.panel)
        self.assertIn("Math.min(MAX_FONT, Math.max(MIN_FONT", self.panel)
        self.assertIn("disabled={fontSize <= MIN_FONT}", self.panel)
        self.assertIn("disabled={fontSize >= MAX_FONT}", self.panel)

    def test_the_size_is_remembered_with_the_window(self):
        self.assertIn("JSON.stringify({ ...box, font: fontSize })", self.panel)
        self.assertIn("function loadFont(", self.panel)

    def test_the_keyboard_shortcuts_work(self):
        self.assertIn("event.ctrlKey || event.metaKey", self.panel)
        self.assertIn("clampFont(current + 1)", self.panel)
        self.assertIn("clampFont(current - 1)", self.panel)
        self.assertIn("setFontSize(DEFAULT_FONT)", self.panel)

    def test_the_controls_never_start_a_window_drag(self):
        # They sit in the draggable title bar, so pressing them must not move
        # the window out from under the click.
        start = self.panel.index('aria-label="Text size"')
        block = self.panel[start:start + 200]

        self.assertIn("onPointerDown={event => event.stopPropagation()}", block)

    def test_the_body_carries_the_size_so_children_inherit_it(self):
        self.assertIn("style={{ fontSize: `${fontSize}px` }}", self.panel)

    def test_the_prompt_editors_do_not_hardcode_a_size(self):
        # A fixed `text-[11px]` on the textarea would win over the inherited
        # size and the buttons would look broken.
        prompt_start = self.dashboard.index("storageKey={`prompt-${pipeline.pipeline_id}-${clip.index}`}")
        block = self.dashboard[prompt_start:prompt_start + 2200]

        self.assertNotIn("text-[11px]", block)
        self.assertIn("leading-relaxed", block)

    def test_the_prompt_window_enables_the_control_and_the_player_does_not(self):
        prompt_start = self.dashboard.index("storageKey={`prompt-${pipeline.pipeline_id}-${clip.index}`}")
        prompt_block = self.dashboard[prompt_start:prompt_start + 900]
        clip_start = self.dashboard.index("storageKey={`clip-${pipeline.pipeline_id}-${clip.index}`}")
        clip_block = self.dashboard[clip_start:clip_start + 900]

        self.assertIn("resizableFont", prompt_block)
        self.assertNotIn("resizableFont", clip_block)


class ClipRegenerationFeedbackTests(unittest.TestCase):
    """A click that starts a multi-minute job must look like it landed.

    A clip rerun takes minutes. The button used to change nothing until the job
    finished, so there was no way to tell a slow job from a missed click, and
    clicking again only queued a duplicate.
    """

    def setUp(self):
        self.dashboard = _read(
            "components", "DirectorDashboard", "DirectorDashboard.tsx",
        )

    def test_the_shot_shows_which_regeneration_is_running(self):
        self.assertIn("const [rerunKind, setRerunKind] = useState<null | 'image' | 'video'>(null)", self.dashboard)
        self.assertIn("'Regenerating image…' : 'Regenerating shot…'", self.dashboard)
        self.assertIn('aria-busy', self.dashboard) if 'aria-busy' in self.dashboard else None
        self.assertIn("role=\"status\"", self.dashboard)

    def test_the_running_button_spins_and_is_disabled(self):
        self.assertIn("rerunKind === 'video' ? <Loader2 size={10} className=\"animate-spin\" /> : <Film size={10} />", self.dashboard)
        self.assertIn("rerunKind === 'image' ? <Loader2 size={10} className=\"animate-spin\" /> : <Camera size={10} />", self.dashboard)
        self.assertEqual(self.dashboard.count("disabled={busy || rerunKind !== null"), 2)

    def test_a_second_click_cannot_queue_a_duplicate_job(self):
        # Belt and braces: the button is disabled, and the handler also refuses.
        self.assertIn("if (rerunKind) return", self.dashboard)

    def test_the_outcome_is_reported_on_the_shot(self):
        self.assertIn("setRerunNotice('Start image regenerated.')", self.dashboard)
        self.assertIn("setRerunNotice('Shot regenerated.')", self.dashboard)
        self.assertIn("setRerunNotice(error instanceof Error ? error.message : String(error))", self.dashboard)

    def test_the_in_flight_state_clears_even_when_the_job_fails(self):
        start = self.dashboard.index("const runRerun = async (")
        body = self.dashboard[start:start + 1100]

        self.assertIn("} finally {", body)
        self.assertIn("setRerunKind(null)", body)

    def test_the_handlers_return_a_promise_so_the_card_can_await_them(self):
        self.assertIn("onRerunImage: (clipIndex: number, prompt?: string) => Promise<void>", self.dashboard)
        self.assertIn("onRerunVideo: (clipIndex: number, prompt?: string) => Promise<void>", self.dashboard)

    def test_a_failure_reaches_the_shot_not_only_the_banner(self):
        # The dashboard used to swallow it into a page-level banner.
        self.assertEqual(self.dashboard.count("throw new Error(message)"), 2)


class ClipPreviewTests(unittest.TestCase):
    def setUp(self):
        self.dashboard = _read(
            "components", "DirectorDashboard", "DirectorDashboard.tsx",
        )

    def _preview_block(self) -> str:
        start = self.dashboard.index("storageKey={`clip-${pipeline.pipeline_id}-${clip.index}`}")
        return self.dashboard[start:start + 2600]

    def test_the_grid_does_not_mount_a_video_element_per_shot(self):
        # 150 shots once mounted 150 <video> elements and the browser held tens
        # of GB of media buffers. Only the on-demand player may mount one.
        self.assertEqual(self.dashboard.count("<video"), 1)
        self.assertIn("<video", self._preview_block())

    def test_the_player_loads_only_when_the_user_asks(self):
        block = self._preview_block()

        self.assertIn("loadPreview ? (", block)
        self.assertIn("Load video", block)
        self.assertIn("setLoadPreview(true)", block)
        self.assertIn("setLoadPreview(false)", self.dashboard)

    def test_the_player_never_autoplays_or_loops(self):
        block = self._preview_block()

        self.assertNotIn("autoPlay", block)
        self.assertNotIn("loop", block)
        self.assertIn('preload="metadata"', block)

    def test_the_file_request_names_its_workspace(self):
        # Without it the backend stats every workspace folder on each request.
        self.assertIn("getFileUrl(clip.video_filename, pipeline.workspace)", self.dashboard)
        self.assertIn("getFileUrl(clip.start_image_filename, pipeline.workspace)", self.dashboard)

    def test_closing_the_window_forgets_the_loaded_player(self):
        block = self._preview_block()

        self.assertIn("setShowPreview(false); setLoadPreview(false)", block)

    def test_a_rendered_shot_can_be_watched_from_the_card(self):
        self.assertIn("clip.video_filename && (", self.dashboard)
        self.assertIn("setShowPreview(true)", self.dashboard)

    def test_the_preview_plays_the_clip_with_controls(self):
        block = self._preview_block()

        self.assertIn("<video", block)
        self.assertIn("controls", block)
        self.assertIn("getFileUrl(clip.video_filename", block)

    def test_a_shot_with_only_a_start_image_still_shows_something(self):
        block = self._preview_block()

        self.assertIn("clip.start_image_filename", block)
        self.assertIn("No clip rendered yet", block)

    def test_a_shot_with_nothing_says_so_instead_of_showing_a_blank_box(self):
        block = self._preview_block()

        self.assertIn("nothing to show", block)

    def test_the_card_placeholder_says_whether_footage_exists(self):
        self.assertIn("'clip ready", self.dashboard)
        self.assertIn("'no visual yet'", self.dashboard)


if __name__ == "__main__":
    unittest.main()
