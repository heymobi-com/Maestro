# A brief the pipeline can read: Valdecuna

This is the corrected brief for the Valdecuna short film, written in the exact shape the
Director pipeline parses. It exists because each of its rules was earned by a failure:

| what it prevents | the rule that prevents it |
| --- | --- |
| the planner numbering participants differently than the compiler | `<Subject 1> is ALWAYS Abuela Rosa.` — the only lock shape `project_subject_lock()` parses. "Abuela Rosa is ALWAYS Abuela Rosa" produces no lock at all |
| a shot arriving with no position in the story | `Stage N (label):` headings — `_brief_stage_list()` numbers the stages and each clip gets the one its position has reached. Without them the sentences are used as beats, which is more fragile |
| one 8-second shot walking through three places | one stage = one place = one moment, stated in the section header |
| a character showing up in scenes that are not his | `PRESENCES — each one exists ONLY in the stage named beside it` |
| the shrouded figure "becoming" the priest | "two different beings: the shroud opens and he is revealed, there is no transformation" |
| one camera move applied to every shot | the camera is written per stage, with no "whenever", "always", "every time" or "throughout", which the planner reads as a rule for the whole film |

Paste the block below into Director's brief field.

```text
SUBJECT LOCK (only S1 and S2 can lip-sync; nothing else ever becomes them):
<Subject 1> is ALWAYS Abuela Rosa.
<Subject 2> is ALWAYS la Figura del Purgatorio.
Don Aurelio, Los Cinco Niños, El Pueblo, La Campana and La Puerta Negra are presences and
objects: they never become S1 or S2, never lip-sync and never carry dialogue.

CHARACTERS:
S1 Abuela Rosa: woman, 78, short and stooped, grey hair under a black headscarf, deep
   wrinkles, pale clear eyes; long dark wool skirt, thick grey cardigan, worn apron, house
   slippers, wooden rosary at her neck.
S2 La Figura del Purgatorio: tall, gaunt, gender-ambiguous, elongated; wrapped in a trailing
   black shroud, bare pale feet; head tilted; face smooth and erased — no eyes, nose or
   mouth, only a blank grey surface; a belt of small bronze bells at the neck.

PRESENCES — each one exists ONLY in the stage named beside it:
Don Aurelio: man, 60, thin, grey hair, kind tired face, eyes closed, faint smile; worn
   yellowish alb, faded chasuble, stole. Only in Stage 5, standing where the shroud opened.
   He and la Figura are two different beings: the shroud opens and he is revealed, there is
   no transformation, and he is in no other stage.
Los Cinco Niños: five children, 6-10, humble old clothes; empty paper masks (skull, witch,
   ghost) with darkness behind. Only in Stage 2 (masks) and Stage 6 (white, luminous faces).
El Pueblo: villagers of all ages, plain dark rural clothing, no faces to camera; pumpkin
   lanterns and paper masks. Only in Stage 2 and at the back of the church in Stage 6.
La Campana: old broken bronze bell, green patina, cracked lip, on a rope between twisted
   trees. Only in Stage 3.
La Puerta Negra: narrow black wooden door, no handle, no keyhole, ajar with blue light
   behind it. Only in Stage 4; closed and turned into a small chapel in Stage 7.

CAMERA (per stage — one move carries one stage, not the film):
35mm anamorphic, shallow depth of field. Change the shot size and the move from shot to
shot; never repeat the previous shot's size and move together.
- Stage 1: locked-off and symmetrical.
- Stage 2: slow lateral track past the masks.
- Stage 3: low-angle slow orbit around La Campana.
- Stage 4: handheld behind Abuela Rosa's shoulder, then settling.
- Stage 5: one slow push-in on the shroud as it opens, then a slow tilt-up to candlelight.
- Stage 6: eye-level for Abuela Rosa, a single push-in at her first tear.
- Stage 7: locked-off and centred.

AMBIENCE:
Stone mountain village at the edge of a dark forest, fog without rain, cobbled streets, slate
roofs, pumpkin lanterns. Palette from cold grey-blue-black to ochre and warm gold-white.
Damp stone, worn wood, linen, paper masks, bronze. Folk-horror Catholic realism, painterly,
candlelight and incense haze.

DESCRIPTION (one stage per beat; each stage is ONE place and ONE moment):
Stage 1 (kitchen, dawn): Abuela Rosa before a wooden table with a plate, a steaming cup and
   a knife driven into the wood; fog outside the window. Only Abuela Rosa is in frame.
Stage 2 (street, dusk): children in paper masks carrying pumpkin lanterns walk in silence,
   the villagers watching; the streetlamps flicker in unison and the cables creak. Only the
   children and the villagers are in frame.
Stage 3 (forest, night): a broken bronze bell sways on its rope between twisted trees in the
   fog, blue moonlight. Nobody is in frame.
Stage 4 (corridor, night): Abuela Rosa walks the narrow stone corridor; the black door is
   ajar with blue light behind it; la Figura del Purgatorio enters behind her, head tilted,
   bells swaying; she turns; the figure raises one hand toward the window; outside, the paper
   mask is empty; her fingers desaturate into paper. Only those two are in frame.
Stage 5 (church threshold, night): the shroud opens like an old chasuble and Don Aurelio is
   revealed inside it — one subject, the shroud opening, never the figure and the priest
   side by side. Behind him the five children stand at the altar, faces luminous.
Stage 6 (church interior, night): candles lit, five new flames, golden light, incense;
   Abuela Rosa kneels, hands joined, head bowed, shoulders trembling, tears. The villagers
   stand at the back, mouths barely open for one low "Amén". The bell sounds one last time.
Stage 7 (corridor, dawn): the black door is shut and turned into a small domestic chapel —
   crucifix, wooden altar, candles; on the kitchen table a broken bread, a cup and an open
   book; the clock ticking. Only Abuela Rosa is in frame.

MEANING: carried by three effects only — the bell's timbre (distant bronze → internal
sub-bass → clean liturgical bell), the palette shift, and paper texture migrating from the
masks onto Abuela Rosa's hands and then dissolving into light.

NEGATIVE:
No extra characters, no demons with horns, no gore, no blood, no faces on El Pueblo, no
modern clothing, no urban setting, no jump scares, no whip pans, no generated speech.
```

## What the pipeline makes of it

`tests/test_brief_template_reads.py` runs this file's own block through the real parsers, so
the template cannot drift into a shape the code no longer reads:

- `project_subject_lock()` → `{1: "Abuela", 2: "Figura"}`
- `_brief_stage_list()` → seven numbered stages, no stray match from the cast rules
- `_stage_for_clip()` → clip 1 is Stage 1, clip 25 is Stage 7, never backwards
- `_h3_shot_project_context()` → each clip's context is the ambience plus its own stage, and
  Don Aurelio appears in the context of the Stage 5 clips only
