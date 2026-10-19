# Saved characters in Speech

In **Studio → Audio → Speech**, open **Characters** and choose **Add voice**
beside Blaine or another character. This is the same persistent library used by
Video Reference mode. Maestro loads the saved voice and fills the speaker name;
the portrait identifies the character in the picker.

Use the character's name at the beginning of each dialogue line:

```text
Blaine: Welcome to Maestro. Let me show you how saved characters work.
Yoda: Ready to begin, we are.
```

Plain monologue text also works. `Speaker 1:` and `Speaker 2:` correspond to the
numbered voice slots. Prompt Enhance receives the selected character names.
Each slot has a character selector for replacing its voice, and still accepts
an uploaded audio or video file. Uploading a replacement detaches that slot from
the saved character. Removing a slot clears its audio reference.

## Model support

| Speech model | Saved voice support |
| --- | --- |
| H3 Voice Audio | Two speakers; retains the 45-second segment and five-minute total limits. |
| KugelAudio | Up to six speakers. |
| DramaBox Audio | Two speakers. |
| Scenema Audio | Two speakers through its existing SeedVC reference path. |
| IndexTTS2 | One voice, voice plus emotion reference, or two-speaker dialogue. Adding a second character defaults to dialogue; an explicitly selected voice-plus-emotion mode keeps its second slot as an emotion reference. |
| Qwen3 Base | Two speakers through voice cloning. |
| Chatterbox | One speaker. |
| Qwen3 Custom Voice / Voice Design | The picker offers **Use with clone**, which switches to Qwen3 Base while retaining the script. These variants do not accept cloned voice audio themselves. |

Model switches respect the destination's speaker limit. For example, switching
from KugelAudio to H3 retains the first two slots; switching to Chatterbox retains
the first slot. A preset-only model clears cloning slots. Output **Load settings**
restores selected character IDs, speaker names, original filenames, and references.

### The language, and why a clone used to fail before it started

Qwen3 Base, Voice Design and Chatterbox publish their languages in the model's own
`model_mode` control (`auto`, `english`, `spanish`, ...). That control is model-owned, and what
it holds depends on the model that declares it: YuE2's composition planner and the inpaint
pickers write **numbers** into the same field, and the Studio keeps one parameter object across
model switches.

Measured on 16 October 2026: a YuE2 song was generated, voice cloning was then attempted with
Qwen3 Base, and every attempt failed in about eight seconds with
`AttributeError: 'int' object has no attribute 'lower'`. The song had left `model_mode = 2`, and
Qwen3 reads that field as a language. Eight consecutive attempts failed that way, across Qwen3
Base, Voice Design and CustomVoice.

Two halves now prevent it (`app/services/tts_model_modes.py`): where settings are validated, a
`model_mode` the model does not declare -- a leftover number, a language it cannot speak, a
stringified number -- becomes the model's own default (`auto`, or the first supported speaker
for CustomVoice), so the whole run sees the repaired value; and inside the Qwen3 pipeline the
value is normalised at the line that reads it, so a caller that validated nothing degrades to
that default instead of losing the attempt. A model whose control holds numbers is left alone,
which is what makes the repair safe to run for every generation.

In the panel: the clone therefore uses **auto** language detection (that picker is the model's
own control, and auto is the safe answer for a script in any language).

## Save a character from Speech

Choose **Save a new character**, enter its name, and select an image or video plus
a voice reference. If the character visual is a video, its audio supplies the
voice when no separate voice file is selected. Use at least two seconds of clear
speech. Maestro saves its own copies, so the character is available in Reference
mode and from other browsers connected to this installation.

Characters saved without voice audio are listed but cannot be used for cloning.
Save a character with a voice reference to use it in Speech. **Refresh saved
characters** reloads changes made elsewhere.

The library uses the existing `GET /api/v1/characters` and
`POST /api/v1/characters` endpoints. Generation uses each model's existing
`audio_guide`, `audio_guide2`, and additional supported voice inputs.

## Local validation

Validated on 5 September 2026 with the production UI build, eight new voice and
character tests, and the existing 576-test regression suite. Coverage includes
all shipped Speech models' reference modes and limits, complete speaker-label
mapping, slot removal and replacement, settings restoration, character creation,
and the character names supplied to Prompt Enhance.

Live browser checks selected Blaine and Yoda in H3, switched Qwen Voice Design
to Base with Blaine while preserving the script, and restored Blaine's character,
original voice filename, and named prompt using **Load settings**. A browser-submitted
H3 run loaded Blaine's library audio and saved 6.283 seconds of 32 kHz stereo WAV
on the RTX 4090. The seed-711 output is in **Wan-Port-Validation**. Other speech
models were checked for request compatibility without downloading or generating
with every checkpoint.
