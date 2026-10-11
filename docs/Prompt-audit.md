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
| `action-section-missing` | warning | a draft whose action is loose prose with nothing separating it from the project text |
| `project-text-in-draft` | error | the project's shared text copied into a shot instead of its own action |
| `brief` | warning | a brief whose budget drops sections a shot needed |
| **`clip-holds-two-shots`** | error | a one-shot body that names another shot at the start of a line, or that keeps a stray `At MM:SS.mmm` from the planner's own clock |
| **`brief-heading-in-clip`** | error | the brief's own `CAMERA:`-style heading reaching a clip, where it overrides the plan identically in every shot |
| **`camera-contradiction`** | warning | a camera declared locked and moving at once |
| **`lighting-contradiction`** | warning | a shot lit by daylight and by night |
| **`wardrobe-contradiction`** | error | one Subject dressed as two different people |
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

## Tests

`tests/test_prompt_contradictions.py` holds one case per code plus the guards the
measurement added: a healthy prompt, the same outfit described twice, a hold-then-pan,
a panorama that is not a pan, a marker inside a sentence, a timestamp at zero, a
sweater under a blazer, and the wiring — that the router serves the path the
interface asks for and that each clip is told which shot it is.
