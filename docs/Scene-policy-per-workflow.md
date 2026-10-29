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
| shot with no performed action | reported | — | — | reported |

Nothing is blocked or rewritten by it. A podcast that holds one studio is not warned
about holding one studio; a short film that holds one church for ten clips is.

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
