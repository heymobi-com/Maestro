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
        reroll = _read("ui", "src", "lib", "directorReroll.ts")

        # The card hands the decision to our own hook...
        self.assertIn("useDirectorAwareReroll({", feed_item)
        # ...and the decision itself lives in our module.
        self.assertIn("const directorPid = meta?.director_pipeline_id", reroll)
        self.assertIn("const directorClipIndex = meta?.director_clip_index", reroll)
        self.assertIn(
            "await rerunClipVideo(directorPid, directorClipIndex)", reroll,
        )
        # The Studio reroll is only the fallback for a plain Studio output.
        self.assertIn("} else {\n        await rerollGeneration()", reroll)

    def test_the_action_shows_progress_and_a_failure_on_the_card(self):
        feed_item = _read("ui", "src", "components", "MainContent", "MediaFeedItem.tsx")
        badges = _read("ui", "src", "components", "MainContent", "DirectorTakeBadges.tsx")
        reroll = _read("ui", "src", "lib", "directorReroll.ts")

        self.assertIn("const [rerolling, setRerolling] = useState(false)", reroll)
        self.assertIn("const [rerollError, setRerollError] = useState<string | null>(null)", reroll)
        self.assertIn("<RerollStatus rerolling={rerolling} error={rerollError} />", feed_item)
        self.assertIn("{rerolling && (", badges)
        self.assertIn("{error && (", badges)
        # The menu closes on click, so the message has to be visible on the card.
        self.assertIn("title={error}", badges)
        self.assertIn("{error}", badges)
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
        regenerated clip could no longer be tied to its shot. The three readings it needs are
        resolved by `publish_position`, which is where they live now -- the batch keeps only the
        unpacking -- so this asserts the wiring and then the values it produces.
        """

        pipeline = _read("app", "services", "director_pipeline.py")
        launch = _read("app", "launch.py")

        self.assertIn('"_director_clip_index": clip_index,', pipeline)
        self.assertIn(
            "from services.director.resume_prefix import publish_position", launch,
        )
        self.assertIn(
            "detached_clip_index, supersedes, clip_index_offset = publish_position(job[\"params\"])",
            launch,
        )
        self.assertIn("elif isinstance(detached_clip_index, int):", launch)
        self.assertIn(
            'file_sidecar["director_clip_index"] = detached_clip_index',
            launch,
        )

    def test_the_position_reader_returns_what_the_sidecar_writes(self):
        """The rule itself, exercised rather than restated."""

        import sys

        app_dir = Path(__file__).resolve().parents[1] / "app"
        if str(app_dir) not in sys.path:
            sys.path.insert(0, str(app_dir))
        from services.director.resume_prefix import publish_position, director_clip_offset

        self.assertEqual(
            publish_position({
                "_director_clip_index": 4,
                "_director_supersedes": "take1.mp4",
                "_director_clip_offset": 3,
            }),
            (4, "take1.mp4", 3),
        )
        # A batch that states nothing answers with nothing, rather than guessing a position.
        self.assertEqual(publish_position({}), (None, None, 0))
        # A resumed batch records its offset as text when a saved project is read back.
        self.assertEqual(director_clip_offset({"_director_clip_offset": "7"}), 7)
        self.assertEqual(director_clip_offset({"_director_clip_offset": "seven"}), 0)

    def test_the_studio_reroll_is_never_a_silent_no_op(self):
        store = _read("ui", "src", "stores", "useStore.ts")
        routing = _read("ui", "src", "stores", "directorPlanRouting.ts")

        # The store asks our own module why the reroll cannot run...
        self.assertIn("const rerollBlocked = studioRerollBlockedReason(get().selectedOutputMeta)", store)
        self.assertIn("if (rerollBlocked) throw new Error(rerollBlocked)", store)
        # ...and the reasons themselves live with our code.
        self.assertIn("This clip belongs to a Director pipeline.", routing)
        self.assertIn("carries no saved settings", routing)


if __name__ == "__main__":
    unittest.main()
