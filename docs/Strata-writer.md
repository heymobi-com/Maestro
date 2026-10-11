# Using Strata as the writer

[Strata](http://127.0.0.1:8081) serves an OpenAI-compatible API, so Maestro can use it as the
writer that plans and rewrites prompts while the video models stay the thing that renders. Choose
**Settings → LLM → Strata** and Maestro fills in `http://127.0.0.1:8081`; press **Load** and the
server's own model appears in the list.

Nothing else has to be configured. The rest of this page is what Maestro changes for it, what it
cannot do, and how to check it.

## What Maestro does differently

Maestro's remote path sends the OpenAI common denominator: it drops the llama.cpp extensions and,
because only the bundled llama-server can compile a grammar, it drops a requested JSON schema and
calls out unconstrained. That is right for a hosted API and wrong for a local server that supports
more.

Both were measured against Strata (`qwen3.8-flash-next-iq2_xs`):

| Sent | Result |
|---|---|
| a schema in llama.cpp's form | misread — the schema itself came back as the answer |
| a schema in the **OpenAI standard** form | valid JSON that conformed to the schema exactly |
| nothing, on a tight token allowance | the whole allowance went to `reasoning_content` and `content` came back **empty** |
| `reasoning_effort: "none"` | no reasoning, the answer straight away |

So for a server that introduces itself as Strata, Maestro sends the OpenAI schema form and silences
the reasoning when the call asked for an answer rather than a deliberation. Both are fields Maestro
already knew how to send — it had no reason to believe the endpoint could use them.

## How it is recognised, and why nothing else is affected

Maestro asks `GET <server>/health` and believes the answer only when it says `"service": "strata"`.
Only a server on this machine or the LAN is asked at all, so a hosted API never receives the extra
request. One unanswered probe means the common denominator, which is the behaviour every other
endpoint already got. The answer is cached for a minute.

`http://127.0.0.1:8081/health` shows what Maestro sees:

```sh
curl http://127.0.0.1:8081/health
# {"status": "ok", "max_context": 65536, "model": "qwen3.8-flash-next-iq2_xs",
#  "images": false, "api_key": false, "loaded": true, "service": "strata"}
```

## What it cannot do

- **Maestro cannot unload Strata's memory for you.** [LM Studio's memory
  control](./LM-Studio-memory.md) works through LM Studio's native API, and Strata does not
  implement it (`/api/v0/model/unload` answers *not found*). The control is hidden for the Strata
  entry rather than offering a button that cannot work. When a video model needs the RAM back, stop
  Strata's server yourself. This is the opposite of Maestro's own local writer, which it releases on
  demand and after an idle timeout.
- **No vision.** `/health` reports `"images": false` and `/v1/models` lists
  `"input_modalities": ["text"]`: Strata cannot read a reference image. See
  [Vision, and the reference images](#vision-and-the-reference-images) below.
- **A model on another host by DNS name** is not probed. Point the Strata entry at an address, not a
  hostname, if the server lives elsewhere on the LAN.

## Vision, and the reference images

Measured: an image block sent to Strata comes back **400 Bad Request**, with no body, in the middle
of a plan — and the thing that fails is a character reference the writer was supposed to read.

The remote path used to make that worse. `load_model` set `_vision_available = True` for *every*
remote writer and left it to the provider to complain, so a Strata run died on transmission instead
of degrading. Now the server is asked what it accepts, and a text-only writer is believed:

- the reference images are left out of the request, which is the degrade path Maestro already had for
  a local model with no `mmproj` — the prompt goes as plain text, and the guides that adapt to
  images choose their text-only form;
- connecting to it says so, once, in the log:
  `[LLM] http://127.0.0.1:8081 takes text only: reference images will not reach it.`
- a server that says nothing keeps the optimistic behaviour, because a hosted API cannot be asked and
  dropping its images would break what works today.

What this costs you, concretely: the steps that *read* a picture run without it — the prompt
enhancer with a reference image, and H3's reference-sequence and sliding-window planners, which
receive the frames so the plan can follow them. Cast identity itself does **not** depend on the
writer's eyes: the compiled prompts bind it by name and slot (`<Subject 1> … identity comes from
<Picture 1>`), and the video model is handed the picture directly. So a text-only writer still plans
a multi-reference project; it just cannot describe what it has not seen.

If a step genuinely needs the images read, run that work on a local vision writer — a GGUF with an
`mmproj`, chosen in the same menu — and keep Strata for the text.

### Turning Strata's own vision on

Strata *can* read pictures; this install answered no when setup asked. The setup log says it in one
line:

```
Images: the model can also read pictures (screenshots, photos, scanned pages). This adds a 0.9 GB ...
[ok] images: off
```

The engine's flag is `--vision yes|no|gpu|cpu`, and `--yes` — the recommended answer to everything,
which is what a scripted install takes — leaves images off. To turn them on, re-run setup with the
flag (`cpu` on an AMD card, `gpu` on NVIDIA):

```sh
C:\Strata\START-HERE.bat --setup --vision gpu
```

That adds the encoder and rewrites `strata-iq2_xs.json` with the flag, and nothing else changes: the
launch script, the port and the pack stay. After a restart, `/health` reports `"images": true`, and
Maestro follows it — the capability is read from that field every time a writer is connected, so the
reference images start travelling again without a Maestro change.

The paperclip in Strata's own chat is the chat's attachment control, not a statement about the
model: an image attached while images are off still cannot be read.

## What Maestro will not pretend

A remote writer is not loaded by Maestro, so nothing about it can be inferred from this machine: the
server's own answer is the only honest one. Two things follow from that.

**"Loaded" means the server answers.** A writer on this machine or the LAN is asked, and the answer
is cached for a few seconds; a hosted API is taken at its word, because polling it would be both
pointless and impolite, and its reachability is not something you can fix here. So when Strata is
stopped, the writer stops showing as loaded instead of the run dying later with a connection error:

```
LLM request failed: HTTPConnectionPool(host='127.0.0.1', port=8081): Max retries exceeded
The writer at http://127.0.0.1:8081 is not answering. Start that server (for example Strata) or
choose a different writer in Settings -> LLM.
```

That is what makes stopping Strata for a render practical: you are told, by name, what to start.

**A run keeps the address it started with.** A Director run is bound to the writer it was created
with -- provider and model -- and every planning phase re-loads that one, so a later change in
Settings does not rewrite a run already underway. The address is now part of that record too: the
first phase writes it into the run, and the run's own value wins from then on. Without it, a run
silently followed a later change of address, and if a different server had moved in, the run was
finished by a different writer with the same name.

The record lives in the run's parameters, so it lasts as long as the run does in this session
(phases, reruns, and a queue entry frozen with it). A run rebuilt from disk after a Maestro restart
takes the current setting, exactly as it did before this existed.

## Checking it works

The verification profile records a given structured call as JSON, not as prose:

```sh
curl http://127.0.0.1:8081/v1/models
```

then run any structured step (Director planning, prompt enhancement) and look for the reply being
JSON rather than an explanation. In Maestro's log, a structured call to Strata reports
`reasoning=0 tokens`; before this, the same call could report the whole allowance spent on reasoning
with nothing to show.
