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

It is an option, off by default, and it is a permission rather than a decision: the project
says continuity between clips may be used, and the **plan** says where. A music video's cuts
are editorial, and turning it on changes how every clip renders. It does not touch the audio,
because it adds references rather than overlapping windows.

Measured before this existed, on a real 30-shot project: 0 of 30 shots carried a start image,
none declared a continuity strategy, and `_omni_sequence_continuity` was never set True
anywhere in the repository -- the Studio sequence endpoint only ever set it to False. So every
shot was rendered as its own scene, which is the reported "cortes duros sin continuidad de la
escena de un clip al siguiente".
"""

from __future__ import annotations

import difflib
from typing import Any, Mapping

from services.director_video_strategy import BOUNDED_START_END

__all__ = [
    "continuity_run",
    "duplicate_image_prompts",
    "restale_neighbours",
    "sequence_continuity_flags",
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
    return {"_omni_sequence_continuity": flags or True}


def _enabled(value: Any) -> bool:
    """A checkbox sends a boolean; a saved project's JSON may hold a string."""

    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)


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
