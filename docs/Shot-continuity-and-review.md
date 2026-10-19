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


## The decision is graded, and a return resumes from its own setup

Reported from use: "hay escenas que requieren continuidad y otras donde el corte debe ser limpio...
lo que no me agrada es que nunca hay continuidad entre un clip y otro, solo son cortes, y esto lleva a
clips repetitivos y donde no se justifica regresar a la escena inicial. No debe ser negro o blanco,
sino dar margen para creatividad sin que esto genere clips que parten todos del mismo punto inicial o
que avanzan tres centímetros y vuelven al mismo loop desde ese otro punto."

Both halves were measured on a real 17-shot run, and they have the same cause.

**The plan fragments one scene.** Its whole film happens in one warehouse at night, its `environment`
is byte-identical in all seventeen shots, and the plan still numbered eleven `continuity_group`s
(`warehouse_piano_1` … `warehouse_piano_11`), which its own contract forbids ("reuse it only while
place and story time remain uninterrupted"). Every group boundary forces `independent`, so **6 of 17
clips carried the one before them and 11 were hard cuts inside one uninterrupted scene** — and each of
those cuts re-staged the same tableau from the same four reference photographs. That is the repetitive
clip.

**The camera setups cycle.** The plan's openings read `Wide shot`, `Hector's subjective POV`,
`Reaction shot`, `Close-up`, then POV again, and again: the POV appears at clips 2, 5, 6, 8, 9, 11, 13.
The pairs (2-3, 5-6, 8-9, 15-16) exist because an eight-second ceiling splits one take; the returns
exist because that is the editing rhythm. Handed only the shot immediately before it, a returning POV
was composed from the character photographs and **re-staged the tableau it had already left**.

### The three levels

Derived from what the plan already writes — the place and the camera setup — so it stays automatic,
and it is a gradient rather than a switch:

| The shot | Continuity | What it is handed |
|---|---|---|
| states a new place (`environment` changes) | **clean cut** | nothing at all |
| same place, a **different** setup | **coherent cut** | the previous clip's late frame as a composition-only reference: world, light and wardrobe continue, the composition is new |
| same place, the **same** setup | **continuation** | the previous clip's own frame, and on a return the frame of the **last clip that staged that same setup** |

`place_continuity_groups()` decides the first row: one place, stated in the same words, is one group
whatever number the plan appended to it, so a scene the plan numbered eleven times is one scene. A
shot that moves somewhere new states a new place and keeps its cut. `carry_from_by_shot()` decides the
last column: the frame a returning shot resumes from, by the setup its opening blocking names
(`Hector's subjective POV.`, `Reaction shot.`, `Close-up.` …) or the structured `camera_plan`.

Measured on that same 17-shot plan, as a fresh run decides it:

```
  clip 1  wide shot               cut, nothing
  clip 2  (unstated setup)        continues the shot before it
  clip 3  hector's subjective pov continues the shot before it
  clip 4  hector's subjective pov resumes from clip 3   (same setup)
  clip 5  reaction shot           continues the shot before it
  clip 6  hector's subjective pov resumes from clip 4   (the return, not its neighbour)
  clip 7  hector's subjective pov resumes from clip 6
  clip 8  reaction shot           resumes from clip 5   (the reaction's own last state)
  ...                             and so on to clip 17
```

16 of 17 clips continue, 11 of them resume from a clip staging the same setup, and the 11 mappings
were checked to name a clip whose setup is the same word for word. The one cut is the film's opening.

### What the engine does with it, and the level that is not available yet

The batch's own hand-over now reads the **receiving** clip's decision (`multi_clip_info`), not the
finished clip's, so a clean cut is genuinely handed nothing — the flag used to be read off the shot
that had just finished, one shot out of phase. The frame it wants is already on disk
(`_omni_sequence_continuity_<job>_<clip>.png`), so a return reuses it rather than re-extracting a clip
that is not the composition it needs.

The strongest level — start from the last frame itself — is **not** wired for `omni_reference` (H3
Singularity references), and it cannot be reached by the obvious route. Verified in the engine:

```python
# app/models/minimax_h3/minimax_h3_main.py
if has_explicit_start:            # image_start is set
    return None, None, 0          # ...no history, and no boundary frame
...
if self.omni_reference:
    continuation_picture = _tensor_to_pil(boundary_video)   # the ONLY first-frame anchor
```

`has_explicit_start` is `image_start is not None`, and the `elif` that consumes `image_start` belongs
to the non-omni branch. So setting `image_start` on an `omni_reference` job would **remove** the one
first-frame anchor that path has and put nothing in its place. On that strategy the anchor arrives
through the sliding-window overlap instead, which is where a true start frame has to be delivered —
a separate, verifiable change.

### Reading the log

One line is the whole evidence, and it is the engine's own:

```
  [H3 Omni Sequence] Added curated continuity Picture 5 for clip 8.
```

The picture number is the label the borrowed frame receives — 5 when the four canonical
references (the cast and the location) are in front of it, 6 when the project-look frame was also
added. The clip number is the clip that **receives** it, the one about to be generated. Two lines
later the same clip reports its runtime bindings, which is where the hand-over becomes visible to
the model:

```
  Picture 5=Maestro rolling continuity reference from the preceding generated clip;
  blocking, environment state, lighting, and screen direction only
```

In the run that measured the fragmentation it appeared **once**, for clip 8, because eleven of the
seventeen clips were forced to `independent`. A run with this decision should print it from clip 2
onward, naming a different receiving clip each time — and it should **not** print for a clean cut,
which is the other half of the evidence.

One wording note, left as it is: the role string says "the preceding generated clip", and on a
return the frame comes from the clip that last staged this setup, several shots back. The string is
upstream's, in a file upstream owns, so it would cost budget to reword; what the model acts on is
the instruction beside it — "preserve prior blocking, environment state, lighting, and screen
direction; begin with a newly generated moving composition rather than an opening freeze-frame" —
which is accurate for both cases.


Where it lives: `app/services/director/sequence_continuity.py` owns the rules(`place_continuity_groups`, `shot_setups`, `carry_from_by_shot`, `sequence_continuity_flags`), the
planner calls the first one before it decides any strategy, and the batch reads the result from
`multi_clip_info`. The pinned cases are in `tests/test_director_sequence_continuity.py`, including the
hand-over block lifted out of `launch.py` and run against real files.


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

The other half was the local path's own rule: the grammar is attached on the first attempt just for
`thinking_budget == 0`, and a gemma-style writer resolves `None` to 4096. So the H3 native shot-plan
call asked for `None` and its first attempt went out unconstrained, while the repair pass two hundred
lines below already asked for `0`. That is now `0` as well, which is the one-line change here: the
grammar holds the window, and the window is what the validator then checks. It is an argument, so it
cannot be extracted into a module; the line belongs to us and the seam budget records it with this
reason.

What this cannot fix is a writer with no grammar decoder. An endpoint that only *states* a schema
(Strata) has no way to hold a 16-22 shot window, and stating the schema as text pulls the answer
toward the single object its directive asks for. The native H3 plan therefore needs a writer that can
compile the schema -- the bundled llama.cpp writer does, from token 0.

## Why the writer went from 80 to 8 tokens a second

Reported while planning: "de 80 que generaba antes ahora baja a 7 - 8 no se puede trabajar asi", and
"unas veces bien y otra demasiado lento". The writer is a mixture-of-experts model served by Strata,
with its experts mapped from the model files and **not** loaded into RAM.

| ruled out | measurement |
|---|---|
| Paging | 0.2 GB of the page file in use; 0-11 disk pages read per second; free list at 0.03 GB. |
| Disk | 0.81-4.11 ms per read, queue length 1, ~860 reads/s. |
| GPU throttling | 2,940 of 3,090 MHz, 47 °C, 69 W of a 360 W limit, and `clocks_event_reasons` reporting none. |
| What is open while it generates | The operator's own observation: it ran at 80 with a loaded machine, so this is not the cause. Our first hypothesis was memory pressure from other applications, and that observation refutes it. |

What tracks the speed exactly is the writer's own decode line:

```
strata serve: decode expert cache hit rate: 36.0% (...)   -> 8 tok/s
strata serve: decode expert cache hit rate: 67.3% (...)   -> 54 tok/s
strata serve: decode expert cache hit rate: 82.7% (...)   -> 67 tok/s
strata serve: decode expert cache hit rate: 92.1% (...)   -> 84 tok/s
```

The experts live in two tiers -- RAM resident and the GPU's expert cache (7.42 GiB) -- and everything
that is in neither is read from the file, 13.1% of the routed experts over PCIe at the slow end
against 5.5% at the fast end. So the hit rate *is* the speed, and the hit rate is set by how much RAM
the writer managed to claim.

That is decided once, at startup, and never revisited:

```
--resident-budget-gib 24 in the config
    started with the machine busy  ->  resident RAM: 12.00 GiB of experts in RAM   ->  8 tok/s
    restarted with the machine free ->  strata.exe holding 27.15 GB                ->  64 tok/s
```

So the answer to "sometimes fine, sometimes unusable" is not what is open during a run but **when the
writer was started**. Half a budget covers half of ~40 GB of experts, and a third of the lookups then
come from the file. The config now asks for 30 GiB, which is a request the machine has to be able to
answer at startup: the number the writer prints is the truth, and asking for more than is free buys
nothing.

To check after a restart, two lines:

- `resident RAM: <N> GiB of experts in RAM` -- if N is short of the request, that start is a slow one.
- `decode expert cache hit rate:` -- above 90% is the healthy band.

`llm_writer_supervision.arena_forecast()` exists for the moment before it happens: it reproduces the
engine's arithmetic from the free RAM it can see and says so in the log and in Settings, which is the
point at which closing the machine and restarting the writer is cheap.

Two costs compound here rather than adding, and both were paid at once on the same run. At 8 tok/s a
reply that thinks for 24,605 tokens (see the thinking cap above) takes **51 minutes**; at 64 tok/s the
same reply is six. A writer that starts with too little room turns every other inefficiency into a
coffee break, which is why the warning is worth reading instead of dismissing.

## A Stop did not reach the writer

Reported after the speed was fixed: "cuando hago stop o detengo un job o hago start over se queda algo
pendiente quiza usando recursos y strata empieza mas lento".

The writer's own counter said it first: of the **109** requests it finished, **one** carried
`cancel=True`. So a stop almost never reached it.

`stop_pipeline` writes `cancelled` and aborts the generation jobs, but a plan in flight is a blocking
HTTP stream that nothing interrupts. The pipeline thread is parked inside that request and only reads
the flag when it returns, so the abandoned generation runs to its last token — and because the engine
serves requests in order, the next call queues behind work nobody is waiting for. That is the whole of
"it feels slower after a stop".

The machinery was already there, for prompt enhancement:

- `studio_enhancement.enhancement_context(settings, cancelled)` puts a context on the calling thread;
- `cancellable_lines` reads the socket in a daemon thread and checks cancellation every 100 ms;
- on the way out it calls `sock.shutdown(SHUT_RDWR)`, so the server sees the client leave and ends its
  own work.

What was missing is that nothing set that context on the Director's planning thread, so
`is_cancellable()` answered False and every check inside the stream did nothing.

`director_plan_cancel.bind(pid)` sets it, one line in `_run_pipeline`, and it reads the pipeline's own
status rather than being told: `stop_pipeline` already writes `cancelled`, so there is one source of
truth and no second caller to keep in step. Three properties make it safe to switch on:

- `generate_streaming` checks cancellation **before** it sends anything, so the planner's
  retry-after-failure path raises there instead of quietly starting a second generation;
- the raise is `InterruptedError`, and `_run_pipeline` already treats a pipeline whose status is
  `cancelled` as a clean stop — "Cancelled during a bounded planning or generation unit; its latest
  checkpoint was preserved" — so a stopped run is not recorded as a failure;
- the binding is per thread and entered once, so a second pipeline thread is untouched.

To check on the next live stop: the writer should start reporting `cancel=True` (it was 1 of 109), and
the call started right after should not wait for the abandoned one.

## A line names its speaker beside itself

Next build after that, and past planning, the run stopped before any render:

    MiniMax H3 Omni would refuse these spoken lines: no cue names the character who speaks them...
    shot 3: The line "[English] Ready to start?" is introduced by "s are deep, chest-based, and
    controlled. Facial expressions show concentration and strain:", which names no character.

The lines were written; nothing beside them said who speaks them. The compiled prompt carries the
attribution **twice, in two different fields**: DIALOGUE AND VOCAL PERFORMANCE lists
"Name (S1) speaks: <words>", while the `<d>` blocks are inserted into `detailed_description`. A cue is
read clipped to the field it sits in, so the renderer's check -- which looks beside the line -- never
sees the first one. Whoever writes that description therefore decides whether the shot can be rendered
at all: a planner held to a grammar ends it by naming the speaker, and one free of a grammar ends it
with the voice itself. That is a Strata-shaped failure, and it is why every shot in the reported run
had two voices and no attribution.

`h3_speaker_cues.compile_blocks` puts the tag where the check looks, and only where the check refuses
the line. Two details earn their place in the code:

- the judgement is the renderer's own `h3_unresolved_speaker_cue_problems`, not a copy of its rules:
  a second implementation would drift, and one that accepted a line the renderer refuses would leave
  the same broken build behind;
- the function also returns the text, with its own tags taken out. The tag sits **outside** the
  `<d>...</d>` span, and that span is all the compiler strips, so without the cleanup a second compile
  would find the first pass's tag stranded in the prose, judge the lines against it, decide they were
  fine, and drop them -- which is exactly what the compiler's own "compiling a repaired prompt returns
  it unchanged" test caught while this was being written.

## The speaker stays beside the line

The first repair was not enough on the operator's own project, and the retest that produced the same
error message is what pointed at the real path. Two things were measured before touching anything:

- an H3 native plan is compiled by **two** different functions. `compile_h3_vocal_contract` is the one
  the first repair went through; the plans the pipeline actually queues go through
  `_compile_official_dialogue`, called by `compile_h3_clip_plans`;
- the renderer's check reads back a fixed window (`window=160` in `h3_unresolved_speaker_cue_problems`)
  and the sentence it judges is `"{name} {stable_id} speaks {delivery}: {tag}"`. The name *is* in the
  sentence -- it was simply further than 160 characters from the line.

Measured both ways on a synthetic cue: a 48-character delivery produced no problem, a 251-character one
produced a problem, and the bounded form produced none. The reported cue starts mid-word
("rises. Vocalizations are low-register..."), which is the signature of that cut.

`h3_speaker_cues.spoken_sentence` now measures the room against the real prefix and bounds the delivery
to what fits. A delivery that already fits is returned verbatim.

### What the delivery actually is on a real project

Reproduced on the operator's saved plan (`outputs/prueba-2/_director_pipeline_6e33723d.json`), which is
the failing run itself:

- `_params_snapshot.planned_clips[n]._director_dialogue_beats[0].delivery` is not a short delivery at
  all: it is the character profile's `performance_direction`, 256 characters of it, written by the
  enrichment pass. The 3-7 character values visible in the base plan belong to an earlier plan.
- Compiling those 16 saved clips with the app's own `compile_h3_clip_plans`:

  | | cue problems |
  |---|---|
  | without the bound | 22 |
  | with the bound | 0 |

  and the cue text inside those 22 is the operator's error message verbatim.

The bound stays on a word boundary: a cut that preferred to close the sentence was tried and reverted,
because it stopped answering to the name's real length -- a longer name no longer shortened what was
kept, and the test that measures the room against the real prefix caught it (1408 tests, 1 failure,
reverted to green).

### Two numbering systems, both printed correctly

Reading the compiled prompt, a `<Subject 2>` beside a line whose contract says `(S1)` looks like a
swapped speaker. It is not: the prompt declares both mappings in its own `subject_definitions` field --

    <Subject 1> is Aria (S2): ... <Subject 2> is Roman (S1): ... <Subject 3> is Hector (S3): ...

`<Subject N>` is frame order (`_speaker_map`, so the first row on screen is 1), while `(S N)` is the
stable label the registry and the project subject lock assign. On the operator's three-character
project every line compiles with both, and all three characters resolve.

## "detailed_description is missing [Shot 1]" on a prompt that carries it

The single-shot project that produced this refusal reported it five times, each about eight seconds
apart, and the message names a rule the prompt visibly satisfied. Measured, not guessed:

- the scene description the run started from was **itself a compiled six-field prompt** (4,799
  characters, `subject_definitions` → `non_diegetic_music`, `[Shot 1]` three times). The intent was
  reasonable -- paste a shot's prompt to correct it in another project -- and the planner was the
  local 4B writer;
- the planner's answer was a prose draft, and the compiler wraps that in the six official fields,
  writing `[Shot 1]` itself. So the marker was never missing from what the compiler built;
- the validator counts a field at a line start (`^\s*detailed_description\s*:`), and then inspects
  the value **`_extract_h3_fields`** returned for that same name. That reader searched without the
  anchor. A label mentioned in the prose earlier in the text won the match, and the slice ended at
  the real label -- so the counter said "one field, present" and the reader handed back the words
  that followed the mention.

That is exactly reproducible: an echo of `detailed_description:` in the summary of an otherwise valid
compiled prompt yields `['detailed_description is missing [Shot 1]', ...]` and nothing else is wrong
with the prompt. A small local planner rewriting a compiled prompt echoes labels; a larger one may
too, and nothing about the compiler made the echo harmless.

The reader now prefers the occurrence of each field that starts its own line, and keeps the inline
search as the fallback it was written for, per field rather than for the whole prompt: legacy
one-line prompts (which have no line-anchored field at all) still read exactly as before, and a
prompt whose *first* field lost its line start to a prefix still yields that field. The refusal also
prints what it saw -- `Refused a prompt built from these fields: summary=…, detailed_description=…;
Body starts: '…'` -- because a rule name alone costs a whole planning pass to diagnose. Both live in
the reader and in `services/director/h3_prompt_shape.py`; the contract itself is unchanged.

## Confirmed in use

The repairs above and the writer's RAM budget were confirmed together, on the operator's machine, after
one restart:

- **the plan that used to die before any render queued**: `_director_pipeline_f1a81be8.json` reached
  `preparing_video` with 21 clips and `error: None`;
- **the writer's resident tier is what was asked for**: `resident RAM: 27.06 GiB of experts in RAM`
  against a 30 GiB budget, `decode expert cache hit rate: 81.2%`, 14.5% of 11,190,240 routed experts
  read over PCIe;
- the rate read at the terminal afterwards was above 100 tok/s.

Two caveats, for whoever reads this next:

- the `done: N tokens in Xs (Y tok/s)` line that reports the rate is printed to the console and is
  **not** written to `C:\Strata\strata-iq2_xs.log`, so that rate is taken in use rather than from the
  log. That log -- not the `strata-iq2_xs.fast.log` the earlier sections quote, which stops on October
  11 -- is the file the current build writes to;
- `cancel=True` appears zero times in that session, so Stop has still not been seen interrupting a live
  generation. The resident tier is also not the only thing that moves the rate: the same ~27 GiB was
  measured at 64 tok/s before the stop and thinking repairs, an abandoned generation that keeps
  decoding holds the expert cache, and a thinking reply bills the answer's own allowance.
