# Shot continuity, and the image review before the video

Two things were reported from use, and they turned out to be one story about the same moment: the
frame each clip starts from.

> "The workflow stops after generating images, but I'm unsatisfied with the result. The images have
> errors. I want to correct them by regenerating, but the system doesn't allow it... it says 'pipeline
> is still active, try again shortly'."

> "Tres de las 5 son exactamente iguales y dos tienen alucinaciones... deben tener una secuencia
> logica en las escenas del video no repetirse iguales, de otro modo genera siempre un primer cuadro
> en cada clip que se regresa el video al punto inicial perdiendo la coherencia con la imagen del
> final del clip anterior."

## What was measured

On a real four-shot project (`bar`, `video_strategy: rolling_window`, model `ltx2_22B_10eros`,
`uses_shot_images: true`):

| Where | What was found |
|---|---|
| The images | The prompt of shot 3 was **91.8% the same text** as shot 2's (`visual_changes` was empty on every shot), so the model returned the same frame twice. |
| The image source | Every shot carried `image_source: "original"`, so each start image was re-invented from the character anchor instead of continuing from the shot before it — even though shots 2, 3 and 4 declared `continuity_strategy: "continuous"`. |
| The stitch | `image_end_paths` and `per_clip_continue_from_previous` both required `BOUNDED_START_END` (MiniMax H3). With LTX and shot images, **upstream applied no continuity mechanism at all**, which is why every clip restarted the scene. |
| The review pause | It was gated on `not auto_mode`, and auto mode is on by default, so a run went from its images straight into video rendering. |
| A parked pipeline | A pause counted as "still active" to the reservation every Dashboard correction has to take: a paused pipeline, a live thread, and both together each answered `409 Pipeline is still active; try again shortly.` |
| Leaving the pause | `continuePipeline` was defined in the store and called by nothing, and the Dashboard's Resume button is offered for `crashed`, `failed` and `cancelled` runs — not paused ones. A parked run waited for a click that did not exist. |

## The review gate

`Settings`-independent and on by default: a run stops once, after it has generated a start image per
shot and **before** any video is rendered, and the Director panel offers **Continue to video**.
Turning the setting off restores the old behaviour (auto mode runs through without stopping).

```
                                     GET  /api/v1/director/review-gate
                                     POST /api/v1/director/review-gate
   {"enabled": true}                 GET  /api/v1/director/pipelines/<pid>/continuity
```

While the run waits there, the corrections the pause exists for work: **regenerate image** on a shot,
and the prompt and tag edits. A running (video-rendering) pipeline still refuses them, because by then
the video stage already holds the images it will render from — a rerun there would change a file on
disk and nothing else. Corrections made during the pause are re-read from the saved state when the run
resumes, so the video renders from the image the reviewer chose rather than the one they rejected.

The setting lives in `app/director_review_gate.json` (not committed: it is this machine's preference).

## The image chain

A shot the plan declares `continuous` (`continuous_strategy`, the planner's own judgement) is drawn
from the shot before it rather than from the character anchor — upstream's `image_source: "previous"`
path, which also passes the anchor as a second reference so identity is not lost. Before this, the
plan's `image_source` field was trusted on its own, and the planner wrote `"original"` on every shot
of a run whose own shots declared they continue each other: a plan that contradicts itself, resolved
in favour of the declaration the story needs.

## The stitch

A stitched shot's **own image is its first frame and the next shot's image is its last**, so two clips
meet on one frame instead of restarting. MiniMax H3's bounded strategy always worked this way; LTX 2
is now asked for it as well, because its engine reads an end frame.

`stitches_shots()` in `app/services/director/sequence_continuity.py` decides which families may be
asked. Today: `BOUNDED_START_END` (H3, unchanged) and any `ltx2*` architecture, verified by reading
`models/ltx2/ltx2.py`, which carries `image_end` into its conditioning entries at `_end_frame_idx`.
A family that is not named there keeps its previous behaviour: asking a model to end on a frame it
ignores is a stitch that only looks arranged.

### Verified on a real render, and the detail that makes it work

An end frame is **only used when `image_prompt_type` contains `E`** (`wgp.py`, `validate_settings`).
Measured on this machine, 512×288, 17–25 frames, 8 steps, `ltx2_22B_10eros`:

| `image_prompt_type` | first frame | last frame |
|---|---|---|
| not set (`image_start` and `image_end` given) | beige, no relation to either image | the same beige frame |
| `"SE"` | exactly the start image | exactly the end image |

The Director computes that type itself — `"SE" if has_any_start and has_any_end` — so populating the
end paths is enough for the engine to receive `SE`. The first row is why this table is here: a stitch
whose type is never set is a stitch that renders as if there were none, and nothing says so.

## The frame hand-over, instead of a similar-looking one

Reported from use: "no la secuencia que se supone deberia existir para que el video tenga una
continuidad logica de la ultima escena del clip anterior... no tiene sentido generar todas imagenes
desde el principio sino generar un clip y de ahi tomar la ultima imagen como referencia para el
siguiente".

That is the exact hand-off, and the queue already implements it. In a rolling-window multi-clip
batch, a clip that carries **no start image of its own** is given one extracted from the video the
clip before it produced:

```
  [Continuation] Extracted last frame for clip 3
```

The extraction skips the eight tail frames LTX distorts, and the clip's prompt type gains an `S` so
the frame is actually conditioned on. What was missing was only the trigger: the Director sent a
start image for every shot, so the hand-off never fired.

So for a shot the plan declares continuous, the Director no longer sends that shot's planned image as
its first frame, and the shot before it hands over the frame it really ended on. What does **not**
change is where each shot must end: the planned image is still the *destination* of the clip before
it. The storyboard becomes the landing point of every clip rather than the opening of one:

| | first frame | last frame |
|---|---|---|
| clip 1 | the planned image of shot 1 (the film opens there) | the planned image of shot 2 |
| clip 2 | **the frame clip 1 really ended on** | the planned image of shot 3 |
| clip 3 | **the frame clip 2 really ended on** | the planned image of shot 4 |
| last clip | the frame the clip before it really ended on | free, this is the ending |

Every planned image is still used exactly once, the review gate still shows all of them before any
render starts, and drift cannot accumulate: each clip starts from what was really produced and is
pulled back toward the planned frame by its end anchor.

`hands_over_the_frame()` decides which families may be asked. MiniMax H3's bounded strategy keeps the
planned start it has always used: its hand-over is opt-in per clip through its own flag and its
shot-image contract is upstream's tested path. A plan that declares nothing keeps its image too —
nothing is inferred from silence.

### What was verified, and what the next run confirms

Verified by reading the three code paths this depends on: the queue's hand-off above, the per-clip
trigger (`_continuation` is set for a clip with no start image in any non-bounded batch), and that a
Director run on this machine really is a multi-clip batch (its log shows `[Task 1/4]` … `[Task 4/4]`
for a four-shot project).

Not yet observed on a live Director run: an HTTP probe could not reproduce a multi-clip batch — it
always rendered one task — so the hand-over itself has not been seen firing here. The next Director
run confirms it in one line, `[Continuation] Extracted last frame for clip N`, and by comparing clip
N's first frame with clip N-1's last frame.


Consecutive shots whose image prompts are at least **85% the same text** are named before the renders
are spent — in the terminal and in the Director panel, from the saved plan rather than from memory:

```
[Continuity] Shots 2 and 3 carry 92% the same image prompt, so they will render as the same frame.
Correct one of them before rendering.
```

The threshold is the measured gap: the duplicated pair scored 91.8% and came back as one frame twice,
and the pair after it scored 74.5% and rendered differently. Nothing is rewritten — a prompt is the
planner's decision and the reviewer's, and the notice exists so that decision can be made at the
review instead of after paying for two identical clips.

## The neighbour of a corrected image

A stitched shot ends on the image of the shot after it, so replacing that image leaves the shot before
ending somewhere else: a visible jump, and one only re-rendering removes. `restale_neighbours()`
names that neighbour, and `rerun_clip_image` marks it — alongside the shot whose own image changed —
so a repair pass knows both clips are out of date.
