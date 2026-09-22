"""Rebind the spoken lines of a saved prompt to the speaker its plan named.

The speaker registry of an older project assigned one label per *key* instead of
per participant: ``director_identity_s1``/``(s1)`` and ``director_identity_s2``/
``(s2)`` became ``(S1)``..``(S4)`` for a two-person cast. The compiler then bound
each line through that registry, so a beat whose plan said ``(S1)`` reached the
model as ``(S2)`` -- the wrong face spoke -- and a line whose key did not resolve
at all was numbered by its position in the shot, which is where ``(S3)`` came
from. Measured on one project: 135 of its 177 clips had a line bound to a speaker
the cast does not contain.

The compiler and the registry are fixed, so new prompts are correct. This repairs
the prompts already saved: every line takes the speaker of the plan's own beat
for that line, resolved through the fixed registry. The spoken words never
change -- only the ``(S2)`` in front of them.

Usage:
    python scripts/rebind_prompt_speakers.py <outputs_folder> [...] [--apply]

Dry-run by default. ``--apply`` copies each state file to ``.bak-<timestamp>``
first and restores the original modification time, which other tests use to
discover their project fixtures. A shot whose lines no longer match its plan's
beats in order is reported and left alone.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from services.director.h3_dialogue import (  # noqa: E402
    _build_stable_speaker_registry,
    _speaker_registry_entry,
    h3_dialogue_blocks,
)

# The binding a compiled body writes ahead of a line: "(S2) speaks calmly: <d>".
_MARKER_RE = re.compile(
    r"[(（]\s*(?:Speaker[_ ]?|S)\s*\d+\s*[)）](?=\s*[^<>]{0,80}?<d>)",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _words(value: object) -> set[str]:
    return set(_WORD_RE.findall(str(value or "").casefold()))


def _line_words(block: str) -> set[str]:
    """The spoken words of a ``<d>`` block, without its tag or language mark."""

    inner = re.sub(r"</?d>", " ", str(block or ""))
    inner = re.sub(r"^\s*\[[^\]]*\]\s*", "", inner)
    return _words(inner)


def _state_files(folder: str) -> list[str]:
    found = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if (
                    entry.is_file()
                    and entry.name.startswith("_director_pipeline_")
                    and entry.name.endswith(".json")
                ):
                    found.append(entry.path)
    except OSError:
        pass
    return sorted(found)


def _planned_beats(clip: dict) -> list:
    planned = clip.get("planned_clip") or {}
    return list(planned.get("_director_dialogue_beats") or [])


def _label_for(registry: dict, key: str) -> str:
    from services.director.h3_dialogue import _label_encoded_in_key

    entry = _speaker_registry_entry(registry, key)
    if entry and entry[0]:
        return entry[0]
    return _label_encoded_in_key(key)


def _rebind(prompt: str, beats: list, registry: dict) -> tuple[str, list[str], bool]:
    """The prompt rebound, what changed, and whether every line matched a beat.

    A plan often splits one line into several beats, so a beat's words are matched
    against the line as a whole: containment is the strong signal, shared words
    the weak one. A line no beat explains is left alone rather than guessed at.
    """

    blocks = h3_dialogue_blocks(prompt)
    markers = list(_MARKER_RE.finditer(prompt))
    if not blocks or len(markers) != len(blocks):
        return prompt, [], False

    candidates = []
    for beat in beats or []:
        words = _words(beat.get("spoken_text"))
        if words:
            candidates.append((words, _label_for(registry, str(beat.get("speaker_id") or ""))))

    used: set[int] = set()
    wanted: list[str] = []
    for block in blocks:
        line = _line_words(block)
        best_index, best_score, best_label = -1, 0.0, ""
        for index, (words, label) in enumerate(candidates):
            if index in used or not label:
                continue
            score = len(line & words) / max(1, len(line | words))
            if line and (line <= words or words <= line):
                score = max(score, 0.95)
            if score > best_score:
                best_index, best_score, best_label = index, score, label
        if best_index < 0 or best_score < 0.6:
            return prompt, [], False
        used.add(best_index)
        wanted.append(best_label)

    changes = []
    out = prompt
    for marker, target in reversed(list(zip(markers, wanted))):
        if marker.group(0).strip().casefold() == target.casefold():
            continue
        changes.append(f"{marker.group(0).strip()} -> {target}")
        out = out[: marker.start()] + target + out[marker.end() :]
    return out, list(reversed(changes)), True


def _repair_state(path: str, apply: bool) -> dict:
    state = json.load(open(path, encoding="utf-8"))
    clips = state.get("clips") or []
    repaired = []
    unmatched = []
    already = []
    for clip in clips:
        prompt = str(clip.get("video_prompt") or "")
        if not h3_dialogue_blocks(prompt):
            continue
        beats = list(clip.get("_director_dialogue_beats") or []) or _planned_beats(clip)
        if not beats:
            continue
        registry = _build_stable_speaker_registry([{
            "_director_dialogue_beats": beats,
            "_director_subjects_on_screen": clip.get("_director_subjects_on_screen") or [],
            "_director_speaker_registry": clip.get("_director_speaker_registry") or {},
        }])
        fixed, changes, matched = _rebind(prompt, beats, registry)
        if not matched:
            unmatched.append(clip.get("index"))
            continue
        if not changes:
            already.append(clip.get("index"))
            continue
        repaired.append({
            "index": clip.get("index"),
            "changes": changes,
            "chars": f"{len(prompt)} -> {len(fixed)}",
            "prompt": fixed,
        })
    report = {
        "path": path,
        "clips": len(clips),
        "repaired": repaired,
        "unmatched": unmatched,
        "already": already,
        "backups": 0,
    }
    if apply and repaired:
        for found in repaired:
            clips[found["index"]]["video_prompt"] = found["prompt"]
        original_mtime = os.path.getmtime(path)
        shutil.copy2(path, path + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        os.utime(path, (original_mtime, original_mtime))
        report["backups"] = 1
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folders", nargs="+", help="project output folders")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the changes (default: report only)",
    )
    args = parser.parse_args(argv)

    mode = "APPLY" if args.apply else "DRY RUN (nothing is written)"
    print(f"=== {mode} ===")
    totals = {"states": 0, "repaired": 0, "changes": 0}
    for folder in args.folders:
        for path in _state_files(folder):
            report = _repair_state(path, args.apply)
            if not report["repaired"]:
                continue
            totals["states"] += 1
            totals["repaired"] += len(report["repaired"])
            totals["changes"] += sum(len(c["changes"]) for c in report["repaired"])
            print()
            print(f"  {os.path.basename(path)}")
            print(f"      clips                       : {report['clips']}")
            print(f"      con speaker equivocado       : {len(report['repaired'])}")
            print(f"      lineas rebindicadas          : {totals['changes']}")
            print(f"      ya estaban bien              : {len(report['already'])}")
            for found in report["repaired"][:10]:
                joined = ", ".join(found["changes"])
                print(f"      clip {found['index']:<4} {joined}  ({found['chars']})")
            if report["unmatched"]:
                print(
                    "      sin plan que los explique    : "
                    f"{len(report['unmatched'])} {report['unmatched'][:12]}"
                )
            if report["backups"]:
                print("      copia de seguridad           : si (.bak-<fecha>)")
    print()
    print("=== resumen ===")
    print(f"  estados de proyecto reparados : {totals['states']}")
    print(f"  clips rebindicados            : {totals['repaired']}")
    print(f"  lineas cambiadas              : {totals['changes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
