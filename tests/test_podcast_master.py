"""The podcast master joins intro/episode/outro and writes MP3 metadata.

These tests keep the command construction pure and also run one real ffmpeg
master so chapter and cover embedding cannot regress silently.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from services.podcast_master import (  # noqa: E402
    build_command,
    build_ffmetadata,
    normalize_chapters,
)

ROOT = Path(__file__).resolve().parents[1]
COVER = ROOT / "app" / "favicon.png"


class PodcastMasterTests(unittest.TestCase):
    def test_chapters_are_ordered_and_bad_rows_are_dropped(self):
        self.assertEqual(
            normalize_chapters([
                {"start": 20, "title": "Second"},
                {"start": "bad", "title": "Skip"},
                {"start": 0, "title": "First"},
                {"start": 30, "title": ""},
            ]),
            [(0.0, "First"), (20.0, "Second")],
        )

    def test_ffmetadata_carries_tags_and_chapters(self):
        metadata = build_ffmetadata(
            {"title": "Episode", "artist": "Maestro"},
            [(0.0, "Intro"), (12.5, "Segment")],
            30.0,
        )
        self.assertIn("title=Episode", metadata)
        self.assertIn("artist=Maestro", metadata)
        self.assertIn("[CHAPTER]", metadata)
        self.assertIn("START=0", metadata)
        self.assertIn("START=12500", metadata)
        self.assertIn("END=30000", metadata)

    def test_command_joins_normalises_and_embeds_cover(self):
        command = build_command(
            "ffmpeg",
            ["intro.wav", "episode.wav", "outro.wav"],
            "cover.png",
            "chapters.ffmetadata",
            "out.mp3",
            loudness=True,
            metadata={"title": "Episode"},
        )
        joined = " ".join(command)
        self.assertIn("concat=n=3", joined)
        self.assertIn("loudnorm=I=-16", joined)
        self.assertIn("-map 3:v", joined)
        self.assertIn("attached_pic", joined)
        self.assertIn("-map_chapters", joined)
        self.assertIn("-metadata title=Episode", joined)

    def test_real_master_writes_mp3_with_chapters(self):
        from shared.utils.video_decode import _resolve_media_binary

        ffmpeg = _resolve_media_binary("ffmpeg")
        ffprobe = _resolve_media_binary("ffprobe")
        if not ffmpeg or not ffprobe:
            self.skipTest("ffmpeg/ffprobe are not available")
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            for name, frequency in (("intro", 440), ("episode", 550), ("outro", 660)):
                subprocess.run(
                    [
                        ffmpeg, "-y", "-v", "error",
                        "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=1",
                        "-ar", "48000", "-ac", "2", str(temp_path / f"{name}.wav"),
                    ],
                    check=True,
                )
            metadata_path = temp_path / "chapters.ffmetadata"
            metadata_path.write_text(
                build_ffmetadata(
                    {"title": "Test Episode", "artist": "Maestro"},
                    [(0.0, "Intro"), (1.0, "Episode"), (2.0, "Outro")],
                    3.0,
                ),
                encoding="utf-8",
            )
            cover = str(COVER) if COVER.is_file() else ""
            out_path = temp_path / "episode.mp3"
            command = build_command(
                ffmpeg,
                [str(temp_path / "intro.wav"), str(temp_path / "episode.wav"), str(temp_path / "outro.wav")],
                cover,
                str(metadata_path),
                str(out_path),
                loudness=True,
                metadata={"title": "Test Episode", "artist": "Maestro"},
            )
            subprocess.run(command, check=True, capture_output=True, timeout=120)
            self.assertTrue(out_path.is_file())
            probe = subprocess.run(
                [
                    ffprobe, "-v", "error", "-show_chapters",
                    "-show_entries", "format_tags=title,artist",
                    "-of", "json", str(out_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            data = json.loads(probe.stdout)
            self.assertEqual(len(data.get("chapters") or []), 3)
            self.assertEqual(data["format"]["tags"]["title"], "Test Episode")
            self.assertEqual(data["format"]["tags"]["artist"], "Maestro")


if __name__ == "__main__":
    unittest.main()
