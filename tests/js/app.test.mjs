import { test } from 'node:test';
import assert from 'node:assert/strict';

import { createDom, loadAppScripts, createApp, installChartStub } from './harness.mjs';

async function makeApp(bodyHtml = '') {
  const dom = createDom(bodyHtml);
  const window = await loadAppScripts(dom);
  installChartStub(window);
  const app = createApp(window);
  app._window = window;
  return app;
}

// ---------------------------------------------------------------- Bug #7

test('escapeHtml neutralises HTML metacharacters', async () => {
  const app = await makeApp();
  assert.equal(
    app.escapeHtml('<img src=x onerror=alert(1)>.stl'),
    '&lt;img src=x onerror=alert(1)&gt;.stl',
  );
  assert.equal(app.escapeHtml(`"'&`), '&quot;&#39;&amp;');
  assert.equal(app.escapeHtml(null), '');
  assert.equal(app.escapeHtml(undefined), '');
  assert.equal(app.escapeHtml(42), '42');
});

test('renderActiveSTLChips escapes the filename', async () => {
  const app = await makeApp('<div id="stl-chip-list"></div><span id="active-stl-count"></span>');
  app.viewer = null;
  app.renderActiveSTLChips(['<img src=x onerror=alert(1)>.stl']);
  const html = app._window.document.getElementById('stl-chip-list').innerHTML;
  assert.ok(!html.includes('<img'), 'raw tag must not survive');
  assert.ok(html.includes('&lt;img'));
});

test('renderQueueTable escapes SLURM job fields', async () => {
  const app = await makeApp('<table><tbody id="slurm-queue-tbody"></tbody></table>');
  app.renderQueueTable([
    {
      job_id: '1', name: '<script>alert(1)</script>', partition: 'cpu',
      state: 'RUNNING', time_used: '0:01', time_limit: '1:00', nodes: '1',
    },
  ]);
  const html = app._window.document.getElementById('slurm-queue-tbody').innerHTML;
  assert.ok(!html.includes('<script>'), 'script tag must not survive');
  assert.ok(html.includes('&lt;script&gt;'));
});

test('renderDownloads escapes the case name in text and title', async () => {
  const app = await makeApp('<div id="download-progress-overlay"></div><div id="download-progress-list"></div>');
  app.downloadStates = new Map([
    ['evil" onmouseover="x', { case_name: 'evil" onmouseover="x', bytes: 0, total_bytes: 0 }],
  ]);
  app.renderDownloads();
  const html = app._window.document.getElementById('download-progress-list').innerHTML;
  assert.ok(!html.includes('onmouseover="x"'), 'attribute injection must not survive');
});

test('showToast escapes the message', async () => {
  const app = await makeApp('<div id="toast-container"></div>');
  app.showToast('<b>x</b>', 'info');
  const html = app._window.document.getElementById('toast-container').innerHTML;
  assert.ok(!html.includes('<b>'));
  assert.ok(html.includes('&lt;b&gt;'));
});

// ---------------------------------------------------------------- Bug #4 / #13

test('solver badge does not report Healthy when continuity is null', async () => {
  const app = await makeApp('<span id="solver-health-badge"></span>');
  app.renderSolverHealth({
    latest_continuity_global: null,
    latest_linear_iters: 7,
    iterations_per_second: null,
    elapsed_seconds: 10,
    eta_seconds: null,
  });
  const badge = app._window.document.getElementById('solver-health-badge').textContent;
  assert.ok(badge.startsWith('No continuity data'), badge);
});

test('solver badge classifies real continuity values', async () => {
  const app = await makeApp('<span id="solver-health-badge"></span>');
  app.renderSolverHealth({ latest_continuity_global: 1e-6, latest_linear_iters: 3 });
  assert.ok(app._window.document.getElementById('solver-health-badge').textContent.startsWith('Healthy'));
  app.renderSolverHealth({ latest_continuity_global: -1e-3, latest_linear_iters: 3 });
  assert.ok(app._window.document.getElementById('solver-health-badge').textContent.startsWith('Fair'));
  app.renderSolverHealth({ latest_continuity_global: 0.5, latest_linear_iters: 3 });
  assert.ok(app._window.document.getElementById('solver-health-badge').textContent.startsWith('High continuity error'));
});

test('a real 0 iterations_per_second is displayed, not "--"', async () => {
  const app = await makeApp('<span id="solver-iter-rate"></span>');
  app.renderSolverHealth({ iterations_per_second: 0, latest_continuity_global: null });
  assert.equal(app._window.document.getElementById('solver-iter-rate').textContent, '0.000 it/s');
});

test('missing iterations_per_second shows "--"', async () => {
  const app = await makeApp('<span id="solver-iter-rate"></span>');
  app.renderSolverHealth({ iterations_per_second: null, latest_continuity_global: null });
  assert.equal(app._window.document.getElementById('solver-iter-rate').textContent, '--');
});

// ---------------------------------------------------------------- Bug #3

test('beginTelemetryCaseSwitch clears the in-flight flag', async () => {
  const app = await makeApp();
  app.telemetryInFlight = true;
  const before = app.telemetryRequestId;
  app.beginTelemetryCaseSwitch('case_a');
  assert.equal(app.telemetryInFlight, false);
  assert.equal(app.telemetryRequestId, before + 1);
});

// ---------------------------------------------------------------- Bug #1

test('validateCurrentConfig sends generate_locally:false', async () => {
  const app = await makeApp();
  let body = null;
  app._window.fetch = async (url, options) => {
    body = JSON.parse(options.body);
    return { ok: true, json: async () => ({ warnings: [] }) };
  };
  app.buildConfigFromVisualForm = () => {};
  app.showToast = () => {};
  await app.validateCurrentConfig();
  assert.equal(body.generate_locally, false);
  assert.equal(body.upload_to_cluster, false);
  assert.equal(body.generate_remotely, false);
  assert.equal(body.submit_slurm, false);
});

// ---------------------------------------------------------------- Bug #5

test('pollTelemetry surfaces a non-ok forces response instead of rendering empty data', async () => {
  const app = await makeApp(`
    <select id="telemetry-case-select"><option value="case_a" selected>case_a</option></select>
    <div id="telemetry-error-banner" style="display:none"></div>
    <div id="telemetry-convergence-pill"><span class="pill-text"></span></div>
  `);
  const calls = [];
  app._window.fetch = async (url) => {
    calls.push(url);
    if (String(url).includes('/api/telemetry/forces')) {
      return { ok: false, status: 500, json: async () => ({ detail: 'boom' }) };
    }
    return { ok: true, json: async () => ({ has_data: false }) };
  };
  // The forces fetch is wrapped in its own try/catch that logs; pollTelemetry
  // itself must not reject, but must not treat the error body as empty data.
  const originalError = app._window.console.error;
  const logged = [];
  app._window.console.error = (...args) => logged.push(args.join(' '));
  try {
    await app.pollTelemetry();
  } finally {
    app._window.console.error = originalError;
  }
  assert.ok(calls.some((u) => String(u).includes('/api/telemetry/forces')));
  assert.ok(logged.some((line) => line.includes('Forces telemetry poll failed')));
  const banner = app._window.document.getElementById('telemetry-error-banner');
  assert.equal(banner.style.display, 'block');
  assert.ok(banner.textContent.includes('Forces request failed (HTTP 500)'));
  const pill = app._window.document.getElementById('telemetry-convergence-pill');
  assert.ok(pill.className.includes('error'));
});

test('clearTelemetryError hides the banner', async () => {
  const app = await makeApp('<div id="telemetry-error-banner" style="display:block">Telemetry unavailable: x</div>');
  app.clearTelemetryError();
  const banner = app._window.document.getElementById('telemetry-error-banner');
  assert.equal(banner.style.display, 'none');
  assert.equal(banner.textContent, '');
});

// ---------------------------------------------------------------- Bug #9 / #11

test('createSTLViewer returns null for a half-initialised viewer', async () => {
  const app = await makeApp();
  app._window.STLViewer = class { constructor() { this.available = false; } };
  const originalWarn = app._window.console.warn;
  app._window.console.warn = () => {};
  try {
    assert.equal(app.createSTLViewer('stl-viewer-container'), null);
  } finally {
    app._window.console.warn = originalWarn;
  }
});

test('createSTLViewer returns a usable viewer when available', async () => {
  const app = await makeApp();
  const fake = { available: true };
  app._window.STLViewer = class { constructor() { return fake; } };
  assert.equal(app.createSTLViewer('stl-viewer-container'), fake);
});

test('disposeTelemetryViewer disposes and clears the telemetry viewer', async () => {
  const app = await makeApp();
  let disposed = false;
  let cleared = false;
  app.telemetryLayer = { clear() { cleared = true; } };
  app.telemetryViewer = { dispose() { disposed = true; } };
  app.telemetry3dCase = 'case_a';
  app.disposeTelemetryViewer();
  assert.equal(disposed, true);
  assert.equal(cleared, true);
  assert.equal(app.telemetryViewer, null);
  assert.equal(app.telemetryLayer, null);
  assert.equal(app.telemetry3dCase, null);
});

test('destroy clears timers and disposes both viewers', async () => {
  const app = await makeApp();
  let mainDisposed = false;
  let telemetryDisposed = false;
  app.pollInterval = setInterval(() => {}, 100000);
  app.viewer = { dispose() { mainDisposed = true; } };
  app.telemetryViewer = { dispose() { telemetryDisposed = true; } };
  app.destroy();
  assert.equal(mainDisposed, true);
  assert.equal(telemetryDisposed, true);
  assert.equal(app.viewer, null);
  assert.equal(app.pollInterval, null);
});

// ------------------------------------------------- Feature-based auto-sizing

test('renderAutoSizePreview summarises the detected feature and refinement', async () => {
  const app = await makeApp('<span id="cfg-auto-size-preview"></span>');
  app.renderAutoSizePreview({
    small_feature_m: 0.00154,
    finest_surface_cell_m: 0.00026,
    surface_level: [4, 7],
    feature_percentile: 5,
    capped: false,
  });
  const text = app._window.document.getElementById('cfg-auto-size-preview').textContent;
  assert.ok(text.includes('1.54 mm'), text);
  assert.ok(text.includes('0.26 mm'), text);
  assert.ok(text.includes('level 7'), text);
  assert.ok(!text.includes('capped'), text);
});

test('renderAutoSizePreview flags a capped result', async () => {
  const app = await makeApp('<span id="cfg-auto-size-preview"></span>');
  app.renderAutoSizePreview({
    small_feature_m: 0.0005,
    finest_surface_cell_m: 0.0001,
    surface_level: [4, 8],
    feature_percentile: 5,
    capped: true,
    max_surface_level: 8,
  });
  const text = app._window.document.getElementById('cfg-auto-size-preview').textContent;
  assert.ok(text.includes('capped at level 8'), text);
});

test('renderAutoSizePreview clears when there is no auto-size info', async () => {
  const app = await makeApp('<span id="cfg-auto-size-preview">stale</span>');
  app.renderAutoSizePreview(null);
  assert.equal(app._window.document.getElementById('cfg-auto-size-preview').textContent, '');
});

test('renderLayerPreview shows auto-size text even without a resolved first layer', async () => {
  const app = await makeApp('<span id="cfg-layer-preview"></span><span id="cfg-auto-size-preview"></span>');
  app.renderLayerPreview({
    mode: 'relative',
    first_layer_thickness: null,
    auto_size: {
      small_feature_m: 0.0062,
      finest_surface_cell_m: 0.0034,
      surface_level: [4, 5],
      feature_percentile: 5,
      capped: false,
    },
  });
  const auto = app._window.document.getElementById('cfg-auto-size-preview').textContent;
  assert.ok(auto.includes('6.20 mm'), auto);
});

// ------------------------------------- Config form -> JSON merge (no rebuild)

function installFormStubs(app) {
  // Minimal getVal/getCheck so buildConfigFromVisualForm can run with a DOM
  // that only carries the fields each test cares about.
  app.getVal = (id) => {
    const el = app._window.document.getElementById(id);
    return el ? el.value : '';
  };
  app.getCheck = (id) => {
    const el = app._window.document.getElementById(id);
    return el ? !!el.checked : false;
  };
  app.syncConfigToJsonDrawer = () => {};
  app.updateDomainBoxVisualization = () => {};
}

const CONFIG_STUB_IDS = [
  'cfg-case-name', 'cfg-flow-velocity-ms', 'cfg-flow-direction', 'cfg-flow-ground',
  'cfg-outputs-drag', 'cfg-outputs-downforce', 'cfg-domain-box', 'cfg-symmetry-plane',
  'cfg-ground-style', 'cfg-ground-clearance', 'cfg-ground-plane',
  'cfg-face-neg-x', 'cfg-face-pos-x', 'cfg-face-neg-y', 'cfg-face-pos-y',
  'cfg-face-pos-z', 'cfg-face-neg-z', 'cfg-parallel-procs', 'cfg-parallel-method',
  'cfg-slurm-qos', 'cfg-slurm-partition', 'cfg-slurm-time', 'cfg-slurm-mem',
  'cfg-slurm-source', 'cfg-slurm-modules', 'cfg-override-feature-angle',
  'cfg-slurm-cpus', 'cfg-override-layer-twopass', 'cfg-override-layer-yplusfit',
  'cfg-override-layer-minratio',
  'cfg-patch-inlet', 'cfg-patch-outlet', 'cfg-patch-ground', 'cfg-patch-walls', 'cfg-patch-symmetry',
  'cfg-surface-enabled', 'cfg-surface-enforce', 'cfg-surface-selfintersection', 'cfg-surface-allowopen',
  'cfg-surface-maxillegal', 'cfg-surface-maxparts',
  'cfg-vehicle-wheelbase', 'cfg-vehicle-frontpct',
  'cfg-override-grading', 'cfg-override-grading-ratio',
];

function buildStubBody() {
  return CONFIG_STUB_IDS.map((id) => `<input id="${id}">`).join('');
}

function seedForm(app) {
  const doc = app._window.document;
  const set = (id, value) => { const el = doc.getElementById(id); if (el) el.value = value; };
  set('cfg-case-name', 'my_case');
  set('cfg-flow-velocity-ms', '16.67');
  set('cfg-flow-direction', '-z');
  set('cfg-outputs-drag', '-z');
  set('cfg-outputs-downforce', '-y');
  set('cfg-ground-style', 'none');
  set('cfg-face-neg-x', 'symmetry');
  set('cfg-face-pos-x', 'farField');
  set('cfg-face-neg-y', 'ground');
  set('cfg-face-pos-y', 'farField');
  set('cfg-face-pos-z', 'inlet');
  set('cfg-face-neg-z', 'outlet');
  set('cfg-parallel-procs', '32');
  set('cfg-parallel-method', 'scotch');
  set('cfg-slurm-cpus', '1');
}

test('buildConfigFromVisualForm preserves unknown and comment keys', async () => {
  const app = await makeApp(buildStubBody());
  installFormStubs(app);
  seedForm(app);
  app.activeConfig = {
    case_name: 'old',
    _README: 'keep me',
    _section_domain: '--- domain ---',
    feature_extract: { extractionMethod: 'extractFromSurface', includedAngle: 140 },
    something_future: { nested: true },
  };
  app.buildConfigFromVisualForm();
  const cfg = JSON.parse(JSON.stringify(app.activeConfig));
  assert.equal(cfg._README, 'keep me');
  assert.equal(cfg._section_domain, '--- domain ---');
  assert.equal(cfg.something_future.nested, true);
  assert.equal(cfg.case_name, 'my_case');
  // feature_extract is form-owned: a blank input resets includedAngle, while
  // the non-owned extractionMethod survives.
  assert.equal(cfg.feature_extract.includedAngle, undefined);
  assert.equal(cfg.feature_extract.extractionMethod, 'extractFromSurface');
});

test('buildConfigFromVisualForm updates a form-owned field without dropping siblings', async () => {
  const app = await makeApp(buildStubBody());
  installFormStubs(app);
  seedForm(app);
  app.activeConfig = { case_name: 'old', fidelity: 'standard', _note: 'x' };
  app.buildConfigFromVisualForm();
  assert.equal(app.activeConfig.fidelity, 'standard');
  assert.equal(app.activeConfig._note, 'x');
});

test('buildConfigFromVisualForm preserves untouched override sections', async () => {
  const app = await makeApp(buildStubBody());
  installFormStubs(app);
  seedForm(app);
  app.activeConfig = {
    case_name: 'c',
    overrides: { force_refs: { Aref: 2.0, _comment: 'keep' } },
  };
  app.buildConfigFromVisualForm();
  // The form-owned numeric key is blank so it clears, but the comment key the
  // form does not own survives.
  assert.deepEqual(
    JSON.parse(JSON.stringify(app.activeConfig.overrides.force_refs)),
    { _comment: 'keep' },
  );
});

test('buildConfigFromVisualForm emits cpus_per_task', async () => {
  const app = await makeApp(buildStubBody());
  installFormStubs(app);
  seedForm(app);
  app._window.document.getElementById('cfg-slurm-cpus').value = '4';
  app.activeConfig = { case_name: 'c' };
  app.buildConfigFromVisualForm();
  assert.equal(app.activeConfig.slurm.cpus_per_task, 4);
});

test('buildConfigFromVisualForm defaults cpus_per_task when blank', async () => {
  const app = await makeApp(buildStubBody());
  installFormStubs(app);
  seedForm(app);
  app._window.document.getElementById('cfg-slurm-cpus').value = '';
  app.activeConfig = { case_name: 'c' };
  app.buildConfigFromVisualForm();
  assert.equal(app.activeConfig.slurm.cpus_per_task, 1);
});

test('buildConfigFromVisualForm emits feature_extract includedAngle', async () => {
  const app = await makeApp(buildStubBody());
  installFormStubs(app);
  seedForm(app);
  app._window.document.getElementById('cfg-override-feature-angle').value = '120';
  app.activeConfig = { case_name: 'c' };
  app.buildConfigFromVisualForm();
  assert.equal(app.activeConfig.feature_extract.includedAngle, 120);
});

test('buildConfigFromVisualForm preserves feature_extract siblings', async () => {
  const app = await makeApp(buildStubBody());
  installFormStubs(app);
  seedForm(app);
  app._window.document.getElementById('cfg-override-feature-angle').value = '120';
  app.activeConfig = {
    case_name: 'c',
    feature_extract: { extractionMethod: 'extractFromSurface', includedAngle: 140 },
  };
  app.buildConfigFromVisualForm();
  assert.equal(app.activeConfig.feature_extract.includedAngle, 120);
  assert.equal(app.activeConfig.feature_extract.extractionMethod, 'extractFromSurface');
});

test('buildConfigFromVisualForm drops a section whose active inputs are blank', async () => {
  const app = await makeApp(
    buildStubBody() + '<input id="cfg-override-ref-aref"><input id="cfg-override-solver-endtime">',
  );
  installFormStubs(app);
  seedForm(app);
  app._window.document.getElementById('cfg-override-solver-endtime').value = '1500';
  app.activeConfig = {
    case_name: 'c',
    // Aref blank in the form -> force_refs dropped; end_time present -> kept.
    overrides: { force_refs: { Aref: 2.0 }, solver: { end_time: 999 } },
  };
  app.buildConfigFromVisualForm();
  assert.equal(app.activeConfig.overrides.force_refs, undefined);
  assert.deepEqual(
    JSON.parse(JSON.stringify(app.activeConfig.overrides.solver)),
    { end_time: 1500 },
  );
});

// ------------------------------------------- Patches / surface / vehicle / grading
test('buildConfigFromVisualForm writes patches, surface_check, vehicle and grading', async () => {
  const app = await makeApp(buildStubBody());
  installFormStubs(app);
  seedForm(app);
  const doc = app._window.document;
  doc.getElementById('cfg-patch-walls').value = 'carShell';
  doc.getElementById('cfg-surface-enforce').checked = true;
  doc.getElementById('cfg-vehicle-wheelbase').value = '1.6';
  doc.getElementById('cfg-vehicle-frontpct').value = '45';
  doc.getElementById('cfg-override-grading').value = 'on';
  doc.getElementById('cfg-override-grading-ratio').value = '4';

  app.activeConfig = { case_name: 'c' };
  app.buildConfigFromVisualForm();

  assert.equal(app.activeConfig.patches.walls, 'carShell');
  assert.equal(app.activeConfig.patches.inlet, 'inlet');
  assert.equal(app.activeConfig.surface_check.enforce, true);
  assert.equal(app.activeConfig.surface_check.enabled, false); // stub checkbox unchecked
  assert.equal(app.activeConfig.vehicle.wheelbase, 1.6);
  assert.equal(app.activeConfig.vehicle.front_weight_pct, 45);
  assert.equal(app.activeConfig.overrides.mesh_params.grading, 'auto');
  assert.equal(app.activeConfig.overrides.mesh_params.grading_ratio, 4);
});

test('buildConfigFromVisualForm omits vehicle when both fields are blank', async () => {
  const app = await makeApp(buildStubBody());
  installFormStubs(app);
  seedForm(app);
  app.activeConfig = { case_name: 'c' };
  app.buildConfigFromVisualForm();
  assert.equal(app.activeConfig.vehicle, undefined);
});

// ------------------------------------------- SLURM preset governance
test('buildConfigFromVisualForm omits slurm time/mem unless pinned (preset governs)', async () => {
  const app = await makeApp(buildStubBody());
  installFormStubs(app);
  seedForm(app);
  app.activeConfig = { case_name: 'c' };
  app.buildConfigFromVisualForm();
  assert.equal(app.activeConfig.slurm.time, undefined);
  assert.equal(app.activeConfig.slurm.mem_per_cpu, undefined);

  app._window.document.getElementById('cfg-slurm-time').value = '04:00:00';
  app._window.document.getElementById('cfg-slurm-mem').value = '4G';
  app.buildConfigFromVisualForm();
  assert.equal(app.activeConfig.slurm.time, '04:00:00');
  assert.equal(app.activeConfig.slurm.mem_per_cpu, '4G');
});

// ------------------------------------------- Two-pass layering
test('buildConfigFromVisualForm emits two_pass on/off/auto', async () => {
  const app = await makeApp(buildStubBody());
  installFormStubs(app);
  seedForm(app);
  const sel = app._window.document.getElementById('cfg-override-layer-twopass');

  sel.value = 'on';
  app.activeConfig = { case_name: 'c' };
  app.buildConfigFromVisualForm();
  assert.equal(app.activeConfig.overrides.layers.two_pass, true);

  sel.value = 'off';
  app.buildConfigFromVisualForm();
  assert.equal(app.activeConfig.overrides.layers.two_pass, false);

  sel.value = 'auto';
  app.buildConfigFromVisualForm();
  assert.equal(app.activeConfig.overrides?.layers?.two_pass, undefined);
});

// ------------------------------------------- y+ fit
test('buildConfigFromVisualForm emits y_plus_fit on/off/auto', async () => {
  const app = await makeApp(buildStubBody());
  installFormStubs(app);
  seedForm(app);
  const sel = app._window.document.getElementById('cfg-override-layer-yplusfit');

  sel.value = 'on';
  app.activeConfig = { case_name: 'c' };
  app.buildConfigFromVisualForm();
  assert.equal(app.activeConfig.overrides.layers.y_plus_fit, true);

  sel.value = 'off';
  app.buildConfigFromVisualForm();
  assert.equal(app.activeConfig.overrides.layers.y_plus_fit, false);

  sel.value = 'auto';
  app.buildConfigFromVisualForm();
  assert.equal(app.activeConfig.overrides?.layers?.y_plus_fit, undefined);
});

// ------------------------------------------- Diagnostic field outputs
test('buildConfigFromVisualForm writes field_outputs from the checkboxes', async () => {
  const app = await makeApp(buildStubBody() + `
    <input type="checkbox" id="cfg-field-wall-shear" checked>
    <input type="checkbox" id="cfg-field-yplus" checked>
    <input type="checkbox" id="cfg-field-minmax">
    <input type="checkbox" id="cfg-field-vorticity" checked>
  `);
  installFormStubs(app);
  seedForm(app);
  app.activeConfig = { case_name: 'c' };
  app.buildConfigFromVisualForm();
  assert.deepEqual(JSON.parse(JSON.stringify(app.activeConfig.field_outputs)), {
    wall_shear_stress: true,
    y_plus: true,
    field_min_max: false,
    vorticity: true,
  });
});
test('buildConfigFromVisualForm leaves field_outputs untouched without the controls', async () => {
  const app = await makeApp(buildStubBody());
  installFormStubs(app);
  seedForm(app);
  app.activeConfig = { case_name: 'c', field_outputs: { vorticity: true } };
  app.buildConfigFromVisualForm();
  assert.equal(app.activeConfig.field_outputs.vorticity, true);
});

test('renderLayerPreview reports an applied y+ fit', async () => {
  const app = await makeApp('<span id="cfg-layer-preview"></span>');
  installFormStubs(app);
  app.renderLayerPreview({
    u_tau: 0.65,
    first_layer_thickness: 0.001,
    y_plus_effective: 30,
    stack: 0.01,
    fit_applied: true,
    fit_ratio: 0.69,
    fit_level: 4,
  });
  const text = app._window.document.getElementById('cfg-layer-preview').textContent;
  assert.ok(text.includes('y+ fit'), text);
  assert.ok(text.includes('0.69'), text);
});

test('renderLayerPreview shows the effective layer controls', async () => {
  const app = await makeApp('<span id="cfg-layer-preview"></span><span id="cfg-effective-layers"></span>');
  installFormStubs(app);
  app.renderLayerPreview({
    first_layer_thickness: 0.001,
    y_plus_effective: 30,
    stack: 0.01,
    n_layers: 8,
    expansion_ratio: 1.15,
    maxFaceThicknessRatio: 0.7,
    min_thickness_ratio: 0.35,
    two_pass: true,
    y_plus_fit: false,
  });
  const text = app._window.document.getElementById('cfg-effective-layers').textContent;
  assert.ok(text.includes('Effective layers'), text);
  assert.ok(text.includes('n_layers 8'), text);
  assert.ok(text.includes('expansion 1.15'), text);
  assert.ok(text.includes('maxFaceThicknessRatio 0.7'), text);
  assert.ok(text.includes('min_thickness_ratio 0.35'), text);
  assert.ok(text.includes('two_pass on'), text);
  assert.ok(text.includes('y+ fit off'), text);
});

// ------------------------------------------- Symmetry (full car)
test('buildConfigFromVisualForm omits symmetry_plane for a blank field (full car)', async () => {
  const app = await makeApp(buildStubBody());
  installFormStubs(app);
  seedForm(app);
  app._window.document.getElementById('cfg-symmetry-plane').value = '';
  app.activeConfig = { case_name: 'c' };
  app.buildConfigFromVisualForm();
  assert.equal(app.activeConfig.symmetry_plane, undefined);

  app._window.document.getElementById('cfg-symmetry-plane').value = '0.5';
  app.buildConfigFromVisualForm();
  assert.equal(app.activeConfig.symmetry_plane, 0.5);
});

// ------------------------------------------- Fidelity cards (data-driven)

test('updateFidelityCards sources cell/time text from the server presets', async () => {
  const app = await makeApp(`
    <label class="fidelity-card" data-fidelity="fast">
      <span class="card-cells">stale</span><span class="card-time">stale</span>
    </label>
    <label class="fidelity-card" data-fidelity="standard">
      <span class="card-cells">stale</span><span class="card-time">stale</span>
    </label>
  `);
  app.fidelityPresets = {
    fast: { cell_estimate: '~3-5M cells', runtime_estimate: '~10-20 min' },
    standard: { cell_estimate: '~9-13M cells', runtime_estimate: '~1-2 hrs' },
  };
  app.updateFidelityCards();
  const doc = app._window.document;
  const fast = doc.querySelector('.fidelity-card[data-fidelity="fast"]');
  assert.equal(fast.querySelector('.card-cells').textContent, '~3-5M cells');
  assert.equal(fast.querySelector('.card-time').textContent, '~10-20 min');
  const std = doc.querySelector('.fidelity-card[data-fidelity="standard"]');
  assert.equal(std.querySelector('.card-cells').textContent, '~9-13M cells');
});

test('updateFidelityCards is a no-op without presets', async () => {
  const app = await makeApp(
    '<label class="fidelity-card" data-fidelity="fast"><span class="card-cells">keep</span></label>',
  );
  app.fidelityPresets = null;
  app.updateFidelityCards();
  assert.equal(
    app._window.document.querySelector('.card-cells').textContent,
    'keep',
  );
});

// ------------------------------------------------- Studio mesh-quality panel

const MESH_BODY = `
  <span id="mesh-quality-badge"></span>
  <span id="mesh-cells"></span>
  <span id="mesh-nonortho"></span>
  <span id="mesh-skewness"></span>
  <span id="mesh-aspect"></span>
  <table><tbody id="mesh-metrics-tbody"></tbody></table>
  <table><tbody id="mesh-layer-tbody"></tbody></table>
  <table><tbody id="mesh-patch-tbody"></tbody></table>
  <table><tbody id="mesh-celltype-tbody"></tbody></table>
  <p id="mesh-quality-note-yplus"></p>
  <p id="mesh-quality-note"></p>
`;

test('renderMeshQuality shows metrics, coverage and an OK badge', async () => {
  const app = await makeApp(MESH_BODY);
  app.renderMeshQuality({
    ok: true,
    verdict: 'good',
    verdict_label: 'Good',
    stats: { cells: 9008844, max_non_ortho: 45.2, avg_non_ortho: 3.1, max_skewness: 1.2, max_aspect_ratio: 12.5 },
    metrics: [
      { key: 'max_non_ortho', label: 'Max non-orthogonality', value: 45.2, limit_text: '<= 65', pass: true, level: 'good' },
      { key: 'concave_cells', label: 'Concave cells', value: 0, limit_text: '== 0', pass: true, level: 'good', integer: true },
    ],
    layers: { geometry: { layers: 2, coverage: 1.0, thickness_fraction: 0.9 } },
    patches: { geometry: { faces: 124862, closed: true, closure: 'closed singly connected' } },
    cell_types: { hexahedra: { count: 8881583, fraction: 0.9859 } },
    y_plus: { available: true, target: 40, patches: { geometry: { average: 42.0, min: 12.0, max: 118.0 } }, missed: [], note: 'all patches within 50% of target' },
    target_layers: 2,
    issues: [],
    note: 'mesh quality OK',
  });
  const doc = app._window.document;
  assert.equal(doc.getElementById('mesh-cells').textContent, (9008844).toLocaleString());
  assert.equal(doc.getElementById('mesh-nonortho').textContent, '45.20 / 3.10');
  const badge = doc.getElementById('mesh-quality-badge');
  assert.ok(badge.className.includes('mesh-quality-badge-ok'));
  assert.equal(badge.textContent, 'Good');
  const rows = doc.getElementById('mesh-layer-tbody').innerHTML;
  assert.ok(rows.includes('geometry'));
  assert.ok(rows.includes('2/2'));
  assert.ok(rows.includes('100%'));
  assert.ok(rows.includes('90%'), 'thickness coverage column');
  assert.ok(rows.includes('42.0 / 40'), 'realised y+ vs target');
  assert.ok(rows.includes('12.0 – 118.0'), 'y+ min–max range');
  assert.ok(doc.getElementById('mesh-quality-note-yplus').textContent.includes('all patches'));
  // metrics table
  const metrics = doc.getElementById('mesh-metrics-tbody').innerHTML;
  assert.ok(metrics.includes('Max non-orthogonality'));
  assert.ok(metrics.includes('mesh-metric-pass'));
  // patch table
  const patch = doc.getElementById('mesh-patch-tbody').innerHTML;
  assert.ok(patch.includes('closed singly connected'));
  // cell types
  const ct = doc.getElementById('mesh-celltype-tbody').innerHTML;
  assert.ok(ct.includes('hexahedra'));
  assert.ok(ct.includes('98.6%'));
});

test('renderMeshQuality surfaces field diagnostics (max y+ location)', async () => {
  const app = await makeApp(MESH_BODY);
  app.renderMeshQuality({
    ok: true,
    verdict: 'good',
    verdict_label: 'Good',
    stats: { cells: 10 },
    metrics: [],
    layers: {},
    patches: {},
    cell_types: {},
    y_plus: { available: true, target: 50, patches: {}, missed: [], note: 'all patches met' },
    field_min_max: { yPlus: { max: 242.0, location: [1.0, 0.2, -0.3] } },
    note: 'mesh quality OK',
  });
  const text = app._window.document.getElementById('mesh-quality-note-yplus').textContent;
  assert.ok(text.includes('Field diagnostics'), text);
  assert.ok(text.includes('max y+ 242.0'), text);
  assert.ok(text.includes('(1.00, 0.20, -0.30)'), text);
});

test('renderMeshQuality surfaces failed-check detail', async () => {
  const app = await makeApp(MESH_BODY);
  app.renderMeshQuality({
    ok: false,
    verdict: 'bad',
    verdict_label: 'Bad',
    stats: { cells: 10 },
    metrics: [],
    layers: {},
    patches: {},
    cell_types: {},
    failures: ['17 non-orthogonality error(s)', '5 cell(s) with small determinant'],
    note: 'failed',
  });
  const text = app._window.document.getElementById('mesh-quality-note').textContent;
  assert.ok(text.includes('failed checks'), text);
  assert.ok(text.includes('17 non-orthogonality'), text);
  assert.ok(text.includes('5 cell(s) with small determinant'), text);
});

test('renderTelemetryAnalysis renders the per-part build-up table', async () => {
  const app = await makeApp(`
    <table><tbody id="coeff-summary-tbody"></tbody></table>
    <span id="coeff-source-badge"></span><span id="ref-source-badge"></span>
    <table><tbody id="component-breakdown-tbody"></tbody></table>
    <table><tbody id="per-part-tbody"></tbody></table>
    <span id="reference-conditions"></span>
    <input id="ref-aref"><input id="ref-lref"><input id="ref-rho"><input id="ref-velocity">
    <input id="ref-cofr-x"><input id="ref-cofr-y"><input id="ref-cofr-z">
  `);
  app.renderTelemetryAnalysis({
    coefficients: { summary: {}, source: 'forceCoeffs' },
    components: {},
    reference: {},
    per_part: { front_wing: { drag: 5.0, downforce: 60.0, ld: 12.0 } },
  });
  const rows = app._window.document.getElementById('per-part-tbody').innerHTML;
  assert.ok(rows.includes('front_wing'), rows);
  assert.ok(rows.includes('5.00'), rows);
  assert.ok(rows.includes('60.00'), rows);
  assert.ok(rows.includes('12.00'), rows);
});

test('renderMeshQuality flags concerns and escapes the patch name', async () => {
  const app = await makeApp(MESH_BODY);
  app.renderMeshQuality({
    ok: false,
    verdict: 'bad',
    verdict_label: 'Bad',
    stats: { cells: 10, max_non_ortho: 68.3 },
    metrics: [
      { key: 'max_non_ortho', label: 'Max non-orthogonality', value: 68.3, limit_text: '<= 65', pass: false, level: 'usable' },
      { key: 'concave_cells', label: 'Concave cells', value: 42, limit_text: '== 0', pass: false, level: 'marginal', integer: true },
    ],
    layers: { 'a<b>': { layers: 1, coverage: 0.5 } },
    patches: { 'body<x>': { faces: 10, closed: false, closure: 'non-closed singly connected' } },
    target_layers: 2,
    issues: ['max non-orthogonality 68.3 > limit 65', 'boundary-layer dropout'],
    note: '2 concerns',
  });
  const doc = app._window.document;
  const badge = doc.getElementById('mesh-quality-badge');
  assert.ok(badge.className.includes('mesh-quality-badge-bad'));
  assert.equal(badge.textContent, 'Bad · 2 concerns');
  const rows = doc.getElementById('mesh-layer-tbody').innerHTML;
  assert.ok(!rows.includes('<b>'), 'raw tag must not survive');
  assert.ok(rows.includes('&lt;b&gt;'));
  assert.ok(rows.includes('50%'));
  const metrics = doc.getElementById('mesh-metrics-tbody').innerHTML;
  assert.ok(metrics.includes('mesh-metric-usable'), 'caution band');
  assert.ok(metrics.includes('CAUTION'));
  assert.ok(metrics.includes('mesh-metric-fail'), 'fail band');
  const patch = doc.getElementById('mesh-patch-tbody').innerHTML;
  assert.ok(!patch.includes('<x>'), 'patch tag must not survive');
  assert.ok(patch.includes('non-closed'));
});

test('renderMeshQuality counts y+ misses as concerns and flags the row', async () => {
  const app = await makeApp(MESH_BODY);
  app.renderMeshQuality({
    ok: true,
    verdict: 'usable',
    verdict_label: 'Usable',
    stats: {},
    metrics: [],
    layers: { geometry: { layers: 2, coverage: 1.0 } },
    patches: {},
    target_layers: 2,
    y_plus: { available: true, target: 100, patches: { geometry: { average: 9.0, ok: false } }, missed: ['geometry'] },
    issues: [],
    note: 'usable',
  });
  const doc = app._window.document;
  const badge = doc.getElementById('mesh-quality-badge');
  assert.equal(badge.textContent, 'Usable · 1 concern');
  const rows = doc.getElementById('mesh-layer-tbody').innerHTML;
  assert.ok(rows.includes('9.0 / 100'));
  assert.ok(rows.includes('mesh-metric-fail'), 'missed y+ row is flagged');
});

test('resetMeshQuality clears the panel and shows a message', async () => {
  const app = await makeApp(MESH_BODY);
  app.renderMeshQuality({ ok: true, stats: { cells: 5 }, layers: {}, note: 'x' });
  app.resetMeshQuality('no logs');
  const doc = app._window.document;
  assert.equal(doc.getElementById('mesh-cells').textContent, '--');
  assert.equal(doc.getElementById('mesh-quality-note').textContent, 'no logs');
  const badge = doc.getElementById('mesh-quality-badge');
  assert.equal(badge.textContent, '--');
  assert.ok(!badge.className.includes('mesh-quality-badge-ok'));
  assert.ok(doc.getElementById('mesh-metrics-tbody').innerHTML.includes('No metrics'));
});

test('pollTelemetry fetches and renders the mesh-quality endpoint', async () => {
  const app = await makeApp(`
    <select id="telemetry-case-select"><option value="case_a" selected>case_a</option></select>
    <span id="telemetry-error-banner"></span>
    <div id="telemetry-convergence-pill"><span class="pill-text"></span></div>
    ${MESH_BODY}
  `);
  const calls = [];
  app._window.console.error = () => {};
  app._window.fetch = async (url) => {
    calls.push(String(url));
    const target = String(url);
    if (target.includes('/api/telemetry/forces')) {
      return { ok: false, status: 500, json: async () => ({ detail: 'boom' }) };
    }
    if (target.includes('/api/telemetry/mesh')) {
      return {
        ok: true,
        json: async () => ({
          has_data: true, ok: true, stats: { cells: 7 }, layers: {}, issues: [],
        }),
      };
    }
    return { ok: true, json: async () => ({ has_data: false }) };
  };
  await app.pollTelemetry();
  assert.ok(calls.some((u) => u.includes('/api/telemetry/mesh')));
  assert.equal(
    app._window.document.getElementById('mesh-cells').textContent,
    (7).toLocaleString(),
  );
});

// ------------------------------------------------- Studio surface-integrity panel

const SURFACE_BODY = `
  <span id="surface-quality-badge"></span>
  <span id="surface-triangles"></span>
  <span id="surface-closed"></span>
  <span id="surface-illegal"></span>
  <span id="surface-self"></span>
  <span id="surface-parts"></span>
  <table><tbody id="surface-region-tbody"></tbody></table>
  <p id="surface-quality-note"></p>
`;

test('renderSurfaceQuality shows a Good badge for a clean surface', async () => {
  const app = await makeApp(SURFACE_BODY);
  app.renderSurfaceQuality({
    ok: true,
    verdict: 'good',
    verdict_label: 'Good',
    has_symmetry: false,
    closed: true,
    stats: {
      triangles: 21000,
      illegal_triangles: 0,
      self_intersection_checked: true,
      self_intersecting: false,
      unconnected_parts: 1,
      regions: { body: 21000 },
    },
    issues: [],
    warnings: [],
    note: 'surface integrity OK',
  });
  const doc = app._window.document;
  assert.equal(doc.getElementById('surface-triangles').textContent, (21000).toLocaleString());
  assert.equal(doc.getElementById('surface-closed').textContent, 'closed');
  assert.equal(doc.getElementById('surface-illegal').textContent, '0');
  assert.equal(doc.getElementById('surface-self').textContent, 'no');
  assert.equal(doc.getElementById('surface-parts').textContent, '1');
  const badge = doc.getElementById('surface-quality-badge');
  assert.ok(badge.className.includes('mesh-quality-badge-ok'));
  assert.equal(badge.textContent, 'Good');
  assert.ok(doc.getElementById('surface-region-tbody').innerHTML.includes('body'));
  assert.ok(doc.getElementById('surface-quality-note').textContent.includes('OK'));
});

test('renderSurfaceQuality flags a bad surface and escapes the region name', async () => {
  const app = await makeApp(SURFACE_BODY);
  app.renderSurfaceQuality({
    ok: false,
    verdict: 'bad',
    verdict_label: 'Bad',
    has_symmetry: false,
    closed: false,
    stats: {
      triangles: 10,
      illegal_triangles: 2,
      self_intersection_checked: false,
      unconnected_parts: 1,
      regions: { '<img src=x onerror=alert(1)>': 10 },
    },
    issues: ['3 illegal (degenerate/duplicate) triangle(s)'],
    warnings: [],
    note: '[Bad] 1 surface defect(s): x',
  });
  const doc = app._window.document;
  const badge = doc.getElementById('surface-quality-badge');
  assert.ok(badge.className.includes('mesh-quality-badge-bad'));
  assert.ok(badge.textContent.includes('Bad'));
  assert.ok(badge.textContent.includes('1 concern'));
  assert.equal(doc.getElementById('surface-closed').textContent, 'open');
  assert.equal(doc.getElementById('surface-self').textContent, '--');
  assert.ok(!doc.getElementById('surface-region-tbody').innerHTML.includes('<img'));
});

test('renderSurfaceQuality labels a symmetry half model open surface', async () => {
  const app = await makeApp(SURFACE_BODY);
  app.renderSurfaceQuality({
    ok: true, verdict: 'good', verdict_label: 'Good', has_symmetry: true, closed: false,
    stats: {}, issues: [], warnings: [], note: 'surface integrity OK',
  });
  assert.equal(
    app._window.document.getElementById('surface-closed').textContent,
    'open (symmetry)',
  );
});

test('resetSurfaceQuality clears the panel and shows a message', async () => {
  const app = await makeApp(SURFACE_BODY);
  app.renderSurfaceQuality({
    ok: true, verdict: 'good', stats: { triangles: 5 }, regions: {},
    issues: [], warnings: [], note: 'x',
  });
  app.resetSurfaceQuality('no logs');
  const doc = app._window.document;
  assert.equal(doc.getElementById('surface-triangles').textContent, '--');
  assert.equal(doc.getElementById('surface-quality-note').textContent, 'no logs');
  assert.ok(!doc.getElementById('surface-quality-badge').className.includes('mesh-quality-badge-ok'));
});

test('pollTelemetry fetches and renders the surface-integrity endpoint', async () => {
  const app = await makeApp(`
    <select id="telemetry-case-select"><option value="case_a" selected>case_a</option></select>
    <span id="telemetry-error-banner"></span>
    <div id="telemetry-convergence-pill"><span class="pill-text"></span></div>
    ${SURFACE_BODY}
  `);
  const calls = [];
  app._window.console.error = () => {};
  app._window.fetch = async (url) => {
    calls.push(String(url));
    if (String(url).includes('/api/telemetry/surface')) {
      return {
        ok: true,
        json: async () => ({
          has_data: true, ok: true, verdict: 'good', verdict_label: 'Good', closed: true,
          stats: {
            triangles: 7, illegal_triangles: 0, self_intersection_checked: true,
            self_intersecting: false, unconnected_parts: 1,
          },
          issues: [], warnings: [], note: 'ok',
        }),
      };
    }
    return { ok: true, json: async () => ({ has_data: false }) };
  };
  await app.pollTelemetry();
  assert.ok(calls.some((u) => u.includes('/api/telemetry/surface')));
  assert.equal(
    app._window.document.getElementById('surface-triangles').textContent,
    (7).toLocaleString(),
  );
});

test('updateDomainBoxVisualization refreshes the auto-size preview from the payload', async () => {
  const app = await makeApp('<span id="cfg-layer-preview"></span><span id="cfg-auto-size-preview"></span>');
  let updated = false;
  app.viewer = { updateDomainBox() { updated = true; } };
  app.currentSTLBounds = { min: [0, 0, 0], max: [1, 1, 1] };
  app._window.fetch = async () => ({
    ok: true,
    json: async () => ({
      domain_box: { min: [-1, -1, -1], max: [2, 2, 2] },
      layer_preview: {
        first_layer_thickness: null,
        mode: 'relative',
        auto_size: {
          small_feature_m: 0.0095,
          finest_surface_cell_m: 0.004,
          surface_level: [4, 6],
          feature_percentile: 5,
          capped: false,
        },
      },
    }),
  });
  await app.updateDomainBoxVisualization(true);
  assert.equal(updated, true);
  const auto = app._window.document.getElementById('cfg-auto-size-preview').textContent;
  assert.ok(auto.includes('9.50 mm'), auto);
  assert.ok(auto.includes('level 6'), auto);
});
