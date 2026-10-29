# Prompt audit

Two prompts that disagree with each other, or one prompt that asks the renderer
for two incompatible things, do not fail. The renderer picks one, averages them,
or invents a third reading — and by then the run is paid for. The audit is the
step that names those contradictions in seconds, before the model is loaded.

It changes nothing. It reports; repairing stays the separate, opt-in step it was.

## What it checks

Two families of findings, all textual, all in the same shape —
`{shot, index, findings: [{code, severity, message}]}`:

| code | severity | what it catches |
|---|---|---|
| `subject-swap` | error | a shot declaring a Subject for the wrong person, judged against the project's own SUBJECT LOCK |
| `subject-field-duplicated` | error | `subject_definitions` written twice, which reads as a second cast |
| `line-without-speaker` | warning | a dialogue line no shot claims |
| `action-section-missing` | warning | a draft whose action is loose prose with nothing separating it from the project text. An H3-native draft writes `Story handoff:`, `Visible cast:`, `Action:` and `Final beat:` instead, which is structure and not loose prose |
| `project-text-in-draft` | warning | the project's shared text copied into a shot instead of its own action — measured by finding the project's own sentences in the draft, not by the draft's length |
| `brief` | warning | a brief whose budget drops sections a shot needed |
| **`clip-holds-two-shots`** | error | a one-shot body that names another shot at the start of a line, or that keeps a stray `At MM:SS.mmm` from the planner's own clock |
| **`brief-heading-in-clip`** | error | the brief's own `CAMERA:`-style heading reaching a clip, where it overrides the plan identically in every shot |
| **`camera-contradiction`** | warning | a camera declared locked and moving at once |
| **`lighting-contradiction`** | warning | a shot lit by daylight and by night |
| **`wardrobe-contradiction`** | error | one Subject dressed as two different people, judged by the garments each entry names |
| **`music-contradiction`** | warning | `non_diegetic_music:` saying silence over a scene that describes a violin |

The last six are the contradictions *inside* a prompt and live in
`app/services/director/prompt_contradictions.py`; the first six compare each shot
against the project's contract and live in `app/services/director/prompt_audit.py`.

## Where it runs

- **Before a render.** `_audit_prompts_before_generating` audits the compiled plans
  in `director_pipeline.py` and prints the report, so a run that is about to spend
  GPU hours says what it found. Nothing is blocked.
- **In the interface.** The Director dashboard reads the same audit back from the
  saved plan and shows it above the planning log, per shot, before anything is
  dispatched.
- **Over HTTP**, for a script or a terminal:

```bash
curl -s http://127.0.0.1:<port>/api/v1/director/pipelines/<pid>/prompt-audit | jq .totals
```

```python
import requests

audit = requests.get(
    f"http://127.0.0.1:{port}/api/v1/director/pipelines/{pid}/prompt-audit",
    timeout=60,
).json()
for shot in audit["findings"]:
    for finding in shot["findings"]:
        print(shot["shot"], finding["severity"], finding["code"], finding["message"])
```

```javascript
const audit = await fetch(`/api/v1/director/pipelines/${pid}/prompt-audit`).then(r => r.json())
console.log(audit.totals)   // { "clip-holds-two-shots": 19, ... }
console.log(audit.report)   // the same text the server log prints
```

`404` means the pipeline has no saved plan yet. The audit needs no GPU and no live
run: it answers for a finished project as well as for one about to be rendered.

Each finding in the response says where it lives and what can be done about it:

```json
{
  "shot": 2, "index": 1,
  "prompt": "<the compiled prompt this shot renders>",
  "findings": [{
    "code": "clip-holds-two-shots", "severity": "error", "message": "...",
    "in_prompt": true,
    "fix": "<the same prompt with the stray timestamp removed, or \"\">"
  }]
}
```

## Correcting from the panel

The audit never applies anything. Every finding it shows comes with a way to fix it,
and the director saves:

- **`suggest the fix`** appears only when the contradiction is a leftover piece of text
  — a stray `At MM:SS.mmm`, a marker naming another shot, a leaked `CAMERA:` heading.
  It puts the corrected text in the editor *without saving it*, so the change is read
  before it is applied. The suggestion is withheld when the same heading reaches the
  pre-polish draft as well, because editing the compiled prompt would then clear
  nothing and the button would be a lie.
- **`fix`** opens the shot's compiled prompt in the same editor, for everything that
  needs a decision: two descriptions disagreeing, a subject in two outfits, a camera
  locked and moving. No rule knows which of the two the director meant.
- **`lives in the pre-polish draft`** marks a finding that the compiled prompt does not
  carry. The editor writes the compiled prompt, so those are reported and left alone.
- Saving goes through the endpoint the clip cards already save with
  (`PUT /api/v1/director/pipelines/{pid}/clips/{index}/prompt`), so an edit here and an
  edit there are the same edit, and the plan — and this audit — reload behind both.

Nothing is blocked and nothing is rewritten behind the director's back: the finding is
signalled, the correction is offered, and the write happens when they press save.

Measured end to end on a real 178-shot project: 19 findings of `clip-holds-two-shots`,
one suggested fix applied to shot 2, and the audit came back at 18 — with the shot's
unrelated `project-text-in-draft` finding still there, exactly as it should be.

## The rules that keep it quiet

A noisy audit is an audit nobody reads, so every check was sharpened against 2156
compiled prompts of 41 finished projects, and the false positives it now avoids
are the ones that measurement found:

- **A marker is not a cut unless it starts a line.** `retention_analysis: <Subject 1>
  (appears in [Shot 3])` names the shots a subject appears in; the compile step
  labels its own shot. Both are bookkeeping. 154 findings came from reading them as
  cuts; a marker only counts when it writes a new beat on its own line, and only
  when it names a shot other than the clip's own (the pipeline hands the audit each
  clip's number for exactly this).
- **A timestamp at zero is the start, not a beat.** `At MM:00.000` opens the clip.
  `At MM:04.000` in a seven-second clip is the planner's own clock — measured on a
  real 30-shot project, 11 clips carried one each, and `h3_single_shot_times.py`
  already strips them at compile time. A prompt that still carries one was written
  around that step.
- **Camera wording has to be about the camera.** "A moment of stillness" is a
  person; "she tilts her head" is not a camera move; a static camera can tilt or
  zoom. So the word must sit in a sentence that also names the camera, the shot or
  the frame, and only *positional* moves (orbit, dolly, pan, push-in…) conflict with
  a locked camera.
- **Two outfits are told apart by garments, not by words.** `her silky red dress and
  red heels` against `red high heels` is one dress and one pair of heels described
  twice; `dark pants, white shirt, and a black leather vest` against `working boots`
  is the same outfit's feet. Only a whole-body garment (dress, robe, uniform) against
  separate pieces (shirt, trousers, vest, suit) is reported, because that is the
  shape of two different people: one Subject in a dress and, further down the same
  prompt, in a shirt, a vest and dark pants. A sweater under a blazer and a coat over
  a dress are layering, not contradiction.
- **The short subject tag counts.** Real prompts write `Roman (S2)` in the prose. A
  garment is charged to the last Subject seen, and without reading `(S2)` a wardrobe
  a later paragraph describes was charged to the wrong person.
- **The name a cast list writes counts, and a draft's length is not evidence.** A shot
  repeats the cast table without any tag beside it, and charging the last Subject seen
  gave every untagged table whichever tag happened to be nearest: on a real project that
  was the `<Subject 3>` of the project's own `CHARACTERS AND BINDINGS` block, so Aria's
  vivid red mini dress and Hector's charcoal suit jacket landed on one Subject and all
  ten shots reported an error-level `wardrobe-contradiction`. The garment is charged to
  the name its own entry writes instead. The same project measured the other half: its
  6980-character draft carried **none** of the project's text — no `SUBJECT LOCK`, no
  `CHARACTERS AND BINDINGS`, no `identity comes from` — it repeated the shot's own cast
  table three times. So `project-text-in-draft` looks for the project's own sentences
  inside the draft rather than reading its size, and the H3-native draft dialect
  (`Story handoff:`, `Visible cast:`, `Action:`, `Final beat:`, `OPENING CONTINUITY:`)
  counts as structured rather than as loose prose.
- **The music field does not contradict itself.** `non_diegetic_music` contains the
  word "music", and a `<Audio 1>` reference line describes a borrowed style rather
  than a score, so both are removed before the scene is searched for music words.

## What it found on 2156 real prompts

| code | findings | projects affected |
|---|---|---|
| `clip-holds-two-shots` | 113 | 5 of 41 |
| `camera-contradiction` | 45 | 15 of 41 |
| `brief-heading-in-clip` | 7 | 1 of 41 |
| `wardrobe-contradiction` | 2 | 2 of 41 |

The two wardrobe findings are both the signature the check exists for: one Subject
in a dress and, in the same prompt, in a shirt and a leather vest over dark pants —
the "same dress, different girl" case, where the wardrobe comes off one description
and the identity off the reference.

Re-measured after the two sharpenings above, on the project they came from (10 shots,
`prueba-2`): ten `action-section-missing`, ten `project-text-in-draft` and ten
error-level `wardrobe-contradiction` went to **zero**, and the report reads "no
inconsistencies found". No rule was relaxed to get there: the same classifier still
reports one Subject in a dress and in a shirt and a vest in one prompt, which is the
case the reduction would have had to keep.

## Plan variety check

The audit reads each prompt for contradictions. This one reads the plan as a
viewer would and asks a different question: will these shots look like the same
shot? It runs at the end of planning, right before the review pause, so the
answer arrives while nothing has been rendered yet, and again before the first
clip is submitted.

It reports three things, and only when they cross a line. The line depends on the
workflow — a podcast is *meant* to hold one studio — so see
[Scene-policy-per-workflow.md](./Scene-policy-per-workflow.md) for the numbers each
workflow publishes.

| finding | threshold (short film) | what it means |
|---|---|---|
| prompts repeated verbatim | any | one clip's prompt is another clip's prompt, character for character |
| the frame declared still | a fifth of the shots or more | the prompt itself says the frame does not move (`remains visually still`, `No movement visible`, `stands perfectly still`). One or two are a choice; eleven of twenty-six is a slideshow |
| one setting held too long | three or more consecutive clips in one place | adjacent clips whose settings name the same place (church, altar, corridor, kitchen…), with one place dominant across the run. Wording is ignored, so "Stone church interior, wooden pews" and "Church altar, golden light" are one setting |

Distinct prompts are not the same thing as distinct shots. Measured on a real
26-clip drama: **26 of 26 prompts were distinct** (highest pairwise similarity
0.42, 25 of 26 settings distinct as text), and the run still looked repetitive,
because clips 17–26 were all inside one church and 11 clips declared the frame
itself motionless. That is the failure this check exists to catch, and the reason
it is measured on settings and stillness rather than on prompt wording.

The findings print in the pipeline log as `Plan variety check:` and are published
on the pipeline as `plan_warnings`, which the Director composer shows as an amber
block above the clip list. They are not a failure: the plan renders exactly as it
was written. Editing a clip's prompt, or re-planning with a brief that asks for
action instead of stillness, changes them — planning again keeps the references
and the setup.

Two things in a brief cause this and are worth writing differently:

- **A stillness instruction stated as a general rule.** `CAMERA: locked-off and
  symmetrical… the frame holds still and lets the absence sit` is applied to every
  shot the planner writes. Name the one or two stages that hold still and say what
  the character does everywhere else.
- **A story told in one room.** If the brief's stages all sit in the same place,
  every clip is the same setting with a different line over it.

The planners are told the same thing in their own prompt, per workflow, so this is
usually fixed before the check finds anything — see
[Scene-policy-per-workflow.md](./Scene-policy-per-workflow.md).

## Tests

`tests/test_prompt_contradictions.py` holds one case per code plus the guards the
measurement added: a healthy prompt, the same outfit described twice, a hold-then-pan,
a panorama that is not a pan, a marker inside a sentence, a timestamp at zero, a
sweater under a blazer, and the wiring — that the router serves the path the
interface asks for, that each clip is told which shot it is, that a suggestion clears
its finding, that a suggestion is withheld when it would clear nothing, and that the
panel saves through the endpoint the clip cards use. `tests/test_project_prompt_audit.py`
adds the other two: a draft that really repeats the project's text is named, a draft that
is only long is not, and a native H3 draft is not read as loose prose.

`tests/test_director_plan_variety.py` holds the variety check: a clean plan stays
quiet, a repeated prompt is named by clip, a fifth of the shots frozen is reported
and two in twelve is not, ambient-only motion is not mistaken for performance, an
action inside an ambient sentence still counts, eight clips at one altar are one
run while a move through corridor, chapel and kitchen is not, and the findings are
published on the pipeline and cleared by a clean re-plan. It also holds the
workflow-aware half: the same plan is reported for a short film, silent for a
podcast, and a music video may hold one stage longer than a short film may.
`tests/test_scene_policy.py` holds the policy itself — the contract text per
workflow, the limits, alias and fallback resolution, and that every planner
interpolates its block into the prompt it sends.
