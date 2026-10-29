"""What "well planned" means for this kind of film.

The four Director workflows are not the same craft, and until now they shared one
set of planning habits in the places that matter most. A podcast is shot in one
studio and lives on framing and reaction; a short film that stays in one room for
ten clips is a mistake; a music video returns to the same stage on purpose; a
viral clip has to hook on frame one. Left unsaid, the planner applies whatever the
brief happens to emphasise, which is how a 26-clip drama ended with ten
consecutive shots in one church and eleven prompts declaring the frame motionless.

This module is the single place that states the contract per kind, as

  * a ``guidance`` block injected into the planner's own prompt (so the writer is
    told the rule while it writes, not after), and
  * the numbers the plan variety check reads back (so a podcast is not warned
    about the setting consistency it was asked for).

It changes no prompt by itself: each planner decides where the block belongs in
its system prompt.
"""

from __future__ import annotations

from dataclasses import dataclass

# The workflows Maestro actually runs (see director_model_compat.DIRECTOR_PIPELINE_TYPES).
SHORT_FILM_AUDIO = "short_film_audio"
SHORT_FILM_STORY = "short_film_story"
MUSIC_VIDEO = "music_video"
PODCAST = "podcast"
VIRAL_VIDEO = "viral_video"

_KIND_ALIASES = {
    "short_film": SHORT_FILM_AUDIO,
    "film": SHORT_FILM_AUDIO,
    "narrative": SHORT_FILM_STORY,
    "story": SHORT_FILM_STORY,
    "audio_film": SHORT_FILM_AUDIO,
    "music": MUSIC_VIDEO,
    "song": MUSIC_VIDEO,
    "podcast_video": PODCAST,
    "viral": VIRAL_VIDEO,
}


@dataclass(frozen=True)
class ScenePolicy:
    """The scene contract for one workflow, plus the numbers that verify it."""

    kind: str
    label: str
    #: Longest run of consecutive shots allowed in one setting before the plan is
    #: reported as standing still. 0 means the setting is meant to be constant.
    max_consecutive_same_setting: int
    #: Largest share of shots allowed to declare the frame itself does not move.
    #: 1.0 means stillness is expected and never reported.
    stillness_limit: float
    #: Whether every shot is expected to contain a performed action by someone in frame.
    requires_performed_action: bool
    #: The prompt block, injected into the planner's system prompt.
    guidance: str


_SHORT_FILM_GUIDANCE = """SCENE AND SHOT PROGRESSION — SHORT FILM (non-negotiable):
- A short film is a sequence of places and states, not one image with different lines over it. Each shot must change at least one thing against the shot before it: the place, the time of day, who is in frame, what the character visibly does, or how the camera frames it.
- Never place more than two consecutive shots in the same setting. A third shot in one place has to be a genuinely new angle on new business (someone arrives, leaves, crosses, opens, or turns), and a whole film may hold one setting only for its single climax.
- Inside a long beat in one place, rotate the shot: change the size AND the move from clip to clip (wide establishing, then medium, then close-up, then an insert of a hand or an object), and give somebody new business. Never repeat the previous shot's size and move together, and never restate the same tableau with different words — two clips that both say "the children stand at the altar as their faces glow" are one clip.
- Stillness is an accent, never the default: at most one shot in five may state that the frame does not move, and never two in a row. Every other shot names a physical action somebody performs. Standing, waiting, watching, and "remains still" are not actions.
- Variety must be caused, never decorative: a new place has to come from an arrival, a departure, a discovery, or a consequence in the shot before it. Changing the framing, who is in frame, or what the character is doing needs no excuse and is always available, so use those before you invent a location.
- A camera or stillness rule qualified with "whenever", "always", "every time" or "throughout" is a habit, not a beat. Apply it once, where it lands hardest, then rotate. When the brief details a specific beat, honour it there and treat it as that beat."""

_MUSIC_VIDEO_GUIDANCE = """SCENE AND SHOT PROGRESSION — MUSIC VIDEO (non-negotiable):
- Cut to the music: a new visual idea at each section boundary (intro, verse, chorus, bridge), not a new location for every clip.
- Performance coverage — the singer, the band, the same stage — may return to one set as often as the song needs. That is intended repetition, not a defect, and it must not be "fixed" by moving the performer somewhere new on every beat.
- Between returns, change the image: a new costume, light, weather, prop, or a different space for the same performer.
- Never illustrate a lyric literally when a concrete image of the same feeling will do. Hold one governing visual idea per section and let the cutting carry the energy.
- Entrances, turns, and gestures fall on musical accents. A clip with no performance and no image change at all is a hole in the cut."""

_PODCAST_GUIDANCE = """SCENE AND SHOT PROGRESSION — PODCAST (non-negotiable):
- The studio is the world: one set, one seating order, one lighting design, one lens family for the whole episode. Do not move the hosts to a new location to create variety.
- Variety comes from coverage, not from places: alternate the active speaker's framing with the listener's reaction, and use an insert or a graphic only where the topic changes.
- A static frame while someone speaks is correct here. Motion is not the goal; the conversation is.
- Behind-the-mic business carries the visual life: gesture, lean-in, laughter, a hand on the desk, a look at the other host. Stage that instead of inventing new rooms."""

_VIRAL_VIDEO_GUIDANCE = """SCENE AND SHOT PROGRESSION — VIRAL VIDEO (non-negotiable):
- The hook is frame one: somebody is already doing something when the video starts. No establishing shot, no empty room, no slow build.
- Keep the pace visible: a new framing, angle, or place every few seconds, and never two consecutive shots with the same framing and setting.
- Every shot earns its screen time with a visible action, a reaction, or a reveal. A clip that only restates the previous one is dead weight.
- End on the payoff and do not restate the opening shot to pay it off."""

_POLICIES = {
    SHORT_FILM_AUDIO: ScenePolicy(
        kind=SHORT_FILM_AUDIO,
        label="short film (audio-driven)",
        max_consecutive_same_setting=2,
        stillness_limit=0.2,
        requires_performed_action=True,
        guidance=_SHORT_FILM_GUIDANCE,
    ),
    SHORT_FILM_STORY: ScenePolicy(
        kind=SHORT_FILM_STORY,
        label="short film (story-driven)",
        max_consecutive_same_setting=2,
        stillness_limit=0.2,
        requires_performed_action=True,
        guidance=_SHORT_FILM_GUIDANCE,
    ),
    MUSIC_VIDEO: ScenePolicy(
        kind=MUSIC_VIDEO,
        label="music video",
        max_consecutive_same_setting=4,
        stillness_limit=0.4,
        requires_performed_action=False,
        guidance=_MUSIC_VIDEO_GUIDANCE,
    ),
    PODCAST: ScenePolicy(
        kind=PODCAST,
        label="podcast",
        max_consecutive_same_setting=0,
        stillness_limit=1.0,
        requires_performed_action=False,
        guidance=_PODCAST_GUIDANCE,
    ),
    VIRAL_VIDEO: ScenePolicy(
        kind=VIRAL_VIDEO,
        label="viral video",
        max_consecutive_same_setting=2,
        stillness_limit=0.15,
        requires_performed_action=True,
        guidance=_VIRAL_VIDEO_GUIDANCE,
    ),
}

#: Used when a caller has no workflow (an old saved pipeline, a direct test).
DEFAULT_KIND = SHORT_FILM_AUDIO


def scene_policy(kind: str | None) -> ScenePolicy:
    """The policy for ``kind``, resolving aliases and defaulting to a short film."""

    key = str(kind or "").strip().lower()
    key = _KIND_ALIASES.get(key, key)
    return _POLICIES.get(key) or _POLICIES[DEFAULT_KIND]


def scene_guidance(kind: str | None) -> str:
    """The prompt block for ``kind``, ready to interpolate into a system prompt."""

    return scene_policy(kind).guidance
