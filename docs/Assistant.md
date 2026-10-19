# The assistant

A chat that follows the person around the studio: the Studio, the Director, the Editor and the
settings. It answers questions about the project they are looking at, reads the plan and the
render log when the answer needs them, takes an image with a question, and can propose an edit
to one shot's prompt.

It is not a second writer. It has its own endpoint, its own model and its own credential, and
it never touches `llm_service` -- which is the whole point, and the reason it was built this way
rather than as another caller of the pipeline's LLM.

## Why it has its own endpoint

`llm_service` holds one model for the whole app and every Director pass loads through it. That is
right for planning and wrong for a chat, which is interactive and is often used *while* clips are
rendering:

| The assistant on... | What happens |
|---|---|
| the pipeline's local model | a chat turn waits for the generation slot and then competes for the same GPU the video model holds |
| a hosted provider through `llm_service` | loading it unloads the pipeline's local model, so the next planning pass pays the reload |

So the assistant gets a provider, a model, an endpoint and a key of its own. A llama.cpp on this
machine or on the LAN is reached the way every other OpenAI-compatible server is -- provider
**Remote OpenAI-Compatible** with its own URL -- and the pipeline's writer is never loaded,
unloaded or asked for anything.

An empty provider, and `local`, deliberately resolve back to `llm_service`: a local model has to
go through that service because it is what owns llama-server, and pretending otherwise would
start a second server on the same GPU. The panel says which of the two is in force, and what it
costs.

**When the chat shares the writer, the panel says so.** That happens two ways, and both are
contended: the assistant has no endpoint of its own (so the pipeline's model answers), or it is
pointed at the very same address as the pipeline's writer. The second is the one a person would not
expect -- it looks separate while every turn queues behind whatever is rendering. Measured on this
machine, with Strata as the writer, that contention is the difference between the writer's full
speed and the writer answering a question instead of planning the film.

### Talking to the writer the same way everything else does

An endpoint call from the assistant is sent through `llm_remote_dialect`, exactly like every other
Maestro writer call, and that is not decoration. For a Strata endpoint the dialect turns "I want an
answer, not a deliberation" into `reasoning_effort: none` plus a hard `reasoning_budget_tokens`
cap. Posting straight to `/v1/chat/completions` -- which this did first -- left the reasoning
uncapped, and measured on that server an uncapped call ran to **24,605 tokens in 700 seconds** with
nothing to show. One such call occupies the engine, so the pipeline's own planning measures slower
behind it: the reported "the assistant loads and pulls resources". The request therefore carries
`enable_thinking: false`, the dialect reads it, and a hosted API that knows nothing about it
receives the plain OpenAI payload it has always had.

**Anthropic is not offered here on purpose.** Its API wants its own headers and its own body shape,
which is a second dialect this path does not implement; a control that fails on first use is worse
than no control. It is reachable the other way: set the pipeline's provider to Anthropic and leave
the assistant unset, so it follows the pipeline through `llm_service`, which does speak it.

## Setting it up

**Settings → Services → Assistant LLM Provider.** Or the panel is reachable from the assistant
itself when it is open.

| Field | What to put there |
|---|---|
| Provider | Remote OpenAI-Compatible for anything that speaks `/v1/chat/completions` |
| Assistant Server URL | `http://127.0.0.1:8080` for a llama.cpp on this machine, `http://192.168.1.20:8080` for one on the LAN. Empty uses the pipeline's endpoint |
| Assistant Model | the model id that endpoint serves; the field lists what it reports, and is filled in for you when the server publishes one model |
| Answer length | the token budget for one reply, 256 to 16384 |
| Assistant API Key | only for a hosted API; a local llama.cpp usually needs none |

Every field here is written on its own -- the provider when it is chosen, the address and the model
when their field is left, the key when it is saved -- so **a field the panel is not editing keeps
what it had**. A partial save that reverts another field would be a bug: for the provider it made
the address and model fields disappear and read as "it does not save my changes".

### It recognises the server you point it at

Entering an address is enough. On the way out of the field the address is asked what it serves
(`GET /v1/models`, with the key when one is set, bounded to 6 seconds because somebody is waiting
on it), and:

- **one model** -- it is filled in and saved, because the server has already answered the question
  the field asks;
- **two or more** -- they are offered and nothing is written: picking the first of them would be a
  guess written into a setting;
- **no answer** -- the field says why, in the address it could not reach, and the model stays
  free text. Type the name the server expects; anything typed is kept as written.

The list says where it came from, and that matters more than it sounds. The pipeline's own picker
(`/api/v1/llm/models`) prepends Maestro's built-in catalogue to whatever a remote answered and
sends a masked credential, so through it an address that never replied and a server that answered
look identical -- the list is populated either way, and the id saved is one that server does not
serve. Here an address that did not answer lists **nothing**, `local` is named as the catalogue it
is, and the probe is the same URL the chat will really call, so a pasted `/v1` or `/` is trimmed
rather than doubled.

A model belongs to the address it was chosen for. Pointing the assistant at a *different* address
does **not** inherit the pipeline's model: that server does not serve it, and a turn sent anyway
fails with a 404 that reads like a broken setting. The panel says `this endpoint has no model`
until one is chosen, and trying to chat says the same thing in words.

Maestro's own local writer is already a light Gemma 4 4B (`Abhiray/gemma-4-E4B-it-heretic-GGUF`),
which is the size this works well at. A bigger model gives better answers and a slower chat.

**The one thing to get right:** give the assistant an engine of its own, on a different address
from the writer the render uses. Sharing it works and is allowed -- the panel warns when you do --
but a chat turn occupies that engine, so a question and a plan wait for each other. A second
llama.cpp on another port (8082, say) is enough, and it costs no GPU when it is idle.

### Vision

Whether an image can be sent is asked of the endpoint, not assumed:

- a **hosted API** cannot be asked, so images are sent, which is what it accepts today;
- a **local server** is probed through `/health` and `/v1/models`. A llama.cpp publishes neither
  field, so it counts as unknown -- and the turn is **retried without the picture** if the endpoint
  refuses it, rather than failing. A llama.cpp without its `mmproj` loaded therefore answers the
  question and drops the image, and the log says so once.

The panel states it plainly: whether images are sent, and whether the text and image stay on this
machine, on another machine of the network, or leave it.

## What it can read, and what it can propose

The reply is prose plus, at most, one JSON block of actions. That is a contract rather than
native tool-calling on purpose: nothing in this repository speaks `tool_calls`, and a quantized
local model asked for the native form tends to invent it. Every endpoint that can hold a JSON
block can hold this one, and a malformed block costs the block and nothing else -- the prose is
kept and no action runs.

| Tool | Kind | What it does |
|---|---|---|
| `get_pipeline_state` | read | the plan: shots, groups, strategies, durations, what is rendered |
| `get_clip_prompt` | read | one shot's compiled prompt and the draft it came from |
| `get_prompt_audit` | read | the contradictions the prompt audit found |
| `get_recent_logs` | read | the tail of `logs/api/start.js/latest` |
| `get_project_text` | read | the project's shared brief |
| `propose_clip_prompt` | **propose** | the whole new prompt for one shot |
| `propose_clip_tag` | **propose** | a short label for one shot |

Reads are executed and their results are handed back for one more round -- two rounds at most,
which is enough for "read the plan, then read the shot it points at" and is a bound rather than a
target. Nothing that burns GPU is on the list: the assistant cannot start a render.

### A proposal is not an edit

A proposed prompt is validated before it is even shown, by the same
`review_h3_revision` the **Correct this shot** panel uses: the spoken lines must survive byte for
byte, the body must carry exactly one shot, the prompt contract must hold, and the project's
shared text is frozen so no single shot may rewrite it. A refusal is reported in the answer with
the validator's own words, so the model can try again inside the same turn.

It is then shown as a card with **Apply**, and applied through the route the clip cards already
save with (`PUT /api/v1/director/pipelines/{pid}/clips/{index}/prompt`). An edit made from the
chat and an edit made on a clip card are the same edit, refused for the same reasons, against the
same lock -- and if a render is holding that lock, the answer is `409`, not a silent no-op.

## Over HTTP

```bash
# What the assistant will use, and whether it leaves this machine
curl -s http://127.0.0.1:<port>/api/v1/assistant/settings | jq .

# What an address serves -- the one typed, or the saved one with no parameters
curl -s 'http://127.0.0.1:<port>/api/v1/assistant/models?url=http://192.168.68.130:8080&provider=remote' | jq .

# One turn
curl -s -X POST http://127.0.0.1:<port>/api/v1/assistant/message \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Why does shot 3 look like shot 2?"}],
       "workspace":"prueba-2","pipeline_id":"0810b082"}' | jq .

# Apply an accepted proposal
curl -s -X POST http://127.0.0.1:<port>/api/v1/assistant/apply \
  -H 'Content-Type: application/json' \
  -d '{"kind":"prompt","shot":3,"pipeline_id":"0810b082","prompt":"<the new text>"}' | jq .
```

```python
import requests

base = f"http://127.0.0.1:{port}"
reply = requests.post(f"{base}/api/v1/assistant/message", json={
    "messages": [{"role": "user", "content": "Summarise what shot 4 is doing."}],
    "workspace": "prueba-2",
}, timeout=300).json()
print(reply["reply"])
for read in reply["reads"]:
    print("read", read["tool"])
for proposal in reply["proposals"]:
    print("proposed", proposal["kind"], "for shot", proposal["shot"],
          "acceptable" if proposal["acceptable"] else proposal["problems"])
```

```javascript
const reply = await fetch('/api/v1/assistant/message', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    messages: [{ role: 'user', content: 'Which shots still share one camera setup?' }],
    area: 'director',
  }),
}).then(r => r.json())
console.log(reply.reply)        // the answer
console.log(reply.reads)        // what it had to look up
console.log(reply.proposals)    // edits waiting for a decision
```

## Where it lives

| Piece | File |
|---|---|
| The endpoint, the model probe and the action contract | `app/services/chat_llm.py` |
| The five reads and the two proposals | `app/services/chat_tools.py` |
| The routes, the bounded loop and the apply path | `app/services/chat_api.py` |
| The client | `ui/src/api/assistant.ts` |
| The floating panel | `ui/src/components/Assistant/AssistantWidget.tsx` |
| Its settings block | `ui/src/components/SettingsDrawer/AssistantLlmSettings.tsx` |
| The cases | `tests/test_assistant_chat.py` |

`app/launch.py` pays for this with one route registration line. The settings live in the same
`services` dict every other preference is in, so they survive a restart and travel with a project,
but they are read and written by the assistant's own routes -- which keeps the shared store's
contract, and the files upstream owns, untouched.
