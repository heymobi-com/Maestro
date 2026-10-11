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
- **Vision is not available** with a text-only Strata model: `/health` reports `"images": false`, so
  image inputs will not be understood.
- **A model on another host by DNS name** is not probed. Point the Strata entry at an address, not a
  hostname, if the server lives elsewhere on the LAN.

## Checking it works

The verification profile records a given structured call as JSON, not as prose:

```sh
curl http://127.0.0.1:8081/v1/models
```

then run any structured step (Director planning, prompt enhancement) and look for the reply being
JSON rather than an explanation. In Maestro's log, a structured call to Strata reports
`reasoning=0 tokens`; before this, the same call could report the whole allowance spent on reasoning
with nothing to show.
