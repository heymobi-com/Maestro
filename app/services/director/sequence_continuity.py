"""Whether each clip carries the one before it.

Three mechanisms answer that, and which one a run can use depends on its model:

* **The shot-image chain.** With a start image per shot, a shot the plan declares ``continuous`` is
  generated *from the shot before it* rather than from the project's character anchor. Reported from
  use: "tres de las 5 son exactamente iguales... esas imagenes no sirven ya que deben tener una
  secuencia logica en las escenas del video no repetirse iguales". Re-inventing every frame from the
  same anchor gives the same frame back, and the scene starts over each time.
* **The stitch.** A shot's own image is its first frame and the *next* shot's image is its last, so
  the two clips meet on one frame instead of restarting the scene -- "se regresa el video al punto
  inicial perdiendo la coherencia con la imagen del final del clip anterior". MiniMax H3's bounded
  strategy has always done this; LTX 2 reads an end frame in the engine too, so it is asked of it.
* **The reference handoff**, below: H3's own late-frame reference, which is a per-project option.

Two continuity mechanisms exist, and only one of them is reachable for an H3 reference music
video:

* The final-frame handoff (``extend_previous`` plus a shared ``continuity_group``) lives
  entirely inside ``BOUNDED_START_END``. All three of its call sites require that strategy, so
  for ``omni_reference`` -- the strategy an H3 music video with character references uses -- it
  can never fire on its own. What this option does instead is read that same declaration, the
  one the planners already write, and answer it on the reference path with the frame the model
  can actually see.
* H3's own sequence continuity appends a late frame of the clip that was just rendered to the
  next clip's references, with the role "blocking, environment state, lighting, and screen
  direction only", and binds that ``<Picture N>`` in the next prompt. The engine path is
  complete; it only needs the job flag ``_omni_sequence_continuity``.

It is enabled by default in the Director reference-video UI and remains user-toggleable. It
is a permission rather than a decision: the project says continuity between clips may be
used, and the **plan** says where. The plan still owns the cuts, so independent shots remain
independent. It does not touch the audio, because it adds references rather than overlapping
windows.

Measured before this existed, on a real 30-shot project: 0 of 30 shots carried a start image,
none declared a continuity strategy, and `_omni_sequence_continuity` was never set True
anywhere in the repository -- the Studio sequence endpoint only ever set it to False. So every
shot was rendered as its own scene, which is the reported "cortes duros sin continuidad de la
escena de un clip al siguiente".
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Mapping

from services.director_video_strategy import BOUNDED_START_END, OMNI_REFERENCE

__all__ = [
    "carry_from_by_shot",
    "continuity_run",
    "duplicate_image_prompts",
    "hands_over_the_frame",
    "keyframe_steps",
    "offered_reference_modes",
    "place_continuity_groups",
    "reference_mode",
    "restale_neighbours",
    "sequence_continuity_flags",
    "shot_setups",
    "starts_to_keep",
    "stitches_shots",
]

OPTION = "_director_sequence_continuity"

# How many shots in a row may carry the one before. Without a bound the whole film becomes one
# continuous take, which is the opposite of the request: continuity where it is justified, and
# the cut where the story needs one.
MAX_RUN = 3

# What a shot's own ``continuity_strategy`` means here, in the vocabulary the planners already
# write: "continuous" is a shot that carries the world of the one before it on an ordinary cut
# inside one scene, and "extend_previous" is the same continuation asked for at its tightest.
# Both are served by the only frame this path can show the model -- a late frame of the previous
# clip, as a composition-only reference -- because a reference model renders without a start
# frame, so a frame it can see is the closest thing to a handoff it has. "independent" is a real
# cut: a new place, a new time, a new point of view.
#
# Measured on the same real project, which is why both halves are named here. Reading only
# "extend_previous" leaves the option with nothing that can ever trigger it: the planner's own
# guide tells it to use that value sparingly, for a literal continuation, which is the one thing
# this path cannot do. Reading only "continuous" makes it trigger everywhere, because that is the
# ordinary same-scene case. The two together are the request, and the plan decides where it is
# made.
_CONTINUES = frozenset({"continuous", "extend_previous"})
_CUTS = frozenset({"independent"})

_ENVIRONMENT_KEYS = ("environment", "_director_environment", "setting")
_SECTION_KEYS = ("section", "narrative_role", "scene_type")
# The camera setup a shot stages, in the order the plan states it: the structured field when the
# plan is in hand, and the label the opening blocking leads with when only the built shot is
# (``Hector's subjective POV.``, ``Reaction shot.``, ``Close-up.``, ``Wide shot.``).
_SETUP_KEYS = ("_director_camera_plan", "camera_plan")
_SETUP_LEADING = (
    "opening_blocking", "_director_opening_blocking",
    "spatial_setup", "_director_spatial_setup",
)
_SETUP_MAX_WORDS = 6
# The batch reads shot state from the persisted clip plan, where the planner's strategy is
# filed under the director-prefixed key. Accept both so a live plan and a resumed one agree.
_DECLARED_KEYS = ("continuity_strategy", "_director_continuity_strategy")
_GROUP_KEYS = ("continuity_group", "_director_continuity_group")


def continuity_run(clip_plans: Any) -> list[bool]:
    """For each shot, whether it carries the one before it.

    The plan decides. A shot declared ``continuous`` -- the ordinary shot inside one place and
    one moment -- carries the world of the one before it, and ``extend_previous`` asks for the
    same continuation at its tightest. A shot declared ``independent`` cuts, because that is a
    new place, time or point of view. That field is the planner's own judgement of what the
    story needs, which is the only thing that knows where a cut serves the idea and where it
    breaks it.

    Where the plan is silent -- a project planned before this option existed, or a planner that
    declares nothing -- only what the plan states plainly is used: two shots continue when they
    name the same ``continuity_group``, or when they describe one place in the same words
    inside one section, and the run has not yet reached ``MAX_RUN``. Nothing is inferred from
    similar wording: measured on a real 30-shot project that restated one warehouse in fresh
    prose in every shot, a looser test decided those shots on the length of their descriptions,
    and a wrong carry drags the previous shot's world into the new one. Where the plan has not
    said, a cut is the honest answer. The bound is a floor under the fallback, not a ceiling
    over the plan: what a planner asks for is an author's decision, and it is written into the
    plan where it can be read and argued with.

    The first shot has nothing to carry.
    """

    plans = [plan for plan in (clip_plans or []) if isinstance(plan, Mapping)]
    flags: list[bool] = []
    run = 0
    previous: Mapping[str, Any] | None = None
    for plan in plans:
        if previous is None:
            flags.append(False)
            run = 1
            previous = plan
            continue
        declared = _text(plan, _DECLARED_KEYS)
        if declared in _CONTINUES:
            carries = True
        elif declared in _CUTS:
            carries = False
        else:
            declared_group = _same_declared_group(plan, previous)
            carries = bool(
                (
                    declared_group
                    if declared_group is not None
                    else _same_stated_place(plan, previous)
                )
                and run < MAX_RUN
            )
        flags.append(carries)
        run = run + 1 if carries else 1
        previous = plan
    return flags


def _text(plan: Mapping[str, Any], names: tuple[str, ...]) -> str:
    for name in names:
        value = plan.get(name)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split()).casefold()
    metadata = plan.get("metadata")
    if isinstance(metadata, Mapping):
        for name in names:
            value = metadata.get(name)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split()).casefold()
    return ""


def _section(plan: Mapping[str, Any]) -> str:
    metadata = plan.get("metadata")
    if isinstance(metadata, Mapping):
        value = metadata.get("section")
        if isinstance(value, str) and value.strip():
            return value.strip().casefold()
    return _text(plan, _SECTION_KEYS)


def _same_declared_group(
    plan: Mapping[str, Any], previous: Mapping[str, Any],
) -> bool | None:
    """The plan's own name for a place and a time, or ``None`` when it named none.

    ``continuity_group`` is a statement about the film rather than a guess about wording, so
    when both shots carry one it decides on its own -- including when it says the two shots are
    different scenes that happen to be described in the same words.
    """

    group = _text(plan, _GROUP_KEYS)
    other = _text(previous, _GROUP_KEYS)
    if not group or not other:
        return None
    return group == other


def _same_stated_place(plan: Mapping[str, Any], previous: Mapping[str, Any]) -> bool:
    """Whether both shots describe one place in the same words, inside one section."""

    place = _text(plan, _ENVIRONMENT_KEYS)
    return bool(
        place != ""
        and place == _text(previous, _ENVIRONMENT_KEYS)
        and _section(plan) == _section(previous)
    )


def sequence_continuity_flags(
    params: Mapping[str, Any] | None,
    director_strategy: Any,
    *,
    omni_reference: str,
    clip_plans: Any = None,
) -> dict[str, Any]:
    """The engine flags telling each clip whether to carry the one before it.

    Gated on the reference strategy because the mechanism is H3's: appending a composition
    frame means adding a ``<Picture N>`` reference, which only exists on that path. A caller
    with no shots in hand asks for every clip and gets that; the batch asks with its plan and
    gets the plan's answer, one flag per clip.
    """

    values = params or {}
    if not _enabled(values.get(OPTION)):
        return {}
    if str(director_strategy or "").strip() != str(omni_reference).strip():
        return {}
    flags = continuity_run(clip_plans) if clip_plans else []
    return {
        "_omni_sequence_continuity": flags or True,
        # Which earlier clip's frame each returning shot resumes from.
        "_omni_sequence_carry_from": carry_from_by_shot(clip_plans) if clip_plans else [],
    }


def shot_setups(shot_dicts: Any) -> list[str]:
    """The camera setup each shot stages, as one comparable word per shot.

    A shot that keeps the setup of the one before it is a continuation of that take, and a shot
    that returns to a setup an earlier shot used is a return. Both are read here, because the
    plan states the setup in plain words: measured on a real 17-shot project, the openings read
    ``Wide shot.``, ``Hector's subjective POV.``, ``Reaction shot.``, ``Close-up.`` and cycled
    through them. An empty word means the plan did not say, and the shot is then treated as
    keeping the setup it had -- the safe reading, because it never restarts a take.
    """

    setups: list[str] = []
    for shot in shot_dicts or []:
        if not isinstance(shot, Mapping):
            setups.append("")
            continue
        setups.append(_setup_words(shot))
    return setups


def _setup_words(shot: Mapping[str, Any]) -> str:
    for key in _SETUP_KEYS:
        camera = shot.get(key)
        if not isinstance(camera, Mapping):
            continue
        stated = " ".join(
            str(camera.get(field) or "")
            for field in ("framing", "angle", "movement")
        )
        stated = re.sub(r"\s+", " ", stated).strip().casefold()
        if stated:
            return stated
    for key in _SETUP_LEADING:
        text = re.sub(r"\s+", " ", str(shot.get(key) or "")).strip()
        if not text:
            continue
        lead = re.sub(r"[^A-Za-z0-9' ]+", " ", text.split(".", 1)[0]).strip().casefold()
        if lead and len(lead.split()) <= _SETUP_MAX_WORDS:
            return lead
    return ""


def carry_from_by_shot(clip_plans: Any) -> list[int | None]:
    """Which earlier shot's final frame each returning shot should resume from.

    A shot that returns to a setup an earlier shot already staged -- the ordinary shot/reverse-shot
    rhythm, and the case measured on a real 17-shot project where ``Hector's subjective POV`` ran
    at clips 2, 5, 6, 8, 9, 11 and 13 -- is handed the frame the last shot *of that same setup* ended
    on, instead of the frame of whatever shot happens to sit immediately before it. Without it the
    return is composed from the character photographs again and re-stages the tableau it had already
    left: the reported "avanzan tres centímetros y vuelven al mismo loop desde ese otro punto".

    ``None`` means the ordinary rule -- resume from the shot immediately before -- which is what
    every shot whose setup is new or unchanged keeps. Positions are 1-based, the way the batch
    numbers its clips, because that is the number the engine names a clip's frame with.
    """

    plans = [plan for plan in (clip_plans or []) if isinstance(plan, Mapping)]
    setups = shot_setups(plans)
    carries: list[int | None] = []
    last_seen: dict[str, int] = {}
    for index, plan in enumerate(plans):
        position = index + 1
        setup = setups[index] if index < len(setups) else ""
        declared = next(
            (
                _normalise_declared(plan.get(key))
                for key in _DECLARED_KEYS
                if _normalise_declared(plan.get(key))
            ),
            "",
        )
        continues = declared in _CONTINUES or (
            not declared and index > 0 and (not setup or setup == setups[index - 1])
        )
        earlier = last_seen.get(setup) if setup else None
        carries.append(
            earlier if continues and earlier is not None and earlier != position else None
        )
        if setup:
            last_seen[setup] = position
    return carries


def _normalise_declared(value: Any) -> str:
    return str(value or "").strip().casefold()


def place_continuity_groups(shot_dicts: Any) -> None:
    """One place, stated in the same words, is one continuity group.

    The plan numbers its groups per shot: measured on a real 17-shot project, the whole film
    happens in one warehouse at night, its ``environment`` is byte-identical in all seventeen
    shots, and the plan still wrote ``warehouse_piano_1`` ... ``warehouse_piano_11``. Every
    group boundary forces ``independent`` -- a hard cut that carries nothing -- so eleven of
    those shots re-staged the same tableau from the same four reference photos, which is the
    reported "los clips siguen siendo similares entre si".

    The words the plan uses for the place are what survives the numbering, so a group is reused
    while they do not change. A shot that moves somewhere new states a new place, and keeps its
    cut.
    """

    previous: Mapping[str, Any] | None = None
    previous_place = ""
    for shot in shot_dicts or []:
        if not isinstance(shot, dict):
            previous, previous_place = None, ""
            continue
        place = _place_words(shot)
        if previous is not None and place and place == previous_place:
            previous_group = str(previous.get("continuity_group") or "").strip()
            if previous_group:
                shot["continuity_group"] = previous_group
        previous, previous_place = shot, place


def _place_words(shot: Mapping[str, Any]) -> str:
    """The place this shot states, in one comparable spelling."""

    for key in _ENVIRONMENT_KEYS:
        value = re.sub(r"\s+", " ", str(shot.get(key) or "")).strip()
        if value:
            return value.casefold()
    return ""


def _enabled(value: Any) -> bool:
    """A checkbox sends a boolean; a saved project's JSON may hold a string."""

    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)


# The two reference formats a family can offer, in the words its own menu uses. "Main image /
# landscape first" (KI) makes the first image the compositional source a generation edits; "Reference
# images" (I) hands the same images over as subjects. Reported from use: "en cada imagen metio la
# misma pose de la referencia sin ninguna variacion... las imagenes de referencia son de referencia
# para que invente los escenarios y personajes y mantenga consistencia pero no para que los copie
# exactamente". The Director preferred KI wherever a family offered it, which is what made every
# generated start image a copy of the one it was handed, and Qwen Image 2.1 even declares I as its
# own default.
_MAIN_IMAGE_MODE = "KI"
_REFERENCE_MODE = "I"


def offered_reference_modes(model_def: Mapping[str, Any] | None) -> set[str]:
    """The reference formats a family's own menu offers."""

    config = (model_def or {}).get("image_ref_choices")
    if not isinstance(config, Mapping):
        return set()
    choices = config.get("choices") or []
    if not isinstance(choices, (list, tuple)):
        return set()
    modes: set[str] = set()
    for choice in choices:
        if isinstance(choice, (list, tuple)) and len(choice) >= 2:
            modes.add(str(choice[1] or ""))
        elif isinstance(choice, Mapping) and choice.get("value") is not None:
            modes.add(str(choice.get("value") or ""))
    return modes


def reference_mode(resolved: Any, get_model_def: Any = None) -> str:
    """Which reference format the Director's generated images are submitted in.

    "Reference images" when the family offers it, because a reference is there to keep the cast and
    the world consistent while the text invents the frame. A family that offers only the main-image
    format keeps it -- z_image and flux_uso declare no reference mode at all, and handing them letters
    their own menu does not list is a broken payload rather than a preference.

    ``get_model_def`` is the caller's own registry accessor, passed in rather than imported: this
    module must not import the engine, which exits the process when its dependency is missing.
    """

    assessment = {}
    if isinstance(resolved, tuple) and len(resolved) == 2:
        assessment = resolved[1] or {}
    elif isinstance(resolved, Mapping):
        assessment = resolved
    chosen = str((assessment or {}).get("image_reference_mode") or "")
    if chosen != _MAIN_IMAGE_MODE or not callable(get_model_def):
        return chosen
    try:
        model_type = resolved[0] if isinstance(resolved, tuple) else ""
        offered = offered_reference_modes(get_model_def(model_type) or {})
    except Exception:
        return chosen  # an unreadable registry keeps the format the caller already chose
    return _REFERENCE_MODE if _REFERENCE_MODE in offered else chosen


# Engine families whose video path reads an end frame. Verified by reading them rather than assumed:
# models/ltx2/ltx2.py carries ``image_end`` into its conditioning entries and the frame index it
# lands on, the same way MiniMax H3 does. A family that does not appear here keeps the behaviour it
# had, because asking a model to end on a frame it ignores is a stitch that only looks arranged.
_END_FRAME_ARCHITECTURES = ("ltx2",)

# How alike two consecutive image prompts have to be before they are the same frame. Measured on the
# project this exists for: the duplicated pair scored 91.8% and rendered as one frame twice, and the
# pair after it scored 74.5% and rendered differently.
_DUPLICATE_PROMPT_RATIO = 0.85


def stitches_shots(director_strategy: Any, model_def: Mapping[str, Any] | None) -> bool:
    """Whether a shot may be told where it must end, so the next one starts on that frame.

    With a start image per shot this is the only thing that makes consecutive clips meet: shot N is
    rendered from its own image to the image of shot N+1, so the join is one frame rather than two
    different ones. The plan still decides *where* -- a shot the plan cuts from is not stitched to
    anything.
    """

    if str(director_strategy or "").strip() == BOUNDED_START_END:
        return True  # H3's bounded strategy stitches its shots, and always has
    architecture = str((model_def or {}).get("architecture") or "").strip().lower()
    return architecture.startswith(_END_FRAME_ARCHITECTURES)


def hands_over_the_frame(
    director_strategy: Any, model_def: Mapping[str, Any] | None,
) -> bool:
    """Whether the engine itself starts a shot from the frame the shot before it really ended on.

    Read from the queue rather than guessed. In a rolling-window multi-clip batch, a clip that
    carries no start image of its own is given one extracted from the video the clip before it
    produced -- skipping the eight tail frames LTX distorts -- and its prompt type gains an ``S``.
    That is the exact hand-off rather than a similar-looking one, and it costs no image generation.

    MiniMax H3's bounded strategy opts in per clip through its own flag, and its planned-start
    contract is upstream's tested path, so it keeps the start it has always used.
    """

    strategy = str(director_strategy or "").strip()
    if strategy in {BOUNDED_START_END, OMNI_REFERENCE}:
        return False
    return bool((model_def or {}).get("sliding_window"))


def starts_to_keep(
    clip_plans: Any, director_strategy: Any, model_def: Mapping[str, Any] | None,
) -> list[bool]:
    """For each shot, whether its planned image is still the first frame it renders from.

    A shot the plan declares continuous does not keep it: the shot before it hands over the frame it
    really ended on. Reported from use: "no la secuencia que se supone deberia existir para que el
    video tenga una continuidad logica de la ultima escena del clip anterior" -- generating every
    start image up front can only approximate that, because shot N+1's image is drawn from shot N's
    *initial* frame rather than from the frame it ended on.

    The planned image is still used, as the shot before this one's last frame: the storyboard becomes
    each clip's destination, and every planned frame appears in the film exactly once. Returns one
    flag per shot, all true when the family does not hand frames over -- and an unreadable plan keeps
    its image, because a cut is the honest answer where the plan said nothing.
    """

    plans = list(clip_plans or [])
    flags = [True] * len(plans)
    if not hands_over_the_frame(director_strategy, model_def):
        return flags
    aligned = [plan if isinstance(plan, Mapping) else {} for plan in plans]
    for index, carried in enumerate(continuity_run(aligned)):
        if index < len(flags):
            flags[index] = not carried
    return flags


# How much a keyframe has to differ from the one before it before the clip between them has
# something to travel. Measured on a real project: consecutive frames that scored 6-12% of pixels
# changing materially rendered as an almost static step, and the whole five-shot sequence differed
# from its first frame by 3-13%. This is a readout, not a verdict: the number is measured on the
# images themselves, so the reviewer sees which joins are nearly the same frame.
_KEYFRAME_STEP_PERCENT = 10.0

# A pixel counts as changed when any channel moves this much. Chosen so noise and re-encoding do not
# count, while a moved limb does.
_CHANGED_PIXEL_DELTA = 40


def _small_rgb(path: str):
    """A 256-pixel-wide RGB array for comparison, or None when the file cannot be read."""

    import os

    if not path or not os.path.isfile(path):
        return None
    try:
        import numpy as np
        from PIL import Image

        with Image.open(path) as image:
            return np.asarray(image.convert("RGB").resize((256, 256)), dtype=np.float32)
    except Exception:
        return None  # a missing reader is not a reason to fail a run


def keyframe_steps(images: Any, out_dir: str, threshold: float = _KEYFRAME_STEP_PERCENT) -> list[dict]:
    """How far each keyframe moved from the one before it, measured on the images themselves.

    Reported from use: "veo imagenes de inicio muy similares entre un clip y otro... genera siempre la
    imagen del primer clip con una minima o ninguna variacion en todas las demas imagenes". Measured
    on that run: consecutive keyframes differed by 6-13% of pixels, so the clip between two of them
    had almost nothing to render.

    Nothing is decided here -- a small step is sometimes right, when the shot is a held moment. What
    the readout does is measure the join and name the ones that are nearly the same frame, so the
    reviewer can redo an image or its prompt before the render is paid for.
    """

    import os

    names = list(images or [])
    findings: list[dict] = []
    previous = None
    for index, name in enumerate(names):
        text = str(name or "")
        path = text if os.path.isabs(text) else os.path.join(out_dir, text)
        current = _small_rgb(path) if text else None
        if previous is not None and current is not None:
            delta = abs(previous - current).max(axis=2)
            change = float((delta > _CHANGED_PIXEL_DELTA).mean() * 100)
            findings.append({
                "after_shot": index,
                "shot": index + 1,
                "change": round(change, 1),
                "still": change < threshold,
            })
        previous = current
    return findings


def duplicate_image_prompts(
    clip_plans: Any, threshold: float = _DUPLICATE_PROMPT_RATIO, announce: bool = True,
) -> list[dict[str, Any]]:
    """Consecutive shots whose image prompts are alike enough to render the same frame.

    The plan is what is read, and nothing is rewritten here: a prompt is the planner's decision and
    the reviewer's. What this does is say so *before* the renders are spent -- in the terminal, and
    in the project's own state for the control that shows it -- so the pair can be corrected at the
    review instead of after paying for two identical clips.

    Returns one entry per pair: the shot, the shot before it, and how alike the two read. A caller
    that only wants the answer (the control, which asks on every poll) passes ``announce=False``
    and gets it without a line per poll in the log.
    """

    plans = [plan for plan in (clip_plans or []) if isinstance(plan, Mapping)]
    findings: list[dict[str, Any]] = []
    for index in range(1, len(plans)):
        before = str(plans[index - 1].get("image_prompt") or "")
        after = str(plans[index].get("image_prompt") or "")
        if not before or not after:
            continue
        ratio = difflib.SequenceMatcher(None, before, after).ratio()
        if ratio < threshold:
            continue
        findings.append({
            "shot": index + 1,
            "after_shot": index,
            "similarity": round(ratio, 3),
        })
        if announce:
            print(
                f"[Continuity] Shots {index} and {index + 1} carry {ratio * 100:.0f}% the same image "
                "prompt, so they will render as the same frame. Correct one of them before rendering."
            )
    return findings


def restale_neighbours(state: Mapping[str, Any], clip_index: int) -> list[int]:
    """The shots whose rendered video stops matching once ``clip_index``'s image is replaced.

    A stitched shot ends on the image of the shot after it, so replacing that image leaves the shot
    before ending somewhere else: a visible jump the reviewer did not ask for, and one that only
    re-rendering removes. The shot whose image changed is marked by its own caller; this names the
    neighbour that has to be marked with it, and names none when the project does not stitch.
    """

    if clip_index <= 0:
        return []
    profile = state.get("video_execution_profile")
    if not isinstance(profile, Mapping):
        return []
    if not stitches_shots(
        profile.get("video_strategy"), {"architecture": profile.get("architecture")},
    ):
        return []
    clips = state.get("clips")
    if not isinstance(clips, list) or len(clips) <= clip_index:
        return []
    before, after = clips[clip_index - 1], clips[clip_index]
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return []
    # The declaration is filed on the planned clip and, for a live plan, on the clip itself; read
    # both so a resumed project and a running one answer the same way.
    carried = continuity_run([
        {**(before.get("planned_clip") or {}), **before},
        {**(after.get("planned_clip") or {}), **after},
    ])
    return [clip_index - 1] if len(carried) == 2 and carried[1] else []
