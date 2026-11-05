# Maestro Editor

Use Editor to assemble media into a saved, non-destructive timeline project.
Original files remain separate from trims, transforms and other edit settings.

## Build a timeline

1. Open Editor and select or create a project. Choose the canvas aspect, size and
   frame rate for the intended output.
2. Browse a workspace or upload media in the Media panel. Preview a source before
   adding it, or drag it onto a compatible timeline track.
3. Select clips to trim, split at the playhead, move, copy or duplicate them. Use
   track locks to protect finished sections; linking keeps related clips together.
4. Adjust the selected clip in the Inspector. Check source bounds, transform,
   speed, audio level and any enabled finishing options before export.

Resize the Media and Inspector panes and the timeline using the dividers. Reset
layout restores the default sizes. Smaller screens expose Media and Adjust as
drawers instead of squeezing every desktop pane into the viewport.

## Titles and finishing

Use Text for editable title overlays or Animated title for generated title media.
See [Editor overlays](Editor-overlays.md) for chroma keying, soft edges and alpha
export. Captions supports subtitle import/export; Audio tools provides the
available range-analysis and level controls. Project tools exposes saved revisions
and portable project packages. Model-dependent operations require their supported
local runtime and remain explicit queued actions.

## AI editing and review

The clip AI workspace acts on a selected clip or range. Director imports retain
shot order and provenance. Auto-edit can assemble Director shots without a writer;
creative assembly, polishing and revision use an available configured writer.

Review the proposed edit actions before applying them. Pin important clips and
lock tracks to preserve decisions. Undo restores an applied edit as a single
operation. Planning does not automatically generate missing shots, voices or
music; those require explicit generation actions.

## Export

Review the canvas, output resolution, frame rate, audio and available encoder in
Export. Export creates a new output file. Preview and export can differ for
encoder-specific behavior and some effects, so inspect the exported result.
