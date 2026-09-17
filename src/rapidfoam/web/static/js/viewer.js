/**
 * 3D Geometry & Wind Tunnel Inspector using Three.js
 */

const STL_PALETTE = [
  0x38bdf8, // Sky Cyan (Aero Primary)
  0x34d399, // Mint Emerald
  0xf472b6, // Rose Pink
  0xa78bfa, // Indigo Lavender
  0xfbbf24, // Amber Gold
  0x2dd4bf, // Marine Teal
  0xf87171, // Coral Red
  0x60a5fa, // Electric Blue
];

class STLViewer {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    if (!this.container) return;

    this.scene = null;
    this.camera = null;
    this.renderer = null;
    this.controls = null;
    this.stlGroup = null;
    this.stlMeshes = new Map(); // filename -> THREE.Mesh
    this.bboxHelper = null;
    this.domainBoxGroup = null;
    this.groundGrid = null;
    this.flowArrow = null;
    this.originAxesGroup = null;
    this.gizmoScene = null;
    this.gizmoCamera = null;

    this.showBounds = true;
    this.showDomain = true;
    this.showGround = true;
    this.showFlow = true;
    this.showAxes = true;

    this.currentFocusTarget = 'domain'; // 'domain' or 'model'
    this.domainMin = null;
    this.domainMax = null;
    this.flowDirection = '-z';
    this.symPlaneCoord = null;

    this.init();
  }

  get currentMesh() {
    // Backward compatibility: return first loaded mesh
    return this.stlMeshes.values().next().value || null;
  }

  init() {
    if (typeof THREE === 'undefined') {
      console.warn('Three.js not loaded. 3D viewer unavailable.');
      return;
    }

    const width = this.container.clientWidth || 600;
    const height = this.container.clientHeight || 480;

    // 1. Scene
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x080c14);

    this.stlGroup = new THREE.Group();
    this.scene.add(this.stlGroup);

    // 2. Camera (Wide view distance so 50m domain fits comfortably)
    this.camera = new THREE.PerspectiveCamera(45, width / height, 0.05, 2000);
    this.camera.position.set(15, 12, 28);

    // 3. Renderer
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setSize(width, height);
    this.renderer.setPixelRatio(window.devicePixelRatio);
    this.renderer.shadowMap.enabled = true;
    this.renderer.autoClear = false;

    // Clean container and attach
    const emptyMsg = document.getElementById('viewer-empty-msg');
    if (emptyMsg) emptyMsg.style.display = 'none';
    this.container.appendChild(this.renderer.domElement);

    // 4. Controls
    if (typeof THREE.OrbitControls !== 'undefined') {
      this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
      this.controls.enableDamping = true;
      this.controls.dampingFactor = 0.05;
      this.controls.maxDistance = 1500;
    }

    // 5. Lighting (Studio Aero lighting)
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.45);
    this.scene.add(ambientLight);

    const hemiLight = new THREE.HemisphereLight(0xffffff, 0x1e293b, 0.75);
    this.scene.add(hemiLight);

    const dirLight1 = new THREE.DirectionalLight(0x00d2ff, 0.65);
    dirLight1.position.set(20, 30, 25);
    this.scene.add(dirLight1);

    const dirLight2 = new THREE.DirectionalLight(0xffffff, 0.55);
    dirLight2.position.set(-20, 15, -25);
    this.scene.add(dirLight2);

    // 6. Ground Grid (Spans 60m to encompass complete wind tunnel)
    this.createGroundGrid();

    // 7. Flow Vector Arrow (default -Z)
    this.updateFlowArrow(new THREE.Vector3(0, 0, -1), new THREE.Vector3(0, 1.2, 8.0), 3.0);

    // 8. 3D Origin Axes Triad (at CAD origin 0,0,0)
    this.createOriginAxes(1.5);

    // 9. Corner Orientation Trihedron Gizmo (synchronous camera tracking)
    this.initGizmo();

    // Handle Resize (window and container observer)
    window.addEventListener('resize', () => this.onResize());
    if (window.ResizeObserver && this.container) {
      this.resizeObserver = new ResizeObserver(() => this.onResize());
      this.resizeObserver.observe(this.container);
    }

    // Animation Loop
    this.animate();
  }

  disposeObject(obj) {
    if (!obj) return;
    if (obj.geometry) {
      obj.geometry.dispose();
    }
    if (obj.material) {
      if (Array.isArray(obj.material)) {
        obj.material.forEach((m) => {
          if (m.map) m.map.dispose();
          m.dispose();
        });
      } else {
        if (obj.material.map) obj.material.map.dispose();
        obj.material.dispose();
      }
    }
  }

  disposeGroup(group) {
    if (!group) return;
    group.traverse((child) => {
      this.disposeObject(child);
    });
    while (group.children && group.children.length > 0) {
      group.remove(group.children[0]);
    }
  }

  createGroundGrid() {
    if (this.groundGrid) this.scene.remove(this.groundGrid);
    this.groundGrid = new THREE.GridHelper(60, 60, 0x00d2ff, 0x1e293b);
    this.groundGrid.position.set(0, 0, 0);
    this.groundGrid.visible = this.showGround;
    this.scene.add(this.groundGrid);
  }

  createCanvasTextSprite(text, color = '#00f0ff', bgColor = 'rgba(15, 23, 42, 0.88)') {
    const canvas = document.createElement('canvas');
    canvas.width = 320;
    canvas.height = 80;
    const ctx = canvas.getContext('2d');

    // Rounded background container
    ctx.fillStyle = bgColor;
    ctx.beginPath();
    if (ctx.roundRect) {
      ctx.roundRect(4, 4, 312, 72, 12);
    } else {
      ctx.rect(4, 4, 312, 72);
    }
    ctx.fill();

    // Border
    ctx.strokeStyle = color;
    ctx.lineWidth = 4;
    ctx.stroke();

    // Typography
    ctx.font = 'bold 24px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    ctx.fillStyle = color;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, 160, 40);

    const texture = new THREE.CanvasTexture(canvas);
    texture.minFilter = THREE.LinearFilter;
    const spriteMat = new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: true, depthWrite: false });
    const sprite = new THREE.Sprite(spriteMat);
    sprite.scale.set(3.8, 0.95, 1.0);
    return sprite;
  }

  parseFlowDirectionVector(dirStr = '-z') {
    const s = (dirStr || '-z').trim().toLowerCase();
    const sign = s.startsWith('-') ? -1 : 1;
    const axis = s.replace(/[^xyz]/g, '') || 'z';
    return new THREE.Vector3(
      axis === 'x' ? sign : 0,
      axis === 'y' ? sign : 0,
      axis === 'z' ? sign : 0
    );
  }

  updateFlowArrow(direction = new THREE.Vector3(0, 0, -1), origin = new THREE.Vector3(0, 1.2, 8.0), length = 3.0) {
    if (this.flowArrow) this.scene.remove(this.flowArrow);
    const color = 0x00e5ff;
    const dirNorm = direction.clone().normalize();
    this.flowArrow = new THREE.ArrowHelper(dirNorm, origin, length, color, length * 0.25, length * 0.15);
    this.flowArrow.visible = this.showFlow;
    this.scene.add(this.flowArrow);
  }

  addSTLFromArrayBuffer(buffer, filename = 'geometry.stl') {
    if (typeof THREE.STLLoader === 'undefined') {
      console.error('STLLoader not available.');
      return null;
    }

    const loader = new THREE.STLLoader();
    try {
      const geometry = loader.parse(buffer);
      geometry.computeVertexNormals();
      geometry.computeBoundingBox();

      // If mesh already loaded under this filename, replace it
      if (this.stlMeshes.has(filename)) {
        const oldMesh = this.stlMeshes.get(filename);
        this.stlGroup.remove(oldMesh);
        this.disposeObject(oldMesh);
        this.stlMeshes.delete(filename);
      }

      // Assign palette color by current count
      const colorIdx = this.stlMeshes.size % STL_PALETTE.length;
      const color = STL_PALETTE[colorIdx];

      // Material: Sleek aerodynamic metallic finish with DoubleSide for thin aero surfaces
      const material = new THREE.MeshStandardMaterial({
        color: color,
        metalness: 0.35,
        roughness: 0.35,
        flatShading: false,
        side: THREE.DoubleSide,
      });

      const mesh = new THREE.Mesh(geometry, material);
      mesh.name = filename;
      mesh.userData = { filename, colorIdx, originalColor: color };
      this.stlGroup.add(mesh);
      this.stlMeshes.set(filename, mesh);

      // Recompute combined bounding box of all active parts
      const info = this.recomputeOverallBoundingBox();

      return {
        ...info,
        filename,
        colorHex: `#${color.toString(16).padStart(6, '0')}`,
      };
    } catch (err) {
      console.error(`Failed to parse STL '${filename}':`, err);
      return null;
    }
  }

  removeSTL(filename) {
    if (this.stlMeshes.has(filename)) {
      const mesh = this.stlMeshes.get(filename);
      this.stlGroup.remove(mesh);
      this.disposeObject(mesh);
      this.stlMeshes.delete(filename);
      return this.recomputeOverallBoundingBox();
    }
    return null;
  }

  clearSTLs() {
    for (const mesh of this.stlMeshes.values()) {
      this.stlGroup.remove(mesh);
      this.disposeObject(mesh);
    }
    this.stlMeshes.clear();
    if (this.bboxHelper) {
      this.scene.remove(this.bboxHelper);
      this.disposeObject(this.bboxHelper);
      this.bboxHelper = null;
    }
  }

  recomputeOverallBoundingBox() {
    if (this.bboxHelper) {
      this.scene.remove(this.bboxHelper);
      this.disposeObject(this.bboxHelper);
      this.bboxHelper = null;
    }

    if (this.stlMeshes.size === 0) {
      const warnEl = document.getElementById('scale-warning');
      if (warnEl) warnEl.style.display = 'none';
      return null;
    }

    const combinedBBox = new THREE.Box3();
    for (const mesh of this.stlMeshes.values()) {
      if (!mesh.geometry.boundingBox) {
        mesh.geometry.computeBoundingBox();
      }
      combinedBBox.union(mesh.geometry.boundingBox);
    }

    const size = new THREE.Vector3();
    combinedBBox.getSize(size);

    // Add Model Bounding Box Helper (Warm Golden Amber for instant visual distinction)
    this.bboxHelper = new THREE.Box3Helper(combinedBBox, 0xf59e0b);
    this.bboxHelper.visible = this.showBounds;
    this.scene.add(this.bboxHelper);

    // Check scale warning (mm vs m)
    const maxDim = Math.max(size.x, size.y, size.z);
    const warnEl = document.getElementById('scale-warning');
    if (warnEl) {
      warnEl.style.display = maxDim > 20.0 ? 'block' : 'none';
    }

    // If domain not yet set, place ground grid at model bottom
    if (!this.domainMin && this.groundGrid) {
      this.groundGrid.position.set(0, combinedBBox.min.y, 0);
    }

    // Proportional origin axes scale
    this.updateOriginAxesScale(maxDim);

    return {
      bbox: {
        min: [combinedBBox.min.x, combinedBBox.min.y, combinedBBox.min.z],
        max: [combinedBBox.max.x, combinedBBox.max.y, combinedBBox.max.z],
      },
      size: [size.x, size.y, size.z],
      isLikelyMM: maxDim > 20.0,
      totalMeshes: this.stlMeshes.size,
    };
  }

  getCombinedBoundingBox() {
    if (this.stlMeshes.size === 0) return null;
    const combinedBBox = new THREE.Box3();
    for (const mesh of this.stlMeshes.values()) {
      if (!mesh.geometry.boundingBox) {
        mesh.geometry.computeBoundingBox();
      }
      combinedBBox.union(mesh.geometry.boundingBox);
    }
    return {
      min: [combinedBBox.min.x, combinedBBox.min.y, combinedBBox.min.z],
      max: [combinedBBox.max.x, combinedBBox.max.y, combinedBBox.max.z],
    };
  }

  getCombinedBoundingBoxVector3() {
    if (this.stlMeshes.size === 0) return null;
    const combinedBBox = new THREE.Box3();
    for (const mesh of this.stlMeshes.values()) {
      if (!mesh.geometry.boundingBox) {
        mesh.geometry.computeBoundingBox();
      }
      combinedBBox.union(mesh.geometry.boundingBox);
    }
    return combinedBBox;
  }

  highlightSTL(filename) {
    const mesh = this.stlMeshes.get(filename);
    if (!mesh) return;

    const originalColor = mesh.userData.originalColor !== undefined
      ? mesh.userData.originalColor
      : mesh.material.color.getHex();

    mesh.material.color.setHex(0xffffff);
    mesh.material.emissive.setHex(0x38bdf8);
    mesh.material.emissiveIntensity = 0.6;

    setTimeout(() => {
      mesh.material.color.setHex(originalColor);
      mesh.material.emissive.setHex(0x000000);
      mesh.material.emissiveIntensity = 0;
    }, 500);
  }

  fitView(target = 'domain') {
    this.currentFocusTarget = target;
    const fov = this.camera.fov * (Math.PI / 180);
    this.camera.up.set(0, 1, 0);

    if (target === 'domain' && this.domainMin && this.domainMax) {
      const minVec = new THREE.Vector3(...this.domainMin);
      const maxVec = new THREE.Vector3(...this.domainMax);
      const center = new THREE.Vector3().addVectors(minVec, maxVec).multiplyScalar(0.5);
      const size = new THREE.Vector3().subVectors(maxVec, minVec);

      const maxDim = Math.max(size.x, size.y, size.z);
      // Framing distance with ample padding so all 40m tunnel boundaries and badges are visible
      const dist = Math.abs(maxDim / 2 / Math.tan(fov / 2)) * 1.35;

      this.camera.position.set(center.x + dist * 0.75, center.y + dist * 0.48, center.z + dist * 0.85);
      this.camera.lookAt(center);

      if (this.controls) {
        this.controls.target.copy(center);
        this.controls.update();
      }
    } else {
      const bbox = this.getCombinedBoundingBoxVector3();
      if (bbox) {
        const center = new THREE.Vector3();
        const size = new THREE.Vector3();
        bbox.getCenter(center);
        bbox.getSize(size);

        const maxDim = Math.max(size.x, size.y, size.z);
        const dist = Math.abs(maxDim / 2 / Math.tan(fov / 2)) * 1.8;

        this.camera.position.set(center.x + dist * 0.8, center.y + dist * 0.6, center.z + dist);
        this.camera.lookAt(center);

        if (this.controls) {
          this.controls.target.copy(center);
          this.controls.update();
        }
      } else {
        this.camera.position.set(15, 12, 28);
        this.camera.lookAt(0, 0, 0);
        if (this.controls) {
          this.controls.target.set(0, 0, 0);
          this.controls.update();
        }
      }
    }
  }

  resetCamera(target = null) {
    this.fitView(target || this.currentFocusTarget || (this.domainMin ? 'domain' : 'model'));
  }

  setViewAngle(angle, target = null) {
    const activeTarget = target || this.currentFocusTarget || (this.domainMin ? 'domain' : 'model');
    const fov = this.camera.fov * (Math.PI / 180);

    let center = new THREE.Vector3(0, 0, 0);
    let dist = 20;

    if (activeTarget === 'domain' && this.domainMin && this.domainMax) {
      const minVec = new THREE.Vector3(...this.domainMin);
      const maxVec = new THREE.Vector3(...this.domainMax);
      center = new THREE.Vector3().addVectors(minVec, maxVec).multiplyScalar(0.5);
      const size = new THREE.Vector3().subVectors(maxVec, minVec);
      const maxDim = Math.max(size.x, size.y, size.z);
      dist = Math.abs(maxDim / 2 / Math.tan(fov / 2)) * 1.35;
    } else {
      const bbox = this.getCombinedBoundingBoxVector3();
      if (bbox) {
        bbox.getCenter(center);
        const size = new THREE.Vector3();
        bbox.getSize(size);
        const maxDim = Math.max(size.x, size.y, size.z);
        dist = Math.abs(maxDim / 2 / Math.tan(fov / 2)) * 1.8;
      }
    }

    if (angle === 'top') {
      this.camera.up.set(0, 0, -1);
      this.camera.position.set(center.x, center.y + dist * 1.25, center.z);
    } else {
      this.camera.up.set(0, 1, 0);
      if (angle === 'side') {
        this.camera.position.set(center.x + dist * 1.25, center.y, center.z);
      } else if (angle === 'front') {
        this.camera.position.set(center.x, center.y, center.z + dist * 1.25);
      } else { // iso
        this.camera.position.set(center.x + dist * 0.75, center.y + dist * 0.48, center.z + dist * 0.85);
      }
    }

    this.camera.lookAt(center);
    if (this.controls) {
      this.controls.target.copy(center);
      this.controls.update();
    }
  }

  orientPlaneToNormal(mesh, normalIdx) {
    // PlaneGeometry lies in local XY with normal +Z; rotate it onto a world axis.
    if (normalIdx === 0) {
      mesh.rotation.set(0, Math.PI / 2, 0);
    } else if (normalIdx === 1) {
      mesh.rotation.set(-Math.PI / 2, 0, 0);
    } else {
      mesh.rotation.set(0, 0, 0);
    }
  }

  planeExtentsForNormal(normalIdx) {
    // Returns [widthAxisIndex, heightAxisIndex] so a PlaneGeometry spans the
    // two in-plane world axes after orientPlaneToNormal() is applied.
    if (normalIdx === 0) return [2, 1];
    if (normalIdx === 1) return [0, 2];
    return [0, 1];
  }

  updateDomainBox(domainMin, domainMax, symPlane = null, flowDirection = '-z', upAxis = 'y', lateralAxis = 'x') {
    if (this.domainBoxGroup) {
      this.scene.remove(this.domainBoxGroup);
      this.disposeGroup(this.domainBoxGroup);
      this.domainBoxGroup = null;
    }

    if (!domainMin || !domainMax) return;

    this.domainMin = domainMin;
    this.domainMax = domainMax;
    this.symPlaneCoord = symPlane;
    this.flowDirection = flowDirection || '-z';

    const axisToIdx = { x: 0, y: 1, z: 2 };
    const flowIdx = axisToIdx[(this.flowDirection || '-z').replace(/^[+-]/, '').toLowerCase()] ?? 2;
    const upIdx = axisToIdx[(upAxis || 'y').replace(/^[+-]/, '').toLowerCase()] ?? 1;
    let latIdx = axisToIdx[(lateralAxis || 'x').replace(/^[+-]/, '').toLowerCase()];
    if (latIdx === undefined || latIdx === flowIdx || latIdx === upIdx) {
      latIdx = [0, 1, 2].find((i) => i !== flowIdx && i !== upIdx) ?? 0;
    }

    this.domainBoxGroup = new THREE.Group();

    const minVec = new THREE.Vector3(domainMin[0], domainMin[1], domainMin[2]);
    const maxVec = new THREE.Vector3(domainMax[0], domainMax[1], domainMax[2]);
    const box3 = new THREE.Box3(minVec, maxVec);

    const size = new THREE.Vector3().subVectors(maxVec, minVec);
    const center = new THREE.Vector3().addVectors(minVec, maxVec).multiplyScalar(0.5);

    // Update origin axes scale according to domain scale
    this.updateOriginAxesScale(Math.max(size.x, size.y, size.z));

    // 1. Vibrant Neon Cyan Domain Wireframe Cage
    const wireHelper = new THREE.Box3Helper(box3, 0x00f0ff);
    this.domainBoxGroup.add(wireHelper);

    // 2. Corner Accent Spheres (Emphasize domain box vertices clearly in 3D)
    const sphereRadius = Math.max(0.08, Math.min(size.x, size.y, size.z) * 0.018);
    const sphereGeo = new THREE.SphereGeometry(sphereRadius, 10, 10);
    const sphereMat = new THREE.MeshBasicMaterial({ color: 0x38bdf8 });
    const corners = [
      new THREE.Vector3(minVec.x, minVec.y, minVec.z),
      new THREE.Vector3(maxVec.x, minVec.y, minVec.z),
      new THREE.Vector3(minVec.x, maxVec.y, minVec.z),
      new THREE.Vector3(maxVec.x, maxVec.y, minVec.z),
      new THREE.Vector3(minVec.x, minVec.y, maxVec.z),
      new THREE.Vector3(maxVec.x, minVec.y, maxVec.z),
      new THREE.Vector3(minVec.x, maxVec.y, maxVec.z),
      new THREE.Vector3(maxVec.x, maxVec.y, maxVec.z),
    ];
    corners.forEach((c) => {
      const sp = new THREE.Mesh(sphereGeo, sphereMat);
      sp.position.copy(c);
      this.domainBoxGroup.add(sp);
    });

    // 3. Translucent Wind Tunnel Enclosure Volume (double-sided so never back-culled)
    const boxGeo = new THREE.BoxGeometry(size.x, size.y, size.z);
    const boxMat = new THREE.MeshBasicMaterial({
      color: 0x0284c7,
      transparent: true,
      opacity: 0.08,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    const boxMesh = new THREE.Mesh(boxGeo, boxMat);
    boxMesh.position.copy(center);
    this.domainBoxGroup.add(boxMesh);

    // 4. Dedicated Inlet and Outlet Faces aligned with active flow direction
    const flowVec = this.parseFlowDirectionVector(this.flowDirection);
    let flowAxis = 'z';
    let flowSign = -1;
    if (Math.abs(flowVec.x) > 0) { flowAxis = 'x'; flowSign = Math.sign(flowVec.x); }
    else if (Math.abs(flowVec.y) > 0) { flowAxis = 'y'; flowSign = Math.sign(flowVec.y); }
    else { flowAxis = 'z'; flowSign = Math.sign(flowVec.z); }

    let inletPos = new THREE.Vector3().copy(center);
    let outletPos = new THREE.Vector3().copy(center);
    let faceWidth = size.x, faceHeight = size.y;
    let planeRotation = new THREE.Euler(0, 0, 0);

    if (flowAxis === 'x') {
      faceWidth = size.z;
      faceHeight = size.y;
      planeRotation = new THREE.Euler(0, Math.PI / 2, 0);
      inletPos.x = flowSign < 0 ? maxVec.x : minVec.x;
      outletPos.x = flowSign < 0 ? minVec.x : maxVec.x;
    } else if (flowAxis === 'y') {
      faceWidth = size.x;
      faceHeight = size.z;
      planeRotation = new THREE.Euler(-Math.PI / 2, 0, 0);
      inletPos.y = flowSign < 0 ? maxVec.y : minVec.y;
      outletPos.y = flowSign < 0 ? minVec.y : maxVec.y;
    } else { // z
      faceWidth = size.x;
      faceHeight = size.y;
      planeRotation = new THREE.Euler(0, 0, 0);
      inletPos.z = flowSign < 0 ? maxVec.z : minVec.z;
      outletPos.z = flowSign < 0 ? minVec.z : maxVec.z;
    }

    const inletPlaneGeo = new THREE.PlaneGeometry(faceWidth, faceHeight);
    const inletMat = new THREE.MeshBasicMaterial({
      color: 0x0284c7,
      transparent: true,
      opacity: 0.28,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    const inletMesh = new THREE.Mesh(inletPlaneGeo, inletMat);
    inletMesh.rotation.copy(planeRotation);
    inletMesh.position.copy(inletPos);
    this.domainBoxGroup.add(inletMesh);

    const inletBadge = this.createCanvasTextSprite('INLET  ➔', '#00f0ff', 'rgba(15, 23, 42, 0.9)');
    inletBadge.position.copy(inletPos);
    if (flowAxis === 'z') inletBadge.position.z += (flowSign < 0 ? 0.2 : -0.2);
    else if (flowAxis === 'x') inletBadge.position.x += (flowSign < 0 ? 0.2 : -0.2);
    else if (flowAxis === 'y') inletBadge.position.y += (flowSign < 0 ? 0.2 : -0.2);
    this.domainBoxGroup.add(inletBadge);

    const outletPlaneGeo = new THREE.PlaneGeometry(faceWidth, faceHeight);
    const outletMat = new THREE.MeshBasicMaterial({
      color: 0xea580c,
      transparent: true,
      opacity: 0.28,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    const outletMesh = new THREE.Mesh(outletPlaneGeo, outletMat);
    outletMesh.rotation.copy(planeRotation);
    outletMesh.position.copy(outletPos);
    this.domainBoxGroup.add(outletMesh);

    const outletBadge = this.createCanvasTextSprite('➔  OUTLET', '#f97316', 'rgba(30, 20, 15, 0.9)');
    outletBadge.position.copy(outletPos);
    if (flowAxis === 'z') outletBadge.position.z += (flowSign < 0 ? -0.2 : 0.2);
    else if (flowAxis === 'x') outletBadge.position.x += (flowSign < 0 ? -0.2 : 0.2);
    else if (flowAxis === 'y') outletBadge.position.y += (flowSign < 0 ? -0.2 : 0.2);
    this.domainBoxGroup.add(outletBadge);

    // 6. Ground Face (Dark road surface on the domain's up axis)
    const [groundW, groundH] = this.planeExtentsForNormal(upIdx);
    const groundPlaneGeo = new THREE.PlaneGeometry(size['xyz'[groundW]], size['xyz'[groundH]]);
    const groundMat = new THREE.MeshBasicMaterial({
      color: 0x0f172a,
      transparent: true,
      opacity: 0.65,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    const groundMesh = new THREE.Mesh(groundPlaneGeo, groundMat);
    this.orientPlaneToNormal(groundMesh, upIdx);
    const groundPos = center.clone();
    groundPos['xyz'[upIdx]] = minVec['xyz'[upIdx]];
    groundMesh.position.copy(groundPos);
    this.domainBoxGroup.add(groundMesh);

    // Update ground grid position and orientation to sit flush with the floor
    if (this.groundGrid) {
      this.groundGrid.rotation.set(0, 0, 0);
      if (upIdx === 0) this.groundGrid.rotation.set(0, 0, -Math.PI / 2);
      else if (upIdx === 2) this.groundGrid.rotation.set(Math.PI / 2, 0, 0);
      this.groundGrid.position.copy(groundPos);
    }

    // 7. Symmetry Plane Indicator (plane normal to the lateral axis)
    if (symPlane !== null && symPlane !== undefined && isFinite(symPlane)) {
      const [symW, symH] = this.planeExtentsForNormal(latIdx);
      const symGeo = new THREE.PlaneGeometry(size['xyz'[symW]], size['xyz'[symH]]);
      const symMat = new THREE.MeshBasicMaterial({
        color: 0x06b6d4,
        transparent: true,
        opacity: 0.22,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      const symMesh = new THREE.Mesh(symGeo, symMat);
      this.orientPlaneToNormal(symMesh, latIdx);
      const symPos = center.clone();
      symPos['xyz'[latIdx]] = symPlane;
      symMesh.position.copy(symPos);
      this.domainBoxGroup.add(symMesh);

      const symBadge = this.createCanvasTextSprite(`SYMMETRY (${symPlane}m)`, '#38bdf8', 'rgba(15, 23, 42, 0.85)');
      const symBadgePos = center.clone();
      symBadgePos['xyz'[latIdx]] = symPlane;
      symBadgePos['xyz'[upIdx]] = maxVec['xyz'[upIdx]] - 0.4;
      symBadge.position.copy(symBadgePos);
      this.domainBoxGroup.add(symBadge);
    }

    // 8. Update Flow Arrow position to point into wind tunnel from inlet
    const flowDirVec = this.parseFlowDirectionVector(this.flowDirection);
    const flowArrowLen = Math.min(3.5, Math.max(1.0, (flowAxis === 'x' ? size.x : flowAxis === 'y' ? size.y : size.z) * 0.1));
    const flowOrigin = inletPos.clone().addScaledVector(flowDirVec, flowArrowLen * 0.2);
    this.updateFlowArrow(flowDirVec, flowOrigin, flowArrowLen);

    this.domainBoxGroup.visible = this.showDomain;
    this.scene.add(this.domainBoxGroup);

    // Update Dimensions in UI (W = lateral, H = up, L = flow)
    const dimsBadge = document.getElementById('viewer-domain-badge');
    const dimsText = document.getElementById('domain-dims-text');
    if (dimsBadge && dimsText) {
      const latSize = size['xyz'[latIdx]];
      const upSize = size['xyz'[upIdx]];
      const flowSize = size['xyz'[flowIdx]];
      dimsText.textContent = `${latSize.toFixed(2)}m (W) × ${upSize.toFixed(2)}m (H) × ${flowSize.toFixed(2)}m (L)`;
      dimsBadge.style.display = 'flex';
    }
  }

  toggleDomain(show) {
    this.showDomain = show;
    if (this.domainBoxGroup) this.domainBoxGroup.visible = show;
    const badge = document.getElementById('viewer-domain-badge');
    if (badge && !show) badge.style.display = 'none';
    else if (badge && show && this.domainMin) badge.style.display = 'flex';
  }

  toggleBounds(show) {
    this.showBounds = show;
    if (this.bboxHelper) this.bboxHelper.visible = show;
  }

  toggleGround(show) {
    this.showGround = show;
    if (this.groundGrid) this.groundGrid.visible = show;
  }

  toggleFlow(show) {
    this.showFlow = show;
    if (this.flowArrow) this.flowArrow.visible = show;
  }

  toggleAxes(show) {
    this.showAxes = show;
    if (this.originAxesGroup) this.originAxesGroup.visible = show;
    const legend = document.getElementById('viewer-axes-legend');
    if (legend) legend.style.display = show ? 'flex' : 'none';
  }

  createAxisLabel(text, color, scale = 0.35, isSceneAxis = false) {
    const canvas = document.createElement('canvas');
    canvas.width = 128;
    canvas.height = 128;
    const ctx = canvas.getContext('2d');

    // Circular dark slate badge background
    ctx.fillStyle = 'rgba(15, 23, 42, 0.92)';
    ctx.beginPath();
    ctx.arc(64, 64, 54, 0, Math.PI * 2);
    ctx.fill();

    // Colored perimeter rim
    ctx.strokeStyle = color;
    ctx.lineWidth = 6;
    ctx.stroke();

    // Bold crisp typography
    ctx.font = 'bold 64px "JetBrains Mono", ui-monospace, monospace, sans-serif';
    ctx.fillStyle = color;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, 64, 66);

    const texture = new THREE.CanvasTexture(canvas);
    texture.minFilter = THREE.LinearFilter;
    texture.needsUpdate = true;

    const spriteMat = new THREE.SpriteMaterial({
      map: texture,
      transparent: true,
      depthTest: isSceneAxis ? true : false,
      depthWrite: false,
    });
    const sprite = new THREE.Sprite(spriteMat);
    sprite.scale.set(scale, scale, 1.0);
    return sprite;
  }

  createGizmoAxis(dir, hexColor, cssColor, label, shaftRadius, shaftLength, headRadius, headLength, labelScale = 0.45, isSceneAxis = false) {
    const group = new THREE.Group();
    const mat = new THREE.MeshLambertMaterial({ color: hexColor });

    const shaftGeo = new THREE.CylinderGeometry(shaftRadius, shaftRadius, shaftLength, 16);
    const shaft = new THREE.Mesh(shaftGeo, mat);

    const headGeo = new THREE.ConeGeometry(headRadius, headLength, 16);
    const head = new THREE.Mesh(headGeo, mat);

    if (dir.x !== 0) {
      const s = Math.sign(dir.x);
      shaft.rotation.z = -Math.PI / 2 * s;
      shaft.position.x = (shaftLength / 2) * s;
      head.rotation.z = -Math.PI / 2 * s;
      head.position.x = (shaftLength + headLength / 2) * s;
    } else if (dir.y !== 0) {
      const s = Math.sign(dir.y);
      shaft.position.y = (shaftLength / 2) * s;
      head.position.y = (shaftLength + headLength / 2) * s;
      if (s < 0) {
        shaft.rotation.z = Math.PI;
        head.rotation.z = Math.PI;
      }
    } else if (dir.z !== 0) {
      const s = Math.sign(dir.z);
      shaft.rotation.x = Math.PI / 2 * s;
      shaft.position.z = (shaftLength / 2) * s;
      head.rotation.x = Math.PI / 2 * s;
      head.position.z = (shaftLength + headLength / 2) * s;
    }

    group.add(shaft);
    group.add(head);

    // Label sprite at arrow tip
    const labelSprite = this.createAxisLabel(label, cssColor, labelScale, isSceneAxis);
    const labelDist = shaftLength + headLength + (isSceneAxis ? headLength * 0.5 : 0.22);
    labelSprite.position.set(dir.x * labelDist, dir.y * labelDist, dir.z * labelDist);
    group.add(labelSprite);

    return group;
  }

  createOriginAxes(axisLength = 1.5) {
    if (this.originAxesGroup) {
      this.scene.remove(this.originAxesGroup);
      this.originAxesGroup = null;
    }

    this.originAxesGroup = new THREE.Group();

    // Center origin sphere at (0, 0, 0)
    const centerGeo = new THREE.SphereGeometry(axisLength * 0.035, 16, 16);
    const centerMat = new THREE.MeshLambertMaterial({ color: 0x94a3b8 });
    const centerMesh = new THREE.Mesh(centerGeo, centerMat);
    this.originAxesGroup.add(centerMesh);

    // Origin (0,0,0) subtle badge
    const originBadge = this.createAxisLabel('0', '#94a3b8', axisLength * 0.18, true);
    originBadge.position.set(-axisLength * 0.08, -axisLength * 0.08, -axisLength * 0.08);
    this.originAxesGroup.add(originBadge);

    const shaftRadius = axisLength * 0.016;
    const headRadius = axisLength * 0.045;
    const headLength = axisLength * 0.18;
    const shaftLength = axisLength - headLength;

    // +X (Red - Lateral)
    const xGroup = this.createGizmoAxis(
      new THREE.Vector3(1, 0, 0),
      0xef4444,
      '#ef4444',
      'X',
      shaftRadius,
      shaftLength,
      headRadius,
      headLength,
      axisLength * 0.26,
      true
    );
    this.originAxesGroup.add(xGroup);

    // +Y (Green - Elevation)
    const yGroup = this.createGizmoAxis(
      new THREE.Vector3(0, 1, 0),
      0x10b981,
      '#10b981',
      'Y',
      shaftRadius,
      shaftLength,
      headRadius,
      headLength,
      axisLength * 0.26,
      true
    );
    this.originAxesGroup.add(yGroup);

    // +Z (Blue - Streamwise)
    const zGroup = this.createGizmoAxis(
      new THREE.Vector3(0, 0, 1),
      0x3b82f6,
      '#3b82f6',
      'Z',
      shaftRadius,
      shaftLength,
      headRadius,
      headLength,
      axisLength * 0.26,
      true
    );
    this.originAxesGroup.add(zGroup);

    this.originAxesGroup.visible = this.showAxes;
    this.scene.add(this.originAxesGroup);
  }

  initGizmo() {
    this.gizmoScene = new THREE.Scene();
    this.gizmoCamera = new THREE.OrthographicCamera(-1.8, 1.8, 1.8, -1.8, 0.1, 50);

    const ambient = new THREE.AmbientLight(0xffffff, 0.85);
    this.gizmoScene.add(ambient);
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.9);
    dirLight.position.set(2, 4, 3);
    this.gizmoScene.add(dirLight);

    // Central hub
    const hubGeo = new THREE.SphereGeometry(0.12, 16, 16);
    const hubMat = new THREE.MeshLambertMaterial({ color: 0x64748b });
    const hub = new THREE.Mesh(hubGeo, hubMat);
    this.gizmoScene.add(hub);

    const length = 1.0;
    const shaftRadius = 0.045;
    const headRadius = 0.11;
    const headLength = 0.28;
    const shaftLength = length - headLength;

    // +X (Red)
    this.gizmoScene.add(this.createGizmoAxis(new THREE.Vector3(1, 0, 0), 0xef4444, '#ef4444', 'X', shaftRadius, shaftLength, headRadius, headLength, 0.45, false));
    // +Y (Green)
    this.gizmoScene.add(this.createGizmoAxis(new THREE.Vector3(0, 1, 0), 0x10b981, '#10b981', 'Y', shaftRadius, shaftLength, headRadius, headLength, 0.45, false));
    // +Z (Blue)
    this.gizmoScene.add(this.createGizmoAxis(new THREE.Vector3(0, 0, 1), 0x3b82f6, '#3b82f6', 'Z', shaftRadius, shaftLength, headRadius, headLength, 0.45, false));

    // Subtle negative axis lines (-X, -Y, -Z) for 3D depth perception
    const negMat = new THREE.LineBasicMaterial({ color: 0x334155, transparent: true, opacity: 0.5 });
    const negGeoX = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(-0.45, 0, 0)]);
    const negGeoY = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, -0.45, 0)]);
    const negGeoZ = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 0, -0.45)]);
    this.gizmoScene.add(new THREE.Line(negGeoX, negMat));
    this.gizmoScene.add(new THREE.Line(negGeoY, negMat));
    this.gizmoScene.add(new THREE.Line(negGeoZ, negMat));
  }

  updateOriginAxesScale(dim) {
    if (!this.originAxesGroup || !dim || dim <= 0) return;
    const targetLength = Math.max(0.6, Math.min(4.5, dim * 0.22));
    const scale = targetLength / 1.5;
    this.originAxesGroup.scale.set(scale, scale, scale);
  }

  onResize() {
    if (!this.container || !this.renderer || !this.camera) return;
    const width = this.container.clientWidth;
    const height = this.container.clientHeight;
    if (!width || !height || width <= 0 || height <= 0) return;
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height);
  }

  animate() {
    requestAnimationFrame(() => this.animate());
    if (!this.container || this.container.clientWidth <= 0 || this.container.clientHeight <= 0) {
      return; // Skip rendering when tab is hidden or 0 size
    }
    if (this.controls) this.controls.update();

    if (this.renderer && this.scene && this.camera) {
      const width = this.container.clientWidth || 600;
      const height = this.container.clientHeight || 480;

      // 1. Clear and render main 3D wind tunnel scene
      this.renderer.setViewport(0, 0, width, height);
      this.renderer.clear();
      this.renderer.render(this.scene, this.camera);

      // 2. Render Corner Orientation Trihedron Gizmo (synchronous camera tracking)
      if (this.showAxes && this.gizmoScene && this.gizmoCamera) {
        const dir = new THREE.Vector3();
        if (this.controls && this.controls.target) {
          dir.copy(this.camera.position).sub(this.controls.target);
        } else {
          dir.copy(this.camera.position);
        }
        dir.normalize().multiplyScalar(3.2);

        this.gizmoCamera.position.copy(dir);
        this.gizmoCamera.up.copy(this.camera.up);
        this.gizmoCamera.lookAt(0, 0, 0);

        // Position in bottom-right corner of the 3D viewport
        const gizmoSize = 80;
        const gizmoMargin = 10;
        const gizmoX = width - gizmoSize - gizmoMargin;
        const gizmoY = gizmoMargin; // WebGL Y is from bottom

        if (gizmoX > 0 && width > 140) {
          this.renderer.clearDepth();
          this.renderer.setScissorTest(true);
          this.renderer.setScissor(gizmoX, gizmoY, gizmoSize, gizmoSize);
          this.renderer.setViewport(gizmoX, gizmoY, gizmoSize, gizmoSize);
          this.renderer.render(this.gizmoScene, this.gizmoCamera);
          this.renderer.setScissorTest(false);
          this.renderer.setViewport(0, 0, width, height);
        }
      }
    }
  }
}

window.STLViewer = STLViewer;
