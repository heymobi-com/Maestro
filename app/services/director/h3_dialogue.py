"""Deterministic MiniMax H3 dialogue compilation and validation.

H3 treats ``<d>[Language] ...</d>`` blocks as an audio-generation contract.
These helpers keep that contract out of best-effort prompt rewriting: structured
Director dialogue is canonical, existing blocks are replaced as whole units,
and malformed or contradictory prompts are rejected before GPU generation.
"""

from __future__ import annotations

import difflib
import math
import re
from collections import Counter
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

from services.dialogue_timing import (
    DIALOGUE_MAX_WORDS_PER_SECOND,
    h3_dialogue_schedule,
)
from services.director import audio_driven_transcript as _transcript
from services.director import performance_expression as _expression
from services.director.h3_reference_subjects import (
    carry_identity_bindings,
    planner_subject_for_role,
)
from services.director.h3_single_shot_times import strip_loose_cut_times
from services.director.h3_field_layout import restore_h3_field_layout
from services.director.h3_subject_rows import (
    merge_subject_rows_for_the_same_speaker as _merge_subject_rows_for_the_same_speaker,
    split_multi_speaker_subject_rows as _split_multi_speaker_subject_rows,
)
from services.director import h3_speaker_cues
from services.director.project_brief import clip_project_context as _clip_context
from services.director.project_brief import section_heading as _section_heading
from services.h3_prompt_budget import (
    H3_ENHANCED_TEXT_TOKEN_TARGET as _H3_DIRECTOR_TEXT_TOKEN_BUDGET,
    fit_h3_base_prompt,
    h3_prompt_token_count,
)
from services.text_integrity import repair_text
from .physical_continuity import identity_only_description, normalize_physical_state


class H3DialogueContractError(ValueError):
    """Raised when an H3 prompt cannot be made safe for native speech."""


_H3_DIALOGUE_TOKEN_RE = re.compile(r"<\s*(/?)\s*d\s*>", re.IGNORECASE)
_H3_STRICT_DIALOGUE_RE = re.compile(
    r"<d>\s*\[([^\]\r\n]+)\]\s+(.+?)\s*</d>",
    re.IGNORECASE | re.DOTALL,
)
_H3_SPOKEN_INSTRUCTION_RE = re.compile(
    r"\b(?:says?|said|speaks?|spoke|speaking|talks?|talking|tells?|told|"
    r"shouts?|shouting|whispers?|whispering|calls?|calling|recites?|"
    r"reciting|utters?|uttering|sings?|sang|singing|lip[ -]?syncs?)\b",
    re.IGNORECASE,
)
_H3_TEXTUAL_SPEECH_SUBJECT_RE = re.compile(
    r"\b(?:sign|poster|label|banner|screen|subtitle|caption|placard|"
    r"note|letter|message|card|word|title|speech\s+bubble|bubble|"
    r"display|text)\s+(?:that\s+)?$",
    re.IGNORECASE,
)
_H3_MUSIC_SUBJECT_CONTINUATION_RE = re.compile(
    r"^(?:(?:the|a|an)\s+)?(?:(?:non[ -]?diegetic|background|"
    r"orchestral|cinematic|instrumental|hopeful|heroic|soaring|gentle|"
    r"peaceful|sustained|uplifting|symphonic|dramatic)\s+)*(?:music|"
    r"score|soundtrack|orchestra|strings?|violins?|violas?|cellos?|"
    r"brass|horns?|piano|guitar|percussion|instruments?|melodies?|"
    r"themes?|chords?|notes?|symphonies|songs?)\b",
    re.IGNORECASE,
)
_H3_MUSIC_FOLLOWTHROUGH_RE = re.compile(
    r"^(?:then\s+)?(?:is|was|becomes?|became|swells?|builds?|rises?|"
    r"crescendo(?:es)?|resolves?|fades?|plays?|underscores?|accompanies|"
    r"continues?|starts?|begins?|ends?|sustains?|opens?|climaxes?)\b",
    re.IGNORECASE,
)
_H3_INDEPENDENT_CLAUSE_START_RE = re.compile(
    r"^(?:(?:the|a|an)\s+)?[A-Za-z][\w’'-]*(?:\s+[A-Za-z][\w’'-]*){0,2}"
    r"\s+[A-Za-z][\w’'-]+\b",
    re.IGNORECASE,
)
_H3_MUSIC_INSTRUCTION_RE = re.compile(
    r"\b(?P<subject>(?:(?:non[ -]?diegetic|background|orchestral|cinematic|"
    r"instrumental|hopeful|heroic|soaring|gentle|peaceful|sustained|"
    r"uplifting|symphonic|dramatic)\s+)*(?:music|score|soundtrack))\b"
    r"\s+(?:(?:slowly|gently|gradually|softly|quietly|briskly|"
    r"steadily|then)\s+){0,3}"
    r"(?P<verb>is|was|becomes|became|swells?|builds?|rises?|crescendo(?:es)?|"
    r"resolves?|fades?|plays?|underscores?|accompanies|continues?|starts?|"
    r"begins?|ends?|sustains?|opens?|climaxes?)\b",
    re.IGNORECASE,
)
_H3_MUSIC_DIRECTIVE_RE = re.compile(
    r"\b(?:add|compose|generate|include|introduce|play|write|create)\s+"
    r"(?:a|an|the|some)?\s*(?:non[ -]?diegetic\s+|background\s+|"
    r"orchestral\s+|cinematic\s+|instrumental\s+)*(?:music|score|soundtrack)\b",
    re.IGNORECASE,
)
_H3_VOCAL_SECTION_RE = re.compile(
    r"\s*(?:DIALOGUE AND VOCAL PERFORMANCE|"
    r"SILENCE AND VOCAL PERFORMANCE)\s*:.*?"
    r"(?=\b(?:FINAL BLOCKING|overall_soundscape|"
    r"non_diegetic_music)\s*:|$)",
    re.IGNORECASE | re.DOTALL,
)
_H3_SOUND_BOUNDARY_RE = re.compile(
    r"\b(?:overall_soundscape|non_diegetic_music)\s*:",
    re.IGNORECASE,
)
_H3_SPEECHLIKE_AMBIENCE_REPLACEMENTS = (
    (
        re.compile(
            r"\b(?:(?:coffee\s*shop|cafe|restaurant|office|party|crowd|"
            r"background)\s+)?chatter\b",
            re.IGNORECASE,
        ),
        "nonverbal room tone",
    ),
    (
        re.compile(
            r"\b(?:indistinct|distant|background|murmuring|soft|low)\s+"
            r"(?:voices?|conversations?|talking|speech)\b",
            re.IGNORECASE,
        ),
        "nonverbal room tone",
    ),
)

_H3_BASE_FIELDS = (
    "integrated_multimodal_description",
    "overall_soundscape",
    "non_diegetic_music",
)
_H3_REF2VA_FIELDS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)
_H3_ALL_FIELDS = tuple(dict.fromkeys((*_H3_REF2VA_FIELDS, *_H3_BASE_FIELDS)))
_H3_CUSTOM_SECTION_RE = re.compile(
    r"\s+(?:OPENING CONTINUITY|FINAL BLOCKING|"
    r"DIALOGUE AND VOCAL PERFORMANCE|SILENCE AND VOCAL PERFORMANCE)\s*:.*?"
    r"(?=\s+(?:OPENING CONTINUITY|FINAL BLOCKING|"
    r"DIALOGUE AND VOCAL PERFORMANCE|SILENCE AND VOCAL PERFORMANCE)\s*:|$)",
    re.IGNORECASE | re.DOTALL,
)
_H3_SOUND_PROSE_RE = re.compile(
    r"\b(?:(?:the|overall)\s+)?(?:ambient\s+)?soundscape\s+(?:is\s+dominated\s+by|includes|contains|features|is)\s+"
    r"(.+?)(?=\s+(?:non-diegetic\s+music|the\s+final\s+beat|"
    r"closing\s+blocking|opening\s+continuity|final\s+blocking)\b|\s*\[Shot\s+\d+\]|(?<=[.!?])\s+|$)",
    re.IGNORECASE | re.DOTALL,
)
_H3_MUSIC_PROSE_RE = re.compile(
    r"\bnon-diegetic\s+music\s+is\s+(.+?)"
    r"(?=\s+(?:the\s+final\s+beat|closing\s+blocking|"
    r"opening\s+continuity|final\s+blocking)\b|\s*\[Shot\s+\d+\]|(?<=[.!?])\s+|$)",
    re.IGNORECASE | re.DOTALL,
)
_H3_SCENE_TIMESTAMP_PATTERN = r"\d{2,}:[0-5]\d(?:\.\d{1,3})?"
_H3_EMPTY_SCENE_TRANSITION_RE = re.compile(
    r"<scenetrans>\s*\[Shot\s+\d+\]\s+At\s+"
    + _H3_SCENE_TIMESTAMP_PATTERN
    + r"\s*</scenetrans>",
    re.IGNORECASE,
)
_H3_TIMED_SHOT_MARKER_RE = re.compile(
    r"\[Shot\s+(?P<number>\d+)\](?=\s+At\s+(?P<timestamp>"
    + _H3_SCENE_TIMESTAMP_PATTERN
    + r")"
    + r"\b)",
    re.IGNORECASE,
)

# The LTX-style renderers write a shot's lines as quoted prose
# (``DIALOGUE: a person says "..."``) before the compiler emits the exact tagged
# block. A second, unlabelled copy of every line lets the model follow the copy
# that names no speaker, which is how a line lands on the wrong voice.
_H3_PLANNER_DIALOGUE_FIELD_RE = re.compile(
    r"(?i)(?:^|(?<=[\s|;]))\s*DIALOGUE\s*:\s*(?P<content>.*?)"
    r"(?=\s*(?:[|;]\s*)?(?:STYLE|ACTION|PERFORMANCE|CAMERA|ENDING|AUDIO|"
    r"SOUNDSCAPE|SETTING|LIGHTING|MOOD)\s*:|$)",
    re.DOTALL,
)
# Anchored to the start of the section on purpose: the compiler's own canonical
# sentence (``Dialogue: Valeria (S1) speaks ... <d>...</d>``) must never match.
_H3_QUOTED_SPEECH_RE = re.compile(
    r"(?i)^\s*[\w'’.-]+(?:\s+[\w'’.-]+){0,5}\s+"
    r"(?:says?|said|speaks?|spoke|asks?|replies?|answers?|whispers?|"
    r"shouts?|yells?|calls?|utters?|recites?|tells?)\s*[:,—–-]?\s*[\"“]"
)
_H3_SHOT_MARKER_RE = re.compile(r"\[Shot\s+(?P<number>\d+)\]", re.IGNORECASE)
_H3_SHOT_REFERENCE_PREFIX_RE = re.compile(
    r"\b(?:throughout|during|within|from|appears\s+in)\s*$",
    re.IGNORECASE,
)
_H3_AUXILIARY_SECTION_BOUNDARY_RE = re.compile(
    r"\b(?:integrated_multimodal_description|detailed_description|"
    r"overall_soundscape|non_diegetic_music|dialogue\s+beats?)\s*:|\[Shot\s+\d+\]",
    re.IGNORECASE,
)
_H3_META_SENTENCE_RE = re.compile(
    r"\bMiniMax H3 generates synchronized picture and stereo sound\.\s*",
    re.IGNORECASE,
)
_H3_MOJIBAKE_REPLACEMENTS = {
    "\u00e2\u0080\u0098": "\u2018",
    "\u00e2\u0080\u0099": "\u2019",
    "\u00e2\u0080\u009c": "\u201c",
    "\u00e2\u0080\u009d": "\u201d",
    "\u00e2\u0080\u0093": "\u2013",
    "\u00e2\u0080\u0094": "\u2014",
    "\u00e2\u0080\u00a6": "\u2026",
    "\u00e2\u20ac\u02dc": "\u2018",
    "\u00e2\u20ac\u2122": "\u2019",
    "\u00e2\u20ac\u0153": "\u201c",
    "\u00e2\u20ac\u009d": "\u201d",
    "\u00e2\u20ac\u201c": "\u2013",
    "\u00e2\u20ac\u201d": "\u2014",
    "\u00e2\u20ac\u00a6": "\u2026",
    "\u00c2\u00a0": " ",
    # Some older Windows JSON round-trips decoded the leading UTF-8 byte for
    # punctuation as U+0101 instead of U+00E2 before preserving the two C1
    # continuation bytes. Repair that observed Maestro variant as well.
    "\u0101\u0080\u0098": "\u2018",
    "\u0101\u0080\u0099": "\u2019",
    "\u0101\u0080\u009c": "\u201c",
    "\u0101\u0080\u009d": "\u201d",
    "\u0101\u0080\u0093": "\u2013",
    "\u0101\u0080\u0094": "\u2014",
    "\u0101\u0080\u00a6": "\u2026",
}


def _field(value: Any, key: str, default: Any = "") -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _normalized_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


_H3_BLOCK_RE = re.compile(
    r"<\s*d\s*>.*?<\s*/\s*d\s*>", re.IGNORECASE | re.DOTALL,
)

# Planner-authored speaker labels. They must never survive inside a field that
# Maestro renders itself, or the prompt ends up with "(S1) (S1) speaks" and a
# contradictory "(S2):" sitting inside the <d> block.
_LABEL_TOKEN_RE = re.compile(
    r"<\s*Subject\s+\d+\s*>"
    r"|\(\s*S\d+\s*\)"
    r"|\[\s*S\d+\s*\]"
    r"|(?<![A-Za-z0-9])S\d+(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# A speaker prefix the planner copied into the spoken line itself, e.g.
# "(S2): Claro, sí." or "S1: hola" or "<Subject 1>: hola".
_SPEAKER_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"<\s*Subject\s+\d+\s*>"
    r"|\(\s*S\d+\s*\)"
    r"|\[\s*S\d+\s*\]"
    r"|(?<![A-Za-z0-9])S\d+(?![A-Za-z0-9])"
    r")\s*:?\s*",
    re.IGNORECASE,
)


def _strip_label_tokens(value: Any) -> str:
    """Remove planner-authored speaker labels from a metadata field."""

    return _normalized_space(
        _LABEL_TOKEN_RE.sub(" ", str(value or "")),
    ).strip(" ,;:-.")


# A planner occasionally corrupts a label with characters from another script
# ("(S\u304e)", "(S\u0e04\u0e34\u0e14)", "<Subject \u043d\u0430\u0441\u043d\u0438\u04431>"). Those
# fragments are pure noise and must never reach the video prompt.
_NON_LATIN_SCRIPT_RE = re.compile(
    "[\u0400-\u04ff\u0530-\u058f\u0590-\u05ff\u0600-\u06ff"
    "\u0900-\u097f\u0980-\u09ff\u0a00-\u0a7f\u0b80-\u0bff"
    "\u0c00-\u0c7f\u0e00-\u0e7f"
    "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]"
)
_NON_LATIN_GROUP_RE = re.compile(
    "[\\(\\[]\\s*[^)\\]]*"
    "[\u0400-\u04ff\u0530-\u058f\u0590-\u05ff\u0600-\u06ff"
    "\u0900-\u097f\u0980-\u09ff\u0a00-\u0a7f\u0b80-\u0bff"
    "\u0c00-\u0c7f\u0e00-\u0e7f"
    "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]"
    "[^)\\]]*[\\)\\]]"
)
_BRACKET_LABEL_RE = re.compile(r"\[\s*(S\d+)\s*\]", re.IGNORECASE)
# Any tag-shaped token still inside a spoken line is corruption, except the two MiniMax writes
# *inside* a <d> block: <scenetrans> where one line crosses a cut, and <cutoff> where it is cut
# off. A spoken line that quotes them must reach the model verbatim.
_STRAY_TAG_RE = re.compile(
    r"</?\s*(?!(?:scenetrans|cutoff)\b)[A-Za-z][^<>\r\n]{0,24}/?\s*>",
    re.IGNORECASE,
)
# Label-shaped groups, capped at three trailing characters so ordinary prose
# such as "(Saturday)" is never treated as a corrupted speaker label.
_BROKEN_LABEL_GROUP_RE = re.compile(r"[\(\[]\s*S[^)\]]{0,3}[\)\]]", re.IGNORECASE)
_ANY_SUBJECT_TAG_RE = re.compile(
    r"<\s*(?:Subject|Picture)[^>]{0,48}>", re.IGNORECASE,
)
# An unclosed corruption has no ">" to cap it, so it needs its own rule. The
# observed shape is "<Subject /{1} (S1)", where the label is glued to the junk.
# A well-formed "<Subject 1>" must be excluded, or its opening would be eaten.
_UNCLOSED_SUBJECT_OPEN_RE = re.compile(
    r"<\s*(?:Subject|Picture)\b(?!\s+\d+\s*>)"
    r"(?:\s*/\s*\{[^}<>]{0,12}\})?"
    r"\s*(?:\(\s*S\d+\s*\))?",
    re.IGNORECASE,
)
# A speaker label somewhere inside a spoken line, as in "...da miedo. (S2): X".
# Two of them, or one that is not at the start, mean the planner fused several
# turns into one beat -- and one beat becomes one <d> block, so one voice.
_INLINE_SPEAKER_LABEL_RE = re.compile(
    r"[\(\[]\s*S(\d+)\s*[\)\]]\s*[:.]?\s*", re.IGNORECASE,
)


def _strip_non_latin_script(value: Any) -> str:
    """Drop corrupted label fragments written in a non-Latin script."""

    text = _NON_LATIN_GROUP_RE.sub(" ", str(value or ""))
    return _NON_LATIN_SCRIPT_RE.sub(" ", text)


# A planner occasionally emits overlapping tokens from another script in the
# middle of a Latin word: "...en que el cerebro\u0e4a\u0e01el cerebro humano...",
# "S\u00ed, las\u3089\u3046las agrupa...", "<Subject 1> (S\ub85c\ub294 S1)". Only runs
# glued to Latin text are noise, so a project whose prose is legitimately
# written in another script keeps every character.
_GLUED_FOREIGN_RE = re.compile(
    "["
    "\u0400-\u04ff\u0530-\u058f\u0590-\u05ff\u0600-\u06ff"
    "\u0900-\u097f\u0980-\u09ff\u0a00-\u0a7f\u0b80-\u0bff"
    "\u0c00-\u0c7f\u0e00-\u0e7f"
    "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]+"
    "(?=[A-Za-z0-9])|(?<=[A-Za-z0-9])["
    "\u0400-\u04ff\u0530-\u058f\u0590-\u05ff\u0600-\u06ff"
    "\u0900-\u097f\u0980-\u09ff\u0a00-\u0a7f\u0b80-\u0bff"
    "\u0c00-\u0c7f\u0e00-\u0e7f"
    "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]+"
)
# Removing the noise from "(S\ub85c\ub294 S1)" leaves "(S S1)", which is a label.
_S_SPLIT_LABEL_RE = re.compile(r"\(\s*S\s*S(\d+)\s*\)", re.IGNORECASE)


def repair_glued_foreign_script(value: Any) -> str:
    """Remove foreign-script runs that are glued into Latin text."""

    text = _GLUED_FOREIGN_RE.sub("", str(value or ""))
    text = _S_SPLIT_LABEL_RE.sub(r"(S\1)", text)
    return text


def _normalize_speaker_brackets(value: Any) -> str:
    """Render every speaker label with parentheses, never square brackets.

    Label-shaped groups that are not a valid ``(Sx)`` \u2014 a planner corruption
    such as ``(S\u03c0\u00fc\u00c4)`` or ``(Sra)`` \u2014 are removed outright. The short
    suffix limit keeps ordinary prose like ``(Saturday)`` untouched.
    """

    text = _BRACKET_LABEL_RE.sub(r"(\1)", str(value or ""))

    def keep(match: re.Match) -> str:
        token = match.group(0)
        return token if re.fullmatch(
            r"[\(\[]\s*S\d+\s*[\)\]]", token, re.IGNORECASE,
        ) else " "

    text = _BROKEN_LABEL_GROUP_RE.sub(keep, text)
    return _normalized_space(text)


def _strip_foreign_script_noise(text: str) -> str:
    """Remove stray non-Latin characters from an otherwise Latin line.

    A planner sometimes injects a few characters from another script into a
    Spanish or English line ("es inmensamente fr\u00edg\u05d2\u05de\u05d2"). Stripping is
    only safe when the line is overwhelmingly Latin, so a genuinely Japanese or
    Chinese project keeps its dialogue untouched.
    """

    letters = [char for char in text if char.isalpha()]
    if not letters:
        return text
    latin = sum(1 for char in letters if ord(char) < 0x0250)
    if latin / len(letters) < 0.6:
        return text
    return _normalized_space(_strip_non_latin_script(text))


# H3 timestamps a director writes as "[Shot 4] At MM:SS.mmm, ...". The model
# frequently copies the literal ``MM:`` placeholder instead of substituting the
# digits, and its arithmetic can disagree with the clip's real audio window.
# The marker may end in a comma or a period, and the value itself contains a
# period, so the terminator must be followed by whitespace or end of text.
_H3_TIME_MARKER_RE = re.compile(
    r"(\[\s*Shot\s+\d+\s*\]\s*At\s+)([^\n]{0,32}?)(\s*[.,])(?=\s|$)",
    re.IGNORECASE,
)


def _format_h3_timestamp(seconds: Any) -> str:
    """Render seconds as the official ``MM:SS.mmm`` H3 marker."""

    total = max(0.0, float(seconds or 0.0))
    minutes = int(total // 60)
    remainder = total - minutes * 60
    return f"{minutes:02d}:{remainder:06.3f}"


def _align_h3_time_markers(
    text: str,
    audio_start_seconds: Any,
) -> tuple[str, int]:
    """Rewrite director-authored cut markers to the real audio window.

    The guides ask for ``[Shot N] At MM:SS.mmm``, but the planner sometimes
    emits the placeholder verbatim (``At MM:58s``) or a cumulative time that
    disagrees with the clip range shown in the UI. Maestro knows the exact
    window, so the marker is written from that instead of trusted.
    """

    if audio_start_seconds is None:
        return text, 0
    stamp = _format_h3_timestamp(audio_start_seconds)
    replacements = 0

    def replace(match: re.Match) -> str:
        nonlocal replacements
        replacements += 1
        return f"{match.group(1)}{stamp}{match.group(3)}"

    return _H3_TIME_MARKER_RE.sub(replace, str(text or "")), replacements


def _clean_malformed_subject_tags(text: Any) -> str:
    """Drop ``<Subject ...>`` tags that are not a well-formed numbered label.

    Maestro writes ``<Subject 1>`` itself. Anything else in that shape is
    planner corruption such as ``<Subject \u0e04\u0e34\u0e14/S1>``.
    """

    def replace(match: re.Match) -> str:
        inner = match.group(0)
        return inner if re.fullmatch(
            r"<\s*(?:Subject|Picture)\s+\d+\s*>", inner, re.IGNORECASE,
        ) else " "

    cleaned = _ANY_SUBJECT_TAG_RE.sub(replace, str(text or ""))
    # A corruption can also be left unclosed, so the bounded pattern above never
    # finds the ">" it needs: "<Subject /{1} (S1) remains still, ...". Drop the
    # opening token with the fragment it swallowed, and keep the prose.
    return _normalized_space(_UNCLOSED_SUBJECT_OPEN_RE.sub(" ", cleaned))


def _clean_h3_contract_field(value: Any) -> str:
    """Sanitize one of the official H3 sound fields.

    ``overall_soundscape`` and ``non_diegetic_music`` are copied straight into
    the compiled prompt, so they used to carry corrupted labels and stray
    non-Latin characters that every other field had already been cleaned of.
    """

    text = _normalize_speaker_brackets(value)
    text = _clean_malformed_subject_tags(text)
    return _normalized_space(_strip_foreign_script_noise(text))


def _clean_subject_text(value: Any) -> str:
    """Clean a visible-subject description.

    Maestro writes the ``<Subject N>`` scaffolding itself, so any copy left by
    the planner duplicates it, and a corrupted label such as
    ``<Subject q> (S1)`` must not survive into the prompt.
    """

    text = _clean_malformed_subject_tags(str(value or ""))
    text = _ANY_SUBJECT_TAG_RE.sub(" ", text)
    text = _strip_non_latin_script(text)
    return _normalized_space(_normalize_speaker_brackets(text)).strip(" ,;:-.")


def _strip_speaker_prefixes(value: Any) -> str:
    """Remove speaker markers the planner copied into the spoken line.

    A line that reaches the model as ``(S2): Claro, sí.`` produced a block like
    ``<d>[English] (S2): Claro, sí.</d>``. The outer speaker then disagreed with
    the inner marker, which is exactly what made H3 give the male voice to the
    woman. Only the words belong inside the block.
    """

    text = str(value or "")
    previous = None
    while text != previous:
        previous = text
        text = _SPEAKER_PREFIX_RE.sub("", text, count=1)
    # Any inline marker left mid-sentence (merged turns) is equally unusable.
    text = _LABEL_TOKEN_RE.sub(" ", text)
    # A corrupted tag can survive inside the line -- "<d>[Spanish] <das/e>Ah\u00ed
    # necesitas..." -- where it becomes a spoken token. Maestro renders the
    # <d>...</d> pair itself, so nothing else belongs inside the block.
    text = _STRAY_TAG_RE.sub(" ", text)
    text = _normalized_space(text).strip()
    # A wrapping quote pair is prose, not part of the line.
    while len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'", "\u201c", "\u2018"}:
        text = text[1:-1].strip()
    return text


def strip_dialogue_markup(value: Any) -> str:
    """Return only the spoken words of a planner-authored line.

    The planner wraps every line in its H3 tag and, in 40% of a real batch,
    repeats the speaker inside it (``<d>(S1): Qu\u00e9 loco.</d>``). Maestro
    renders the block and the speaker itself, so normalize once at the boundary:
    the compiled prompt, the saved plan, and the review UI then all show words
    only, instead of each consumer having to remember to clean it.
    """

    if is_silent_dialogue(value):
        return ""
    try:
        return _dialogue_payload(value)[1]
    except H3DialogueContractError:
        return _strip_speaker_prefixes(value)


def _clean_h3_metadata(value: Any, limit: int = 160) -> str:
    """Clean a beat metadata field before it is written into the prompt.

    A planner occasionally leaks its reasoning into ``delivery``,
    ``physical_cue``, or ``speaker_name``. That text is copied verbatim into
    the prompt, where an embedded ``<d>...</d>`` fragment becomes an extra,
    non-canonical dialogue block and fails the whole vocal contract. Remove any
    dialogue markup, drop planner-authored speaker labels so the rendered
    instruction cannot repeat the label, and cap the length.
    """

    text = _H3_BLOCK_RE.sub(" ", str(value or ""))
    text = _H3_DIALOGUE_TOKEN_RE.sub(" ", text)
    text = _strip_non_latin_script(text)
    # A corrupted "<Subject ...>" tag reaches the prompt verbatim here, because
    # this field is rendered as prose rather than rebuilt by Maestro.
    text = _clean_malformed_subject_tags(text)
    text = _strip_label_tokens(text)
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-") + "..."
    return text


# A JSON grammar stops the planner from writing prose *around* the payload, but
# nothing stops it escaping its reasoning *into* a string value. One real batch
# produced ``ending_beat = "Wait, there should be 12 total entries in the JSON
# array."``, and that field is rendered into the prompt as "By the final beat,
# ...". These markers are specific enough not to fire on real scene prose.
_PLANNER_REASONING_RE = re.compile(
    r"^\s*(?:but\s+|ok(?:ay)?,?\s+|hmm,?\s+)?wait\b"
    r"|\b(?:placeholder|end of thought|logic holds|manual structure|"
    r"recalculat\w*|the user asked)\b"
    r"|\blet me\b|\blet's\b|\bI need to\b|\bnote to self\b"
    r"|\bJSON\b[^.]{0,40}\b(?:entr|array|output|total|items?|shots?)\b"
    r"|(?<!:)//|/\*",
    re.IGNORECASE,
)


def looks_like_planner_reasoning(value: Any) -> bool:
    """True when a field holds the planner's reasoning instead of content."""

    return bool(_PLANNER_REASONING_RE.search(str(value or "")))


def drop_reasoning_sentences(text: Any) -> tuple[str, int]:
    """Remove planner reasoning that leaked into free prose.

    The planner's reasoning can land mid-paragraph rather than in a dedicated
    field, for example ``"...vulnerabilidad evidente. Wait, there should be 12
    total entries in the JSON array. Dialogue timing: ..."``. A sentence that
    contains dialogue markup is never touched, so a real line can't be lost.
    """

    raw = str(text or "")
    if not raw or not _PLANNER_REASONING_RE.search(raw):
        return raw, 0
    parts = re.split(r"(?<=[.!?])\s+", raw)
    kept = [
        part for part in parts
        if "<d>" in part or "</d>" in part or not looks_like_planner_reasoning(part)
    ]
    return " ".join(kept), len(parts) - len(kept)


def drop_planner_reasoning(value: Any, limit: int = 240) -> str:
    """Clean one planner-authored field, discarding leaked reasoning.

    A field that is entirely reasoning is dropped rather than shortened: there
    is no content to keep, and whatever remains is rendered into the prompt
    verbatim.

    Speaker labels are deliberately preserved here. These fields describe *who*
    does what ("(S1) waits for his reaction"), so stripping the label the way
    ``_clean_h3_metadata`` does would lose the attribution.
    """

    text = str(value or "").strip()
    if not text or looks_like_planner_reasoning(text):
        return ""
    text = _H3_BLOCK_RE.sub(" ", text)
    text = _H3_DIALOGUE_TOKEN_RE.sub(" ", text)
    text = _clean_malformed_subject_tags(text)
    text = _strip_non_latin_script(text)
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-") + "..."
    return _normalized_space(text)


def _dialogue_spans(prompt: str) -> tuple[list[tuple[int, int]], bool]:
    """Return balanced top-level dialogue spans and whether markup was bad."""

    spans: list[tuple[int, int]] = []
    depth = 0
    start = -1
    malformed = False
    for token in _H3_DIALOGUE_TOKEN_RE.finditer(prompt or ""):
        closing = bool(token.group(1))
        if not closing:
            if depth == 0:
                start = token.start()
            else:
                malformed = True
            depth += 1
            continue
        if depth == 0:
            malformed = True
            continue
        depth -= 1
        if depth == 0 and start >= 0:
            spans.append((start, token.end()))
            start = -1
    if depth:
        malformed = True
    return spans, malformed


def _audio_policy_flag(audio_policy: Mapping[str, Any] | None, key: str) -> bool | None:
    """Return an explicitly supplied audio permission, preserving legacy defaults."""

    if not isinstance(audio_policy, Mapping) or key not in audio_policy:
        return None
    value = audio_policy.get(key)
    return value if isinstance(value, bool) else None


def _has_spoken_beats(dialogue_beats: Sequence[Any] | None) -> bool:
    return any(
        _normalized_space(_field(beat, "spoken_text", ""))
        for beat in dialogue_beats or []
    )


def _is_negated_instruction(text: str, start: int) -> bool:
    """Recognize nearby negative wording without treating it as an instruction."""

    boundary = max(
        text.rfind(".", 0, start),
        text.rfind("!", 0, start),
        text.rfind("?", 0, start),
        text.rfind(";", 0, start),
        text.rfind("\n", 0, start),
    )
    prefix = text[max(boundary + 1, start - 90):start]
    if re.search(
        r"\b(?:no|not|never|without|don't|doesn't|didn't|cannot|can't|"
        r"do\s+not|does\s+not|did\s+not|cannot)\s+"
        r"(?:[\w’'-]+\s+){0,4}$",
        prefix,
        flags=re.IGNORECASE,
    ):
        return True
    suffix = text[start:start + 100]
    return bool(re.match(
        r"\s*(?:(?:non[ -]?diegetic|background|orchestral|cinematic|"
        r"instrumental|hopeful|heroic|soaring|gentle|peaceful|sustained|"
        r"uplifting|symphonic|dramatic)\s+)*(?:music|score|soundtrack)?\s*"
        r"(?:is|was|remains?|stays?)\s+"
        r"(?:not|absent|silent|off|missing|muted|disabled|unavailable|"
        r"unheard|inaudible)\b",
        suffix,
        flags=re.IGNORECASE,
    ))


def _same_sentence(text: str, start: int, end: int) -> str:
    left = max(
        text.rfind(".", 0, start),
        text.rfind("!", 0, start),
        text.rfind("?", 0, start),
        text.rfind(";", 0, start),
        text.rfind("\n", 0, start),
    ) + 1
    right_candidates = [
        position for marker in ".!?;\n"
        if (position := text.find(marker, end)) >= 0
    ]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left:right]


def _is_generated_speech_instruction(text: str, cue: re.Match[str]) -> bool:
    """Ignore visible text and nonverbal calls while retaining real voice cues."""

    if _is_negated_instruction(text, cue.start()):
        return False
    boundary = max(
        text.rfind(".", 0, cue.start()),
        text.rfind("!", 0, cue.start()),
        text.rfind("?", 0, cue.start()),
        text.rfind(";", 0, cue.start()),
        text.rfind("\n", 0, cue.start()),
    ) + 1
    prefix = text[boundary:cue.start()]
    suffix = text[cue.end():]
    if _H3_TEXTUAL_SPEECH_SUBJECT_RE.search(prefix):
        return False

    verb = cue.group(0).casefold().replace(" ", "-")
    if verb in {"call", "calls", "calling"}:
        # Bare calls commonly describe birdsong, a call button, or a person
        # summoning a lift. Treat it as speech only when words are explicit.
        return bool(re.match(
            r"\s*(?:out\s*)?(?:[:,—-]\s*)?[\"'“][^\"'”]{1,}"
            r"|\s+(?:out\s+)?(?:for\s+(?:help|someone|the\s+others?)|"
            r"(?:her|his|their|the\s+\w+['’]s)\s+name|"
            r"to\s+(?:the\s+)?(?:other\s+\w+|\w+))\b",
            suffix,
            flags=re.IGNORECASE,
        ))

    if verb in {"say", "says", "said"} and re.match(
        r"\s*(?:nothing|no\s+(?:words?|speech|dialogue)|silently)\b",
        suffix,
        flags=re.IGNORECASE,
    ):
        return False
    return True


def _independent_continuation_start(text: str, start: int, end: int) -> int | None:
    """Find an action/SFX/camera clause after a music cue without a verb list."""

    tail = text[start:end]
    for boundary in re.finditer(
        r"(?:,\s*|\s+)(?:and|then|while|as)\s+",
        tail,
        flags=re.IGNORECASE,
    ):
        clause = tail[boundary.end():].lstrip()
        if (
            _H3_MUSIC_SUBJECT_CONTINUATION_RE.match(clause)
            or _H3_MUSIC_FOLLOWTHROUGH_RE.match(clause)
        ):
            continue
        if _H3_INDEPENDENT_CLAUSE_START_RE.match(clause):
            return start + boundary.start()
    return None


def _is_mapped_audio_instruction(
    text: str,
    start: int,
    end: int,
    *,
    has_driving_audio: bool,
) -> bool:
    if not has_driving_audio:
        return False
    sentence = _same_sentence(text, start, end)
    return bool(re.search(
        r"\b(?:mapped\s+driving\s+audio|supplied\s+(?:source\s+)?"
        r"(?:audio|track|soundtrack|song)|provided\s+(?:audio|track|"
        r"soundtrack)|source\s+(?:audio|track|soundtrack|song)|"
        r"<Audio\s+\d+>)\b",
        sentence,
        flags=re.IGNORECASE,
    ))


def _h3_music_instruction_matches(text: str) -> list[re.Match[str]]:
    matches = [*_H3_MUSIC_INSTRUCTION_RE.finditer(text or "")]
    matches.extend(_H3_MUSIC_DIRECTIVE_RE.finditer(text or ""))
    return sorted(matches, key=lambda match: match.start())


def _strip_generated_music_instructions(
    value: Any,
    *,
    has_driving_audio: bool = False,
) -> str:
    """Remove positive score cues while preserving nearby visual and SFX prose."""

    text = str(value or "")
    removals: list[tuple[int, int]] = []
    for match in _h3_music_instruction_matches(text):
        if _is_negated_instruction(text, match.start()) or _is_mapped_audio_instruction(
            text, match.start(), match.end(), has_driving_audio=has_driving_audio,
        ):
            continue

        start = match.start()
        prefix = text[max(0, start - 12):start]
        conjunction = re.search(r"\b(?:and|while|as)\s+$", prefix, re.IGNORECASE)
        if conjunction:
            start -= len(prefix) - conjunction.start()

        sentence_end_candidates = [
            position for marker in ".!?;\n"
            if (position := text.find(marker, match.end())) >= 0
        ]
        sentence_end = min(sentence_end_candidates) if sentence_end_candidates else len(text)
        comma_positions = [
            position for marker in ",;"
            if (position := text.find(marker, match.end(), sentence_end)) >= 0
        ]
        verb = str(match.groupdict().get("verb") or "").casefold()
        end = sentence_end
        if comma_positions and verb not in {"is", "was", "becomes", "became"}:
            comma = min(comma_positions)
            tail = text[comma + 1:sentence_end]
            if re.match(
                r"\s*(?:matching|underscoring|which|that|thereby|"
                r"building\s+(?:to|toward)|rising\s+(?:to|toward))\b",
                tail,
                flags=re.IGNORECASE,
            ):
                end = sentence_end
            else:
                end = comma

        # Retain independent movement, camera, and SFX clauses even when their
        # verb is outside the original whitelist (for example ``unlocks`` or
        # ``pans``). Musical follow-through clauses remain part of the removed
        # score instruction.
        continuation_start = _independent_continuation_start(
            text, match.end(), end,
        )
        if continuation_start is not None:
            end = continuation_start
        removals.append((start, end))

    result = text
    for start, end in sorted(removals, reverse=True):
        result = result[:start] + " " + result[end:]
    result = re.sub(r"\s+([,.;!?])", r"\1", result)
    result = re.sub(r"([,;])\s*[,;]+", r"\1", result)
    result = re.sub(r"\b(?:and|while|as)\s*([,.;!?])", r"\1", result, flags=re.I)
    result = re.sub(r"([.!?])\s*([.!?])", r"\1", result)
    result = _normalized_space(result).strip(" ,;:")
    return re.sub(r"^(?:and|while|as|then)\s+", "", result, flags=re.IGNORECASE)


def _strip_music_mentions_from_soundscape(
    value: Any,
    *,
    has_driving_audio: bool = False,
) -> str:
    """Drop score-only soundscape clauses while retaining ambience and effects."""

    text = _strip_generated_music_instructions(value, has_driving_audio=has_driving_audio)
    clauses = re.split(r"\s*(?:,|;|\band\b)\s*", text, flags=re.IGNORECASE)
    kept: list[str] = []
    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        if not re.search(r"\b(?:music|score|soundtrack)\b", clause, re.IGNORECASE):
            kept.append(clause)
            continue
        noun = re.search(r"\b(?:music|score|soundtrack)\b", clause, re.IGNORECASE)
        if (
            _is_negated_instruction(clause, noun.start())
            or _is_mapped_audio_instruction(
                clause, noun.start(), noun.end(), has_driving_audio=has_driving_audio,
            )
        ):
            kept.append(clause)
    return ", ".join(kept)


def _strip_planner_quoted_dialogue(value: Any) -> str:
    """Remove the renderer's quoted ``DIALOGUE: ... says "..."`` prose.

    Structured beats are compiled separately into the canonical tagged block.
    Keeping the quoted copy states every line twice, and the copy that reads
    "a person says" names no participant, so the generator can bind the line to
    the wrong speaker.
    """

    text = str(value or "")
    if "dialogue:" not in text.casefold():
        return text
    pieces: list[str] = []
    cursor = 0
    removed = False
    for match in _H3_PLANNER_DIALOGUE_FIELD_RE.finditer(text):
        # Only a section that actually quotes speech is the renderer's
        # dialogue field; project context can legitimately contain the word.
        if not _H3_QUOTED_SPEECH_RE.match(match.group("content")):
            continue
        pieces.append(text[cursor:match.start()])
        cursor = match.end()
        removed = True
    if not removed:
        return text
    pieces.append(text[cursor:])
    text = " ".join(pieces)
    text = re.sub(r"^\s*(?:\|\s*)+", "", text)
    text = re.sub(r"(?:\s*\|\s*)+$", "", text)
    text = re.sub(r"(?:\s*\|\s*){2,}", " | ", text)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    return _normalized_space(text)


def _strip_h3_dialogue_blocks(value: Any) -> str:
    """Remove dialogue spans from text that must not carry speech.

    The project context is reference prose injected into every shot. A podcast
    brief that templates its lines with ``<d>[Spanish] ...</d>`` placeholders
    therefore repeated those blocks in every shot, where they sat beside the
    shot's own canonical line: three blocks for one beat, and the contract
    refused the whole render.
    """

    text = str(value or "")
    spans, _malformed = _dialogue_spans(text)
    if not spans:
        return text
    cleaned = _replace_spans(text, spans, [""] * len(spans))
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    cleaned = re.sub(r"\(\s*\)|\[\s*\]", " ", cleaned)
    return _normalized_space(cleaned)


# A project brief that narrates the film's own progression is not this shot's world.
# Measured twice: spoken brief prose was read aloud by the model, and on a
# 27B-planned 14-clip project every rendered clip began with both hosts entering
# the studio and the key lights blooming, because that arc lived in all fourteen
# prompts. The block already told the model "do not perform its progression" and it
# performed it anyway, so the arc is now removed instead of forbidden.
#
# Two layers, deliberately: a story under a heading (``DESCRIPTION:``, ``STORY:``) is
# dropped whole by ``clip_project_context``, and prose with no heading at all is
# trimmed sentence by sentence here. Only the second layer lives in this module.
_ARC_MARKER_RE = re.compile(
    r"\b(?:ent(?:er|ers|ering|rance|rances)|settl(?:e|es|ing)|awak(?:e|en|ens|ening)|"
    r"bloom\w*|unfurl\w*|contract\w*|dim(?:s|med|ming)?|fold\w*|sprout\w*|"
    r"ris(?:e|es|ing)|eas(?:e|es|ing)|push\w*|lift\w*|unfold\w*|expand\w*|grow\w*|"
    r"progress\w*|begi(?:n|ns|nning)|start\w*|close(?:s|d)?|"
    r"then|afterwards|meanwhile|finally|peak|arc|cycle|"
    r"despu[eé]s|luego|entr(?:a|an|ada|ando)|se sientan|comienz(?:a|an)|"
    r"termin(?:a|an)|finalmente|mientras|crec\w*|expande\w*)\b",
    re.IGNORECASE,
)
# A story names several steps of one process. One verb is a description that happens to
# use it -- "warm light rising along the desk edge" is a look, not an event -- so a block
# is only read as narrative when more than one of its sentences carries a marker.
_MIN_ARC_SENTENCES = 2
_ARC_DROP_REPORTED: set[str] = set()


def _static_sentences(block: str) -> str:
    """A prose block without the sentences that narrate its progression."""

    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", block) if part.strip()]
    marked = [sentence for sentence in sentences if _ARC_MARKER_RE.search(sentence)]
    if len(marked) < _MIN_ARC_SENTENCES:
        return block
    return " ".join(sentence for sentence in sentences if not _ARC_MARKER_RE.search(sentence)).strip()


def _world_only_project_context(context: Any) -> str:
    """The brief's static world, without the film's progression.

    A labelled section is a contract that must hold in every clip -- the cast, the
    vocal roles, the look of the place -- so it travels whole and is never trimmed:
    cutting it was measured to delete the voice rules and the ambience, which is the
    opposite of what this pass is for. Free prose is the author's story, and only its
    place/look sentences are kept.
    """

    text = str(context or "").strip()
    if not text:
        return ""
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    kept: list[str] = []
    for block in blocks:
        if _section_heading(block):
            kept.append(block)
            continue
        world = _static_sentences(block)
        if world:
            kept.append(world)
    return "\n\n".join(kept)


def _report_dropped_project_context(project_context: Any) -> None:
    """Say why a brief the director wrote reached no shot at all."""

    text = str(project_context or "").strip()
    if not text:
        return
    digest = str(abs(hash(text)))
    if digest in _ARC_DROP_REPORTED:
        return
    _ARC_DROP_REPORTED.add(digest)
    print(
        "[MiniMax H3] The project brief narrates the film's story, so none of it is sent "
        "to a shot: a clip that is told the whole film performs the whole film. The "
        "planner still reads all of it. Put the cast in CHARACTERS AND BINDINGS, the "
        "look of the place in AMBIENCE, and the action per clip in the timeline."
    )


def _h3_injected_project_context(project_context: Any) -> str:
    """The exact project-brief prose a compiled shot carries.

    Computed once so the audio-policy scan can mask the same text it injected: the
    brief states film-wide rules, and they are not this shot's speech.
    """

    raw = normalize_h3_text(project_context)
    trimmed = _world_only_project_context(_clip_context(raw))
    # Dialogue belongs to the shot that speaks it. A brief may template its
    # lines with empty <d> placeholders, and those were injected into every
    # shot as extra dialogue blocks.
    context = _strip_h3_dialogue_blocks(trimmed)
    context = _normalized_space(pack_project_context(context)) if context.strip() else ""
    if not context:
        _report_dropped_project_context(raw)
    return context


def _strip_forbidden_dialogue_blocks(value: Any) -> str:
    """Remove planner-authored tags and only the speech lead-in attached to them."""

    text = str(value or "")
    spans, _malformed = _dialogue_spans(text)
    for start, end in reversed(spans):
        prefix_start = max(
            text.rfind(".", 0, start),
            text.rfind("!", 0, start),
            text.rfind("?", 0, start),
            text.rfind(";", 0, start),
            text.rfind("\n", 0, start),
        ) + 1
        prefix = text[prefix_start:start]
        lead = re.search(
            r"(?i)(?:(?:[A-Z][\w’'-]*|the\s+[A-Z][\w’'-]*)\s+)?"
            r"(?:says?|speaks?|sang|sings?|whispers?|shouts?|calls?|"
            r"utters?|recites?|tells?)\s*(?:[:,—-]\s*)?$",
            prefix,
        )
        remove_start = prefix_start + lead.start() if lead else start
        text = text[:remove_start] + " " + text[end:]
    text = _strip_dialogue_for_driving_audio(text)
    text = re.sub(r"([.!?])(?:\s*[,;:.!?])+", r"\1", text)
    return _normalized_space(text)


def _audio_policy_violations(
    prompt: str,
    audio_policy: Mapping[str, Any] | None,
    *,
    dialogue_beats: Sequence[Any] | None = None,
    dialogue_manifest_authoritative: bool = False,
    has_driving_audio: bool = False,
    ignore_regions: Sequence[str] | None = None,
) -> list[str]:
    """Find final prompt instructions that contradict explicit audio permissions.

    ``ignore_regions`` names prose that travels with every shot rather than
    instructing this one -- the project brief. A brief states film-wide rules
    such as "only the person currently speaking moves their lips", and reading
    that as this shot's own speech request refused every silent shot in a
    podcast. The mask keeps offsets unchanged so the scan still reports where
    a real instruction sits.
    """

    text = normalize_h3_text(prompt)
    for region in ignore_regions or ():
        region = normalize_h3_text(region).strip()
        if len(region) < 24:
            # Too short to be an unambiguous region; masking it could hide a
            # genuine instruction.
            continue
        text = text.replace(region, " " * len(region))
    errors: list[str] = []
    speech_forbidden = (
        _audio_policy_flag(audio_policy, "generated_speech_allowed") is False
        or (
            dialogue_manifest_authoritative
            and not _has_spoken_beats(dialogue_beats)
        )
    )
    if speech_forbidden:
        spans, _malformed = _dialogue_spans(text)
        if spans:
            errors.append("dialogue tags contradict the authoritative no-generated-speech policy")
        if not has_driving_audio:
            without_tags = _replace_spans(text, spans, [""] * len(spans))
            for cue in _H3_SPOKEN_INSTRUCTION_RE.finditer(without_tags):
                if _is_generated_speech_instruction(without_tags, cue):
                    errors.append(
                        "generated speech instructions contradict the authoritative "
                        "no-generated-speech policy: this shot says "
                        f"{_sentence_around(without_tags, cue.start(), cue.end())!r}. "
                        "Either let the shot speak, or describe only the visible "
                        "performance (the mouth moving with the supplied track) "
                        "without naming a speech act."
                    )
                    break

    if _audio_policy_flag(audio_policy, "generated_music_allowed") is False:
        fields = _extract_h3_fields(text)
        music_field = _normalized_space(fields.get("non_diegetic_music", ""))
        no_music_values = {"", "n/a", "none", "no music", "no non-diegetic music", "not applicable"}
        if music_field.casefold() not in no_music_values and not re.search(
            r"\b(?:mapped\s+driving\s+audio|supplied\s+(?:source\s+)?"
            r"(?:audio|track|soundtrack|song)|provided\s+(?:audio|track|"
            r"soundtrack)|source\s+(?:audio|track|soundtrack|song)|"
            r"<Audio\s+\d+>)\b",
            music_field,
            flags=re.IGNORECASE,
        ):
            errors.append(
                "non_diegetic_music contains generated music despite the audio policy"
            )
        for cue in _h3_music_instruction_matches(text):
            if _is_negated_instruction(text, cue.start()) or _is_mapped_audio_instruction(
                text, cue.start(), cue.end(), has_driving_audio=has_driving_audio,
            ):
                continue
            errors.append("generated music instructions contradict the audio policy")
            break
    return list(dict.fromkeys(errors))


def _sentence_around(text: str, start: int, end: int) -> str:
    """The clause a diagnostic is about, so the report names what to change."""

    left = max(
        text.rfind(".", 0, start),
        text.rfind("!", 0, start),
        text.rfind("?", 0, start),
        text.rfind(";", 0, start),
        text.rfind("\n", 0, start),
    ) + 1
    stops = [
        index for index in (
            text.find(".", end),
            text.find("!", end),
            text.find("?", end),
            text.find(";", end),
            text.find("\n", end),
        ) if index >= 0
    ]
    right = min(stops) if stops else len(text)
    return _normalized_space(text[left:right]).strip()[:160]


def _h3_visual_shot_markers(visual: str) -> list[re.Match[str]]:
    """Return cut labels outside dialogue and explicit reference phrases."""

    spans, _ = _dialogue_spans(visual)
    span_index = 0
    markers: list[re.Match[str]] = []
    for marker in _H3_SHOT_MARKER_RE.finditer(visual or ""):
        while span_index < len(spans) and marker.start() >= spans[span_index][1]:
            span_index += 1
        if (
            span_index < len(spans)
            and spans[span_index][0] <= marker.start() < spans[span_index][1]
        ):
            continue
        preceding = visual[max(0, marker.start() - 45):marker.start()]
        if _H3_SHOT_REFERENCE_PREFIX_RE.search(preceding):
            continue
        markers.append(marker)
    return markers


def _replace_spans(
    prompt: str,
    spans: Sequence[tuple[int, int]],
    replacements: Sequence[str],
) -> str:
    result = prompt
    for (start, end), replacement in reversed(list(zip(spans, replacements))):
        result = f"{result[:start]}{replacement}{result[end:]}"
    return result


def _strip_dialogue_for_driving_audio(prompt: str) -> str:
    """Remove generated dialogue when mapped audio owns every audible vocal.

    Music-video planning sometimes receives a noisy transcription containing a
    repeated refrain. If an LLM copies that transcript into a ``<d>`` block and
    reaches its output limit before ``</d>``, the visual plan is still safe to
    use: the mapped soundtrack, rather than prompt-authored speech, is the vocal
    source. Remove balanced blocks, malformed nested blocks, unmatched closing
    tags, and an unterminated final block without altering surrounding visual
    instructions.
    """

    text = str(prompt or "")
    pieces: list[str] = []
    cursor = 0
    depth = 0
    open_start = -1
    for token in _H3_DIALOGUE_TOKEN_RE.finditer(text):
        closing = bool(token.group(1))
        if not closing:
            if depth == 0:
                pieces.append(text[cursor:token.start()])
                open_start = token.start()
            depth += 1
            continue
        if depth:
            depth -= 1
            if depth == 0:
                cursor = token.end()
                open_start = -1
            continue

        # A closing tag without an opening tag is markup noise. Preserve the
        # prose around it, but do not leave the invalid token in the prompt.
        pieces.append(text[cursor:token.start()])
        cursor = token.end()

    if depth:
        # Preserve official sound/music fields if malformed raw text includes
        # them after an unterminated dialogue block. Context-IR parsing usually
        # separates these fields before this helper runs, but this also makes
        # the recovery safe for legacy free-form prompts.
        boundary = _H3_SOUND_BOUNDARY_RE.search(text, max(0, open_start))
        cursor = boundary.start() if boundary else len(text)
    pieces.append(text[cursor:])
    return _normalized_space(" ".join(pieces))


_SPANISH_HINTS = (
    " el ", " la ", " los ", " las ", " que ", " de ", " en ", " es ",
    " un ", " una ", " para ", " con ", " por ", " se ", " pero ",
    " como ", " esto ", " o sea", " muy ", " m\u00e1s ",
)


def _detect_dialogue_language(sample: Any, fallback: str = "English") -> str:
    """Choose the H3 language tag from the scripted dialogue itself.

    The tag used to be hard-coded to English, so a Spanish project emitted
    ``<d>[English] ...</d>`` on every line. Accents or a handful of Spanish
    function words are enough evidence to tag the block correctly.
    """

    text = " " + _normalized_space(str(sample or "")).casefold() + " "
    if not text.strip():
        return fallback
    if re.search(r"[\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1\u00fc\u00bf\u00a1]", text):
        return "Spanish"
    hits = sum(1 for hint in _SPANISH_HINTS if hint in text)
    return "Spanish" if hits >= 4 else fallback


# The six Context-IR field names this module writes. A planner's raw shot
# prompt never contains them: measured across all 150 shots of a real project,
# zero raw sources matched, so the test is an exact discriminator between "a
# prompt to compile" and "a prompt this compiler already produced".
_COMPILED_H3_FIELD_MARKERS = (
    "subject_definitions",
    "retention_analysis",
    "detailed_description",
)


def looks_like_compiled_h3_prompt(text: Any) -> bool:
    """True when ``text`` is already an output of this compiler."""

    value = str(text or "")
    return any(marker in value for marker in _COMPILED_H3_FIELD_MARKERS)


def _dialogue_payload(
    value: Any,
    default_language: str | None = None,
) -> tuple[str, str]:
    """Return ``(language, words)`` from plain or nested H3 dialogue.

    The words are stripped of any speaker marker the planner copied in, because
    Maestro renders the speaker itself; a second, contradictory label inside
    the block is what mis-assigned the voices.

    Language precedence, strongest first: the structured ``language`` field of a Director beat,
    the caller's project language, an inline ``[Language]`` tag, and finally the line's own text.
    A beat's field and the project language are decisions; the inline tag is prose the planner
    hard-codes to ``[English]`` even on Spanish projects, so it cannot outrank either. Nothing is
    defaulted to English up front, because that mistagged Spanish lines the moment nothing else
    had decided -- which is exactly what a hand-edited prompt looks like -- and ``h3_dialogue_tag``
    reads its language through this function.
    """

    structured_language = (
        _normalized_space(_field(value, "language", "")) if isinstance(value, Mapping) else ""
    )
    text_value = _field(value, "spoken_text", value)
    text = _H3_DIALOGUE_TOKEN_RE.sub("", str(text_value or "")).strip()
    language = ""
    language_prefix = re.match(r"^\[([^\]]+)\]\s*(.*)$", text, re.DOTALL)
    if language_prefix:
        language = language_prefix.group(1).strip()
        text = language_prefix.group(2).strip()
    text = _strip_speaker_prefixes(text)
    text = _strip_foreign_script_noise(text)
    if not text:
        raise H3DialogueContractError("MiniMax H3 dialogue contains an empty line.")
    resolved = (
        structured_language
        or _normalized_space(default_language)
        or _normalized_space(language)
        # Nothing named a language. Hard-coding English here mistagged Spanish lines the moment
        # nothing else had decided, which is what a hand-edited prompt does (saving an edit clears
        # the beat cache on purpose). Read the line itself instead.
        or _detect_dialogue_language(text)
    )
    return resolved, text


# A planner marks "this beat has no speech" with a bracketed marker such as
# ``<d>[silent]</d>``. That token is not a language tag, but it has the same
# shape, so it used to be consumed as one -- leaving an empty line and aborting
# the whole render with "dialogue contains an empty line". A no-speech marker
# is a legitimate planning outcome, so it removes the line instead of failing.
_H3_SILENCE_MARKERS = frozenset({
    "silent", "silence", "silencio", "no dialogue", "no dialog", "no line",
    "no speech", "no voice", "no vocals", "no vocal", "none", "n/a", "na",
    "pause", "pausa", "instrumental", "music", "music only", "music_only",
    "solo musica", "solo música", "sin dialogo", "sin diálogo", "sin voz",
    "sin voces", "sin habla", "silencio total", "-", "--", "---", "...",
})
_H3_SILENCE_TOKEN_RE = re.compile(r"^[\[\(]\s*([^\]\)\r\n]{0,32}?)\s*[\]\)]$")
# The planner writes "nothing audible here" as an ellipsis, sometimes followed
# by a marker: "... (silent)", "...", "\u2026". Only a lone marker counted as
# silence, so those placeholders reached the video model as spoken lines.
_H3_ELLIPSIS_RE = re.compile(r"\.{2,}|\u2026")
_H3_MARKER_GROUP_RE = re.compile(r"[\[\(]\s*([^\]\)\r\n]{0,32}?)\s*[\]\)]")
_H3_LANGUAGE_TAG_RE = re.compile(r"^\s*\[[^\]\r\n]{1,24}\]")
_H3_SPOKEN_CHAR_RE = re.compile(r"[A-Za-z0-9\u00c0-\u024f]")


def _h3_is_placeholder_only(text: str) -> bool:
    """True when every remaining token is an ellipsis or a no-speech marker.

    An explicitly empty block is *not* a placeholder: ``<d>[English] </d>``
    keeps its language tag and nothing else, which is a malformed line the
    contract must still reject. Silence is an ellipsis (optionally with a
    marker), which is what the planner actually writes for a wordless stretch.
    """

    residual = _H3_LANGUAGE_TAG_RE.sub(" ", text, count=1)
    placeholder = bool(_H3_ELLIPSIS_RE.search(residual))
    residual = _H3_ELLIPSIS_RE.sub(" ", residual)
    previous = None
    while residual != previous:
        previous = residual
        for match in list(_H3_MARKER_GROUP_RE.finditer(residual)):
            token = match.group(1).strip().casefold().strip(" .:;,;-")
            if token in _H3_SILENCE_MARKERS:
                placeholder = True
                residual = residual.replace(match.group(0), " ", 1)
    return placeholder and not _H3_SPOKEN_CHAR_RE.search(residual)


def is_silent_dialogue(value: Any) -> bool:
    """True when a line carries a no-speech marker instead of spoken words.

    Blank values count as silent too: the caller wants "no line here", not a
    contract violation.
    """

    text = _H3_DIALOGUE_TOKEN_RE.sub(" ", str(value or "")).strip()
    if not text:
        return True
    match = _H3_SILENCE_TOKEN_RE.match(text)
    if match:
        token = match.group(1).strip().casefold().strip(" .:;,;-")
        if token in _H3_SILENCE_MARKERS:
            return True
    # An ellipsis with no words left around it is not a spoken line.
    return _h3_is_placeholder_only(text)


def h3_dialogue_tag(spoken_text: Any, default_language: str | None = None) -> str:
    """Build exactly one canonical H3 dialogue block."""

    language, words = _dialogue_payload(spoken_text, default_language)
    return f"<d>[{language}] {words}</d>"


def _safe_dialogue_tag(
    value: Any,
    default_language: str | None = None,
) -> str | None:
    """Canonicalize one salvaged line, or return None when it has no words."""

    if is_silent_dialogue(value):
        return None
    try:
        return h3_dialogue_tag(value, default_language)
    except H3DialogueContractError:
        return None


def _insert_dialogue_blocks(prompt: str, blocks: Sequence[str]) -> str:
    """Place already-canonical blocks immediately before the sound fields."""

    clean = [block for block in blocks if _normalized_space(block)]
    if not clean:
        return str(prompt or "").strip()
    joined = " ".join(clean)
    text = str(prompt or "").strip()
    if not text:
        return joined

    boundary = _H3_SOUND_BOUNDARY_RE.search(text)
    if boundary:
        prefix = text[:boundary.start()].rstrip()
        suffix = text[boundary.start():].lstrip()
        if prefix:
            return f"{prefix} {joined} {suffix}".strip()
        return f"{joined} {suffix}".strip()
    return f"{text} {joined}".strip()


def _append_canonical_dialogue_blocks(
    prompt: str,
    dialogue_beats: Sequence[Any],
    default_language: str = "English",
) -> str:
    """Append exactly the canonical H3 blocks for each beat, in order."""

    blocks = [
        h3_dialogue_tag(_field(beat, "spoken_text", ""), default_language)
        for beat in dialogue_beats or []
        if _normalized_space(_field(beat, "spoken_text", ""))
    ]
    return _insert_dialogue_blocks(prompt, blocks)


def _repair_unbalanced_dialogue(prompt: str) -> tuple[str, list[str]]:
    """Salvage spoken words from a prompt whose ``<d>`` markup is broken.

    A truncated planner answer, a hand-edited prompt, or an older saved
    project can contain nested blocks, an unmatched closing tag, or a final
    block whose closing tag never arrived. All of those used to abort the
    whole render. Recover every complete line plus the words of an
    unterminated one, remove all markup, and return canonical blocks so the
    caller can rebuild a valid contract deterministically.
    """

    text = str(prompt or "")
    blocks: list[str] = []
    pieces: list[str] = []
    cursor = 0
    depth = 0
    body_start = -1
    for token in _H3_DIALOGUE_TOKEN_RE.finditer(text):
        if not token.group(1):
            if depth == 0:
                pieces.append(text[cursor:token.start()])
                # Keep the outermost opening tag: a nested ``<d><d>`` pair is
                # one line whose inner markup is noise, not a second line.
                body_start = token.end()
            depth += 1
            continue
        if depth:
            depth -= 1
            if depth == 0:
                tag = _safe_dialogue_tag(text[body_start:token.start()])
                if tag:
                    blocks.append(tag)
                cursor = token.end()
                body_start = -1
            continue
        # A closing tag with no opening tag is markup noise. Keep the prose
        # around it but never leave the invalid token in the prompt.
        pieces.append(text[cursor:token.start()])
        cursor = token.end()
    if depth:
        # Unterminated final block: the words stay usable up to the official
        # sound fields, which never contain dialogue.
        boundary = _H3_SOUND_BOUNDARY_RE.search(text, max(0, body_start))
        end = boundary.start() if boundary else len(text)
        tag = _safe_dialogue_tag(text[body_start:end])
        if tag:
            blocks.append(tag)
        cursor = end
    pieces.append(text[cursor:])
    return _normalized_space(" ".join(pieces)), blocks


def _replace_first_outside_dialogue(
    prompt: str,
    needle: str,
    replacement: str,
) -> tuple[str, bool]:
    """Replace an exact plain-text line without touching existing H3 tags."""

    if not needle:
        return prompt, False
    spans, malformed = _dialogue_spans(prompt)
    if malformed:
        return prompt, False
    cursor = 0
    for start, end in [*spans, (len(prompt), len(prompt))]:
        found = prompt.find(needle, cursor, start)
        if found >= 0:
            return (
                f"{prompt[:found]}{replacement}{prompt[found + len(needle):]}",
                True,
            )
        cursor = end
    return prompt, False


def _rewrite_outside_dialogue(prompt: str, rewrite) -> str:
    spans, malformed = _dialogue_spans(prompt)
    if malformed:
        raise H3DialogueContractError(
            "MiniMax H3 dialogue tags are unbalanced after compilation."
        )
    parts: list[str] = []
    cursor = 0
    for start, end in spans:
        parts.append(rewrite(prompt[cursor:start]))
        parts.append(prompt[start:end])
        cursor = end
    parts.append(rewrite(prompt[cursor:]))
    return "".join(parts)


def _strip_empty_h3_scene_transitions(prompt: str) -> str:
    """Remove marker-only transition shells while protecting dialogue text."""

    text = str(prompt or "")
    pieces: list[str] = []
    cursor = 0
    protected_start = -1
    depth = 0
    for token in _H3_DIALOGUE_TOKEN_RE.finditer(text):
        if not token.group(1):
            if depth == 0:
                pieces.append(
                    _H3_EMPTY_SCENE_TRANSITION_RE.sub("", text[cursor:token.start()])
                )
                protected_start = token.start()
            depth += 1
            continue

        if depth == 0:
            continue
        depth -= 1
        if depth == 0 and protected_start >= 0:
            pieces.append(text[protected_start:token.end()])
            cursor = token.end()
            protected_start = -1

    if depth and protected_start >= 0:
        # Leave an unterminated speech block for the dialogue compiler's
        # existing repair/rejection path.
        pieces.append(text[protected_start:])
    else:
        pieces.append(_H3_EMPTY_SCENE_TRANSITION_RE.sub("", text[cursor:]))
    return "".join(pieces)


def _normalize_h3_compiled_visual_shots(visual: str) -> str:
    """Remove empty transition shells and localize timed shot labels.

    Director planners can number a prompt's internal cuts across the full
    sequence. H3 receives one clip at a time, so only timestamped cut labels
    are rebased to that clip's local sequence. Untimed markers stay intact for
    the strict contract validator to judge. Tagged dialogue is kept verbatim.
    """

    text = _strip_empty_h3_scene_transitions(str(visual or ""))
    spans, malformed = _dialogue_spans(text)
    if malformed:
        raise H3DialogueContractError(
            "MiniMax H3 dialogue tags are unbalanced after compilation."
        )

    prose_segments: list[str] = []
    dialogue_segments: list[str] = []
    cursor = 0
    for start, end in spans:
        prose_segments.append(text[cursor:start])
        dialogue_segments.append(text[start:end])
        cursor = end
    prose_segments.append(text[cursor:])

    timed_markers: list[re.Match[str]] = []
    for segment in prose_segments:
        for marker in _H3_TIMED_SHOT_MARKER_RE.finditer(segment):
            preceding = segment[max(0, marker.start() - 45):marker.start()]
            if not _H3_SHOT_REFERENCE_PREFIX_RE.search(preceding):
                timed_markers.append(marker)

    # A monotonic sequence starting beyond Shot 1 can be a planner-wide
    # sequence label, so translate it by one constant offset. Preserve every
    # gap between source labels: compressing [Shot 1], [Shot 3] into [Shot 1],
    # [Shot 2] would hide a malformed local sequence from strict validation.
    # A decrease is also left intact so validation still rejects cut reversal.
    source_numbers = [int(marker.group("number")) for marker in timed_markers]
    can_rebase = bool(source_numbers and source_numbers[0] > 1) and all(
        current >= previous
        for previous, current in zip(source_numbers, source_numbers[1:])
    )
    first_local_number = 2
    if timed_markers:
        first = timed_markers[0]
        first_timestamp = first.group("timestamp")
        clock, _, fraction = first_timestamp.partition(".")
        minutes, seconds = (int(part) for part in clock.split(":"))
        begins_at_zero = minutes == 0 and seconds == 0 and not int(fraction or "0")
        if begins_at_zero:
            # A source's first frame is this clip's Shot 1, even when the
            # planner assigned it a sequence-wide number.
            first_local_number = 1

    source_to_local_offset = (
        first_local_number - source_numbers[0]
        if can_rebase
        else 0
    )

    def localize_segment(segment: str) -> str:
        def localize(match: re.Match[str]) -> str:
            if not can_rebase:
                return match.group(0)
            preceding = segment[max(0, match.start() - 45):match.start()]
            if _H3_SHOT_REFERENCE_PREFIX_RE.search(preceding):
                return match.group(0)

            source_number = int(match.group("number"))
            return f"[Shot {source_number + source_to_local_offset}]"

        return _H3_TIMED_SHOT_MARKER_RE.sub(localize, segment)

    parts: list[str] = []
    for index, segment in enumerate(prose_segments):
        parts.append(localize_segment(segment))
        if index < len(dialogue_segments):
            parts.append(dialogue_segments[index])
    return "".join(parts)


def _sanitize_scripted_ambience(prompt: str) -> str:
    """Remove requests for synthetic background speech around exact lines."""

    def sanitize(segment: str) -> str:
        for pattern, replacement in _H3_SPEECHLIKE_AMBIENCE_REPLACEMENTS:
            segment = pattern.sub(replacement, segment)
        return re.sub(
            r"\bnonverbal room tone(?:\s*(?:,|and)\s*nonverbal room tone)+\b",
            "nonverbal room tone",
            segment,
            flags=re.IGNORECASE,
        )

    return _rewrite_outside_dialogue(prompt, sanitize)


def _insert_visual_detail(prompt: str, label: str, detail: str) -> str:
    prompt = str(prompt or "").strip()
    detail = _normalized_space(detail)
    statement = f"{label}: {detail}"
    suffix = "" if statement.endswith((".", "!", "?")) else "."
    boundary = _H3_SOUND_BOUNDARY_RE.search(prompt)
    if boundary:
        return (
            f"{prompt[:boundary.start()].rstrip()} {statement}{suffix} "
            f"{prompt[boundary.start():].lstrip()}"
        ).strip()
    return f"{prompt} {statement}{suffix}".strip()


def _speaker_map(subjects: Iterable[Any]) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for index, subject in enumerate(subjects or []):
        character_id = _normalized_space(_field(subject, "character_id", ""))
        speaker_name = (
            _h3_identity_label(_field(subject, "speaker_name", ""))
            or _h3_identity_label(_field(subject, "visual_description", ""))
            or _h3_identity_label(character_id)
            or f"visible subject {index + 1}"
        )
        stable_id = f"(S{index + 1})"
        if character_id:
            result[character_id] = (stable_id, speaker_name)
            result[character_id.casefold()] = (stable_id, speaker_name)
        result.setdefault(speaker_name.casefold(), (stable_id, speaker_name))
    return result


def validate_h3_vocal_contract(
    prompt: str,
    dialogue_beats: Sequence[Any] | None = None,
) -> list[str]:
    """Return structural/semantic H3 dialogue errors for a final prompt."""

    prompt = normalize_h3_text(prompt)
    spans, malformed = _dialogue_spans(prompt)
    errors: list[str] = []
    if malformed:
        errors.append("dialogue tags are nested or unbalanced")
    blocks = [prompt[start:end] for start, end in spans]
    actual_words: list[str] = []
    for index, block in enumerate(blocks):
        strict = _H3_STRICT_DIALOGUE_RE.fullmatch(block.strip())
        if not strict or _H3_DIALOGUE_TOKEN_RE.search(strict.group(2) if strict else ""):
            errors.append(f"dialogue block {index + 1} is not canonical")
            continue
        actual_words.append(_normalized_space(strict.group(2)))

    expected_words = []
    for beat in dialogue_beats or []:
        spoken = normalize_h3_text(_field(beat, "spoken_text", ""))
        # A beat with no words -- blank, or bare markup such as "<d>" -- is not
        # a broken line: the compiler skips it, so the validator must skip it
        # too. Checking only for whitespace let "<d>" through, and the resulting
        # "Shot 144: ... contains an empty line" aborted a whole render.
        if is_silent_dialogue(spoken):
            continue
        try:
            expected_words.append(_normalized_space(_dialogue_payload(spoken)[1]))
        except H3DialogueContractError as exc:
            errors.append(str(exc))

    if expected_words:
        if len(blocks) != len(expected_words):
            errors.append(
                f"expected {len(expected_words)} dialogue block(s), found {len(blocks)}"
            )
        if actual_words != expected_words:
            errors.append("dialogue words or speaker order differ from dialogue_beats")
    elif not blocks:
        has_silence_contract = bool(re.search(
            r"\bno (?:one|character) speaks\b",
            prompt,
            flags=re.IGNORECASE,
        ))
        has_mapped_audio_contract = (
            "mapped driving audio" in prompt.casefold()
            and "do not generate additional dialogue" in prompt.casefold()
        )
        if not (has_silence_contract or has_mapped_audio_contract):
            errors.append("silent prompt has no explicit H3 silence contract")
    return errors


def compile_h3_vocal_contract(
    prompt: str,
    subjects: Sequence[Any] | None,
    dialogue_beats: Sequence[Any] | None,
) -> tuple[str, str]:
    """Compile a final, idempotent H3 speech/silence contract.

    Existing dialogue is replaced by *whole top-level blocks*. Balanced nested
    blocks from older Maestro projects are therefore repaired without ever
    performing the unsafe ``prompt.replace(spoken_text, tag)`` operation that
    originally split sentences and produced gibberish.
    """

    prompt = str(prompt or "").strip()
    default_language = _detect_dialogue_language(
        " ".join(
            str(_field(beat, "spoken_text", ""))
            for beat in (dialogue_beats or [])
        ) or prompt,
    )
    valid_beats: list[tuple[Any, str, str]] = []
    for beat in dialogue_beats or []:
        spoken = _field(beat, "spoken_text", "")
        if _normalized_space(spoken) and not is_silent_dialogue(spoken):
            _, words = _dialogue_payload(spoken, default_language)
            valid_beats.append((beat, words, h3_dialogue_tag(spoken, default_language)))

    # Older saved projects do not carry structured dialogue metadata. If they
    # already contain a vocal section, preserve its speaker assignments and
    # repair the full prompt in place instead of deleting the only copy of a
    # line that had been appended inside that section.
    existing_vocal_section = _H3_VOCAL_SECTION_RE.search(prompt)
    if not valid_beats and existing_vocal_section:
        salvaged: list[str] = []
        spans, malformed = _dialogue_spans(prompt)
        if malformed:
            # A saved or hand-edited prompt can carry nested, unmatched, or
            # unterminated tags. Salvage the words and rebuild canonical
            # blocks instead of blocking the render.
            prompt, salvaged = _repair_unbalanced_dialogue(prompt)
            prompt = _insert_dialogue_blocks(prompt, salvaged)
            spans, malformed = _dialogue_spans(prompt)
        if spans:
            prompt = _replace_spans(
                prompt,
                spans,
                [
                    _safe_dialogue_tag(prompt[start:end]) or ""
                    for start, end in spans
                ],
            )
            prompt = _sanitize_scripted_ambience(prompt)
        _, remains_malformed = _dialogue_spans(prompt)
        if remains_malformed:
            raise H3DialogueContractError(
                "MiniMax H3 dialogue tags remain unbalanced after repair."
            )
        errors = validate_h3_vocal_contract(prompt, [])
        if errors:
            raise H3DialogueContractError(
                "Invalid MiniMax H3 vocal contract: " + "; ".join(errors)
            )
        contract_match = _H3_VOCAL_SECTION_RE.search(prompt)
        contract = _normalized_space(
            contract_match.group(0) if contract_match else ""
        )
        return prompt, contract

    prompt = _H3_VOCAL_SECTION_RE.sub(" ", prompt).strip()
    salvaged: list[str] = []
    spans, malformed = _dialogue_spans(prompt)
    if malformed:
        # Broken markup must never abort generation: recover the spoken words
        # here, then rebuild one canonical block per line below.
        prompt, salvaged = _repair_unbalanced_dialogue(prompt)
        if not valid_beats:
            prompt = _insert_dialogue_blocks(prompt, salvaged)
        spans, malformed = _dialogue_spans(prompt)

    instructions: list[str] = []
    if valid_beats:
        prompt = _replace_spans(prompt, spans, [""] * len(spans))
        _, remains_malformed = _dialogue_spans(prompt)
        if remains_malformed:
            raise H3DialogueContractError(
                "MiniMax H3 dialogue tags remain unbalanced after repair."
            )

        mapped = _speaker_map(subjects or [])
        for beat, words, tag in valid_beats:
            speaker_id = _normalized_space(_field(beat, "speaker_id", ""))
            stable_id, speaker_name = mapped.get(
                speaker_id,
                mapped.get(
                    speaker_id.casefold(),
                    (speaker_id or "(S1)", speaker_id or "the visible speaker"),
                ),
            )
            delivery = _clean_h3_metadata(_field(beat, "delivery", ""))
            physical_cue = _clean_h3_metadata(_field(beat, "physical_cue", ""))
            delivery_text = f" with a {delivery} delivery" if delivery else ""
            name = _clean_h3_metadata(speaker_name)
            name_text = f"{name} " if name else ""
            instruction = (
                f"{name_text}{stable_id} speaks{delivery_text}: {words}."
            )
            if physical_cue:
                instruction += f" While speaking, {physical_cue}"
            instructions.append(instruction)

        cleaned, dialogue_blocks = h3_speaker_cues.compile_blocks(prompt, dialogue_beats, subjects, default_language)
        prompt = _insert_dialogue_blocks(cleaned, dialogue_blocks)
        prompt = _sanitize_scripted_ambience(prompt)
        guard = (
            "Only these explicitly tagged lines are spoken, once each and in "
            "the listed order. Do not add, paraphrase, repeat, or improvise "
            "dialogue, muttering, murmuring, gibberish, or speech-like "
            "vocalizations. No background or crowd voices are audible. Anyone "
            "not currently delivering a tagged line remains silent with their "
            "mouth closed except for explicitly described nonverbal actions."
        )
        detail = " ".join([*instructions, guard])
        label = "DIALOGUE AND VOCAL PERFORMANCE"
    elif spans:
        replacements = [
            _safe_dialogue_tag(prompt[start:end]) or "" for start, end in spans
        ]
        prompt = _replace_spans(prompt, spans, replacements)
        _, remains_malformed = _dialogue_spans(prompt)
        if remains_malformed:
            raise H3DialogueContractError(
                "MiniMax H3 dialogue tags remain unbalanced after repair."
            )
        prompt = _sanitize_scripted_ambience(prompt)
        detail = (
            "Only the explicitly tagged dialogue already present in this "
            "prompt is spoken. Do not add, paraphrase, repeat, or improvise "
            "any other words, muttering, murmuring, gibberish, or speech-like "
            "vocalizations. No background or crowd voices are audible. "
            "Non-speaking characters keep their mouths closed."
        )
        label = "DIALOGUE AND VOCAL PERFORMANCE"
    else:
        detail = (
            "No one speaks in this shot. All visible people remain silent and "
            "keep their mouths closed except for explicitly described nonverbal "
            "actions. Generate no muttering, murmuring, gibberish, invented "
            "words, background voices, or speech-like vocalizations."
        )
        label = "SILENCE AND VOCAL PERFORMANCE"

    prompt = _insert_visual_detail(prompt, label, detail)
    errors = validate_h3_vocal_contract(prompt, dialogue_beats)
    if errors:
        raise H3DialogueContractError(
            "Invalid MiniMax H3 vocal contract: " + "; ".join(errors)
        )
    return prompt, f"{label}: {detail}"


def h3_dialogue_budget_violations(
    shot_dicts: Sequence[Mapping[str, Any]],
    durations: Sequence[float] | None = None,
    *,
    words_per_second: float = DIALOGUE_MAX_WORDS_PER_SECOND,
) -> list[dict[str, Any]]:
    """Describe shots whose complete dialogue cannot fit their duration."""

    violations: list[dict[str, Any]] = []
    for index, shot in enumerate(shot_dicts):
        try:
            duration = float(
                durations[index]
                if durations is not None and index < len(durations)
                else shot.get("duration_sec") or 0
            )
        except (TypeError, ValueError):
            duration = 0.0
        word_count = 0
        for beat in shot.get("dialogue_beats") or []:
            spoken = _field(beat, "spoken_text", "")
            if not _normalized_space(spoken):
                continue
            try:
                words = _dialogue_payload(spoken)[1]
            except H3DialogueContractError:
                words = str(spoken or "")
            word_count += len(words.split())
        budget = max(1, int(math.floor(max(0.0, duration) * words_per_second)))
        if word_count > budget:
            violations.append({
                "index": index,
                "title": _normalized_space(shot.get("title") or f"Shot {index + 1}"),
                "duration_sec": duration,
                "word_count": word_count,
                "word_budget": budget,
            })
    return violations


def normalize_h3_text(value: Any) -> str:
    """Repair common UTF-8 mojibake before H3 tokenization.

    Saved Director projects created through a Windows code-page boundary can
    contain the three code points for a UTF-8 punctuation byte sequence (for
    example ``\u00e2\u0080\u0099``) instead of the intended apostrophe.  The
    C1 control bytes are especially harmful in literal dialogue, so repair the
    known sequences and remove any orphaned controls deterministically.
    """

    return repair_text(value)


def _extract_h3_fields(text: str) -> dict[str, str]:
    """Extract known Context-IR fields even from legacy one-line prompts."""

    matches = list(re.finditer(
        r"(?i)(?<![A-Za-z0-9_])(" + "|".join(
            re.escape(field) for field in _H3_ALL_FIELDS
        ) + r")\s*:",
        text,
    ))
    # A compiled field starts its own line, which is how the contract counts it; an inline
    # mention of a label in the prose must not win over the field it names.
    on_line = [m for m in matches if not text[text.rfind("\n", 0, m.start()) + 1:m.start()].strip()]
    declared = {m.group(1).lower(): m for m in reversed(on_line)}
    chosen = (declared.get(m.group(1).lower(), m) for m in matches)
    matches = sorted({m.start(): m for m in chosen}.values(), key=lambda match: match.start())
    fields: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group(1).lower()
        if name in fields:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        fields[name] = text[match.end():end].strip()
    return fields


def _strip_h3_custom_sections(text: str) -> str:
    previous = None
    result = str(text or "")
    while result != previous:
        previous = result
        result = _H3_CUSTOM_SECTION_RE.sub(" ", result)
    return result


def _trim_sentence(value: Any) -> str:
    return _normalized_space(value).strip(" .")


# Rule language, in the languages Director prompts are written in. A context
# that states rules must always be injected, and a bullet test alone missed this
# project's own context: normalisation had already folded its bullets onto one
# line -- "RESTRICCIONES GLOBALES DEL PROYECTO (critico, no negociable): - Este
# proyecto tiene EXACTAMENTE DOS participantes" -- so the block was treated as
# descriptive prose and could be dropped as redundant.
_H3_CONTEXT_RULE_HINTS = (
    "no negociable", "not negotiable", "obligatorio", "obligatoria",
    "mandatory", "prohibido", "prohibida", "forbidden", "exactamente",
    "exactly", "no se permite", "must not", "must ", "mustn't",
    "nunca ", "never ", "siempre ", "always ", "debe ", "deben ",
)


def _context_states_rules(context: str) -> bool:
    """True when the context lists rules rather than describing the scene."""

    text = str(context or "")
    for line in text.splitlines():
        if line.strip().startswith(("-", "*", "\u2022")):
            return True
    if re.search(r"(?:^|\s)[-*\u2022]\s+\S", text):
        return True
    folded = text.casefold()
    return any(hint in folded for hint in _H3_CONTEXT_RULE_HINTS)


def _meaningful_context_present(context: str, body: str) -> bool:
    """Decide whether the body already carries the project context.

    Only descriptive prose may count as already present. A context that states
    rules must always be injected. A real 2.6k-character project context once
    lost its gender lock, its audio-driving rules and its studio setting on 15
    of 150 shots, because four of its words -- "studio", "background",
    "subtle", "lighting" -- happened to appear in the shot's own description of
    the studio, and this heuristic then reported the whole block as redundant.
    """

    if _context_states_rules(context):
        return False
    stop = {
        "about", "after", "again", "being", "from", "have", "into",
        "make", "named", "show", "starring", "that", "their", "this",
        "with", "when", "where", "while",
    }
    words = {
        word.casefold()
        for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]{3,}", context)
        if word.casefold() not in stop
    }
    if not words:
        return True
    body_words = {
        word.casefold()
        for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]{3,}", body)
    }
    # Four shared words prove nothing about a long context. This project's
    # 3,100-character restrictions block matched on the two characters' names
    # alone, so 113 of its 177 shots were told the context was already there and
    # went without it. Scale the bar with the context instead of capping it low.
    required = max(6, math.ceil(len(words) * 0.3))
    return len(words & body_words) >= required


# The project context constrains the *audio*, so the section naming which subject owns which
# voice outranks any other prose in it. A structured one (header, subject definitions, gender
# lock, audio-driven rules, setting, dialogue format) is ~2.6k characters against 360.
# Keep the section-selection path for oversized contexts; H3 publishes no token limit.
_H3_CONTEXT_BUDGET = 3000
# Two tiers: a section that names a voice's pitch or gender outranks one that
# merely mentions speakers, so the rules that assign the voices are kept even
# when a bigger speaker-restriction section precedes them.
_H3_CONTEXT_VOICE_STRONG = (
    "voz aguda", "voz grave", "femenin", "masculin", "female", "male",
    "pitched", "aguda", "grave",
)
_H3_CONTEXT_VOICE_WEAK = (
    "voz", "voce", "voice", "speaker", "habla", "speaks", "timbre",
)


def _context_section_priority(section: str) -> int:
    """Rank a context section: 2 = assigns voices, 1 = mentions speakers."""

    folded = str(section or "").casefold()
    if any(hint in folded for hint in _H3_CONTEXT_VOICE_STRONG):
        return 2
    if any(hint in folded for hint in _H3_CONTEXT_VOICE_WEAK):
        return 1
    return 0


def _cut_context_at_sentence(text: str, limit: int) -> str:
    """Shorten unstructured prose without leaving a dangling period run."""

    window = str(text or "")[:limit]
    boundary = max(
        window.rfind(". "),
        window.rfind("! "),
        window.rfind("? "),
        window.rfind(".\n"),
    )
    if boundary > 0:
        return window[: boundary + 1].rstrip()
    space = window.rfind(" ")
    trimmed = (window[:space] if space > 0 else window).rstrip(" ,;:-.")
    return f"{trimmed}..."


def pack_project_context(
    text: Any,
    limit: int = _H3_CONTEXT_BUDGET,
) -> str:
    """Fit the project context into ``limit`` characters without cutting a line.

    The context arrives as blank-line separated sections of bullet lines. A raw
    ``text[:limit]`` cut used to end mid-sentence and, on any project whose
    context is longer than the budget, deleted the whole voice-rules section --
    the one part that assigns each subject a voice. Whole *sections* are packed
    by priority instead, and a section that cannot fit whole contributes as many
    complete lines as the remaining budget allows. Its heading always travels
    with the first line that is kept, so a rule is never shown without a label,
    and the result always ends on a complete line.

    A heading is never kept on its own: ``SUBJECT DEFINITIONS:`` followed by the
    next section's heading reads as a complete section whose rules are missing,
    which is worse than omitting the section. Sections that do not fit are
    dropped whole, lowest priority first, and the survivors keep document order.
    """

    raw = str(text or "").strip()
    if not raw or len(raw) <= limit:
        return raw
    sections = [section.strip() for section in re.split(r"\n\s*\n", raw) if section.strip()]
    if len(sections) <= 1:
        return _cut_context_at_sentence(raw, limit)

    header = sections[0]
    if len(header) > limit:
        return _cut_context_at_sentence(raw, limit)

    # Rank by what the section is worth to the audio contract, then keep the
    # document order inside each rank.
    ranked = sorted(
        enumerate(sections[1:]),
        key=lambda item: (-_context_section_priority(item[1]), item[0]),
    )

    selected: dict[int, str] = {}
    used = len(header)
    for position, section in ranked:
        lines = [line.strip() for line in section.splitlines() if line.strip()]
        if not lines:
            continue
        heading = lines[0]
        rules = lines[1:]
        if not rules or used + len(heading) + 2 > limit:
            continue
        taken = [heading]
        cost = len(heading) + 2
        for line in rules:
            if used + cost + len(line) + 1 > limit:
                break
            taken.append(line)
            cost += len(line) + 1
        if len(taken) == 1:
            # A heading with no rule under it tells the model nothing.
            continue
        selected[position] = "\n".join(taken)
        used += cost + 2
    if not selected:
        return _cut_context_at_sentence(raw, limit)
    # Survivors keep the order the author wrote them in, not their priority.
    kept = [header, *(selected[index] for index in sorted(selected))]
    # The caller terminates the injected block with its own period.
    return "\n\n".join(kept).rstrip(" .")


_H3_GENERIC_IDENTITY_LABELS = {
    "character", "crowd", "customer", "extra", "listener", "man",
    "narrator", "patron", "person", "speaker", "subject", "the man",
    "the woman", "visible speaker", "woman", "unnamed", "n/a", "n a",
    "na",
}
_H3_NONIDENTITY_REFERENCE_ALIASES = {
    "he", "her", "hers", "him", "his", "it", "its", "she", "their",
    "theirs", "them", "they", "we", "you",
}


def _h3_anchor_key(value: Any) -> str:
    return re.sub(
        r"[^a-z0-9]+",
        " ",
        normalize_h3_text(value).casefold(),
    ).strip()


_H3_PLACEHOLDER_IDENTITY_KEYS = {"unnamed", "n a", "na"}


def _h3_identity_label(value: Any) -> str:
    """Return a usable identity label, excluding empty planner placeholders."""

    label = _normalized_space(value)
    key = _h3_anchor_key(label)
    key = re.sub(r"^(?:the|a|an)\s+", "", key)
    if key in _H3_PLACEHOLDER_IDENTITY_KEYS:
        return ""
    return label


def _h3_is_placeholder_identity(value: Any) -> bool:
    return bool(value) and not _h3_identity_label(value)


def _h3_anchor_present(anchor: Any, text: Any) -> bool:
    wanted = _h3_anchor_key(anchor)
    return not wanted or wanted in _h3_anchor_key(text)


def _h3_context_anchors(values: Iterable[Any]) -> list[str]:
    anchors: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        anchor = _normalized_space(normalize_h3_text(value)).strip(" .;:-")
        key = _h3_anchor_key(anchor)
        if not key or key in seen:
            continue
        seen.add(key)
        anchors.append(anchor)
    return anchors


def _ensure_h3_context_anchors(body: str, anchors: Iterable[Any]) -> str:
    required = _h3_context_anchors(anchors)
    missing = [anchor for anchor in required if not _h3_anchor_present(anchor, body)]
    if not missing:
        return body
    return _normalized_space(
        f"Canonical identity and world: {'; '.join(missing)}. {body}"
    )


# The body field of a compiled Context-IR prompt: where a reviewer's edits live,
# and where a missing canonical anchor has to be added.
_H3_BODY_FIELD_RE = re.compile(
    r"(?mis)^([ \t]*(?:detailed_description|integrated_multimodal_description)[ \t]*:)"
    r"(.*?)(?=^[ \t]*[A-Za-z_][\w_]*[ \t]*:|\Z)"
)


def _ensure_h3_prompt_anchors(prompt: str, anchors: Iterable[Any]) -> str:
    """Add the required anchors to a prompt that is used as it was saved.

    The compiler inserts them into a body it builds, but a reviewed prompt is
    rendered verbatim, so that was the one place a missing anchor could never be
    added -- and the contract refuses a prompt that lacks them. A shot whose
    reviewer had rephrased the background therefore could not satisfy the
    contract at all, and the correction assistant was refused for not inventing
    the sentence. Appended to the body field rather than prepended to the prompt,
    so the six fields keep their order and the reviewed words stay where they are.
    """

    text = str(prompt or "")
    missing = [
        anchor for anchor in _h3_context_anchors(anchors)
        if not _h3_anchor_present(anchor, text)
    ]
    if not missing:
        return prompt
    sentence = f"Canonical identity and world: {'; '.join(missing)}."
    match = _H3_BODY_FIELD_RE.search(text)
    if not match:
        return _normalized_space(f"{sentence} {text}")
    # Insert before the whitespace that closes the field: the next field is found
    # at a line start, so a sentence glued to its label would hide it.
    end = match.end(2)
    prefix = text[:end]
    stripped = prefix.rstrip()
    return f"{stripped} {sentence}{prefix[len(stripped):]}{text[end:]}"


# A Director clip is one continuous shot: the film is joined from the clips, so
# a cut inside one is a cut the join cannot describe. Stated explicitly because
# the project context is injected into every shot and often describes the whole
# film's camera progression and its closing fade, which a shot then performs.
_H3_SINGLE_SHOT_SCOPE = (
    "This is one continuous shot: do not cut, do not switch camera, and do not "
    "fade to black within it."
)

# ``Shot 2 (Medium, 6s):`` style blocks mark a body that deliberately holds
# several shots (storyboard format). Not anchored to a line start: the body is
# whitespace-normalised before the scope line is decided, so the newlines a
# storyboard was written with are already gone by then.
_H3_MULTI_SHOT_BODY_RE = re.compile(r"\bShot\s+\d+\s*[\(:]", re.IGNORECASE)

# The compiled Context-IR body marks its shots with ``[Shot 1]``, which the
# storyboard pattern above does not match, so a body carrying ``[Shot 2]`` used to
# read as a single shot. One clip is one continuous shot: a body that also asks
# for ``[Shot 2]`` makes the model perform both framings inside the same clip, and
# anyone placed in the later one is rendered a second time. Shot 26 of one project
# described Ricardo "visible in the periphery" and then again "in the background
# blur", in two shots inside a 7.29 s clip, and the duplicate man on screen was
# that instruction being obeyed.
_H3_BRACKET_SHOT_RE = re.compile(r"\[\s*Shot\s+(\d+)\s*\]", re.IGNORECASE)


def _is_shot_reference(body: str, start: int) -> bool:
    """Whether a ``[Shot N]`` sits inside a parenthesis, naming a shot.

    A retention row says which shot it keeps: ``<Subject 2> (appears in
    [Shot 1]): fully_preserved``. That is a reference to the shot, not a
    declaration of a new one, and counting it made a one-shot body look like a
    three-shot body. The correction assistant was then told to fix a defect that
    did not exist, rewrote the dialogue lines while trying, and had its answer
    refused -- which is what "the assistant answers with an error and the shot
    never changes" looked like from the outside.
    """

    line_start = body.rfind("\n", 0, start) + 1
    prefix = body[line_start:start]
    return prefix.count("(") > prefix.count(")")


def _declared_shot_numbers(body: str) -> list[int]:
    """Shot numbers a Context-IR body declares, in order of appearance.

    The retention rows that name the shot they keep (``(appears in [Shot 1])``)
    are references, not declarations, so they are not counted.
    """

    text = str(body or "")
    return [
        int(match.group(1))
        for match in _H3_BRACKET_SHOT_RE.finditer(text)
        if not _is_shot_reference(text, match.start())
    ]


def _looks_like_a_multi_shot_body(body: str) -> bool:
    """True when the body intentionally holds more than one shot."""

    text = str(body or "")
    if any(number > 1 for number in _declared_shot_numbers(text)):
        return True
    return len(_H3_MULTI_SHOT_BODY_RE.findall(text)) > 1


def _single_shot_body(body: str) -> str:
    """Leave exactly one shot marker in a clip body.

    The shot-breakdown guide asks for ``[Shot 1]`` at the start and then "later
    cuts begin [Shot N] At MM:SS.mmm", and the planner answered with the film's
    own shot number: clip 10 of a real project arrived as ``[Shot 1] Opening
    composition ... [Shot 11] At MM:37.500, ...``. The compiler prepends its own
    clip-local marker on top of that, so the body declared two shots -- 162 of
    that project's 177 clips carried both. A model told that a later shot
    follows performs that framing inside the same clip, which is how a character
    placed in the later one is rendered a second time.

    The first marker wins and the rest are dropped: the prose after them is this
    clip's own body, so only the misleading label goes, and a beat that carried
    a timestamp stays a beat of the one shot. Storyboard bodies (``Shot 2
    (Medium, 6s):``) do not use brackets and are left untouched.
    """

    text = str(body or "")
    seen = False

    def replace(match: re.Match) -> str:
        nonlocal seen
        if _is_shot_reference(text, match.start()):
            # A retention row naming its shot is not a second declaration.
            return match.group(0)
        if seen:
            # A cut the compiler can localize is upstream's to number: v3 rebases
            # "[Shot 5] At 00:01.000" against this clip's window and the strict validator
            # judges the sequence, so dropping the label here would hide a reversal
            # instead of reporting it. What is left is the shape that rebase cannot
            # read -- an untimed marker, or the literal "At MM:37.500" a planner copies
            # from the guide -- and that is the one this pass exists for.
            if _H3_TIMED_SHOT_MARKER_RE.match(text, match.start()):
                return match.group(0)
            return ""
        seen = True
        return "[Shot 1]"

    normalized = _H3_BRACKET_SHOT_RE.sub(replace, text)
    if not seen:
        return normalized
    # Dropping a marker can leave the space it occupied behind.
    return re.sub(r"[ \t]{2,}", " ", normalized)


_H3_SPOKEN_BLOCK_RE = re.compile(r"<d>.*?</d>", re.DOTALL)
_H3_AT_TIMESTAMP_RE = re.compile(r"\bAt\s+(\d+(?:\.\d+)?)\s*s\b", re.IGNORECASE)
_H3_BEHIND_POSITION_RE = re.compile(
    r"background|behind|periphery|peripheral|out of focus behind",
    re.IGNORECASE,
)

# The action a compiled body attaches to a spoken line: "... </d>. While speaking,
# nodding slowly.". The prompt already tells every speaker to lip-sync globally, so
# when a note asks for lip-sync the only thing left to change is here -- and the
# assistant cannot see it unless it is measured.
_H3_SPEAKING_GESTURE_RE = re.compile(r"While speaking,\s*([^.]{3,90})\.", re.IGNORECASE)

# Head and face movements read as the primary motion and suppress the mouth. Hand
# gestures are left alone: they do not stop a speaker from forming words.
_H3_COMPETING_GESTURE_RE = re.compile(
    r"\bnod|look(?:s|ing)? (?:down|away)|shak(?:e|es|ing)\s+(?:his|her|their)\s+head|"
    r"\btilt|\bblink|eyes (?:close|closed|shut)|turn(?:s|ing)? away",
    re.IGNORECASE,
)

# Every speaker id the prompt mentions anywhere, and the ones it binds to a subject.
# "<Subject 2> (S2) = Ricardo" binds (S2); a field head like "(S4): Valeria" does not,
# which is the whole point: measured on shot 37 the field head declared (S4) and the
# bindings declared (S1) and (S2), and an earlier version of this pattern read the
# field head's colon as a binding and reported the wrong orphan id.
_H3_SPEAKER_ID_RE = re.compile(r"\(([Ss]\d+)\)")
_H3_SPEAKER_BINDING_RE = re.compile(
    r"(?im)(?:<Subject\s*\d+>|Subject\s*\d+)?\s*\(([Ss]\d+)\)\s*(?:=|\u2014|--|\u2013)"
)

# A tag that was left holding nothing: measured on shot 37 of a real project, the
# prose said "as she says: <scenetrans></scenetrans> <scenetrans></scenetrans>" and
# the three tags rendered as nothing at all.
_H3_EMPTY_TAG_RE = re.compile(r"<(?P<tag>[a-zA-Z][\w-]*)>\s*</(?P=tag)>")

# Words that say which gender a described person is, so a speaker's own description
# can be compared with the pronouns the prose gives the person who speaks.
_H3_FEMININE_WORDS_RE = re.compile(
    r"(?i)\b(?:mujer|chica|se[ñn]ora|actriz|femenin\w*|woman|female|girl|she|her|hers)\b"
)
_H3_MASCULINE_WORDS_RE = re.compile(
    r"(?i)\b(?:hombre|chico|se[ñn]or|actor|masculin\w*|man|male|boy|he|him|his)\b"
)
# A sentence that says who is speaking, which is where a mixed-up speaker shows up.
_H3_SPEAKING_SENTENCE_RE = re.compile(
    r"(?i)\b(?:speaks?|speaking|says|saying|delivers?|talks?|talking)\b"
)


def _h3_speaker_ids(text: str) -> set[str]:
    """Every speaker id the text mentions, upper-cased."""

    return {match.group(1).upper() for match in _H3_SPEAKER_ID_RE.finditer(text or "")}


# A rule that names an id only to forbid it: "NUNCA se genera (S3), (S4)". Measured on
# the real prompt, an orphan-id check that read the whole text reported (S3) and (S4)
# as ids in use when the sentence naming them says they must never be used at all.
_H3_ID_PROHIBITION_RE = re.compile(
    r"(?i)\b(?:nunca|jam[\u00e1a]s|never|no se (?:crea|crean|genera|generan|usa|usan))\b"
)


def _h3_id_is_prohibited(text: str, position: int) -> bool:
    """Whether the id at this position is named by a rule that forbids it."""

    line_start = text.rfind("\n", 0, position) + 1
    line_end = text.find("\n", position)
    line = text[line_start:line_end if line_end > 0 else len(text)]
    return bool(_H3_ID_PROHIBITION_RE.search(line))


def _h3_bound_speaker_ids(text: str) -> set[str]:
    """The speaker ids the text binds to a subject or a name."""

    return {
        match.group(1).upper()
        for match in _H3_SPEAKER_BINDING_RE.finditer(text or "")
    }


def _h3_line_speakers(text: str) -> set[str]:
    """The speaker id that introduces each spoken line.

    The id sits in the prose before the line -- "(S2) speaks thoughtfully: <d>..." --
    not inside the tag, so reading the tags alone found no speaker at all: measured on
    shot 37, `h3_dialogue_blocks` returned the four lines and none of them carried an
    id, which is why a check written against the tags saw nothing to compare.
    """

    found: set[str] = set()
    body = str(text or "")
    for match in re.finditer(r"<d\b[^>]*>", body, re.IGNORECASE):
        head = body[max(0, match.start() - 240):match.start()]
        ids = _H3_SPEAKER_ID_RE.findall(head)
        if ids:
            found.add(ids[-1].upper())
    return found


def _h3_speaker_gender(text: str, speaker_id: str) -> str:
    """Which gender the prompt states for a speaker, from the lines that describe it.

    Two kinds of line name a speaker and only one of them describes the person:
    "<Subject 2> (S2) = Ricardo = [Speaker_02] = <Picture 2> + <Audio 2>" binds the id
    and says nothing about the person, while "S2 = Ricardo \u2014 [Speaker_02] | ... |
    Hombre maduro (60), te\u00f3logo laico..." carries the description. Reading only the
    binding line is why the gender came back empty on the real prompt.
    """

    body = str(text or "")
    token = str(speaker_id or "").upper()
    if not token.startswith("S") or not token[1:].isdigit():
        return ""
    padded = f"{int(token[1:]):02d}"
    marks = (f"({token})", f"{token} =", f"{token}=", f"[Speaker_{padded}]", f"[Speaker_{int(token[1:])}]")
    candidates: list[str] = []
    for line in body.splitlines():
        if any(mark in line for mark in marks):
            candidates.append(line)
    # The shortest line that states a gender, not the first one: a compiled prompt
    # carries its identity in a short line ("S2 = Ricardo — [Speaker_02] | ... |
    # Hombre maduro (60)") while the same id also appears in a six-thousand-character
    # body line that mentions both characters, and that line describes both genders.
    # Reading the first match made Ricardo look feminine on two of three audited clips.
    stated = [line for line in candidates if _h3_gender_of(line)]
    if not stated:
        return ""
    return _h3_gender_of(min(stated, key=len))


def _h3_gender_of(text: str) -> str:
    """Which gender a description states, from the words it uses for the person."""

    feminine = len(_H3_FEMININE_WORDS_RE.findall(text or ""))
    masculine = len(_H3_MASCULINE_WORDS_RE.findall(text or ""))
    if feminine > masculine:
        return "feminine"
    if masculine > feminine:
        return "masculine"
    return ""


# The field head of a compiled prompt names the subjects on screen, and the prompt's own
# binding lines say who each Subject is. When they disagree the shot is built on the wrong
# person. Measured on magnifica-humanitas: shots 35, 36, 37, 39 and 41 declare
# "<Subject 1> (S1): Ricardo" while the rule inside the very same prompt says
# "<Subject 1> (S1) = Valeria".
_H3_SUBJECT_FIELD_RE = re.compile(
    r"(?mi)^[ \t]*subject_definitions[ \t]*:[ \t]*(.+)$"
)
_H3_SUBJECT_DECLARED_RE = re.compile(
    r"<Subject\s*(\d+)>\s*(?:\(([Ss]\d+)\))?\s*:?\s*([A-Z\u00c1\u00c9\u00cd\u00d3\u00da\u00d1]"
    r"[\w\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1]*)"
)
_H3_SUBJECT_BOUND_RE = re.compile(
    r"<Subject\s*(\d+)>\s*\(([Ss]\d+)\)\s*=\s*"
    r"([A-Za-z\u00c1\u00c9\u00cd\u00d3\u00da\u00d1\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1]+)"
)


def h3_subject_binding_problems(prompt: str) -> list[str]:
    """Where a shot's own field head names a different person than its own bindings.

    The prompt carries both: the field head says who is on screen ("<Subject 1> (S1):
    Ricardo") and the project's rules say who each Subject is ("<Subject 1> (S1) =
    Valeria"). Nothing compared them, so a shot could be built on the wrong person and
    the next scene could contradict it, which is the thread a director sees when the
    wrong person keeps speaking between shots.
    """

    text = str(prompt or "")
    head = _H3_SUBJECT_FIELD_RE.search(text)
    if not head:
        return []
    declared = {
        match.group(1): match.group(3)
        for match in _H3_SUBJECT_DECLARED_RE.finditer(head.group(1))
    }
    bound = {
        match.group(1): match.group(3)
        for match in _H3_SUBJECT_BOUND_RE.finditer(text)
    }
    problems = []
    for number, person in sorted(declared.items()):
        other = bound.get(number)
        if other and other.lower() != person.lower():
            problems.append(
                f"The shot declares <Subject {number}> as {person}, while the rule in "
                f"the same prompt says <Subject {number}> is {other}. The shot is built "
                "on the wrong person, so the person who speaks can change from one shot "
                "to the next."
            )
    return problems


def h3_shared_line_speaker_problems(
    clips: Sequence[Any], clip_index: int, *, span: int = 2,
) -> list[str]:
    """The same spoken line given to a different speaker in a neighbouring shot.

    Measured on magnifica-humanitas: "datos, a ordenes y a rendimientos." is the last
    line of shot 37 spoken by (S2) and the first line of shot 38 spoken by (S1), so the
    two shots tell different stories about who says it. A shot cannot be judged on its
    own for this: it needs the shots next to it, which is what the director meant by
    "muchas estan hiladas con la siguiente escena".
    """

    def lines_with_speaker(prompt: str) -> dict[str, str]:
        found: dict[str, str] = {}
        body = str(prompt or "")
        for match in re.finditer(r"<d\b[^>]*>(.*?)</d>", body, re.S):
            head = body[max(0, match.start() - 200):match.start()]
            ids = _H3_SPEAKER_ID_RE.findall(head)
            spoken = " ".join(match.group(1).split()).strip()
            if spoken and ids:
                found.setdefault(spoken, ids[-1].upper())
        return found

    try:
        current = str(clips[clip_index].get("video_prompt") or "")
    except (IndexError, AttributeError, TypeError):
        return []
    mine = lines_with_speaker(current)
    if not mine:
        return []
    problems: list[str] = []
    for offset in range(-span, span + 1):
        index = clip_index + offset
        if offset == 0 or not 0 <= index < len(clips):
            continue
        theirs = lines_with_speaker(str(clips[index].get("video_prompt") or ""))
        for spoken, speaker in theirs.items():
            own = mine.get(spoken)
            if own and own != speaker:
                problems.append(
                    f"Shot {clip_index + 1} gives the line \"{spoken[:80]}\" to ({own}), "
                    f"and shot {index + 1} gives the same line to ({speaker}). Both shots "
                    "render the same sentence, so the one that is wrong will be seen "
                    "contradicting the other."
                )
    return problems


# A spoken line whose cue names nobody. The renderer resolves the speaker from a name or a
# <Subject N> tag beside the line and refuses to guess a voice, so such a shot cannot be
# generated at all. Measured on clip 38 of a real project:
# "H3SpeakerBindingError: MiniMax H3 Omni could not determine which referenced character
# speaks '[Spanish] B\u00e1slyamente, un proyecto donde importa much\u00edsimo m\u00e1s la...'".
_H3_SUBJECT_TAG_RE = re.compile(r"<Subject\s*\d+>")
_H3_SUBJECT_NAME_RE = re.compile(
    r"(?mi)^[ \t]*subject_definitions[ \t]*:[ \t]*(.+)$"
)
_H3_DECLARED_NAME_RE = re.compile(
    r"<Subject\s*\d+>\s*(?:\(S\d+\))?\s*:?\s*"
    r"([A-Z\u00c1\u00c9\u00cd\u00d3\u00da\u00d1][\w\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1]+)"
)


def h3_unresolved_speaker_cue_problems(prompt: str, *, window: int = 160) -> list[str]:
    """Spoken lines whose cue identifies no speaker, which makes the shot unrenderable.

    The renderer reads the character from a name or a <Subject N> tag beside the line; a
    bare pronoun is not enough and it refuses rather than hand a line to the wrong voice.
    Measured across the project's 177 clips, two lines have this and both are in clip 38,
    which is exactly the clip that could not be generated: "His voice carries wisdom as he
    says:" names nobody, while the shot declares <Subject 1> = Valeria.
    """

    text = str(prompt or "")
    head = _H3_SUBJECT_NAME_RE.search(text)
    declared: list[str] = []
    if head:
        declared = [
            name for name in _H3_DECLARED_NAME_RE.findall(head.group(1)) if name
        ]
    # The names the prompt binds, too: a cue that says "Ricardo says" is readable even in a
    # shot whose field head happens to declare only the other subject.
    declared.extend(
        match.group(3) for match in _H3_SUBJECT_BOUND_RE.finditer(text)
    )
    field_head_re = re.compile(
        r"(?im)^[ \t]*(?:subject_definitions|summary|retention_analysis|"
        r"detailed_description|integrated_multimodal_description|overall_soundscape|"
        r"non_diegetic_music)[ \t]*:"
    )
    problems: list[str] = []
    previous_end = 0
    for match in re.finditer(r"<d\b[^>]*>(.*?)</d>", text, re.S):
        # The cue is read inside the line's own field: the fields are independent
        # contracts, and reaching back into subject_definitions found the "(S1)" of the
        # field head, which is not the speaker of the line.
        field_start = 0
        for header in field_head_re.finditer(text, 0, match.start()):
            field_start = header.end()
        cue = text[max(previous_end, field_start, match.start() - window):match.start()]
        previous_end = match.end()
        if _H3_SUBJECT_TAG_RE.search(cue) or _H3_SPEAKER_ID_RE.search(cue):
            continue
        if any(name and name in cue for name in declared):
            continue
        spoken = " ".join(match.group(1).split())[:70]
        cue_shown = " ".join(cue.split())[-90:]
        problems.append(
            f"The line \"{spoken}\" is introduced by \"{cue_shown}\", which names no "
            "character and carries no speaker tag. The renderer cannot tell whose voice "
            "reads it and refuses to render the shot: name the speaker beside the line."
        )
    return problems


def _h3_gender_mismatch_finding(
    text: str, spoken_ids: set[str],
) -> str:
    """A spoken line given to one gender while the prose says the other speaks.

    Measured on shot 37 of a real project: every one of the four tagged lines belongs
    to (S2) = Ricardo, whose own description says "Hombre maduro (60)", and the prose
    still said "She speaks to camera about human differences..." and captured "her
    thoughtful expression and natural lip sync as she says". Three correction notes
    about the gender came back refused because the assistant was rewriting pronouns
    it had no measurement for, and the measurement said nothing about them.
    """

    if not spoken_ids:
        return ""
    genders = {
        speaker_id: _h3_speaker_gender(text, speaker_id)
        for speaker_id in sorted(spoken_ids)
    }
    stated = {gender for gender in genders.values() if gender}
    if len(stated) != 1:
        return ""
    speaker_gender = stated.pop()
    other = "masculine" if speaker_gender == "feminine" else "feminine"
    matcher = _H3_FEMININE_WORDS_RE if other == "feminine" else _H3_MASCULINE_WORDS_RE
    culprit_sentences: list[str] = []
    # Two readings, because each one misses what the other finds, and both are true:
    #  - a cue is often glued to the line it introduces ("His voice carries wisdom as he
    #    says: <d>...</d>"), so the sentence is judged with the line cut out of it --
    #    skipping every sentence that holds a tag is what hid the contradiction in clip 38,
    #    which could not be rendered;
    #  - and a speaking sentence standing on its own is judged as it is written, which is
    #    what found the shots whose prose says "she" in a sentence of its own.
    # Together they are a superset of either, so nothing that was named before is lost.
    for sentence in re.split(r"(?<=[.!?])\s+", str(text or "")):
        if "<d>" in sentence:
            continue
        if (
            _H3_SPEAKING_SENTENCE_RE.search(sentence)
            and matcher.search(sentence)
            and _h3_gender_of(sentence) == other
        ):
            culprit_sentences.append(" ".join(sentence.split())[:120])
    for sentence in re.split(r"(?<=[.!?])\s+", str(text or "")):
        cue = re.sub(r"<d\b[^>]*>.*?</d>", " ", sentence, flags=re.S)
        if not _H3_SPEAKING_SENTENCE_RE.search(cue):
            continue
        if matcher.search(cue) and _h3_gender_of(cue) == other:
            shown = " ".join(cue.split())[:120]
            if shown and shown not in culprit_sentences:
                culprit_sentences.append(shown)
    culprits = culprit_sentences
    if not culprits:
        return ""
    ids = ", ".join(f"({speaker_id})" for speaker_id in sorted(spoken_ids))
    return (
        f"The spoken lines belong to {ids}, described here as {speaker_gender}, "
        f"while the prose describes the person who speaks as {other}: "
        + " | ".join(f'"{sentence}"' for sentence in culprits[:2])
        + ". The renderer follows the prose, which is why the wrong person ends up "
        "speaking: the sentence has to name the speaker of the tagged lines."
    )


def h3_dialogue_blocks(prompt: str) -> list[str]:
    """The spoken lines of a compiled prompt, in order, byte for byte."""

    return _H3_SPOKEN_BLOCK_RE.findall(str(prompt or ""))


_H3_LINE_SPEAKER_RE = re.compile(
    r"[(\(]\s*(?:Speaker[_ ]?|S)\s*(\d+)\s*[)\)]",
    re.IGNORECASE,
)


def h3_dialogue_line_speakers(prompt: str) -> list[str]:
    """The speaker bound to each spoken line, in line order.

    A compiled body writes the binding immediately ahead of the block
    (``(S2) speaks with a heavy sigh: <d>...</d>``), so the last marker found
    before a block belongs to that block.
    """

    prompt = str(prompt or "")
    speakers: list[str] = []
    cursor = 0
    for block in _H3_SPOKEN_BLOCK_RE.finditer(prompt):
        marker = None
        for candidate in _H3_LINE_SPEAKER_RE.finditer(prompt, cursor, block.start()):
            marker = candidate
        speakers.append(f"(S{marker.group(1)})" if marker else "")
        cursor = block.end()
    return speakers


def retain_dialogue_beats(
    beats: Sequence[Any] | None,
    prompt: str,
) -> list[dict]:
    """Keep the beats whose lines *prompt* still carries, in line order.

    Saving reviewed prompt text used to clear the beat cache outright. That also
    dropped the plan metadata -- the speaker, the delivery, and the words the
    timing clause is scheduled from -- this compiler needs to rebuild a shot's
    speech contract. A reviewed compiled prompt is rendered verbatim, so nothing
    downstream could restore it and the shot rendered with closed mouths however
    often the note was rewritten. A line the editor deleted must not come back;
    a line the editor kept must keep its plan metadata.
    """

    block_words: list[str] = []
    for block in h3_dialogue_blocks(prompt):
        try:
            block_words.append(_dialogue_payload(block)[1])
        except H3DialogueContractError:
            block_words.append("")
    if not block_words:
        return []
    speakers = h3_dialogue_line_speakers(prompt)
    kept: list[dict] = []
    cursor = 0
    for beat in beats or []:
        spoken = _field(beat, "spoken_text", "")
        if not _normalized_space(spoken) or is_silent_dialogue(spoken):
            continue
        try:
            words = _dialogue_payload(spoken)[1]
        except H3DialogueContractError:
            continue
        if not words:
            continue
        for index in range(cursor, len(block_words)):
            if block_words[index] != words:
                continue
            entry = dict(beat) if isinstance(beat, Mapping) else {"spoken_text": spoken}
            # The prompt is what the user reads and approves, so its binding
            # wins over a stale plan id when the two disagree.
            if index < len(speakers) and speakers[index]:
                entry["speaker_id"] = speakers[index]
            kept.append(entry)
            cursor = index + 1
            break
    return kept


def _h3_subject_display_name(subject: Mapping[str, Any]) -> str:
    """The name a subject row is known by, whatever shape the row arrived in."""

    name = _normalized_space(
        _field(subject, "speaker_name", "") or _field(subject, "character_id", "")
    )
    if name:
        return name
    visual = _normalized_space(_field(subject, "visual_description", ""))
    return visual.split(",")[0].strip() if visual else ""


def diagnose_h3_clip_prompt(
    prompt: str,
    *,
    duration_seconds: float = 0.0,
    subjects: Sequence[Any] | None = None,
    mode: str = "ref2va",
    references: Sequence[Mapping[str, Any]] | None = None,
    context_anchors: Sequence[str] | None = None,
    audio_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """What is measurably wrong with one clip's prompt.

    The correction assistant used to receive only the prompt text and the
    director's note, so it reasoned about a symptom it could not see and left
    causes untouched: shot 26 of one project described Ricardo twice in the
    background, in two shots inside a 7.29 s clip, and three correction notes
    never removed it. These are the facts a reader would take from the saved
    shot, so the assistant reasons from measurements instead of guesses.
    """

    text = str(prompt or "")
    shots = _declared_shot_numbers(text)
    findings: list[str] = []

    try:
        clip_seconds = float(duration_seconds or 0.0)
    except (TypeError, ValueError):
        clip_seconds = 0.0
    if clip_seconds > 0:
        findings.append(f"The clip is {clip_seconds:.2f} seconds long.")

    if shots:
        listed = ", ".join(f"[Shot {number}]" for number in shots)
        if any(number > 1 for number in shots):
            findings.append(f"The body declares {len(shots)} shot(s): {listed}.")
            findings.append(
                "A Director clip is ONE continuous shot, so the model performs "
                "each declared shot inside the same clip: anyone placed in a "
                "later framing is rendered again."
            )
        elif len(shots) > 1:
            # Harmless, but it reads as a storyboard and is worth naming: the
            # format wants one marker per shot.
            findings.append(
                f"The body repeats the {listed} marker {len(shots)} times; the "
                "format expects one marker per clip."
            )
    else:
        findings.append("The body declares no [Shot 1] marker.")

    positions: list[dict[str, str]] = []
    for subject in subjects or []:
        if not isinstance(subject, Mapping):
            continue
        name = _h3_subject_display_name(subject)
        position = _normalized_space(
            _field(subject, "position_or_relation", "")
            or _field(subject, "position", "")
        )
        if name or position:
            positions.append({"subject": name, "position": position})
    behind = [
        row["subject"] or "a subject"
        for row in positions
        if row["position"] and _H3_BEHIND_POSITION_RE.search(row["position"])
    ]
    if behind:
        findings.append(
            "Subject row: "
            + ", ".join(behind)
            + " is placed behind the speaker. For a prompt the director edited the "
            "text is what the model reads, but a shot recompiled from the plan "
            "would carry that placement again."
        )
    if len([row for row in positions if row["subject"]]) > 2:
        findings.append(
            f"The shot declares {len(positions)} subjects; a two-person scene "
            "needs two."
        )

    if clip_seconds > 0:
        beyond = [
            float(value)
            for value in _H3_AT_TIMESTAMP_RE.findall(text)
            if float(value) > clip_seconds
        ]
        if beyond:
            findings.append(
                "Timestamps beyond the end of the clip: "
                + ", ".join(f"{value}s" for value in sorted(set(beyond)))
                + f" (the clip ends at {clip_seconds:.2f}s)."
            )

    blocks = h3_dialogue_blocks(text)
    findings.append(f"The body carries {len(blocks)} spoken <d> line(s).")

    # Contradictions a reader sees at once and the measurement could not name.
    # Measured on shot 37 of a real project: the field head declared
    # "<Subject 1> (S4)" while every binding in the same prompt says only (S1) and
    # (S2) exist, three tags held nothing, and the prose gave the speaking to "she"
    # while all four tagged lines are (S2) = Ricardo. The director asked three times
    # for the gender to be fixed, the assistant had no finding that mentioned it, and
    # its answers rewrote pronouns and were refused: "the assistant is useless, it
    # corrects nothing".
    used_ids = _h3_speaker_ids(text)
    bound_ids = _h3_bound_speaker_ids(text)
    orphan_ids = sorted(
        speaker_id for speaker_id in used_ids - bound_ids
        if not all(
            _h3_id_is_prohibited(text, match.start())
            for match in _H3_SPEAKER_ID_RE.finditer(text)
            if match.group(1).upper() == speaker_id
        )
    )
    if orphan_ids:
        listed = ", ".join(f"({speaker_id})" for speaker_id in orphan_ids)
        bindings = (
            ", ".join(f"({speaker_id})" for speaker_id in sorted(bound_ids))
            or "none"
        )
        findings.append(
            f"The prompt uses the speaker id(s) {listed} that no binding in the same "
            f"prompt declares (it binds {bindings}). Whoever renders it has to invent "
            "a person for that id, and the wrong person can end up speaking."
        )

    empty_tags = [
        match.group("tag") for match in _H3_EMPTY_TAG_RE.finditer(text)
    ]
    if empty_tags:
        listed = ", ".join(f"<{name}></{name}>" for name in empty_tags[:3])
        findings.append(
            f"The body carries {len(empty_tags)} tag(s) that hold nothing: {listed}. "
            "A tag with no text renders as nothing at all, so the line it stands for "
            "is missing from the shot."
        )

    mismatch = _h3_gender_mismatch_finding(text, _h3_line_speakers(text))
    if mismatch:
        findings.append(mismatch)

    findings.extend(h3_subject_binding_problems(text))
    findings.extend(h3_unresolved_speaker_cue_problems(text))

    # The gesture paired with each line, because the global lip-sync instruction is
    # already in the prompt: a note that asks for lip-sync has nothing to add until
    # the action competing with the mouth is named. Clip 13 of a real project told
    # Valeria to lip-sync her one line and, in the same sentence, to nod slowly.
    gestures = [
        match.group(1).strip()
        for match in _H3_SPEAKING_GESTURE_RE.finditer(text)
    ]
    competing = [
        gesture for gesture in gestures
        if _H3_COMPETING_GESTURE_RE.search(gesture)
    ]
    if competing:
        findings.append(
            "A spoken line is paired with a head movement that competes with the "
            "mouth: "
            + "; ".join(f'"{gesture}"' for gesture in competing[:3])
            + ". The prompt already tells every speaker to lip-sync, so this is the "
            "part that has to change: let the speaking mouth do the work and move "
            "the gesture to a beat where that person is silent."
        )

    # Lips turn is decided by the plan, not by the prompt: the orchestrator picks the
    # audio-to-video path only for a shot marked lip-sync critical. Three notes on clip
    # 13 of a real project -- "the woman must faithfully lip-sync S1" -- changed 2, 55
    # and 1 characters and could never work, because the plan said "ambient_only".
    plan_mode = str(_field(audio_plan or {}, "mode", "") or "")
    lip_critical = bool(_field(audio_plan or {}, "lip_sync_critical", False))
    if blocks and not (
        plan_mode in ("audio_driven", "dialogue_driven") and lip_critical
    ):
        findings.append(
            f"The clip's stored audio plan says {plan_mode or '(no mode)'!r} while "
            f"the prompt carries {len(blocks)} spoken line(s). The renderer "
            "reconciles that before choosing the source mode, so the lips can "
            "follow the voice track, but the stored plan is stale and a re-plan "
            "would write the right mode."
        )

    errors = validate_h3_prompt_contract(
        text,
        [],
        mode=mode,
        references=references,
        subjects=subjects,
        context_anchors=context_anchors,
    )
    if errors:
        findings.extend(f"Contract problem: {error}" for error in errors)

    return {
        "shots": shots,
        "duration_seconds": clip_seconds,
        "dialogue_blocks": len(blocks),
        "subject_positions": positions,
        "behind_speaker": behind,
        "errors": list(errors),
        "findings": findings,
    }


def reconcile_audio_plan_with_dialogue(
    audio_plan: Mapping[str, Any] | None,
    *,
    prompt: str = "",
    dialogue_beats: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Make the plan agree with the dialogue the shot actually carries.

    The planner marks a shot "ambient_only" and then writes four spoken lines
    into it: clip 13 of a real project did exactly that, and 104 of that project's
    177 clips carried dialogue under a plan that never asked for lip-sync. The
    orchestrator selects the audio-to-video path only for an audio/dialogue-driven
    shot marked lip-sync critical, so nothing drove the mouths from the voice
    track -- and no wording in the prompt could bring the path back, which is why
    three correction notes on that clip changed 2, 55 and 1 characters and the
    render stayed wrong.

    A shot whose prompt speaks is a dialogue shot, so the plan is corrected rather
    than the prompt. ``generated_audio`` and ``music_driven`` are left alone: the
    first synthesizes its own speech, and the second is a deliberate music
    workflow whose audio drives everything.
    """

    resolved = dict(audio_plan or {})
    speaks = bool(h3_dialogue_blocks(prompt)) or bool(dialogue_beats)
    if not speaks:
        return resolved
    mode = str(_field(resolved, "mode", "") or "").strip().casefold()
    lip_critical = bool(_field(resolved, "lip_sync_critical", False))
    if mode in ("audio_driven", "dialogue_driven") and lip_critical:
        return resolved
    if mode not in ("", "ambient_only", "audio_driven", "dialogue_driven"):
        return resolved
    resolved["mode"] = "dialogue_driven"
    resolved["lip_sync_critical"] = True
    resolved.setdefault("timing_anchor", "audio")
    return resolved


# A model that re-types thousands of characters damages them. Measured on one real
# proposal: "Vestuario" came back as "Vestología", "focus tightens" as "focus tights",
# "jeans oscuros" as "jeans oscamericanos", the speaker rule "(S1) y (S2)" as
# "(S1) y (2)", and a stretch of the dialogue contract as
# "i/s/a/f/d...rced/m/b/c/u/t/i/n/g". The saved prompt was clean in all five places, and
# every check here looked at the spoken lines, the fields and the shots instead.
_GARBLED_TEXT_RE = re.compile(r"(?:\b[A-Za-z0-9]/){4,}[A-Za-z0-9/]*")

_EDITORIAL_LEAK_RE = re.compile(
    r"\bthe current prompt\b|\bthe rewrite\b|\bthe director's note\b|"
    r"^[ \t]*(?:ANALYSIS|QUESTION|OPTIONS|EDITS|FIXED_PROMPT)[ \t]*:",
    re.IGNORECASE | re.MULTILINE,
)


def _edit_distance(left: str, right: str) -> int:
    """Levenshtein distance, small enough for word-length strings."""

    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, start=1):
        current = [i]
        for j, b in enumerate(right, start=1):
            current.append(min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (a != b),
            ))
        previous = current
    return previous[-1]


def _looks_damaged(before: str, after: str) -> bool:
    """True when one word looks like a mangled copy of another."""

    old = str(before or "").strip(".,;:()'\"").casefold()
    new = str(after or "").strip(".,;:()'\"").casefold()
    if not old or not new or old == new or len(new) < 5:
        return False
    if abs(len(old) - len(new)) > 3:
        return False
    if _edit_distance(old, new) <= 2:
        return True
    # "Vestología" against "Vestuario": six edits apart, same opening, same job.
    return len(old) >= 6 and len(new) >= 6 and old[:4] == new[:4]


def _prompt_sentences(text: str) -> list[str]:
    """The sentences of a prompt, with their bullets and field headers."""

    parts = re.split(r"(?<=[.;:])\s+|\n+", str(text or ""))
    return [part.strip() for part in parts if len(part.strip()) > 1]


def h3_project_base_block(project_context: str) -> str:
    """The general block a project puts in every clip, as one piece of text.

    Measured on a real project: the state holds this text once (4,421 characters) and
    every clip is supposed to carry it, but only 67 of 177 did. A clip is rendered on its
    own, so the general instructions have to travel with it -- what must not happen is
    each clip being *edited* separately, which is how "Vestuario" became "Vestología" and
    a speaker rule became "(S1) y (2)".
    """

    context = str(project_context or "").strip()
    if not context:
        return ""
    return (
        "Project context (the whole film; do not perform its progression or its "
        f"closing fade inside this shot): {context}"
    )


# The markers that say a prompt already carries the project's general text.
_H3_BASE_MARKERS = ("RESTRICCIONES GLOBALES", "ESPECIFICACIONES TÉCNICAS")

# Where the general block goes when a prompt is missing it: with the rest of the
# instructions, before the spoken lines it applies to.
_H3_BASE_ANCHORS = (
    "Dialogue timing:",
    "Dialogue:",
    "Only the tagged lines are spoken",
)


def h3_ensure_project_base(prompt: str, project_context: str) -> tuple[str, bool]:
    """The prompt with the project's general block, if it was missing one.

    Idempotent and additive: a prompt that already carries a base is returned untouched,
    because replacing an existing copy is a decision for the director, not for an
    assembly step. What this fixes is the 110 of 177 clips that render with no project
    rules at all, while their state held those rules for every one of them.
    """

    text = str(prompt or "")
    block = h3_project_base_block(project_context)
    if not block or not text.strip():
        return prompt, False
    if any(marker in text for marker in _H3_BASE_MARKERS):
        return prompt, False
    for anchor in _H3_BASE_ANCHORS:
        at = text.find(anchor)
        if at > 0:
            return f"{text[:at].rstrip()} {block} {text[at:].lstrip()}", True
    match = _H3_BODY_FIELD_RE.search(text)
    if match:
        end = match.end(2)
        prefix = text[:end].rstrip()
        return f"{prefix} {block}{text[end:]}", True
    return _normalized_space(f"{text} {block}"), True


def h3_shared_project_phrases(
    prompts: Sequence[str], *, minimum_share: float = 0.25,
) -> list[str]:
    """The sentences every clip of a project carries, which no single clip may edit.

    Measured on a real project of 177 clips: the project ledger, the subject lock, the
    wardrobe, the lighting arc and the technical specs are the same sentences in every
    clip -- 2,770 characters of one 6,184-character prompt, while the direction that
    actually moves the scene is 812. That text is the consistency of the film, it is what
    a model mangles when it re-types a prompt ("Vestuario" became "Vestología", the
    speaker rule "(S1) y (S2)" became "(S1) y (2)"), and it is what 110 of those clips
    were missing entirely.

    A sentence counts as shared when a quarter of the clips carry it: that is a property
    of the data, not a list of Spanish headings. The threshold is low on purpose, because
    the project rules were present in only 67 of 177 clips (38%) and a majority rule
    missed exactly the text that matters.
    """

    counts: Counter = Counter()
    spellings: dict[str, str] = {}
    total = 0
    for text in prompts:
        seen: set[str] = set()
        for sentence in _prompt_sentences(text):
            key = _normalized_space(sentence).casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            counts[key] += 1
            spellings.setdefault(key, sentence.strip())
        total += 1
    if total < 2:
        return []
    threshold = max(2, int(round(total * minimum_share)))
    return [
        spellings[key]
        for key, count in counts.items()
        if count >= threshold and len(key) >= 24
    ]


def h3_frozen_problems(
    original: str, proposal: str, frozen: Sequence[str],
) -> list[str]:
    """A rewrite may not change the text every clip of the project shares.

    That text is the film's consistency, and it is short enough to leave alone: the
    direction of one shot is a few hundred characters, so nothing a director needs
    requires editing the rules, the wardrobe or the lighting arc.
    """

    if not frozen:
        return []
    before = _normalized_space(original).casefold()
    after = _normalized_space(proposal).casefold()
    problems: list[str] = []
    for phrase in frozen:
        key = _normalized_space(phrase).casefold()
        if not key or key not in before or key in after:
            continue
        problems.append(
            "the proposal changes the project's shared text, which every clip carries "
            f"verbatim: {_normalized_space(phrase)[:90]!r}"
        )
    return problems


def h3_clip_spans(prompt: str, frozen: Sequence[str]) -> list[tuple[int, int]]:
    """The spans of a prompt that are this clip's own, not the project's shared text.

    Measured on a real project: 2,770 of one clip's 6,184 characters are text every clip
    carries, and the direction that moves the scene is 812. Showing the whole prompt made a
    two-sentence change a hunt through six thousand characters, and handed the model
    thousands of characters it could damage on the way back. This is the part that varies:
    the only part worth showing, and the only part worth editing.
    """

    text = str(prompt or "")
    blocked = bytearray(len(text))
    for phrase in frozen or []:
        needle = str(phrase or "").strip()
        if not needle:
            continue
        start = 0
        while True:
            at = text.find(needle, start)
            if at < 0:
                break
            blocked[at:at + len(needle)] = b"\x01" * len(needle)
            start = at + 1
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(text):
        if blocked[index]:
            index += 1
            continue
        end = index
        while end < len(text) and not blocked[end]:
            end += 1
        spans.append((index, end))
        index = end
    return spans


def h3_clip_text(prompt: str, frozen: Sequence[str]) -> str:
    """The clip's own text: what the assistant may change and what the review shows."""

    text = str(prompt or "")
    joined = "".join(text[start:end] for start, end in h3_clip_spans(text, frozen))
    return re.sub(r"\n{3,}", "\n\n", joined).strip()


def h3_proposal_warnings(prompt: str, proposal: str) -> list[str]:
    """Words that look damaged while re-typing rather than corrected.

    A correction changes what the note asked for; damage looks different from the
    outside -- the same number of words, one of them mangled. Measured on a real
    proposal: "Vestuario" came back as "Vestología" and "focus tightens" as "focus
    tights", inside text no check looks at. Comparing the whole vocabulary was tried
    first and flagged unrelated words instead ("last" against "loft"), so only
    same-length replacements are compared, which is what a typo is. These are warnings
    rather than refusals: a correction may introduce a word, it is just not allowed to
    do it invisibly six thousand characters into a diff.
    """

    left = str(prompt or "").split()
    right = str(proposal or "").split()
    warnings: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, left, right).get_opcodes():
        if tag != "replace" or (i2 - i1) != (j2 - j1) or (i2 - i1) > 2:
            continue
        for old, new in zip(left[i1:i2], right[j1:j2]):
            if _looks_damaged(old, new):
                shown_new = new.strip(".,;:()'\"")
                shown_old = old.strip(".,;:()'\"")
                warning = (
                    f"posible errata: '{shown_new}' donde el prompt dice '{shown_old}'"
                )
                if warning not in warnings:
                    warnings.append(warning)
    return warnings


def review_h3_revision(
    original: str,
    revised: str,
    *,
    duration_seconds: float = 0.0,
    subjects: Sequence[Any] | None = None,
    mode: str = "ref2va",
    references: Sequence[Mapping[str, Any]] | None = None,
    context_anchors: Sequence[str] | None = None,
    frozen: Sequence[str] | None = None,
) -> list[str]:
    """Why a rewritten prompt must not reach the editor.

    The assistant is told to keep the spoken lines and a single shot, but a rule
    in a prompt is a request, not a guarantee. A rewrite that drops a <d> line or
    keeps a [Shot 2] would otherwise be discovered by rendering the shot, which
    is the most expensive way to find a typo. An empty list means the rewrite is
    safe to hand over.
    """

    text = str(revised or "")
    problems: list[str] = []

    before = h3_dialogue_blocks(original)
    after = h3_dialogue_blocks(text)
    if before != after:
        if len(before) != len(after):
            problems.append(
                f"The rewrite has {len(after)} spoken line(s) instead of "
                f"{len(before)}: the spoken words must survive untouched."
            )
        else:
            problems.append(
                "The rewrite changed the spoken words. The <d> lines must be "
                "copied exactly, including their [Language] tag."
            )

    shots = _declared_shot_numbers(text)
    if any(number > 1 for number in shots):
        listed = ", ".join(f"[Shot {number}]" for number in shots)
        problems.append(
            f"The rewrite still declares several shots ({listed}). A clip is "
            "one continuous shot: fold them into a single [Shot 1]."
        )
    elif not shots:
        problems.append("The rewrite lost its [Shot 1] marker.")

    garbled = _GARBLED_TEXT_RE.search(text)
    if garbled:
        problems.append(
            "the rewrite contains text that is not prose "
            f"({garbled.group(0)[:50]!r}), which is what re-typing thousands of "
            "characters does to them. Send the sentences to change instead, as EDITS."
        )
    leaked = _EDITORIAL_LEAK_RE.search(text)
    if leaked:
        problems.append(
            "the rewrite contains editorial text instead of only the prompt "
            f"({leaked.group(0)[:50]!r})."
        )
    # The shared text is the film's consistency and no shot may edit it: the direction of
    # one shot is a few hundred characters, so nothing a director needs is in there.
    problems.extend(h3_frozen_problems(original, text, frozen or []))

    # A rewrite is judged on what it changed, not on what it inherited: a reviewed
    # prompt is rendered verbatim, so the compiler never inserted the canonical anchors
    # and requiring a rewrite to invent them refused every answer for such a shot. An
    # anchor the original carried and the rewrite dropped is still refused: that is a
    # change.
    inherited = validate_h3_prompt_contract(
        str(original or ""),
        [],
        mode=mode,
        references=references,
        subjects=subjects,
        context_anchors=context_anchors,
    )
    for problem in validate_h3_prompt_contract(
        text,
        [],
        mode=mode,
        references=references,
        subjects=subjects,
        context_anchors=context_anchors,
    ):
        if problem not in inherited:
            problems.append(problem)
    return problems


def h3_restore_shot_marker(original: str, revised: str) -> str:
    """Put the ``[Shot 1]`` marker back when a rewrite dropped it.

    Measured on a real correction of shot 109: the assistant named the cause correctly -- the prose
    describes as feminine the person whose three spoken lines belong to (S2), described as
    masculine -- and the whole answer was thrown away with "La propuesta fue rechazada: The rewrite
    lost its [Shot 1] marker." The marker is written by Maestro: the compiler inserts it into the
    body it builds, so an answer that rewrites a shot's text can legitimately not carry one, and
    refusing the correction over it costs the director the fix it asked for.

    Restored where the original had it, by anchoring on the words that follow it; when those words
    are not in the rewrite either, the marker goes at the head of the action field, which is where
    the compiler writes it. Nothing else is touched.
    """

    text = str(revised or "")
    if _declared_shot_numbers(text):
        return text
    if 1 not in _declared_shot_numbers(str(original or "")):
        # The prompt being corrected does not carry one either: there is nothing to restore, and
        # requiring one would refuse every answer, which is the fault this function exists for.
        return text
    anchor = re.search(r"\[Shot\s+1\]\s*(.{0,60})", str(original or ""), re.S | re.IGNORECASE)
    if anchor:
        tail = anchor.group(1).strip()
        if tail and tail in text:
            return text.replace(tail, f"[Shot 1] {tail}", 1)
    head = re.search(
        r"(?mi)^[ \t]*(?:detailed_description|integrated_multimodal_description)[ \t]*:[ \t]*",
        text,
    )
    if head:
        return text[: head.end()] + "[Shot 1] " + text[head.end():]
    return text

def _source_prompt_parts(
    prompt: str,
    *,
    project_context: str = "",
    context_anchors: Sequence[str] | None = None,
    opening_blocking: str = "",
    closing_blocking: str = "",
    audio_plan: Mapping[str, Any] | None = None,
    audio_policy: Mapping[str, Any] | None = None,
    has_driving_audio: bool = False,
) -> tuple[str, str, str, list[str]]:
    """Recover one concise visual timeline and the two official sound fields."""

    text = normalize_h3_text(prompt).strip()
    spans, _ = _dialogue_spans(text)
    existing_blocks = [
        tag
        for tag in (
            _safe_dialogue_tag(normalize_h3_text(text[start:end]))
            for start, end in spans
        )
        if tag
    ]
    fields = _extract_h3_fields(text)
    body = (
        fields.get("detailed_description")
        or fields.get("integrated_multimodal_description")
        or text
    )
    soundscape = fields.get("overall_soundscape", "")
    music = fields.get("non_diegetic_music", "")
    if not (
        fields.get("detailed_description")
        or fields.get("integrated_multimodal_description")
    ):
        boundary = _H3_SOUND_BOUNDARY_RE.search(body)
        if boundary:
            body = body[:boundary.start()]

    if not soundscape:
        match = _H3_SOUND_PROSE_RE.search(body)
        if match:
            soundscape = match.group(1)
            body = f"{body[:match.start()]} {body[match.end():]}"
    if not music:
        match = _H3_MUSIC_PROSE_RE.search(body)
        if match:
            music = match.group(1)
            body = f"{body[:match.start()]} {body[match.end():]}"

    # Legacy Maestro added a long context anchor before the actual prompt.
    # The planner's native body starts at this sentence, so retain everything
    # after it and discard only the duplicated wrapper.
    marker = _H3_META_SENTENCE_RE.search(body)
    if marker and body.lstrip().lower().startswith("project continuity"):
        body = body[marker.end():]
    body = _H3_META_SENTENCE_RE.sub("", body)
    body = _strip_h3_custom_sections(body)
    # A planner may serialize a transition with no action after its timed
    # marker. Remove that empty shell while the body is still source text so
    # Ref2VA's style-opening split cannot mistake the trailing marker for the
    # boundary between its opening and visual timeline.
    body = _strip_empty_h3_scene_transitions(body)
    body = re.sub(
        r"^\s*(?:integrated_multimodal_description|detailed_description)\s*:\s*",
        "",
        body,
        flags=re.IGNORECASE,
    )
    # Decided from the incoming text: normalising whitespace below collapses the
    # per-shot lines a storyboard body is written with.
    multi_shot_source = _looks_like_a_multi_shot_body(prompt)
    body = re.sub(r"^\s*\[Shot\s+1\]\s*", "", body, flags=re.IGNORECASE)
    body = _normalized_space(body)
    body, reasoning_dropped = drop_reasoning_sentences(body)
    if reasoning_dropped:
        print(
            "[MiniMax H3] Removed "
            f"{reasoning_dropped} sentence(s) of planner reasoning that leaked "
            "into the shot description."
        )

    plan = audio_plan if isinstance(audio_plan, Mapping) else {}
    audio_mode = _normalized_space(_field(plan, "mode", "")).casefold()
    context = _h3_injected_project_context(project_context)
    # The single-shot guard is decided here but is NOT conditional on the
    # context: it used to share this branch, so every shot whose body looked
    # like it already carried the context lost the guard with it. 113 of a real
    # project's 177 shots were left free to cut and fade inside a single clip.
    scope = "" if multi_shot_source else f"{_H3_SINGLE_SHOT_SCOPE} "
    if scope and _H3_SINGLE_SHOT_SCOPE in body:
        # A compiled prompt fed back through the compiler already carries the guard: the
        # recompile path, the correction assistant and the saved-plan refresh all do this.
        # Prepending it again states it twice and makes the compile non-idempotent.
        scope = ""
    # A music-driven shot keeps the song-wide brief available to the vocal helpers, but its own
    # prose and anchors stay authoritative for its visual action; the brief can name actions
    # assigned to later clips.
    if (
        context
        and audio_mode != "music_driven"
        and not _meaningful_context_present(context, body)
    ):
        # The project context describes the WHOLE film and is injected into every
        # shot. A real project's context read "the lighting and framing evolve
        # progressively: starts on medium shots ... moves to close-ups ...
        # returns to a wide ... reaches extreme close-ups ... and closes in a
        # cosy wide shot with a slow fade in the outro", and 106 of that run's
        # 177 clips carried that sentence verbatim. A Director clip is ONE
        # continuous shot, so the model performed the entire arc -- and the
        # film's closing fade -- inside every clip: the reported "sudden camera
        # changes of seconds and constant fade-outs".
        body = (
            f"{scope}Project context (the whole film; reference only, never spoken aloud "
            f"and not part of the dialogue; do not perform its progression or its closing fade "
            f"inside this shot): {context}. {body}"
        ).strip()
    elif scope:
        body = f"{scope}{body}".strip()
    body = _ensure_h3_context_anchors(body, context_anchors or [])

    opening = drop_planner_reasoning(opening_blocking, 320)
    if opening and opening.casefold() not in body.casefold():
        body = f"Opening composition: {opening}. {body}".strip()
    closing = drop_planner_reasoning(closing_blocking, 320)
    # Native shot prompts already carry a concise ``Final beat``. Appending
    # Director's expanded closing cast table repeated every identity, outfit,
    # and position near the end of the prompt, making the actual action and
    # dialogue less prominent to the conditioner.
    if (
        closing
        and "final beat:" not in body.casefold()
        and closing.casefold() not in body.casefold()
    ):
        body = f"{body} By the final beat, {closing}.".strip()

    if not _trim_sentence(soundscape):
        sound_bits = []
        ambience = _trim_sentence(plan.get("ambience", ""))
        if ambience:
            sound_bits.append(ambience)
        sound_bits.extend(
            _trim_sentence(effect)
            for effect in (plan.get("effects") or [])
            if _trim_sentence(effect)
        )
        soundscape = ", ".join(sound_bits) or "Natural scene-appropriate stereo ambience"
    # Small local planners occasionally emit the requested Context-IR labels
    # inline after first writing a prose prompt. The prose music extractor can
    # then capture the repeated visual/sound fields (and even a non-canonical
    # ``<d>`` block) as part of non_diegetic_music. Auxiliary fields never own
    # dialogue, so trim at the first nested field and remove any dialogue block
    # before final validation. The visual compiler retains/canonicalizes the
    # authoritative copy when scripted dialogue is actually present.
    def clean_auxiliary(value: Any) -> str:
        cleaned = normalize_h3_text(value)
        boundary = _H3_AUXILIARY_SECTION_BOUNDARY_RE.search(cleaned)
        if boundary:
            cleaned = cleaned[:boundary.start()]
        return _strip_dialogue_for_driving_audio(cleaned)

    soundscape = _trim_sentence(
        _sanitize_scripted_ambience(clean_auxiliary(soundscape))
    )
    music = _trim_sentence(clean_auxiliary(music)) or "N/A"
    if music.casefold() in {"n/a", "none", "no music"}:
        music = "N/A"
    if _audio_policy_flag(audio_policy, "generated_music_allowed") is False:
        body = _strip_generated_music_instructions(
            body,
            has_driving_audio=has_driving_audio,
        )
        soundscape = _strip_music_mentions_from_soundscape(
            soundscape,
            has_driving_audio=has_driving_audio,
        ) or "Natural scene-appropriate stereo ambience"
        music = (
            "Use the mapped driving audio according to retention_analysis."
            if has_driving_audio
            else "N/A"
        )
    return body, soundscape, music, existing_blocks


def _speaker_registry_entry(
    registry: Mapping[str, Any], key: str,
) -> tuple[str, str] | None:
    value = registry.get(key) or registry.get(key.casefold())
    if isinstance(value, Mapping):
        stable_id = _normalized_space(value.get("stable_id", ""))
        name = _normalized_space(value.get("speaker_name", ""))
    elif value:
        stable_id = _normalized_space(value)
        name = ""
    else:
        return None
    if stable_id and not stable_id.startswith("("):
        stable_id = f"({stable_id})"
    return stable_id, _h3_identity_label(name)


def _subject_name_for_key(subjects: Sequence[Any], key: str) -> str:
    folded = key.casefold()
    for subject in subjects or []:
        character_id = _normalized_space(_field(subject, "character_id", ""))
        speaker_name = _h3_identity_label(_field(subject, "speaker_name", ""))
        if folded == character_id.casefold() and not _h3_is_placeholder_identity(key):
            return speaker_name or _h3_identity_label(character_id) or "the visible speaker"
        if speaker_name and folded == speaker_name.casefold():
            return speaker_name
    return _h3_identity_label(key) or "the visible speaker"


def _set_h3_subject_field(subject: Any, key: str, value: Any) -> None:
    if isinstance(subject, MutableMapping):
        subject[key] = value
    elif hasattr(subject, key):
        setattr(subject, key, value)


def _h3_subjects_for_physical_state(
    subjects: Sequence[Any] | None,
    physical_state: Any,
) -> Sequence[Any] | None:
    """Keep temporary blocking out of H3's clip-long subject definitions."""

    state = normalize_physical_state(physical_state)
    if not state or not any(
        state.get(key) for key in ("opening", "transitions", "closing")
    ):
        return subjects

    sanitized_subjects: list[Any] = []
    for subject in subjects or []:
        if isinstance(subject, Mapping):
            serialized = dict(subject)
        else:
            try:
                serialized = dict(vars(subject))
            except TypeError:
                # H3 planner subjects are JSON mappings. Unknown opaque subject
                # objects remain untouched rather than being mutated in place.
                sanitized_subjects.append(subject)
                continue
        description = _field(serialized, "visual_description", "")
        if description:
            serialized["visual_description"] = identity_only_description(description)
        sanitized_subjects.append(serialized)
    return sanitized_subjects


def _leading_h3_identity(value: Any) -> str:
    text = _normalized_space(value)
    proper = r"[A-Z][A-Za-z0-9'’-]+"
    match = (
        # Shot planners most commonly use ``Rachel: ...`` or
        # ``George Costanza (char_2): ...``. A single capitalized token is
        # trustworthy before those explicit identity delimiters.
        re.match(rf"^({proper}(?:\s+{proper}){{0,2}})(?=\s*(?:\(|:))", text)
        # Before ordinary descriptive punctuation, require a multi-token name
        # so adjectives such as "Massive," or hyphenated wardrobe colors do
        # not become phantom characters.
        or re.match(rf"^({proper}(?:\s+{proper}){{1,2}})(?=\s*(?:,|—|–|-))", text)
        or re.match(
            rf"^({proper}(?:\s+{proper}){{0,2}})(?=\s+(?:is|wears|stands|"
            r"sits|walks|enters|from|in)\b)",
            text,
        )
    )
    if not match:
        return ""
    candidate = match.group(1).strip()
    if (
        candidate.casefold() in _H3_GENERIC_IDENTITY_LABELS
        or _h3_is_placeholder_identity(candidate)
    ):
        return ""
    return candidate


_H3_LOCAL_CAST_SLOT_RE = re.compile(
    r"^(?:char(?:acter)?|subject|speaker)[_-]?\d+$",
    re.IGNORECASE,
)


def _h3_identity_names_match(left: Any, right: Any) -> bool:
    """Return true for a full cast name and its unambiguous short form."""

    left_key = _h3_anchor_key(left)
    right_key = _h3_anchor_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    left_parts = left_key.split()
    right_parts = right_key.split()
    if len(left_parts) == 1:
        return left_parts[0] in {right_parts[0], right_parts[-1]}
    if len(right_parts) == 1:
        return right_parts[0] in {left_parts[0], left_parts[-1]}
    return False


def _h3_subject_identity_evidence(subject: Any) -> tuple[str, list[str]]:
    """Read the local shot's identity without trusting a stale global label.

    Bounded long-form sequence writers legitimately reuse ``char_1`` and
    ``char_2`` for different guest casts.  Older project compilation then
    stamped one globally selected name onto every matching slot.  The leading
    name in the shot-local visual description is more authoritative when it
    directly contradicts that stale label.
    """

    name = _h3_identity_label(_field(subject, "speaker_name", ""))
    inferred = _leading_h3_identity(
        _field(subject, "visual_description", "")
    )
    if inferred and name and not _h3_identity_names_match(inferred, name):
        return inferred, [inferred]
    candidates = [
        candidate for candidate in (name, inferred)
        if candidate
        and candidate.casefold() not in _H3_GENERIC_IDENTITY_LABELS
        and not _h3_is_placeholder_identity(candidate)
        and not _H3_LOCAL_CAST_SLOT_RE.fullmatch(candidate)
    ]
    if not candidates:
        return "", []
    best = max(
        candidates,
        key=lambda candidate: (
            len(_h3_anchor_key(candidate).split()),
            int(not candidate.isupper()),
            len(candidate),
        ),
    )
    return best, candidates


def _h3_identity_slug(value: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", _h3_anchor_key(value)).strip("_")
    return slug[:56] or "subject"


def _h3_direct_speaker_mentions(value: Any, name: str) -> int:
    """Count explicit action statements which assign speech to ``name``.

    This deliberately recognizes only direct grammatical attributions such as
    ``Thanos speaks`` or ``Thanos begins to speak``.  A loose proximity search
    would misread action such as ``Thanos watches as Rachel speaks`` and move
    Rachel's line to the wrong face.
    """

    text = normalize_h3_text(value)
    clean_name = _normalized_space(name)
    if not text or not clean_name:
        return 0
    pattern = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(clean_name)}(?![A-Za-z0-9])"
        r"(?:\s*\([^)]{0,80}\))?\s+"
        r"(?:visibly\s+)?"
        r"(?:(?:begins?|starts?|continues?|finishes?)\s+(?:to\s+)?)?"
        r"(?:speaks?|says?|asks?|replies?|answers?|shouts?|yells?|"
        r"declares?|whispers?|delivers?)\b",
        flags=re.IGNORECASE,
    )
    return len(pattern.findall(text))


def _repair_h3_phantom_dialogue_subjects(
    clip_plans: Sequence[Mapping[str, Any]],
) -> None:
    """Merge a misspelled dialogue-only person back into the visible actor.

    A long-form screenplay occasionally misspells an established heading
    (for example ``THANOS`` -> ``THORNS``).  Pass 2 historically converted the
    typo into a new ``dialogue_thorns`` subject, placed it beside Thanos, and
    then handed both identities to Ref2VA.  That is a literal instruction to
    render the principal twice.

    Repair is intentionally evidence-bound: the unknown subject must use a
    synthetic ``dialogue_*`` id, another visible subject must be explicitly
    assigned speech in the shot action, and that attribution must identify one
    unique person.  Once established, the same typo is repaired across later
    clips in the bounded sequence.  Ambiguous or genuinely independent
    speakers are left untouched for the normal contract validator.
    """

    alias_targets: dict[str, str] = {}

    # Discover a reliable identity for each synthetic alias before mutating
    # anything, so a later clip can reuse evidence established in an earlier
    # one even when the later action contains only a reaction beat.
    for plan in clip_plans:
        if not isinstance(plan, Mapping):
            continue
        subjects = [
            subject for subject in plan.get("_director_subjects_on_screen") or []
            if isinstance(subject, Mapping)
        ]
        speaking_ids = {
            _normalized_space(beat.get("speaker_id", "")).casefold()
            for beat in plan.get("_director_dialogue_beats") or []
            if isinstance(beat, Mapping)
            and _normalized_space(beat.get("spoken_text", ""))
        }
        source = " ".join(filter(None, [
            _normalized_space(plan.get("_director_h3_source_prompt", "")),
            _normalized_space(plan.get("video_prompt_pre_polish", "")),
        ]))
        for phantom in subjects:
            phantom_id = _normalized_space(phantom.get("character_id", ""))
            if (
                not phantom_id.casefold().startswith("dialogue_")
                or phantom_id.casefold() not in speaking_ids
            ):
                continue
            phantom_name = _normalized_space(
                _h3_identity_label(phantom.get("speaker_name", "")) or phantom_id
            )
            alias_keys = {
                phantom_id.casefold(),
                phantom_name.casefold(),
            }
            if any(key in alias_targets for key in alias_keys):
                continue
            attributed: list[tuple[int, str]] = []
            for candidate in subjects:
                candidate_id = _normalized_space(candidate.get("character_id", ""))
                if not candidate_id or candidate_id.casefold() == phantom_id.casefold():
                    continue
                candidate_name, _evidence = _h3_subject_identity_evidence(candidate)
                if not candidate_name:
                    candidate_name = (
                        _h3_identity_label(candidate.get("speaker_name", ""))
                        or _h3_identity_label(candidate_id)
                    )
                count = _h3_direct_speaker_mentions(source, candidate_name)
                if count:
                    attributed.append((count, candidate_name))
            if not attributed:
                continue
            best_count = max(count for count, _name in attributed)
            best_names = {
                name for count, name in attributed if count == best_count
            }
            if len(best_names) != 1:
                continue
            target_name = next(iter(best_names))
            for key in alias_keys:
                alias_targets[key] = target_name

    repaired = 0
    for plan in clip_plans:
        if not isinstance(plan, MutableMapping):
            continue
        subjects = [
            subject for subject in plan.get("_director_subjects_on_screen") or []
            if isinstance(subject, MutableMapping)
        ]
        replacements: list[tuple[str, str, str, str]] = []
        for phantom in subjects:
            phantom_id = _normalized_space(phantom.get("character_id", ""))
            phantom_name = _normalized_space(
                _h3_identity_label(phantom.get("speaker_name", "")) or phantom_id
            )
            target_name = (
                alias_targets.get(phantom_id.casefold())
                or alias_targets.get(phantom_name.casefold())
            )
            if not target_name:
                continue
            target = next((
                candidate for candidate in subjects
                if candidate is not phantom
                and _h3_identity_names_match(
                    _h3_subject_identity_evidence(candidate)[0]
                    or _h3_identity_label(candidate.get("speaker_name", ""))
                    or _h3_identity_label(candidate.get("character_id", "")),
                    target_name,
                )
            ), None)
            if target is None:
                continue
            target_id = _normalized_space(target.get("character_id", ""))
            canonical_name = _normalized_space(
                _h3_identity_label(target.get("speaker_name", "")) or target_name
            )
            if not target_id or not canonical_name:
                continue
            replacements.append((
                phantom_id,
                phantom_name,
                target_id,
                canonical_name,
            ))

        if not replacements:
            continue
        phantom_ids = {old_id.casefold() for old_id, _old, _new_id, _new in replacements}
        plan["_director_subjects_on_screen"] = [
            subject for subject in subjects
            if _normalized_space(subject.get("character_id", "")).casefold()
            not in phantom_ids
        ]
        for beat in plan.get("_director_dialogue_beats") or []:
            if not isinstance(beat, MutableMapping):
                continue
            speaker_id = _normalized_space(beat.get("speaker_id", ""))
            for old_id, old_name, new_id, new_name in replacements:
                if speaker_id.casefold() != old_id.casefold():
                    continue
                beat["speaker_id"] = new_id
                cue = _normalized_space(beat.get("physical_cue", ""))
                if cue:
                    beat["physical_cue"] = re.sub(
                        rf"(?<![A-Za-z0-9]){re.escape(old_name)}(?![A-Za-z0-9])",
                        new_name,
                        cue,
                        flags=re.IGNORECASE,
                    )
                repaired += 1
                break

        for field in (
            "_director_h3_source_prompt",
            "_director_opening_blocking",
            "_director_closing_blocking",
        ):
            value = plan.get(field)
            if not isinstance(value, str):
                continue
            for old_id, old_name, new_id, new_name in replacements:
                value = re.sub(
                    rf"(?<![A-Za-z0-9]){re.escape(old_id)}(?![A-Za-z0-9])",
                    new_id,
                    value,
                    flags=re.IGNORECASE,
                )
                value = re.sub(
                    rf"(?<![A-Za-z0-9]){re.escape(old_name)}(?![A-Za-z0-9])",
                    new_name,
                    value,
                    flags=re.IGNORECASE,
                )
            plan[field] = value
        # Force an immediate clean compilation rather than comparing against
        # a wrapper produced before the identity merge.
        plan.pop("_director_h3_compiled_prompt", None)
        plan.pop("_director_speaker_registry", None)
        plan["_director_h3_phantom_speaker_repaired"] = True

    if repaired:
        # The registry is copied onto every saved clip.  One phantom entry in
        # a single shot therefore contaminates otherwise unrelated clips too;
        # discard every cached copy and rebuild it from the cleaned subjects.
        for plan in clip_plans:
            if isinstance(plan, MutableMapping):
                plan.pop("_director_speaker_registry", None)
        print(
            "[MiniMax H3] Merged "
            f"{repaired} phantom dialogue turn(s) back into their explicitly "
            "attributed visible character."
        )


def _canonicalize_h3_project_subject_names(
    clip_plans: Sequence[Mapping[str, Any]],
) -> None:
    """Keep stable identities without conflating bounded-sequence cast slots.

    Long-form Director calls are intentionally planned in small independent
    batches.  A local model may use ``char_1`` for Monica in one batch, Pam in
    the next, and Leslie later.  Treating that local slot as a project-global
    identity caused the compiler to relabel every guest as one person and gave
    Ref2VA contradictory identity evidence that could materialize duplicate
    principals.  Rebind only demonstrably reused slots; meaningful IDs and
    explicitly separate same-name characters remain unchanged.
    """

    project_context = " ".join(
        _normalized_space(plan.get("_director_project_context", ""))
        for plan in clip_plans
        if isinstance(plan, Mapping)
    )
    occurrences: list[dict[str, Any]] = []
    identity_groups: list[dict[str, Any]] = []
    groups_by_original_id: dict[str, set[int]] = {}

    def matching_group(identity: str) -> int:
        matches = [
            index for index, group in enumerate(identity_groups)
            if any(
                _h3_identity_names_match(identity, candidate)
                for candidate in group["candidates"]
            )
        ]
        # A one-word shorthand can be ambiguous when two full names share it.
        # Do not merge through that ambiguity.
        if len(matches) == 1:
            return matches[0]
        identity_groups.append({"candidates": [], "occurrences": []})
        return len(identity_groups) - 1

    for plan_index, plan in enumerate(clip_plans):
        for subject_index, subject in enumerate(
            plan.get("_director_subjects_on_screen") or []
        ):
            character_id = _normalized_space(_field(subject, "character_id", ""))
            if not character_id or _h3_is_placeholder_identity(character_id):
                continue
            original_key = character_id.casefold()
            identity, candidates = _h3_subject_identity_evidence(subject)
            if not identity:
                identity = character_id
                candidates = (
                    [character_id]
                    if not _h3_is_placeholder_identity(character_id)
                    else []
                )
            group_index = matching_group(identity)
            group = identity_groups[group_index]
            for candidate in candidates:
                if candidate and not any(
                    _h3_anchor_key(candidate) == _h3_anchor_key(existing)
                    for existing in group["candidates"]
                ):
                    group["candidates"].append(candidate)
            occurrence = {
                "plan_index": plan_index,
                "subject_index": subject_index,
                "subject": subject,
                "original_id": character_id,
                "original_key": original_key,
                "group_index": group_index,
            }
            occurrences.append(occurrence)
            group["occurrences"].append(occurrence)
            groups_by_original_id.setdefault(original_key, set()).add(group_index)

    def score(name: str) -> tuple[int, int, int, int]:
        words = re.findall(r"[A-Za-z0-9'’-]+", name)
        context_key = _h3_anchor_key(project_context)
        first_token_present = bool(
            words
            and re.search(
                rf"(?:^|\s){re.escape(words[0].casefold())}(?:\s|$)",
                context_key,
            )
        )
        return (
            int(_h3_anchor_present(name, project_context) or first_token_present),
            len(words),
            int(not name.isupper()),
            len(name),
        )

    canonical_by_group = {
        index: max(group["candidates"], key=score)
        for index, group in enumerate(identity_groups)
        if group["candidates"]
    }
    unstable_ids = {
        key for key, groups in groups_by_original_id.items()
        if len(groups) > 1
    }
    used_ids = {
        occurrence["original_id"].casefold()
        for occurrence in occurrences
        if occurrence["original_key"] not in unstable_ids
    }
    generated_ids: dict[int, str] = {}
    for occurrence in occurrences:
        if occurrence["original_key"] not in unstable_ids:
            continue
        group_index = occurrence["group_index"]
        if group_index in generated_ids:
            continue
        canonical_name = canonical_by_group.get(group_index, "subject")
        base = f"director_identity_{_h3_identity_slug(canonical_name)}"
        candidate = base
        suffix = 2
        while candidate.casefold() in used_ids:
            candidate = f"{base}_{suffix}"
            suffix += 1
        generated_ids[group_index] = candidate
        used_ids.add(candidate.casefold())

    plan_rebindings: dict[int, dict[str, set[str]]] = {}
    repaired_occurrences = 0
    for occurrence in occurrences:
        subject = occurrence["subject"]
        group_index = occurrence["group_index"]
        canonical_name = canonical_by_group.get(group_index)
        if canonical_name:
            _set_h3_subject_field(subject, "speaker_name", canonical_name)
        if occurrence["original_key"] not in unstable_ids:
            continue
        target_id = generated_ids[group_index]
        _set_h3_subject_field(subject, "character_id", target_id)
        plan_rebindings.setdefault(
            occurrence["plan_index"], {}
        ).setdefault(occurrence["original_key"], set()).add(target_id)
        repaired_occurrences += 1

    for plan_index, plan in enumerate(clip_plans):
        mappings = plan_rebindings.get(plan_index, {})
        for beat in plan.get("_director_dialogue_beats") or []:
            if not isinstance(beat, MutableMapping):
                continue
            speaker_id = _normalized_space(beat.get("speaker_id", ""))
            targets = mappings.get(speaker_id.casefold(), set())
            if len(targets) == 1:
                beat["speaker_id"] = next(iter(targets))
            elif len(targets) > 1:
                raise H3DialogueContractError(
                    "A long-form shot reused one local character ID for "
                    "multiple visible people, so dialogue ownership is ambiguous."
                )
        if mappings and isinstance(plan, MutableMapping):
            # Any saved registry was keyed by the now-invalid local slots.
            # Rebuild it deterministically from the repaired project cast.
            plan.pop("_director_speaker_registry", None)
            plan["_director_h3_identity_rebound"] = True

    if repaired_occurrences:
        print(
            "[MiniMax H3] Rebound "
            f"{repaired_occurrences} long-form cast occurrence(s) from "
            f"{len(unstable_ids)} reused local character slot(s)."
        )


def _h3_source_requests_multiple_instances(source: Any, name: Any) -> bool:
    """Allow twins/copies only when the source explicitly requests them."""

    text = normalize_h3_text(source)
    identity = _normalized_space(name)
    if not text or not identity:
        return False
    escaped = re.escape(identity)
    quantity = (
        r"(?:two|three|four|five|six|seven|eight|nine|ten|multiple|"
        r"several|many|a\s+pair\s+of|a\s+group\s+of)"
    )
    return bool(re.search(
        rf"(?:{quantity})\s+(?:identical\s+)?(?:copies?\s+of\s+)?"
        rf"{escaped}(?:es|s)?\b|\b{escaped}(?:es|s)?\b[^.!?]{{0,45}}\b"
        r"(?:twins?|clones?|copies|duplicates?|multiple\s+versions?)\b",
        text,
        flags=re.IGNORECASE,
    ))


def _h3_plan_context_anchors(plan: Mapping[str, Any]) -> list[str]:
    anchors: list[str] = []
    for subject in plan.get("_director_subjects_on_screen") or []:
        name = (
            _h3_identity_label(_field(subject, "speaker_name", ""))
            or _h3_identity_label(_field(subject, "character_id", ""))
        )
        folded = name.casefold()
        if (
            name
            and folded not in _H3_GENERIC_IDENTITY_LABELS
            and not _h3_is_placeholder_identity(name)
            and not re.fullmatch(r"(?:char|subject|speaker)[_-]?\d+", folded)
        ):
            anchors.append(name)
    environment = _normalized_space(plan.get("_director_environment", ""))
    if environment:
        # Environment is already Director's concise, shot-specific world ledger.
        # Cap pathological legacy values while retaining named franchise/venue data.
        words = environment.split()
        anchors.append(" ".join(words[:32]).rstrip(" .;:-"))
    return _h3_context_anchors(anchors)


def _speaker_label_for_identity(
    subjects: Sequence[Any], folded_key: str,
) -> str:
    """The label a participant's own subject row gives them, e.g. ``(S1)``.

    A dialogue beat may name its speaker by character id rather than by label.
    That character's subject row states the label in its visual description, so
    the row is the authority for the identity-to-label mapping.

    This is also how the speaker registry tells two participants apart, so it must never answer
    from row position: numbering a participant by where their row happens to sit is what renamed
    a project's cast from one shot to the next. ``_subject_position_label`` covers the one case
    that has nothing else to go on.
    """

    for subject in subjects or []:
        character_id = _normalized_space(_field(subject, "character_id", ""))
        speaker_name = _normalized_space(_field(subject, "speaker_name", ""))
        if folded_key not in {character_id.casefold(), speaker_name.casefold()}:
            continue
        # Position is only a fallback for a row the planner left unlabelled, and
        # an unlabelled row cannot answer who this identity is.
        _slot, label = _planner_subject_slot(subject, 0)
        if label:
            return f"({label})"
    return ""


def _subject_position_label(subjects: Sequence[Any], folded_key: str) -> str:
    """The label a participant gets from their place in the subjects list, e.g. ``(S1)``.

    Only for a plain subjects list handed to the compiler with no project SUBJECT LOCK and no
    speaker registry: there, ``<Subject N>`` being the Nth subject is the H3 contract and the
    list is the only statement of identity available. The registry builder never calls this.
    """

    for index, subject in enumerate(subjects or [], start=1):
        keys = {
            _normalized_space(_field(subject, "character_id", "")).casefold(),
            _normalized_space(_field(subject, "speaker_name", "")).casefold(),
        }
        if folded_key in keys:
            return f"(S{index})"
    return ""


def _label_encoded_in_key(key: str) -> str:
    """The ``(S2)`` a speaker key already spells out, or "" when it does not.

    A beat often names its speaker with the label the participant already has
    (``"(s2)"``) or with an identity id built from it
    (``"director_identity_s2"``). Those keys are not new people, but resolving
    them through the subject rows failed because those rows carry no label in
    their prose, so each key minted a fresh number: a two-person project
    compiled S1..S4, and 32 dialogue beats were spoken by an ``(S4)`` no subject
    row described.
    """

    text = _normalized_space(key)
    if not text:
        return ""
    match = re.fullmatch(r"\(\s*s(\d+)\s*\)", text, re.IGNORECASE)
    if match:
        return f"(S{int(match.group(1))})"
    match = re.fullmatch(r"[A-Za-z0-9_\- ]*?[_\- ]s(\d+)", text, re.IGNORECASE)
    if match:
        return f"(S{int(match.group(1))})"
    return ""


def _build_stable_speaker_registry(
    clip_plans: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, str]]:
    """Assign one label per participant, not one per key.

    A saved registry can carry a number per *key* instead of per person: a
    two-person project stored ``director_identity_s1 -> (S1)``, ``(s1) -> (S2)``,
    ``(s2) -> (S3)`` and ``director_identity_s2 -> (S4)``. Every line then reached
    the model with the next participant's face: a beat whose plan said ``(S1)``
    was rendered as ``(S2)``, and a shot whose third line bound to ``(s2)``
    invented an ``(S3)`` nobody plays. Measured on one project: 135 of its 177
    clips had a line bound to a speaker the cast does not contain.

    Keys are therefore grouped into the participant they belong to -- through the
    label a key spells out, or through the subject rows -- and every key of that
    participant shares its label. A registry compiled before this rule keeps its
    participants: only the aliases of one person are collapsed.
    """

    existing: dict[str, dict[str, str]] = {}
    all_subjects: list[Any] = []
    keys: list[str] = []

    def remember(raw_key: Any) -> None:
        key = _normalized_space(raw_key).casefold()
        if _h3_is_placeholder_identity(raw_key):
            # A placeholder speaker token cannot become a project-wide identity alias.
            return
        if key and key not in keys:
            keys.append(key)

    # First-speaking order decides the numbers: the participant who speaks first
    # in the project is (S1), which is how the UI and the audio analysis number
    # them too. Reading the cast rows first would renumber a project whose rows
    # simply arrive in a different order in a later shot.
    for plan in clip_plans:
        for beat in plan.get("_director_dialogue_beats") or []:
            remember(_field(beat, "speaker_id", ""))
    for plan in clip_plans:
        for subject in plan.get("_director_subjects_on_screen") or []:
            all_subjects.append(subject)
            remember(_field(subject, "character_id", ""))
            remember(_field(subject, "speaker_name", ""))
        saved = plan.get("_director_speaker_registry") or {}
        if isinstance(saved, Mapping):
            for raw_key in saved:
                key = _normalized_space(raw_key).casefold()
                entry = _speaker_registry_entry(saved, str(raw_key))
                if key and entry and entry[0]:
                    existing[key] = {"stable_id": entry[0], "speaker_name": entry[1]}
                remember(raw_key)

    def identity_of(key: str) -> str:
        """The participant a key belongs to, as a label when one exists."""

        encoded = _label_encoded_in_key(key)
        if encoded:
            return encoded.upper()
        known = _speaker_label_for_identity(all_subjects, key)
        if known:
            return known.upper()
        return key

    groups: dict[str, list[str]] = {}
    for key in keys:
        groups.setdefault(identity_of(key), []).append(key)

    registry: dict[str, dict[str, str]] = {}
    used_numbers: set[int] = set()
    for identity, group in groups.items():
        encoded = _label_encoded_in_key(identity)
        stable_id = encoded.upper() if encoded else ""
        if not stable_id:
            # An opaque key: keep the number a saved registry already gave it,
            # so a project's existing cast is not renumbered.
            for key in group:
                candidate = str(existing.get(key, {}).get("stable_id") or "")
                if re.fullmatch(r"\(S\d+\)", candidate, re.IGNORECASE):
                    stable_id = candidate.upper()
                    break
        if stable_id:
            match = re.fullmatch(r"\(S(\d+)\)", stable_id, re.IGNORECASE)
            if match:
                used_numbers.add(int(match.group(1)))
        for key in group:
            registry[key] = {
                "stable_id": stable_id,
                "speaker_name": (
                    _normalized_space(_subject_name_for_key(all_subjects, key))
                    if _subject_name_for_key(all_subjects, key) != key
                    else ""
                )
                or str(existing.get(key, {}).get("speaker_name") or ""),
            }

    next_number = 1
    for key in keys:
        entry = registry[key]
        if entry["stable_id"]:
            continue
        while next_number in used_numbers:
            next_number += 1
        entry["stable_id"] = f"(S{next_number})"
        used_numbers.add(next_number)
        next_number += 1
        # Every alias of this participant now shares one label.
        for alias in groups[identity_of(key)]:
            registry[alias] = dict(entry)

    for plan in clip_plans:
        for subject in plan.get("_director_subjects_on_screen") or []:
            character_id = _normalized_space(_field(subject, "character_id", ""))
            speaker_name = _h3_identity_label(_field(subject, "speaker_name", ""))
            entry = registry.get(character_id.casefold())
            if entry is not None and speaker_name:
                entry["speaker_name"] = speaker_name
    return registry


_H3_VISUAL_LABELS = (
    "Visible cast",
    "Action",
    "Camera",
    "Lighting",
    "Mood",
    "Final beat",
    "SPEAKER VISIBILITY",
    "By the final beat",
)

_H3_VISUAL_LABEL_RE = re.compile(
    r"(?:^|(?<=[.!?;\n]))\s*(?P<label>"
    + "|".join(re.escape(item) for item in _H3_VISUAL_LABELS)
    + r")\s*:\s*",
    re.IGNORECASE,
)


def _h3_labeled_value(body: str, label: str) -> str:
    # Native Director fields begin sentences. A phrase such as "cinematic
    # action: ..." inside the world description is ordinary prose, not a
    # field delimiter. Use the same boundary for extraction and insertion.
    fields = list(_H3_VISUAL_LABEL_RE.finditer(body))
    for index, field in enumerate(fields):
        if field.group("label").casefold() == label.casefold():
            end = fields[index + 1].start() if index + 1 < len(fields) else len(body)
            return _normalized_space(body[field.end():end])
    return ""


def _compact_h3_visual_body(
    body: str,
    subjects: Sequence[Any],
    registry: Mapping[str, Any],
    *,
    closing_blocking: str = "",
) -> str:
    """Remove planner repetition while retaining H3's highest-value visuals.

    Director's planning JSON intentionally contains redundant continuity data.
    Sending every copy can dilute late dialogue and final-action instructions
    even though H3 now receives the complete sequence. Rebuild the visual body
    from its structured labels so identity, wardrobe, action, camera, and the
    final state each appear once.
    """

    text = _normalized_space(body)
    spans, _ = _dialogue_spans(text)
    if spans:
        text = _normalized_space(_replace_spans(text, spans, [""] * len(spans)))

    label_positions = [match.start() for match in _H3_VISUAL_LABEL_RE.finditer(text)]
    if not label_positions:
        return text

    # A cosmetic token target cannot justify severing an action chain, a
    # camera's destination or the final state. Keep unique field content;
    # the shared Studio fitter removes redundant boilerplate afterward.
    intro = _normalized_space(text[:min(label_positions)])
    cast: list[str] = []
    for subject in subjects or []:
        character_id = _normalized_space(_field(subject, "character_id", ""))
        speaker_name = (
            _h3_identity_label(_field(subject, "speaker_name", ""))
            or _h3_identity_label(character_id)
        )
        entry = _speaker_registry_entry(registry, character_id)
        stable_id = entry[0] if entry else ""
        visual = _normalized_space(_field(subject, "visual_description", ""))
        wardrobe = _normalized_space(_field(subject, "wardrobe", ""))
        position = _normalized_space(_field(subject, "position_or_relation", ""))
        bits = [f"{speaker_name} {stable_id}".strip(), visual]
        if wardrobe:
            bits.append(f"wearing {wardrobe}")
        if position:
            bits.append(position)
        compact_subject = ", ".join(bit for bit in bits if bit)
        if compact_subject:
            cast.append(compact_subject)

    action = _h3_labeled_value(text, "Action")
    camera = _h3_labeled_value(text, "Camera")
    lighting = _h3_labeled_value(text, "Lighting")
    mood = _h3_labeled_value(text, "Mood")
    final = _h3_labeled_value(text, "Final beat")
    if not final:
        final = closing_blocking

    parts = [intro]
    if cast:
        parts.append(f"Cast: {'; '.join(cast)}.")
    if camera:
        parts.append(f"Camera: {camera}.")
    if action:
        parts.append(f"Action: {action}.")
    light_and_mood = "; ".join(item for item in (lighting, mood) if item)
    if light_and_mood:
        parts.append(f"Lighting and mood: {light_and_mood}.")
    if final:
        parts.append(f"Final frame: {final}.")
    result = _normalized_space(" ".join(part for part in parts if part))
    for subject in subjects or []:
        key = _normalized_space(_field(subject, "character_id", ""))
        name = _h3_identity_label(_field(subject, "speaker_name", ""))
        if name and name != key and re.fullmatch(r"(?i)char(?:acter)?[_ -]?\d+", key):
            entry = _speaker_registry_entry(registry, key)
            label = f"{name} {entry[0]}" if entry else name
            result = re.sub(rf"\b{re.escape(key)}\b", lambda _: label, result, flags=re.I)
            result = re.sub(re.escape(label) + rf"\s*\({re.escape(name)}\)", lambda _: label, result, flags=re.I)
    return result


def _compact_h3_line_delivery(value: Any) -> str:
    parts = [
        part.strip(" .")
        for part in str(value or "").split(";")
        if part.strip(" .")
    ]
    if not parts:
        return ""
    # Director joins a project-wide voice bible to a concise line-specific
    # direction with semicolons.  Keep the line direction, not the repeated
    # multi-sentence bible on every turn.
    selected = parts[-2] if len(parts) >= 3 else parts[-1]
    return _normalized_space(selected)


def _h3_dialogue_timing_clause(
    beats: Sequence[Mapping[str, str]],
    duration_seconds: float,
) -> str:
    word_count = sum(len(beat.get("words", "").split()) for beat in beats)
    duration, start, end = h3_dialogue_schedule(word_count, duration_seconds)
    return (
        f"Dialogue timing: mouths stay closed with no human voice from 0.00 "
        f"to {start:.2f} seconds; the tagged lines run once in order from "
        f"about {start:.2f} to {end:.2f} seconds; after {end:.2f} seconds, "
        f"everyone remains silent through {duration:.2f} seconds."
    )


def _insert_h3_vocal_detail(body: str, detail: str) -> str:
    """Keep dialogue ahead of camera/action detail in the token stream."""

    boundary = next((
        field for field in _H3_VISUAL_LABEL_RE.finditer(body)
        if field.group("label").casefold() in {"camera", "action"}
    ), None)
    if boundary:
        return _normalized_space(
            f"{body[:boundary.start()]} {detail} {body[boundary.start():]}"
        )
    return _normalized_space(f"{body} {detail}")


def _ensure_speaker_before_tag(
    body: str,
    tag: str,
    *,
    speaker_name: str,
    stable_id: str,
    cursor: int,
) -> tuple[str, int]:
    tag_index = body.find(tag, cursor)
    if tag_index < 0:
        return body, cursor
    nearby_start = max(0, tag_index - 260)
    nearby = body[nearby_start:tag_index]
    if stable_id in nearby:
        return body, tag_index + len(tag)

    name_matches = list(re.finditer(
        re.escape(speaker_name), nearby, flags=re.IGNORECASE,
    )) if speaker_name else []
    if name_matches:
        insertion = nearby_start + name_matches[-1].end()
        body = f"{body[:insertion]} {stable_id}{body[insertion:]}"
        tag_index += len(stable_id) + 1
    else:
        prefix = f"{speaker_name or 'The visible speaker'} {stable_id} says: "
        body = f"{body[:tag_index]}{prefix}{body[tag_index:]}"
        tag_index += len(prefix)
    return body, tag_index + len(tag)


def _compile_official_dialogue(
    body: str,
    subjects: Sequence[Any],
    dialogue_beats: Sequence[Any],
    registry: Mapping[str, Any],
    existing_blocks: Sequence[str],
    *,
    has_driving_audio: bool = False,
    duration_seconds: float = 0.0,
    music_driven: bool = False,
    vocal_activity: str | None = None,
    project_context: str = "",
    default_language: str | None = None,
    generated_speech_allowed: bool | None = None,
    dialogue_manifest_authoritative: bool = False,
) -> tuple[str, str]:
    """Place exact tagged lines and stable speaker IDs in the visual field."""

    # The project's SUBJECT LOCK, when it declares one, is what decides which participant
    # owns which label: the rows arrive in frame order, so without it a shot built on the
    # wrong person looks exactly like a correct one.
    lock = project_subject_lock(project_context)
    valid_beats: list[dict[str, str]] = []
    if not default_language:
        default_language = _detect_dialogue_language(
            " ".join(
                str(_field(beat, "spoken_text", ""))
                for beat in (dialogue_beats or [])
            ) or body,
        )
    authoritative_empty = (
        dialogue_manifest_authoritative and not _has_spoken_beats(dialogue_beats)
    )
    speech_forbidden = generated_speech_allowed is False or authoritative_empty
    # which plans carry beats here is decided in audio_driven_transcript
    for beat in ([] if has_driving_audio or speech_forbidden else (dialogue_beats or [])):
        spoken = normalize_h3_text(_field(beat, "spoken_text", ""))
        # A no-speech marker is a valid planning outcome, not a broken line.
        if is_silent_dialogue(spoken):
            continue
        beat_payload = dict(beat) if isinstance(beat, Mapping) else {
            "spoken_text": spoken,
            "language": _field(beat, "language", ""),
        }
        beat_payload["spoken_text"] = spoken
        # The project's language is passed as the default: the planner hard-codes
        # [English] even on Spanish projects, so its own tag is not trustworthy
        # once the project language is known.
        language, words = _dialogue_payload(beat_payload, default_language)
        speaker_key = _normalized_space(_field(beat, "speaker_id", ""))
        entry = _speaker_registry_entry(registry, speaker_key)
        if entry:
            stable_id, speaker_name = entry
        else:
            speaker_name = _subject_name_for_key(subjects, speaker_key)
            # Never mint a number from the line's position: that is how the
            # third line of a two-person shot became "(S3)" and how a podcast
            # reached "(S9)". A beat's key usually spells out the participant
            # it belongs to ("(s2)", "director_identity_s2"), and the subject
            # rows name the rest, so resolve through those instead.
            stable_id = _label_encoded_in_key(speaker_key)
            if not stable_id:
                stable_id = _speaker_label_for_identity(subjects, speaker_key)
            if not stable_id and not registry and not lock:
                # A plain subjects list with no lock and no registry: its order is the only
                # thing left that says which participant is which.
                stable_id = _subject_position_label(subjects, speaker_key)
            if not stable_id:
                # Say it where the log is read. A line with no resolvable
                # speaker is a planning defect, not something to guess at.
                print(
                    "[MiniMax H3] A spoken line has no resolvable speaker "
                    f"(speaker key {speaker_key!r}); it is left unnamed instead "
                    "of being numbered by its position in the shot."
                )
        valid_beats.append({
            "words": normalize_h3_text(words),
            "tag": f"<d>[{language}] {normalize_h3_text(words)}</d>",
            "stable_id": stable_id,
            "speaker_name": _clean_h3_metadata(speaker_name),
            "delivery": _clean_h3_metadata(_field(beat, "delivery", "")),
            "physical_cue": _clean_h3_metadata(_field(beat, "physical_cue", "")),
        })

    body = _strip_h3_custom_sections(body)
    # A planner sometimes writes "[S2] is still explaining", which H3 does not
    # recognize as a speaker mark; render every label with parentheses, and
    # drop corrupted <Subject ...> tags while keeping Maestro's own labels.
    body = _clean_malformed_subject_tags(_normalize_speaker_brackets(body))
    # A line exists in H3 only inside a tagged block. The renderers also write
    # each line as quoted prose with no stable speaker ("a person says ..."),
    # and that copy competes with the tagged one for the assignment.
    body = _strip_planner_quoted_dialogue(body)
    if has_driving_audio and not valid_beats:
        # The mapped audio is authoritative. LLM-authored vocal tags are both
        # unnecessary and actively harmful here: they can compete with the
        # soundtrack or become unbalanced when a repetitive transcript causes
        # output truncation. Structured dialogue remains authoritative for
        # narrative shots and follows the stricter validation path below.
        body = _strip_dialogue_for_driving_audio(body)
        existing_blocks = []
    elif speech_forbidden:
        # Planner-written spans are untrusted when a reviewed audio policy or
        # authoritative empty manifest says that this shot has no generated
        # speech. Remove the tag and an immediately attached ``Kitten says:``
        # lead-in, while leaving visual actions around it intact.
        body = _strip_forbidden_dialogue_blocks(body)
        existing_blocks = []
    spans, malformed = _dialogue_spans(body)
    if valid_beats and spans:
        # A complete top-level block is safely identifiable as dialogue even
        # if it contains duplicated inner tags. Drop that whole block first;
        # recovery below is reserved for an unmatched outer duplicate whose
        # trailing depth-one prose may contain visual action/continuity.
        body = _replace_spans(body, spans, [""] * len(spans))
        spans, malformed = _dialogue_spans(body)
    if malformed:
        # The visual prompt is best-effort prose; a duplicated nested opener cannot override
        # the exact structured dialogue and speaker map. Its inner tagged payload is discarded
        # as untrusted speech, while prose after the inner close remains action/continuity.
        # With no structured lines there is nothing that outranks the broken markup, so the
        # prompt is refused rather than guessed at.
        if not valid_beats or not _has_nested_dialogue_open(body):
            raise H3DialogueContractError(
                "MiniMax H3 dialogue tags are unbalanced and cannot be repaired safely."
            )
        body = _strip_malformed_dialogue_markup_for_structured_beats(
            body,
            [beat["words"] for beat in valid_beats],
        )
        spans, malformed = _dialogue_spans(body)
    if malformed:
        raise H3DialogueContractError(
            "MiniMax H3 dialogue tags are unbalanced and cannot be repaired safely."
        )

    if valid_beats:
        # Structured dialogue is authoritative. Remove any planner-authored
        # copies and insert one concise, prominent sequence so visual prose
        # cannot bury the exact lines and their timing cues.
        body = _replace_spans(body, spans, [""] * len(spans))
        dialogue_parts: list[str] = []
        for beat in valid_beats:
            sentence = h3_speaker_cues.spoken_sentence(beat)
            if beat["physical_cue"]:
                cue = _normalized_space(beat["physical_cue"])
                if cue:
                    sentence += f" While speaking, {cue}."
            dialogue_parts.append(sentence)

        timing = _h3_dialogue_timing_clause(valid_beats, duration_seconds)
        guard = (
            "Only the tagged lines are spoken, once each in order. After the "
            "final tagged line, every character remains silent with their "
            "mouth closed; no invented dialogue, muttering, gibberish, "
            "speech-like vocalization, or background voice occurs."
        )
        on_screen_labels = {
            f"(S{int(match.group(1))})"
            for subject in subjects or []
            for key in ("visual_description", "character_id", "speaker_name")
            for match in _H3_SPEAKER_TOKEN_RE.finditer(
                str(_field(subject, key, "") or "")
            )
        }
        off_camera = {
            beat["stable_id"] for beat in valid_beats
            if beat["stable_id"]
        } - on_screen_labels
        frame_clause = (
            "Keep every on-screen speaker visible with an unobstructed face and "
            "mouth through their complete line; a speaker who is not in this "
            "frame stays off-camera, and the visible listener does not move "
            "their lips."
            if off_camera else
            "Keep each current speaker visibly framed with an unobstructed "
            "face and mouth through their complete line."
        )
        vocal_detail = (
            f"{timing} Dialogue: {' '.join(dialogue_parts)} {guard} "
            f"{frame_clause}"
        )
        body = _insert_h3_vocal_detail(body, vocal_detail)
        contract = " ".join(
            f"{beat['speaker_name']} {beat['stable_id']}: {beat['tag']}"
            for beat in valid_beats
        )
    else:
        canonical_blocks = [
            tag
            for tag in (
                _safe_dialogue_tag(block, default_language)
                for block in existing_blocks
            )
            if tag
        ]
        if spans:
            # Keep one entry per span so the replacement stays aligned; a
            # no-speech marker becomes an empty entry and is therefore removed
            # from the prompt instead of aborting the compile.
            canonical_blocks = [
                _safe_dialogue_tag(body[start:end], default_language) or ""
                for start, end in spans
            ]
            body = _replace_spans(body, spans, canonical_blocks)
        elif canonical_blocks:
            additions = " ".join(
                f"A visible speaker (S{index}) says: {tag}."
                for index, tag in enumerate(canonical_blocks, start=1)
            )
            body = f"{body} {additions}".strip()
        if canonical_blocks:
            guard = (
                "Only the tagged lines are spoken; everyone remains silent "
                "with their mouth closed at all other times. Generate no "
                "muttering, gibberish, or additional speech-like vocalization."
            )
            if "only the tagged lines are spoken" not in body.casefold():
                body = f"{body} {guard}".strip()
            contract = " ".join(canonical_blocks)
        elif has_driving_audio:
            driver_contract = (
                "Any audible voice or vocal comes only from the mapped driving "
                "audio and remains synchronized to it; do not generate "
                "additional dialogue, gibberish, or speech-like vocalization."
            )
            if "mapped driving audio" not in body.casefold():
                body = _insert_h3_vocal_detail(body, driver_contract)
            if music_driven:
                from .music_performance import music_performance_direction
                direction = music_performance_direction(subjects, vocal_activity, project_context=project_context)
                if direction and direction not in body:
                    body = _insert_h3_vocal_detail(body, direction)
                if direction:
                    driver_contract = f"{driver_contract} {direction}"
            contract = driver_contract
        else:
            silence = (
                "No character speaks; all visible mouths remain closed, and "
                "no muttering, gibberish, speech-like vocalization, or "
                "background voice occurs."
            )
            if not re.search(r"\bno (?:one|character) speaks\b", body, re.IGNORECASE):
                body = _insert_h3_vocal_detail(body, silence)
            contract = silence

    # What the rest of the face does, which H3 has no separate emotion channel for.
    if _expression.needs_expression(body, driving=has_driving_audio, subjects=subjects):
        body = _insert_h3_vocal_detail(body, _expression.PERFORMANCE_EXPRESSION_DIRECTION)

    body = _normalized_space(body)
    return body, contract


def _strip_malformed_dialogue_markup_for_structured_beats(
    prompt: str,
    authoritative_lines: Sequence[str],
) -> str:
    """Keep visual prose while discarding malformed dialogue delimiters.

    This recovery is safe only when structured dialogue supplies the exact
    replacement lines and speaker order. Content at a duplicated inner tag
    depth is discarded as untrusted generated speech; text after its close is
    retained as visual prose. Exact copies of authoritative lines in retained
    tagged prose are removed before the canonical lines are reinserted once.
    """

    text = str(prompt or "")
    parts: list[tuple[str, bool, int]] = []
    cursor = 0
    depth = 0
    for token in _H3_DIALOGUE_TOKEN_RE.finditer(text):
        content = text[cursor:token.start()]
        if content:
            marked = depth > 0
            level = depth
            if parts and parts[-1][1:] == (marked, level):
                prior, _, _ = parts[-1]
                parts[-1] = (prior + content, marked, level)
            else:
                parts.append((content, marked, level))
        if not token.group(1):
            depth += 1
        elif depth:
            depth -= 1
        cursor = token.end()
    tail = text[cursor:]
    if tail:
        marked = depth > 0
        level = depth
        if parts and parts[-1][1:] == (marked, level):
            prior, _, _ = parts[-1]
            parts[-1] = (prior + tail, marked, level)
        else:
            parts.append((tail, marked, level))

    cleaned: list[str] = []
    for content, marked, level in parts:
        if marked and level > 1:
            continue
        if not marked:
            cleaned.append(content)
            continue

        had_authoritative_line = False
        for line in authoritative_lines:
            words = _normalized_space(line)
            if not words:
                continue
            phrase = r"\s+".join(re.escape(word) for word in words.split())
            content, count = re.subn(
                rf"(?<!\w){phrase}(?!\w)",
                "",
                content,
            )
            had_authoritative_line = had_authoritative_line or count > 0
        if had_authoritative_line:
            content = re.sub(
                r"^\s*\[[^\]\r\n]+\]\s*",
                "",
                content,
                count=1,
            )
        cleaned.append(content)
    return "".join(cleaned)


def _has_nested_dialogue_open(prompt: str) -> bool:
    """Return whether any dialogue opener occurs before the prior one closes."""

    depth = 0
    for token in _H3_DIALOGUE_TOKEN_RE.finditer(str(prompt or "")):
        if not token.group(1):
            if depth:
                return True
            depth = 1
        elif depth:
            depth -= 1
    return False


def _ref2va_inferred_visible_identity(subject: Any) -> str:
    """Infer a short visible label only when a cast member has no usable name.

    Semantic planner IDs such as ``lead_performer`` are stable keys, not names
    that appear in visual prose. A capitalized identity at the start of the
    subject's description can supply the visible label, but generic role words
    such as ``performer`` are not identities.
    """

    if _h3_identity_label(_field(subject, "speaker_name", "")):
        return ""
    description = _normalized_space(_field(subject, "visual_description", ""))
    opening = re.split(
        r"[,;:(]|\s+(?:from|with|wearing|whose|in|is|stands?|sits?|holds?|"
        r"approach(?:es|ing)?|standing|sitting|holding|posing|looking|walking|"
        r"running|clad)\b",
        description,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" .,:;—–-")
    opening = re.sub(
        r"^(?:(?:the|a|an|same|single)\s+)+", "", opening, flags=re.IGNORECASE,
    )
    if not opening or not re.fullmatch(
        r"[A-Z][A-Za-z0-9'’–-]*(?:\s+[A-Z][A-Za-z0-9'’–-]*){0,2}",
        opening,
    ):
        return ""
    label_key = _h3_anchor_key(opening)
    generic_labels = _H3_GENERIC_IDENTITY_LABELS | {
        "actor", "actress", "dancer", "musician", "performer", "singer",
    }
    if (
        label_key in {_h3_anchor_key(label) for label in generic_labels}
        or _H3_LOCAL_CAST_SLOT_RE.fullmatch(opening)
        or _h3_is_placeholder_identity(opening)
    ):
        return ""
    return opening


def _ref2va_compound_identity_alias(value: str) -> str:
    """Read a semantic identity before neutral planner-ID suffixes."""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:[_-][A-Za-z0-9]+)+", value):
        return ""
    parts = re.split(r"[_-]+", value)
    original_count = len(parts)
    while parts and (
        parts[-1].isdigit()
        or parts[-1].casefold() in {"actor", "character", "creature", "entity", "subject"}
    ):
        parts.pop()
    if not parts or len(parts) == original_count:
        return ""
    alias = " ".join(parts)
    return "" if alias.casefold() in {
        "actor", "char", "character", "creature", "entity", "identity", "native", "speaker", "subject",
    } else alias


def _ref2va_identity_aliases(subject: Any) -> list[str]:
    """Read explicit names or a conservative leading identity noun phrase.

    Older music plans have only visual_description ("the Kitten, ...").
    Match word boundaries and require a unique subject; shared traits never
    justify picking the first member of an ensemble.
    """
    aliases = [
        _h3_identity_label(_field(subject, key, ""))
        for key in ("speaker_name", "character_id")
    ]
    inferred = _ref2va_inferred_visible_identity(subject)
    if inferred:
        aliases.append(inferred)
    for name in list(aliases):
        compound_alias = _ref2va_compound_identity_alias(name)
        if compound_alias:
            aliases.append(compound_alias)
        # Qualified names and semantic IDs can share an explicit identity noun
        # with their reference role ("Northern Kingdom Dragon" / "Dragon").
        # Retain the full name; a shared short alias must still resolve uniquely.
        readable_name = re.sub(r"[_-]+", " ", compound_alias or name).strip()
        name_parts = readable_name.split()
        if 1 < len(name_parts) <= 6:
            aliases.append(readable_name)
            noun = name_parts[-1].strip("'’-")
            if re.fullmatch(r"[A-Za-z][A-Za-z'’-]+", noun) and noun.casefold() not in {
                "person", "man", "woman", "child", "boy", "girl", "performer",
                "singer", "subject", "character", "figure", "people", "actor",
                "creature", "entity",
            }:
                aliases.append(noun)
        # Planner names and reference roles commonly differ only by a leading
        # article ("the Kitten" vs "Kitten"). Keep the original alias and add
        # the article-free form so a native Subject ID remains authoritative.
        name_without_article = re.sub(r"^(?:the|a|an)\s+", "", name, flags=re.I)
        if name_without_article and name_without_article.casefold() != name.casefold():
            aliases.append(name_without_article)
        name_for_parts = name_without_article or name
        if re.fullmatch(
            r"[A-Z][A-Za-z'’-]+(?: [A-Z][A-Za-z'’-]+){1,2}",
            name_for_parts,
        ):
            aliases.extend(
                part for part in name_for_parts.split()
                if part.casefold() not in {"the", "a", "an"}
            )
    description = _normalized_space(_field(subject, "visual_description", ""))
    opening = re.split(
        r"[(:;]|\s+(?:from|with|wearing|whose|in|is|stands?|sits?|holds?|"
        r"approach(?:es|ing)?|standing|sitting|holding|posing|looking|walking|running|clad)\b", description, maxsplit=1, flags=re.I,
    )[0].strip(" ,.;")
    opening = re.sub(r"^(?:(?:the|a|an|same|single)\s+)+", "", opening, flags=re.I)
    if (
        opening
        and len(opening.split()) <= 6
        and not _h3_is_placeholder_identity(opening)
    ):
        aliases.append(opening)
        # A descriptor such as "single warrior kitten" may be called simply
        # "kitten" in the reference label. Generic human nouns are excluded.
        noun = opening.split()[-1].strip("'’-")
        if noun.casefold() not in {
            "person", "man", "woman", "child", "boy", "girl", "performer",
            "singer", "subject", "character", "figure", "people",
        }:
            aliases.append(noun)
    return list(dict.fromkeys(
        value for value in aliases
        if len(value) >= 2
        and _h3_anchor_key(value) not in _H3_NONIDENTITY_REFERENCE_ALIASES
        and not _h3_is_placeholder_identity(value)
    ))


def _mapped_ref2va_subject(role: str, subjects: Sequence[Any]) -> int | None:
    # An explicit (Sx) is the strongest identity: the reference role and the
    # subject row both state it, and a plan can describe the same person as
    # "Roman (S2)" and "the man from the reference image (S2)" with no shared
    # name. Without this, the reference created a phantom extra Subject and a
    # single-subject reaction shot was paired with the off-camera speaker.
    role_labels = {
        f"S{int(match.group(1))}"
        for match in _H3_SPEAKER_TOKEN_RE.finditer(str(role or ""))
    }
    scored: list[tuple[tuple[int, int], int]] = []
    for index, subject in enumerate(subjects):
        explicit = _ref2va_identity_aliases({
            key: _field(subject, key, "") for key in ("speaker_name", "character_id")
        })
        def match_length(aliases: Sequence[str]) -> int:
            return max((len(alias) for alias in aliases if re.search(
                rf"(?<![\w]){re.escape(alias)}(?![\w])", role, re.I,
            )), default=0)
        explicit_length = match_length(explicit)
        inferred_length = match_length(_ref2va_identity_aliases(subject))
        subject_labels = {
            f"S{int(match.group(1))}"
            for match in _H3_SPEAKER_TOKEN_RE.finditer(
                " ".join(
                    str(_field(subject, key, "") or "")
                    for key in ("visual_description", "character_id", "speaker_name")
                )
            )
        }
        shared_labels = role_labels & subject_labels
        if shared_labels:
            scored.append(((3, max(len(label) for label in shared_labels)), index))
        else:
            scored.append(((2, explicit_length) if explicit_length else (1, inferred_length), index))
    best = max((score for score, _index in scored), default=(0, 0))
    matches = [index for score, index in scored if best[1] and score == best]
    if len(matches) > 1:
        raise H3DialogueContractError(
            f"Reference identity '{role}' matches multiple visible subjects; "
            "use a distinct character name in the reference and cast list."
        )
    return matches[0] if matches else None


def _ref2va_cast_from_source(
    prompt: str, subjects: Sequence[Any],
) -> tuple[list[Any], dict[int, str]]:
    """Recover reviewed cast definitions when structured cast metadata is absent.

    Explicit Context-IR edits are authoritative. Retaining their numbered
    definitions also retains non-reference actors (e.g. a mounted creature).
    Existing structured cast stays authoritative for legacy compiled copies.
    """
    field = _extract_h3_fields(prompt).get("subject_definitions", "")
    matches = list(re.finditer(r"<Subject\s+(\d+)>\s*(?:\(S\d+\)\s*)?is\s+", field, re.I))
    if not matches:
        return list(subjects), {}
    numbers = [int(match[1]) for match in matches]
    if len(numbers) != len(set(numbers)):
        # Duplicated numbers are corruption. A gap is not: a prompt can name only the
        # participant it shows ("<Subject 2> (S2)" with no Subject 1 in this shot), and
        # the validator judges what the definitions actually declare.
        raise H3DialogueContractError("Reviewed subject definitions must have unique consecutive subject numbers.")
    recovered: list[Any] = []
    definitions: dict[int, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(field)
        # Other reference definitions follow the final cast member in a
        # compiled six-field prompt. Rebuild those from the current manifest,
        # rather than swallowing them into that last character definition.
        reference_definition = re.search(
            r"(?:^|(?<=[.!?])\s+|\n)\s*<(?:Picture|Video|Audio)\s+\d+>"
            r"\s+(?:is|provides|supplies)\b", field[match.end():end], re.I,
        )
        if reference_definition:
            end = match.end() + reference_definition.start()
        definition = field[match.start():end].strip()
        description = field[match.end():end].strip()
        subject = {"visual_description": description}
        aliases = _ref2va_identity_aliases(subject)
        # Keep descriptions out of display names; full reviewed prose below
        # remains the authority for identity, wardrobe, and reference binding.
        if aliases:
            subject["speaker_name"] = _leading_h3_identity(description) or aliases[-1]
        recovered.append(subject)
        definitions[index + 1] = definition
    if subjects:
        if len(subjects) > len(recovered):
            raise H3DialogueContractError("Reviewed subject definitions omit a visible cast member.")
        for index, subject in enumerate(subjects):
            aliases = _ref2va_identity_aliases(subject)
            if aliases and not any(re.search(
                rf"(?<![\w]){re.escape(alias)}(?![\w])",
                str(recovered[index]["visual_description"]), re.I,
            ) for alias in aliases):
                raise H3DialogueContractError(
                    f"Reviewed <Subject {index + 1}> contradicts the saved cast; "
                    "update the cast identity together with the prompt."
                )
            recovered[index] = subject
    return recovered, definitions


def _collapse_ref2va_compiler_identity_duplicates(
    prompt: str, references: Sequence[Mapping[str, Any]] | None,
) -> str:
    """Repair only redundant identity definitions in the compiler's template.

    Older native prompts can contain both a semantic cast ID and an appended
    reference-only definition for that same identity. Rich authored cast
    definitions and ambiguous identities remain untouched.
    """
    field = _extract_h3_fields(prompt).get("subject_definitions", "")
    if not field:
        return prompt
    _cast, definitions = _ref2va_cast_from_source(prompt, [])
    template_pattern = re.compile(
        r"<Subject\s+\d+>\s+is\s+(.+?),\s+whose\s+facial,\s+bodily,\s+"
        r"and\s+character\s+identity\s+come\s+from\s+<Picture\s+(\d+)>\.", re.I,
    )
    identity_references: list[tuple[int, str]] = []
    if references:
        picture_no = 0
        for reference in references:
            if str(reference.get("type") or "").casefold() != "image":
                continue
            picture_no += 1
            if str(reference.get("image_intent") or "identity").casefold() == "identity":
                identity_references.append((picture_no, _trim_sentence(reference.get("role", ""))))
    else:
        # The first rerun preflight precedes concrete asset resolution. Exact
        # compiler-owned definitions already prove their picture/name mapping.
        identity_references = [
            (int(match[2]), match[1]) for definition in definitions.values()
            if (match := template_pattern.fullmatch(definition))
        ]
    replacements: dict[int, int] = {}
    for picture_no, role in identity_references:
        role_name = re.sub(r"^(?:the|a|an)\s+", "", role, flags=re.I)
        if not role_name or _h3_source_requests_multiple_instances(prompt, role_name):
            continue
        # Fail closed when the authored text could describe distinct instances,
        # including plural nouns and a second/another named identity.
        escaped_role = re.escape(role_name)
        if re.search(
            rf"\b{escaped_role}(?:s|es)\b|\b(?:another|second|additional|different)\s+"
            rf"(?:[A-Za-z'-]+\s+){{0,3}}{escaped_role}\b", prompt, re.I,
        ):
            continue
        role_key = _h3_anchor_key(role_name)
        generated: list[int] = []
        existing: list[int] = []
        for number, definition in definitions.items():
            template = template_pattern.fullmatch(definition)
            if template:
                template_name = re.sub(r"^(?:the|a|an)\s+", "", template[1], flags=re.I)
                if int(template[2]) == picture_no and _h3_anchor_key(template_name) == role_key:
                    generated.append(number)
                continue
            label = re.match(
                r"<Subject\s+\d+>\s*(?:\(S\d+\)\s*)?is\s+([^:;,\n]+)",
                definition, re.I,
            )
            if not label:
                continue
            identity = label[1].strip(" .")
            pictures = {int(value) for value in re.findall(r"<Picture\s+(\d+)>", definition, re.I)}
            if pictures - {picture_no}:
                continue
            identity = re.sub(r"^(?:the|a|an)\s+", "", identity, flags=re.I)
            identity = _ref2va_compound_identity_alias(identity) or identity
            readable_identity = re.sub(r"[_-]+", " ", identity).strip()
            if _h3_anchor_key(readable_identity) == role_key or re.search(
                rf"(?<![\w]){re.escape(role_name)}$", readable_identity, re.I,
            ):
                existing.append(number)
        if len(generated) == 1 and len(existing) == 1:
            replacements[generated[0]] = existing[0]
    if not replacements:
        return prompt
    surviving = [number for number in definitions if number not in replacements]
    renumbered = {number: index + 1 for index, number in enumerate(surviving)}
    # Dropping a duplicate identity must not drop the reference it carried (see the helper).
    cleaned_field = carry_identity_bindings(replacements, definitions, field)
    cleaned = prompt.replace(field, cleaned_field.strip(), 1)
    return re.sub(
        r"<Subject\s+(\d+)>",
        lambda match: f"<Subject {renumbered.get(replacements.get(int(match[1]), int(match[1])), int(match[1]))}>",
        cleaned, flags=re.I,
    )


def _reference_relationships(
    references: Sequence[Mapping[str, Any]] | None,
    subjects: Sequence[Any] | None = None,
    registry: Mapping[str, Any] | None = None,
    source_text: str = "",
) -> tuple[
    list[str],
    list[str],
    bool,
    dict[int, list[str]],
    list[str],
    list[str],
]:
    """Build MiniMax's documented Ref2VA reference contract.

    Visual and audio retention markers intentionally use different fixed
    vocabularies. Identity/location pictures are bound through ``<Subject N>``
    instead of being misrepresented as concrete keyframes.
    """

    definitions: list[str] = []
    retention: list[str] = []
    subject_sources: dict[int, list[str]] = {}
    detail_bindings: list[str] = []
    task_types: list[str] = ["reference generation"]
    picture_no = video_no = audio_no = 0
    has_driving_audio = False
    subject_slots = [
        _planner_subject_slot(subject, position)[0]
        for position, subject in enumerate(subjects or [], start=1)
    ]
    # The planner's own "<Subject N> (Sx)" pairs are the binding the action text
    # uses. A reference that names one of those speakers belongs on that Subject
    # even when the shot lists no row for it or labelled its row corruptly
    # ("<Subject 2> (e) mature man"), which otherwise allocated a fresh Subject
    # and gave one participant two contradictory identities.
    speaker_slots: dict[str, int] = {}
    for slot, speaker in sorted(_h3_body_subject_bindings(source_text).items()):
        speaker_slots.setdefault(speaker.casefold(), slot)
    next_subject_no = max(
        [*subject_slots, *speaker_slots.values(), 0],
    ) + 1
    registry = registry if isinstance(registry, Mapping) else {}

    def mapped_subject(role: str) -> tuple[int, str, str] | None:
        matched_index = _mapped_ref2va_subject(role, subjects or [])
        if matched_index is not None:
            subject = (subjects or [])[matched_index]
            character_id = _normalized_space(_field(subject, "character_id", ""))
            aliases = _ref2va_identity_aliases(subject)
            subject_name = (
                _h3_identity_label(_field(subject, "speaker_name", ""))
                or _ref2va_inferred_visible_identity(subject)
                or _h3_identity_label(character_id)
                or (aliases[0] if aliases else f"visible subject {matched_index + 1}")
            )
            entry = _speaker_registry_entry(registry, character_id or subject_name)
            # The planner's slot, not the row's list position: a one-person
            # shot of S2 is position 1 but Subject 2, and returning the
            # position minted a phantom <Subject 1> beside the real <Subject 2>.
            slot, _ = _planner_subject_slot(subject, matched_index + 1)
            return slot, subject_name, entry[0] if entry else ""
        # A planner row can name its participant only through the inline "(Sx)" label, or through
        # the label the body binds to its Subject. Falling through to a brand-new Subject gave one
        # participant two labels -- one for acting and one for identity -- and the identity
        # reference then never reached the person it described.
        return planner_subject_for_role(
            role,
            subjects or [],
            registry=registry,
            speaker_slots=speaker_slots,
            planner_slot=_planner_subject_slot,
            registry_entry=_speaker_registry_entry,
            field=_field,
            normalize=_normalized_space,
        )

    def add_task_type(value: str) -> None:
        if value not in task_types:
            task_types.append(value)

    for reference in references or []:
        kind = str(reference.get("type") or "").strip().lower()
        role = _trim_sentence(reference.get("role", "")) or f"the supplied {kind} reference"
        subject_match = (
            mapped_subject(role)
            if kind != "image" or str(reference.get("image_intent") or "identity").lower() == "identity"
            else None
        )
        mapped_role = (
            f"<Subject {subject_match[0]}> ({subject_match[1]})"
            if subject_match else role
        )
        if kind == "image":
            picture_no += 1
            label = f"<Picture {picture_no}>"
            intent = str(reference.get("image_intent") or "identity").lower()
            if intent == "object":
                from models.minimax_h3.reference_manifest import object_reference_prompt_contract

                definition, analysis = object_reference_prompt_contract(label, role)
                definitions.append(definition)
                retention.append(analysis)
                detail_bindings.append(
                    f"The requested object uses {label}'s design and appearance with "
                    "the count, scale, placement, and action described in the target scene."
                )
                continue
            if intent == "composition":
                definitions.append(
                    f"{label} is the soft composition and cast-layout anchor "
                    f"for [Shot 1], showing {mapped_role}."
                )
                retention.append(
                    f"{label} ([Shot 1] composition anchor): partially_preserved - "
                    "retain the intended subject placement, wardrobe, setting, "
                    "and spatial relationships while generating natural motion "
                    "rather than a frozen opening frame."
                )
                detail_bindings.append(
                    f"{label} softly guides the opening composition and cast layout."
                )
                continue

            if intent in {"scene", "style"}:
                subject_match = None
            if subject_match:
                subject_no, subject_name, _ = subject_match
            else:
                subject_no = next_subject_no
                next_subject_no += 1
                aliases = _ref2va_identity_aliases({"visual_description": role})
                subject_name = aliases[0] if intent == "identity" and aliases else role

            if intent == "scene":
                source_clause = f"environment and location identity come from {label}"
                explanation = (
                    f"preserve the architecture, materials, lighting context, and "
                    f"location identity supplied by {label}, but not incidental characters"
                )
                marker = "fully_preserved"
                detail_bindings.append(
                    f"<Subject {subject_no}> is the environment established by {label}."
                )
            elif intent == "style":
                source_clause = f"visual style is guided by {label}"
                explanation = (
                    f"retain broad similarity to the medium, palette, lighting "
                    f"language, and texture of {label}, but not its characters, pose, "
                    "framing, or exact composition"
                )
                marker = "weak_reference"
                detail_bindings.append(
                    f"The visual treatment of <Subject {subject_no}> follows {label} broadly."
                )
            else:
                source_clause = (
                    f"facial, bodily, and character identity come from {label}"
                )
                allow_multiple = _h3_source_requests_multiple_instances(
                    source_text,
                    subject_name,
                )
                uniqueness = (
                    " This mapping is exactly one physical instance of the "
                    "character; do not create a second copy, clone, reflection, "
                    "portrait, screen image, background likeness, or literal "
                    "reference-image cutaway."
                    if not allow_multiple else ""
                )
                explanation = (
                    f"preserve the facial, bodily, and character identity supplied "
                    f"by {label}; use it for identity only, follow the target "
                    "shot's explicitly described wardrobe and lighting, and do "
                    "not copy its background, source location, framing, "
                    "composition, pose, source lighting, or opening-still appearance"
                    f"{uniqueness}"
                )
                marker = "fully_preserved"
                detail_bindings.append(
                    f"<Subject {subject_no}> uses facial, bodily, and character "
                    f"identity from {label} as the same single character already "
                    "described in this shot, never as inserted source footage or "
                    "an additional character."
                    if not allow_multiple else
                    f"<Subject {subject_no}> uses facial, bodily, and character "
                    f"identity from {label} for the explicitly requested multiple "
                    "instances."
                )

            if subject_match:
                subject_sources.setdefault(subject_no, []).append(source_clause)
            else:
                definitions.append(
                    f"<Subject {subject_no}> is {subject_name}, whose {source_clause}."
                )
            retention.append(
                f"<Subject {subject_no}> (appears in [Shot 1]): {marker} - {explanation}."
            )
        elif kind == "video":
            video_no += 1
            label = f"<Video {video_no}>"
            definitions.append(f"{label} provides motion and temporal reference for {mapped_role}.")
            retention.append(
                f"{label} (motion, camera, and temporal structure): weak_reference - "
                "retain only the requested motion, timing, camera, or scene traits "
                "while generating the described target video."
            )
            detail_bindings.append(
                f"The requested motion and temporal behavior follow {label} without copying it as source footage."
            )
        elif kind == "audio":
            audio_no += 1
            label = f"<Audio {audio_no}>"
            intent = str(reference.get("audio_intent") or "voice").lower()
            if intent == "drive":
                has_driving_audio = True
                add_task_type("audio reuse")
                definitions.append(
                    f"{label} is the performance-driving audio timeline for {mapped_role}."
                )
                retention.append(
                    f"{label}: partially_copy - reuse its audible content and "
                    "timing while synchronizing visible action and lip movement; "
                    "additional scene ambience or practical effects may be mixed around it."
                )
                detail_bindings.append(
                    f"Visible performance and lip movement remain synchronized to {label} throughout [Shot 1]."
                )
            elif intent == "style":
                add_task_type("audio reference")
                definitions.append(
                    f"{label} is the audio-style reference for {mapped_role}."
                )
                retention.append(
                    f"{label}: weak_reference - retain broad similarity to its "
                    "rhythm, texture, and style without copying its words, exact "
                    "timing, or waveform."
                )
                detail_bindings.append(
                    f"The requested audio treatment broadly follows {label}."
                )
            elif intent == "sound":
                add_task_type("audio reference")
                definitions.append(
                    f"{label} is a reusable sound-effect reference for {role}."
                )
                retention.append(
                    f"{label}: reference - retain the sound effect's timbre and "
                    "texture without copying its waveform or original timing."
                )
                detail_bindings.append(
                    f"Generate the requested matching sound effects using {label}'s "
                    "timbre and texture, synchronized to the visible actions in this window."
                )
            else:
                add_task_type("audio reference")
                speaker_suffix = ""
                if subject_match and subject_match[2]:
                    speaker_suffix = f" {subject_match[2]}"
                audio_target = (
                    f"<Subject {subject_match[0]}>"
                    if subject_match else mapped_role
                )
                definitions.append(
                    f"{label} is the voice-timbre reference for "
                    f"{audio_target}{speaker_suffix}."
                )
                retention.append(
                    f"{label}: reference - the target speaker follows its voice "
                    "timbre, emotion, and delivery without copying the source "
                    "words, timing, or waveform."
                )
                detail_bindings.append(
                    f"Newly scripted dialogue for {audio_target}{speaker_suffix} follows the voice timbre and delivery of {label}."
                )
    return (
        definitions,
        retention,
        has_driving_audio,
        subject_sources,
        detail_bindings,
        task_types,
    )


# The project's own subject lock, written by the director in the project text:
#     SUBJECT LOCK (critico, no negociable):
#     - <Subject 1> es SIEMPRE Valeria. <Subject 2> es SIEMPRE Ricardo.
# It is the authority on which participant is which Subject, so it is parsed here and used
# when the compiled definitions are numbered.
# Planners decorate the locked name inconsistently -- ``Valeria``,
# ``<Valeria>``, ``"Valeria"`` -- so the decoration is accepted and dropped.
# Without this the lock parsed to nothing and every identity bound only by a
# reference picture lost its name.
_SUBJECT_LOCK_RE = re.compile(
    r"<Subject\s*(\d+)>\s*(?:es\s+SIEMPRE|is\s+ALWAYS)\s+"
    r"[<\"'“‘*]?"
    # "la Figura del Purgatorio" names the Figure, not "la": the article is not part
    # of the participant, and a lock read as {2: "la"} matches no row at all.
    r"(?:(?:la|el|los|las|the|un|una|a|an)\s+)?"
    r"([A-Za-z\u00c1\u00c9\u00cd\u00d3\u00da\u00dc\u00d1"
    r"\u00e1\u00e9\u00ed\u00f3\u00fa\u00fc\u00f1]+)",
    re.IGNORECASE,
)


def project_subject_lock(project_context: str) -> dict[int, str]:
    """The Subject-to-participant map a project declares in its own SUBJECT LOCK block."""

    lock: dict[int, str] = {}
    for match in _SUBJECT_LOCK_RE.finditer(str(project_context or "")):
        lock[int(match.group(1))] = match.group(2)
    return lock


_H3_SUBJECT_SLOT_RE = re.compile(r"<\s*Subject\s+(\d+)\s*>", re.IGNORECASE)
_H3_SPEAKER_TOKEN_RE = re.compile(r"\(\s*S\s*(\d+)\s*\)", re.IGNORECASE)
# A speaker token is a cue, not a name. Rows built by the short-film planner carry
# their label in ``speaker_name`` ("(S1)"), and printing it as the participant's name
# produced ``<Subject 1> ((S1))`` and ``<Subject 1> is (S1)`` -- a Subject introduced
# by the very cue the definition is supposed to explain.
_H3_BARE_SPEAKER_RE = re.compile(r"^\(?\s*S\s*\d+\s*\)?$", re.IGNORECASE)


def _h3_name_not_a_cue(value: Any) -> str:
    """``value`` unless it is only a speaker cue, which is never a participant's name."""

    text = str(value or "").strip()
    if not text or _H3_BARE_SPEAKER_RE.match(text):
        return ""
    return text

# The participant a row starts with, before any other words: "Ricardo (S2), leaning in".
_H3_SUBJECT_ROW_NAME_RE = re.compile(
    r"^\s*(?:<\s*Subject\s*\d+\s*>\s*)?(?:\(\s*S\s*\d+\s*\)\s*)?\s*"
    r"([A-Za-z\u00c1\u00c9\u00cd\u00d3\u00da\u00dc\u00d1\u00e1\u00e9\u00ed\u00f3\u00fa\u00fc\u00f1'\-]+)"
)


def _planner_subject_slot(
    subject: Any,
    position: int,
    lock: Mapping[int, str] | None = None,
) -> tuple[int, str]:
    """Recover the planner's ``<Subject N>`` slot and ``(Sx)`` speaker for a participant.

    The short-film planner emits ``subjects_on_screen`` rows whose only fields
    are ``visual_description`` and ``position_or_relation``, and it names the
    participant inline: ``<Subject 1> (S1), the woman in the cream A-dress.``
    Those rows are not ordered by Subject number. Numbering the compiled
    definitions by list position therefore relabelled a participant as the
    other one in roughly half the shots: the action text kept the planner's
    numbering while ``subject_definitions`` swapped it, so one ``<Subject 1>``
    was bound to ``(S1)`` in one field and to ``(S2)`` in the other. The model
    resolved that contradiction by giving one participant the other's reference
    appearance.

    Measured again on a 177-shot project: the rows arrive in *frame* order and
    carry the participant's own label inline -- ``"Ricardo (S2), leaning into the
    frame."`` -- without the ``<Subject N>`` wrapper this parser required, so
    every one of them fell through to list position. That is how 56 of those
    shots were built on the other person, which is what puts one character's
    voice on the other one. The name is now the first thing consulted, against
    the project's SUBJECT LOCK; the explicit label and the inline number follow,
    and list position stays as the last resort for a project with no lock and no
    labelling at all.
    """

    raw = _field(subject, "visual_description", "")
    if not raw and not isinstance(subject, Mapping):
        raw = subject
    text = str(raw or "")
    tag = _H3_SUBJECT_SLOT_RE.search(text)
    token = _H3_SPEAKER_TOKEN_RE.search(text)
    inline_speaker = f"S{int(token.group(1))}" if token else ""

    slot = 0
    locked = dict(lock or {})
    if locked:
        named = _H3_SUBJECT_ROW_NAME_RE.match(text)
        person = named.group(1).casefold() if named else ""
        for number, name in locked.items():
            if person and str(name).casefold() == person:
                slot = int(number)
                break
    if not slot and tag and int(tag.group(1)) > 0:
        slot = int(tag.group(1))
    if not slot and token and int(token.group(1)) > 0:
        # This pipeline numbers its speakers as identities -- "S1 is always
        # Valeria, reused when she speaks again, never S3" -- so the label the
        # planner wrote inline is that participant's number.
        slot = int(token.group(1))
    return slot or position, inline_speaker


def _subject_description_without_label(subject: Any) -> str:
    """Drop the planner's leading ``<Subject N> (Sx)`` label from a row.

    The label is rendered by Maestro and consumed by
    :func:`_planner_subject_slot`. Leaving it in the prose produced a
    definition such as ``<Subject 1> is subject 1: (S2), speaking
    authoritatively.``, where a speaker token sat in the identity slot and
    disagreed with the speaker the same label had in the action text.
    """

    text = str(_field(subject, "visual_description", "") or "")
    if isinstance(subject, str):
        text = subject
    text = _H3_SUBJECT_SLOT_RE.sub(" ", text, count=1)
    text = re.sub(r"^\s*[,;:.\-\u2013\u2014]*\s*", "", text)
    return _H3_SPEAKER_TOKEN_RE.sub(" ", text, count=1)


def _h3_body_subject_bindings(body: str) -> dict[int, str]:
    """Read the authoritative ``<Subject N> (Sx)`` pairs from the body."""

    bindings: dict[int, str] = {}
    for match in re.finditer(
        r"<\s*Subject\s+(\d+)\s*>\s*\(\s*S\s*(\d+)\s*\)",
        str(body or ""),
        re.IGNORECASE,
    ):
        bindings.setdefault(int(match.group(1)), f"S{int(match.group(2))}")
    return bindings


_REF2VA_ROLE_SPEAKER_RE = re.compile(r"\(\s*S\s*(\d+)\s*\)", re.IGNORECASE)


def _ref2va_reference_speaker_pairings(
    references: Sequence[Mapping[str, Any]] | None,
    subjects: Sequence[Any] | None,
    dialogue_beats: Sequence[Any] | None,
) -> dict[int, str]:
    """The ``(Sx)`` each Subject already has a statement for, as ``{slot: "S2"}``.

    A reference role is authored by the user and may state the label beside the name
    ("Valeria (S1)"). The renderer only accepts a bare ``(Sx)`` cue when the prompt's
    definitions pair it with a Subject, and a plan whose rows carry names but no labels
    produced neither -- so three spoken lines could not be attributed at all.

    When exactly one participant is left unlabelled and exactly one speaker label used by
    the clip is left unclaimed, that is the remaining pairing. Anything less certain is
    left unsaid: the renderer refuses a guess rather than hand a line to the wrong voice.
    """

    subjects = list(subjects or [])
    slots = [
        _planner_subject_slot(subject, position)[0]
        for position, subject in enumerate(subjects, start=1)
    ]
    pairings: dict[int, str] = {}
    conflicts: set[int] = set()
    for reference in references or []:
        role = _normalized_space(_field(reference, "role", ""))
        match = _REF2VA_ROLE_SPEAKER_RE.search(role)
        if not match:
            continue
        index = _mapped_ref2va_subject(role, subjects)
        if index is None:
            continue
        slot = slots[index]
        stated = f"S{int(match.group(1))}"
        if slot in pairings and pairings[slot] != stated:
            conflicts.add(slot)
            continue
        pairings[slot] = stated
    for slot in conflicts:
        pairings.pop(slot, None)

    used: set[int] = set()
    for beat in dialogue_beats or []:
        match = re.fullmatch(
            r"\(\s*S(\d+)\s*\)",
            _normalized_space(_field(beat, "speaker_id", "")),
            re.IGNORECASE,
        )
        if match:
            used.add(int(match.group(1)))
    unclaimed = sorted(used - {int(value[1:]) for value in pairings.values()})
    unlabelled = [slot for slot in slots if slot not in pairings]
    if len(unlabelled) == 1 and len(unclaimed) == 1:
        pairings[unlabelled[0]] = f"S{unclaimed[0]}"
    return pairings


def _ref2va_subject_definitions(
    subjects: Sequence[Any],
    registry: Mapping[str, Any],
    source_bindings: Mapping[int, Sequence[str]] | None = None,
    body: str = "",
    project_context: str = "",
    speaker_pairings: Mapping[int, str] | None = None,
    authored_text: str = "",
) -> list[str]:
    """Emit one definition per participant, numbered as the project numbers them.

    ``body`` is the compiled action text, which already carries the canonical
    ``<Subject N> (Sx)`` pairs. Reusing them here is what keeps the identity
    block and the action text describing the same person; without it the two
    disagreed for every shot whose ``subjects_on_screen`` rows arrived in a
    different order than the planner numbered them.

    ``project_context`` carries the project's SUBJECT LOCK when it has one, which
    is the authority on who is which Subject.
    """

    source_bindings = source_bindings or {}
    speaker_pairings = speaker_pairings or {}
    body_bindings = _h3_body_subject_bindings(body)
    lock = project_subject_lock(project_context)
    rows: list[dict[str, Any]] = []
    seen: dict[int, int] = {}
    for position, subject in enumerate(subjects or [], start=1):
        slot, inline_speaker = _planner_subject_slot(subject, position, lock)
        character_id = _normalized_space(_field(subject, "character_id", ""))
        speaker_name = _normalized_space(
            _h3_name_not_a_cue(_field(subject, "speaker_name", "")),
        )
        if not inline_speaker:
            # The planner may carry the cue in ``speaker_name`` rather than inline in
            # the description. It is not the participant's name, but the renderer
            # still needs it paired with this Subject.
            cue = _H3_SPEAKER_TOKEN_RE.search(str(_field(subject, "speaker_name", "") or ""))
            if cue:
                inline_speaker = f"S{int(cue.group(1))}"
        aliases = _ref2va_identity_aliases(subject)
        name = (
            _clean_h3_metadata(speaker_name, 80)
            or _ref2va_inferred_visible_identity(subject)
            or _h3_identity_label(character_id)
            or next(iter(aliases), f"visible subject {position}")
        )
        entry = (
            _speaker_registry_entry(registry, character_id or speaker_name)
            or _speaker_registry_entry(registry, name)
        )
        speaker = (
            body_bindings.get(slot)
            or (entry[0].strip("()") if entry and entry[0] else "")
            or speaker_pairings.get(slot, "")
            or inline_speaker
        )
        # Maestro writes "<Subject N>" itself, so a copy left in the planner's
        # description would duplicate it, and a corrupted label such as
        # "<Subject q> (S1)" must never reach the prompt.
        description = _trim_sentence(
            _clean_subject_text(_subject_description_without_label(subject)),
        )
        wardrobe = _trim_sentence(_clean_subject_text(_field(subject, "wardrobe", "")))
        details = [
            item for item in (
                description,
                f"wearing {wardrobe}" if wardrobe else "",
                *source_bindings.get(slot, ()),
            ) if item
        ]
        if slot in seen:
            # Two rows claiming one slot describe one participant. Merge them
            # rather than emit two definitions that contradict each other.
            existing = rows[seen[slot]]
            for item in details:
                if item not in existing["details"]:
                    existing["details"].append(item)
            continue
        seen[slot] = len(rows)
        rows.append({
            "slot": slot,
            "name": name,
            "speaker": speaker,
            "details": details,
        })

    # An identity picture can be bound to a Subject the shot's own row list
    # omitted ("<Subject 2> (S2) in dark grey suit" as the only row). The
    # binding must still be declared, or the reference reaches H3 under no
    # label the action text uses.
    for slot in sorted(source_bindings):
        if slot in seen:
            continue
        seen[slot] = len(rows)
        rows.append({
            "slot": slot,
            "name": _clean_h3_metadata(lock.get(slot, ""), 80),
            "speaker": body_bindings.get(slot, ""),
            "details": list(source_bindings[slot]),
        })

    for slot, name in lock.items():
        if slot in seen and not rows[seen[slot]]["name"]:
            rows[seen[slot]]["name"] = _clean_h3_metadata(name, 80)

    # A label the authored text already uses but no row declared -- a hand-edited
    # prompt, or a plan whose rows an edit cleared -- still has to be defined, or
    # validation rejects the prompt for using an undefined subject. Read the
    # authored text, not the compiled body: the body also carries the injected
    # project context, whose SUBJECT LOCK rules name every slot, and defining a
    # slot that no action uses states the cast twice.
    for match in _H3_SUBJECT_SLOT_RE.finditer(authored_text or body):
        slot = int(match.group(1))
        if slot in seen or slot not in lock:
            continue
        seen[slot] = len(rows)
        rows.append({
            "slot": slot,
            "name": _clean_h3_metadata(lock[slot], 80),
            "speaker": body_bindings.get(slot) or speaker_pairings.get(slot, ""),
            "details": [],
        })
    for slot in sorted(body_bindings):
        if slot in seen:
            continue
        seen[slot] = len(rows)
        rows.append({
            "slot": slot,
            "name": f"visible subject {slot}",
            "speaker": body_bindings.get(slot, ""),
            "details": [],
        })

    definitions: list[str] = []
    for row in sorted(rows, key=lambda item: item["slot"]):
        label = f"<Subject {row['slot']}>"
        if row["name"]:
            label = f"{label} is {row['name']}"
        if row["speaker"]:
            label = f"{label} ({row['speaker']})"
        if not row["name"]:
            # Every definition must say what the Subject is. A participant bound
            # only by an identity picture can arrive without a name, and
            # emitting "<Subject 2> (S2): ..." left the label undefined to the
            # contract while the action text already used it.
            label = f"{label} is the visible character in its reference"
        details = "; ".join(row["details"])
        definitions.append(f"{label}" + (f": {details}." if details else "."))
    return definitions


def _label_ref2va_subjects_in_body(
    body: str,
    subjects: Sequence[Any],
) -> str:
    """Insert each official subject label at its first named appearance."""

    result = body
    missing: list[str] = []
    for index, subject in enumerate(subjects or [], start=1):
        # Use the participant's own slot, not its list position. A one-person
        # shot of S2 is position 1 but Subject 2, and labelling it <Subject 1>
        # here made the action text contradict the definitions, which are
        # numbered from the same inline label (a real run failed with
        # "Ref2VA visual action uses undefined subjects: 1").
        slot, _ = _planner_subject_slot(subject, index)
        label = f"<Subject {slot}>"
        if label in result:
            continue
        speaker_token = _h3_name_not_a_cue(_field(subject, "speaker_name", ""))
        name = (
            _h3_identity_label(speaker_token)
            or _ref2va_inferred_visible_identity(subject)
            or _h3_identity_label(_field(subject, "character_id", ""))
        )
        if (
            not _h3_identity_label(speaker_token)
            and not _ref2va_inferred_visible_identity(subject)
        ):
            # Semantic IDs such as lead_performer need not occur in natural
            # visual prose. Bind a unique visible identity alias instead.
            for alias in _ref2va_identity_aliases(subject):
                if not re.search(rf"(?<![\w>]){re.escape(alias)}(?![\w<])", result, re.I):
                    continue
                try:
                    matched = _mapped_ref2va_subject(alias, subjects)
                except H3DialogueContractError:
                    continue
                if matched == index - 1:
                    name = alias
                    break
        if not name:
            continue
        pattern = re.compile(rf"(?<![\w>]){re.escape(name)}(?![\w<])", re.IGNORECASE)
        result, count = pattern.subn(f"{label} ({name})", result, count=1)
        if not count:
            # A machine identity ("director_identity_s1") is not something the model
            # can see in the frame; naming it there only adds noise.
            described = "" if "_" in name else f" ({name})"
            missing.append(f"{label}{described} is visible in the described blocking.")
    if missing:
        shot = re.search(r"\[Shot\s+1\]", result, flags=re.IGNORECASE)
        if shot:
            result = (
                f"{result[:shot.end()]} {' '.join(missing)} "
                f"{result[shot.end():].lstrip()}"
            )
        else:
            result = f"{' '.join(missing)} {result}".strip()
    return result


def _split_ref2va_style_opening(body: str) -> tuple[str, str]:
    """Separate or synthesize MiniMax's pre-[Shot 1] style opening."""

    text = _normalized_space(body)
    shot = re.search(r"\[Shot\s+1\]", text, flags=re.IGNORECASE)
    timed_first_shot = bool(
        shot and _H3_TIMED_SHOT_MARKER_RE.match(text[shot.start():])
    )
    if shot and text[:shot.start()].strip():
        opening = _normalized_space(text[:shot.start()])
        # Keep a real timestamped first marker in the timeline. It may be
        # needed to distinguish a malformed [Shot 1], [Shot 3] sequence from
        # a valid local sequence after the compiler adds its own Shot 1.
        timeline = text[shot.start() if timed_first_shot else shot.end():].strip()
        return opening.rstrip(" .") + ".", timeline
    if shot and not timed_first_shot:
        text = text[shot.end():].strip()

    # Free-form prose after a Lighting/Mood label can include the whole action
    # timeline. Echoing that span here performed source transitions twice.
    # Keep authored lighting and mood in the body, with a neutral style opening.
    opening = (
        "The target video maintains the requested visual style, lighting, "
        "color, and cinematic texture."
    )
    return opening, text


def _alignment_header(
    mode: str,
    duration_seconds: float,
    final_shot_number: int = 1,
) -> str:
    duration = max(0.0, float(duration_seconds or 0.0))
    final_shot_number = max(1, int(final_shot_number or 1))
    if mode == "i2va":
        return (
            "For the target video, at 0.00 seconds into the target video, "
            "<Picture 1> (from [Shot 1]) is fully referenced."
        )
    if mode == "fl2va":
        return (
            "How the reference pictures align with the target video — "
            "Picture 1 (from Shot 1) aligns with the 0.00-second mark of the "
            f"target video; Picture 2 (from Shot {final_shot_number}) aligns with the "
            f"{duration:.2f}-second "
            "mark of the target video."
        )
    if mode == "l2va":
        return (
            "How the reference pictures align with the target video — "
            f"<Picture 1> (from [Shot {final_shot_number}]) aligns with the "
            f"{duration:.2f}-second mark of the target video."
        )
    return ""


def compile_h3_official_prompt(
    prompt: str,
    subjects: Sequence[Any] | None,
    dialogue_beats: Sequence[Any] | None,
    *,
    mode: str = "t2va",
    duration_seconds: float = 0.0,
    references: Sequence[Mapping[str, Any]] | None = None,
    speaker_registry: Mapping[str, Any] | None = None,
    project_context: str = "",
    context_anchors: Sequence[str] | None = None,
    visual_style: str = "",
    opening_blocking: str = "",
    closing_blocking: str = "",
    audio_plan: Mapping[str, Any] | None = None,
    default_language: str | None = None,
    audio_start_seconds: Any = None,
    audio_policy: Mapping[str, Any] | None = None,
    dialogue_manifest_authoritative: bool = False,
    strict_audio_policy: bool = False,
    driving_audio: bool = False,
) -> tuple[str, str]:
    """Compile Director metadata into MiniMax's documented Context-IR shape."""

    mode = str(mode or "t2va").strip().lower()
    if mode not in {"t2va", "i2va", "fl2va", "l2va", "ref2va"}:
        raise H3DialogueContractError(f"Unknown MiniMax H3 prompt mode: {mode}")
    # Repair a corrupted fragment from another script that a planner glued into
    # the middle of a Latin word, before it is copied into any field.
    prompt = restore_h3_field_layout(repair_glued_foreign_script(prompt))
    registry = speaker_registry if isinstance(speaker_registry, Mapping) else {}
    reviewed_definitions: dict[int, str] = {}
    if mode == "ref2va":
        prompt = _collapse_ref2va_compiler_identity_duplicates(prompt, references)
        subjects, reviewed_definitions = _ref2va_cast_from_source(prompt, subjects or [])
    (
        reference_definitions,
        retention,
        has_driving_audio,
        subject_sources,
        detail_bindings,
        task_types,
    ) = _reference_relationships(
        references if mode == "ref2va" else None,
        subjects or [],
        registry,
        source_text=f"{project_context}\n{prompt}",
    )
    # The caller may know something the manifest cannot show: the project supplies its
    # own soundtrack, so the mouths follow that track instead of generated speech. A
    # clip whose own audio plan came through neutral then read as "silent shot with no
    # track", and describing the very mouth that has to sync was refused as a request
    # to generate speech.
    #
    # Kept separate from ``has_driving_audio`` on purpose: that flag also decides which
    # beats this compiler writes into the prompt, and widening it silenced a shot whose
    # beats the caller still required -- "expected 4 dialogue block(s), found 0".
    policy_driving_audio = has_driving_audio or driving_audio
    audio_mode = _normalized_space(_field(audio_plan or {}, "mode", "")).casefold()
    if audio_mode == "music_driven":
        from .music_performance import constrain_music_performance
        activity = _field(audio_plan or {}, "vocal_activity", None)
        prompt = constrain_music_performance(
            prompt, subjects, activity, project_context=project_context,
        )
        opening_blocking = constrain_music_performance(
            opening_blocking, subjects, activity, project_context=project_context,
        )
        closing_blocking = constrain_music_performance(
            closing_blocking, subjects, activity, project_context=project_context,
        )
    # Departs from upstream only where the plan says the mouths follow the line; the
    # policy module says why the mode alone does not answer this. The same answer is
    # handed to the dialogue compiler below: it decides which beats it places, and a
    # second gate left on the raw audio mode would drop them again.
    states_lines = _transcript.states_the_lines(
        audio_mode=audio_mode, audio_plan=audio_plan, driving_audio=has_driving_audio,
    )
    if not states_lines:
        dialogue_beats = []
    if audio_mode in {"audio_driven", "music_driven"}:
        # Initial Director preflight runs before concrete Ref2VA manifests are
        # assembled. The shot's audio plan still proves that a mapped source
        # track owns the vocals, so do not temporarily treat it as a silent or
        # prompt-scripted shot.
        has_driving_audio = True
    if strict_audio_policy:
        violations = _audio_policy_violations(
            prompt,
            audio_policy,
            dialogue_beats=dialogue_beats,
            dialogue_manifest_authoritative=dialogue_manifest_authoritative,
            has_driving_audio=policy_driving_audio,
            ignore_regions=[_h3_injected_project_context(project_context)]
            if project_context else None,
        )
        if violations:
            raise H3DialogueContractError(
                "Audio policy violation: " + "; ".join(violations)
            )
    if (
        _audio_policy_flag(audio_policy, "generated_speech_allowed") is False
        or (
            dialogue_manifest_authoritative
            and not _has_spoken_beats(dialogue_beats)
        )
    ) and not has_driving_audio:
        # The policy controls generated speech, not a supplied driving track.
        # In a visual-only shot, an empty authoritative manifest means there
        # are no exact lines available for the planner to add or promote.
        dialogue_beats = []
    body, soundscape, music, existing_blocks = _source_prompt_parts(
        prompt,
        project_context=project_context,
        context_anchors=context_anchors,
        opening_blocking=opening_blocking,
        closing_blocking=closing_blocking,
        audio_plan=audio_plan,
        audio_policy=audio_policy,
        has_driving_audio=has_driving_audio and not states_lines,
    )
    style_text = _normalized_space(str(visual_style or "")).rstrip(" .")
    if style_text:
        style_direction = (
            "Visual style across the entire frame: "
            f"{style_text}. Apply this style consistently to every subject, "
            "the environment and background, and all linework, shading, "
            "color treatment, and rendering."
        )
        if _normalized_space(style_direction).casefold() not in _normalized_space(body).casefold():
            # Ref2VA treats prose before its first shot marker as the visual-
            # style opening. Give this explicit direction that same placement
            # while keeping it out of the immutable source prompt. Other H3
            # modes retain it as an introduction to their complete timeline.
            if mode == "ref2va" and not re.search(
                r"\[Shot\s+1\]", body, flags=re.IGNORECASE,
            ):
                body = f"{style_direction} [Shot 1] {body}".strip()
            else:
                body = f"{style_direction} {body}".strip()
    speech_forbidden = (
        _audio_policy_flag(audio_policy, "generated_speech_allowed") is False
        or (
            dialogue_manifest_authoritative
            and not _has_spoken_beats(dialogue_beats)
        )
    )
    if speech_forbidden and not has_driving_audio:
        # Compacting and Ref2VA subject labeling can remove tags before the
        # vocal compiler sees them. Strip the attached planner lead-in first so
        # a remnant such as ``The Kitten says:`` cannot survive as an instruction.
        body = _strip_forbidden_dialogue_blocks(body)
        existing_blocks = []
    if mode == "ref2va":
        style_opening, body = _split_ref2va_style_opening(body)
        # The opening is compiler-owned framing (style and project context) and
        # is re-attached after the vocal compiler has emitted the shot's exact
        # lines. A dialogue block left here is a second copy the model can
        # speak, and the contract counts it as an unexpected block.
        if _has_spoken_beats(dialogue_beats):
            style_opening = _strip_h3_dialogue_blocks(style_opening)
        # Bind visual prose before inserting compiler-owned vocal directions.
        # Otherwise the inserted labels alter those exact canonical sentences
        # and a second preflight can no longer remove/rebuild them once.
        body = _label_ref2va_subjects_in_body(body, subjects or [])
        body, vocal_contract = _compile_official_dialogue(
            body,
            subjects or [],
            dialogue_beats or [],
            registry,
            existing_blocks,
            has_driving_audio=has_driving_audio and not states_lines,
            duration_seconds=duration_seconds,
            music_driven=audio_mode == "music_driven",
            vocal_activity=_field(audio_plan or {}, "vocal_activity", None),
            project_context=project_context,
            default_language=default_language,
            generated_speech_allowed=_audio_policy_flag(
                audio_policy, "generated_speech_allowed",
            ),
            dialogue_manifest_authoritative=dialogue_manifest_authoritative,
        )
        if not _H3_TIMED_SHOT_MARKER_RE.match(body.lstrip()):
            body = re.sub(r"^\s*\[Shot\s+1\]\s*", "", body, flags=re.IGNORECASE)
        body = f"[Shot 1] {body}".strip()
        body, _ = _align_h3_time_markers(body, audio_start_seconds)
        # After alignment: it needs the ``[Shot N] At ...`` shape this removes, and a
        # loose ``At MM:SS.mmm`` would otherwise read as a cut inside the one shot.
        # The clip's duration also decides a labelled marker the planner took from
        # the film's clock: no second shot can sit past the end of this clip.
        body = strip_loose_cut_times(_single_shot_body(body), duration_seconds)[0]
        subject_definitions = _ref2va_subject_definitions(
            subjects or [],
            registry,
            subject_sources,
            body=body,
            project_context=project_context,
            speaker_pairings=_ref2va_reference_speaker_pairings(
                references if mode == "ref2va" else None,
                subjects or [],
                dialogue_beats,
            ),
            authored_text=prompt,
        )
        for index, reviewed in reviewed_definitions.items():
            missing_sources = [
                clause for clause in subject_sources.get(index, ())
                if any(label not in reviewed for label in re.findall(r"<Picture \d+>", clause))
            ]
            subject_definitions[index - 1] = reviewed.rstrip(" .") + (
                "; " + "; ".join(missing_sources) if missing_sources else ""
            ) + "."
        definitions = "\n".join([*subject_definitions, *reference_definitions])
        if not definitions:
            definitions = "Use the explicitly described subjects and target scene."
        retention_text = "\n".join(retention) or (
            "Preserve the explicitly described identities, wardrobe, setting, action, and audio roles."
        )
        body = _label_ref2va_subjects_in_body(body, subjects or [])
        # Prefer the structured action, not the preceding-frame handoff or the
        # film-wide contract that is added to detailed_description.
        action_summary = re.search(
            r"\bAction:\s*(.+?)(?=\s+(?:Lighting|Mood|Final beat|"
            r"overall_soundscape|non_diegetic_music)\s*:|$)",
            prompt,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if action_summary:
            summary_body = action_summary.group(1)
        else:
            summary_body, _, _, _ = _source_prompt_parts(
                prompt,
                opening_blocking=opening_blocking,
                closing_blocking=closing_blocking,
            )
        summary_body = re.sub(r"^\[Shot\s+1\]\s*", "", summary_body, flags=re.IGNORECASE)
        summary_body = _H3_STRICT_DIALOGUE_RE.sub(
            "scripted dialogue",
            summary_body,
        )
        # The scope line states a constraint, not what the shot shows. Left in,
        # it became the one-line summary of every shot that had no opening
        # composition ahead of it.
        summary_body = summary_body.replace(_H3_SINGLE_SHOT_SCOPE, "", 1).strip()
        summary = _trim_sentence(re.split(r"(?<=[.!?])\s+", summary_body, maxsplit=1)[0])
        if len(summary) > 320:
            summary = summary[:320].rsplit(" ", 1)[0].rstrip(" ,;:-") + "..."
        summary = (
            f"[{' + '.join(task_types)}] "
            f"{summary or 'A complete audiovisual shot matching the mapped subjects and requested action.'}"
        )
        if detail_bindings:
            body = re.sub(r"^\[Shot\s+1\]\s*", "", body, flags=re.IGNORECASE)
            body = f"[Shot 1] {' '.join(detail_bindings)} {body}".strip()
        # The style opening may already contain the world anchor inserted
        # during source recovery. Check the complete visual field so moving
        # that opening does not cause a second copy to be added before it.
        body = f"{style_opening} {body}".strip()
        body = _ensure_h3_context_anchors(body, context_anchors or [])
        body = _normalize_h3_compiled_visual_shots(body)
        if has_driving_audio and music == "N/A":
            music = "Use the mapped driving audio according to retention_analysis."
        compiled = (
            f"subject_definitions: {definitions}\n\n"
            f"summary: {summary}\n\n"
            f"retention_analysis: {retention_text}\n\n"
            f"detailed_description: {body}\n\n"
            f"overall_soundscape: {_clean_h3_contract_field(soundscape)}.\n\n"
            f"non_diegetic_music: {_clean_h3_contract_field(music)}"
        )
    else:
        compact_body = _compact_h3_visual_body(
            body, subjects or [], registry, closing_blocking=closing_blocking,
        )
        compact_body = _ensure_h3_context_anchors(compact_body, context_anchors or [])
        compiled_body, vocal_contract = _compile_official_dialogue(
            compact_body, subjects or [], dialogue_beats or [], registry, existing_blocks,
            has_driving_audio=has_driving_audio, duration_seconds=duration_seconds,
            music_driven=audio_mode == "music_driven",
            vocal_activity=_field(audio_plan or {}, "vocal_activity", None),
            project_context=project_context,
            default_language=default_language,
            generated_speech_allowed=_audio_policy_flag(
                audio_policy, "generated_speech_allowed",
            ),
            dialogue_manifest_authoritative=dialogue_manifest_authoritative,
        )
        if not _H3_TIMED_SHOT_MARKER_RE.match(compiled_body.lstrip()):
            compiled_body = re.sub(
                r"^\s*\[Shot\s+1\]\s*",
                "",
                compiled_body,
                flags=re.IGNORECASE,
            )
        compiled_body = f"[Shot 1] {compiled_body}".strip()
        compiled_body, _ = _align_h3_time_markers(
            compiled_body, audio_start_seconds,
        )
        # Before the shot numbers are read, so a clip-local header cannot inherit the
        # planner's film-global number, and a loose timestamp cannot read as a cut.
        compiled_body = strip_loose_cut_times(
            _single_shot_body(compiled_body), duration_seconds,
        )[0]
        compiled_body = _normalize_h3_compiled_visual_shots(compiled_body)
        shot_numbers = [
            int(marker.group("number"))
            for marker in _h3_visual_shot_markers(compiled_body)
        ]
        header = _alignment_header(mode, duration_seconds, max(shot_numbers) if shot_numbers else 1)
        compiled = (
            f"integrated_multimodal_description: {compiled_body}\n\n"
            f"overall_soundscape: {_clean_h3_contract_field(soundscape)}.\n\n"
            f"non_diegetic_music: {_clean_h3_contract_field(music)}"
        )
        if header:
            compiled = f"{header}\n\n{compiled}"
        initial_tokens = final_tokens = h3_prompt_token_count(compiled)
        if final_tokens > _H3_DIRECTOR_TEXT_TOKEN_BUDGET:
            # Give unusually dense Director prompts one final structure-aware
            # quality pass. Exact dialogue, timing, and first/final states are
            # protected; if the target cannot be reached safely the complete
            # prompt remains valid and is sent to H3 intact.
            fitted = fit_h3_base_prompt(
                compiled,
                target_tokens=_H3_DIRECTOR_TEXT_TOKEN_BUDGET,
            )
            compiled = fitted.prompt
            final_tokens = fitted.token_count
        if final_tokens < initial_tokens:
            print(
                "[MiniMax H3] Compacted Director prompt from "
                f"{initial_tokens} to {final_tokens} text tokens for clearer "
                "instruction adherence."
            )
        elif final_tokens > _H3_DIRECTOR_TEXT_TOKEN_BUDGET:
            print(
                "[MiniMax H3] Preserving complete Director prompt at "
                f"{final_tokens} text tokens; the quality target is not a "
                "generation limit."
            )
    compiled = normalize_h3_text(compiled).strip()
    policy_errors = _audio_policy_violations(
        compiled,
        audio_policy,
        dialogue_beats=dialogue_beats,
        dialogue_manifest_authoritative=dialogue_manifest_authoritative,
        has_driving_audio=policy_driving_audio or has_driving_audio,
        ignore_regions=[_h3_injected_project_context(project_context)]
        if project_context else None,
    )
    if policy_errors:
        raise H3DialogueContractError(
            "Invalid MiniMax H3 audio policy: " + "; ".join(policy_errors)
        )
    return compiled, vocal_contract


def validate_final_h3_subject_bindings(
    prompt: str,
    expected_bindings: Mapping[int, int | None] | None = None,
) -> list[str]:
    """Check final Ref2VA picture ownership by subject number.

    ``expected_bindings`` maps each identity picture number to its expected
    subject number. Use ``None`` when the reference has no safe existing-cast
    match; that picture must still bind to exactly one subject. This pure
    check can also be called at the final render boundary after prompt review.
    """

    definitions_text = _extract_h3_fields(str(prompt or "")).get(
        "subject_definitions", "",
    )
    definitions = list(re.finditer(
        r"<Subject\s+(\d+)>\s*(?:\(S\d+\)\s*)?is\s+",
        definitions_text,
        re.I,
    ))
    owners_by_picture: dict[int, list[int]] = {}
    for index, match in enumerate(definitions):
        end = (
            definitions[index + 1].start()
            if index + 1 < len(definitions)
            else len(definitions_text)
        )
        body = definitions_text[match.end():end]
        subject_number = int(match[1])
        picture_numbers = {
            int(value) for value in re.findall(r"<Picture\s+(\d+)>", body, re.I)
        }
        for picture_number in picture_numbers:
            owners_by_picture.setdefault(picture_number, []).append(subject_number)

    expected = (
        expected_bindings
        if expected_bindings is not None
        else {picture_number: None for picture_number in owners_by_picture}
    )
    errors: list[str] = []
    for raw_picture_number, raw_subject_number in expected.items():
        picture_number = int(raw_picture_number)
        label = f"<Picture {picture_number}>"
        owners = owners_by_picture.get(picture_number, [])
        if raw_subject_number is None:
            if len(owners) != 1:
                errors.append(
                    f"Identity reference {label} must have exactly one subject binding; "
                    f"found {owners}"
                )
            continue
        expected_subject_number = int(raw_subject_number)
        if owners != [expected_subject_number]:
            errors.append(
                f"Identity reference {label} must bind only to "
                f"<Subject {expected_subject_number}>; found subject bindings {owners}"
            )
    return errors


def validate_h3_prompt_contract(
    prompt: str,
    dialogue_beats: Sequence[Any] | None = None,
    *,
    mode: str = "t2va",
    references: Sequence[Mapping[str, Any]] | None = None,
    subjects: Sequence[Any] | None = None,
    context_anchors: Sequence[str] | None = None,
    duration_seconds: float = 0.0,
    audio_policy: Mapping[str, Any] | None = None,
    dialogue_manifest_authoritative: bool = False,
    has_driving_audio: bool = False,
    project_context: str = "",
) -> list[str]:
    """Validate the official field order plus Maestro's exact dialogue data."""

    text = str(prompt or "")
    mode = str(mode or "t2va").strip().lower()
    expected = _H3_REF2VA_FIELDS if mode == "ref2va" else _H3_BASE_FIELDS
    errors = validate_h3_vocal_contract(text, dialogue_beats)
    has_driving_audio = has_driving_audio or any(
        str(_field(reference, "type", "")).strip().casefold() == "audio"
        and str(_field(reference, "audio_intent", "voice") or "voice")
        .strip().casefold() == "drive"
        for reference in references or []
    )
    errors.extend(_audio_policy_violations(
        text,
        audio_policy,
        dialogue_beats=dialogue_beats,
        dialogue_manifest_authoritative=dialogue_manifest_authoritative,
        has_driving_audio=has_driving_audio,
        # The brief travels with every shot; its film-wide rules are not this
        # shot's own speech request.
        ignore_regions=[_h3_injected_project_context(project_context)]
        if project_context else None,
    ))
    positions: list[int] = []
    for field in expected:
        matches = list(re.finditer(
            rf"(?mi)^\s*{re.escape(field)}\s*:", text,
        ))
        if len(matches) != 1:
            # Where they are, so the refusal is actionable to the correction assistant.
            where = ""
            if len(matches) > 1:
                lines = ", ".join(
                    str(text[: match.start()].count("\n") + 1)
                    for match in matches[:6]
                )
                where = f" (lines {lines})"
            errors.append(f"expected one {field} field, found {len(matches)}{where}")
        elif matches:
            positions.append(matches[0].start())
    if len(positions) == len(expected) and positions != sorted(positions):
        errors.append("Context-IR fields are out of order")
    unexpected = set(_H3_ALL_FIELDS) - set(expected)
    for field in unexpected:
        if re.search(rf"(?mi)^\s*{re.escape(field)}\s*:", text):
            errors.append(f"unexpected {field} field for {mode}")
    extracted_fields = _extract_h3_fields(text)
    visual_field = "detailed_description" if mode == "ref2va" else "integrated_multimodal_description"
    visual = extracted_fields.get(visual_field, "")
    errors.extend(_expression.performance_expression_problems(text, visual, references, subjects))
    # Reference bindings mention an existing shot (for example, "throughout
    # [Shot 1]"); those mentions do not introduce a camera cut. Legacy inline
    # Context-IR can also repeat the current marker before more action prose.
    # Collapse that continuation, while rejecting skipped/global shot indices
    # or a return to an earlier shot after a later cut.
    shot_numbers: list[int] = []
    visual_markers = _h3_visual_shot_markers(visual)
    for marker in visual_markers:
        number = int(marker.group("number"))
        if not shot_numbers or number != shot_numbers[-1]:
            shot_numbers.append(number)
    if shot_numbers and shot_numbers != list(range(1, len(shot_numbers) + 1)):
        errors.append("visual shot numbers must be consecutive within this clip, starting at [Shot 1]")
    if duration_seconds > 0:
        from .prompt_timing import music_video_visual_timing_issue
        timing_issue = music_video_visual_timing_issue(visual, duration_seconds)
        if timing_issue:
            errors.append(timing_issue)
    for field in ("overall_soundscape", "non_diegetic_music"):
        if re.search(r"\[Shot\s+\d+\]", extracted_fields.get(field, ""), re.I):
            errors.append(f"{field} contains visual shot instructions")
    if mode == "ref2va":
        if not shot_numbers or shot_numbers[0] != 1:
            errors.append("detailed_description is missing [Shot 1]")
        elif not visual[:visual_markers[0].start()].strip():
            errors.append(
                "detailed_description is missing the visual-style opening before [Shot 1]"
            )
    elif not re.match(r"^\s*\[Shot\s+1\]", visual, flags=re.IGNORECASE):
        errors.append(f"{visual_field} does not begin with [Shot 1]")
    if re.search(
        r"\b(?:PROJECT CONTINUITY|OPENING CONTINUITY|FINAL BLOCKING|"
        r"DIALOGUE AND VOCAL PERFORMANCE|SILENCE AND VOCAL PERFORMANCE)\s*:",
        text,
        flags=re.IGNORECASE,
    ):
        errors.append("legacy Maestro prompt wrapper remains in the H3 payload")
    if any(0x80 <= ord(character) <= 0x9F for character in text):
        errors.append("prompt contains an orphaned C1 control character")
    required_anchors = list(context_anchors or [])
    for subject in subjects or []:
        name = (
            _h3_identity_label(_field(subject, "speaker_name", ""))
            or _h3_identity_label(_field(subject, "character_id", ""))
        )
        folded = name.casefold()
        if (
            name
            and folded not in _H3_GENERIC_IDENTITY_LABELS
            and not _h3_is_placeholder_identity(name)
            and not re.fullmatch(r"(?:char|subject|speaker)[_-]?\d+", folded)
        ):
            required_anchors.append(name)
    for anchor in _h3_context_anchors(required_anchors):
        if not _h3_anchor_present(anchor, text):
            errors.append(f"missing canonical identity/world context: {anchor}")
    if mode == "i2va" and not text.startswith(
        "For the target video, at 0.00 seconds into the target video, <Picture 1>"
    ):
        errors.append("I2VA prompt is missing the official 0.00-second alignment line")
    if mode == "fl2va" and not text.startswith(
        "How the reference pictures align with the target video"
    ):
        errors.append("FL2VA prompt is missing the official two-picture alignment line")
    if mode == "l2va" and not text.startswith(
        "How the reference pictures align with the target video — <Picture 1>"
    ):
        errors.append("L2VA prompt is missing the official last-picture alignment line")
    if mode == "t2va" and text.startswith((
        "For the target video,", "How the reference pictures align",
    )):
        errors.append("T2VA prompt must not contain a picture-alignment header")
    if mode == "ref2va":
        summary = extracted_fields.get("summary", "")
        if not re.match(
            r"^\[(?:reference generation|video editing|video continuation|"
            r"keyframe completion|audio reuse|audio reference)(?:\s+\+\s+"
            r"(?:reference generation|video editing|video continuation|"
            r"keyframe completion|audio reuse|audio reference))*\]\s+\S",
            summary,
            flags=re.IGNORECASE,
        ):
            errors.append("Ref2VA summary is missing its official task-type prefix")

        definitions_text = extracted_fields.get("subject_definitions", "")
        definitions = list(re.finditer(r"<Subject\s+(\d+)>\s*(?:\(S\d+\)\s*)?is\s+", definitions_text, re.I))
        numbers = [int(match[1]) for match in definitions]
        if len(numbers) != len(set(numbers)):
            errors.append("Ref2VA defines the same subject number more than once")
        # The brief names the film's whole cast; a close-up declares only who is
        # on screen. Reading its Subject labels as this shot's action refused a
        # one-person shot because the brief also mentioned <Subject 2>.
        action_text = visual
        brief_prose = _h3_injected_project_context(project_context).strip()
        if len(brief_prose) >= 24:
            action_text = visual.replace(brief_prose, " " * len(brief_prose))
        undefined = set(int(value) for value in re.findall(r"<Subject\s+(\d+)>", action_text, re.I)) - set(numbers)
        if undefined:
            errors.append("Ref2VA visual action uses undefined subjects: " + ", ".join(str(number) for number in sorted(undefined)))
        picture_no = 0
        expected_picture_bindings: dict[int, int | None] = {}
        for reference in references or []:
            if str(reference.get("type") or "").lower() != "image":
                continue
            picture_no += 1
            if str(reference.get("image_intent") or "identity").lower() != "identity":
                continue
            try:
                mapped = _mapped_ref2va_subject(str(reference.get("role") or ""), subjects or [])
            except H3DialogueContractError as exc:
                errors.append(str(exc))
                continue
            if mapped is None:
                expected_picture_bindings[picture_no] = None
            else:
                # The planner's slot, not the row's list position. A one-person
                # S2 shot stores the man at position 1 but defines <Subject 2>,
                # and expecting <Subject 1> rejected the correct prompt.
                subject = (subjects or [])[mapped]
                slot, _ = _planner_subject_slot(subject, mapped + 1)
                expected_picture_bindings[picture_no] = slot
        errors.extend(validate_final_h3_subject_bindings(
            text,
            expected_picture_bindings,
        ))

        if references:
            expected_labels: list[str] = []
            counts = {"image": 0, "video": 0, "audio": 0}
            for reference in references:
                kind = str(reference.get("type") or "").strip().lower()
                if kind not in counts:
                    continue
                counts[kind] += 1
                noun = {"image": "Picture", "video": "Video", "audio": "Audio"}[kind]
                expected_labels.append(f"<{noun} {counts[kind]}>")
            missing_labels = [label for label in expected_labels if label not in text]
            if missing_labels:
                errors.append(
                    "Ref2VA prompt does not map supplied references: "
                    + ", ".join(missing_labels)
                )

            retention_text = extracted_fields.get("retention_analysis", "")
            if counts["image"] + counts["video"] and not re.search(
                r":\s*(?:fully_preserved|partially_preserved|"
                r"attribute_transfer|weak_reference)\s+-",
                retention_text,
            ):
                errors.append("Ref2VA visual retention uses no official marker")
            if counts["audio"] and not re.search(
                r":\s*(?:fully_copy|partially_copy|reference|weak_reference)\s+-",
                retention_text,
            ):
                errors.append("Ref2VA audio retention uses no official marker")
    return list(dict.fromkeys(errors))


def _plan_audio_start_seconds(plan: Mapping[str, Any]) -> float | None:
    """Read a clip's real audio window start from its saved plan."""

    nested = plan.get("planned_clip")
    if isinstance(nested, Mapping):
        value = nested.get("start")
        if isinstance(value, (int, float)):
            return float(value)
    for key in ("_director_audio_start_seconds", "_director_audio_start"):
        value = plan.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _drop_silent_dialogue_beats(
    clip_plans: Sequence[MutableMapping[str, Any]],
) -> int:
    """Remove beats that carry no spoken words, in place.

    A planner legitimately emits "no speech here" beats, either as a blank row
    or as a bracketed marker such as ``<d>[silent]</d>``. Both used to reach the
    dialogue contract, which read the marker as a language tag, found no words
    left, and aborted the entire render. Dropping the beat is the correct
    outcome: the clip simply has one fewer line.
    """

    dropped = 0
    for plan in clip_plans or []:
        if not isinstance(plan, MutableMapping):
            continue
        for key in ("_director_dialogue_beats", "dialogue_beats"):
            beats = plan.get(key)
            if not isinstance(beats, list):
                continue
            kept = [
                beat for beat in beats
                if not (
                    isinstance(beat, Mapping)
                    and is_silent_dialogue(_field(beat, "spoken_text", ""))
                )
            ]
            if len(kept) != len(beats):
                dropped += len(beats) - len(kept)
                plan[key] = kept
    if dropped:
        print(
            "[MiniMax H3] Dropped "
            f"{dropped} dialogue beat(s) that carried no spoken words "
            "(blank or a no-speech marker such as [silent])."
        )
    return dropped


def _split_multi_speaker_beats(
    clip_plans: Sequence[MutableMapping[str, Any]],
) -> int:
    """Split beats that packed several speakers into one line, in place.

    A planner sometimes fuses a whole exchange into a single beat:
    ``"...da miedo. (S2): Esa sombrosa. (S2): Porque a nivel abstracto..."``.
    One beat becomes one ``<d>`` block, and H3 gives a block exactly one voice,
    so every fused line inherited the first speaker's voice. Each labelled
    segment is a real turn, so emit one beat per segment and keep its own label.
    """

    split_total = 0
    for plan in clip_plans or []:
        if not isinstance(plan, MutableMapping):
            continue
        beats = plan.get("_director_dialogue_beats")
        if not isinstance(beats, list) or not beats:
            continue
        rebuilt: list[Any] = []
        for beat in beats:
            spoken = (
                _field(beat, "spoken_text", "") if isinstance(beat, Mapping) else ""
            )
            markers = list(_INLINE_SPEAKER_LABEL_RE.finditer(str(spoken or "")))
            # One leading label is the normal case and is handled later; only a
            # second label, or one sitting mid-line, proves a fused exchange.
            if len(markers) < 2 and not (
                markers and markers[0].start() > 0
            ):
                rebuilt.append(beat)
                continue
            segments: list[tuple[str, str]] = []
            head = str(spoken)[: markers[0].start()].strip()
            # In "<d>(S1): hola. (S2): adios." the text before the first label
            # is just the opening tag. Emitting it produced a beat with no
            # words at all, which then failed the dialogue contract.
            if head and not is_silent_dialogue(head):
                existing = re.fullmatch(
                    r"\s*\(?\s*S(\d+)\s*\)?\s*",
                    str(_field(beat, "speaker_id", "") or ""),
                    re.IGNORECASE,
                )
                beat_label = (
                    f"(S{int(existing.group(1))})" if existing
                    else f"(S{markers[0].group(1)})"
                )
                segments.append((beat_label, head))
            for position, marker in enumerate(markers):
                end = (
                    markers[position + 1].start()
                    if position + 1 < len(markers)
                    else len(str(spoken))
                )
                text = str(spoken)[marker.end():end].strip()
                if text and not is_silent_dialogue(text):
                    segments.append((f"(S{marker.group(1)})", text))
            if len(segments) < 2:
                rebuilt.append(beat)
                continue
            for position, (label, text) in enumerate(segments):
                clone = dict(beat) if isinstance(beat, Mapping) else {}
                clone["spoken_text"] = text
                clone["speaker_id"] = label
                if position:
                    # ``delivery`` and ``physical_cue`` described the fused
                    # line, so they cannot be reused for a later turn.
                    clone["delivery"] = ""
                    clone["physical_cue"] = ""
                rebuilt.append(clone)
            split_total += len(segments) - 1
        plan["_director_dialogue_beats"] = rebuilt
    if split_total:
        print(
            "[MiniMax H3] Split "
            f"{split_total} extra dialogue beat(s) out of lines that packed "
            "several speakers into one block."
        )
    return split_total
def _retime_h3_visual_timecodes(
    prompt: str,
    authored_duration: Any,
    native_duration: Any,
) -> str:
    """Scale visual-local timecodes when native fitting changes clip length.

    The planner's cue is first validated against its authored duration. Only
    eligible visual ``At MM:SS`` cues are then scaled; dialogue and explicitly
    labeled source-audio timing stay byte-for-byte intact.
    """

    from . import prompt_timing

    try:
        authored_seconds = float(authored_duration)
        native_seconds = float(native_duration)
    except (TypeError, ValueError, OverflowError):
        return prompt
    if (
        not math.isfinite(authored_seconds)
        or authored_seconds <= 0
        or not math.isfinite(native_seconds)
        or native_seconds <= 0
    ):
        return prompt

    authored_issue = prompt_timing.music_video_visual_timing_issue(
        prompt, authored_seconds
    )
    if authored_issue:
        raise H3DialogueContractError(
            f"authored visual timing is invalid: {authored_issue}"
        )
    if math.isclose(authored_seconds, native_seconds, rel_tol=0.0, abs_tol=1e-9):
        return prompt

    masked_prompt = prompt_timing._mask_dialogue_tags(prompt)
    ratio = native_seconds / authored_seconds
    replacements: list[tuple[int, int, str]] = []
    for match in prompt_timing._AT_TIME_RE.finditer(masked_prompt):
        if prompt_timing._is_source_timing_reference(
            masked_prompt, match.start()
        ):
            continue
        token = match.group("value")
        timecode = token.strip("([{<").rstrip(".,:)]}>")
        if not prompt_timing._TIME_LIKE_RE.match(timecode):
            continue
        parsed = prompt_timing._LOCAL_TIMECODE_RE.fullmatch(timecode)
        # The authored-duration validator above rejects malformed candidates.
        if not parsed:
            continue
        seconds_field = int(parsed.group("seconds"))
        fraction = parsed.group("fraction") or ""
        visual_seconds = (
            int(parsed.group("minutes")) * 60
            + seconds_field
            + (
                int(fraction) / (10 ** len(fraction))
                if fraction else 0.0
            )
        )
        prefix_length = len(token) - len(token.lstrip("([{<"))
        suffix_length = len(token) - len(token.rstrip(".,:)]}>"))
        suffix = token[len(token) - suffix_length:] if suffix_length else ""
        replacement = (
            token[:prefix_length]
            + prompt_timing._format_local_timecode(visual_seconds * ratio)
            + suffix
        )
        replacements.append((*match.span("value"), replacement))

    if not replacements:
        return prompt
    retimed = prompt
    for start, end, replacement in reversed(replacements):
        retimed = retimed[:start] + replacement + retimed[end:]
    return retimed


def _strip_planner_subject_labels(plan: MutableMapping[str, Any]) -> None:
    """Remove ``<Subject N>`` tags the planner wrote from everything it authored.

    Maestro writes the ``<Subject N>`` scaffolding itself, from the identities it can
    define for this shot. Measured on a real plan: the writer labelled the black door
    ``<Subject 7> (Sx)`` in ``ending_beat`` and in a dialogue beat's ``physical_cue``,
    while that shot's own rows defined only Subjects 1 and 2 — the compile then failed
    with "Shot 11: Ref2VA visual action uses undefined subjects: 7". Only descriptions
    were being cleaned; every other authored field carried the tag straight into the
    body. A speaker cue such as ``(S1)`` is kept: it is how the compiler recovers which
    participant a row is.
    """

    if not isinstance(plan, dict):
        return

    def clean(value: Any) -> Any:
        if isinstance(value, str):
            text = _clean_malformed_subject_tags(value)
            text = _ANY_SUBJECT_TAG_RE.sub(" ", text)
            # "(Sx)" is the planner's placeholder for a label it never resolved.
            return _PLACEHOLDER_SPEAKER_CUE_RE.sub(" ", text)
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        return value

    for key in (
        "spatial_setup",
        "environment",
        "opening_blocking",
        "closing_blocking",
        "ending_beat",
        "action_beats",
        "subjects_on_screen",
        "dialogue_beats",
        "_director_dialogue_beats",
    ):
        if key in plan:
            plan[key] = clean(plan[key])

    # The prompt fields are only the planner's own text on the first compile; after
    # that they hold a compiled Context-IR block whose <Subject N> definitions are
    # exactly what must be preserved, so a compiled body is left untouched.
    for key in ("video_prompt", "_director_h3_source_prompt"):
        text = plan.get(key)
        if isinstance(text, str) and "subject_definitions:" not in text:
            plan[key] = clean(text)


_PLACEHOLDER_SPEAKER_CUE_RE = re.compile(r"\(\s*S\s*[xX\u2026?.]{1,3}\s*\)")


def compile_h3_clip_plans(
    clip_plans: Sequence[MutableMapping[str, Any]],
    *,
    prompt_modes: Sequence[str] | None = None,
    durations: Sequence[float] | None = None,
    reference_manifests: Sequence[Sequence[Mapping[str, Any]]] | None = None,
    supplied_track: bool = False,
) -> Sequence[MutableMapping[str, Any]]:
    """Compile every saved/renderable H3 clip immediately before queuing.

    ``_director_h3_source_prompt`` remains the immutable planner result. This
    lets generation recompile a prompt from T2VA to I2VA/FL2VA after the actual
    start/end files are known without nesting an alignment header or six-field
    Ref2VA wrapper around a previously compiled prompt.

    ``supplied_track`` says the project renders with the user's own soundtrack, so the
    track owns every voice. Without it, a clip whose own audio plan came through neutral
    was treated as a silent shot with no audio at all: its transcript beats were cleared
    and its description of a synced mouth was refused as a request for generated speech.
    """

    references_by_plan = [
        (
            reference_manifests[index]
            if reference_manifests is not None and index < len(reference_manifests)
            else plan.get("_director_h3_reference_manifest") or []
        )
        for index, plan in enumerate(clip_plans)
    ]

    # The compiler owns the <Subject N> labels; a copy the planner left in any other
    # field becomes an undefined subject and fails the whole plan.
    for plan in clip_plans:
        _strip_planner_subject_labels(plan)

    # Audio transcript beats remain useful identity and continuity metadata.
    # Track separately which beats are actual generated dialogue so prompt
    # compilation and final validation never treat a source transcript as a
    # second speech request.
    transcript_only = []
    for plan, references in zip(clip_plans, references_by_plan):
        audio_mode = _normalized_space(
            _field(plan.get("_director_audio_plan") or {}, "mode", "")
        ).casefold()
        has_driving_audio_reference = any(
            str(_field(reference, "type", "")).strip().casefold() == "audio"
            and str(_field(reference, "audio_intent", "voice") or "voice")
            .strip().casefold() == "drive"
            for reference in references or []
        )
        transcript_only.append(not _transcript.states_the_lines(audio_mode=audio_mode, audio_plan=plan.get("_director_audio_plan"), driving_audio_reference=has_driving_audio_reference))

    # A supplied track that owns the voice and a prompt that writes the same lines as
    # new speech are two instructions for the same mouth. Name it here: when the
    # rendered mouths do not match the track, this line says which configuration the
    # shot was compiled under.
    restated_over_track = [
        index + 1
        for index, plan in enumerate(clip_plans)
        if not transcript_only[index]
        and any(
            str(_field(reference, "type", "")).strip().casefold() == "audio"
            and str(_field(reference, "audio_intent", "voice") or "voice")
            .strip().casefold() == "drive"
            for reference in (references_by_plan[index] or [])
        )
    ]
    if restated_over_track:
        print(
            "[MiniMax H3] Shot(s) "
            + ", ".join(str(number) for number in restated_over_track[:8])
            + " write their lines as speech while a driving audio reference owns the "
            "voice. If a rendered mouth does not match that track, this is the shot to "
            "re-plan as a transcript-only performance."
        )

    for index, (plan, has_driving_audio) in enumerate(zip(clip_plans, transcript_only)):
        audio_policy = plan.get("_director_audio_policy")
        dialogue_manifest_authoritative = bool(
            plan.get("_director_dialogue_manifest_authoritative")
        )
        beats = plan.get("_director_dialogue_beats") or []
        source = str(
            plan.get("_director_h3_source_prompt") or plan.get("video_prompt") or ""
        )
        if plan.get("_director_prompt_user_edited"):
            violations = _audio_policy_violations(
                source,
                audio_policy,
                dialogue_beats=beats,
                dialogue_manifest_authoritative=dialogue_manifest_authoritative,
                has_driving_audio=has_driving_audio,
            )
            if violations:
                raise H3DialogueContractError(
                    f"Shot {index + 1}: Audio policy violation: "
                    + "; ".join(violations)
                )

        speech_forbidden = (
            _audio_policy_flag(audio_policy, "generated_speech_allowed") is False
            or (dialogue_manifest_authoritative and not _has_spoken_beats(beats))
        )
        if speech_forbidden and not has_driving_audio:
            # Empty authoritative source dialogue must not leave planner-added
            # beat metadata or lip-sync timing active in the saved clip plan.
            plan["_director_dialogue_beats"] = []
            audio_plan = plan.get("_director_audio_plan")
            if isinstance(audio_plan, MutableMapping):
                if _normalized_space(_field(audio_plan, "mode", "")).casefold() == "dialogue_driven":
                    audio_plan["mode"] = "ambient_only"
                audio_plan["timing_anchor"] = "video"
                audio_plan["lip_sync_critical"] = False
            # A voice-timbre reference primes H3 to speak even under this
            # contract. A silent shot must not carry one; the remaining image,
            # video and sound references keep their numbering and their bindings.
            references_by_plan[index] = [
                reference for reference in references_by_plan[index] or []
                if not (
                    str(_field(reference, "type", "")).strip().casefold() == "audio"
                    and str(_field(reference, "audio_intent", "voice") or "voice")
                    .strip().casefold() == "voice"
                )
            ]
        # Persist the list this shot actually uses: later validators and the
        # rerun/chat paths read the plan's manifest, and it must not promise a
        # reference the compiled prompt no longer maps.
        plan["_director_h3_reference_manifest"] = references_by_plan[index]

    for index, (plan, references) in enumerate(zip(clip_plans, references_by_plan)):
        source = str(plan.get("_director_h3_source_prompt") or plan.get("video_prompt") or "")
        current = str(plan.get("video_prompt") or "")
        previous = str(plan.get("_director_h3_compiled_prompt") or "")
        if source and previous and current != previous:
            source = current
        mode = (
            prompt_modes[index]
            if prompt_modes is not None and index < len(prompt_modes)
            else plan.get("_director_h3_prompt_mode") or plan.get("_director_h3_model_family") or "t2va"
        )
        repaired = (
            _collapse_ref2va_compiler_identity_duplicates(source, references)
            if str(mode).strip().casefold() == "ref2va" else source
        )
        if repaired != source:
            plan["_director_h3_source_prompt"] = repaired
            if current == source:
                plan["video_prompt"] = repaired
            plan["_director_h3_compiled_prompt"] = ""
        if plan.get("_director_prompt_user_edited"):
            source = str(plan.get("_director_h3_source_prompt") or plan.get("video_prompt") or "")
            if _extract_h3_fields(source).get("subject_definitions"):
                recovered, _definitions = _ref2va_cast_from_source(source, [])
                if recovered:
                    previous_subjects = plan.get("_director_subjects_on_screen") or []
                    for subject in recovered:
                        try:
                            previous = _mapped_ref2va_subject(subject.get("speaker_name", ""), previous_subjects)
                        except H3DialogueContractError:
                            previous = None
                        if previous is not None:
                            role = _field(previous_subjects[previous], "performance_role", "")
                            if role:
                                subject["performance_role"] = role
                    plan["_director_subjects_on_screen"] = recovered
                    plan["_director_opening_blocking"] = ""
                    plan["_director_closing_blocking"] = ""
                    plan.pop("_director_speaker_registry", None)

    _repair_h3_phantom_dialogue_subjects(clip_plans)
    _canonicalize_h3_project_subject_names(clip_plans)
    _split_multi_speaker_subject_rows(clip_plans)
    _merge_subject_rows_for_the_same_speaker(clip_plans)
    _drop_silent_dialogue_beats(clip_plans)
    _split_multi_speaker_beats(clip_plans)
    # The splitter can surface a wordless fragment it found between two labels.
    _drop_silent_dialogue_beats(clip_plans)
    registry = _build_stable_speaker_registry(clip_plans)
    # Detect the language once from the whole script. Per-shot detection
    # mis-tagged short lines such as "Exacto." as English.
    spoken_sample = " ".join(
        str(_field(beat, "spoken_text", ""))
        for entry in clip_plans
        for beat in (entry.get("_director_dialogue_beats") or [])
    )
    if not spoken_sample.strip():
        # Saving a prompt edit clears the beat cache on purpose, so a one-clip
        # rerun reaches this point with nothing to sample and the whole run
        # fell back to English, overwriting the language tag the user had
        # written into their own line. The prompt text carries the same
        # dialogue, so sample that instead.
        spoken_sample = " ".join(
            str(
                entry.get("_director_h3_source_prompt")
                or entry.get("video_prompt")
                or ""
            )
            for entry in clip_plans
        )
    project_language = _detect_dialogue_language(spoken_sample)
    for index, plan in enumerate(clip_plans):
        beats = plan.get("_director_dialogue_beats") or []
        for beat in beats:
            if isinstance(beat, MutableMapping) and "spoken_text" in beat:
                beat["spoken_text"] = normalize_h3_text(beat["spoken_text"])
            # Leaked reasoning in these fields is copied into the prompt, where
            # an embedded <d>...</d> fragment becomes a non-canonical block.
            # Clean them once here so the persisted plan stays safe on reruns.
            if isinstance(beat, MutableMapping):
                for key in ("delivery", "physical_cue"):
                    if key in beat:
                        beat[key] = _clean_h3_metadata(beat[key])

        current_prompt = str(plan.get("video_prompt", "") or "")
        last_compiled = str(plan.get("_director_h3_compiled_prompt", "") or "")
        explicit_source = plan.get("_director_h3_source_prompt")
        source_prompt = explicit_source
        if source_prompt and last_compiled and current_prompt != last_compiled:
            # Prompt review/editing happens after the first preflight. Treat a
            # changed compiled prompt as the user's new authoritative source.
            source_prompt = current_prompt
            plan["_director_h3_source_prompt"] = source_prompt
        if not source_prompt:
            source_prompt = current_prompt
            plan["_director_h3_source_prompt"] = source_prompt
        if explicit_source and looks_like_compiled_h3_prompt(str(explicit_source)):
            # The user reviewed a prompt this compiler produced and saved it
            # back. Rebuilding it wraps a SECOND six-field Context-IR prompt
            # around the first, so the dialogue and the DIALOGUE FORMAT block
            # were duplicated on every save -- one real shot reached 17,331
            # characters with its lines present twice, and the speaker of a
            # line could no longer be resolved ('MiniMax H3 Omni could not
            # determine which referenced character speaks ...'). Their text is
            # authoritative, so it is used as the prompt instead of as input.
            #
            # A shot repair collapses whitespace across the whole prompt, which
            # also deletes the boundaries between the six fields; the renderer
            # reads the cast from a line-anchored subject_definitions and refused
            # every line of a flattened prompt. The layout is restored here.
            prompt = restore_h3_field_layout(str(source_prompt))
            required = _h3_plan_context_anchors(plan)
            if required:
                anchored = _ensure_h3_prompt_anchors(prompt, required)
                if anchored != prompt:
                    # Say it where the log is read: this shot gains the identity and
                    # world line the compiler writes for every prompt it builds.
                    print(
                        f"[MiniMax H3] Shot {index + 1} reviewed prompt gained its "
                        "canonical identity/world anchor."
                    )
                    prompt = anchored
            plan["video_prompt"] = prompt
            plan["_director_h3_compiled_prompt"] = prompt
            validate_h3_prompt_contract(
                prompt,
                plan.get("_director_dialogue_beats") or [],
                mode=(
                    plan.get("_director_h3_prompt_mode")
                    or ("ref2va" if plan.get("_director_h3_model_family") == "ref2va" else "t2va")
                ),
                references=plan.get("_director_h3_reference_manifest") or [],
                subjects=plan.get("_director_subjects_on_screen") or [],
                context_anchors=_h3_plan_context_anchors(plan),
                project_context=plan.get("_director_project_context") or "",
            )
            continue
        mode = (
            prompt_modes[index]
            if prompt_modes is not None and index < len(prompt_modes)
            else plan.get("_director_h3_prompt_mode")
            or (
                "ref2va"
                if plan.get("_director_h3_model_family") == "ref2va"
                else "t2va"
            )
        )
        duration = (
            durations[index]
            if durations is not None and index < len(durations)
            else plan.get("_director_duration_sec") or 0.0
        )
        authored_duration = plan.get("_director_authored_duration_sec")
        if plan.get("_director_prompt_user_edited"):
            # Edited prompt text is authored against the current native clip
            # duration, so it must remain strict instead of inheriting the
            # planner's pre-fit timing scale.
            authored_duration = duration
        try:
            compile_source_prompt = (
                _retime_h3_visual_timecodes(
                    str(source_prompt), authored_duration, duration
                )
                if authored_duration is not None
                else str(source_prompt)
            )
        except H3DialogueContractError as error:
            raise H3DialogueContractError(
                f"Shot {index + 1}: {error}"
            ) from error
        references = references_by_plan[index]
        speech_beats = [] if transcript_only[index] else beats
        audio_policy = plan.get("_director_audio_policy")
        dialogue_manifest_authoritative = bool(
            plan.get("_director_dialogue_manifest_authoritative")
        )
        subjects = plan.get("_director_subjects_on_screen") or []
        subject_descriptions = (
            _h3_subjects_for_physical_state(
                subjects,
                plan.get("_director_physical_state"),
            )
            if str(mode).strip().casefold() == "ref2va"
            else subjects
        )
        context_anchors = _h3_plan_context_anchors(plan)
        plan["_director_required_context_anchors"] = context_anchors
        prompt, contract = compile_h3_official_prompt(
            compile_source_prompt,
            subject_descriptions or [],
            speech_beats,
            mode=mode,
            duration_seconds=duration,
            references=references,
            speaker_registry=registry,
            project_context=plan.get("_director_project_context", ""),
            context_anchors=context_anchors,
            # Reviewed prompt text is authoritative after a user edit; stale
            # planner metadata must not reimpose the earlier art direction.
            visual_style=(
                ""
                if plan.get("_director_prompt_user_edited")
                else plan.get("_director_visual_style", "")
            ),
            opening_blocking=plan.get("_director_opening_blocking", ""),
            closing_blocking=plan.get("_director_closing_blocking", ""),
            audio_plan=plan.get("_director_audio_plan") or {},
            # A prompt the user edited is authoritative for its own language
            # tags. Forcing the detected project language rewrote an explicit
            # <d>[Spanish] ...</d> into [English] on a Spanish project, so the
            # edit looked like it had been ignored.
            default_language=(
                None
                if plan.get("_director_prompt_user_edited")
                else project_language
            ),
            audio_start_seconds=_plan_audio_start_seconds(plan),
            audio_policy=audio_policy,
            dialogue_manifest_authoritative=dialogue_manifest_authoritative,
            strict_audio_policy=bool(plan.get("_director_prompt_user_edited")),
            driving_audio=supplied_track,
        )
        plan["video_prompt"] = prompt
        plan["_director_h3_compiled_prompt"] = prompt
        # A body declaring a later shot duplicates whoever is placed in it, which the
        # user only sees in the rendered clip. Say it where the log is read.
        declared_shots = _declared_shot_numbers(prompt)
        if any(number > 1 for number in declared_shots):
            print(
                f"[MiniMax H3] Shot {index + 1} declares {len(declared_shots)} shots "
                f"inside one clip ({', '.join(f'[Shot {number}]' for number in declared_shots)}). "
                "A Director clip is one continuous shot: whoever is placed in a later "
                "framing is rendered again, which is how a character ends up "
                "duplicated on screen."
            )
        plan["_director_vocal_contract"] = contract
        plan["_director_h3_prompt_mode"] = mode
        plan["_director_speaker_registry"] = registry
        # A driven shot's mouth follows the supplied track, so "lip sync
        # accurate" and "only the speaking person moves their lips" are this
        # shot's own direction, not a request for generated speech. The
        # compiler already treats these modes as driven; the validator has to
        # agree or it refuses every dialogue-free shot in a driven project.
        driven_mode = _normalized_space(
            _field(plan.get("_director_audio_plan") or {}, "mode", "")
        ).casefold() in {"audio_driven", "music_driven"}
        errors = validate_h3_prompt_contract(
            prompt,
            speech_beats,
            mode=mode,
            references=references,
            subjects=subject_descriptions or [],
            context_anchors=context_anchors,
            duration_seconds=duration,
            audio_policy=audio_policy,
            dialogue_manifest_authoritative=dialogue_manifest_authoritative,
            has_driving_audio=transcript_only[index] or driven_mode or supplied_track,
            project_context=plan.get("_director_project_context") or "",
        )
        if errors:
            from services.director.h3_prompt_shape import describe
            print(describe(_extract_h3_fields(prompt)))
            from services.enhancement_diagnostics import record_rejection
            record_rejection(errors, stage=f"Director H3 shot {index + 1}")
            raise H3DialogueContractError(
                f"Shot {index + 1}: " + "; ".join(errors)
            )
    return clip_plans
