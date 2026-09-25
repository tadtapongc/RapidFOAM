import { test } from 'node:test';
import assert from 'node:assert/strict';

import { createDom, loadScript } from './harness.mjs';

/** Build a bare STLViewer instance without running the WebGL-heavy init(). */
function makeViewer(extraThree = {}) {
  const dom = createDom('<div id="viewer"></div><div id="scale-warning"></div>');
  const window = loadScript(dom, 'viewer.js');

  function makeDisposable(tag) {
    return {
      tag,
      geometry: { dispose() { this.disposed = true; } },
      material: { dispose() { this.disposed = true; } },
    };
  }

  class ArrowHelperStub {
    constructor() {
      this.line = makeDisposable('line');
      this.cone = makeDisposable('cone');
      this.visible = true;
      this.children = [this.line, this.cone];
    }
    traverse(fn) { this.children.forEach(fn); }
  }

  const THREE = {
    ...extraThree,
    Vector3: class {
      constructor(x = 0, y = 0, z = 0) { this.x = x; this.y = y; this.z = z; }
      clone() { return new THREE.Vector3(this.x, this.y, this.z); }
      copy(v) { this.x = v.x; this.y = v.y; this.z = v.z; return this; }
      set(x, y, z) { this.x = x; this.y = y; this.z = z; return this; }
      normalize() { return this; }
      length() { return Math.hypot(this.x, this.y, this.z); }
      multiplyScalar() { return this; }
    },
    ArrowHelper: ArrowHelperStub,
  };
  window.THREE = THREE;

  const viewer = Object.create(window.STLViewer.prototype);
  Object.assign(viewer, {
    container: null,
    scene: { children: [], add(o) { this.children.push(o); }, remove(o) { const i = this.children.indexOf(o); if (i >= 0) this.children.splice(i, 1); } },
    camera: null,
    controls: null,
    flowArrow: null,
    domainBoxGroup: null,
    domainMin: null,
    domainMax: null,
    symPlaneCoord: null,
    flowDirection: '-z',
    showFlow: true,
    showDomain: true,
  });
  viewer._window = window;
  return viewer;
}

// ---------------------------------------------------------------- Bug #6

function vec(viewer, x = 0, y = 0, z = -1) {
  return new viewer._window.THREE.Vector3(x, y, z);
}

test('updateFlowArrow disposes the previous arrow before replacing it', () => {
  const viewer = makeViewer();
  viewer.updateFlowArrow(vec(viewer), vec(viewer), 1);
  const first = viewer.flowArrow;
  viewer.updateFlowArrow(vec(viewer), vec(viewer), 1);
  assert.notEqual(viewer.flowArrow, first);
  assert.equal(first.line.geometry.disposed, true);
  assert.equal(first.line.material.disposed, true);
  assert.equal(first.cone.geometry.disposed, true);
  assert.equal(first.cone.material.disposed, true);
});

test('updateFlowArrow removes the old arrow from the scene', () => {
  const viewer = makeViewer();
  viewer.updateFlowArrow(vec(viewer), vec(viewer), 1);
  const first = viewer.flowArrow;
  viewer.updateFlowArrow(vec(viewer), vec(viewer), 1);
  assert.ok(!viewer.scene.children.includes(first));
});

// ---------------------------------------------------------------- Bug #10

test('updateDomainBox(null, null) clears stale domain state', () => {
  const viewer = makeViewer();
  viewer.domainMin = [-1, -1, -1];
  viewer.domainMax = [1, 1, 1];
  viewer.symPlaneCoord = 0.0;
  viewer.updateDomainBox(null, null);
  assert.equal(viewer.domainMin, null);
  assert.equal(viewer.domainMax, null);
  assert.equal(viewer.symPlaneCoord, null);
});

// ---------------------------------------------------------------- Bug #2

test('recomputeOverallBoundingBox scales far plane and maxDistance for large models', () => {
  const dom = createDom('<div id="viewer"></div>');
  const window = loadScript(dom, 'viewer.js');

  class Box3Stub {
    constructor() {
      this.min = { x: 0, y: 0, z: 0 };
      this.max = { x: 0, y: 0, z: 0 };
    }
    union(other) { this.min = other.min; this.max = other.max; return this; }
    getSize(target) {
      target.x = this.max.x - this.min.x;
      target.y = this.max.y - this.min.y;
      target.z = this.max.z - this.min.z;
      return target;
    }
  }

  const THREE = {
    Box3: Box3Stub,
    Box3Helper: class { constructor() { this.visible = true; } },
    Vector3: class {
      constructor(x = 0, y = 0, z = 0) { this.x = x; this.y = y; this.z = z; }
      set(x, y, z) { this.x = x; this.y = y; this.z = z; return this; }
      length() { return Math.hypot(this.x, this.y, this.z); }
    },
  };
  window.THREE = THREE;

  const viewer = Object.create(window.STLViewer.prototype);
  Object.assign(viewer, {
    scene: { children: [], add() {}, remove() {} },
    camera: { far: 2000, updateProjectionMatrix() { this.updated = true; } },
    controls: { maxDistance: 1500 },
    stlMeshes: new Map(),
    bboxHelper: null,
    showBounds: true,
    domainMin: null,
    groundGrid: null,
  });

  // A 650 mm model (maxDim > 20 triggers the mm warning) extends far beyond the
  // default 2000 far plane / 1500 maxDistance framing distance.
  const geometry = {
    boundingBox: new Box3Stub(),
    computeBoundingBox() {},
  };
  geometry.boundingBox.min = { x: 0, y: 0, z: 0 };
  geometry.boundingBox.max = { x: 650, y: 300, z: 200 };
  viewer.stlMeshes.set('big.stl', { geometry });

  const info = viewer.recomputeOverallBoundingBox();
  assert.equal(info.isLikelyMM, true);
  assert.ok(viewer.camera.far > 2000, `far should grow, got ${viewer.camera.far}`);
  assert.ok(viewer.controls.maxDistance > 1500, `maxDistance should grow, got ${viewer.controls.maxDistance}`);
  assert.equal(viewer.camera.updated, true);
});
