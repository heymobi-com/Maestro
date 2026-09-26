# Updating from upstream

Our fork develops between Maestro releases, so an update is a merge, not a pull.
This is the measured procedure: it predicts the conflicts before the tree is
touched, and it fails loudly when our code has drifted back into files upstream
owns.

## Why this exists

A file we add ourselves is immune to an update: upstream never touches a path it
does not have. The friction is entirely in the files **both** sides edit, and that
surface is measurable.

Measured on 2026-09-26, after the v2.4.1 merge:

| | before isolation | after |
|---|---|---|
| lines of ours inside upstream's files | 7511 | 7155 |
| such files | 33 | 32 |

The scope was decided by evidence rather than by size. Over the merge base,
upstream v2.4.1 changed `app/services/director/h3_dialogue.py`,
`app/services/director_pipeline.py`, `ui/src/components/DirectorDashboard/DirectorDashboard.tsx`
and `ui/src/components/Sidebar/DirectorChat.tsx` by **zero hunks**, so the ~5600
lines we keep in those four cost nothing today and were deliberately deferred.
Upstream's real churn is DLSS5, Qwen2.1, the comfy_kitchen kernels, `media_info`
and `ui/src/components/MainContent/MediaFeedItem.tsx`.

## The procedure

```bash
# 1. Start clean. A dirty tree turns a merge into a guessing game.
git status --short

# 2. Predict. Every conflict must be a known seam; anything else means the
#    seam map is out of date.
python scripts/upstream_update_guard.py --predict

git fetch origin

# 3. Merge on a branch, never on main.
git checkout -b v241-merge
git merge origin/main --no-edit

# 4. Resolve each conflict by keeping BOTH sides: our seam, and their change
#    re-applied inside the module that seam points at. Then stage it -- a
#    resolved file stays "unmerged" until `git add`.

# 5. Verify, in this order.
python -m py_compile <the touched Python files>
cd ui && npx tsc -b --noEmit && npm run build && cd ..
python -m unittest discover -s tests -p 'test_director_*.py'
python -m unittest discover -s tests -p 'test_*.py'

# 6. Check the ratchet.
python scripts/upstream_update_guard.py

# 7. Publish.
git commit -F <message-file>
git checkout main && git merge --ff-only v241-merge
git push fork main
```

`scripts/upstream_update_guard.py` needs no arguments to check the ratchet:

- a shared file carrying **more** of our lines than its budget is a failure:
  extract the code, do not raise the number;
- a shared file with **no budget line** is a failure too, because we started
  editing another file upstream owns without deciding to;
- `--write-baseline` records the current numbers and is only used from a tree
  that has just been verified.

The budget in `scripts/upstream_seam_budget.json` carries a `notes` entry for
every file we leave alone on purpose, so a reader can see the reason next to the
number instead of having to remember it.

`tests/test_upstream_update_guard.py` runs the check as a test, so a merge that
grows the surface fails the battery rather than passing quietly.

## The two rules of a seam

1. **One line in the upstream file.** A seam is an import, a spread or a call. If
   resolving a conflict needs more than that, the seam is in the wrong place.
2. **The behaviour lives in our module**, so a future conflict is resolved by
   taking their side and re-applying their change *inside* our module. The test
   that pins that behaviour reads our module and still asserts the seam, so
   dropping the seam during a merge fails a test instead of losing a feature.

## Where our code lives

| upstream's file | our module | what it owns |
|---|---|---|
| `ui/src/api/client.ts` | `ui/src/api/outputDelete.ts` | the delete endpoint split, the forced delete, the server's reason |
| | `ui/src/api/directorRevision.ts` | the shot-correction conversation |
| | `ui/src/api/directorPlanOperation.ts` | the live planning pass |
| `ui/src/stores/useStore.ts` | `ui/src/stores/directorScriptSlice.ts` | a written script as the source of the words |
| | `ui/src/stores/directorPlanRouting.ts` | which planner a skill runs, a cancelled pass, a blocked reroll |
| | `ui/src/stores/directorDeleteGuard.ts` | the refused delete and its question |
| | `ui/src/lib/directorSpeakerId.ts` | the stable `(S1)`/`(S2)` labels and the implied cast |
| | `ui/src/stores/directorSlice.ts` | the barrel the store imports in one line |
| `ui/src/components/MainContent/MediaFeedItem.tsx` | `ui/src/lib/directorReroll.ts` | regenerate through the pipeline instead of the Studio reroll |
| | `ui/src/components/MainContent/DirectorTakeBadges.tsx` | the shot and "new take" badges, progress, failures |
| `app/launch.py` | `app/services/director_slot_guard.py` | which shot is using a file, and the refusal that names it |

## Deferred on purpose

- The wiring inside upstream's `director_v2_plan` and `_run_generation` endpoints,
  and the revise-prompt endpoint. Those are hooks inside upstream functions, so
  they cannot be delegated without rewriting the endpoint; the budget keeps them
  from growing while they wait.
- `app/services/director/h3_dialogue.py` (3014) and `app/services/director_pipeline.py`
  (1798), `DirectorDashboard.tsx` (592) and `DirectorChat.tsx` (215): zero upstream
  churn, so they cost nothing today.

## When a conflict lands outside the seam map

Take it as a finding, not as bad luck: it means a file we believed was safe moved
under us. Resolve it, then either extract that code or add the file to the budget
with its reason written down -- and preferably push the fix upstream, which is the
only route to zero conflicts.
