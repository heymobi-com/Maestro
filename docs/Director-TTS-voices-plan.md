# Plan — voces por TTS y H3 como lip-sync

Estado: **informe de alcance**. No hay código escrito para esto todavía. Reúne lo
medido, lo que la app ya tiene y lo que falta, para decidir antes de tocar nada.

## El problema, medido

En un proyecto de guion escrito (sin pista subida) es **H3 quien sintetiza la voz**, y
eso trae tres defectos que no se arreglan desde el prompt:

1. **Timbre metálico.** Los clips renderizados son `aac · 32000 Hz · stereo`. 32 kHz es
   la frecuencia nativa de la rama de audio de H3, y con AAC encima el resultado es una
   voz fina, sin armónicos. Ningún ajuste del compilador sube eso.
2. **Palabras imprecisas.** Aunque el prompt lleve las 20 líneas exactas con sus
   etiquetas `(S1)`/`(S2)`, la pronunciación depende del modelo.
3. **Habla mezclada.** La prosa del contexto del proyecto (`scene_description`) está en
   el prompt en el idioma del diálogo y el modelo **la lee en voz alta** y la funde con
   las líneas. Medido dos veces transcribiendo los clips. Una instrucción que se lo
   prohíbe no lo evita.

El punto 3 tiene su propio arreglo pendiente (ver `docs/LOCAL_FIXES.md` y los commits
de la serie). Los puntos 1 y 2 **no**: son del modelo. Esta vía los esquiva en vez de
pelearlos.

## La vía

**Que H3 no hable, sino que sincronice.** Las voces las produce un TTS dedicado
(44,1/48 kHz, voces de personaje) y H3 sólo mueve la boca sobre ese audio. Con eso:

- el timbre es de un motor de voz, no de una rama de vídeo;
- las palabras las garantiza el audio: no hay nada que inventar;
- no hay prosa que el modelo pueda leer de más.

Y va por el modo **audio**, que es el camino viejo y ya funciona.

## Lo que la app YA tiene

| Pieza | Dónde | Nota |
|---|---|---|
| Speech como sub-modo de audio | `audio_sub_mode: 'speech'` (`ui/src/types/index.ts`, `AudioSubModeToggle.tsx`) | se genera con el endpoint de generación normal + un modelo de la familia `tts_speech` |
| Biblioteca de personajes con voz | `TtsCharacterLibrary.tsx`, Studio → Audio → Speech → Characters | referencia de voz + retrato; mínimos de 2 s de habla clara |
| Guion con `NOMBRE: línea` | `ui/src/lib/ttsVoices.ts`, `docs/TTS-Characters.md` | **la misma sintaxis que nuestro parser de guion** |
| Enlazado de personajes al prompt | `lib/ttsVoices.ts` (`Saved character voice bindings … Use these exact speaker names for dialogue labels`) | los nombres del guion se atan a voces guardadas |
| Catálogo de motores | `docs/TTS-Characters.md` | H3 Voice Audio, KugelAudio, DramaBox, Scenema, IndexTTS2, Qwen3 Base, Chatterbox |
| El Director acepta filas + pista | `app/services/director_pipeline.py` (podcast/viral: `params["transcript"]` **antes** que las letras analizadas) | ya escrito para «guion escrito **o** transcript del análisis» |
| Modo audio con lip-sync | el modo con pista subida | **blindado** por `tests/test_audio_mode_unchanged.py` |

## Lo que falta

### 1. Los tiempos reales de cada línea (la pieza clave)

Hoy `ui/src/lib/directorScript.ts` estima a **2,8 palabras/s** (`WORDS_PER_SECOND`) y de
ahí salen los clips y las ventanas. Con TTS **cada línea ya es un archivo con su duración
medida**: el timeline debe construirse con esas duraciones, no con la estimación. Si no,
volvemos al defecto de ventanas desalineadas que ya costó dos rondas (`shot_duration`).

### 2. El puente guion → TTS → proyecto

Un paso que haga, en orden:

1. sintetizar el guion con las voces guardadas (una llamada al módulo de voz existente,
   reutilizando `lib/ttsVoices.ts` para el enlazado por nombre);
2. **medir** la duración de cada línea sintetizada y construir con ella las filas del
   transcript y los clips (`start`/`end` reales, con las pausas entre turnos);
3. concatenar en una pista única (más las pausas), escribiendo el archivo en el
   workspace del proyecto;
4. entregar al Director `audio_path` = esa pista y `transcript` = esas filas, en modo
   audio.

**Por verificar antes de implementar:** si con un `transcript` suministrado el modo audio
puede **saltarse la diarización** (`/api/v1/audio/analyze`) en lugar de correr pyannote
sobre una pista cuyas voces ya conocemos. Es la diferencia entre un paso instantáneo y
una pasada de Whisper + pyannote que además podría repartir mal a los hablantes.

### 3. La UI

Un botón en el paso de guion — **«Generar voces»** — que dispare el puente y deje el
proyecto en modo audio. Después, el flujo es el de siempre: revisar el timeline, planificar,
renderizar.

## Límites por motor (a respetar al elegir)

De `docs/TTS-Characters.md`: H3 Voice Audio admite **2 hablantes**, 45 s por segmento y
5 min totales; KugelAudio hasta **6**; DramaBox y Scenema **2**; Chatterbox **1**. Los
guiones largos se parten solos (`AudioDurationControl.tsx` lo anuncia en la UI). Un guion
como el del proyecto medido —20 líneas, ~93 s— cabe con margen.

## Riesgos y cómo se acotan

- **No romper el modo audio.** Ya está fijado por `tests/test_audio_mode_unchanged.py`:
  ids de máquina intactos, palabras del transcript, tono del modelo, duración de la
  ventana, y el contexto todavía presente con pista propia. Los cambios del puente deben
  pasar esa suite sin tocarla.
- **Duplicar el motor de voz.** El riesgo real de esta vía es escribir un segundo
  camino de TTS. Debe ser **una llamada a lo que ya existe**, no una reimplementación.
- **Sincronía boca-voz.** El lip-sync depende de que la ventana del clip y el audio
  coincidan; de ahí que el punto 1 (tiempos reales) sea el primero y no el último.

## Alcance

Es una **función nueva**, no un arreglo: no sustituye al modo guion actual, lo complementa.
El modo guion con voces de H3 sigue disponible; esta vía se ofrece cuando el timbre o la
precisión importan.
