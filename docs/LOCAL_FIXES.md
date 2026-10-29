# Local fixes bundle

A fixes bundle is a single `.bundle` file that carries the local commits on top of
upstream Maestro. It exists so one person's fixes can reach another person's **existing**
install without a fork, without cloning, and without touching their environments, models or
projects.

## Applying a bundle

**Run Update once first.** The bundle records the upstream commit it was built from, so the
install has to be on that commit or newer: `Advanced > Update`, in the app menu.

### The first bundle is applied from a terminal

The menu entry that applies bundles is itself inside the bundle, so before the first merge
the install has no `Apply local fixes` to click. Open a terminal in the install folder
(`PINOKIO_HOME/api/Maestro.git`, usually `C:\pinokio\api\Maestro.git` on Windows) and run
these five lines one after another. They work in both PowerShell and `cmd`:

```
git fetch "<full path to the .bundle file>" main:maestro-local-fixes
git -c user.name=Maestro -c user.email=maestro@localhost merge --no-edit maestro-local-fixes
cd ui
npm install
npm run build
```

If that machine has no `git`, Pinokio ships one inside its own folder:
`<PINOKIO_HOME>\bin\miniforge\Library\cmd\git.exe`, for example
`C:\pinokio\bin\miniforge\Library\cmd\git.exe`. The same is true of `node` and `npm` — Pinokio
keeps its own copies, which is why the launcher scripts can build the interface on a machine
where Node was never installed. Alternatively, run the first two lines only and let
`Advanced > Apply local fixes` do the build on the second pass: by then the entry exists and
the merge is a no-op, so the script goes straight to rebuilding with Pinokio's own npm.

The `-c` flags matter only if the merge needs to create a commit: committing requires an
author, and this way the fix neither needs nor edits the git identity of the machine.
The order matters too — fetching is safe and reversible, merging is not, and the rebuild has
to come last because the interface is a compiled React bundle in `ui/dist`.

### Later bundles are one click

Once the first merges, the app menu gains `Advanced > Apply local fixes`: pick the `.bundle`
file and the script fetches it, merges it, and rebuilds the interface in one go. That script
exists to force the build, because Maestro's own Update rebuilds the interface only when
`git pull` reports new commits. A bundle arrives as a file, so the pull afterwards says
"already up to date" and Update would skip the rebuild, leaving the old interface on screen.

The file may live anywhere: Downloads, Desktop, an external drive. It is read from where it
is and nothing is copied into the repository. Two details if the dialog seems empty: extract
the bundle first if it arrived inside a `.zip`, and set the dialog's file-type filter to
"All files" if the downloader renamed the extension.

### Last step, either way

Stop Maestro if it is running, start it again, and hard-refresh the browser (`Ctrl+Shift+R`).
Without the refresh the browser keeps serving the previous interface from its cache.

## Producing a new bundle

From the repository that holds the fixes, where `origin` is upstream:

```
git bundle create dist/maestro-local-fixes.bundle main --not origin/main
git bundle verify dist/maestro-local-fixes.bundle
```

`--not origin/main` keeps upstream's objects out, so only the new commits travel (a few
hundred KB). Bundles stack: applying a second bundle to an install that already has the
first is a fast-forward, and each one keeps its history, so upstream Updates continue to
merge normally.

## Shipping fixes through a fork

The bundle above needs a file transfer and a terminal on the receiving machine. A fork
removes both: the receiving install pulls the fixes with the Update button it already has,
and Pinokio supplies git and npm from its own folder, so that machine needs neither
installed.

Setup, once, from the repository that holds the fixes:

```
git remote add fork https://github.com/<owner>/Maestro.git
git push fork main
```

Every delivery after that is the same push: merge upstream into `main` here, then
`git push fork main`. On the receiving machine, Update does the rest — it sees new commits,
so it takes its build path and rebuilds the interface, which is exactly what a bundle has to
force by hand.

### Pointing an existing install at the fork without a terminal

Replace that install's `install.js` with the drop-in in `dist/drop-in/`. It does three
things and then gets out of the way:

1. `git remote set-url origin <fork>`, so every future Update pulls the fixes.
2. Restores the real installer with `git checkout -- install.js`, leaving the working tree
   clean and the Install button normal again.
3. Asks the user to press Update.

Why `install.js` and not `pinokio.js`: the Install button exists in every Maestro version,
so the drop-in cannot break a menu whose shape depends on the version installed, and the
pull and the interface build are left to that machine's own `update.js`. It also sets a
repo-local git identity when none exists, because a merge commit needs an author and an
install that has only ever fast-forwarded may never have needed one.

The drop-in is generated per fork: the fork URL is substituted into it, so it is not
committed. `dist/` is ignored by git.

## What it does not touch

- No environment is created or reinstalled, and no model or component is downloaded. The
  Python environment, `app/ckpts`, `app/loras`, `app/settings` and `ui/node_modules` are
  left exactly as they are.
- No project or render output is modified. Projects live in `app/outputs/`, which is not
  part of the repository.
- The bundle path pushes nothing anywhere: it is fetched into a local branch named
  `maestro-local-fixes` and merged into `main`, leaving the `origin` remote alone. The fork
  path is the opposite by design — pushing to the fork is how the fixes travel.

## Consequences

- After applying a bundle, `main` holds commits that upstream does not have. `Update` keeps
  working, but it merges upstream into `main` instead of fast-forwarding, so each future
  Update may create a merge commit.
- If upstream edits the same lines, that Update stops on a conflict. `git merge --abort`
  returns the install to the previous state; nothing is lost.
- To go back to a plain upstream install, `git reset --hard origin/main` and run Update.

---

# El arco del proyecto entraba en cada clip (28-10-2026)

Reporte: con **Underdog Saluki 27B** la planificación empezó bien, pero **todos los
clips empiezan igual**: los dos presentadores entrando al estudio y las luces
encendiéndose; a partir del segundo no pasa nada distinto y el lip-sync se pierde.
Detalle del usuario: *"todos los clips comienzan con los dos personajes entrando al
estudio y las luces encendiendo"*.

## Medido en `_director_pipeline_a53d2f8b` (14 clips, 9 renderizados)

| Comprobación | Resultado |
|---|---|
| Prompts de los 14 clips | 14 hashes distintos: **el plan no era el problema** |
| `_director_project_context` | 2159 caracteres, **idénticos en los 14 clips** |
| Acción propia del clip | ~350 caracteres |
| Audio de los 9 clips vs pista subida | correlación 0.96–0.9996 en su offset: **la pista muxeada es correcta** |
| Frame a 1.0 s del clip 2 | los dos de pie, llegando — su acción dice "seated at the curved desk" |

El contexto era el brief del proyecto, que **narra la película entera**:

> The set begins dark and dormant ... A low hum rises and **the key lights bloom up
> in stages** ... **The two participants enter in sequence and settle into their
> chairs** ... At the peak ... the hologram **begins to contract** ...

El bloque ya decía *"do not perform its progression or its closing fade inside this
shot"*. **El modelo lo ejecutó igual.** Es el gemelo visual del defecto ya medido con
la prosa hablada: una instrucción que lo prohíbe no lo evita.

Consecuencia doble: cada clip representa la entrada (no su acción) y, como el audio
empieza en 0 s con diálogo, **las bocas no coinciden con la pista** — el "lip-sync
omitido" era el mismo defecto.

## Corregido

La causa raíz era más simple y más grave que el arco narrativo: **el parser de
secciones del brief no reconocía `DESCRIPTION:` escrito en la misma línea que su
texto**. El brief del usuario empezaba literalmente:

```
DESCRIPTION: A two-host tech podcast set awakens, conducts its conversation, ...
```

y `_HEADING_RE` exigía el dos puntos al final de la línea. Al no haber encabezado
detectado, esos 2159 caracteres **no eran "la historia" para el código**: eran
"contrato del clip" y viajaban a los 14 prompts. El diseño correcto ya existía
(`DESCRIPTION` no viaja nunca a un clip); el parser no lo aplicaba.

`app/services/director/project_brief.py`:

- `_HEADING_RE` acepta el encabezado con su texto en la misma línea, y ya no
  confunde una fila de datos (`<Subject 1> (S1): ...`) con un encabezado.
- `section_heading()` es público: otro módulo necesita saber si un bloque es
  contrato etiquetado o prosa libre del autor.

Ahora hay **dos capas con responsabilidades distintas**, y ninguna pisa a la otra:

| Capa | Qué quita | Dónde |
|---|---|---|
| `clip_project_context()` | una historia **con encabezado** (`DESCRIPTION:`, `STORY:`) y decisiones por plano (`CAMERA:`) | brief → clip |
| `_world_only_project_context()` | la progresión de la **prosa sin encabezado**, frase por frase | brief → clip |

`_world_only_project_context` ya **no trunca secciones etiquetadas**: el tope de
400 caracteres borraba `VOICES` y `AMBIENCE` — justo lo que debe viajar a cada
clip. Sólo recorta prosa libre, y sólo cuando **más de una frase** de un bloque
lleva un verbo de progresión (una sola es una descripción que usa ese verbo).

## Verificación

Recompilado **el mismo plan real** que falló, con los dos arreglos:

| Clip | Antes | Después |
|---|---|---|
| 2 | 6963 caracteres, con "enter in sequence", "key lights bloom", "settle into their chairs" | 2762 caracteres, **ninguna** de esas frases |
| 1 | — | 2877 caracteres; conserva "key lights bloom up" porque **es su acción planificada** (el clip de apertura) |

El contexto del brief desaparece por completo en **los 14 clips** (2159 → 0
caracteres), la acción de cada plano y sus etiquetas de diálogo se conservan. Un
brief etiquetado mantiene su `VOICES` y su `AMBIENCE` enteros: comprobado sobre
`BRIEF` de `test_director_clip_project_brief`, donde "glossy, mirror-like floor"
sobrevive y el arco no.

Pruebas: `tests/test_h3_project_arc.py` (6) y las suites de H3 de prompt intactas
(`test_h3_director_dialogue` 102, `test_h3_final_subject_bindings` 16,
`test_audio_driven_transcript` 10). Los fallos de `test_minimax_h3*` son previos:
`Failed to import diffusers.loaders.peft`, verificados con stash.

## Lo que esto dice sobre el modelo

El 27B no arregló el síntoma porque **el defecto no estaba en la planificación**:
los 14 prompts eran distintos y correctos. Estaba en la construcción del prompt que
recibe H3. Cambiar de modelo no podía tocar eso.

---

# La actuacion planificada se borraba al comprimir el prompt (28-10-2026)

Reporte: drama pequeno con multiples referencias y prompt variado; **no sale de la
misma escena estatica de un personaje, la viejita en la calle del pueblo**. Detalle del
usuario: *"necesito mas riqueza en las escenas ya que es una historia no un podcast"*.

Run medido: `_director_pipeline_20a3897e` (Valdecuna, 26 clips, audio-driven,
Underdog Saluki 27B).

## Lo que el planner escribio

Por cada clip, una actuacion cronologica real:

> She touches the knife with her knotted veined hands, then touches the rosary at her
> chest ... Don Aurelio ... enters from behind her with his head tilted to one side ...
> Rosa turns to look at him.

## Lo que llego al renderer

| | |
|---|---|
| Prompt del planner | 903 caracteres |
| Prompt almacenado en el clip | **235 caracteres** |
| Contenido | camara + lugar + "No new dialogue is generated." |

`_compress_a2v()` (`app/services/director/validators/compressor.py`) se aplica a todo
plano con `lip_sync_critical` y mas de 6 frases: conservaba las **dos primeras**, las
que llevan comillas o una de las palabras `says/speaks/whispers/gestures/nods`, y la
**ultima**. Todo lo demas se borraba. Resultado: siete planos distintos del mismo set
quedaron reducidos a las mismas tres frases, o sea **el mismo plano siete veces**, y el
resto de clips a un retrato estatico del personaje en el decorado.

Reproducido en aislado: el prompt real de 903 caracteres devuelve exactamente los 235
caracteres guardados, byte a byte.

## Corregido

`compressor.py`: se elimina solo una frase que **repita lo ya conservado** (menos del
34% de palabras de contenido nuevas), y nunca una frase con actuacion: dialogo, camara o
un cuerpo haciendo algo (lista amplia de verbos de movimiento, no cinco palabras). Una
sola frase de apertura se conserva siempre por ser el decorado. Un prompt de 6 frases o
menos no se toca, como antes.

`app/services/director_pipeline.py` (`_h3_references_for_shot`): **un plano ocurre en un
solo lugar**. Las 5 imagenes de localizacion viajaban a los 26 clips a la vez (iglesia,
corredor, calle, cocina, borde del bosque), asi que el render promediaba los cinco y se
quedaba en la calle del pueblo. Ahora una localizacion se elige **por nombre**; con una
sola imagen se mantiene; con varias sin nombre no se ata ninguna y el decorado lo dice la
prosa del propio clip, con un aviso en consola que pide nombrarlas.

`short_film.py` (`_audio_shot_row_issue`): se rechaza un clip que repite **las mismas
acciones y el mismo prompt** de un clip anterior. Medido en este run: 11 de 26 clips eran
copias (6 de cocina, 5 de iglesia). El rechazo dispara la reparacion focalizada que ya
existe, con el motivo escrito ("write what happens in these lines instead of restaging
the earlier shot").

## Verificacion

Sobre las 26 filas reales del planner de ese run:

| | Antes | Despues |
|---|---|---|
| Clips vacios de accion | 26 de 26 | 0 |
| Prompt del clip de cocina | 235 caracteres, sin accion | 903, con las 6 acciones |
| Salidas distintas | 3 | 17 (el planner mismo repitio 11 filas) |

Pruebas: `tests/test_director_audio_action_kept.py` (10) y
`tests/test_director_batch_integrity.py` (21). Suites de referencia y de H3 intactas:
`test_h3_reference_scope` 9, `test_director_reference_intents` 11,
`test_h3_reference_view_groups` 16, `test_h3_reference_validation` 8,
`test_h3_director_dialogue` 102, `test_h3_prompt_regressions` 15,
`test_h3_final_subject_bindings` 16, `test_director_prompt_integrity` 10,
`test_director_source_contract` 28, `test_civitai_h3_pipeline` 27.

## Nota para el usuario

Los prompts de un run ya planificado quedaron comprimidos y guardados; un rerun de esos
clips reutiliza el texto viejo. Hace falta **planificar de nuevo**. Si el brief tiene una
seccion `CAMERA:` que pide "static locked-off shots", el planner la obedece en todos los
clips: la variedad de camara hay que escribirla como rango o quitarla del brief, porque el
plan por plano la decide.

Los fallos de `test_director_reference_contract` (2) y `test_long_form_director_planning`
(3) son previos: se reproducen igual con `git stash` de estos archivos.

---

# Una regla de un personaje se leia como la politica del film (28-10-2026)

Reporte: `Invalid MiniMax H3 audio policy: generated speech instructions contradict the
authoritative no-generated-speech policy`. El run no llegaba ni a planificar:
`_director_pipeline_418ae205`, `status: failed`, `phase: planning`, 0 clips.

## Causa

El brief habia crecido de 5.003 a 8.016 caracteres, y las filas de reparto nuevas decian:

```
S3 (Don Aurelio): ... No lip-sync, no voice: he appears only inside the
opened shroud in Stage 5.
S4 (Los cinco ninos del rio ...): ... No lip-sync, no voice, no articulated
speech - only faint distant breathing and soft footsteps.
```

`director_audio_policy()` lo leyo como una prohibicion de TODO el proyecto
(`generated_speech_allowed: False`) en un film cuyo narrador tiene 88 beats de
transcripcion. Cada clip se compilo entonces como "prohibido generar voz" mientras el
plan llevaba lineas habladas y el prompt describia una boca sincronizada.

Dos defectos concretos:

1. `dialogue_forbidden()` no distinguia el alcance. `S3 (Don Aurelio): ... no voice` es
   una regla de ESE personaje, y es exactamente la forma de fila que la plantilla del
   brief pide escribir.
2. `director_audio_policy()` colapsaba los saltos de linea **antes** de la comprobacion
   de voz, borrando justo los limites de fila que hacen legible el alcance.

## Corregido

`app/services/dialogue_writing.py`:

- `_cast_row_lines()` retira las filas de reparto (incluidas las que se parten en dos
  lineas) antes de buscar una prohibicion global. Un encabezado en mayusculas
  (`NEGATIVE:`) o una linea en blanco cierran la fila, asi que una prohibicion global
  escrita debajo **sigue siendo global**.
- `no new dialogue` ya no cuenta como prohibicion: prohibe **inventar** lineas, no
  hablar.

`app/services/director/source_contract.py`: la comprobacion de voz usa el texto con sus
saltos de linea; la de musica mantiene el texto colapsado.

`app/services/director/h3_dialogue.py`: `compile_h3_clip_plans(..., supplied_track=True)`
y `compile_h3_official_prompt(..., driving_audio=...)`. Un clip cuyo plan de audio llego
neutro se compilaba como "sin voz y sin pista", y describir la boca que tiene que
sincronizar era una peticion de voz generada. Con pista propia, la boca sincronizada es
legal; sin pista, la prohibicion global sigue vigente.

`app/services/director_pipeline.py`: el preflight recibe
`supplied_track=bool(params["audio_path"])`.

Mensaje de error: ahora nombra la frase culpable y dice que cambiar
(`_sentence_around()`).

## Verificacion

| Caso | Pista propia | Sin pista |
|---|---|---|
| prohibicion global + prompt que dice "speaks" | compila | **rechazado** |
| brief de 8.016 caracteres (filas de reparto) | compila | compila |

`director_audio_policy(brief nuevo)`: `False` -> `True`.
Prohibiciones globales reales ("silent film", "nobody speaks", y una escrita tras las
filas de reparto) siguen detectandose.

Pruebas nuevas: `tests/test_director_brief_policy_scope.py` (10). Suites intactas:
`test_h3_director_dialogue` 102, `test_director_source_contract` 28,
`test_director_audio_permissions` 11, `test_prompt_contradictions` 52,
`test_h3_sparse_speech` 21, `test_audio_driven_transcript` 10,
`test_h3_performance_policy` 17, `test_h3_prompt_regressions` 15.

# Reabrir un run fallido sin re-teclear el proyecto (28-10-2026)

Peticion del usuario: *"cuando algo falla no puedo regresar a corregir sino que tengo
que volver a meter todo: personajes, referencias, volver a correr ... deberia tener algun
punto de meter todo el bloque de imagenes y solo limpiar el run de planeacion"*.

Eso ya existe y se llama **Open & Edit** (Dashboard -> proyecto -> Open & Edit):
`loadDirectorFromPipeline()` reconstruye desde `_params_snapshot` el brief, el audio y su
analisis, las referencias (incluido el manifiesto Omni ordenado con sus roles), los
modelos, LoRAs y los controles, y deja el proyecto en el paso de estilo para planificar
de nuevo. Comprobado sobre el run fallido: 9 referencias, audio, 8.016 caracteres de
brief, 13 clips de linea de tiempo y 0 planes.

Dos cambios para que ese camino sirva despues de un fallo:

- El error del Director ahora trae el boton **Fix and plan again**, que reabre ese mismo
  proyecto sin salir del chat, con la nota de que conserva brief, referencias y audio.
  (`ui/src/components/Sidebar/DirectorChat.tsx`)
- Un run que murio **antes de planificar** ya no devuelve su plan viejo: se restaura con
  el plan vacio. Antes, si el compositor tenia planes de un intento anterior, reabrirlo
  llevaba a la revision de esos prompts y el siguiente render repetia justo lo que acababa
  de fallar. (`ui/src/stores/useStore.ts`, `restoredClipPlans`)

Verificado: `npx tsc --noEmit` limpio, `npm run build` correcto.

## Corregido: la pista propia no debe decidir que lineas se escriben (28-10-2026)

Reporte del usuario: `Shot 7: expected 4 dialogue block(s), found 0; dialogue words or
speaker order differ from dialogue_beats`. Run `_director_pipeline_1507e7df`.

**Regresion introducida por el arreglo anterior**, no un problema del brief.

`_director_pipeline_1507e7df` fila 7 (Shot 7): `audio_driven` con
`lip_sync_critical: False` y 4 lineas. `reconcile_audio_plan_with_dialogue()` lo convierte
en `dialogue_driven` + `lip_sync_critical: True` (un personaje sin boca igual habla: su
"voz" va por el balanceo y las campanas).

`states_the_lines()` responde, medido sobre esa forma:

| modo | driving=False | driving=True |
|---|---|---|
| `dialogue_driven` + lip True | **True** | **False** |

El arreglo anterior ensanchaba `has_driving_audio` **antes** de esa decision, asi que el
compilador no escribia las 4 lineas mientras `compile_h3_clip_plans` seguia exigiendo 4.

Separado en dos banderas: `policy_driving_audio` (alcance de la politica de audio, para
`_audio_policy_violations`) y `has_driving_audio` (que lineas escribe el compilador). Solo
la primera recibe `supplied_track`.

Verificacion sobre el plan real completo (26 filas, con la reconciliacion del pipeline
aplicada): **0 fallos de 26** con `supplied_track=True` y con `supplied_track=False`.
Pruebas nuevas en `tests/test_director_brief_policy_scope.py` (13 total), incluidas
"a_supplied_track_keeps_the_beats_the_caller_requires" y una que fija que las dos banderas
no vuelvan a unirse.

---

# Guardar el setup de un proyecto y volver a empezar desde el (29-10-2026)

Peticion del usuario: *"cuando algo falla no puedo regresar a corregir sino que tengo que
volver a meter todo: personajes, referencias, volver a correr ... deberia tener algun
punto de meter todo el bloque de imagenes y solo limpiar el run de planeacion"*, y
*"hay una funcion de save recipe supongo que guarda todos los parametros, pero no se puede
servir a modo director, ahi no hay un link que recupere los parametros"*.

## Por que una receta de Studio no sirve aqui

`recipes.RECIPE_PARAM_KEYS` es una lista blanca de *aspecto*: modelo, tuning, punteros de
LoRA. Excluye a proposito las entradas por-generacion (referencias, fotogramas, guias de
audio). Para Studio es lo correcto; para Director es inutil, porque **las imagenes del
reparto, la banda sonora y su transcripcion SON el setup**: rehacerlos a mano cuesta una
sesion de subida antes de la primera llamada al LLM.

## Nuevo: presets de Director

`app/services/director_presets.py` congela el snapshot del proyecto en una carpeta propia:

| Se guarda | No se guarda |
|---|---|
| el brief | el plan de clips y la linea de tiempo |
| el manifiesto ordenado de referencias, **con los archivos copiados** | los prompts generados |
| el audio y su analisis (incluida la transcripcion etiquetada) | el checkpoint de planificacion |
| modelos, LoRAs, resolucion, duracion y todos los controles | el id del run y su entrada de cola |
| nombres de hablantes y sus voces | los anclajes de assets del run |

Guardar el analisis es deliberado: cargar un setup **no vuelve a transcribir**, que es el
paso mas lento de un proyecto de dos minutos. Y no se guarda nada que pueda volver a
renderizar lo que acaba de fallar: un setup es un punto de partida, nunca un plan.

Rutas: `GET/POST /api/v1/director/presets`, `GET/DELETE /api/v1/director/presets/{id}`.

UI: `ui/src/components/Sidebar/DirectorPresetBar.tsx`, barra **Setups** arriba del chat de
Director, con select + **Load** + **Save setup**. Cargar reutiliza
`_buildDirectorRestorePatch` (el mismo camino de Open & Edit) y aterriza en el paso del
brief, con el audio y los hablantes ya resueltos.

Archivos en el workspace activo:

```
outputs/<workspace>/_director_presets/<id>.json
outputs/<workspace>/_director_presets/<id>/…png|wav
```

Las copias son el punto: un setup sobrevive al borrado del run del que salio.

## Defecto colateral corregido

`_director_asset_filename()` volvia a prefijar un archivo ya prefijado, y producia
`audio_path_audio_path_audio_path_8802b1b4.wav` (y crecia en cada pasada: resume, rerun,
preset). Ahora, si el nombre ya empieza por su clave, se deja intacto.

## Verificacion

Sobre el run real `_director_pipeline_8c94a5a5` (26 clips, 9 referencias, 8.016 caracteres
de brief):

| | |
|---|---|
| brief conservado | 8.016 caracteres |
| referencias conservadas | 9, todas sus copias en disco |
| audio + vocals | copiados (23 MB + 46 MB) |
| transcripcion | 101.462 caracteres de analisis, conservada |
| plan / linea de tiempo / llm log | **retirados** |
| `serve_path` de cada asset | relativa al workspace, carga por el endpoint de archivos |

Pruebas: `tests/test_director_presets.py` (15). `npx tsc --noEmit` limpio y
`npm run build` correcto.

## Donde esta la barra de Setups, y guardar sin haber corrido nada (29-10-2026)

Reporte del usuario: *"no encuentro ese menu setups"*.

Causa: la barra se ocultaba cuando no habia ni un run ni un preset guardado
(`if (!presets.length && !pipelineId) return null`), que es justo el momento en que se
busca. Y el boton de guardar exigia un `pipeline_id`, asi que solo existia **despues** de
planificar, congelando el brief con el que se habia enviado ese run, no el que el usuario
acababa de editar.

Corregido:

- La barra **siempre visible** en el chat de Director (`data-testid="director-preset-bar"`),
  con un texto claro cuando no hay nada guardado.
- `Save setup` guarda **lo que el compositor tiene ahora**, sin run previo:
  `_directorSetupParams(state)` en `useStore.ts` es el inverso exacto de
  `_buildDirectorRestorePatch` (que es quien lee lo guardado), asi que no hay dos
  definiciones del mismo contrato que puedan separarse.
- `POST /api/v1/director/presets` acepta `params` (setup vivo) o `pipeline_id` (run
  guardado). El `pipeline_id` pasa a ser solo procedencia.
- Se rechaza un setup vacio ("There is nothing to save yet...") y se **nombran** los
  archivos que ya no estan en disco, en vez de restaurar un setup con imagenes rotas.

Verificado: guardar desde el compositor sin run copia 2 referencias y el audio, conserva
la transcripcion, y el setup resultante tiene `source_pipeline_id` vacio. Los mensajes de
error se comprobaron para setup vacio y archivo ausente.

Pruebas: `tests/test_director_presets.py` pasa a 18. `npx tsc --noEmit` limpio y
`npm run build` correcto.

## El lapiz de un clip ahora pregunta: Studio o Director (29-10-2026)

Reporte del usuario: *"ayer hice un run que deje incompleto, lo veo en el panel de videos,
como lo guardo a director, no hay una funcion para ello, solo lo carga en modo studio"*, y
despues: *"la funcion ya existe es un tooltip que dice load settings ... no hace falta tanto
gizmo, solo que ese tooltip me pregunte donde lo carga: en studio o en director"*.

## Lo que impide que baste el sidecar

El `.meta.json` de un clip de Director lleva `director_pipeline_id`, `director_artifact_kind`
y `director_clip_index`, pero **no** el proyecto: no hay `scene_description`, ni
`minimax_h3_references`, ni `director_ui_snapshot`. Eso vive en el run
(`_director_pipeline_<pid>.json`). Por eso "Load settings" solo podia reconstruir Studio:
los parametros del clip no describen un proyecto.

## Corregido

`ui/src/components/MainContent/MediaFeedItem.tsx` — el lapiz es el mismo de siempre:

- Clip de Director -> despliega **Load settings in: Studio | Director**.
- Clip normal -> carga en Studio como antes, sin preguntar.

"Director" usa `reattachDirectorPipeline(pid, true)`, el mismo camino que Open & Edit del
Dashboard: brief, referencias, audio y transcripcion, con el plan y los clips intactos.
Primero se dan de baja los dos gizmos que habia añadido en `VideoInfoBar.tsx`, y ese archivo
se revierte: **ese componente no lo importa nadie** (codigo muerto), por eso mis cambios
anteriores no aparecian en el bundle.

`reattachDirectorPipeline` tampoco dejaba rastro si el run habia sido borrado: la llamada
a `fetchPipelineStatus` estaba fuera del try. Ahora informa en `directorError` y, si venia
de un clip, abre Director para que el mensaje se lea.

## Verificacion

Sobre el workspace Valdecuna, con un clip real del run cancelado `20a3897e`:

| | |
|---|---|
| sidecar del clip | `director_artifact_kind: clip`, `director_clip_index: 0` |
| id recuperado | `director_pipeline_id: 20a3897e` |
| run en disco | si, `status: cancelled`, 26 clips |
| setup resultante | brief 5.003 caracteres, 10 referencias, audio, transcripcion, plan retirado |

El menú aparece en el bundle compilado (`index-D77_NsJs.js`: "Load settings in").

`npx tsc --noEmit` limpio, ESLint limpio, `npm run build` correcto.
