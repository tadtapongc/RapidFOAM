/**
 * Minimal JSDOM harness for the RapidFOAM front-end.
 *
 * The browser sources are plain scripts that attach their classes to `window`
 * (e.g. `window.CFDApp`, `window.STLViewer`). To unit-test them we load the
 * files into a JSDOM window and expose the constructors.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { JSDOM } from 'jsdom';

const here = dirname(fileURLToPath(import.meta.url));
export const JS_DIR = resolve(here, '../../src/rapidfoam/web/static/js');

export function readSource(name) {
  return readFileSync(resolve(JS_DIR, name), 'utf8');
}

/** Create a JSDOM window with the DOM nodes the tested methods expect. */
export function createDom(bodyHtml = '') {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>${bodyHtml}</body></html>`, {
    // Do not enable pretendToBeVisual: its permanent requestAnimationFrame
    // loop keeps the Node process alive after the tests finish.
    runScripts: 'outside-only',
  });
  return dom;
}

/**
 * Resolve once the JSDOM document has finished loading. Evaluating app.js
 * before this point would let its `DOMContentLoaded` handler run
 * `new CFDApp()` (network I/O) during the tests; after load, that listener is
 * registered too late to ever fire.
 */
export function whenReady(dom) {
  const { window } = dom;
  if (window.document.readyState === 'complete') return Promise.resolve(window);
  return new Promise((resolve) => {
    window.addEventListener('load', () => resolve(window), { once: true });
  });
}

/**
 * Evaluate a browser source file inside a JSDOM window.
 *
 * `window.eval` runs the script in the window's global scope so `window.X = ...`
 * assignments take effect. Top-level `class Foo {}` declarations create lexical
 * bindings that do not become window properties, so after evaluating we publish
 * any known class names explicitly for the tests to reach.
 */
export function loadScript(dom, name, exportNames = []) {
  const { window } = dom;
  const exporter = exportNames
    .map((n) => `try { window.${n} = ${n}; } catch (e) { /* not defined */ }`)
    .join('\n');
  window.eval(`${readSource(name)}\n${exporter}`);
  return window;
}

/** Load the set of scripts needed by the tests (after the DOM has loaded). */
export async function loadAppScripts(dom) {
  await whenReady(dom);
  loadScript(dom, 'charts.js', ['TelemetryCharts']);
  loadScript(dom, 'viewer.js', ['STLViewer']);
  loadScript(dom, 'app.js', ['CFDApp']);
  return dom.window;
}

/**
 * Build a CFDApp instance without running its constructor (which performs
 * network I/O and constructs the 3D viewer). We copy the constructor's
 * state fields and then let each test add only what it needs.
 */
export function createApp(window) {
  const CFDApp = window.CFDApp;
  if (!CFDApp) throw new Error('CFDApp not loaded');
  const app = Object.create(CFDApp.prototype);
  Object.assign(app, {
    viewer: null,
    charts: null,
    clusterConnected: false,
    pollInterval: null,
    telemetryPollingActive: true,
    telemetryRefOverrides: {},
    balanceOverrides: {},
    telemetryRequestId: 0,
    telemetryInFlight: false,
    telemetryViewer: null,
    telemetryLayer: null,
    telemetry3dCase: null,
    telemetry3dModelSize: 1,
    _telemetry3dPayload: null,
    _telemetry3dStlFiles: [],
    telemetry3dExpanded: false,
    telemetry2dView: null,
    telemetry2dCase: null,
    _telemetry2dStlFiles: [],
    _telemetry2dGeomCache: new Map(),
    archiveCases: [],
    currentArchiveFilter: 'all',
    archiveSearchTerm: '',
    isSyncingFromJson: false,
    currentSTLName: null,
    downloadStates: new Map(),
    downloadPollTimer: null,
    fidelityPresets: null,
    layerPreviewTimer: null,
    activeConfig: { case_name: 'my_case', stl_files: [] },
  });
  return app;
}

/** Minimal THREE stub sufficient for viewer geometry bookkeeping. */
export function installThreeStub(window) {
  class Vector3 {
    constructor(x = 0, y = 0, z = 0) { this.x = x; this.y = y; this.z = z; }
    clone() { return new Vector3(this.x, this.y, this.z); }
    copy(v) { this.x = v.x; this.y = v.y; this.z = v.z; return this; }
    set(x, y, z) { this.x = x; this.y = y; this.z = z; return this; }
    sub(v) { this.x -= v.x; this.y -= v.y; this.z -= v.z; return this; }
    add(v) { this.x += v.x; this.y += v.y; this.z += v.z; return this; }
    addVectors(a, b) { this.x = a.x + b.x; this.y = a.y + b.y; this.z = a.z + b.z; return this; }
    subVectors(a, b) { this.x = a.x - b.x; this.y = a.y - b.y; this.z = a.z - b.z; return this; }
    multiplyScalar(s) { this.x *= s; this.y *= s; this.z *= s; return this; }
    normalize() { const l = Math.hypot(this.x, this.y, this.z) || 1; this.x /= l; this.y /= l; this.z /= l; return this; }
    length() { return Math.hypot(this.x, this.y, this.z); }
  }

  const THREE = {
    Vector3,
    PerspectiveCamera: class {
      constructor(fov, aspect, near, far) {
        this.fov = fov; this.aspect = aspect; this.near = near; this.far = far;
        this.position = new Vector3(); this.up = new Vector3(0, 1, 0);
      }
      lookAt() {}
      updateProjectionMatrix() { this._updated = true; }
    },
    Scene: class {
      constructor() { this.children = []; }
      add(o) { this.children.push(o); }
      remove(o) { const i = this.children.indexOf(o); if (i >= 0) this.children.splice(i, 1); }
    },
  };
  window.THREE = THREE;
  return THREE;
}

/** A tiny Chart.js stub with the surface TelemetryCharts touches. */
export function installChartStub(window) {
  const makeChart = () => ({
    data: { labels: [], datasets: [] },
    update() {},
    resize() {},
    destroy() {},
  });
  window.Chart = function Chart() { return makeChart(); };
  window.Chart.defaults = { color: '', font: {} };
}
