// Apply a "local fixes" bundle to an existing install, then rebuild the UI.
//
// Why this script exists instead of just using Update: Update rebuilds the
// React interface only when "git pull" reports new commits. A fixes bundle
// arrives as a file rather than from the configured remote, so after the merge
// the pull says "already up to date" and Update takes its fast path -- leaving
// the old interface in ui/dist while app/ is new. Every user-visible feature
// lives in ui/dist, so the build is the one step that must not be skipped.
//
// Prerequisite: run Update once first, so the install is on the upstream commit
// the bundle was built from. The bundle is fetched into a local branch named
// "maestro-local-fixes" and merged; nothing is pushed, and the upstream remote
// is left alone, so Update keeps working afterwards.
//
// If a future bundle changes app/requirements.txt, add the dependency install
// step from update.js here as well. The current bundle needs none: it only
// edits Python and TypeScript source.
module.exports = {
  run: [{
    // The bundle travels as a single file, so ask for it instead of guessing
    // which folder it was downloaded to.
    method: "filepicker.open",
    params: {
      title: "Select the Maestro fixes bundle (.bundle)",
      type: "file",
      filetypes: [["Git bundle", "*.bundle"]]
    }
  }, {
    // Copy it into the repo first: git then receives a short relative path with
    // no spaces, which avoids a class of quoting problems in the shell.
    method: "fs.copy",
    params: {
      src: "{{input.paths[0]}}",
      dest: "dist/local_fixes.bundle"
    }
  }, {
    // Fetching into a branch is non-destructive. A bundle whose base commit is
    // missing fails here, while the working tree is still untouched, so the
    // diagnostic is "run Update first" rather than a half-applied state.
    method: "shell.run",
    params: {
      message: "git fetch dist/local_fixes.bundle main:maestro-local-fixes"
    }
  }, {
    // -c on the command line only: a merge commit needs an author, and applying
    // the fixes must neither depend on nor overwrite the user's own git config.
    method: "shell.run",
    params: {
      message: "git -c user.name=Maestro -c user.email=maestro@localhost merge --no-edit maestro-local-fixes"
    }
  }, {
    // The merge output decides the next step (captured here as input.stdout,
    // exactly as update.js branches on its pull). A conflict must never be
    // followed by a build: it would ship an interface with conflict markers in
    // the source.
    method: "jump",
    params: {
      id: "{{/CONFLICT|Automatic merge failed/i.test(input.stdout) ? 'conflict' : 'build'}}"
    }
  }, {
    id: "conflict",
    method: "log",
    params: {
      raw: "The fixes conflict with this install and were NOT applied. Run \"git merge --abort\" in the app folder to return to the previous state, then send this log."
    },
    next: null
  }, {
    // Unconditional, and deliberately not gated on the pull: this is the step
    // Update skips when the commits arrived from a file. Mirrors the build
    // steps of install.js and update.js.
    id: "build",
    method: "shell.run",
    params: {
      path: "ui",
      message: [
        "npm install",
        "npm run build"
      ]
    }
  }, {
    method: "notify",
    params: {
      html: "Fixes applied and the interface rebuilt. Stop Maestro if it is running, start it again, and hard-refresh the browser (Ctrl+Shift+R) so the new bundle is loaded."
    }
  }]
}
