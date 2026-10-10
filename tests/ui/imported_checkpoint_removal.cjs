// Exercise the imported-checkpoint affordances through their real module, the
// real API client and a recording stub for the calls they make.
// Run: node tests/ui/imported_checkpoint_removal.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const ts = require(path.join(root, 'ui/node_modules/typescript'));

function transpile(relative, requireModule, globals = {}) {
  const filename = path.join(root, relative);
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
      esModuleInterop: true,
    },
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(code, {
    module,
    exports: module.exports,
    require: requireModule,
    console,
    Error,
    Object,
    Symbol,
    encodeURIComponent,
    setImmediate,
    setTimeout,
    ...globals,
  }, { filename });
  return module.exports;
}

// The components are plain functions of their props, so a hook store by index
// and a jsx factory that returns the element as an object drive them without a
// DOM, and a click is a call to the element's own onClick.
function createHookHost() {
  let store = [];
  let cursor = 0;
  const react = {
    useState(initial) {
      const index = cursor++;
      if (!(index in store)) store[index] = typeof initial === 'function' ? initial() : initial;
      return [store[index], next => { store[index] = typeof next === 'function' ? next(store[index]) : next; }];
    },
    useCallback: fn => fn,
    useEffect: () => {},
  };
  const jsxRuntime = {
    Fragment: 'Fragment',
    jsx: (type, props) => ({ type, props: props || {} }),
    jsxs: (type, props) => ({ type, props: props || {} }),
  };
  return {
    react,
    jsxRuntime,
    render(component, props) {
      cursor = 0;
      return component(props);
    },
  };
}

const lucide = { AlertTriangle: 'AlertTriangle', Loader2: 'Loader2', Trash2: 'Trash2' };

function loadHealth(host, api, confirmWindow) {
  return transpile('ui/src/components/LoraBrowser/ImportedCheckpointHealth.tsx', name => {
    if (name === 'react') return host.react;
    if (name === 'react/jsx-runtime') return host.jsxRuntime;
    if (name === 'lucide-react') return lucide;
    if (name === '../../api/client') return { reloadModels: api.reloadModels };
    if (name === '../../api/importedCheckpoints') return { removeImportedCheckpoint: api.removeImportedCheckpoint };
    throw new Error(`unexpected import ${name}`);
  }, { window: confirmWindow });
}

const item = {
  model_type: 'civitai_h3_2851079_3294059_3185154_references',
  name: 'H3 Eros Max · beta5 · INT8 ConvRot — References (CivitAI)',
  architecture: 'minimax_h3_ref2va',
  civitai_model_id: 2851079,
  current_version_id: 3294059,
  base_model: 'MiniMax H3',
  filename: 'h3ErosMax_beta5_3185154.safetensors',
  auto_quantize: false,
  update_status: 'unknown',
  missing: true,
  latest_version_id: null,
  latest_published_at: null,
  latest_changelog: null,
  preview_url: null,
};

const settle = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  const calls = [];
  let deferred = null;
  let confirmed = false;
  const client = {
    removeImportedCheckpoint: modelType => {
      calls.push(['remove', modelType]);
      return deferred ? deferred.promise : Promise.resolve({ registrations: [modelType] });
    },
    reloadModels: () => {
      calls.push(['reload']);
      return Promise.resolve({ status: 'ok', model_count: 1, added: [] });
    },
  };
  const confirmWindow = { confirm: () => confirmed };

  const host = createHookHost();
  const health = loadHealth(host, client, confirmWindow);

  const absent = host.render(health.ImportedCheckpointMissingBadge, { item: { ...item, missing: false } });
  assert.equal(absent, null, 'a healthy install carries no badge');
  const badge = host.render(health.ImportedCheckpointMissingBadge, { item });
  assert.equal(badge.type, 'span');
  assert.match(badge.props.title, /weights are gone/);

  let removedCallbacks = 0;
  const button = host.render(health.ImportedCheckpointRemoveButton, {
    item,
    onRemoved: () => { removedCallbacks += 1; },
  });
  assert.equal(button.props['aria-label'], `Remove ${item.name}`);

  await button.props.onClick();
  await settle();
  assert.deepEqual(calls, [], 'a dismissed confirm removes nothing');
  assert.equal(removedCallbacks, 0);

  confirmed = true;
  await button.props.onClick();
  await settle();
  assert.deepEqual(calls, [['remove', item.model_type], ['reload']]);
  assert.equal(removedCallbacks, 1, 'the list refreshes after the removal');
  assert.notEqual(
    host.render(health.ImportedCheckpointRemoveButton, { item, onRemoved: () => {} }).props.disabled,
    true,
    'the button is idle again',
  );

  // A removal in flight must not be started twice. React re-renders on the
  // state change, so the second click is the one the live button would carry.
  calls.length = 0;
  let release;
  deferred = { promise: new Promise(resolve => { release = resolve; }) };
  const first = host.render(health.ImportedCheckpointRemoveButton, { item, onRemoved: () => {} });
  await first.props.onClick();
  await settle();
  const second = host.render(health.ImportedCheckpointRemoveButton, { item, onRemoved: () => {} });
  assert.equal(second.props.disabled, true, 'the button is disabled while removing');
  await second.props.onClick();
  await settle();
  assert.deepEqual(calls, [['remove', item.model_type]], 'one removal at a time');
  release({ registrations: [item.model_type] });
  await settle();
  await settle();
  assert.equal(calls.at(-1)[0], 'reload');
  deferred = null;

  // A failed removal must not refresh as if it had worked.
  let failures = 0;
  const logged = console.error;
  console.error = () => { failures += 1; };
  const failingHost = createHookHost();
  const failing = loadHealth(failingHost, {
    removeImportedCheckpoint: () => Promise.reject(new Error('HTTP 500')),
    reloadModels: () => Promise.resolve({}),
  }, confirmWindow);
  let refreshed = 0;
  const failed = failingHost.render(failing.ImportedCheckpointRemoveButton, {
    item,
    onRemoved: () => { refreshed += 1; },
  });
  await failed.props.onClick();
  await settle();
  console.error = logged;
  assert.equal(failures, 1, 'the failure is reported, not swallowed');
  assert.equal(refreshed, 0, 'a failed removal does not refresh as if it worked');

  // And the client itself: the path, the verb and the refusal.
  const requests = [];
  const api = transpile('ui/src/api/importedCheckpoints.ts', () => {
    throw new Error('the client module imports nothing');
  }, {
    fetch: async (url, options) => {
      requests.push([url, options.method]);
      return { ok: true, json: async () => ({ registrations: ['a'] }) };
    },
  });
  assert.deepEqual(await api.removeImportedCheckpoint('a b/c'), { registrations: ['a'] });
  assert.deepEqual(requests, [['/api/v1/checkpoints/a%20b%2Fc', 'DELETE']]);

  const refused = transpile('ui/src/api/importedCheckpoints.ts', () => {
    throw new Error('the client module imports nothing');
  }, { fetch: async () => ({ ok: false, status: 500, json: async () => ({}) }) });
  await assert.rejects(
    () => refused.removeImportedCheckpoint('x'),
    /Failed to remove the imported checkpoint/,
  );

  console.log('badge, confirm gate, in-flight guard, failure path and client path passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
