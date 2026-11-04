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

import re
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


#: Language that puts a narrator inside the film instead of over it. A brief written as a
#: narrated documentary says some of these; measured on a real run every shot was planned
#: as if the narrator spoke from off camera, so the one person the film is built around
#: never appeared.
_PRESENCE_PHRASES = (
    "standing rule",
    "inside every scene",
    "inside the scene",
    "inside the action",
    "moves through the settings",
    "moves immersively",
    "at the center of the action",
    "at the centre of the action",
    "at the center of the cinematic action",
    "at the centre of the cinematic action",
    "never beside",
    "appear around her",
    "appear around him",
    "appear around the narrator",
    "in sync with the spoken account",
)


def narrator_name(brief: str) -> str:
    """The first Subject's name, or ``""`` when the brief names none.

    Parsed by the shared lock reader. A local copy of this regex required one
    parenthetical, so a lock written ``<Subject 1> (S1)(Picture 1) is ALWAYS Anita.``
    yielded no name — and with no name there was no on-screen narrator either, so a whole
    documentary was planned with Anita speaking from off camera and appearing in no shot.
    """

    from .subject_lock import parse

    return parse(brief).get(1, "")


def narrator_from_context(text: str) -> str:
    """The narrator's name from whatever text is at hand: a brief's lock, or a shot contract.

    The plan-level repairs run after the brief is gone -- the only text left is the per-shot
    context, which states the presenter as "Anita (a woman) is ON SCREEN ...".
    """

    name = narrator_name(text)
    if name:
        return name
    match = re.search(
        r"(?m)^\s*(?:-\s*)?([A-Z][\w'\u00c0-\u024f-]{1,30})\s*(?:\(a (?:woman|man)\))?"
        r"\s+is ON SCREEN\b",
        str(text or ""),
    )
    return match.group(1) if match else ""


def narration_is_present(brief: str) -> bool:
    """Whether the brief puts the narrator inside the film rather than over it.

    Two ways to say it, and the second is the one real briefs use. Some write a rule
    ("STANDING RULE: inside every scene"); most just show the narrator doing things —
    "Anita walks the audience through the ancient landscape", "Anita stands near him",
    "Anita moves through the camp". The phrase list alone missed a real 42-clip brief
    built exactly that way, so the narrator was never marked as present and every shot was
    planned as an off-screen voiceover.

    So: an explicit phrase, or the narrator's own name next to a verb of presence.
    """

    text = str(brief or "")
    lowered = text.casefold()
    if any(phrase in lowered for phrase in _PRESENCE_PHRASES):
        return True
    name = narrator_name(text)
    if not name:
        return False
    return bool(re.search(
        rf"\b{re.escape(name)}\b[^.\n]{{0,90}}?\b"
        r"(walks?|walking|stands?|standing|steps?|moves?|moving|crosses?|crossing|"
        r"enters?|entering|kneels?|kneeling|sits?|sitting|points?|pointing|gestures?|"
        r"gesturing|holds?|holding|leads?|leading|guides?|guiding|tours?|shows?|"
        r"showing|beside|between|among|next to|in front of|through)\b",
        text,
        re.IGNORECASE,
    ))


def presenter_voiceover_repair(brief: str, prompt: str) -> str:
    """Undo the off-camera wording when the brief puts the speaker inside the film.

    The planner is told to use "says in an off-screen voiceover" for narration, and a
    presenter who stands in the scene is not narration. Measured on a real 42-clip run,
    every clip said "Anita (S1) says in an off-screen voiceover … while her lips remain
    completely closed", so the renderer drew her as a voice and she appeared in no shot.

    This is the deterministic half: if the brief has a presenter, that phrasing is rewritten
    to on-camera delivery and the closed-lips clause is removed. It is a wording repair,
    not a re-plan.
    """

    text = str(prompt or "")
    name = narrator_name(brief)
    if not text or not name or not narration_is_present(brief):
        return text
    repaired = re.sub(
        r"(?i)\bsays?\s+in\s+an?\s+off[- ]screen\s+voiceover\b",
        "says on camera, lips visible and moving",
        text,
    )
    repaired = re.sub(
        r"(?i)\b(the\s+)?voice[- ]over\b",
        "on-camera speech",
        repaired,
    )
    repaired = re.sub(
        rf"(?i)(?:\s*,?\s*while\s+(?:her|his|their)\s+lips\s+remain\s+completely\s+closed)"
        rf"|(?:\s*,?\s*and\s+(?:her|his|their)\s+lips\s+remain\s+completely\s+closed)",
        "",
        repaired,
    )
    repaired = re.sub(
        r"(?i)\s+with\s+(?:her|his|their)\s+lips\s+(?:remaining\s+)?closed\b",
        " with the mouth visible",
        repaired,
    )
    return repaired


def narration_presence(brief: str, *, compact: bool = False, gender: str = "") -> str:
    """The on-screen-narrator contract for a brief that asks for one, or ``""``.

    A brief that says the narrator "moves through the settings" and "stands at the centre
    of the cinematic action" is asking for a presenter who is *in* the film. The planner
    read that as narration over the image and planned every shot as an off-screen
    voiceover, so the one person the film is built around appeared in none of them.

    ``compact`` is for the per-shot context. The long form belongs in the planner's system
    prompt, where it is read once; a shot needs the rule, not the essay. Measured on a real
    42-clip run, the long form was 1,005 characters injected into every clip — identical —
    while that clip's own action was 734, so the instruction outweighed the scene four to
    one and every clip rendered the same shot.
    """

    if not narration_is_present(brief):
        return ""
    name = narrator_name(brief) or "the narrator"
    # The picture read states the presentation; the brief often does not, and a planner
    # with no gender writes a man. Measured on a real run: the narrator was described as
    # male in every shot while her own reference was a woman.
    who = f"{name} (a {gender})" if gender in ("woman", "man") else name
    if compact:
        # 160 characters, not 330: this text is injected into every clip, and on a real
        # plan it was 798 characters against a 638-character scene, so the instructions
        # outweighed the shot. The rest of the contract lives in the planner's prompt,
        # which is read once.
        return (
            f"{who} is ON SCREEN, speaking on camera with the mouth visible, at the SAME "
            f"DEPTH AND SCALE as what {name} describes; never off-screen voiceover."
        )
    return f"""NARRATION PRESENCE — {who.upper()} IS IN THE FILM, NOT OVER IT (non-negotiable):
- {who} is a visible participant of EVERY shot: on screen, in frame, inside the scene being described. Never off camera, never a disembodied voice.
- Never change the presenter's gender, age or build between shots: {who}.
- Every shot lists {name} in subjects_on_screen with her own speaker cue, and her line is delivered ON CAMERA with the mouth visible and moving.
- Never write "off-screen voiceover", "voiceover", "narrates over the image", "the voice of", or "lips remain completely closed" for {name} while the brief puts her inside the scene.
{staging_rules(name)}"""


#: Verbs and nouns that put a described figure far away from the narrator. A film built out
#: of these is one shot repeated: the presenter in the foreground, the story small behind
#: her. Measured on a real 36-clip run, 30 openings said "Anita in the foreground center"
#: and seven put David, Saul or Goliath in the "distant background".
_DISTANT_RE = re.compile(
    r"(?i)\b(?:distant|far|tiny|small in the)\s+background\b|\bdistant (?:left|right)\b|"
    r"\bin the distance\b|\bfar behind\b"
)

_FOREGROUND_RE = re.compile(r"(?i)\bforeground\b")

_FACING_CAMERA_RE = re.compile(
    r"(?i)\b(?:facing|address(?:ing|es)?|speak(?:s|ing)? to|look(?:s|ing)? (?:at|into)|"
    r"talk(?:s|ing)? to)\s+(?:the\s+)?(?:camera|lens|viewer|audience)\b"
)


def staging_rules(name: str, *, compact: bool = False) -> str:
    """How the narrator stands in the action, and how the shots must differ.

    The planner read "in frame" as "in front of the action": a presenter in the foreground
    centre with the described figures small in the distance, thirty-six times. Every clip
    was a different background behind the same shot, which is what the person reported as
    "una copia de la misma escena con distinto diálogo".

    Presence alone is not enough; depth and scale are what make the narrator a participant
    instead of a host.
    """

    if compact:
        return (
            f"{name} is at the SAME DEPTH AND SCALE as what she describes, not a presenter "
            f"in front of it: the figures and the action fill the frame with her, never "
            f"small in the distant background. This shot's framing differs from the shots "
            f"before and after it."
        )
    return f"""- {name} stands at the SAME DEPTH AND SCALE as the action she describes. When she talks about Goliath, Goliath fills the frame with her; when she talks about soldiers, she is among them. The described figures are never small in the distant background while she fills the foreground, and they are never behind glass, over a hill or across the valley from her.
- The frame is not a host in front of a backdrop. Do not write "in the foreground center" as a default composition, and do not repeat the same framing in consecutive shots. Vary it: she walks among the soldiers, she stands at David's shoulder, a low angle past her up to Goliath, an over-shoulder view of what she points at, a close shot on her face with the action at her back, a wide shot with her small inside the crowd she describes.
- No more than one shot in three may open with {name} centred and facing the camera. If the previous shots did that, this one does not.
- The described action happens at her scale and in the same plane of depth. If the shot cannot hold both her and the thing she describes at that scale, move the camera closer to the action rather than pushing the action into the distance."""


#: Distance phrasing applied to the figures the narrator describes. Each of these puts the
#: action behind her, which turns a documentary she walks through into a presenter in front
#: of a backdrop: 8 of 36 openings on a real run said David, Saul or Goliath stood in the
#: "distant background".
_DISTANCE_REWRITES = (
    (r"(?i)\bsmall in the distant (left|right) background\b", r"at her scale just behind her on the \1"),
    (r"(?i)\bsmall in the distant background\b", "at her scale just behind her"),
    (r"(?i)\bin the distant (left|right) background\b", r"right behind her on the \1, at her scale"),
    (r"(?i)\bin the distant background,? on the (left|right)\b", r"right behind her on the \1, at her scale"),
    (r"(?i)\bin the distant background,? (left|right)\b", r"right behind her on the \1, at her scale"),
    (r"(?i)\bin the distant background\b", "right behind her, at her scale"),
    (r"(?i)\bat her scale (left|right)\b", r"at her scale on the \1"),
    (r"(?i)\bbehind her (left|right)\b", r"behind her on the \1"),
    (r"(?i)\bin the background on the (left|right)\b", r"beside her on the \1"),
    (r"(?i)\bin the background,? (left|right)\b", r"beside her on the \1"),
    (r"(?i)\bin the distance\b", "just beyond her, at her scale"),
    (r"(?i)\bfar behind (her|him)\b", r"right behind \1"),
    (r"(?i)\bdistant (left|right)\b", r"beside her on the \1"),
    (r"(?i)\bsmall in the (rear|background)\b", "at her scale behind her"),
    (r"(?i)\bon (?:a |the )?distant (hill|hillside|ridge|slope|rise)\s*(left|right)\b", r"on the \1 beside her on the \2, at her scale"),
    (r"(?i)\bon (?:a |the )?distant (hill|hillside|ridge|slope|rise)\b", r"on the \1 beside her, at her scale"),
    (r"(?i)\bdistant (hill|hillside|ridge|slope|rise)s?\s*(left|right)\b", r"\1 beside her on the \2, at her scale"),
    (r"(?i)\bdistant (hill|hillside|ridge|slope|rise)s?\b", r"\1 beside her, at her scale"),
)

#: The narration of "she is a host": the narrator centred in the foreground, often facing
#: the lens, with nothing else placed in the frame. 30 of 36 openings on a real run put her
#: in the foreground, and the ones that named nobody were the same shot with a new backdrop.
_PRESENTER_TEMPLATE_RE = re.compile(
    r"(?i)^(?:the\s+|a\s+)?[^.]{0,60}?\b(?:in|at)\s+(?:the\s+)?foreground"
    r"(?:\s+cent(?:er|re))?\b[^.]{0,90}?\.?$"
)

#: Framings that replace a pure presenter template. Deterministic rotation, not invention:
#: the shot keeps its own environment and its own line, and only the camera's relationship
#: to the narrator changes.
_FRAMING_ROTATION = (
    "Medium shot: {who} moves through {where}, the figures she describes at her scale around her.",
    "Over {who}'s shoulder: what she describes fills the frame just beyond her.",
    "Low angle beside {who}: the action rises above her at the same scale.",
    "Wide shot: {who} small inside {where}, pointing out what she describes.",
    "Close on {who}'s face, the action sharp immediately behind her.",
)


#: Words that open a sentence or describe a shot rather than name a person. Used to tell
#: "Anita stands in the foreground" from "Anita stands in the foreground, David behind her".
_NOT_A_PERSON = {
    "the", "a", "an", "medium", "wide", "close", "low", "high", "over", "shot", "camera",
    "ancient", "valley", "this", "that", "she", "he", "they", "and", "but", "with", "in",
}


def _names_another_person(text: str, who: str) -> bool:
    for word in re.findall(r"\b([A-Z][a-z\u00c0-\u024f]{2,})\b", str(text or "")):
        key = word.casefold()
        if key == str(who or "").casefold() or key in _NOT_A_PERSON:
            continue
        return True
    return False


def _sentences(text: str) -> list:
    return [part for part in re.split(r"(?<=[.!?])\s+", " ".join(str(text or "").split())) if part]


def spread_staging(
    openings: list, *, name: str = "", environments=None,
    allow_share: float = 0.15,
) -> list:
    """Repair a plan whose every shot is the same framing, without asking a model.

    Two deterministic repairs, applied to the shot's own opening text:

    1. **Scale.** A figure the narrator describes is brought to her plane: "in the distant
       background" becomes "right behind her, at her scale". Her line does not change and no
       subject is invented -- only the distance qualifier moves.
    2. **Variety.** At most ``allow_share`` of the shots may keep the pure "narrator in the
       foreground, nothing else in frame" template. The excess are replaced by a rotating
       framing that keeps the shot's own place and its own line.

    A shot with neither fault is returned byte for byte.
    """

    items = [str(item or "") for item in (openings or [])]
    where = [str(item or "").strip() for item in (environments or [])]
    who = name or "the narrator"

    def _place(index: int) -> str:
        place = where[index] if index < len(where) else ""
        place = re.sub(r"\s+", " ", place).strip(" .")
        return place or "the scene"

    repaired: list = []
    for item in items:
        out = item
        for pattern, replacement in _DISTANCE_REWRITES:
            out = re.sub(pattern, replacement, out)
        repaired.append(out)

    # The template is judged on the OPENING SENTENCE, because that is the camera: "Anita in
    # foreground center. Goliath stands towering behind her." is the same shot as the one
    # with David in it -- same framing, different content -- which is exactly the complaint.
    templated = [
        index for index, item in enumerate(repaired)
        if item.strip() and _PRESENTER_TEMPLATE_RE.match(_sentences(item)[0].strip())
    ]
    allowed = max(1, int(len([item for item in items if item.strip()]) * allow_share))
    for position, index in enumerate(templated[allowed:]):
        framing = _FRAMING_ROTATION[position % len(_FRAMING_ROTATION)]
        sentences = _sentences(repaired[index])
        rest = " ".join(sentences[1:]).strip()
        head = framing.format(who=who, where=_place(index))
        repaired[index] = f"{head} {rest}".strip() if rest else head
    return repaired


def staging_report(openings: list, *, name: str = "") -> list:
    """Name a plan that is one shot repeated, after `spread_staging` had its chance."""

    text = [" ".join(str(item or "").split()) for item in (openings or [])]
    text = [item for item in text if item]
    if len(text) < 4:
        return []
    findings: list = []
    foreground = sum(1 for item in text if _FOREGROUND_RE.search(item))
    distant = sum(1 for item in text if _DISTANT_RE.search(item))
    facing = sum(1 for item in text if _FACING_CAMERA_RE.search(item))
    total = len(text)
    who = name or "the narrator"
    if foreground >= max(4, int(total * 0.6)):
        findings.append(
            f"{foreground} of {total} shots put {who} in the foreground, so the film is one "
            f"framing repeated with different audio; give the shots different distances and "
            f"angles, and put the described action at her scale"
        )
    if distant >= max(2, int(total * 0.15)):
        findings.append(
            f"{distant} of {total} shots push the described figures into the distant "
            f"background while {who} fills the frame; she is meant to be inside the action, "
            f"at the same depth and scale as what she describes"
        )
    if facing >= max(3, int(total * 0.34)):
        findings.append(
            f"{facing} of {total} shots have {who} facing the camera; a presenter who "
            f"addresses the lens in a third of the film reads as a studio segment, not a "
            f"documentary she is walking through"
        )
    return findings



#: Words that name a place, used to tell a beat that happens somewhere from a beat
#: that only describes the film.
PLACE_WORDS = (
    "kitchen", "corridor", "hallway", "church", "chapel", "altar", "sacristy", "street",
    "square", "courtyard", "doorway", "stairs", "tower", "forest", "clearing", "field",
    "bedroom", "cellar", "attic", "office", "stage", "rooftop", "balcony", "bridge",
    "river", "riverbank", "shore", "beach", "cave", "room", "house", "village", "town",
    "valley", "camp", "battlefield", "desert", "studio", "desk", "throne", "gate",
    "temple", "market", "hillside", "slope", "ridge", "well", "stream", "road", "path",
)

#: Verbs that mean somebody does something visible on screen. A beat built only from
#: "the documentary presents epic scenes that illustrate the narration" has none of
#: these, and a clip planned from it has nothing to show.
EVENT_ACTION_RE = re.compile(
    r"\b(walks?|walking|enters?|entering|kneels?|kneeling|stands?|standing|sits?|sitting|"
    r"turns?|turning|raises?|raising|lifts?|holds?|holding|carries?|carrying|throws?|"
    r"throwing|strikes?|striking|climbs?|climbing|runs?|running|opens?|closes?|"
    r"falls?|falling|rises?|arrives?|leaves?|steps?|crosses?|reaches?|touches?|"
    r"prays?|praying|fights?|fighting|draws?|aims?|shoots?|washes?|builds?|"
    r"sleeps?|wakes?|weeps?|cries?|laughs?|shouts?|calls?|answers?|greets?|"
    r"whispers?|plants|swings|releases|crouches?)\b",
    re.I,
)

#: Verbs that describe the film's narration rather than anything happening in it. A
#: brief written as a contract ("Anita narrates the story, showing and explaining what
#: is happening") is almost all of these, and must never be read as a shot list.
_TELLING_RE = re.compile(
    r"\b(narrat\w*|explain\w*|describ\w*|introduc\w*|illustrat\w*|present\w*|"
    r"transport\w*|documentary|documentaries|audience|viewer\w*|speaks?|speaking|"
    r"voice|narration)\b",
    re.I,
)


def beat_states_an_event(text: str) -> bool:
    """True when a beat says somebody does something somewhere, not what the film is.

    A brief can read as a story ("Dawn-grey kitchen: Abuela Rosa lifts the cup") or as
    a contract for making one ("The documentary presents epic biblical scenes that
    visually illustrate what is being narrated"). Only the first gives a planner a
    shot; a film planned from the second is invented from nothing, which is the
    wandering measured on a real 42-clip project.

    A leading label is dropped before the words are judged. "STANDING RULE: the narrator
    is inside every scene she describes" is the rule's name, not somebody standing, and
    reading it as an action turned the one correct shape for a narrated documentary into
    a shot list.
    """

    body = re.sub(r"^\s*[A-Z][A-Z ]{2,30}\s*:\s*", "", str(text or ""))
    if _TELLING_RE.search(body):
        return False
    return bool(EVENT_ACTION_RE.search(body))
