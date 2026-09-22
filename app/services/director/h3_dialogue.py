"""Deterministic MiniMax H3 dialogue compilation and validation.

H3 treats ``<d>[Language] ...</d>`` blocks as an audio-generation contract.
These helpers keep that contract out of best-effort prompt rewriting: structured
Director dialogue is canonical, existing blocks are replaced as whole units,
and malformed or contradictory prompts are rejected before GPU generation.
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

from services.dialogue_timing import (
    DIALOGUE_MAX_WORDS_PER_SECOND,
    h3_dialogue_schedule,
)
from services.h3_prompt_budget import (
    H3_ENHANCED_TEXT_TOKEN_TARGET as _H3_DIRECTOR_TEXT_TOKEN_BUDGET,
    fit_h3_base_prompt,
    h3_prompt_token_count,
)
from services.text_integrity import repair_text


class H3DialogueContractError(ValueError):
    """Raised when an H3 prompt cannot be made safe for native speech."""


_H3_DIALOGUE_TOKEN_RE = re.compile(r"<\s*(/?)\s*d\s*>", re.IGNORECASE)
_H3_STRICT_DIALOGUE_RE = re.compile(
    r"<d>\s*\[([^\]\r\n]+)\]\s+(.+?)\s*</d>",
    re.IGNORECASE | re.DOTALL,
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
    r"\b(?:the\s+)?soundscape\s+(?:is\s+dominated\s+by|includes|contains|is)\s+"
    r"(.+?)(?=\s+(?:non-diegetic\s+music|the\s+final\s+beat|"
    r"closing\s+blocking|opening\s+continuity|final\s+blocking)\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_H3_MUSIC_PROSE_RE = re.compile(
    r"\bnon-diegetic\s+music\s+is\s+(.+?)"
    r"(?=\s+(?:the\s+final\s+beat|closing\s+blocking|"
    r"opening\s+continuity|final\s+blocking)\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_H3_AUXILIARY_SECTION_BOUNDARY_RE = re.compile(
    r"\b(?:integrated_multimodal_description|detailed_description|"
    r"overall_soundscape|non_diegetic_music|dialogue\s+beats?)\s*:",
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
# Any tag-shaped token still inside a spoken line is corruption.
_STRAY_TAG_RE = re.compile(r"</?\s*[A-Za-z][^<>\r\n]{0,24}/?\s*>")
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

    An explicit ``default_language`` wins over a tag the planner wrote into the
    line: the planner hard-codes ``[English]`` even for Spanish projects, so its
    own tag is not trustworthy once the project language is known.
    """

    text = _H3_DIALOGUE_TOKEN_RE.sub("", str(value or "")).strip()
    language = ""
    language_prefix = re.match(r"^\[([^\]\r\n]+)\]\s*(.*)$", text, re.DOTALL)
    if language_prefix:
        language = language_prefix.group(1).strip()
        text = language_prefix.group(2).strip()
    text = _strip_speaker_prefixes(text)
    text = _strip_foreign_script_noise(text)
    if not text:
        raise H3DialogueContractError("MiniMax H3 dialogue contains an empty line.")
    resolved = (
        _normalized_space(default_language)
        or _normalized_space(language)
        # Neither the caller nor the line named a language. Hard-coding English
        # here mistagged Spanish lines the moment nothing else had decided --
        # which is what a hand-edited prompt does, because saving an edit
        # clears the beat cache on purpose. Read the line itself instead.
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
        speaker_name = _normalized_space(
            _field(subject, "speaker_name", "")
            or _field(subject, "visual_description", "")
            or character_id
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

        prompt = _append_canonical_dialogue_blocks(
            prompt, dialogue_beats, default_language,
        )
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


# The project context is injected to constrain the *audio*, so the section that
# states which subject owns which voice is worth more than any other prose in
# it. A 360-character budget fitted the short contexts this was first written
# for, but a structured project context (project header, subject definitions,
# gender lock, audio-driven rules, setting, dialogue format) is ~2.6k
# characters, and packing it into 360 kept little more than two headings. MiniMax
# H3 publishes no prompt-token limit and ``services.h3_prompt_budget`` says the
# token target is cosmetic, so the budget can be generous and reserve the
# section-selection path for genuinely oversized contexts.
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
    "the woman", "visible speaker", "woman",
}


def _h3_anchor_key(value: Any) -> str:
    return re.sub(
        r"[^a-z0-9]+",
        " ",
        normalize_h3_text(value).casefold(),
    ).strip()


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


def _declared_shot_numbers(body: str) -> list[int]:
    """Shot numbers a Context-IR body declares, in order of appearance."""

    return [
        int(value)
        for value in _H3_BRACKET_SHOT_RE.findall(str(body or ""))
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
        if seen:
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


def h3_dialogue_blocks(prompt: str) -> list[str]:
    """The spoken lines of a compiled prompt, in order, byte for byte."""

    return _H3_SPOKEN_BLOCK_RE.findall(str(prompt or ""))


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
        findings.append(f"The body declares {len(shots)} shot(s): {listed}.")
        if any(number > 1 for number in shots):
            findings.append(
                "A Director clip is ONE continuous shot, so the model performs "
                "each declared shot inside the same clip: anyone placed in a "
                "later framing is rendered again."
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

    # Lips turn is decided by the plan, not by the prompt: the orchestrator picks
    # the audio-to-video path only for an audio/dialogue-driven shot marked
    # lip-sync critical. Three notes on clip 13 of a real project -- "the woman
    # must faithfully lip-sync S1" -- changed 2, 55 and 1 characters and could
    # never work, because the clip's plan said "ambient_only". Without this
    # finding the assistant blamed pronouns and gestures instead.
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


def review_h3_revision(
    original: str,
    revised: str,
    *,
    duration_seconds: float = 0.0,
    subjects: Sequence[Any] | None = None,
    mode: str = "ref2va",
    references: Sequence[Mapping[str, Any]] | None = None,
    context_anchors: Sequence[str] | None = None,
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

    problems.extend(
        validate_h3_prompt_contract(
            text,
            [],
            mode=mode,
            references=references,
            subjects=subjects,
            context_anchors=context_anchors,
        )
    )
    return problems


def _source_prompt_parts(
    prompt: str,
    *,
    project_context: str = "",
    context_anchors: Sequence[str] | None = None,
    opening_blocking: str = "",
    closing_blocking: str = "",
    audio_plan: Mapping[str, Any] | None = None,
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

    context = _normalized_space(
        pack_project_context(normalize_h3_text(project_context)),
    )
    # The single-shot guard is decided here but is NOT conditional on the
    # context: it used to share this branch, so every shot whose body looked
    # like it already carried the context lost the guard with it. 113 of a real
    # project's 177 shots were left free to cut and fade inside a single clip.
    scope = "" if multi_shot_source else f"{_H3_SINGLE_SHOT_SCOPE} "
    if context and not _meaningful_context_present(context, body):
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
            f"{scope}Project context (the whole film; do not perform its "
            f"progression or its closing fade inside this shot): {context}. {body}"
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

    plan = audio_plan if isinstance(audio_plan, Mapping) else {}
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
    return stable_id, name


def _subject_name_for_key(subjects: Sequence[Any], key: str) -> str:
    folded = key.casefold()
    for subject in subjects or []:
        character_id = _normalized_space(_field(subject, "character_id", ""))
        speaker_name = _normalized_space(_field(subject, "speaker_name", ""))
        if folded in {character_id.casefold(), speaker_name.casefold()}:
            return speaker_name or character_id
    return key or "the visible speaker"


def _set_h3_subject_field(subject: Any, key: str, value: Any) -> None:
    if isinstance(subject, MutableMapping):
        subject[key] = value
    elif hasattr(subject, key):
        setattr(subject, key, value)


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
    if candidate.casefold() in _H3_GENERIC_IDENTITY_LABELS:
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

    name = _normalized_space(_field(subject, "speaker_name", ""))
    inferred = _leading_h3_identity(
        _field(subject, "visual_description", "")
    )
    if inferred and name and not _h3_identity_names_match(inferred, name):
        return inferred, [inferred]
    candidates = [
        candidate for candidate in (name, inferred)
        if candidate
        and candidate.casefold() not in _H3_GENERIC_IDENTITY_LABELS
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
                phantom.get("speaker_name", "") or phantom_id
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
                    candidate_name = _normalized_space(
                        candidate.get("speaker_name", "") or candidate_id
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
                phantom.get("speaker_name", "") or phantom_id
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
                    or candidate.get("speaker_name", ""),
                    target_name,
                )
            ), None)
            if target is None:
                continue
            target_id = _normalized_space(target.get("character_id", ""))
            canonical_name = _normalized_space(
                target.get("speaker_name", "") or target_name
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
            if not character_id:
                continue
            original_key = character_id.casefold()
            identity, candidates = _h3_subject_identity_evidence(subject)
            if not identity:
                identity = character_id
                candidates = [character_id]
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
        rf"{escaped}\b|\b{escaped}(?:es|s)?\b[^.!?]{{0,45}}\b"
        r"(?:twins?|clones?|copies|duplicates?|multiple\s+versions?)\b",
        text,
        flags=re.IGNORECASE,
    ))


def _h3_plan_context_anchors(plan: Mapping[str, Any]) -> list[str]:
    anchors: list[str] = []
    for subject in plan.get("_director_subjects_on_screen") or []:
        name = _normalized_space(
            _field(subject, "speaker_name", "")
            or _field(subject, "character_id", "")
        )
        folded = name.casefold()
        if (
            name
            and folded not in _H3_GENERIC_IDENTITY_LABELS
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


def _split_multi_speaker_subject_rows(
    clip_plans: Sequence[MutableMapping[str, Any]],
) -> None:
    """One row per person when the planner packed two into a single row.

    A real row read ``"Valeria (S1), brunette, navy blazer / Ricardo (S2),
    mature man in grey sweater & glasses."`` -- two people carrying ONE label.
    Left alone, the compiler emitted a duplicate entry for the first speaker and
    then minted a fresh ``<Subject 3>`` to bind the second person's identity
    picture, so a project with two participants rendered a third character.
    Nothing in the planner's own output named three people.
    """

    for plan in clip_plans:
        rows = plan.get("_director_subjects_on_screen")
        if not isinstance(rows, list) or not rows:
            continue
        rebuilt: list[Any] = []
        for row in rows:
            if not isinstance(row, MutableMapping):
                rebuilt.append(row)
                continue
            text = str(_field(row, "visual_description", "") or "")
            if len(_H3_SPEAKER_TOKEN_RE.findall(text)) < 2:
                rebuilt.append(row)
                continue
            fragments = [
                fragment.strip()
                for fragment in re.split(r"\s*(?:/|;|\||\n|\u2022)\s*", text)
                if fragment.strip()
            ]
            # A fragment with no label of its own belongs to the person named
            # before the separator, so it is merged back into their row.
            grouped: list[str] = []
            for fragment in fragments:
                if not grouped or _H3_SPEAKER_TOKEN_RE.search(fragment):
                    grouped.append(fragment)
                else:
                    grouped[-1] = f"{grouped[-1]}, {fragment}"
            labelled = [
                fragment for fragment in grouped
                if _H3_SPEAKER_TOKEN_RE.search(fragment)
            ]
            if len(labelled) < 2:
                rebuilt.append(row)
                continue
            for fragment in grouped:
                clone = dict(row)
                clone["visual_description"] = fragment
                rebuilt.append(clone)
        plan["_director_subjects_on_screen"] = rebuilt


def _merge_subject_rows_for_the_same_speaker(
    clip_plans: Sequence[MutableMapping[str, Any]],
) -> None:
    """One row per person when the planner described the same one twice.

    Clip 25 listed ``"Valeria (S1), brunette, navy blazer"`` and then a second
    row ``"Valeria (S1)"``. Two rows for one label became two subject slots, and
    the spare one was emitted as ``<Subject 3> (S1): Valeria`` -- a third
    character for a project with two participants. Rows are keyed by the label
    they carry, because that is what identifies the person.
    """

    for plan in clip_plans:
        rows = plan.get("_director_subjects_on_screen")
        if not isinstance(rows, list) or len(rows) < 2:
            continue
        merged: list[Any] = []
        seen: dict[str, int] = {}
        for row in rows:
            if not isinstance(row, MutableMapping):
                merged.append(row)
                continue
            token = _H3_SPEAKER_TOKEN_RE.search(
                str(_field(row, "visual_description", "") or "")
            )
            key = token.group(1).casefold() if token else ""
            if not key or key not in seen:
                if key:
                    seen[key] = len(merged)
                merged.append(row)
                continue
            # Keep the fuller description; a restated stub adds no detail.
            previous = merged[seen[key]]
            current_text = str(_field(row, "visual_description", "") or "")
            previous_text = str(_field(previous, "visual_description", "") or "")
            if len(current_text) > len(previous_text):
                merged[seen[key]] = row
        plan["_director_subjects_on_screen"] = merged


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
    """Assign speaker IDs once per Director project, not once per shot."""

    registry: dict[str, dict[str, str]] = {}
    used_numbers: set[int] = set()
    for plan in clip_plans:
        existing = plan.get("_director_speaker_registry") or {}
        if not isinstance(existing, Mapping):
            continue
        for raw_key, raw_value in existing.items():
            key = _normalized_space(raw_key).casefold()
            entry = _speaker_registry_entry(existing, str(raw_key))
            if not key or not entry or not entry[0]:
                continue
            match = re.fullmatch(r"\(S(\d+)\)", entry[0], re.IGNORECASE)
            if not match:
                continue
            used_numbers.add(int(match.group(1)))
            registry[key] = {
                "stable_id": f"(S{int(match.group(1))})",
                "speaker_name": entry[1],
            }

    next_number = 1
    for plan in clip_plans:
        subjects = plan.get("_director_subjects_on_screen") or []
        for beat in plan.get("_director_dialogue_beats") or []:
            raw_key = _normalized_space(_field(beat, "speaker_id", ""))
            name = _subject_name_for_key(subjects, raw_key)
            key = (raw_key or name).casefold()
            if not key or key in registry:
                continue
            # A beat's speaker_id is often the character's identity id
            # ("director_identity_valeria") rather than a label, and the person
            # already has a label in their own subject row. Resolve through that
            # instead of minting a number: minting one per identity id gave every
            # participant a second name, so a project whose audio analysis
            # detected two speakers compiled prompts containing S1..S5 and
            # <Subject 3>/<Subject 4> -- a phantom cast the planner never wrote.
            known_label = _speaker_label_for_identity(subjects, key)
            if known_label:
                registry[key] = {"stable_id": known_label, "speaker_name": name}
                continue
            # Before minting anything: a key that already spells out a label is
            # that participant, not a new one. This runs after the subject-row
            # lookup so a row's own label always wins.
            encoded = _label_encoded_in_key(raw_key or name)
            if encoded:
                registry[key] = {"stable_id": encoded, "speaker_name": name}
                continue
            while next_number in used_numbers:
                next_number += 1
            registry[key] = {
                "stable_id": f"(S{next_number})",
                "speaker_name": name,
            }
            used_numbers.add(next_number)
            next_number += 1
    # Existing saved projects may carry a registry compiled before a later
    # shot supplied the character's complete canonical name. The project-wide
    # subject ledger above is authoritative for labels while stable IDs remain.
    for plan in clip_plans:
        for subject in plan.get("_director_subjects_on_screen") or []:
            character_id = _normalized_space(_field(subject, "character_id", ""))
            speaker_name = _normalized_space(_field(subject, "speaker_name", ""))
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
        speaker_name = _normalized_space(
            _field(subject, "speaker_name", "") or character_id
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
        name = _normalized_space(_field(subject, "speaker_name", ""))
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
) -> tuple[str, str]:
    """Place exact tagged lines and stable speaker IDs in the visual field."""

    valid_beats: list[dict[str, str]] = []
    if not default_language:
        default_language = _detect_dialogue_language(
            " ".join(
                str(_field(beat, "spoken_text", ""))
                for beat in (dialogue_beats or [])
            ) or body,
        )
    for beat in dialogue_beats or []:
        spoken = normalize_h3_text(_field(beat, "spoken_text", ""))
        # A no-speech marker is a valid planning outcome, not a broken line.
        if is_silent_dialogue(spoken):
            continue
        _, words = _dialogue_payload(spoken, default_language)
        speaker_key = _normalized_space(_field(beat, "speaker_id", ""))
        entry = _speaker_registry_entry(registry, speaker_key)
        if entry:
            stable_id, speaker_name = entry
        else:
            speaker_name = _subject_name_for_key(subjects, speaker_key)
            stable_id = f"(S{len(valid_beats) + 1})"
        valid_beats.append({
            "words": normalize_h3_text(words),
            "tag": h3_dialogue_tag(spoken, default_language),
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
    if has_driving_audio and not valid_beats:
        # The mapped audio is authoritative. LLM-authored vocal tags are both
        # unnecessary and actively harmful here: they can compete with the
        # soundtrack or become unbalanced when a repetitive transcript causes
        # output truncation. Structured dialogue remains authoritative for
        # narrative shots and follows the stricter validation path below.
        body = _strip_dialogue_for_driving_audio(body)
        existing_blocks = []
    spans, malformed = _dialogue_spans(body)
    if malformed:
        # Prompt review, a truncated planner answer, or a hand-edited prompt
        # can leave nested or unterminated tags. Salvage the spoken words so a
        # valid clip is still queued instead of failing the whole render.
        body, salvaged_blocks = _repair_unbalanced_dialogue(body)
        if not valid_beats:
            body = _insert_dialogue_blocks(body, salvaged_blocks)
        spans, malformed = _dialogue_spans(body)

    if valid_beats:
        # Structured dialogue is authoritative. Remove any planner-authored
        # copies and insert one concise, prominent sequence so visual prose
        # cannot bury the exact lines and their timing cues.
        body = _replace_spans(body, spans, [""] * len(spans))
        dialogue_parts: list[str] = []
        for beat in valid_beats:
            delivery = _compact_h3_line_delivery(beat["delivery"])
            delivery_text = f" {delivery}" if delivery else ""
            # The label is rendered once, by Maestro. A planner-authored label
            # left in the name produced "(S1) (S1) speaks ...".
            name = _normalized_space(beat.get("speaker_name", ""))
            name_text = f"{name} " if name else ""
            sentence = (
                f"{name_text}{beat['stable_id']} speaks"
                f"{delivery_text}: {beat['tag']}."
            )
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
        vocal_detail = (
            f"{timing} Dialogue: {' '.join(dialogue_parts)} {guard} "
            "Keep each current speaker visibly framed with an unobstructed "
            "face and mouth through their complete line."
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
                if "vocal ownership stays with the assigned singer" not in body.casefold():
                    body = _insert_h3_vocal_detail(body, direction)
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

    body = _normalized_space(body)
    return body, contract


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
        folded_role = role.casefold()
        for subject_index, subject in enumerate(subjects or [], start=1):
            slot, inline_speaker = _planner_subject_slot(subject, subject_index)
            character_id = _normalized_space(_field(subject, "character_id", ""))
            subject_name = _normalized_space(
                _field(subject, "speaker_name", "") or character_id
            )
            candidates = [
                value for value in (subject_name, character_id)
                if len(value) >= 2
            ]
            # A planner row with no structured identity names its participant
            # only through the inline "(Sx)" label, and the reference map
            # addresses that same participant by the same token.
            if inline_speaker and inline_speaker.casefold() in folded_role:
                candidates.append(inline_speaker)
            if not any(value.casefold() in folded_role for value in candidates):
                continue
            entry = _speaker_registry_entry(
                registry,
                character_id or subject_name,
            )
            stable_id = entry[0] if entry else ""
            return slot, subject_name or character_id, stable_id
        # No row matched. Fall back to the planner's own binding so the picture
        # lands on the Subject the shot actually uses.
        for speaker, slot in speaker_slots.items():
            if re.search(
                rf"(?<![A-Za-z0-9]){re.escape(speaker)}(?![0-9])",
                role,
                re.IGNORECASE,
            ):
                return slot, role, ""
        return None

    def add_task_type(value: str) -> None:
        if value not in task_types:
            task_types.append(value)

    for reference in references or []:
        kind = str(reference.get("type") or "").strip().lower()
        role = _trim_sentence(reference.get("role", "")) or f"the supplied {kind} reference"
        subject_match = mapped_subject(role)
        mapped_role = (
            f"<Subject {subject_match[0]}> ({subject_match[1]})"
            if subject_match else role
        )
        if kind == "image":
            picture_no += 1
            label = f"<Picture {picture_no}>"
            intent = str(reference.get("image_intent") or "identity").lower()
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

            if subject_match:
                subject_no, subject_name, _ = subject_match
            else:
                subject_no = next_subject_no
                next_subject_no += 1
                subject_name = role

            if intent == "scene":
                source_clause = f"environment and location identity come from {label}"
                explanation = (
                    f"preserve the architecture, materials, lighting context, and "
                    f"location identity supplied by {label}, but not incidental people"
                )
                marker = "fully_preserved"
                detail_bindings.append(
                    f"<Subject {subject_no}> is the environment established by {label}."
                )
            elif intent == "style":
                source_clause = f"visual style is guided by {label}"
                explanation = (
                    f"retain broad similarity to the medium, palette, lighting "
                    f"language, and texture of {label}, but not its people, pose, "
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
                    f"identity from {label} as the same single person already "
                    "described in this shot, never as inserted source footage or "
                    "an additional person."
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


_H3_SUBJECT_SLOT_RE = re.compile(r"<\s*Subject\s+(\d+)\s*>", re.IGNORECASE)
_H3_SPEAKER_TOKEN_RE = re.compile(r"\(\s*S\s*(\d+)\s*\)", re.IGNORECASE)


def _planner_subject_slot(subject: Any, position: int) -> tuple[int, str]:
    """Recover the planner's own ``<Subject N>`` slot and ``(Sx)`` speaker.

    The short-film planner emits ``subjects_on_screen`` rows whose only fields
    are ``visual_description`` and ``position_or_relation``, and it names the
    participant inline: ``<Subject 1> (S1), the woman in the cream A-dress.``
    Those rows are not ordered by Subject number. Numbering the compiled
    definitions by list position therefore relabelled a participant as the
    other one in roughly half the shots: the action text kept the planner's
    numbering while ``subject_definitions`` swapped it, so one ``<Subject 1>``
    was bound to ``(S1)`` in one field and to ``(S2)`` in the other. The model
    resolved that contradiction by giving one participant the other's reference
    appearance. Trust the explicit label, and fall back to list position only
    when the planner omits it.
    """

    raw = _field(subject, "visual_description", "")
    if not raw and not isinstance(subject, Mapping):
        raw = subject
    text = str(raw or "")
    tag = _H3_SUBJECT_SLOT_RE.search(text)
    slot = int(tag.group(1)) if tag and int(tag.group(1)) > 0 else position
    token = _H3_SPEAKER_TOKEN_RE.search(text)
    return slot, (f"S{int(token.group(1))}" if token else "")


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


def _ref2va_subject_definitions(
    subjects: Sequence[Any],
    registry: Mapping[str, Any],
    source_bindings: Mapping[int, Sequence[str]] | None = None,
    body: str = "",
) -> list[str]:
    """Emit one definition per participant, numbered as the shot numbers them.

    ``body`` is the compiled action text, which already carries the canonical
    ``<Subject N> (Sx)`` pairs. Reusing them here is what keeps the identity
    block and the action text describing the same person; without it the two
    disagreed for every shot whose ``subjects_on_screen`` rows arrived in a
    different order than the planner numbered them.
    """

    source_bindings = source_bindings or {}
    body_bindings = _h3_body_subject_bindings(body)
    rows: list[dict[str, Any]] = []
    seen: dict[int, int] = {}
    for position, subject in enumerate(subjects or [], start=1):
        slot, inline_speaker = _planner_subject_slot(subject, position)
        character_id = _normalized_space(_field(subject, "character_id", ""))
        speaker_name = _normalized_space(_field(subject, "speaker_name", ""))
        name = _clean_h3_metadata(speaker_name or character_id, 80)
        entry = (
            _speaker_registry_entry(registry, character_id or speaker_name)
            or _speaker_registry_entry(registry, name)
        )
        speaker = body_bindings.get(slot) or (
            entry[0].strip("()") if entry and entry[0] else inline_speaker
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
            "name": "",
            "speaker": body_bindings.get(slot, ""),
            "details": list(source_bindings[slot]),
        })

    definitions: list[str] = []
    for row in sorted(rows, key=lambda item: item["slot"]):
        label = f"<Subject {row['slot']}>"
        if row["name"]:
            label = f"{label} is {row['name']}"
        if row["speaker"]:
            label = f"{label} ({row['speaker']})"
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
        label = f"<Subject {index}>"
        if label in result:
            continue
        name = _normalized_space(
            _field(subject, "speaker_name", "")
            or _field(subject, "character_id", "")
        )
        if not name:
            continue
        pattern = re.compile(rf"(?<![\w>]){re.escape(name)}(?![\w<])", re.IGNORECASE)
        result, count = pattern.subn(f"{label} ({name})", result, count=1)
        if not count:
            missing.append(f"{label} ({name}) is visible in the described blocking.")
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
    if shot and text[:shot.start()].strip():
        opening = _normalized_space(text[:shot.start()])
        timeline = text[shot.end():].strip()
        return opening.rstrip(" .") + ".", timeline
    if shot:
        text = text[shot.end():].strip()

    lighting = _h3_labeled_value(text, "Lighting")
    mood = _h3_labeled_value(text, "Mood")
    if lighting or mood:
        details = "; ".join(value for value in (lighting, mood) if value)
        opening = (
            "The target video maintains the requested visual treatment, with "
            f"{details}."
        )
    else:
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
    opening_blocking: str = "",
    closing_blocking: str = "",
    audio_plan: Mapping[str, Any] | None = None,
    default_language: str | None = None,
    audio_start_seconds: Any = None,
) -> tuple[str, str]:
    """Compile Director metadata into MiniMax's documented Context-IR shape."""

    mode = str(mode or "t2va").strip().lower()
    if mode not in {"t2va", "i2va", "fl2va", "l2va", "ref2va"}:
        raise H3DialogueContractError(f"Unknown MiniMax H3 prompt mode: {mode}")
    # Repair a corrupted fragment from another script that a planner glued into
    # the middle of a Latin word, before it is copied into any field.
    prompt = repair_glued_foreign_script(prompt)
    registry = speaker_registry if isinstance(speaker_registry, Mapping) else {}
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
    audio_mode = _normalized_space(_field(audio_plan or {}, "mode", "")).casefold()
    if audio_mode == "music_driven":
        # The supplied soundtrack already contains the sung words. Do not let
        # an LLM's transcript become a competing generated-dialogue request.
        dialogue_beats = []
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
    if audio_mode in {"audio_driven", "music_driven"}:
        # Initial Director preflight runs before concrete Ref2VA manifests are
        # assembled. The shot's audio plan still proves that a mapped source
        # track owns the vocals, so do not temporarily treat it as a silent or
        # prompt-scripted shot.
        has_driving_audio = True
    body, soundscape, music, existing_blocks = _source_prompt_parts(
        prompt,
        project_context=project_context,
        context_anchors=context_anchors,
        opening_blocking=opening_blocking,
        closing_blocking=closing_blocking,
        audio_plan=audio_plan,
    )
    if mode == "ref2va":
        style_opening, body = _split_ref2va_style_opening(body)
        body, vocal_contract = _compile_official_dialogue(
            body,
            subjects or [],
            dialogue_beats or [],
            registry,
            existing_blocks,
            has_driving_audio=has_driving_audio,
            duration_seconds=duration_seconds,
            music_driven=audio_mode == "music_driven",
            vocal_activity=_field(audio_plan or {}, "vocal_activity", None),
            project_context=project_context,
            default_language=default_language,
        )
        body = re.sub(r"^\s*\[Shot\s+1\]\s*", "", body, flags=re.IGNORECASE)
        body = f"[Shot 1] {body}".strip()
        body, _ = _align_h3_time_markers(body, audio_start_seconds)
        # After the time markers are aligned, because alignment needs the
        # ``[Shot N] At ...`` shape that this step removes.
        body = _single_shot_body(body)
        subject_definitions = _ref2va_subject_definitions(
            subjects or [],
            registry,
            subject_sources,
            body=body,
        )
        definitions = "\n".join([*subject_definitions, *reference_definitions])
        if not definitions:
            definitions = "Use the explicitly described subjects and target scene."
        retention_text = "\n".join(retention) or (
            "Preserve the explicitly described identities, wardrobe, setting, action, and audio roles."
        )
        body = _label_ref2va_subjects_in_body(body, subjects or [])
        summary_body = re.sub(r"^\[Shot\s+1\]\s*", "", body, flags=re.IGNORECASE)
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
        body = _ensure_h3_context_anchors(body, context_anchors or [])
        body = f"{style_opening} {body}".strip()
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
        )
        compiled_body = re.sub(r"^\s*\[Shot\s+1\]\s*", "", compiled_body, flags=re.IGNORECASE)
        compiled_body = f"[Shot 1] {compiled_body}".strip()
        compiled_body, _ = _align_h3_time_markers(
            compiled_body, audio_start_seconds,
        )
        # Before the shot numbers are read, so a clip-local header can no longer
        # inherit the planner's film-global number.
        compiled_body = _single_shot_body(compiled_body)
        shot_numbers = [int(value) for value in re.findall(
            r"\[Shot\s+(\d+)\]", compiled_body, flags=re.IGNORECASE,
        )]
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
    return normalize_h3_text(compiled).strip(), vocal_contract


def validate_h3_prompt_contract(
    prompt: str,
    dialogue_beats: Sequence[Any] | None = None,
    *,
    mode: str = "t2va",
    references: Sequence[Mapping[str, Any]] | None = None,
    subjects: Sequence[Any] | None = None,
    context_anchors: Sequence[str] | None = None,
) -> list[str]:
    """Validate the official field order plus Maestro's exact dialogue data."""

    text = str(prompt or "")
    mode = str(mode or "t2va").strip().lower()
    expected = _H3_REF2VA_FIELDS if mode == "ref2va" else _H3_BASE_FIELDS
    errors = validate_h3_vocal_contract(text, dialogue_beats)
    positions: list[int] = []
    for field in expected:
        matches = list(re.finditer(
            rf"(?mi)^\s*{re.escape(field)}\s*:", text,
        ))
        if len(matches) != 1:
            errors.append(f"expected one {field} field, found {len(matches)}")
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
    if mode == "ref2va":
        shot = re.search(r"\[Shot\s+1\]", visual, flags=re.IGNORECASE)
        if not shot:
            errors.append("detailed_description is missing [Shot 1]")
        elif not visual[:shot.start()].strip():
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
        name = _normalized_space(
            _field(subject, "speaker_name", "")
            or _field(subject, "character_id", "")
        )
        folded = name.casefold()
        if (
            name
            and folded not in _H3_GENERIC_IDENTITY_LABELS
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


def compile_h3_clip_plans(
    clip_plans: Sequence[MutableMapping[str, Any]],
    *,
    prompt_modes: Sequence[str] | None = None,
    durations: Sequence[float] | None = None,
    reference_manifests: Sequence[Sequence[Mapping[str, Any]]] | None = None,
) -> Sequence[MutableMapping[str, Any]]:
    """Compile every saved/renderable H3 clip immediately before queuing.

    ``_director_h3_source_prompt`` remains the immutable planner result. This
    lets generation recompile a prompt from T2VA to I2VA/FL2VA after the actual
    start/end files are known without nesting an alignment header or six-field
    Ref2VA wrapper around a previously compiled prompt.
    """

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
        if _normalized_space(_field(plan.get("_director_audio_plan") or {}, "mode", "")).casefold() == "music_driven":
            beats = []
            plan["_director_dialogue_beats"] = []
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
            prompt = str(source_prompt)
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
        references = (
            reference_manifests[index]
            if reference_manifests is not None and index < len(reference_manifests)
            else plan.get("_director_h3_reference_manifest") or []
        )
        context_anchors = _h3_plan_context_anchors(plan)
        plan["_director_required_context_anchors"] = context_anchors
        prompt, contract = compile_h3_official_prompt(
            source_prompt,
            plan.get("_director_subjects_on_screen") or [],
            beats,
            mode=mode,
            duration_seconds=duration,
            references=references,
            speaker_registry=registry,
            project_context=plan.get("_director_project_context", ""),
            context_anchors=context_anchors,
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
        )
        plan["video_prompt"] = prompt
        plan["_director_h3_compiled_prompt"] = prompt
        # A clip is one continuous shot, so a body that also declares a later one
        # is a defect the user cannot see in the rendered clip until a character
        # comes out duplicated. Say it where the log is read, not silently.
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
        errors = validate_h3_prompt_contract(
            prompt,
            beats,
            mode=mode,
            references=references,
            subjects=plan.get("_director_subjects_on_screen") or [],
            context_anchors=context_anchors,
        )
        if errors:
            raise H3DialogueContractError(
                f"Shot {index + 1}: " + "; ".join(errors)
            )
    return clip_plans
