"""The gallery's "Regenerate with same settings" must act and report.

A Director clip's action used to restore the Director project and then fire a
Studio generation with whatever params the sidebar held, so the button looked
like it did nothing, and it gave no running state and swallowed any failure.
"""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


class GalleryClipRegenerateTests(unittest.TestCase):
    def test_a_director_clip_regenerates_through_its_pipeline(self):
        feed_item = _read("ui", "src", "components", "MainContent", "MediaFeedItem.tsx")

        self.assertIn("const directorPid = meta?.director_pipeline_id", feed_item)
        self.assertIn("const directorClipIndex = meta?.director_clip_index", feed_item)
        self.assertIn(
            "await rerunClipVideo(directorPid, directorClipIndex)", feed_item,
        )
        # The Studio reroll is only the fallback for a plain Studio output.
        self.assertIn("} else {\n        await rerollGeneration()", feed_item)

    def test_the_action_shows_progress_and_a_failure_on_the_card(self):
        feed_item = _read("ui", "src", "components", "MainContent", "MediaFeedItem.tsx")

        self.assertIn("const [rerolling, setRerolling] = useState(false)", feed_item)
        self.assertIn("const [rerollError, setRerollError] = useState<string | null>(null)", feed_item)
        self.assertIn("{rerolling && (", feed_item)
        self.assertIn("{rerollError && (", feed_item)
        # The menu closes on click, so the message has to be visible on the card.
        self.assertIn("title={rerollError}", feed_item)
        self.assertIn("{rerollError}", feed_item)
        self.assertIn(
            "rerolling ? 'Regenerating\\u2026' : 'Regenerate with same settings'",
            feed_item,
        )
        self.assertIn("disabled={rerolling}", feed_item)

    def test_the_info_bar_regenerates_the_same_way(self):
        info_bar = _read("ui", "src", "components", "MainContent", "VideoInfoBar.tsx")

        self.assertIn("const handleReroll = async () => {", info_bar)
        self.assertIn("await rerunClipVideo(directorPid, directorClipIndex)", info_bar)
        self.assertIn("disabled={rerolling}", info_bar)
        self.assertIn("title={rerollError || 'Re-generate with same settings'}", info_bar)
        # No bare store call: that was the silent path.
        self.assertNotIn("onClick={rerollGeneration}", info_bar)

    def test_a_regenerated_clip_keeps_its_place_in_the_film(self):
        """A rerun publishes one output and no clip_output_files map.

        Without the fallback the sidecar lost director_clip_index, so a
        regenerated clip could no longer be tied to its shot.
        """

        pipeline = _read("app", "services", "director_pipeline.py")
        launch = _read("app", "launch.py")

        self.assertIn('"_director_clip_index": clip_index,', pipeline)
        self.assertIn(
            'detached_clip_index = job["params"].get("_director_clip_index")',
            launch,
        )
        self.assertIn(
            'elif isinstance(detached_clip_index, int):\n'
            '                        file_sidecar["director_clip_index"] = detached_clip_index',
            launch,
        )

    def test_the_studio_reroll_is_never_a_silent_no_op(self):
        store = _read("ui", "src", "stores", "useStore.ts")

        self.assertIn("if (meta?.director_pipeline_id) {", store)
        self.assertIn("This clip belongs to a Director pipeline.", store)
        self.assertIn("carries no saved settings", store)


if __name__ == "__main__":
    unittest.main()
