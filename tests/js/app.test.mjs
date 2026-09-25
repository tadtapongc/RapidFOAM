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

test('pollTelemetry throws on a non-ok forces response instead of rendering empty data', async () => {
  const app = await makeApp(`
    <select id="telemetry-case-select"><option value="case_a" selected>case_a</option></select>
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
});
