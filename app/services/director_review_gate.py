"""Stopping before the video, so the images can be corrected first.

Reported from use: "The workflow stops after generating images, but I'm unsatisfied with the result.
The images have errors. I want to correct them by regenerating, but the system doesn't allow it...
when I click regenerate image, it says 'pipeline is still active, try again shortly'. So, if I can't
correct the initial image for the video, that is going to make a defective video as well."

Three things were true at once, and each one alone was enough to lose the run:

- **The gate never opened.** The pause that exists for exactly this decision -- ``pause_reason
  "review_images"`` -- only fired when auto mode was off, and auto mode is on by default, so a run
  went from the freshly generated images straight into video rendering.
- **A parked pipeline refused every correction.** While it waits for the reviewer its thread is
  blocked in ``_wait_for_resume`` and its status is ``paused``, and both of those count as "still
  active" to the reservation a Dashboard mutation has to take. Measured on this machine: a paused
  pipeline, a live thread, and a paused pipeline with a live thread each answered ``409 Pipeline is
  still active; try again shortly.`` to ``reroll-image``. A gate nobody can act behind is not a gate.
- **Nothing could leave it.** ``continuePipeline`` is defined in the interface and called by nothing,
  and the Dashboard's Resume button is offered for ``crashed``, ``failed`` and ``cancelled`` -- not
  for ``paused``. A parked pipeline therefore waited for a click that did not exist.

One more thing had to be true for the gate to mean anything: **the correction has to reach the
render.** The pipeline holds the images it generated in a local list and hands that list to the video
stage, while a correction writes the saved state, so resuming without re-reading would render the
video from the image the reviewer had just rejected -- and the save that the next phase boundary
performs would write the old name back over the correction.

So the gate is a setting (on by default, which is the point of it), a paused pipeline is open to the
corrections the pause exists for, and the images are re-read on resume. It is deliberately not a
timeout: the decision belongs to the person who asked for the gate, and proceeding on a timer would
render the video from images nobody approved.
"""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, HTTPException

__all__ = [
    "enabled",
    "parked",
    "reviewed_images",
    "router",
    "set_enabled",
    "should_pause",
    "state",
]

router = APIRouter()

# Our own file, in our own name: this is a machine-local preference, not something the shared
# application settings should carry, and the tests replace the path instead of reading the user's.
_CONFIG_NAME = "director_review_gate.json"
_SETTING = "review_images_before_video"

# The pauses that mean "a person is looking at this run right now". They are the two upstream review
# gates; both park the pipeline in _wait_for_resume with the generation slot free.
_REVIEW_PAUSES = ("review_images", "review_prompts")

_PIPELINE_FILE_PREFIX = "_director_pipeline_"


def _config_path() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), _CONFIG_NAME)


def enabled() -> bool:
    """Whether a run stops after its shot images and waits for the reviewer.

    On unless it was turned off: the images a run just invented for each shot are the last thing a
    person can judge before hours of video rendering spend themselves, and the cost of stopping is
    one click.
    """

    try:
        with open(_config_path(), encoding="utf-8") as handle:
            value = json.load(handle).get(_SETTING)
    except Exception:
        return True  # no file, unreadable, or not JSON: the default is on
    return True if value is None else bool(value)


def set_enabled(value) -> dict:
    """Remember the setting, and answer with what was stored."""

    keep = bool(value)
    data: dict = {}
    try:
        with open(_config_path(), encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            data = loaded
    except Exception:
        data = {}  # a missing or damaged file is replaced, not treated as an error
    data[_SETTING] = keep
    try:
        with open(_config_path(), "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
    except Exception as exc:
        print(f"[Review gate] could not save the setting: {exc}")
    return {"enabled": keep}


def should_pause(auto_mode: bool) -> bool:
    """Whether the image review gate opens for this run.

    Auto mode used to skip it, which is how a run reached the video stage without anyone looking at
    the images. The gate is now asked first, so auto mode means "do not stop me for the plan" and the
    images are still shown unless the setting says otherwise. A run that was never going to stop (auto
    mode off) keeps stopping, because that is upstream's own review.
    """

    return enabled() or not auto_mode


def parked(pipeline) -> bool:
    """Whether this pipeline is waiting at a review, with its thread parked and no media moving."""

    if not isinstance(pipeline, dict):
        return False
    if str(pipeline.get("status") or "") != "paused":
        return False
    return str(pipeline.get("pause_reason") or "") in _REVIEW_PAUSES


def _pipeline_file(pid: str, out_dir: str) -> str:
    """The saved state for this pipeline, under the output folder or a workspace inside it."""

    name = f"{_PIPELINE_FILE_PREFIX}{pid}.json"
    direct = os.path.join(out_dir, name)
    if os.path.isfile(direct):
        return direct
    try:
        for entry in os.listdir(out_dir):
            candidate = os.path.join(out_dir, entry, name)
            if os.path.isfile(candidate):
                return candidate
    except OSError:
        pass
    return ""


def _saved_images(pid: str, out_dir: str) -> list:
    """The start image each clip records right now, or [] when the state cannot be read."""

    path = _pipeline_file(pid, out_dir)
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    except Exception as exc:
        print(f"[Pipeline {pid}] Review: could not read the saved images: {exc}")
        return []
    clips = state.get("clips")
    if not isinstance(clips, list):
        return []
    return [str((clip or {}).get("start_image_filename") or "") for clip in clips]


def _resolves(filename: str, out_dir: str) -> bool:
    """Whether a recorded name still points at a file, absolute or relative to the output folder."""

    if not filename:
        return False
    return os.path.isfile(filename) or os.path.isfile(os.path.join(out_dir, filename))


def reviewed_images(pid: str, clip_images: list, out_dir: str) -> list:
    """The images this run should render from, after any correction made while it waited.

    Returns the list it was given when nothing changed, so the caller can compare and skip its own
    bookkeeping. A recorded name that no longer points at a file is not a correction: a stale entry
    would fail the video stage with a missing input instead of rendering what the reviewer chose.
    """

    current = [str(name or "") for name in (clip_images or [])]
    saved = _saved_images(pid, out_dir)
    if not saved:
        return current
    corrected = list(current)
    changed = []
    for index, name in enumerate(current):
        if index >= len(saved):
            break
        replacement = saved[index]
        if not replacement or replacement == name:
            continue
        if not _resolves(replacement, out_dir):
            print(
                f"[Pipeline {pid}] Review: clip {index + 1} records an image that is gone "
                f"({replacement}); keeping the one that exists"
            )
            continue
        corrected[index] = replacement
        changed.append(index + 1)
    if changed:
        print(
            f"[Pipeline {pid}] Review: rendering from the corrected image"
            f"{'s' if len(changed) != 1 else ''} for shot"
            f"{'s' if len(changed) != 1 else ''} {', '.join(str(i) for i in changed)}"
        )
    return corrected


def state() -> dict:
    """What the control needs to draw itself: this setting, and nothing else."""

    return {"enabled": enabled()}


def continuity(pid: str, out_dir: str) -> dict:
    """What the plan of this project says about its own shots, for the control that shows it.

    Read from the saved plan rather than remembered at generation time, so the answer is the same
    before a run, at the review, and after it -- and so a corrected prompt stops being reported the
    moment it is corrected.
    """

    from services.director import sequence_continuity

    path = _pipeline_file(pid, out_dir)
    if not path:
        return {"shots": 0, "duplicates": []}
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    except Exception as exc:
        return {"shots": 0, "duplicates": [], "error": str(exc)}
    clips = state.get("clips")
    if not isinstance(clips, list):
        return {"shots": 0, "duplicates": []}
    plan = [clip for clip in clips if isinstance(clip, dict)]
    return {
        "shots": len(plan),
        "duplicates": sequence_continuity.duplicate_image_prompts(plan, announce=False),
    }


@router.get("/api/v1/director/review-gate")
def review_gate_state():
    return state()


@router.get("/api/v1/director/pipelines/{pid}/continuity")
def pipeline_continuity(pid: str):
    base = _output_dir()
    return continuity(pid, base)


def _output_dir() -> str:
    """Where the projects live: the application's own setting, read from its config file.

    Read from the file rather than through ``wgp.server_config`` on purpose: importing that module
    from a service exits the process when its engine dependency is missing, which is how a test
    suite once lost thirty tests to a single import.
    """

    base = os.path.dirname(_config_path())
    folder = "outputs"
    try:
        with open(os.path.join(base, "wgp_config.json"), encoding="utf-8") as handle:
            value = json.load(handle).get("save_path")
        if isinstance(value, str) and value.strip():
            folder = value.strip()
    except Exception:
        pass  # no readable config: the application's own default is what the pipeline uses
    return folder if os.path.isabs(folder) else os.path.join(base, folder)


@router.post("/api/v1/director/review-gate")
def review_gate_set(body: dict | None = None):
    if not isinstance(body, dict) or "enabled" not in body:
        raise HTTPException(
            status_code=400,
            detail="Body must be a JSON object with an 'enabled' field",
        )
    return set_enabled(body.get("enabled"))
