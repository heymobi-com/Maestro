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

- **The LM Studio memory control is hidden for Strata** — it works through LM Studio's native API,
  which Strata does not implement (`/api/v0/model/unload` answers *not found*). Strata's own switch
  is `/v1/unload` and Maestro uses it by itself:
  [Automating load and unload](#automating-load-and-unload). The hidden button is about that one
  protocol, not about Maestro's ability to give the memory back.
- **No vision until it is turned on.** `/health` reports `"images": false` when the encoder is not
  installed, and Strata cannot read a reference image then — which Director planning needs, since it
  hands the writer the reference photos. See [Vision, and the reference images](#vision-and-the-reference-images).
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

What this costs you, concretely: the steps that *read* a picture run without it. And in Maestro's
Director, that is not a side path — the planners hand the writer **the reference photos themselves**
(the main image, every character reference and every location reference, through their own
`_build_all_image_paths`), so the shot plan can follow what is actually in them. Cast binding itself
does not depend on it (the compiled prompts bind identity by name and slot, and the video model is
handed the picture directly), but the writing does.

So for Director work, keep Strata's vision on. The no-vision mode is for text-only work, and the
difference in speed is small once the memory is arranged: measured here on real prompts, ~80 tok/s
with vision against ~84 without, both with 64k context and the 20 GiB arena.

If you do want the images read by a different model, pick a local vision writer — a GGUF with an
`mmproj` — in the same menu and keep Strata for the text.

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

## Automating load and unload

The writer and the video models want the same memory, and on a 48 GB machine they cannot both hold
it. That hand-over is now arranged on both sides, and neither side kills a process.

Measured here (Strata, `qwen3.8-flash-next-iq2_xs`, vision on, RTX 5080 / 48 GB):

| | RAM in use | VRAM in use |
|---|---|---|
| writer loaded | 32.7 GiB | 14915 MiB |
| **writer released** | **8.9 GiB** | **5416 MiB** |
| loaded again | 22.0 s warm · 52.7 s cold, streaming | and the call that follows runs at full speed |

**Maestro hands the memory over when a render starts.** The queue asks the writer to release in the
job that holds the generation slot, after the last prompt that job owed and before any checkpoint is
staged — the same place it lets a local planner go. That is the path every render takes, and it has to
be: a render started from the film tab never enters the Director pipeline, so the two releases inside
that pipeline were not enough. Measured here, with the writer resident and holding its arena:

| | RAM free |
|---|---|
| writer loaded, arena resident | 11.91 GiB |
| after `release_before_render` | 39.19 GiB |

```
[Pipeline 93acc71a] writer released for the render; it loads itself again on the next request
(~20-50 s, once) (9.1 GiB of RAM and 2437 MiB of VRAM still in use)
```

**Why it matters, measured from the failure it caused.** A video render failed with "CUDA reported an
out-of-memory error" while the GPU was *idle*: PyTorch had 0.03 GiB allocated, 14.5 GiB of VRAM were
free, and the traceback ended in the reference-image processor —
`qwen2_vl/image_processing_qwen2_vl_fast.py:201`, `torch.stack(images_list, dim=0)` — that is, an
allocation from **pinned host memory**, not from VRAM. The video checkpoint was pinning ~20 GB of
weights ("Unable to pin more tensors … no reserved RAM left") into memory the writer's page-locked
arena was holding. Strata's arena only grows on real work: `loaded` alone showed 13.9 GiB in use, and
the first completion took the machine to 33.8 GiB used, where it stayed.

A release that cannot happen is harmless, and at the render hand-over it is **never silent**: a hosted
API has no such switch, an endpoint that is not on this machine is never asked, a writer that is
already released is not asked twice, and a writer that is **busy** answers `409` — the render waits its
turn rather than fighting for the GPU. When nothing came back, the log names the reason, because a
hand-over that quietly did not happen is what a render reports as an out-of-memory error with an idle
GPU:

```
[Pipeline b54b4066] the writer was not released before the render: not running
```

Maestro never raises over this; a render does not fail because a convenience could not be arranged.

**Strata releases itself when it is left alone.** Its own `idle_unload_s` watches for requests:

```json
{ "idle_unload_s": 600 }
```

Ten minutes of no requests and the model goes; verified here by setting it to 60 s and watching the
model state become `unloaded` on its own. Set it to `0` to keep the model loaded always.

**The reload is automatic and announced.** `/v1/models` reports `status: {"value": "unloaded"}` and
`"auto_load": true`; the next request — plain or streaming — loads the model and answers. Maestro
counts such a writer as reachable (it *is* one call away, so a run is never refused for it) and names
the wait once instead of leaving it unexplained:

```
[LLM] http://127.0.0.1:8081 released the model to free memory; the next call loads it back
(around 20-50 s on this machine, once).
```

### A writer that is not running at all

The control also starts a stopped server, and that is the half that was missing. Reported from use:
"si no esta funcionando strata maestro no muestra el selector de model qwen3.8-flash-next-iq2_xs, es
hasta que se inicia strata que lo puede uno seleccionar" — a closed circle, because the model list
comes from the server, so there is nothing to select until the server is up.

So the panel now carries the command that starts the writer (**start command**, saved by Maestro in
`app/writer_supervision.json`, which is machine-specific and not part of the repository). When the
panel finds the writer down it runs that command once, waits for it, and asks the panel to refresh the
model list — after which the model is there to select. `Start now` does the same on demand, and
clearing the command means Maestro never starts anything by itself.

The pipeline asks for the same thing when it needs the writer, so planning does not stop at an empty
list either: with the writer down, a call waits up to 120 s for it to come up, and the honest error
arrives only if it does not.

```bash
curl http://127.0.0.1:<maestro-port>/api/v1/llm/writer/autostart
curl -X POST http://127.0.0.1:<maestro-port>/api/v1/llm/writer/autostart \
     -H 'Content-Type: application/json' -d '{"start":"powershell -File C:\\Strata\\restart-strata.ps1 -NoBrowser"}'
curl -X POST http://127.0.0.1:<maestro-port>/api/v1/llm/writer/start     # blocks until it answers
```

Verified with the server stopped: the state read `unknown` ("not running"), `start` answered
`{"started": true, "state": "loaded"}`, and `/v1/models` then listed `qwen3.8-flash-next-iq2_xs` —
which is the list the selector is built from.

Only the running application starts a process: a test suite imports these modules, and it must never
launch a server on the machine it runs on.

### The control in Settings

**Settings → LLM** shows *Writer memory* for the Strata entry: the state (`loaded` / `released`), the
address it is talking about, **Release memory**, **Load now** and a refresh. It is the manual form of
the two automatic behaviours above — for a director who knows a render is next, or who would rather
load the model before the session instead of paying for it on the first planning call.

The request goes to Maestro, not to the writer: a model server accepts `/v1/unload` and `/v1/load`
only from its own page or an origin it trusts, and Maestro's page is neither, while a server-side call
sends no `Origin` at all.

```bash
curl http://127.0.0.1:<maestro-port>/api/v1/llm/writer/memory
curl -X POST http://127.0.0.1:<maestro-port>/api/v1/llm/writer/memory/release
curl -X POST http://127.0.0.1:<maestro-port>/api/v1/llm/writer/memory/load     # blocks until ready
```

The control acts on the **address** — the one a run used, or the remote URL in Settings — and not on
which provider is selected, because the memory worth releasing belongs to the server either way.
"A local writer and a Strata URL": releasing Strata still frees 20 GiB.

### The pieces, if you want to drive it yourself

```bash
# give the memory back now (200; 409 while a request is running)
curl -X POST http://127.0.0.1:8081/v1/unload -H 'Content-Type: application/json' -d '{}'

# load it back now, ahead of a request (blocks until ready)
curl -X POST http://127.0.0.1:8081/v1/load -H 'Content-Type: application/json' -d '{}'
```

Both need JSON and no `Origin` header, which is what any server-side client sends, so Maestro needs
no browser and no elevation. They are also the reason the desktop shortcuts only matter for a hard
restart or switching modes — day to day, the writer sleeps and wakes on its own.

## Tuning for this machine, measured

Reported from use: "tarda demasiado en arrancar, el sistema completo se pone non-responsive por un rato
y al final me esta generando 43 tok/sec, la ram anda por 33/34 gb de 48 asi que hay margen". Measured
on this PC (RTX 5080 16 GB, 48 GB RAM, IQ2_XS abliterated, vision on, 64k context, KV int8), each
figure from a 5.5k-token prompt answered with ~110 tokens, one warm-up pass then the measured one:

| `--resident-budget-gib` | start | decode | prefill | system RAM | engine RAM |
|---|---|---|---|---|---|
| 12 | **28 s** | 29.8 tok/s | 491 tok/s | 24.9 GB | 14.4 GB |
| 20 | 40 s | 54.6 tok/s | 744 tok/s | 32.9 GB | 22.4 GB |
| **24** | **28 s** | **70.6-74.9 tok/s** | 958 tok/s | 37.1 GB | 26.6 GB |

The arena is the whole story: it decides how many experts are resident in page-locked RAM, and every
expert that is not resident is read per token. Lowering it makes the start shorter (8 s of copying
instead of 12) and frees RAM for the file cache -- and that does **not** pay for the missing experts
(29.8 against 70.6 tok/s). Free VRAM is the same in all three (about 1.0 GB), so this has nothing to do
with the video card.

So this machine runs at `--resident-budget-gib 24`: it starts in 28 s and answers at 70-75 tok/s, with
37 of 48 GB in use, which leaves room for the rest of the desktop.

**Idle unload is off (`"idle_unload_s": 0`).** The automatic release after ten minutes was making every
pause pay the full start again -- 28 s of copying 24 GiB into page-locked memory, which is also what
makes the desktop unresponsive while it happens. The memory is still handed over when it matters: the
render path releases the writer before an image or a video stage
([Automating load and unload](#automating-load-and-unload)), and **Release memory** in Settings does it
by hand. Set `idle_unload_s` to a number of seconds to have it give the memory back on its own after a
quiet spell, knowing that the next call then pays the start.

Smaller, still untested here: `--draft-vocab en` trades the draft head's Chinese/Japanese/Korean token
subset for the English/code one -- 110 MiB less VRAM (about 80 more expert slots) and English answers
1-2% faster, at the cost of almost no drafts for languages outside that subset. It needs a `--setup`
run because the subset is a file in the MTP folder, not a flag.

## Checking it works

The verification profile records a given structured call as JSON, not as prose:

```sh
curl http://127.0.0.1:8081/v1/models
```

then run any structured step (Director planning, prompt enhancement) and look for the reply being
JSON rather than an explanation. In Maestro's log, a structured call to Strata reports
`reasoning=0 tokens`; before this, the same call could report the whole allowance spent on reasoning
with nothing to show.
