/**
 * AeroVectorLayer — draws aerodynamic force/moment vectors on a Three.js
 * scene (an STLViewer instance) anchored at the configured center of
 * rotation (CofR). Used by the Telemetry tab 3D view (Phase A).
 */

class AeroVectorLayer {
  constructor(viewer) {
    this.viewer = viewer;
    this.group = new THREE.Group();
    this.group.name = 'aero-vectors';
    viewer.scene.add(this.group);

    this.colors = {
      force: 0x38bdf8,
      moment: 0xa855f7,
      drag: 0xf43f5e,
      downforce: 0x00d2ff,
      side: 0x10b981,
    };
  }

  clear() {
    this.group.traverse((node) => {
      if (node === this.group) return;
      if (node.geometry) node.geometry.dispose();
      if (node.material) {
        const materials = Array.isArray(node.material) ? node.material : [node.material];
        materials.forEach((mat) => {
          if (mat.map) mat.map.dispose();
          mat.dispose();
        });
      }
    });
    while (this.group.children.length > 0) {
      this.group.remove(this.group.children[0]);
    }
  }

  static axisVector(axis) {
    const s = String(axis || '').trim().toLowerCase();
    const sign = s.startsWith('-') ? -1 : 1;
    const name = s.replace(/[^xyz]/g, '') || 'z';
    const v = [0, 0, 0];
    v['xyz'.indexOf(name)] = sign;
    return v;
  }

  _label(text, colorCss, size) {
    const canvas = document.createElement('canvas');
    canvas.width = 256;
    canvas.height = 64;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = 'rgba(10, 12, 18, 0.85)';
    ctx.fillRect(0, 0, 256, 64);
    ctx.strokeStyle = colorCss;
    ctx.lineWidth = 3;
    ctx.strokeRect(1.5, 1.5, 253, 61);
    ctx.font = 'bold 30px Inter, sans-serif';
    ctx.fillStyle = colorCss;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, 128, 33);

    const texture = new THREE.CanvasTexture(canvas);
    texture.minFilter = THREE.LinearFilter;
    const material = new THREE.SpriteMaterial({
      map: texture, transparent: true, depthTest: true, depthWrite: false,
    });
    const sprite = new THREE.Sprite(material);
    const width = 1.15 * size;
    sprite.scale.set(width, width * 0.25, 1);
    return sprite;
  }

  _addArrow(origin, dir, length, color, size, labelText, labelColor) {
    if (!isFinite(length) || length <= 1e-6 || dir.lengthSq() < 1e-12) return;
    const direction = dir.clone().normalize();
    const headLength = Math.min(length * 0.32, 0.16 * size);
    const headWidth = Math.min(length * 0.16, 0.08 * size);
    const arrow = new THREE.ArrowHelper(direction, origin, length, color, headLength, headWidth);
    this.group.add(arrow);

    if (labelText) {
      const sprite = this._label(labelText, labelColor || '#e2e8f0', size);
      sprite.position.copy(origin).add(direction.clone().multiplyScalar(length + 0.12 * size));
      this.group.add(sprite);
    }
  }

  _addMarker(origin, size) {
    const radius = 0.018 * size;
    const sphere = new THREE.Mesh(
      new THREE.SphereGeometry(radius, 16, 16),
      new THREE.MeshBasicMaterial({ color: 0x38bdf8 }),
    );
    sphere.position.copy(origin);
    this.group.add(sphere);
  }

  /**
   * update({ force, moment, cofr, dragVec, dfVec, modelSize, scale, show })
   *  force/moment: [x, y, z] in raw CAD axes; cofr: [x, y, z] metres.
   */
  update(data) {
    this.clear();
    const { force, moment, cofr, dragVec, dfVec } = data;
    if (!force || !cofr) return;

    const size = data.modelSize && data.modelSize > 0 ? data.modelSize : 1;
    const scale = data.scale && data.scale > 0 ? data.scale : 1;
    const show = data.show || {};
    const origin = new THREE.Vector3(cofr[0], cofr[1], cofr[2]);
    this._addMarker(origin, size);

    const d = new THREE.Vector3(...(dragVec || [0, 0, -1])).normalize();
    const lift = new THREE.Vector3(...(dfVec || [0, -1, 0])).normalize();
    const side = new THREE.Vector3().crossVectors(lift, d);

    const F = new THREE.Vector3(force[0], force[1], force[2]);
    const dragC = F.dot(d);
    const dfC = F.dot(lift);
    const sideC = F.dot(side);

    const forceRef = Math.max(Math.abs(F.length()), Math.abs(dragC), Math.abs(dfC), Math.abs(sideC), 1e-6);
    const forceUnit = (0.45 * size * scale) / forceRef;

    if (show.force) {
      const mag = F.length();
      this._addArrow(origin, F.clone(), mag * forceUnit, this.colors.force, size,
        `F ${mag.toFixed(0)} N`, '#38bdf8');
    }

    if (show.components) {
      const component = (vec, value, color, label, cssColor) => {
        const dir = value >= 0 ? vec.clone() : vec.clone().negate();
        this._addArrow(origin, dir, Math.abs(value) * forceUnit, color, size,
          `${label} ${value.toFixed(0)} N`, cssColor);
      };
      component(d, dragC, this.colors.drag, 'Drag', '#f43f5e');
      component(lift, dfC, this.colors.downforce, 'DF', '#00d2ff');
      component(side, sideC, this.colors.side, 'Side', '#10b981');
    }

    if (show.moment && moment) {
      const M = new THREE.Vector3(moment[0], moment[1], moment[2]);
      const pitchC = M.dot(side);
      const rollC = M.dot(d);
      const yawC = M.dot(lift);
      const momentRef = Math.max(Math.abs(M.length()), Math.abs(pitchC), Math.abs(rollC), Math.abs(yawC), 1e-6);
      const momentUnit = (0.45 * size * scale) / momentRef;
      const mag = M.length();
      this._addArrow(origin, M.clone(), mag * momentUnit, this.colors.moment, size,
        `M ${mag.toFixed(0)} N·m`, '#a855f7');
    }
  }
}

window.AeroVectorLayer = AeroVectorLayer;
