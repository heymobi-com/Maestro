# The brief assistant: the freeform box is the weak link

Every planning failure this project has measured starts in one place: the text box where
the person writes the brief. It is the only input written freely, and it is also the
input the rest of the pipeline reads **structurally**.

| what the pipeline reads | what a prose brief gives it |
| --- | --- |
| `<Subject 1> is ALWAYS <name>` numbers the participants | "Abuela Rosa is ALWAYS Abuela Rosa" — no lock, so the Subjects are numbered from the planner's own list |
| `Stage N (label):` places a clip in the story | one paragraph — no clip has a position, so characters appear and disappear |
| the camera section reaches the planner once | "push-ins whenever she is watched" — applied to every shot |
| the cast list decides which faces are drawn | a character named in prose but never cast, or cast but placed in no stage |

Patching the code per film does not scale, and the four workflows do not even agree on
what a good brief is: a podcast wants one studio and no stages, a short film wants stages
and places, a music video wants song sections, a viral clip wants a hook. So the contract
is stated once, per workflow, in
[scene_policy.py](../app/services/director/scene_policy.py), and the assistant checks a
brief against it.

## What it does

Two buttons under the brief box, on the step where you write it.

**Check brief** — no model, no GPU. Names what is wrong in the code's own terms and gives
a fix for each:

| code | severity | what it catches |
| --- | --- | --- |
| `empty-brief` | error | nothing to plan from |
| `no-subject-lock` | error | no lock the compiler can parse, while characters exist |
| `no-stages` | error | a short film with no beats, so no clip has a story position |
| `few-stages` | warning | fewer than three beats for the clip count |
| `stage-wanders` | warning | one stage naming three or more places — one clip is one shot in one place |
| `camera-habit` | warning | `whenever` / `always` / `every time` / `throughout` in the camera section |
| `cast-without-stage` | warning | cast entries no stage places, listed by name |
| `undescribed-character` | warning | a name the story acts with that has no cast row |
| `speakers-unbound` | warning | audio speakers that the lock binds to no Subject |
| `podcast-stages` | info | staged locations in a workflow that is one studio |
| `music-no-sections` | info | a music video that never names verse / chorus / bridge |
| `no-negative` | info | no `NEGATIVE:` section |

**Structure for this workflow** — rewrites the text into the canonical shape:

    SUBJECT LOCK → CHARACTERS → PRESENCES → CAMERA → AMBIENCE → DESCRIPTION → NEGATIVE

Two passes, and the deterministic one is not optional:

1. `normalized_brief` repairs what needs no model — section order and case, `Stage N`
   numbering restored from the description's own sentences (a fragment like "One night in
   Valdecuna." joins the beat that follows), blank lines.
2. The LLM pass reorganises and fills the gaps the diagnosis named. It is told, in the
   prompt, that every name, image, action and idea is the person's, that a stage is one
   place and one moment, and that it may never invent a character or an event.

The result is shown **in full** with Apply / Discard; nothing replaces your text until you
press Apply. The response also says which faults were resolved and which remain.

## Why the model cannot make it worse

- The proposal is kept only if it is at least 200 characters and has the shape of a brief
  (`X:` headings); a model that answers prose leaves the mechanical result in place.
- A model that raises falls back to the mechanical result and logs the reason.
- `use_llm: false` runs the deterministic pass alone, which is enough to give a prose
  brief numbered stages and ordered sections.

## API

```bash
# Check only — no model loaded
curl -s $MAESTRO_URL/api/v1/director/brief/review \
  -H 'Content-Type: application/json' \
  -d '{"brief":"...","pipeline_type":"short_film_audio","clip_count":26,
       "transcript_speakers":["S1","S2"],"has_audio":true}'

# Structure — deterministic pass, then the model
curl -s $MAESTRO_URL/api/v1/director/brief/structure \
  -H 'Content-Type: application/json' \
  -d '{"brief":"...","pipeline_type":"short_film_audio","clip_count":26,
       "characters":["Abuela Rosa"],"use_llm":true}'
```

`pipeline_type` is what makes the answer workflow-aware: `short_film_audio`,
`short_film_story`, `music_video`, `podcast`, `viral_video`. An unknown value is judged as
a short film, the strictest case.

## Where it lives

| file | what it is |
| --- | --- |
| [brief_assistant.py](../app/services/director/brief_assistant.py) | `diagnose_brief`, `normalized_brief`, `structure_prompt` |
| [brief_api.py](../app/services/director/brief_api.py) | the two routes, with the LLM injected so they are testable |
| [DirectorBriefAssistant.tsx](../ui/src/components/Sidebar/DirectorBriefAssistant.tsx) | the two buttons, the findings, the Apply/Discard proposal |

## Tests

`tests/test_director_brief_assistant.py` (27) holds the diagnosis per code, the ordering
of errors before warnings, that every finding carries a fix, the workflow-specific
expectations (a podcast is not asked for stages, a music video is asked for sections), and
the mechanical normalization — sections reordered, beats numbered from prose, a podcast not
given stages, the ideas never rewritten.

`tests/test_director_brief_api.py` (9) drives the routes over ASGI: a prose brief is
reported, the route works with no model, the mechanical pass still fixes the shape, a
model answer resolves the fault, a model that answers prose does not replace the brief, a
model that raises falls back, and `use_llm: false` never calls the model.
