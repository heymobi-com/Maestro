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
