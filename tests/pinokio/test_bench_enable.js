const { legacyRuntimeProfile, runtimeProfile } = require("../../scripts/pinokio/launcher_profile")

// Explicit local opt-in only; it never restarts the running server.
module.exports = async (kernel) => {
  const preferred = runtimeProfile(kernel)
  const legacy = legacyRuntimeProfile(kernel)
  const preferredMarker = `../../${preferred.marker}`
  const selectedEnv = preferred.env !== legacy.env
    ? `{{exists('${preferredMarker}') ? '${preferred.env}' : '${legacy.env}'}}`
    : preferred.env
  return { run: [{ method: "shell.run", params: {
    path: "../../app", venv: selectedEnv,
    message: { _: ["python", "-m", "promptbench", "enable"] },
  } }] }
}
