"""
Prompt Compressor — removes redundancy and optimizes token usage.

Non-creative layer: preserves meaning while reducing prompt length.
Preserves: action clarity, camera direction, dialogue phrasing, ending beat.
Removes: repeated adjectives, filler phrases, redundant identity restatement.
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional

from ..schema import ShotPlan
from ..policies import compress_prompt_text


@dataclass
class CompressionResult:
    original: str
    compressed: str
    original_words: int
    compressed_words: int
    chars_removed: int
    compression_ratio: float  # 0.0 = nothing removed, 1.0 = everything removed


def compress_prompt(
    prompt: str,
    mode: str,
    shot: Optional[ShotPlan] = None,
    aggressive: bool = False,
) -> CompressionResult:
    """Compress a rendered prompt while preserving essential content.

    Args:
        prompt: The rendered prompt text.
        mode: Render mode (t2v, i2v, a2v, retake, extend, image_gen).
        shot: Optional ShotPlan for context-aware compression.
        aggressive: If True, apply more aggressive compression rules.

    Returns:
        CompressionResult with original and compressed text plus metrics.
    """
    original = prompt
    original_words = len(original.split())
    result = prompt

    # Step 1: Basic compression (filler removal, duplicate adjectives)
    result, chars_removed = compress_prompt_text(result)

    # Step 2: Mode-specific compression
    if mode == "i2v":
        result = _compress_i2v(result)
    elif mode == "a2v":
        result = _compress_a2v(result, shot)
    elif mode == "image_gen":
        result = _compress_image(result)

    # Step 3: Generic cleanup
    result = _generic_cleanup(result)

    # Step 4: Aggressive compression (if enabled)
    if aggressive:
        result = _aggressive_compress(result)

    compressed_words = len(result.split())
    total_removed = len(original) - len(result)

    return CompressionResult(
        original=original,
        compressed=result,
        original_words=original_words,
        compressed_words=compressed_words,
        chars_removed=total_removed,
        compression_ratio=total_removed / len(original) if original else 0.0,
    )


def _compress_i2v(text: str) -> str:
    """I2V-specific: reduce redundant static scene description.

    The start image already shows the scene — remove descriptors that
    don't add new motion/change information.
    """
    # Remove redundant "in the scene" / "visible in frame" type phrases
    redundant = [
        r'\bin the (?:current |existing )?(?:scene|frame|shot)\b',
        r'\balready (?:visible|shown|present)\b',
        r'\bas seen (?:in|from) the (?:image|photo|frame)\b',
    ]
    for pattern in redundant:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    return text


def _compress_a2v(text: str, shot: Optional[ShotPlan] = None) -> str:
    """A2V-specific: trim repeated setting, never the performance.

    A dialogue shot's sentences are the performance: what the actor does while the
    line plays. Measured on a real 26-clip drama, this function used to keep the
    first two sentences (the setting), any sentence containing a quote or one of
    "says/speaks/whispers/gestures/nods", and the last sentence -- and delete
    everything else. Every action sentence went with it: "She touches the knife,
    then touches the rosary", "Don Aurelio enters from behind her", "Streetlamps
    flicker and fail one by one". Seven different shots reduced to the same
    setting plus the same closing line, so they rendered as one static portrait
    of one character in one place, which is what the user reported.

    Only a sentence that restates what the kept text already said is dropped, so
    the budget this exists for is still met on a prompt that repeats its setting,
    and a prompt whose sentences each add something survives whole.
    """

    if not (shot and shot.audio_plan.lip_sync_critical):
        return text
    sentences = [part for part in re.split(r'(?<=[.!?])\s+', text) if part.strip()]
    if len(sentences) <= 6:
        return text
    kept: list[str] = []
    stated: set[str] = set()
    for position, sentence in enumerate(sentences):
        is_last = position == len(sentences) - 1
        # The opening sentence is the setting, and it is what makes the shot readable.
        opens_the_shot = position == 0
        protected = bool(_PERFORMANCE_RE.search(sentence))
        repeats = _novel_content_ratio(sentence, stated) < _MIN_NOVELTY
        if is_last or opens_the_shot or protected or not repeats:
            kept.append(sentence)
            stated.update(_content_words(sentence))
    return " ".join(kept)


# Anything that shows something happening: dialogue, camera direction, or a body
# doing something. Protected sentences are kept regardless of how much they repeat,
# because cutting the performance is the failure this guards against.
_PERFORMANCE_RE = re.compile(
    r'["“”«»]|'
    r'\b(?:camera|shots?|framing|dolly|tracking|pans?|tilts?|zoom\w*|push[- ]?in|'
    r'orbit\w*|crane|handheld|lens|close[- ]?ups?|angle)\b|'
    r'\b(?:turns?|turning|looks?|looking|gazes?|stares?|glares?|blinks?|nods?|'
    r'shakes?|touches?|touching|reaches?|raises?|lifting|lifts?|lowers?|drops?|'
    r'opens?|closing|closes?|steps?|walks?|enters?|entering|exits?|kneels?|'
    r'rises?|sits?|settles?|leans?|picks?|places?|holds?|presses?|pulls?|'
    r'pushes?|sways?|swaying|flickers?|fails?|spills?|dims?|brightens?|'
    r'gathers?|crosses?|trembles?|shivers?|breathes?|'
    r'says?|speaks?|speaking|whispers?|mutters?|shouts?|sings?|recites?)\b',
    re.IGNORECASE,
)

# A sentence that names almost nothing new is the setting said twice.
_MIN_NOVELTY = 0.34

_STOP_WORDS = frozenset({
    "the", "and", "with", "from", "that", "this", "into", "over", "under", "then",
    "her", "his", "their", "its", "she", "he", "they", "it", "as", "at", "in",
    "on", "of", "to", "a", "an", "is", "are", "was", "were", "be", "been",
    "while", "which", "who", "for", "by", "not", "no", "all", "only",
})


def _content_words(text: str) -> set[str]:
    return {
        word for word in re.findall(r"[a-záéíóúüñ0-9'-]+", text.casefold())
        if len(word) > 2 and word not in _STOP_WORDS
    }


def _novel_content_ratio(sentence: str, stated: set[str]) -> float:
    words = _content_words(sentence)
    if not words:
        return 1.0
    return len(words - stated) / len(words)


def _compress_image(text: str) -> str:
    """Image prompt compression — remove any lingering motion language."""
    # Remove motion-implying phrases that may have leaked through
    motion_phrases = [
        r'\b(?:begins? to|starts? to|about to|ready to)\s+\w+',
        r'\bin (?:the process|the act) of\b',
    ]
    for pattern in motion_phrases:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    return text


def _generic_cleanup(text: str) -> str:
    """Generic cleanup applicable to all modes."""
    # Collapse repeated commas/spaces
    text = re.sub(r',\s*,', ',', text)
    text = re.sub(r'\.\s*\.', '.', text)
    text = re.sub(r'  +', ' ', text)

    # Remove trailing/leading whitespace and punctuation issues
    text = text.strip()
    if text and not text[-1] in '.!?"':
        text += "."

    return text


def _aggressive_compress(text: str) -> str:
    """More aggressive compression for token-constrained scenarios."""
    # Remove hedging language
    hedges = [
        r'\b(?:somewhat|slightly|a bit|rather|quite|fairly|kind of|sort of)\s+',
        r'\b(?:appears to be|seems to be|looks like)\s+',
    ]
    for pattern in hedges:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)

    # Simplify compound descriptors
    text = re.sub(r'\b(soft and gentle|warm and inviting|dark and moody)\b',
                  lambda m: m.group(0).split(' and ')[0], text, flags=re.IGNORECASE)

    return text
