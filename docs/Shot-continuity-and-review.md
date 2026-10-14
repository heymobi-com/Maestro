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

## The images are not drawn from each other, and that was a correction

An earlier version of this drew a `continuous` shot from the shot before it (upstream's
`image_source: "previous"` path), to stop each shot from being re-invented from the character anchor.
It shipped, and it was wrong. Reported from use: "en cada imagen metio la misma pose de la referencia
sin ninguna variacion y la siguiente imagen esta mas distorionada que la anterior, como si hicera
copia de la copia". Measured on that run:

| | difference from the first keyframe |
|---|---|
| shot 2 | 1.8% of the frame |
| shot 3 | 4.1% of the frame |

The shots' own image prompts differed by 25% and 67%, so the prompts were asking for different moments
and the model was ignoring them: it was reproducing the frame it had been handed, and each generation
added its own artefacts to the previous one's, with nothing to reset them. The same project without
chaining had produced 6-13% between keyframes.

So the images are drawn from the character anchor and the reference photographs again, one generation
away from a good reference each time, and continuity is the video's job — the shot before ends on this
image and this shot starts from the frame that shot really ended on. Two ways to keep the film
continuous, and only the one that does not compound belongs in the image stage.

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


## What holds the images back, measured

Reported after the first attempt: "veo imagenes de inicio muy similares entre un clip y otro... genera
siempre la imagen del primer clip con una minima o ninguna variacion en todas las demas imagenes".

It is not the first image or the first prompt: replaying the pipeline's exact recipe (six references,
four steps, the same seed) reproduced its frame pixel for pixel. What the measurement shows is how
little the image model moves, and which reference does the holding. Variation against the first
keyframe of a real run, same seed, 1280×720:

| references sent | pixels changed materially |
|---|---|
| the pipeline's set: previous frame + anchor + 3 character photos + location | 11.8% |
| **previous frame + anchor only** | **18.6%** |
| previous frame only, 12 steps instead of 4 | 19.1% |
| previous frame + the character photos + location (no anchor) | 7.9% |
| no previous frame: anchor + character photos + location | 9.5% |

Two things follow. The **character reference photos are the strongest grip** — a photo pins a pose,
and the frame a shot is drawn from already contains those people, their wardrobe and the room, so the
photos only pin the pose back: sending them alongside the previous frame changes *less* than the
pipeline's own output did (7.9% against 11.8%).

**The reference format was the deeper cause.** A family's own menu offers two formats, and they are
named exactly for this difference:

```json
{"choices": [["None", ""],
             ["Main image / landscape first", "KI"],   <- the first image is a composition to edit
             ["Reference images", "I"]],               <- the images are subjects
 "default": "I"}
```

That is Qwen Image 2.1's, and its own default is `I`. The Director preferred `KI` wherever a family
offered it, which is what made a generated start image a copy of the image it was handed — the anchor
for an unChained shot, the previous frame for a chained one. `I` is what the requirement asks for:
reported from use, "las imagenes de referencia son de referencia para que invente los escenarios y
personajes y mantenga consistencia pero no para que los copie exactamente". So the Director asks for
the reference format when the family offers one and keeps `KI` when it does not (`z_image` and
`flux_uso` declare no reference mode, and letters their own menu does not list are a broken payload,
not a preference).

### Where each clip travels from

A keyframe pair that barely differs leaves the clip between them with almost nothing to move through.
The review control measures it on the generated images themselves — the share of pixels that moved
materially from one keyframe to the next — and names the joins that are nearly one frame:

```
Shots 3 and 4 differ by 6% of the frame, so the clip between them has almost nothing to move
through. Regenerate one of them, or widen the change its prompt describes.
```

The number is a readout, not a verdict: a held moment is a legitimate small step. A pair differing by
less than 10% is named, a boundary whose two frames cannot both be read is not reported at all (an
unmeasurable step is not a step), and the measurement never fails a run. It reads the same plan the
panel already fetches, so a corrected image stops being reported the moment it is corrected.

## Two shots that would render one frame

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

## A model entry that saves black frames

Reported as "it generated nothing, but it doesn't mark any error and I see no image".

| Where | What was found |
|---|---|
| The files | They were saved. `Qwen Image Edit GGUF Q4_K_M 20B` wrote 1296x704 JPEGs of 17.6 KB whose 2,737,152 pixels are **exactly zero** in R, G and B. No error, no warning, in any log. |
| The recipe | Four runs and four seeds, at 4 steps and at 40: all absolute black. The 4 steps were the *lightning* sibling's recipe; the GGUF definition declares no steps and no guidance at all, so the UI kept the value it remembers per model. |
| The file | Complete: 1,934 tensors, 60 blocks, `img_in` [64, 3072] against a config of 24 heads x 128. It loads and pins 12,620 MB. |
| The VAE | Healthy, and it is what rules the decoder out: it loads with 0 missing tensors and decodes a synthetic latent into a 704x1296 tensor spanning -1 to 1. A decoder whose biases are non-zero cannot emit exact zeros. |
| The discriminator | The fp8 safetensors of the **same architecture** (`qwen_image_edit_2511_20B_fp8_lightning_4step`) draws a valid 1296x704 image from the same reference photo, prompt, 4 steps and cfg 1: 41.8 s per image. |

The architecture, the VAE, the text encoder, the reference and the recipe are all fine. What does not
reach the compute path is the GGUF container: only `krea2` ships a GGUF mapping
(`shared/utils/gguf_mapping.py` plus its own name map), while the qwen family hands the `.gguf` to the
generic loader with a fused-qkv split map and nothing else. Use the fp8 entry, not the GGUF one.

## A schema that never reached the writer

Reported as "I tried to generate prompts for H3 Omni with Director and it is giving me errors", and
then, importantly, "I tried Gemma and Strata, so this is more systemic than just Strata".

| Where | What was found |
|---|---|
| The log | Four `400` per run: `response_format schema must accept only JSON objects at its root (type object, or anyOf/oneOf of object schemas)`. The H3 shot list, the H3 table read, the H3 voice bible and the generic retry schema all constrain an **array**, and llama.cpp compiles an array root happily, so the grammar had always crossed the local path and never crossed this one. |
| The consequence | The call fell back to unconstrained, and the plan came back as **37 shots where 16-22 were asked for**. 37 x ~865 tokens reached the 32000-token ceiling exactly: `reasoning=0 tokens, answer=32000 tokens, finish=length`, 148,599 characters. The last shot was cut mid-object, so `json_repair` salvaged it as an empty 37th item and completeness failed. |
| Gemma is not exempt | Older logs show the same truncation with `budget=4096` (thinking on: `answer=25347 finish=length`, `answer=23910 finish=length`). The grammar is deliberately skipped when a model thinks, so **both writers planned without one**. The token ceiling was raised once before for this same symptom (200 -> 300 tokens/second); raising it again would not fix a model writing 37 shots instead of 22. |
| What Strata actually is | Its own `serve/structured.py` says it: "The native engine has no grammar decoder. Failed generations are errors, never silently retried." It cannot *constrain* an answer at all; it **states the schema to the model** and validates what comes back. So for this endpoint the schema text is the only lever on the shot count, and dropping the schema removed it. |

The fix keeps the shape the callers expect: an array schema now travels as an object holding it under
`shots`, which is the envelope every branch of the planner's `_parse_json_response` already unwraps
(verified against the real parser, including a truncated answer). The endpoint accepts it, states the
real schema -- `maxItems` and the required fields included -- to the model, and checks the answer
against it. `jsonschema` is optional on that endpoint and only broadens the checking: without it, the
answer is verified to be one JSON object.

The other half is upstream's own decision, left alone deliberately: the local path applies the grammar
on the first attempt **only when that model's thinking is off**, and Gemma's thinking is on by default
with a 4096-token budget. Turning Gemma's thinking off for this pass is what would give the local
writer the same ceiling the grammar gives it elsewhere.
