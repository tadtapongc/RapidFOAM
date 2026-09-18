/**
 * Aero2DView — side-view (flow–vertical plane) free-body diagram of the car.
 *
 * Draws the projected STL silhouette, the CofR marker, horizontal drag and
 * vertical downforce arrows, and a curved pitching-moment arc.
 *
 * Performance: the silhouette (the expensive part — a fill of every projected
 * triangle) is rendered once into an offscreen canvas and cached; each update
 * only blits it and redraws the cheap arrows on top.
 */

class Aero2DView {
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    this.ctx = this.canvas ? this.canvas.getContext('2d') : null;
    this.positions = null; // Float32Array of x,y,z triangle vertices
    this.data = null;      // { force, moment, cofr, dragVec, dfVec }
    this.scale = 1;
    this.contentScale = 0.8; // overall shrink of everything drawn in the box
    this.show = { model: true, flow: true, drag: true, downforce: true, pitch: true, cop: true };
    this._sil = null;      // cached offscreen silhouette
    this._silFor = null;
    this._silW = 0;
    this._silH = 0;
    this._silFlow = -1;
    this._silUp = -1;
  }

  setShow(show) {
    this.show = { ...this.show, ...(show || {}) };
    this.draw();
  }

  setGeometry(positions) {
    this.positions = positions || null;
    this._sil = null; // invalidate
    this.draw();
  }

  setData(data) {
    this.data = data || null;
    this.draw();
  }

  setScale(scale) {
    this.scale = scale > 0 ? scale : 1;
    this.draw();
  }

  clear() {
    this.data = null;
    this.draw();
  }

  resize() {
    this.draw();
  }

  _axes() {
    const dragVec = (this.data && this.data.dragVec) || [0, 0, -1];
    const dfVec = (this.data && this.data.dfVec) || [0, -1, 0];
    const flowIdx = Math.max(0, dragVec.findIndex((v) => v !== 0));
    const upIdx = Math.max(0, dfVec.findIndex((v) => v !== 0));
    const latIdx = [0, 1, 2].find((i) => i !== flowIdx && i !== upIdx);
    const pitchAxis = [
      dfVec[1] * dragVec[2] - dfVec[2] * dragVec[1],
      dfVec[2] * dragVec[0] - dfVec[0] * dragVec[2],
      dfVec[0] * dragVec[1] - dfVec[1] * dragVec[0],
    ];
    const pitchSign = Math.sign(pitchAxis[latIdx] || 1) || 1;
    return { flowIdx, upIdx, latIdx, pitchSign, dragVec };
  }

  _buildSilhouette(cssW, cssH, positions, flowIdx, upIdx) {
    let uMin = Infinity; let uMax = -Infinity; let vMin = Infinity; let vMax = -Infinity;
    for (let i = 0; i < positions.length; i += 3) {
      const u = positions[i + flowIdx];
      const v = positions[i + upIdx];
      if (u < uMin) uMin = u;
      if (u > uMax) uMax = u;
      if (v < vMin) vMin = v;
      if (v > vMax) vMax = v;
    }
    // Internal margin around the car (fraction of the smaller viewport side).
    const pad = Math.round(Math.min(cssW, cssH) * (0.26 / this.contentScale));
    const s = Math.min(
      (cssW - 2 * pad) / Math.max(uMax - uMin, 1e-6),
      (cssH - 2 * pad) / Math.max(vMax - vMin, 1e-6),
    );
    const offU = pad + ((cssW - 2 * pad) - (uMax - uMin) * s) / 2;
    const offV = pad + ((cssH - 2 * pad) - (vMax - vMin) * s) / 2;
    const toX = (u) => offU + (u - uMin) * s;
    const toY = (v) => cssH - offV - (v - vMin) * s;

    const dpr = window.devicePixelRatio || 1;
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    const g = canvas.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);

    // Fill each projected triangle as its own primitive. A single Path2D of a
    // closed surface cancels (front/back faces wind oppositely); per-triangle
    // fills union correctly into a solid silhouette. Done once, then cached.
    g.fillStyle = '#6b7a94';
    for (let i = 0; i + 8 < positions.length; i += 9) {
      const x0 = toX(positions[i + flowIdx]);
      const y0 = toY(positions[i + upIdx]);
      const x1 = toX(positions[i + 3 + flowIdx]);
      const y1 = toY(positions[i + 3 + upIdx]);
      const x2 = toX(positions[i + 6 + flowIdx]);
      const y2 = toY(positions[i + 6 + upIdx]);
      if ((x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0) === 0) continue;
      g.beginPath();
      g.moveTo(x0, y0);
      g.lineTo(x1, y1);
      g.lineTo(x2, y2);
      g.closePath();
      g.fill();
    }

    return { canvas, toX, toY, vMin, dpr };
  }

  _arrow(ctx, x0, y0, x1, y1, color, width) {
    const dx = x1 - x0;
    const dy = y1 - y0;
    const len = Math.hypot(dx, dy);
    if (len < 2) return;
    const ux = dx / len;
    const uy = dy / len;
    const head = Math.min(12, len * 0.45);
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = width || 2;
    ctx.beginPath();
    ctx.moveTo(x0, y0);
    ctx.lineTo(x1 - ux * head * 0.7, y1 - uy * head * 0.7);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x1 - ux * head - uy * head * 0.5, y1 - uy * head + ux * head * 0.5);
    ctx.lineTo(x1 - ux * head + uy * head * 0.5, y1 - uy * head - ux * head * 0.5);
    ctx.closePath();
    ctx.fill();
  }

  _arcArrow(ctx, cx, cy, radius, clockwise, color) {
    const start = -Math.PI * 0.6;
    const end = clockwise ? start + Math.PI * 1.7 : start - Math.PI * 1.7;
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 2.2;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, start, end, !clockwise);
    ctx.stroke();

    const tangent = end + (clockwise ? Math.PI / 2 : -Math.PI / 2);
    const hx = Math.cos(tangent);
    const hy = Math.sin(tangent);
    const ax = cx + radius * Math.cos(end);
    const ay = cy + radius * Math.sin(end);
    const size = 9;
    ctx.beginPath();
    ctx.moveTo(ax + hx * size, ay + hy * size);
    ctx.lineTo(ax - hy * size * 0.6, ay + hx * size * 0.6);
    ctx.lineTo(ax + hy * size * 0.6, ay - hx * size * 0.6);
    ctx.closePath();
    ctx.fill();
  }

  _label(ctx, x, y, text, color) {
    const fontSize = Math.max(9, Math.round(12 * this.contentScale));
    ctx.font = `600 ${fontSize}px Inter, sans-serif`;
    const width = ctx.measureText(text).width;
    const boxHeight = fontSize + 6;
    const cssW = ctx.canvas.clientWidth;
    const cssH = ctx.canvas.clientHeight;
    const bx = Math.min(Math.max(4, x - 4), Math.max(4, cssW - (width + 12)));
    const by = Math.min(Math.max(boxHeight + 2, y), cssH - 2);
    ctx.fillStyle = 'rgba(10, 12, 18, 0.85)';
    ctx.fillRect(bx, by - boxHeight, width + 8, boxHeight);
    ctx.fillStyle = color;
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'left';
    ctx.fillText(text, bx + 4, by - boxHeight / 2);
  }

  draw() {
    const { ctx, canvas } = this;
    if (!ctx || !canvas) return;
    const cssW = canvas.clientWidth;
    const cssH = canvas.clientHeight;
    if (cssW <= 0 || cssH <= 0) return;

    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(cssW * dpr) || canvas.height !== Math.round(cssH * dpr)) {
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const positions = this.positions;
    if (!positions || positions.length < 9) return;

    const { flowIdx, upIdx, latIdx, pitchSign, dragVec } = this._axes();

    // Rebuild the (expensive) silhouette only when geometry or viewport size changes.
    if (
      !this._sil || this._silFor !== positions ||
      this._silW !== cssW || this._silH !== cssH ||
      this._silFlow !== flowIdx || this._silUp !== upIdx
    ) {
      this._sil = this._buildSilhouette(cssW, cssH, positions, flowIdx, upIdx);
      this._silFor = positions;
      this._silW = cssW;
      this._silH = cssH;
      this._silFlow = flowIdx;
      this._silUp = upIdx;
    }
    const { canvas: silCanvas, toX, toY, vMin } = this._sil;
    if (this.show.model) ctx.drawImage(silCanvas, 0, 0, cssW, cssH);

    // Ground reference: configured ground plane, else clearance below the
    // model's lowest point, else the model bottom.
    const payload = this.data || {};
    let groundV = vMin;
    if (payload.groundPlane !== null && payload.groundPlane !== undefined) {
      groundV = payload.groundPlane;
    } else if (payload.groundClearance) {
      groundV = vMin - payload.groundClearance;
    }
    ctx.strokeStyle = 'rgba(100, 116, 139, 0.55)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, toY(groundV));
    ctx.lineTo(cssW, toY(groundV));
    ctx.stroke();

    // Center of pressure: vertical dashed line + ground marker.
    const balance = payload.balance;
    if (this.show.cop && balance && balance.available && balance.cop) {
      const xCop = Math.min(Math.max(toX(balance.cop[flowIdx]), 6), cssW - 6);
      const yGround = toY(groundV);
      ctx.save();
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = 'rgba(245, 158, 11, 0.9)';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(xCop, 8);
      ctx.lineTo(xCop, yGround);
      ctx.stroke();
      ctx.restore();
      ctx.fillStyle = '#f59e0b';
      ctx.beginPath();
      ctx.moveTo(xCop, yGround);
      ctx.lineTo(xCop - 5, yGround + 8);
      ctx.lineTo(xCop + 5, yGround + 8);
      ctx.closePath();
      ctx.fill();
      this._label(ctx, xCop + 6, Math.max(26, yGround - 6),
        `CoP ${balance.cop_pct_wheelbase}% WB`, '#f59e0b');
    }

    // Freestream flow direction indicator (air travel direction).
    if (this.show.flow) {
      const flowSign = Math.sign(dragVec[flowIdx] || -1) || -1;
      const flowY = 26;
      const flowLen = 64 * this.contentScale;
      const flowStartX = flowSign > 0 ? 28 : cssW - 28;
      const flowEndX = flowStartX + flowSign * flowLen;
      this._arrow(ctx, flowStartX, flowY, flowEndX, flowY, 'rgba(56, 189, 248, 0.85)', 2);
      // Label below the arrow so it never clips at the top edge.
      this._label(ctx, Math.max(6, Math.min(flowStartX, flowEndX)), flowY + 22,
        'flow', 'rgba(56, 189, 248, 0.95)');
    }

    if (!this.data || !this.data.force || !this.data.cofr) return;
    const F = this.data.force;
    const M = this.data.moment;
    const cofr = this.data.cofr;
    const cx = toX(cofr[flowIdx]);
    const cy = toY(cofr[upIdx]);

    // Arrow scale anchored to the viewport, then clamped so nothing runs off.
    const margin = 18;
    const Fflow = F[flowIdx];
    const Fup = F[upIdx];
    const maxComp = Math.max(Math.abs(Fflow), Math.abs(Fup), 1e-6);
    const unitPx = (0.3 * Math.min(cssW, cssH) * this.scale * this.contentScale) / maxComp;

    const clampX = (x) => Math.min(Math.max(x, 4), cssW - 92);
    const clampY = (y) => Math.min(Math.max(y, 16), cssH - 6);

    ctx.fillStyle = '#38bdf8';
    ctx.beginPath();
    ctx.arc(cx, cy, 3.5 * this.contentScale, 0, Math.PI * 2);
    ctx.fill();

    const dragAvail = Fflow >= 0 ? (cssW - margin - cx) : (cx - margin);
    const dragLen = Math.min(Math.abs(Fflow) * unitPx, Math.max(0, dragAvail));
    if (this.show.drag && dragLen > 2) {
      const ex = cx + Math.sign(Fflow) * dragLen;
      this._arrow(ctx, cx, cy, ex, cy, '#f43f5e', 2.4);
      this._label(ctx, clampX(Math.min(cx, ex)), clampY(cy - 10),
        `Drag ${Math.abs(Fflow).toFixed(0)} N`, '#f43f5e');
    }

    const dfAvail = Fup >= 0 ? (cy - margin) : (cssH - margin - cy);
    const dfLen = Math.min(Math.abs(Fup) * unitPx, Math.max(0, dfAvail));
    if (this.show.downforce && dfLen > 2) {
      const ey = cy - Math.sign(Fup) * dfLen;
      this._arrow(ctx, cx, cy, cx, ey, '#00d2ff', 2.4);
      this._label(ctx, clampX(cx + 8), clampY(Math.min(cy, ey)),
        `Downforce ${Math.abs(Fup).toFixed(0)} N`, '#00d2ff');
    }

    if (this.show.pitch && M) {
      const pitchValue = M[latIdx] * pitchSign;
      const maxRadius = Math.min(cx, cssW - cx, cy, cssH - cy) - margin;
      const radius = Math.max(14, Math.min(0.16 * cssH * this.contentScale, maxRadius));
      this._arcArrow(ctx, cx, cy, radius, pitchValue > 0, '#a855f7');
      this._label(ctx, clampX(cx + radius + 6), clampY(cy - radius),
        `Pitch ${pitchValue.toFixed(0)} N·m`, '#a855f7');
    }
  }
}

window.Aero2DView = Aero2DView;
