"""A Director film holds together in newest-shot-first order after a regeneration.

Regenerating a shot writes a brand-new file with a fresh mtime. The gallery sorts
by mtime, so that one shot used to jump to the top of the feed, away from the film
it belongs to; in a 150-shot run its place was then impossible to find. Inside the
film block the shots read shot 150 at the top down to shot 1 at the bottom, the
same direction as the descending feed, so the newest work is nearest the top.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from app.services.gallery_library import GalleryLibrary


class DirectorFilmOrderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folders = [("default", str(self.root / "default"))]
        Path(self.folders[0][1]).mkdir()
        self.library = GalleryLibrary()

    def shot(self, name, *, stamp, film=None, index=None):
        path = self.root / "default" / name
        path.write_bytes(b"media")
        os.utime(path, (stamp, stamp))
        sidecar = {"generation_mode": "video", "params": {}}
        if film is not None:
            sidecar["director_pipeline_id"] = film
        if index is not None:
            sidecar["director_clip_index"] = index
        path.with_suffix(".meta.json").write_text(json.dumps(sidecar), encoding="utf-8")
        return path

    def names(self, **kwargs):
        result = self.library.list(self.folders, **kwargs)
        return [item["name"] for item in result["outputs"]]

    def test_a_regenerated_shot_keeps_its_place_in_the_film(self):
        self.shot("shot0.mp4", stamp=100, film="film1", index=0)
        # Regenerated: much newer than its neighbours.
        self.shot("shot1.mp4", stamp=900, film="film1", index=1)
        self.shot("shot2.mp4", stamp=200, film="film1", index=2)

        self.assertEqual(
            self.names(), ["shot2.mp4", "shot1.mp4", "shot0.mp4"],
        )

    def test_the_film_is_ordered_by_shot_not_by_time(self):
        self.shot("a.mp4", stamp=300, film="film2", index=2)
        self.shot("b.mp4", stamp=100, film="film2", index=0)
        self.shot("c.mp4", stamp=200, film="film2", index=1)

        # Highest shot number first, regardless of when each shot was written.
        self.assertEqual(self.names(), ["a.mp4", "c.mp4", "b.mp4"])

    def test_the_whole_film_sits_at_its_oldest_shot(self):
        self.shot("newer.mp4", stamp=900)
        self.shot("shot0.mp4", stamp=100, film="film3", index=0)
        self.shot("shot1.mp4", stamp=800, film="film3", index=1)

        # The film stays at its own place in the feed rather than moving to the
        # front on the strength of a regenerated shot.
        self.assertEqual(self.names(), ["newer.mp4", "shot1.mp4", "shot0.mp4"])

    def test_items_without_a_film_keep_their_chronological_order(self):
        self.shot("one.mp4", stamp=100)
        self.shot("two.mp4", stamp=300)
        self.shot("three.mp4", stamp=200)

        self.assertEqual(self.names(), ["two.mp4", "three.mp4", "one.mp4"])

    def test_a_shot_without_a_position_does_not_join_the_block(self):
        self.shot("shot0.mp4", stamp=100, film="film4", index=0)
        self.shot("unknown.mp4", stamp=900, film="film4")
        self.shot("shot1.mp4", stamp=200, film="film4", index=1)

        names = self.names()

        # The unplaced file leads the feed; the film keeps its own order.
        self.assertEqual(names[0], "unknown.mp4")
        self.assertEqual(names[1:], ["shot1.mp4", "shot0.mp4"])

    def test_separate_films_do_not_interleave(self):
        self.shot("f1a.mp4", stamp=100, film="filmA", index=0)
        self.shot("f2a.mp4", stamp=100, film="filmB", index=0)
        self.shot("f1b.mp4", stamp=100, film="filmA", index=1)
        self.shot("f2b.mp4", stamp=100, film="filmB", index=1)

        names = self.names()

        # Each film's two shots stay together, whichever direction they read in.
        for first, second in (("f1a.mp4", "f1b.mp4"), ("f2a.mp4", "f2b.mp4")):
            self.assertEqual(abs(names.index(first) - names.index(second)), 1)

    def test_a_new_take_sits_directly_above_the_one_it_replaces(self):
        self.shot("shot0.mp4", stamp=100, film="film6", index=0)
        self.shot("old1.mp4", stamp=200, film="film6", index=1)
        self.shot("shot2.mp4", stamp=300, film="film6", index=2)
        # Regenerated take of shot 1, so both carry the same shot number.
        self.shot("new1.mp4", stamp=900, film="film6", index=1)

        self.assertEqual(
            self.names(),
            ["shot2.mp4", "new1.mp4", "old1.mp4", "shot0.mp4"],
        )

    def test_paging_still_walks_the_reordered_list(self):
        for index in range(5):
            self.shot(f"shot{index}.mp4", stamp=500 - index * 10, film="film5", index=index)

        first = self.library.list(self.folders, limit=2)
        self.assertEqual([item["name"] for item in first["outputs"]], ["shot4.mp4", "shot3.mp4"])
        second = self.library.list(self.folders, limit=2, cursor=first["next_cursor"])
        self.assertEqual([item["name"] for item in second["outputs"]], ["shot2.mp4", "shot1.mp4"])


if __name__ == "__main__":
    unittest.main()
