# What "well planned" means per workflow

Maestro runs four different crafts through one Director pipeline. They do not agree
about what a good shot list is:

| workflow | the setting | stillness | the variety comes from |
| --- | --- | --- | --- |
| **short film** (audio- or story-driven) | must keep moving | an accent, not the default | new places, new actions, new angles |
| **music video** | returns to the same stage on purpose | tolerated | new visual ideas per song section |
| **podcast** | one studio, one seating, one light | expected | framing and reaction, never new rooms |
| **viral video** | changes with the pace | almost never | a hook on frame one, then constant change |

`app/services/director/scene_policy.py` is the one place that states this. It gives
every planner a `guidance` block to interpolate into its own system prompt — so the
writer is told the rule while it writes — and the two numbers the plan variety check
reads back: how long a setting may be held, and how many shots may declare the frame
motionless.

## Where each planner receives it

| planner | prompt | asks for |
| --- | --- | --- |
| `planners/short_film.py` | scene planner for an audio film | `scene_guidance("short_film_audio")` |
| `planners/short_film.py` | generic story Pass 2 | `scene_guidance("short_film_story")` |
| `planners/short_film.py` | MiniMax H3 story Pass 2 | `scene_guidance("short_film_story")` |
| `planners/podcast.py` | podcast visual director | `scene_guidance("podcast")` |
| `planners/viral_video.py` | viral video director | `scene_guidance("viral_video")` |
| `planners/music_video.py` | clip planner | `scene_guidance("music_video")` |

The workflow name comes from `pipeline_type`: `short_film_audio`, `short_film_story`,
`music_video`, `podcast`, `viral_video`. Unknown or missing names resolve to the
short-film contract, which is the strictest about progression.

## Why a short film says something different

The story-driven Pass 2 prompt already carried this rule:

> A location/time cut must be motivated by the outgoing scene and must open on the
> resulting arrival or consequence. **Never create an unrelated scene merely for
> visual variety.**

That rule exists because a plan can drift into disconnected vignettes. It is right,
and it is not the same instruction as "hold this room for ten clips". The short-film
contract states the difference explicitly:

- a new place must come from an arrival, a departure, a discovery, or a consequence;
- changing the framing, who is in frame, or what the character is doing needs no
  excuse, so those are used *before* inventing a location;
- at most two consecutive shots share a setting, at most one shot in five declares
  the frame motionless, and every other shot names a physical action somebody
  performs — standing, waiting, watching and "remains still" are not actions.

## What the plan variety check reads from it

`_director_prompt_diversity_report` takes the workflow and applies its numbers:

| finding | short film | music video | podcast | viral |
| --- | --- | --- | --- | --- |
| prompt repeated verbatim | reported | reported | reported | reported |
| frame declared motionless | reported above 20% | above 40% | never | above 15% |
| one setting held | reported from 3 consecutive clips | from 5 | never | from 3 |
| one framing held | reported from 3 consecutive clips | from 5 | never | from 3 |
| shot with no performed action | reported | — | — | reported |

Nothing is blocked or rewritten by it. A podcast that holds one studio is not warned
about holding one studio; a short film that holds one church for ten clips is.

## Identity drift: why a face changes over a long film

Two mechanisms, measured on the same real 42-clip documentary (`David-y-Goliat`), and
both are now either fixed or reported.

### 1. A missing identity picture (fixed)

Every clip bound exactly one picture — David's — and the narrator's own reference was in
**none of the 42**. She looked right for a few clips and then changed.

The per-shot filter was the cause. The story planner writes its cast rows with positional
ids (`char_0`) and no inline `(S1)` cue, while the user's picture carries the cue, so
`on_screen` came back empty and a *labelled* picture was dropped as "not on screen" — the
one picture the user was most explicit about. Three rules now:

- a labelled picture is dropped **only on evidence**: when the shot's own cast is
  identified and does not include it. A shot whose cast cannot be identified keeps every
  labelled identity;
- `char_N` rows map onto the manifest order, so positional ids name the user's pictures;
- a character the brief names is attached whenever the shot puts **more people in frame
  than identities already attached** (the planner called them "a young man" and "a massive
  giant" while the manifest held `David` and `Goliath`); a character the brief never
  mentions is still a leftover and is still dropped.

Result on that run's own clips: the narrator is bound in all 42 (was 0), 24 shots of her
alone carry only her picture, and the 18 shots that stage the biblical cast carry David's
and Goliath's too.

### 2. Clip continuity re-renders the previous face (reported)

`Clip continuity` chains every clip to a frame extracted from the one before it, so each
shot re-renders the previous shot's face. That is generation loss by design, and it is
right for one continuous scene; it is wrong for a narrated film with cutaways. On that run
15 continuity frames were written and fed forward, and the likeness drifted a little in
each — the user's *"la va transformando conforme avanza el video"*.

The pipeline now reports it at planning time when the mode is on, the project has a
labelled identity reference and the film has more than eight clips:

> Clip continuity is on with 1 labelled identity reference(s) (Anita) over 42 clips: every
> shot is chained to a frame of the clip before it, so the face is re-rendered from the
> previous generation and drifts a little each time. For a narrated film with cutaways,
> switch the workflow to the independent mode so each shot starts from the identity
> picture alone.

**What to do:** for a documentary-narrator format, choose the independent workflow
(`Clip continuity` off). One face, many places: the identity picture carries the face and
the plan carries the place.

### 3. Wardrobe is text, and only the brief can hold it

The run's brief described Anita and then treated everyone else as "Ancient Biblical
Characters". The code no longer invents their faces, but their wardrobe is only as stable
as the words: give each character a line in `CHARACTERS` (`David: young shepherd, curly
dark hair, tan tunic, leather sling`) and the plan repeats it.

## The cast is per shot, not per project

### The stage is decided by position, not by the model

A brief written as seven stages still put stage 7 (the closing chapel) in clip 11 of 26
and the bell in the forest in clip 6, whose lines were about street lamps: the planner
was free to choose a stage per shot, so it chose badly and out of order. The pipeline now
reads the brief's stages (`_brief_stage_list`) and hands each clip the stage its position
in the narration has reached, with the instruction to show nothing from a later stage and
nothing from an earlier one that has passed. A stage number mentioned in the cast rules
("he appears only inside the opened shroud in Stage 5") is ignored, and a batch keeps its
global position so a batch of twelve is not given all seven stages.

Two rules keep a character out of a shot it does not belong to.

**In the prompt.** The audio-film planner carries a `NARRATION AUTHORITY` block and a
`ONE SHOT, ONE MOMENT, ONE PLACE` block. The second one exists because a measured clip
walked villagers down a street, pushed the camera through a doorway into a church and
ended on five children at a golden altar — three spaces and most of the cast in one
eight-second shot whose lines were about the grandmother offering the night.

**In the reference manifest.** H3 maps every supplied reference to a Subject and states
that the Subject appears in the shot, so a leftover picture is cast into every clip. A
real manifest carried ten references from two projects: five with no role at all,
`Roman (S2)` and `Veronica` (characters the brief never mentions), and `Dona Rosa` and
`Los cinco niños del río` without a speaker cue. Because only one carried `(S2)`, only
that one could be filtered, and every clip bound five pictures as present — which is how
the priest, the shrouded figure and the children ended up mixed into scenes whose lines
named none of them. Two changes:

- an identity picture now has to be *named* by the shot to be attached: a labelled
  picture follows the shot's labelled cast, an unlabelled one needs its role's words to
  appear in the shot, and a picture with no role at all is only kept when nothing else
  survives;
- `_h3_manifest_issues` names the problems before the render — role-less images, a role
  the brief never mentions, and identities with no `(S1)…(S7)` cue — in the pipeline log
  and in the composer's warning block, because no filter can guess that `Veronica` came
  from another project.

## Where the brief still wins

The policy is a default, not a veto. A brief that names a single room for a specific
beat keeps that beat; the short-film contract says so in as many words:

> When the brief's own text asks for stillness or one location throughout, honour it
> where it names a specific beat and treat it as that beat, not as the rule for every
> shot.

What the policy removes is the *silent* version of that instruction — a `CAMERA:`
line that asks for a still frame as a habit, which the planner then applies to every
shot it writes. See
[Maestro-project-brief-template.md](./Maestro-project-brief-template.md#two-things-a-brief-can-say-that-make-every-clip-look-alike)
for how to write that differently.

## Tests

`tests/test_scene_policy.py` holds the contract text per workflow, the limits each
one publishes, alias and fallback resolution, and the wiring — that every planner
asks for its own block and interpolates it into the prompt it sends, not merely
imports it. `tests/test_scene_policy_in_prompts.py` proves the runtime half by
capturing the system prompt each planner actually builds: the audio-film prompt that
planned the 26-clip drama carries the short-film contract beside its own rules, the
podcast prompt asks for one studio, the viral prompt asks for the hook on frame one,
and the two are not told the same thing.
`tests/test_director_plan_variety.py` holds the workflow-aware behaviour on one plan:
a short film is reported for one church and a frozen altar, a podcast is not, a music
video may hold one stage longer than a short film may, and a podcast is still reported
for repeating a prompt verbatim.
