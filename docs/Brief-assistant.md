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
| `template-placeholder` | error | a line whose key is in angle brackets, or a heading carrying the instruction in parentheses — the assistant's own template reaching the planner as content |
| `abstract-beats` | warning | a stage that names no place and no action, so the planner has to invent the film from it |
| `cast-without-appearance` | warning | a character who acts in a stage but is only listed as a *presence*, so every clip invents a different face |
| `cast-not-bound-to-picture` | warning | a supplied picture names a character the brief gives no appearance |
| `lock-cue-not-canonical` | error | a cue written as a diarization id (`SPEAKER_00`) instead of `(S1)` |
| `lock-missing-cue` | warning | the lock names participants but binds none to the audio's `(Sx)` label |
| `no-references` | info | no picture reached the check, so the appearance can only come from text |
| `narration-driven` | info | the description states how the film is made, not what happens in it; correct when a narration drives the clips |
| `speakers-unbound` | warning | audio speakers that the lock binds to no Subject |
| `podcast-stages` | info | staged locations in a workflow that is one studio |
| `music-no-sections` | info | a music video that never names verse / chorus / bridge |
| `no-negative` | info | no `NEGATIVE:` section |

**Structure for this workflow** — rewrites the text into the canonical shape:

    SUBJECT LOCK → CHARACTERS → PRESENCES → CAMERA → AMBIENCE → DESCRIPTION → NEGATIVE

Two passes, and the deterministic one is not optional:

1. `normalized_brief` repairs what needs no model — section order and case, `Stage N`
   numbering restored from the description's own sentences (a fragment like "One night in
   Valdecuna." joins the beat that follows), blank lines, and `strip_template_placeholders`
   removing any line the assistant's own instructions leaked into the text.
2. The LLM pass reorganises and fills the gaps the diagnosis named. It is told, in the
   prompt, that every name, image, action and idea is the person's, that a stage is one
   place and one moment, and that it may never invent a character or an event. Its answer
   goes through `strip_template_placeholders` too, so a template line cannot come back.

The result is shown **in full** with Apply / Discard; nothing replaces your text until you
press Apply. The response also says which faults were resolved and which remain.

## The appearance is read from the picture, not asked of the writer

A brief is prose and a reference picture is a photograph, and only one of them contains a
face. Measured on a real project, the brief said `David: presence in the biblical action
stages` — a row that says *when* he appears and never *how he looks* — while the narrator
was described as "una mujer joven, bella y carismatica" and her reference picture showed a
woman with her hair tied back, a white shirt, a blue skirt and low heels. The picture was
attached to every clip and the text never agreed with it, so each shot re-invented the
face and the likeness drifted over the run.

So the appearance is not asked of the writer at all:

| where | what happens |
| --- | --- |
| **Structure for this workflow** | the identity reference paths are sent with the brief; each one is described once by the vision model and written into `CHARACTERS` before the model sees the text, and again after, so the row holds the picture's own sentence |
| **planning** | `_h3_cast_looks` reads each identity reference once per pipeline and caches it; `_h3_looks_for_shot` writes it into a shot's project context **only if that shot's own text names the character** |

The read is deliberately narrow (`CAST_LOOK_PROMPT`): apparent age and build, skin, hair
colour **length and how it is worn** (parting, fringe, tied back, bun, under a headscarf),
brows, eyes, nose, mouth, jaw, marks, every garment top to bottom with colour, fabric and
fit, heel height, jewellery. No background, no pose, no mood, no camera. A model that
answers with a scene, a refusal, or a row that says "presence" is discarded — a wrong
sentence is worse than none.

A reference is often a 2K render that took a long generation, and the detail in it is the
reason the face stays coherent, so the read is not a caption:

| setting | value | why |
| --- | --- | --- |
| `image_max_size` | 1536 px | 768 is enough to caption a picture; it is not enough to see a fabric or an eyebrow |
| `max_new_tokens` | 380 | the answer alone, not the answer minus the reasoning |
| `thinking_budget` | 1400, then 2800 on retry | a thinking model pays for its reasoning out of the same budget |
| `temperature` | 0.15 | observation, not invention |
| `LOOK_LIMIT` | 900 chars | the sentence travels into every shot that shows the character, so it is bounded |
| `looks_block(limit=)` | 1400 chars | three detailed reads must not spend the shot's prompt budget on wardrobe |

**A thinking model must be given room to think _and_ answer.** Strata serves a Qwen3.8
thinking model, and the remote provider drops `enable_thinking`, so thinking cannot be
switched off. Measured: 560 total tokens became 2,279 characters of reasoning, empty
content and `finish=length`, so every reference read came back blank and the appearance was
discarded — the same silence as having no picture at all. The two budgets are separate now
(`max_new_tokens` for the answer, `thinking_budget` on top), and an answer that is still
empty gets one attempt with twice the room. `_brief_generate` applies the same rule to the
structuring call, which was equally exposed.

**The picture has to actually reach the model.** `launch.py`'s `_brief_generate` — the entry
point the routes are given — was declared with exactly three positional parameters
(`prompt, system_prompt, max_new_tokens`), so `image_paths` was dropped on the floor: the
model was asked to describe a photo it had never been shown, answered from the prompt text,
and the appearance was discarded as unusable. That is why the references the user had
already supplied produced nothing. Three guards now:

- `_brief_generate` forwards `image_paths` and `image_max_size`;
- `_accepts_images` refuses to call a callable that cannot carry an image, so a wrapper
  cannot silently swallow it again;
- `_brief_generate` raises a readable error when the loaded model has no `mmproj`, and
  `/cast` and `/structure` return `vision: {loaded, available, model}` so the panel can say
  "load a vision-capable model" instead of showing silence.

`vision_available()` asks a remote endpoint **live** rather than trusting the value cached
at connect time. Strata reports `images` on `/health` and swaps the loaded model between
runs, so a session connected while a text-only model was loaded stayed "text only" for good
and dropped every reference while the server was serving a vision model. The probe now reads
`architecture.input_modalities` for the model **actually in use**, falls back to the
server-wide flag, and refreshes the cached value when the answer changes.

`generate(..., image_max_size=…)` threads through `_user_message` to `_image_to_data_url`,
so any caller can choose the resolution the picture is sent at. `describe_image` had been
passing the *filename* and nothing else — it answered from the name alone; it now attaches
the image and accepts the same size argument.

A row the writer already described is kept: the picture is evidence for a gap, not a
replacement for a choice. Everything fails soft, so no vision model, no image or a model
that raises leaves the pipeline exactly as it was.

In the panel the read is shown under the proposal, labelled *read from the reference
pictures*, so the sentence that will hold the face is visible before Apply.

### Narration-driven briefs get no stages

`structure_prompt` decides by the text whether it is looking at a story or at a contract:

    "Dawn-grey kitchen: Rosa before a wooden table. Cobbled street: children in paper
     masks. … Small village church: candles lit, Rosa kneels."

    "Anita narrates the biblical story in a photorealistic documentary in which Anita
     moves immersively through the settings. The documentary presents epic biblical
     scenes that visually illustrate what is being narrated."

The first names places the film goes to; the second names "the settings", "the events" and
"the audience". `description_states_events` separates them — two or more distinct places,
or most beats stating a performed action — and the second kind gets **no stages at all**.
The narration supplies each clip's moment; stages invented from a contract pin the narrator
to a location the film never asked for, which is the static tableau a documentary must not
have. Instead the prompt requires one `STANDING` line saying where the narrator is relative
to the action, and forbids relocating her. The diagnosis reports `narration-driven` as
`info` in that case, and only reports `abstract-beats` when there is no narration to carry
the beats.

## The cue is `(S1)`, never `SPEAKER_00`

The transcript is diarized by pyannote, which names its voices `SPEAKER_00`, `SPEAKER_01`;
Maestro numbers them by first appearance. The assistant was handed the raw ids and copied
one straight into the lock:

    <Subject 1> (SPEAKER_00) is ALWAYS Anita.

That cue is not a number, so the lock regex matched nothing, the lock was discarded, and
the plan then inferred every voice from row order. Worse, the panel then reported *"No
SUBJECT LOCK the compiler can parse"* about a brief that plainly had one.

Three layers now close it:

| layer | what it does |
| --- | --- |
| `canonical_speaker_labels` | turns any list of ids into `["S1", "S2"]`, by first-appearance position, never by the `_00` suffix |
| `normalize_speaker_cues` | rewrites a cue in the model's answer (`(SPEAKER_00)` → `(S1)`) before it becomes the proposal |
| `_SUBJECT_LOCK_RE` | accepts any short parenthetical as a cue and normalizes it, so a saved brief with a bad cue still parses |

The structure prompt is told the canonical labels, is told never to write a diarization id,
and the report shows what the audio actually offers (`audio voice S1 (SPEAKER_00→S1)`).

`derive_subject_lock` closes the fallback too: when the model is unavailable or answers
nothing usable, the mechanical pass writes the lock itself if — and only if — the answer is
unambiguous: exactly one voice, and one name the text itself calls the speaker (scored, so
"narrates the story of David" does not crown David). A wrong lock is worse than none, so
two voices or two candidates leave the text alone.

### The narrator is in the film, not over it

A brief that says the narrator "moves immersively through the settings" and stands "at the
centre of the cinematic action" is asking for a presenter who is *in* the film. The planner
read it as narration over the image and planned every shot as an off-screen voiceover, so
the one person the film is built around appeared in none of them.

`narration_presence(brief)` recognises that language (a standing rule, "inside every
scene", "moves through the settings", "at the centre of the action", "never beside"…) and
emits a contract that reaches the planner **and** every compiled shot:

- the narrator is a visible participant of every shot, never a disembodied voice;
- every shot lists her in `subjects_on_screen` with her own cue, and the line is delivered
  ON CAMERA with the mouth visible and moving;
- never `off-screen voiceover`, `voiceover`, `narrates over the image`, or `lips remain
  completely closed` for her;
- when the line describes something else, she is still in frame — watching it, standing in
  it, gesturing at it.

The H3 guide's voiceover rule was amended to match: voiceover is only for a speaker the
project places off camera. A story brief with no presenter gets no presence rule.

## H3's prompt budget: where instructions could go missing

**H3 does not truncate the prompt.** `MiniMaxH3Qwen3VL._plain_inputs` tokenizes the whole
string (`add_special_tokens=False`, no `max_length`, no `truncation`), and the
ref2va/vision presentations are assembled from every reference; the transformer's token
constants (`MINIMAX_H3_LARGE_SEQUENCE_TOKENS = 50000`) are compute-chunking thresholds, not
text limits.

What *can* shorten a shot is Maestro's own **1024-token quality target**
(`H3_PROMPT_QUALITY_TARGET`). Above it, `fit_h3_base_prompt` runs a cleanup that removes
recognised boilerplate and repeated static-environment clauses. Measured cost of the new
context on a real shot:

| block | tokens |
| --- | --- |
| narration presence | 225 |
| cast appearance | 92 |
| picture binding | 51 |
| planner prompt (~150 words) | 280 |
| **before** subject definitions, retention and sound | **649** |

So the cleanup path is routinely exercised — which is why the injected context is now passed
as `protect=`: it is swapped for a sentinel, the cleanup runs, and the context is restored
byte-identical. A rule deleted there would reach the renderer as a missing rule.

### A number is a promise of a shot

`_number_stages` only numbers a sentence that can actually be shot: it names a place, or
somebody visibly does something, and it is not a habit. Measured on a real brief, seventeen
sentences about *how* the film is made were frozen into `Stage 1` … `Stage 17`, and the
check then reported all seventeen as stages that name no place and no action — true, and
caused by the numbering itself. A sentence that is not shootable stays in the description as
context, before the numbered stages, where it reads as a rule.

The `abstract-beats` message now says what to write instead:

> Replace the number with the place and the action it stands for: `Stage 3: the cobbled
> street at dusk — the children lift their lanterns and walk past the well`, not `Stage 3`.

`Beat 1:` and `Shot 2:` are read as stage headings too, because they are the vocabulary a
model reaches for when it has read the planner's own guidance.

## The lock carries the voice, and the text carries the pictures

A structured brief from an earlier run came back like this:

    SUBJECT LOCK:
    <Subject 1> is ALWAYS Anita.
    CHARACTERS:
    Anita: young, beautiful and charismatic woman, photorealistic documentary narrator.

Three pictures were attached to the project — Anita, David, Goliath — and nothing in the
text said so. No `(S1)` tied the Subject to the voice on the audio, no row held a face, and
David and Goliath were not described at all. The plan then invented both and, because no
row matched the picture, every shot re-drew them.

`apply_reference_roster` writes what the manifest already knows into the text, and only
that:

| the manifest says | the brief gets |
| --- | --- |
| `role: "Anita (S1)"` | `<Subject 1> (S1) is ALWAYS Anita.` and `Anita (S1): …` |
| `role: "David"` | a `REFERENCES:` entry, no cue — David never speaks |
| a path per identity picture | `REFERENCES:` naming every picture in manifest order |

The `REFERENCES:` roster names each picture with `<Picture N>` in manifest order, and the
planner is told never to write a picture tag itself: the number belongs to the renderer.

### The picture number the shot will really use

The renderer filters the manifest per shot before it numbers it, so a manifest number is
not a shot number. `_h3_picture_map_for_shot` repeats the renderer's own filter for that
shot, numbers the survivors, and injects the result into the shot's project context:

    PICTURES IN THIS SHOT (the renderer binds these exact numbers; never renumber them
    and never write a <Picture N> tag yourself):
    - <Picture 1> is Anita (S1).
    - <Picture 2> is David.
    - <Picture 3> is Goliat.

That is the same numbering the compiler writes into `subject_definitions`, so the plan and
the render agree about which picture is whom. Without it the planner did not know a picture
existed for David or Goliath and invented both faces in every shot.

`_SUBJECT_LOCK_RE` accepts the cue (`<Subject 1> (S1) is ALWAYS Anita.`) and
`project_subject_lock_cues` reads it back, so the compiler no longer infers the speaker
from row order. `_h3_shot_project_context` re-emits the line with its cue, and the same
regex now also accepts the plain `<Subject 1> is ALWAYS …` form.

Three mechanical repairs come with it, because a brief written by a model repeats itself:

- **`consolidate_cast_rows`** merges a person listed in both `CHARACTERS` and `PRESENCES`
  into one row that keeps both facts ("…undyed tunic, leather sling; does not speak").
- **`same_name`** treats `Goliat`/`Goliath`, `Abuela Rosa`/`Rosa` and casing as one
  person, so a picture cannot land on a row that does not exist and one character cannot
  read as two.
- **A label inside the description is not a section**: `STANDING RULE: …` stays in
  `DESCRIPTION`, and `beat_states_an_event` ignores the label's words — "STANDING" used
  to match the verb *standing*, which turned the one correct shape for a narrated
  documentary into a shot list and produced three false findings.

## The leak this was fixed for

The first version of this assistant produced briefs that were worse than the prose they
replaced. Measured on a real project, its own instruction text had become content:

    SUBJECT LOCK:
    <Subject 1> is ALWAYS Anita.
    <every other character>: named as presences and objects that never become S1 or S2.

    PRESENCES (only what this workflow needs):
    Ancient biblical crowds/soldiers: presences in the battle and camp stages.

Three separate faults, all now diagnosed and stripped:

1. **A placeholder line became a cast row** — `<every other character>:` is a cast member
   with no character in it. The planner read it and had to fill it in.
2. **An instruction heading leaked** — `PRESENCES (only what this workflow needs):` is the
   machine talking to itself.
3. **The beats described the format, not the film** — "Epic biblical scenes visually
   illustrate what is being narrated". Given four of those and 42 clips, the planner
   invents 42 clips. This is the wandering the user reported, and no amount of prompt
   hardening downstream can repair it: there is no story to be consistent with.

A fourth fault is structural and cannot be fixed mechanically: the same brief listed
`David: presence in the biblical action stages.` — a row that says *when* he appears and
never *how he looks*. The reference image is supplied, but nothing in the text holds the
face, so the plan drifts. `cast-without-appearance` names those rows; only the person (or
the model, told to add nothing beyond what the text implies) can fill them in.

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
       "characters":["Abuela Rosa"],"use_llm":true,
       "references":[{"type":"image","path":"C:/…/anita.png","role":"Anita (S1)",
                      "image_intent":"identity"}]}'

# Read the cast from the identity pictures only
curl -s $MAESTRO_URL/api/v1/director/brief/cast \
  -H 'Content-Type: application/json' \
  -d '{"references":[{"type":"image","path":"C:/…/anita.png","role":"Anita (S1)",
                      "image_intent":"identity"}]}'
```

`pipeline_type` is what makes the answer workflow-aware: `short_film_audio`,
`short_film_story`, `music_video`, `podcast`, `viral_video`. An unknown value is judged as
a short film, the strictest case.

## Where it lives

| file | what it is |
| --- | --- |
| [brief_assistant.py](../app/services/director/brief_assistant.py) | `diagnose_brief`, `normalized_brief`, `consolidate_cast_rows`, `structure_prompt`, `description_states_events` |
| [cast_vision.py](../app/services/director/cast_vision.py) | `cast_descriptions`, `appearance_from_reference`, `apply_cast_descriptions`, `reference_roster`, `apply_reference_roster`, `same_name`, `looks_block` |
| [brief_api.py](../app/services/director/brief_api.py) | the three routes, with the LLM injected so they are testable |
| [DirectorBriefAssistant.tsx](../ui/src/components/Sidebar/DirectorBriefAssistant.tsx) | the two buttons, the findings, the Apply/Discard proposal, the read looks |

## Tests

`tests/test_director_brief_assistant.py` (37) holds the diagnosis per code, the ordering
of errors before warnings, that every finding carries a fix, the workflow-specific
expectations (a podcast is not asked for stages, a music video is asked for sections), the
mechanical normalization — sections reordered, beats numbered from prose, a podcast not
given stages, the ideas never rewritten — and `TemplateLeakTests`, the four faults above
plus the two stripping passes and the prompt rules that forbid them.

`tests/test_director_cast_vision.py` (85) holds the read-from-the-picture path: which
references are character sheets, one read per name, the picture being what is sent, the
2K reference being sent at full detail, the prompt covering every visible detail, the
answer being the appearance, labels and refusals discarded, a raise or a missing model
leaving the brief untouched, the cap, the per-shot filter and its budget, and the apply
pass — a presence row replaced, the writer's own description kept, a missing row added. It
also pins the name matching (`Goliat`/`Goliath`, honorifics, different people never
match), the roster (manifest order, the cue, the appearance, idempotency, the brief's own
spelling winning), the cast consolidation, the `(S1)` binding end to end — including the
raw `SPEAKER_00` cue being repaired and the mechanical pass deriving a lock it can prove —
plus the per-shot `<Picture N>` map (every picture numbered with its person, the planner
told not to write the tag), the vision routing (the shipped entry point must forward the
picture, a callable that cannot carry one is refused rather than asked to invent, a model
that only thinks is retried with more room), the saved-project manifest fallback, and the
documentary case: the Anita prose yields no stages, reports `narration-driven` with audio
and `abstract-beats` without it, and the prompt forbids inventing stages while a story
brief still gets them.

`tests/test_director_brief_api.py` (10) drives the routes over ASGI: a prose brief is
reported, the route works with no model, the mechanical pass still fixes the shape, a
model answer resolves the fault, a model answer that leaks the template is cleaned before
it reaches the brief, a model that answers prose does not replace the brief, a model that
raises falls back, and `use_llm: false` never calls the model.
