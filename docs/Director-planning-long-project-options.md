# Director: por qué se aplana el tercio medio de un proyecto largo (y qué opciones hay)

Nota de trabajo. Diagnóstico medido sobre los logs reales del proyecto y sobre el código
del planner. No describe comportamiento ya implementado: son las opciones pendientes de
elegir.

## El síntoma

Con el modelo chico y rápido (Gemma 4B, el default: `Abhiray/gemma-4-E4B-it-heretic-GGUF`),
un video de dos minutos se divide en 16 clips. Los primeros salen con imaginación y a
mitad del proyecto el modelo deja de proponer y repite lo mismo ("los pone a caminar en la
calle"). Es consistente entre proyectos.

## Lo que NO es

- **No es saturación de contexto.** El planner no acumula: al pasar de 12 clips divide la
  línea de tiempo en lotes acotados (16 clips = lote 1 de clips 1-12 y lote 2 de 13-16) y
  el contexto de cada lote es del mismo tamaño que el del primero.
- **No es truncación por tokens.** Medido sobre 14 planificaciones reales de 150 shots: la
  longitud de cada prompt se mantiene entre ~450 y ~900 caracteres de principio a fin.
- **No es la vía de relleno determinista.** Cero señales de `fallback` / `deterministically
  filled` en esos logs.

## Lo que sí es

1. **El razonamiento se apaga justo en proyectos largos.**
   - `app/services/director/planners/music_video.py:963`:
     `thinking_budget=(0 if kwargs.get("_bounded_music_batch") else 4096)`
   - `app/services/director/planners/short_film.py:7231`: `thinking_budget=0 if _bounded_batch else None`
   - Y `app/services/director/planners/base.py:330-360` documenta por qué eso le pega a este
     modelo: «Gemma 3/4 … budget=4096, thinking ON. Gemma's reasoning is well-behaved and
     helps it follow the structured-output rules … **Without thinking, smaller Gemma models
     like 4B miss these rules under cognitive load**.»

2. **Cada lote arranca pidiendo continuidad** (`music_video.py:714-728`): «Continue the same
   music video; do not restart its visual premise… **Previous planned ending: X**». Sin
   razonamiento, un 4B cumple quedándose donde estaba: mismo entorno, mismo encuadre,
   misma acción.

3. **Dentro de un lote de 12 elementos, los últimos se autoconfirman** con los que el propio
   modelo acaba de escribir: la respuesta se aplana hacia su final. En 16 clips, ese
   "final del lote" cae en la mitad del video, que es justo lo observado.

### Huella en los datos

- Rachas de shots consecutivos casi idénticos: pares de similitud **0.99** y tramos de diez
  seguidos por encima de **0.82**.
- Vocabulario concentrado en el escenario y no en la acción: `studio` 40-56 veces, `camera`
  28-35, `medium` 20-35, `modern/futuristic` 25-28, `continues` 18-20.

## Estado

Implementado el 2026-09-25 (**1 + 2 + 3 + 5**, elegidas por el usuario):

- **1** — `planners/base.py`: un lote acotado ya no fuerza `thinking_budget=0`. El helper
  da 2048 a Gemma en lote (frente a 4096 en plan corto) y deja a Qwen apagado, que es su
  caso documentado de fuga. El reintento sin razonamiento y con gramática sigue intacto.
- **2** — `BasePlanner.long_form_batch_size()`: 8 para modelos de ≤4B (el token de tamaño
  se lee completo, porque un `"4b" in name` clasificaba un 14B como chico) y 12 en
  cualquier otro caso, incluido «sin registro». music_video y short_film lo usan.
- **3** — cada lote recibe la línea de tiempo completa como contexto
  («THE WHOLE TIMELINE … plan ONLY clips X-Y»), en vez de tener solo el final anterior
  como referencia.
- **5** — `base.py` dice en consola cuántos planes de un lote se rellenaron de forma
  determinista y que esos clips son genéricos.

Pendiente si el tercio medio sigue aplanándose: **4** (beat sheet). Verificación:
`tests/test_director_long_form_planning_quality.py` (11 pruebas) y, sobre un proyecto real,
replanificar y comparar longitud por shot, similitud con el shot anterior y palabras
repetidas.

## Opciones

| # | Qué cambia | Coste | Efecto esperado | Riesgo |
|---|---|---|---|---|
| 1 | Encender el razonamiento en lotes para Gemma (`music_video.py:963`, `short_film.py:7231`): de `0` a 2048, o sin valor para usar el default por modelo (4096) | Planificar tarda más (una vez por proyecto) | El que documenta `base.py`: el 4B sostiene las reglas bajo carga | Muy bajo, es una constante; reversible |
| 2 | Lotes más chicos para modelos chicos: `_LONG_FORM_BATCH_SIZE` de 12 a 8 (tu caso: 8+8 en vez de 12+4) | Más llamadas, mismo trabajo total | Menos autoconfirmación dentro de una respuesta larga | Bajo |
| 3 | Que cada lote vea el arco completo: pasarle toda la lista de `clip_contexts` (secciones, energía, tiempos ya calculados) en vez de solo su tramo | Más tokens de entrada, ninguna llamada extra | El modelo ve la línea entera y deja de tener el final anterior como única referencia | Bajo |
| 4 | Beat sheet: un pase previo fija K intenciones distintas para toda la línea; cada lote planifica **contra sus intenciones** | Un pase extra y código nuevo de planificación | Ataca la raíz de la homogenización; sostiene 16 clips con identidad propia aunque el modelo sea chico | Medio; se puede dejar apagado por defecto |
| 5 | Hacer visible el relleno determinista: si un lote falla y se rellena genérico, decirlo en el dashboard | Bajo | Un clip genérico se ve como relleno y no como falta de imaginación | Bajo |

## Recomendación

Empezar con **1 + 2 + 3**: bajo riesgo, sin arquitectura nueva, y se verifica con los mismos
dos números ya usados aquí (similitud entre shots consecutivos y concentración de vocabulario
en el escenario). Si el tercio medio sigue aplanándose, ir a **4**. La **5** es casi gratis y
se puede incluir de paso.

## Cómo se verifica

Replanificar el mismo proyecto y comparar, por shot: longitud del prompt, similitud con el
shot anterior y palabras más repetidas. El script usado para el diagnóstico está en
`logs/_degrade.py` (temporal); se puede guardar en `scripts/` si se quiere repetir.
