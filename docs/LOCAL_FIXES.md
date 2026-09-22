# Local fixes bundle

A fixes bundle is a single `.bundle` file that carries the local commits on top of
upstream Maestro. It exists so one person's fixes can reach another person's **existing**
install without a fork, without cloning, and without touching their environments, models or
projects.

## Applying a bundle

1. **Run Update once first.** The bundle records the upstream commit it was built from, so
   the install has to be on that commit or newer. `Advanced > Update`, in the app menu.
2. **`Advanced > Apply local fixes`**, then pick the `.bundle` file when the dialog opens.
3. **Stop Maestro if it is running, start it again, and hard-refresh the browser**
   (`Ctrl+Shift+R`).

The last step is not optional and is the reason `apply_local_fixes.js` exists: the interface
is a compiled React bundle in `ui/dist`, and Maestro's own Update rebuilds it only when
`git pull` reports new commits. A bundle arrives as a file, so the pull afterwards says
"already up to date" and Update would skip the rebuild, leaving the old interface on screen.

### Manual equivalent

If the script cannot run, the same result comes from three commands in the install folder
(`PINOKIO_HOME/api/Maestro.git`), followed by the rebuild:

```
git fetch <path-to-file>.bundle main:maestro-local-fixes
git merge --no-edit maestro-local-fixes
cd ui && npm install && npm run build
```

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

## What it does not touch

- No environment is created or reinstalled, and no model or component is downloaded. The
  Python environment, `app/ckpts`, `app/loras`, `app/settings` and `ui/node_modules` are
  left exactly as they are.
- No project or render output is modified. Projects live in `app/outputs/`, which is not
  part of the repository.
- Nothing is pushed anywhere. The bundle is fetched into a local branch named
  `maestro-local-fixes` and merged into `main`; the `origin` remote is untouched.

## Consequences

- After applying a bundle, `main` holds commits that upstream does not have. `Update` keeps
  working, but it merges upstream into `main` instead of fast-forwarding, so each future
  Update may create a merge commit.
- If upstream edits the same lines, that Update stops on a conflict. `git merge --abort`
  returns the install to the previous state; nothing is lost.
- To go back to a plain upstream install, `git reset --hard origin/main` and run Update.
