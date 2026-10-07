# The Director project brief: the template the pipeline actually reads

There is one text box, but **two readers** with different rules:

| Reader | What it takes | Limit |
| --- | --- | --- |
| **The planner** (writes the shot list) | your text whole, labelled `Scene Concept:` in its prompt | none |
| **The per-shot compiler** (builds each clip's prompt) | the same text, section by section | **3,000 characters** (`_H3_CONTEXT_BUDGET`) |

That difference is what makes the order matter. The compiler keeps your sections in the order
you wrote them, but when the text exceeds 3,000 characters it **drops whole sections**,
lowest priority first. So what you write last is what you can afford to lose.

## Only one section is parsed by code: `SUBJECT LOCK`

Everything else is read as a labelled brief by the planning model. `SUBJECT LOCK` is the
exception: it is parsed, and it is the authority on which participant is which `<Subject N>`.
Without it, the numbering is inferred, and a shot can be built on the wrong person.

The syntax is exact (the parser accepts Spanish or English):

```
SUBJECT LOCK (critical, non-negotiable):
- <Subject 1> is ALWAYS Valeria. <Subject 2> is ALWAYS Ricardo.
```

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
4. **`CAMERA` and `AMBIENCE`** — the world and the look, so the film does not change aspect
   between shots. Keep the visual world here, not in `DESCRIPTION`.
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
