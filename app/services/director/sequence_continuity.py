"""Whether each clip carries the one before it, as a per-project option.

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

from typing import Any, Mapping

__all__ = ["continuity_run", "sequence_continuity_flags"]

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
