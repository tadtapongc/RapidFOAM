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

// ------------------------------------------------- Studio mesh-quality panel

const MESH_BODY = `
  <span id="mesh-quality-badge"></span>
  <span id="mesh-cells"></span>
  <span id="mesh-nonortho"></span>
  <span id="mesh-skewness"></span>
  <span id="mesh-aspect"></span>
  <table><tbody id="mesh-layer-tbody"></tbody></table>
  <p id="mesh-quality-note"></p>
`;

test('renderMeshQuality shows metrics, coverage and an OK badge', async () => {
  const app = await makeApp(MESH_BODY);
  app.renderMeshQuality({
    ok: true,
    stats: { cells: 9008844, max_non_ortho: 45.2, max_skewness: 1.2, max_aspect_ratio: 12.5 },
    layers: { geometry: { layers: 2, coverage: 1.0 } },
    target_layers: 2,
    issues: [],
    note: 'mesh quality OK',
  });
  const doc = app._window.document;
  assert.equal(doc.getElementById('mesh-cells').textContent, (9008844).toLocaleString());
  assert.equal(doc.getElementById('mesh-nonortho').textContent, '45.20');
  const badge = doc.getElementById('mesh-quality-badge');
  assert.ok(badge.className.includes('mesh-quality-badge-ok'));
  assert.equal(badge.textContent, 'Mesh OK');
  const rows = doc.getElementById('mesh-layer-tbody').innerHTML;
  assert.ok(rows.includes('geometry'));
  assert.ok(rows.includes('2/2'));
  assert.ok(rows.includes('100%'));
});

test('renderMeshQuality flags concerns and escapes the patch name', async () => {
  const app = await makeApp(MESH_BODY);
  app.renderMeshQuality({
    ok: false,
    stats: { cells: 10, max_non_ortho: 68.3 },
    layers: { 'a<b>': { layers: 1, coverage: 0.5 } },
    target_layers: 2,
    issues: ['max non-orthogonality 68.3 > limit 65', 'boundary-layer dropout'],
    note: '2 concerns',
  });
  const doc = app._window.document;
  const badge = doc.getElementById('mesh-quality-badge');
  assert.ok(badge.className.includes('mesh-quality-badge-warn'));
  assert.equal(badge.textContent, '2 concerns');
  const rows = doc.getElementById('mesh-layer-tbody').innerHTML;
  assert.ok(!rows.includes('<b>'), 'raw tag must not survive');
  assert.ok(rows.includes('&lt;b&gt;'));
  assert.ok(rows.includes('50%'));
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
