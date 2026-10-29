# The Director project brief: the template the pipeline actually reads

There is one text box, but **two readers** with different rules:

| Reader | What it takes | Limit |
| --- | --- | --- |
| **The planner** (writes the shot list) | your text whole, labelled `Scene Concept:` in its prompt | none |
| **The per-shot compiler** (builds each clip's prompt) | the same text, section by section | **3,000 characters** (`_H3_CONTEXT_BUDGET`) |

That difference is what makes the order matter. The compiler keeps your sections in the order
you wrote them, but when the text exceeds 3,000 characters it **drops whole sections**,
lowest priority first. So what you write last is what you can afford to lose.

## `SUBJECT LOCK` is the only section whose content is parsed

Everything else is read as a labelled brief by the planning model. `SUBJECT LOCK` is the
exception: it is parsed, and it is the authority on which participant is which `<Subject N>`.
Without it, the numbering is inferred, and a shot can be built on the wrong person.

The syntax is exact (the parser accepts Spanish or English):

```
SUBJECT LOCK (critical, non-negotiable):
- <Subject 1> is ALWAYS Valeria. <Subject 2> is ALWAYS Ricardo.
```

### `DESCRIPTION:` and `CAMERA:` are read by the code too — as sections to withhold

Any line that starts with a `HEADING:` marks a section, and the text may follow on the same
line or on the next:

```
DESCRIPTION: a two-host podcast set awakens, the conversation unfolds, the set closes.
```

is the same section as

```
DESCRIPTION:
a two-host podcast set awakens, the conversation unfolds, the set closes.
```

The headings `DESCRIPTION`, `STORY`, `NARRATIVE`, `PLOT`, `ARC`, `SCRIPT`, `SCENE LIST`,
`SHOT LIST`, `CLIP PLAN` and `CAMERA` are **kept away from the clips on purpose** — see
"Creative direction: the whole film versus one clip" below. A subject row such as
`<Subject 1> (S1): a woman in red` is a data row, not a section.

## The template

```text
SUBJECT LOCK (critical, non-negotiable):
- <Subject 1> is ALWAYS <name>. <Subject 2> is ALWAYS <name>.

CHARACTERS AND BINDINGS:
<Subject 1> (S1): <age, build, hair, wardrobe head to toe>; identity comes from <Picture 1>.
<Subject 2> (S2)(SINGER): <...>; identity comes from <Picture 2>.

AUDIO-DRIVEN GENERATION:
- Video is driven by the provided audio track. Sync lip movements to the audio exactly.
- Only <who> lip-syncs. Everyone else keeps their mouth closed.
- Do not generate new dialogue. Do not invent speech. Do not add voices or characters.

VOICES:
- S1: <pitch, gender, delivery>.  S2: <pitch, gender, delivery>.

CAMERA:
- <the language of the film: lens, movement, framing habits>.

AMBIENCE:
- <the visual world: setting, lighting, palette, texture, style>. No unrelated worlds.

DESCRIPTION:
<the film's story, in as much detail as you like: what happens, in what order, and which
effects carry the meaning. This is the planner's text.>
```

### Why this order

1. **`SUBJECT LOCK` first** — it is the only parsed section, and it decides who is who.
2. **`CHARACTERS AND BINDINGS` second** — the faces and wardrobe every shot must carry.
3. **`AUDIO-DRIVEN GENERATION` and `VOICES`** — the contract that stops invented speech and
   wrong voices. These also rank highest when the 3,000-character budget forces a cut, because
   a section that names a voice's pitch or gender outranks one that merely mentions speakers.
4. **`AMBIENCE`** — the world and the look, so the film does not change aspect between shots.
   Keep the visual world here, not in `DESCRIPTION`. A `CAMERA` section belongs at this level
   too, but it travels only as far as the **planner**: measured on a real project whose brief
   asked for "orbit shots where appropiate", that heading reached all **30** compiled prompts,
   every clip orbited the same way at the same speed, and the plan's own camera -- 18 distinct
   movements, 10 of them static -- was never seen. The camera is a per-shot decision, so the
   planner reads it once and the plan carries it into the clips.
5. **`DESCRIPTION` last, and long if you want** — the story belongs to the planner. It is the
   one section the per-shot prompt deliberately does not carry: a clip that is told the whole
   film performs the whole film. The planner reads it and distributes it into shots.

### Keep the budget in mind

Everything **except `DESCRIPTION`** should fit inside about 3,000 characters, because that is
all that travels into each shot's prompt. Measured on a real project: 2,916 characters of
`CHARACTERS AND BINDINGS` + `AUDIO-DRIVEN GENERATION` + `VOICES` + `AMBIENCE` fits, and a
1,357-character `DESCRIPTION` rode along on top of it until it was stopped from doing so.

### Words that do work in the other direction

- One process with stages (a plant growing, blooming and drying) is one beat **spread over
  several shots**. Write it in `DESCRIPTION` as a progression, and let the shot list decide
  where each stage lands. Writing it as a single sentence invites the planner to put all of
  it inside one seven-second clip.
- Weather and atmosphere are a **state**, not an event: "rain falling across the warehouse"
  is a condition to carry; "it starts raining" belongs to the one shot where it changes.

## Creative direction: the whole film versus one clip

A clip prompt is seven seconds of video. Anything that travels into every clip is therefore
performed *inside every clip* — measured, not theorised: a 2,159-character brief that narrated
a podcast set awakening and closing made all fourteen rendered clips begin with the two hosts
entering the studio and the key lights coming up, while each clip's own action said they were
seated and already talking.

So direct the film at the level where each instruction is true:

| What you want to say | Where it goes | Reaches a clip? |
| --- | --- | --- |
| who these people are, their faces and wardrobe | `CHARACTERS AND BINDINGS` | yes, every clip |
| who lip-syncs, and that no speech may be invented | `AUDIO-DRIVEN GENERATION` | yes, every clip |
| each voice's pitch and delivery | `VOICES` | yes, every clip |
| the place, the light, the palette, the style | `AMBIENCE` | yes, every clip |
| the film's sequence: what happens, in what order | `DESCRIPTION` | **no** — the planner reads it once and distributes it into shots |
| lens, movement and framing habits | `CAMERA` | **no** — the plan decides the camera per shot |

The rule behind the split: **an invariant travels, a progression does not.** "Cool blue LED
strip along the desk edge" is true in every shot, so every shot is told it. "The key lights
bloom up in stages, then the hosts enter and sit down" is true in *one* shot, so only that shot
is told it — by the plan, as that shot's action.

Two practical consequences:

- If you want a specific beat in a specific clip, say it as that clip's action. The review step
  before rendering lists every clip with its own prompt and lets you edit it; a beat you place
  there is exactly where it lands.
- If you put a beat inside `DESCRIPTION`, you are trusting the planner's distribution. That is
  the intended workflow for a story, and it is what the 27B does well — it produced fourteen
  distinct prompts with eighteen distinct camera choices on the run above. What it cannot do is
  obey "do not perform this progression" while the progression is in front of it.

The honest gap: there is no first-class beat sheet yet — no input that says "beat 3 belongs to
clip 3". Today the planner owns that mapping. Option 4 in
[Director-planning-long-project-options.md](./Director-planning-long-project-options.md) is
that feature, and it is not built.

## Two things a brief can say that make every clip look alike

Both were measured on a 26-clip drama whose 26 prompts were all distinct (highest pairwise
similarity 0.42) and which still played as one static scene. Nothing was wrong with the writing
of any single prompt; the brief told every clip the same thing about stillness and place.

- **Stillness written as a habit, not as a beat.** `CAMERA: locked-off and symmetrical in the
  silences… the frame holds still and lets the absence sit` is a rule the planner applies to
  every shot it writes, so eleven of those twenty-six prompts say the frame does not move. Say
  which stages hold still — "Stage 1 and Stage 7 are locked off; everywhere else the character
  is doing something visible" — and leave the rest to the plan.
- **A story that never changes room.** If every stage is in the same place, every clip is the
  same setting with a different line over it: clips 17–26 of that run were ten consecutive shots
  inside one church.

The plan variety check reports both before anything renders, in the pipeline log and as an
amber block above the clip list in the composer — see
[Prompt-audit.md](./Prompt-audit.md#plan-variety-check). It reports; it does not rewrite. Fixing
it is either an edit to one clip's prompt, or a re-plan with the brief reworded — and re-planning
keeps the references and the setup.

The planner is also told what kind of film it is writing before it writes anything: a short film
must keep moving through places and actions, while a podcast is meant to stay in one studio and a
music video may return to the same stage on purpose. That contract lives in
[Scene-policy-per-workflow.md](./Scene-policy-per-workflow.md).
