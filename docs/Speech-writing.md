# Speech writing and long scripts

In **Audio → Speech → Advanced → Generation → Speech writer**, save writing
instructions for tone, personality, vocabulary, storytelling and dialogue.
For example: “A warm, observant narrator with dry humor; give each speaker a
distinct point of view.” These notes supplement the selected speech model's
format rules. They go to the writing LLM, and are not prepended to the spoken
script or used as a Fish voice reference.
The preferences are remembered in this browser and retained in output settings.

**Write** uses the selected speech duration as its approximate draft target by
default. For example, selecting 20 minutes requests about 2,600 spoken words.
The writer estimates 130 words per minute; actual audio duration varies with
language, voice, pauses and delivery. For audio generation, that same duration
remains a maximum, so the output can finish earlier.

An explicit length in the brief takes precedence: a ten-minute story, a
twelve-minute dialogue, a word count, or an explicit short/brief request.
**Draft length override (minutes)** in Speech writer takes highest priority
when positive; leave it at 0 to use the brief or selected speech duration.
With **Auto** speech length, the writer follows the brief, and an unqualified
“long story” requests a bounded ten-minute draft. There is no fixed 600-word
or 3,000-character limit on the assembled draft.

Use **Write** to draft a monologue or dialogue. Long requests are planned and
written in sections with a bounded continuity context, then returned as one
editable script. The current limit is an approximate sixty-minute draft and
thirty model calls. Word estimates exclude performance cues and speaker labels.
Sections that reach the model's answer limit get a bounded continuation. A
section that stops far below its word target can also receive a bounded expansion;
a repeated section gets a focused repair instead of counting as new story.
Previously generated text is continuity context, not new verbatim source text.
Empty, short, repeated or still-truncated drafts carry review warnings when the
bounded recovery cannot resolve them; raw drafts remain in enhancement history.
Substantially overlong drafts also show a warning: a fixed synthesis maximum
could stop before the whole script is spoken. Maestro retains the text for review.
Review the complete script and
speaker assignments before generating audio. Writing does not start synthesis.

Token-limited continuations share the same thirty-call bound and can continue
until the response finishes or stops making progress. Completion takes priority
over adding words to an approximate target: a final section that finishes
normally is kept without a length-only expansion. An unfinished repair cannot
replace a usable section; the original stays in the script and the partial
repair remains in draft history with a warning. A normal model stop still does
not establish a satisfying story ending; review the handoffs and resolution.

Fish-specific recovery also checks generated delivery markup before deciding
whether a section needs completion. Clear one-off angle cues become square
brackets, and an inherited open bracket can close at a verified continuation
boundary. These repairs preserve spoken words and exact source tokens. Unknown
or ambiguous markup remains flagged instead of being silently rewritten.
Recent continuity excerpts start at complete paragraph, line or word boundaries
outside native cues, rather than cutting into a word or tag. The ending stays
intact for continuation. A scope too large to excerpt safely is omitted from
that private context; the user's script and saved raw responses are unchanged.
Stray closing brackets also retain a syntax warning. Repeated model-authored
passages still need review when one focused repair cannot resolve them.

Speech cleanup preserves repeated phrases and refrains instead of applying the
generic video-prompt repetition cutoff. A normal rhetorical repetition must not
silently remove the remaining story or later dialogue turns. The complete raw
writer response remains available in enhancement history.

For **Fish Audio Drama 3**, **Auto** speech length generates long scripts in
bounded parts, carrying speaker/delivery instructions and recent audio context,
then assembles one audio file. You do not have to supply one prompt per part.
**Maximum length** is an optional whole-file cap, up to one hour. Generation
shows part progress. If a part or the whole output reaches its limit, the
available audio is retained with an incomplete warning and part coverage in
output details. A model end token does not independently verify that every
word was spoken; listen to the result before treating it as a finished take.

These controls do not raise the native limits of other speech generators.
Writing a long script and synthesizing it are separate capabilities.

## API

Draft with `POST /api/v1/llm/enhance-prompt`:

```json
{
  "mode": "audio",
  "model_type": "fish_drama3_int4",
  "prompt": "Write a dialogue about two lighthouse keepers discovering a letter.",
  "tts_enhance_mode": "dialogue",
  "tts_voice_count": 2,
  "duration_seconds": 1200,
  "tts_writer_instructions": "Warm, witty characters and a satisfying ending.",
  "tts_target_minutes": 12
}
```

For drafting, `duration_seconds` supplies the default target when the brief has
no explicit size and `tts_target_minutes` is 0; a positive `tts_target_minutes`
overrides both. Auto duration (0) follows the brief. The example uses the
explicit twelve-minute writer override rather than its twenty-minute clip.

The response includes `enhanced`, `warnings` and the saved draft history ID.
Submit the reviewed script through `POST /api/v1/generate` with
`generation_mode: "audio"`, the chosen `model_type`, and `duration_seconds: 0`
for Fish Auto, or a positive whole-output maximum. Clone voices through the
ordinary audio reference/transcript fields. The file's model details retain
the synthesis part report and any incomplete warnings.

## Formas de guion y sintaxis del motor (28-10-2026)

El botón de escritura ofrece cinco formas además de la escritura libre. Cada una
cambia la **estructura** del guion, no solo el tema:

| Forma | Voces | Minutos por defecto | Lo que la sostiene |
|---|---|---|---|
| Write Podcast | 2 (diálogo) | 8 | frío abierto, 2-4 segmentos, cierre |
| Write Interview | 2 (diálogo) | 8 | una pregunta que se responde con hechos |
| Write Documentary | 1 (narración) | 10 | gancho en 15 s, capítulos con promesa y pago |
| Write Story | 1 (narración) | 6 | deseo y obstáculo concretos, escenas, escalada |
| Write Audio Drama | 2-3 (diálogo) | 8 | conflicto ya en marcha, revelación final |

Dos reglas se aplican a todas:

1. **La sintaxis de entrega es del motor, no de la forma.** Fish Drama 3 pide
   `[whisper]`, `[laughs]`, `[pauses]` y `<emphasis>…</emphasis>`; KugelAudio y
   Qwen3 los prohíben explícitamente ("NO brackets, NO emotion tags"), así que su
   brief pide emoción solo con léxico y puntuación. El brief se elige por
   `architecture` del modelo activo (`podcastTagStyle`).
2. **La forma decide si una etiqueta va en el guion.** Un diálogo necesita
   `Speaker 1:`/`Speaker 2:` para que el motor ate cada línea a su voz; una
   narración continua no debe llevarla, o el motor lee "Speaker 1:" en voz alta.

La lógica vive en `ui/src/lib/speechModes.ts` (formas) y
`ui/src/lib/podcastWriter.ts` (estilo de etiquetas y duración). Pruebas:
`tests/test_speech_modes.py` y `tests/test_tts_voice_requirements.py`.

Pendiente acordado: ingesta de documentos con recuperación léxica (BM25) para que
un guion largo se escriba citando pasajes reales en vez de improvisar.

## Ingesta de documentos (Sources) — 28-10-2026

`Sources (document grounding)` en Studio → Audio → Speech. Con documentos indexados,
el botón de escritura busca los pasajes que corresponden al brief **antes** de
escribir y se los pasa al escritor como material permisible.

| Pieza | Dónde |
|---|---|
| Extracción, troceado, BM25 y bloque de fundamentación | `app/services/sources_index.py` |
| Endpoints | `POST /api/v1/sources/upload`, `/ingest`, `/search`, `/reset`, `GET /api/v1/sources` |
| Panel | `ui/src/components/Sidebar/SourcesPanel.tsx` |
| Enganche a la escritura | `ui/src/components/Sidebar/PromptInput.tsx` (`writeSpeechFormatAsync`) |

### Decisiones, y por qué

- **Sin dependencias nuevas salvo PDF.** Markdown/TXT con `open`, HTML con
  `html.parser`, DOCX con `zipfile` + `xml.etree` (un DOCX es un ZIP de XML). Así
  `lxml` —compilado— no entra en un manifiesto de 74 pines. `pypdf==5.1.0` es el
  único paquete añadido: Python puro, y su `dry-run` resolvió **un** paquete sin
  tocar ningún pin existente. Se importa de forma perezosa: si falta, solo se
  degrada el formato PDF.
- **Recuperación léxica (BM25)** con plegado de acentos y prefijo corto de raíz.
  Medido sobre un informe real en español: 5 de 5 consultas al pasaje correcto, y
  una consulta fuera de tema devuelve vacío (para que el escritor pueda decir "el
  material no lo cubre" en vez de inventar). Sin el prefijo de raíz, "clonar" no
  encontraba "clonación" y ganaba el pasaje de precios.
- **Fundamentación explícita.** El bloque `REFERENCE MATERIAL` dice que es la
  única fuente de hechos permitida y qué hacer cuando el material no cubre algo.
  Sin él, recuperar pasajes no cambia el guion.
- **Entrada no confiable acotada**: 25 MB por documento, 500 páginas de PDF,
  4000 pasajes por documento, y un fichero ilegible se informa en el panel sin
  perder los demás.

### Pendiente

- **Vídeo/audio como fuente**: transcribir con el servicio existente
  (`services.audio_analysis._transcribe`) y luego indexar el texto.
- Citas por segmento en el MP3 final (qué fuente alimentó cada capítulo).

### Carpetas y citas revisables — 28-10-2026

**Carpeta.** El panel ofrece `Add documents` y `Add a folder`. La carpeta se sube
fichero a fichero por el mismo endpoint, así que un documento roto no tumba el
lote; el informe del panel dice qué se indexó, qué ya estaba y qué se ignoró
(`unsupported type`, `beyond the 200-document limit`). Para una carpeta que ya
está en disco dentro de `uploads/` o del workspace existe
`POST /api/v1/sources/ingest-folder` con `{path, recursive}`.

**Citas por defecto, a nivel de documento.** `citation_rows()` colapsa los pasajes
usados en **un registro por documento**, en orden de uso, con cuántos pasajes y
qué secciones aportó. Es lo que un oyente puede oír ("según el informe sobre
síntesis de voz local"); una referencia a un pasaje no se puede escuchar.

**Revisión antes de renderear.** Después de escribir, y solo si hubo
fundamentación, se abre `SpeechCitationReview`: la lista de documentos citados con
casilla para descartar los que no quieras acreditar, y los pasajes plegados en
`Review the N passages used` por si quieres comprobarlos. Lo confirmado se guarda
en `_speech_citations` y viaja al master, que escribe:

- `comment: "Sources: codec.md; clon.md"` como etiqueta ID3 del MP3 (solo si no
  diste un comentario propio);
- `citations` completas en el `.meta.json`, con pasajes y secciones.

Verificado con un MP3 real: `ffprobe` devuelve `comment: Sources: codec.md;
clon.md`, y el sidecar conserva el detalle. Filas en blanco se descartan.
