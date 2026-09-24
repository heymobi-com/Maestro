"""Give each spoken line the speaker the project actually recorded.

Measured on clip 38 of magnifica-humanitas, which could not be generated at all:

    H3SpeakerBindingError: MiniMax H3 Omni could not determine which referenced character
    speaks '[Spanish] Báslyamente, un proyecto donde importa muchísimo más la...'

The renderer reads the character from a name or an explicit <Subject N> tag beside the line, and
the names it resolves against are the reference labels ``S1`` and ``S2``. Two of the shot's three
lines carry no such cue (``His voice carries wisdom as he says:`` and an empty ``<scenetrans>``
pair), so no voice can be chosen and the shot is unrenderable.

The speakers were also inverted. The shot's own lines, against the song:

    4:29-4:32  [S2] datos, a órdenes y a rendimientos.          (Ricardo)
    4:32-4:35  [S1] Básicamente, un proyecto donde importa...   (Valeria)
    4:35-4:38  [S1] grandeza de la estructura, de la tecnología, (Valeria)

but the prose cued his line with ``[S1] is seen finishing her thought:`` and hers with ``His
voice carries wisdom as he says:``. The same sentence is ``(S2)`` in the neighbouring shot, so
the map above is the project's, not a guess -- and it is why renders kept putting the woman's
voice on the man's line.

Where the fix belongs was measured too, and the first attempt at it got this wrong: a clip keeps
the planner's draft in ``_director_h3_source_prompt``, and the compiler compiles *that* draft and
rebuilds ``video_prompt`` from it, so editing ``video_prompt`` changes nothing that is rendered.
This script edits the draft and verifies the compiled text -- the one the model is handed -- with
the renderer's own resolver and the project's real reference aliases.
Dry run by default; ``--apply`` writes, keeping a timestamped copy beside the state.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
_APP = os.path.join(_ROOT, "app")
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from models.minimax_h3.ref2va import _canonicalize_ref2va_tagged_dialogue  # noqa: E402
from services.director.h3_dialogue import (  # noqa: E402
    compile_h3_clip_plans,
    h3_dialogue_blocks,
    h3_shared_project_phrases,
    h3_unresolved_speaker_cue_problems,
)
from services.director_pipeline import _apply_h3_music_audio_contract  # noqa: E402

PROJECT = os.path.join(_APP, "outputs", "magnifica-humanitas")
STATE = os.path.join(PROJECT, "_director_pipeline_2d7ed490.json")
CLIP_INDEX = 37

# Reviewed by hand against the song: line 1 is Ricardo's (4:29-4:32), lines 2 and 3 are
# Valeria's (4:32-4:35, 4:35-4:38). The draft had them the other way round and left two
# lines without a cue. ``[Sx]`` is the draft's own marker idiom; the compiler turns it into
# the ``(Sx)`` the renderer resolves.
EXPECTED_CUES = ("S2", "S1", "S1")
REPLACEMENTS = (
    # His line, cued as hers.
    (
        "[S1] is seen finishing her thought:",
        "[S2] is seen finishing his thought:",
    ),
    # Her line, cued as his voice.
    (
        "His voice carries wisdom as he says:",
        "[S1] answers, carrying the same warmth:",
    ),
    # Her last line had no cue at all, only an empty <scenetrans> pair that also
    # split one sentence across two <d> tags.
    (
        "</d> <scenetrans><d>",
        "</d>. [S1] continues: <d>",
    ),
    # Same trap: the closing prose asserts a man for the two lines that are hers.
    (
        "</d></scenetrans>. The camera captures him mid-speech, focusing on his kind yet "
        "grave facial expression",
        "</d>. The camera captures them mid-speech, focusing on their kind yet grave "
        "facial expressions",
    ),
)

FIELD_HEAD_RE = re.compile(
    r"(?mi)^[ \t]*(?:subject_definitions|summary|retention_analysis|"
    r"detailed_description|integrated_multimodal_description|overall_soundscape|"
    r"non_diegetic_music)[ \t]*:"
)

# The renderer resolves the speaker against the reference labels it was handed. Reading them
# from the project's own reference list keeps this check identical to the renderer's.
BINDING_NAME_RE = re.compile(
    r"<Subject\s*(\d+)>\s*\(S\d+\)\s*(?:=|—|--|–)\s*([^,\n<;]{1,60})"
)


def _load():
    with open(STATE, encoding="utf-8") as handle:
        return json.load(handle)


def _renderer_aliases(state: dict) -> tuple[dict, int]:
    """The alias table ``_build_ref2va_character_bindings`` gives the renderer.

    The references this project carries are labelled ``S1``/``S2``, so those, not the
    characters' names, are what a cue has to say.
    """

    from models.minimax_h3.ref2va import _build_ref2va_character_bindings

    items = (state.get("_params_snapshot") or {}).get("minimax_h3_references") or []
    try:
        _, _, _, aliases, count = _build_ref2va_character_bindings(list(items))
    except Exception as exc:
        print(f"  (could not read the project's references: {exc})")
        return {}, 0
    return dict(aliases), int(count)


def _aliases_from_prompt(text: str) -> dict:
    aliases: dict[str, int] = {}
    for raw_subject, raw_name in BINDING_NAME_RE.findall(text):
        name = re.sub(r"\s+", "", raw_name.strip().casefold())
        name = re.sub(r"[^a-z0-9]+", "", name)
        if name:
            aliases.setdefault(name, int(raw_subject))
    return aliases


def _cue_mismatches(compiled: str) -> list[str]:
    """Each spoken line's cue, against the speakers the project recorded.

    Resolvability alone is not the goal: a line cued to the wrong character renders the
    wrong voice, which is the defect this repair is for.
    """

    problems: list[str] = []
    previous_end = 0
    for index, match in enumerate(re.finditer(r"<d>(.*?)</d>", compiled, flags=re.S)):
        window = compiled[max(previous_end, match.start() - 120):match.start()]
        previous_end = match.end()
        if index >= len(EXPECTED_CUES):
            break
        expected = EXPECTED_CUES[index]
        marker = expected[1:]
        if not re.search(rf"S{marker}", window, re.IGNORECASE):
            problems.append(
                f"line {index + 1} is not cued to {expected}: {window.strip()[-70:]!r}"
            )
    if len(EXPECTED_CUES) != len(re.findall(r"<d>", compiled)):
        problems.append(
            f"expected {len(EXPECTED_CUES)} spoken line(s), found "
            f"{len(re.findall(r'<d>', compiled))}"
        )
    return problems


def _compiled_prompt(state: dict, clip: dict, prompt: str) -> str:
    """Reproduce the rewrite the renderer receives: preflight compiles the clip.

    ``_rerun_clip_video_impl`` builds this plan and ``_preflight_h3_director_prompts``
    compiles it, which is the text the model is actually handed.
    """

    snapshot = state.get("_params_snapshot") or {}
    video_model = state.get("video_model") or ""
    plan = {
        "video_prompt": prompt,
        "_director_prompt_user_edited": bool(clip.get("_director_prompt_user_edited")),
        "_director_h3_source_prompt": clip.get("_director_h3_source_prompt") or prompt,
        "_director_h3_compiled_prompt": clip.get("_director_h3_compiled_prompt") or "",
        "_director_dialogue_beats": clip.get("_director_dialogue_beats") or [],
        "_director_subjects_on_screen": clip.get("_director_subjects_on_screen", []) or [],
        "_director_duration_sec": clip.get("_director_duration_sec"),
        "_director_h3_prompt_mode": clip.get("_director_h3_prompt_mode"),
        "_director_h3_model_family": clip.get("_director_h3_model_family"),
        "_director_speaker_registry": clip.get("_director_speaker_registry") or {},
        "_director_project_context": (
            clip.get("_director_project_context") or state.get("scene_description") or ""
        ),
        "_director_opening_blocking": clip.get("_director_opening_blocking", ""),
        "_director_closing_blocking": clip.get("_director_closing_blocking", ""),
        "_director_audio_plan": clip.get("_director_audio_plan") or {},
        "_director_vocal_contract": clip.get("_director_vocal_contract"),
    }
    _apply_h3_music_audio_contract(
        video_model,
        [plan],
        {
            **snapshot,
            "pipeline_type": state.get("pipeline_type") or snapshot.get("pipeline_type") or "music_video",
            "scene_description": snapshot.get("scene_description", ""),
        },
    )
    compile_h3_clip_plans([plan], prompt_modes=["ref2va"])
    return str(plan.get("video_prompt") or "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the change")
    args = parser.parse_args()

    state = _load()
    clips = state.get("clips") or []
    clip = next((item for item in clips if item.get("index") == CLIP_INDEX), None)
    if clip is None:
        print(f"clip {CLIP_INDEX + 1} is not in {os.path.basename(STATE)}")
        return 2

    # Which text the renderer receives is not obvious from the file, and getting it
    # wrong is how the first attempt at this fix changed nothing: the compiler
    # rebuilds ``video_prompt`` from the planner's draft when a draft exists.
    print(f"clip {CLIP_INDEX + 1} in {os.path.basename(STATE)}")
    for key in ("video_prompt", "_director_h3_source_prompt", "_director_h3_compiled_prompt"):
        text = str(clip.get(key) or "")
        unnamed = h3_unresolved_speaker_cue_problems(text) if text else []
        print(
            f"  {key}: {len(text)} characters, "
            f"{len(unnamed)} line(s) whose cue identifies nobody"
        )
    draft = str(clip.get("_director_h3_source_prompt") or "")
    target_name = "_director_h3_source_prompt"
    if not draft:
        draft = str(clip.get("video_prompt") or "")
        target_name = "video_prompt"
    if not draft:
        print("refusing: the clip has no prompt to fix")
        return 1
    print(f"fixing {target_name}: the text the compiler compiles")

    patched = draft
    for old, new in REPLACEMENTS:
        found = patched.count(old)
        print(f"  [{'ok' if found == 1 else 'REFUSED'}] {found} x {old[:64]!r}")
        if found != 1:
            print("refusing: the draft is not in the shape this script was written for")
            return 1
        patched = patched.replace(old, new)

    end_state = dict(clip)
    end_state[target_name] = patched
    # The compiled cache is derived; leaving a stale one lets the compiler decide the
    # saved prompt is newer than the compile it came from and switch sources.
    end_state["_director_h3_compiled_prompt"] = ""
    prompt = str(clip.get("video_prompt") or "")
    frozen = [clip.get("_director_project_context") or "", *h3_shared_project_phrases(clips)]
    in_prompt = [phrase for phrase in frozen if phrase.strip() and phrase in draft]

    print()
    print("=== what the renderer will receive ===")
    compiled = _compiled_prompt(state, end_state, prompt)
    aliases, count = _renderer_aliases(state)
    if not aliases:
        aliases = _aliases_from_prompt(compiled)
    if not count:
        count = max(aliases.values()) if aliases else 1
    print(f"  resolving against {aliases} over {count} subject(s)")
    try:
        _canonicalize_ref2va_tagged_dialogue(compiled, aliases, count)
        render_error = ""
    except Exception as exc:  # the renderer's own refusal, if it still refuses
        render_error = str(exc)
    checks = {
        "spoken lines unchanged": (h3_dialogue_blocks(patched), h3_dialogue_blocks(draft)),
        "six fields in the compiled prompt": (len(FIELD_HEAD_RE.findall(compiled)) >= 6, True),
        "project text intact": (all(phrase in compiled for phrase in in_prompt), True),
        "cues match the recorded speakers": (_cue_mismatches(compiled), []),
        "cues resolved in the compiled prompt": (h3_unresolved_speaker_cue_problems(compiled), []),
        "renderer accepts it": (render_error, ""),
    }
    failed = False
    for name, (got, want) in checks.items():
        ok = got == want
        failed = failed or not ok
        print(f"  {'ok     ' if ok else 'FAILED '} {name}: {got if not ok else ''}")
    if failed:
        print("refusing to write: a check failed")
        return 1

    print()
    print("=== the change ===")
    for old, new in REPLACEMENTS:
        print(f"  - {old[:70]}")
        print(f"  + {new[:70]}")
    print("  the draft now cues line 1 to S2 and lines 2-3 to S1, and the prose no")
    print("  longer asserts a man for the two lines that are hers.")

    if not args.apply:
        print()
        print("dry run: nothing written. Pass --apply to write it.")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{STATE}.bak-{stamp}"
    shutil.copy2(STATE, backup)
    clip[target_name] = patched
    clip["_director_h3_compiled_prompt"] = ""
    with open(STATE, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
    print()
    print(f"backup: {os.path.basename(backup)}")
    print(f"written: {os.path.basename(STATE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
