# Wan2GP 12.71 integration

Maestro v2.1.0 incorporated selected Wan2GP 12.71 algorithms into its existing
model loaders, reference compiler, queue, React controls and Editor. This record
describes that integration; see the [release notes](../RELEASE_NOTES_V2.1.0.md)
for the released feature set.

## Upstream source and licensing

The source revision is [Wan2GP 1e1dd2757f24923f008593d9d4ec09062234be20](https://github.com/deepbeepmeep/Wan2GP/tree/1e1dd2757f24923f008593d9d4ec09062234be20).
Preserve the original WanGP and third-party license notices. Optional native
runtime components are separate downloads under their own terms; model weights
and runtime binaries are not distributed in this source repository.

## Integration layout

| Component | Maestro integration |
| --- | --- |
| H3 VDN | Dedicated hybrid attention, trained-module loading and First/Last presets; ordinary H3 attention remains separate. |
| DLSS finishing | Optional native worker adapter shared by generation finishing and Media Flow, with runtime capability checks. |
| Temporal upsampling | Direct RIFE x3 timesteps and runtime-reported DLSS frame-generation factors. |
| Media Flow | Per-item queued finishing and Outpaint jobs with new outputs, source preservation and cancellation. |
| H3 Voice Audio | Audio-only reference generation with bounded segments and assembled longer output. |
| H3 Outpaint | Latent-aligned grouped masking, proportional margins and restoration of the original central region. |
| H3 audio refinement | An optional six-step soundtrack phase that freezes video latents and omits reference/control reinjection. |

H3 integrations reside beside the existing pipelines in `app/models/minimax_h3/`.
Shared services include `app/services/media_processing.py` and
`app/services/media_flow.py`; the optional native adapter is under
`app/postprocessing/dlss5/`. Existing checkpoint, encoder and VAE files are reused
where supported rather than silently converted or replaced.

## Runtime constraints and user guides

VDN has its own attention path and does not combine with ordinary H3 SOL/SLA or
Turbo controls. Native DLSS availability and frame-generation factors depend on
the installed runtime and hardware. Functional integration does not establish
upstream speed claims or quality equivalence on every GPU and workload.

- [H3 Voice Audio](../H3-Voice-Audio.md)
- [Optional DLSS installation and finishing](../DLSS5.md)
- [Maestro v2.1.0 release notes](../RELEASE_NOTES_V2.1.0.md)

## Media Flow API

Use the existing Maestro server and queue:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/media-flow/capabilities?refresh=true` | Probe installed native finishing support. |
| `POST /api/v1/media-flow` | Queue finishing and receive batch/job identifiers. |
| `POST /api/v1/media-flow/outpaint` | Queue per-video Outpaint jobs. |
| `GET /api/v1/status/{job_id}` | Inspect progress, completion and output filenames. |
| `POST /api/v1/cancel/{job_id}` | Cancel a queued or running job. |

Supply uploaded media or a path accessible to the selected workspace. Keep full
configuration backups, runtime logs and per-machine benchmark evidence private.
