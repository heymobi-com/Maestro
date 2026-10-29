# Director setups: start from a project you already built

Iterating on a Director project means planning, watching what came out, and planning
again. Before this, every one of those attempts cost the whole setup again: the cast
images, the soundtrack, the transcript and the brief. A **setup** freezes all of that, so
a new attempt is one click.

A Studio **recipe** is not this. A recipe saves a *look* — model, tuning, LoRA pointers —
and deliberately leaves out the per-generation inputs, so it can never rebuild a Director
project. A setup is the opposite: it exists to carry the inputs.

## Getting there from a clip in the Videos panel

The pencil (**Load settings**) on a clip already sent its settings to Studio. For a clip
rendered by Director, that button now asks where it should load:

| Choice | What you get |
| --- | --- |
| **Studio** | the clip's generation settings, as before |
| **Director** | the project that rendered it: brief, references, audio and transcript |

Only Director clips offer the choice, because only they have a project behind them — a
plain Studio output asks nothing and loads Studio directly. Once the project is open, use
**Save setup** in the Setups bar to freeze it for later attempts.

A clip keeps that project even when the run was stopped before the job finished. Full clip
metadata is written at job completion, so a cancelled run used to leave finished mp4s that
named no project at all: the gallery showed them as ordinary renders and the pencil offered
them nothing, while the same clips from a completed run did offer the project. Two things
now cover that. A minimal ownership sidecar (`director_pipeline_id`, `director_clip_index`,
`director_artifact_kind`) is written as soon as each clip's file exists, and the gallery also
recovers ownership from the saved plan for any video in the folder that names no project. The
completion sidecar still replaces the minimal one with the full recipe when the job finishes.

## Editing a loaded project before rendering again

Once a project is past its setup steps — which is where a loaded project or an Open & Edit
lands — the Director sidebar carries two collapsible editors, both of them outside the step
flow:

| Editor | What it lets you change |
| --- | --- |
| **References** | the reference photo, the ordered cast and location manifest |
| **Prompts** | the video prompt of every shot, one box per shot, with its length in characters |

Editing a prompt there writes it into the project that the next **Generate** submits, so a
single bad shot no longer means re-planning the film. When a loaded setup has no prompts yet
(a setup never stores a plan), the Prompts editor says so and offers **Plan prompts** instead
of showing an empty list.

## Saving a setup

In the **Setups** bar above the Director chat, type a name and press **Save setup**. It
works at any point, including before the project has ever been run: the bar reads the
composer as it stands, so it freezes the brief you just edited, not the one an earlier
attempt was submitted with.

What is frozen:

| Kept | Not kept |
| --- | --- |
| the brief | the clip plan and the timeline |
| the ordered reference manifest, with the image and audio files copied | the generated prompts |
| the soundtrack and its analysis, including the speaker-labelled transcript | the planning checkpoint |
| the models, LoRAs, resolution, duration, and every Director control | the run id and its queue entry |
| the speaker names and their voice assignments | the per-run asset anchors |

Keeping the analysis matters: it means loading a setup does **not** re-transcribe the
audio, which is the slowest step of a two-minute project.

Nothing is kept that could re-render what just failed. A setup is a starting point, never a
plan.

## Loading a setup

Pick it in the **Setups** bar above the Director chat and press **Load**. The composer
fills with the brief, the audio, the references and the controls, and lands on the step
where you write the brief — with the audio and the speakers already marked as done.

From there: change the brief, add or remove a character, press Plan.

## How this differs from Open & Edit

| | Open & Edit | Load a setup |
| --- | --- | --- |
| source | one specific run | a saved setup, independent of runs |
| plan | that run's plan, editable | always empty |
| survives deleting the run | no | yes, it owns copies of its files |
| use case | fix one shot of a finished project | try the whole project again |

## Where the files live

Inside the active workspace:

```
outputs/<workspace>/_director_presets/<setup-id>.json          # the configuration
outputs/<workspace>/_director_presets/<setup-id>/  (images, audio, its own copies)
```

The copies are the point: a setup survives deleting the run it came from, and when you
plan from a setup the new run copies them again into its own project folder.

Deleting a setup removes both the configuration and its media.

## API

`$MAESTRO_URL` is the address the app prints at startup (`Maestro UI: http://…/`); the
port is assigned per install, so it is not fixed.

```bash
# List
curl "$MAESTRO_URL/api/v1/director/presets?workspace=Valdecuna"

# Save from the current setup (no run needed)
curl -X POST "$MAESTRO_URL/api/v1/director/presets" \
  -H 'Content-Type: application/json' \
  -d '{"name": "Valdecuna drama", "params": { ... }}'

# Save from a saved run instead
curl -X POST "$MAESTRO_URL/api/v1/director/presets" \
  -H 'Content-Type: application/json' \
  -d '{"pipeline_id": "8c94a5a5", "name": "Valdecuna drama"}'

# Read one (includes params and asset_manifest, the shape the UI restores from)
curl "$MAESTRO_URL/api/v1/director/presets/<setup-id>?workspace=Valdecuna"

# Delete
curl -X DELETE "$MAESTRO_URL/api/v1/director/presets/<setup-id>?workspace=Valdecuna"
```

`params` is what the UI already builds at submit time; `pipeline_id` is accepted as
provenance and as a way to freeze a run you can no longer open. Either way, a setup with
no brief, no audio and no references is refused rather than saved, and a setup whose input
files are no longer on disk names them instead of restoring broken images later.

A saved run created before the setup snapshot existed reports it plainly and asks you to
submit the project once before freezing it.
