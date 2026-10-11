// The Strata entry in the LLM provider menu: the URL check it decides on, and the
// wiring the panel has to carry for the preset to mean anything.
// Run: node tests/ui/strata_provider_menu.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const ts = require(path.join(root, 'ui/node_modules/typescript'));

function load(relative, globals = {}) {
  const filename = path.join(root, relative);
  const module = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(code, { module, exports: module.exports, URL, ...globals }, { filename });
  return module.exports;
}

const { isStrataUrl, STRATA_DEFAULT_URL } = load('ui/src/lib/strataProvider.ts');

assert.equal(STRATA_DEFAULT_URL, 'http://127.0.0.1:8081');
for (const url of [
  'http://127.0.0.1:8081',
  'http://127.0.0.1:8081/',
  '  http://127.0.0.1:8081  ',
  'http://localhost:8081',
  'http://192.168.1.50:8081',
  'http://10.0.0.5:8081',
]) {
  assert.equal(isStrataUrl(url), true, `expected a Strata address: ${url}`);
}
for (const url of [
  'http://127.0.0.1:1234',          // LM Studio's own default port
  'http://192.168.1.50:1234',
  'https://api.deepseek.com',        // hosted, whatever the port
  'https://api.deepseek.com:8081',
  'http://strata.local:8081',        // a name could resolve anywhere; the backend decides
  '',
  undefined,
  'not a url',
]) {
  assert.equal(isStrataUrl(url), false, `expected a non-Strata address: ${url}`);
}

// The panel is a large component; what matters is that the preset is wired, so the
// assertions are on the source, the same way the existing provider tests pin it.
const panel = fs.readFileSync(
  path.join(root, 'ui/src/components/SettingsDrawer/ServicesSettingsPanel.tsx'), 'utf8',
);
assert.match(panel, /import \{ isStrataUrl, STRATA_DEFAULT_URL \} from '\.\.\/\.\.\/lib\/strataProvider'/);
assert.match(panel, /const isStrata = isRemote && isStrataUrl\(servicesConfig\.llm_remote_url\)/);
assert.match(panel, /<option value="strata">/);
// The select shows Strata when it is the address in use, and picks the remote transport.
assert.match(panel, /value=\{isStrata \? 'strata' : provider\}/);
assert.match(panel, /const newProvider = picked === 'strata' \? 'remote' : picked/);
assert.match(panel, /updates\.llm_remote_url = STRATA_DEFAULT_URL/);
// A URL that already points at a Strata on another host is left alone.
assert.match(panel, /if \(picked === 'strata' && !isStrataUrl\(servicesConfig\.llm_remote_url\)\)/);
// LM Studio's memory control is its own API, which Strata does not implement.
assert.match(panel, /\{isRemote && !isStrata && <RemoteModelMemoryControls/);
// The preset must not become a public provider: it runs on this machine.
assert.match(panel, /const PUBLIC_PROVIDERS = new Set\(\['openai', 'anthropic'\]\)/);

console.log('Strata address detection and the provider-menu preset wiring passed');
