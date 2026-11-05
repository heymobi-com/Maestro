const { legacyRuntimeProfile, runtimeProfile } = require("../../scripts/pinokio/launcher_profile")

// A one-shot action, like system/examples/errortest/test.js: shell.run uses
// an existing venv and waits for completion. Maestro itself keeps running.
module.exports = async (kernel) => {
  const preferred = runtimeProfile(kernel)
  const legacy = legacyRuntimeProfile(kernel)
  const preferredMarker = `../../${preferred.marker}`
  const selectedEnv = preferred.env !== legacy.env
    ? `{{exists('${preferredMarker}') ? '${preferred.env}' : '${legacy.env}'}}`
    : preferred.env
  return {
    run: [{
      method: "shell.run",
      params: {
        path: "../../app",
        venv: selectedEnv,
        env: { MAESTRO_TEST_BASE_URL: "{{args.base_url}}" },
        message: { _: ["python", "-m", "testbench", "--mode", "{{args.mode || 'smoke'}}"] },
      },
    }],
  }
}
