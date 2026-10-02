"""Whether a plan's spoken lines belong in the prompt, or only in its timing metadata.

Two plans both report ``audio_driven``, and they need opposite answers.

* A **film** whose dialogue track supplies the words: the clipped speech *is* the
  performance, the prompt is the only place that says who speaks when and what the words
  are, and the reported 190-shot project rendered with zero ``<d>`` lines. Upstream v2.4.0
  dropped them here too, on the reading that "supplied driving audio owns every audible
  voice". That assumes the model can perform a line it is never told; H3 cannot, so it
  invented the speech and the speakers.
* A **music video** whose uploaded song supplies the vocals: nothing has to be mouthed, so
  the lines stay metadata. Upstream pins this with its own tests, and the measurements agree
  (viral-2: 32/32 clips declare ``lip_sync_critical`` false and carry no beats at all).

``lip_sync_critical`` separates them, and it is the plan's own declaration that the mouths
have to follow the line rather than something inferred from the audio mode: the reported
project declares it on 190/190 clips, the music video on 0/32. Reading the mode alone is what
made this fork compile the music video's lyrics as generated speech and trip upstream's own
contract.

So this fork compiles them where the plan asks the mouths to follow them, and this module is
the single place that decides it, because the decision is spread over three gates in the
compiler: which beats reach it at all, which survive the prompt builder, and which the
dialogue loop reads. Stating it once also keeps a merge from quietly restoring upstream's
behaviour through any one of the three.

Upstream's behaviour is kept everywhere the plan does not ask for mouthed lines:

* a music-driven plan keeps its beats out, because there the lyrics belong to the soundtrack
  and not to a performer in frame;
* any other mode driven by an audio reference keeps them out too, which is where upstream
  already was and what its own tests pin.
"""

from __future__ import annotations

from typing import Any, Mapping

AUDIO_DRIVEN = "audio_driven"
MUSIC_DRIVEN = "music_driven"


def _lip_critical(audio_plan: Any) -> bool:
    """Read the plan's "the mouths must follow this line" declaration.

    Only a real truth counts: a plan saved with the string ``"False"`` must not read as
    ``True``, which is what a bare ``bool()`` would make of it.
    """

    value = getattr(audio_plan, "lip_sync_critical", None)
    if value is None and isinstance(audio_plan, Mapping):
        value = audio_plan.get("lip_sync_critical")
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "yes", "1"}
    return bool(value)


def states_the_lines(
    *,
    audio_mode: str,
    audio_plan: Any = None,
    driving_audio: bool = False,
    driving_audio_reference: bool = False,
) -> bool:
    """True when a plan's spoken lines must be written into its prompt.

    ``audio_mode`` is the plan's own mode (``audio_driven`` when the supplied audio is what
    the shot performs), and ``audio_plan`` is that same plan, read for the declaration below.
    ``driving_audio`` says a track owns the voice, and ``driving_audio_reference`` says an
    audio reference in the manifest is driving it; the compiler knows the first at one gate
    and the second at another.
    """

    mode = str(audio_mode or "").strip().casefold()
    if mode == AUDIO_DRIVEN:
        # The prompt is the only place that says who speaks when, so it states the lines
        # wherever the plan wants those mouths to follow them.
        return _lip_critical(audio_plan)
    if mode == MUSIC_DRIVEN:
        # A music plan's beats are the song, already audible in the supplied track.
        return False
    return not (driving_audio or driving_audio_reference)
