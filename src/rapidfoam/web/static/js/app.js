/**
 * OpenFOAM Case Generator Studio - Main Application Controller
 */

class CFDApp {
  constructor() {
    this.viewer = null;
    this.charts = null;
    this.clusterConnected = false;
    this.pollInterval = null;
    this.telemetryPollingActive = true;
    this.archiveCases = [];
    this.currentArchiveFilter = 'all';
    this.archiveSearchTerm = '';
    this.isSyncingFromJson = false;
    this.currentSTLName = null;
    this.downloadStates = new Map();
    this.downloadPollTimer = null;

    this.activeConfig = {
      case_name: "my_case",
      stl_files: ["geometry.stl"],
      stl_dir: "stl",
      case_dir: "cases",
      fidelity: "standard",
      flow: {
        velocity: 16.67,
        direction: "-z",
        ground: true,
      },
      outputs: {
        drag_axis: "-z",
        downforce_axis: "-y",
      },
      domain_box: "auto",
      symmetry_plane: 0.0,
      ground_clearance: 0.035,
      domain_faces: {
        "-x": "symmetry",
        "+x": "farField",
        "-y": "ground",
        "+y": "farField",
        "+z": "inlet",
        "-z": "outlet",
      },
      parallel: {
        n_procs: 32,
        method: "scotch",
      },
      slurm: {
        qos: "cu_hpc",
        partition: "cpu",
        nodes: 1,
        time: "08:00:00",
        mem_per_cpu: "2G",
        openfoam_module: [
          "GCC/11.3.0",
          "OpenMPI/4.1.4-GCC-11.3.0"
        ],
        openfoam_source: "$HOME/OpenFOAM/OpenFOAM-v2606/etc/bashrc",
      },
      _comment_overrides: "Expert overrides — all fields below have built-in defaults in fidelity presets. Uncomment only if manual tuning is needed.",
      _optional_overrides_example: {
        solver: {
          _end_time: 800,
          _write_interval: 400,
          _purge_write: 2,
        },
        force_refs: {
          _Aref: 1.0,
          _lRef: 1.0,
          _CofR: [0.0, 0.0, 0.0],
        },
        mesh_params: {
          _base_cell_size: 0.10,
          _surface_level: [4, 5],
          _edge_level: 6,
          _near_wake_level: 3,
          _far_wake_level: 1,
        },
        layers: {
          _n_layers: 5,
          _expansion_ratio: 1.2,
          _first_layer_thickness: 0.3,
          _min_thickness: 0.05,
        },
        fluid: {
          _rho: 1.225,
          _nu: 1.516e-5,
        },
        turbulence: {
          _model: "kOmegaSST",
          _intensity: 0.005,
          _nut_ratio: 10,
        },
      }
    };

    this.init();
  }

  async init() {
    // 1. Initialize components
    this.viewer = new STLViewer('stl-viewer-container');
    this.charts = new TelemetryCharts();

    // 2. Bind UI event listeners
    this.bindNavigation();
    this.bindSubtabs();
    this.bindConfigFormInputs();
    this.bindJsonDrawer();
    this.bindSTLUpload();
    this.bindSSHModal();
    this.bindTelemetryEvents();
    this.bindCasesArchiveEvents();

    // 3. Load initial data from backend
    await this.loadLocalClusterConfig();
    await this.checkClusterStatus();
    await this.loadTemplatesList();
    await this.loadExistingSTLs();
    // Automatically load configs/config.json as the default config
    await this.loadConfigFile('config.json', true);
    await this.loadCasesArchive();
    await this.resumeActiveDownloads();

    // 5. Start background queue polling
    this.pollInterval = setInterval(() => {
      if (this.clusterConnected) {
        this.refreshQueue();
      }
      const activeTab = document.querySelector('.nav-tab.active');
      if (this.telemetryPollingActive && activeTab && activeTab.dataset.tab === 'telemetry-tab') {
        this.pollTelemetry();
      }
    }, 5000);
  }

  // -------------------------------------------------------------
  // Navigation & Sub-Tabs
  // -------------------------------------------------------------
  bindNavigation() {
    const tabs = document.querySelectorAll('.nav-tab');
    tabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        tabs.forEach((t) => t.classList.remove('active'));
        tab.classList.add('active');

        const targetId = tab.dataset.tab;
        document.querySelectorAll('.tab-pane').forEach((p) => p.classList.remove('active'));
        const pane = document.getElementById(targetId);
        if (pane) pane.classList.add('active');

        if (targetId === 'config-tab' && this.viewer) {
          setTimeout(() => this.viewer.onResize(), 50);
        } else if (targetId === 'telemetry-tab') {
          this.pollTelemetry();
        } else if (targetId === 'cases-tab') {
          this.loadCasesArchive();
        }
      });
    });
  }

  bindSubtabs() {
    const subtabs = document.querySelectorAll('.sub-tab');
    subtabs.forEach((st) => {
      st.addEventListener('click', () => {
        subtabs.forEach((s) => s.classList.remove('active'));
        st.classList.add('active');

        const targetId = st.dataset.subtab;
        document.querySelectorAll('.subtab-content').forEach((sc) => sc.classList.remove('active'));
        const content = document.getElementById(targetId);
        if (content) content.classList.add('active');
      });
    });
  }

  // -------------------------------------------------------------
  // Bidirectional Config Synchronization
  // -------------------------------------------------------------
  bindConfigFormInputs() {
    // Fidelity cards
    const fidelityCards = document.querySelectorAll('.fidelity-card');
    fidelityCards.forEach((card) => {
      card.addEventListener('click', () => {
        fidelityCards.forEach((c) => c.classList.remove('selected'));
        card.classList.add('selected');
        const radio = card.querySelector('input[type="radio"]');
        if (radio) radio.checked = true;
        const fidelity = card.dataset.fidelity || 'standard';
        this.updateOverridePlaceholders(fidelity);
        this.buildConfigFromVisualForm();
      });
    });

    // Velocity slider <-> km/h <-> m/s dual-sync
    const sliderVel = document.getElementById('slider-velocity');
    const inputKmh = document.getElementById('cfg-flow-velocity-kmh');
    const inputMs = document.getElementById('cfg-flow-velocity-ms');

    if (sliderVel && inputKmh && inputMs) {
      sliderVel.addEventListener('input', (e) => {
        const kmh = parseFloat(e.target.value);
        inputKmh.value = kmh.toFixed(1);
        inputMs.value = (kmh / 3.6).toFixed(2);
        this.buildConfigFromVisualForm();
      });

      inputKmh.addEventListener('input', (e) => {
        const kmh = parseFloat(e.target.value) || 0;
        sliderVel.value = Math.min(Math.max(kmh, 5), 150);
        inputMs.value = (kmh / 3.6).toFixed(2);
        this.buildConfigFromVisualForm();
      });

      inputMs.addEventListener('input', (e) => {
        const ms = parseFloat(e.target.value) || 0;
        const kmh = ms * 3.6;
        inputKmh.value = kmh.toFixed(1);
        sliderVel.value = Math.min(Math.max(kmh, 5), 150);
        this.buildConfigFromVisualForm();
      });
    }

    // Domain box selector (auto vs custom)
    const domainBoxSel = document.getElementById('cfg-domain-box');
    const customDomainDiv = document.getElementById('custom-domain-container');
    if (domainBoxSel && customDomainDiv) {
      domainBoxSel.addEventListener('change', () => {
        customDomainDiv.style.display = domainBoxSel.value === 'custom' ? 'block' : 'none';
        this.buildConfigFromVisualForm();
      });
    }

    // Overrides clear all button
    document.getElementById('btn-clear-all-overrides')?.addEventListener('click', () => {
      this.clearAllOverrides();
    });

    // Ground level style selector (2 + 1 styles)
    const groundStyleSelect = document.getElementById('cfg-ground-style');
    groundStyleSelect?.addEventListener('change', (e) => {
      const val = e.target.value;
      const groupRel = document.getElementById('group-ground-relative');
      const groupAbs = document.getElementById('group-ground-absolute');
      if (groupRel) groupRel.style.display = val === 'relative' ? 'block' : 'none';
      if (groupAbs) groupAbs.style.display = val === 'absolute' ? 'block' : 'none';
      this.buildConfigFromVisualForm();
      this.updateDomainBoxVisualization();
    });

    // Generic input change listeners on all visual inputs
    const form = document.getElementById('case-config-form');
    if (form) {
      form.addEventListener('input', () => this.buildConfigFromVisualForm());
      form.addEventListener('change', () => this.buildConfigFromVisualForm());
    }

    // Action buttons
    document.getElementById('btn-validate-config')?.addEventListener('click', () => this.validateCurrentConfig());
    document.getElementById('btn-save-config')?.addEventListener('click', () => this.saveCurrentConfig(false));
    document.getElementById('btn-generate-local')?.addEventListener('click', () => this.generateCaseLocally());
    document.getElementById('btn-submit-case')?.addEventListener('click', () => this.saveCurrentConfig(true));
    document.getElementById('btn-quick-run')?.addEventListener('click', () => this.saveCurrentConfig(true));

    document.getElementById('btn-reset-defaults')?.addEventListener('click', async () => {
      if (confirm('Reset all fields to configs/config.json?')) {
        await this.loadConfigFile('config.json');
        this.showToast('Reset to configs/config.json', 'info');
      }
    });

    // Template loader (auto-loads on change)
    const selectTemplate = document.getElementById('select-template');
    selectTemplate?.addEventListener('change', async (e) => {
      if (e.target.value) {
        await this.loadConfigFile(e.target.value);
      }
    });
    document.getElementById('btn-load-template')?.addEventListener('click', async () => {
      if (selectTemplate && selectTemplate.value) {
        await this.loadConfigFile(selectTemplate.value);
      }
    });

    // Auto symmetry plane shortcut in Domain & Ground form
    document.getElementById('btn-auto-sym-inline')?.addEventListener('click', () => this.autoSymmetryPlaneCenter());
  }

  updateVisualFormFromConfig(cfg) {
    if (!cfg) return;

    // General
    this.setVal('cfg-case-name', cfg.case_name || 'my_case');

    const fidelity = cfg.fidelity || 'standard';
    document.querySelectorAll('.fidelity-card').forEach((card) => {
      const match = card.dataset.fidelity === fidelity;
      card.classList.toggle('selected', match);
      const r = card.querySelector('input');
      if (r) r.checked = match;
    });

    // Flow
    const flow = cfg.flow || {};
    const velMs = flow.velocity !== undefined ? flow.velocity : 16.67;
    this.setVal('cfg-flow-velocity-ms', velMs);
    const kmh = velMs * 3.6;
    this.setVal('cfg-flow-velocity-kmh', kmh.toFixed(1));
    this.setVal('slider-velocity', Math.min(Math.max(kmh, 5), 150));
    this.setSelectValue('cfg-flow-direction', flow.direction || '-z');
    this.setCheck('cfg-flow-ground', flow.ground !== false);

    // Outputs
    const outputs = cfg.outputs || {};
    this.setSelectValue('cfg-outputs-drag', outputs.drag_axis || '-z');
    this.setSelectValue('cfg-outputs-downforce', outputs.downforce_axis || '-y');

    // Domain & Boundaries
    if (typeof cfg.domain_box === 'object' && cfg.domain_box !== null) {
      this.setSelectValue('cfg-domain-box', 'custom');
      document.getElementById('custom-domain-container').style.display = 'block';
      this.setVal('cfg-domain-min', JSON.stringify(cfg.domain_box.min || []));
      this.setVal('cfg-domain-max', JSON.stringify(cfg.domain_box.max || []));
    } else {
      this.setSelectValue('cfg-domain-box', 'auto');
      document.getElementById('custom-domain-container').style.display = 'none';
    }

    const sym = cfg.symmetry_plane !== undefined ? cfg.symmetry_plane : (cfg._symmetry_plane !== undefined ? cfg._symmetry_plane : 0.0);
    this.setVal('cfg-symmetry-plane', sym);

    // Ground Level Specification (2 + 1 Styles)
    const groupRel = document.getElementById('group-ground-relative');
    const groupAbs = document.getElementById('group-ground-absolute');

    if (cfg.ground_clearance !== undefined && cfg.ground_clearance !== null) {
      // Style 1: Relative Ride Height
      this.setSelectValue('cfg-ground-style', 'relative');
      this.setVal('cfg-ground-clearance', cfg.ground_clearance);
      if (groupRel) groupRel.style.display = 'block';
      if (groupAbs) groupAbs.style.display = 'none';
    } else if (cfg.ground_plane !== undefined && cfg.ground_plane !== null) {
      // Style 2: Absolute CAD Ground Plane
      this.setSelectValue('cfg-ground-style', 'absolute');
      this.setVal('cfg-ground-plane', cfg.ground_plane);
      if (groupRel) groupRel.style.display = 'none';
      if (groupAbs) groupAbs.style.display = 'block';
    } else {
      // Style 0 / None (Default in config.json): Touching CAD Bottom
      this.setSelectValue('cfg-ground-style', 'none');
      this.setVal('cfg-ground-clearance', cfg._ground_clearance !== undefined ? cfg._ground_clearance : 0.035);
      this.setVal('cfg-ground-plane', cfg._ground_plane !== undefined ? cfg._ground_plane : 0.0);
      if (groupRel) groupRel.style.display = 'none';
      if (groupAbs) groupAbs.style.display = 'none';
    }

    const faces = cfg.domain_faces || {};
    this.setSelectValue('cfg-face-neg-x', faces['-x'] || 'symmetry');
    this.setSelectValue('cfg-face-pos-x', faces['+x'] || 'farField');
    this.setSelectValue('cfg-face-neg-y', faces['-y'] || 'ground');
    this.setSelectValue('cfg-face-pos-y', faces['+y'] || 'farField');
    this.setSelectValue('cfg-face-pos-z', faces['+z'] || 'inlet');
    this.setSelectValue('cfg-face-neg-z', faces['-z'] || 'outlet');

    // Parallel & SLURM
    const par = cfg.parallel || {};
    this.setSelectValue('cfg-parallel-procs', par.n_procs || 32);
    this.setSelectValue('cfg-parallel-method', par.method || 'scotch');

    const slurm = cfg.slurm || {};
    this.setSelectValue('cfg-slurm-qos', slurm.qos || 'cu_hpc');
    this.setVal('cfg-slurm-partition', slurm.partition || 'cpu');
    this.setVal('cfg-slurm-time', slurm.time || '08:00:00');
    this.setVal('cfg-slurm-mem', slurm.mem_per_cpu || '2G');
    this.setVal('cfg-slurm-source', slurm.openfoam_source || '$HOME/OpenFOAM/OpenFOAM-v2606/etc/bashrc');
    
    if (Array.isArray(slurm.openfoam_module)) {
      this.setVal('cfg-slurm-modules', slurm.openfoam_module.join(', '));
    } else if (slurm.openfoam_module) {
      this.setVal('cfg-slurm-modules', slurm.openfoam_module);
    } else {
      this.setVal('cfg-slurm-modules', 'GCC/11.3.0, OpenMPI/4.1.4-GCC-11.3.0');
    }



    // Overrides handling: populate only overridden fields, leave others blank for preset fallback
    const overrides = cfg.overrides || {};
    const solver = overrides.solver || null;
    const refs = overrides.force_refs || null;
    const meshParams = overrides.mesh_params || null;
    const layers = overrides.layers || null;
    const fluid = overrides.fluid || null;
    const turb = overrides.turbulence || null;

    // 1. Solver & Iterations (Priority 1: Most frequently adjusted)
    this.setVal('cfg-override-solver-endtime', solver?.end_time ?? '');
    this.setVal('cfg-override-solver-writeinterval', solver?.write_interval ?? '');
    this.setVal('cfg-override-solver-purgewrite', solver?.purge_write ?? '');

    // 2. Force References (Priority 2: Geometry scale & moment centers)
    this.setVal('cfg-override-ref-aref', refs?.Aref ?? '');
    this.setVal('cfg-override-ref-lref', refs?.lRef ?? '');
    if (Array.isArray(refs?.CofR) && refs.CofR.length >= 3) {
      this.setVal('cfg-override-ref-cofr-x', refs.CofR[0]);
      this.setVal('cfg-override-ref-cofr-y', refs.CofR[1]);
      this.setVal('cfg-override-ref-cofr-z', refs.CofR[2]);
    } else {
      this.setVal('cfg-override-ref-cofr-x', '');
      this.setVal('cfg-override-ref-cofr-y', '');
      this.setVal('cfg-override-ref-cofr-z', '');
    }

    // 3. Mesh Params (Priority 3: Discretization & wake boxes)
    this.setVal('cfg-override-basecell', meshParams?.base_cell_size ?? '');
    if (Array.isArray(meshParams?.surface_level) && meshParams.surface_level.length >= 2) {
      this.setVal('cfg-override-surf-min', meshParams.surface_level[0]);
      this.setVal('cfg-override-surf-max', meshParams.surface_level[1]);
    } else {
      this.setVal('cfg-override-surf-min', '');
      this.setVal('cfg-override-surf-max', '');
    }
    this.setVal('cfg-override-edge', meshParams?.edge_level ?? '');
    this.setVal('cfg-override-nearwake', meshParams?.near_wake_level ?? '');
    this.setVal('cfg-override-farwake', meshParams?.far_wake_level ?? '');

    // 4. Boundary Layer Overrides (Priority 4: Wall y+ & inflation)
    this.setVal('cfg-override-layer-nlayers', layers?.n_layers ?? '');
    this.setVal('cfg-override-layer-expansion', layers?.expansion_ratio ?? '');
    this.setVal('cfg-override-layer-firstlayer', layers?.first_layer_thickness ?? '');
    this.setVal('cfg-override-layer-minthickness', layers?.min_thickness ?? '');

    // 5. Fluid Properties (Priority 5: Ambient medium)
    this.setVal('cfg-override-fluid-rho', fluid?.rho ?? '');
    this.setVal('cfg-override-fluid-nu', fluid?.nu ?? '');

    // 6. Turbulence Modeling (Priority 6: Closure model)
    this.setSelectValue('cfg-override-turb-model', turb?.model ?? '');
    this.setVal('cfg-override-turb-intensity', turb?.intensity ?? '');
    this.setVal('cfg-override-turb-nut-ratio', turb?.nut_ratio ?? '');

    this.updateOverridePlaceholders(fidelity);

    // Render active STL chips (reconcile placeholders with available server geometries)
    const newStls = cfg.stl_files || [];
    if (this.availableSTLs && this.availableSTLs.length > 0) {
      const availableNames = new Set(this.availableSTLs.map((s) => s.filename));
      const validActive = newStls.filter((f) => availableNames.has(f));
      if (validActive.length > 0) {
        this.activeConfig.stl_files = validActive;
      } else if (newStls.length > 0 && !newStls.includes('geometry.stl')) {
        this.activeConfig.stl_files = newStls;
      } else {
        // Fall back to first available geometry if config uses a generic placeholder (e.g. geometry.stl)
        this.activeConfig.stl_files = [this.availableSTLs[0].filename];
      }
    } else {
      this.activeConfig.stl_files = newStls;
    }
    this.renderActiveSTLChips(this.activeConfig.stl_files);
    this.loadAllActiveSTLsFromServer(true);
  }

  buildConfigFromVisualForm() {
    if (this.isSyncingFromJson) return;
    const cfg = { ...this.activeConfig };

    // General
    cfg.case_name = this.getVal('cfg-case-name') || 'my_case';

    const selectedFidelityCard = document.querySelector('.fidelity-card.selected');
    cfg.fidelity = selectedFidelityCard ? selectedFidelityCard.dataset.fidelity : 'standard';

    // Flow
    cfg.flow = {
      velocity: parseFloat(this.getVal('cfg-flow-velocity-ms')) || 16.67,
      direction: this.getVal('cfg-flow-direction') || '-z',
      ground: this.getCheck('cfg-flow-ground'),
    };

    // Outputs
    cfg.outputs = {
      drag_axis: this.getVal('cfg-outputs-drag') || '-z',
      downforce_axis: this.getVal('cfg-outputs-downforce') || '-y',
    };

    // Domain
    const domainChoice = this.getVal('cfg-domain-box');
    if (domainChoice === 'custom') {
      try {
        cfg.domain_box = {
          min: JSON.parse(this.getVal('cfg-domain-min')),
          max: JSON.parse(this.getVal('cfg-domain-max')),
        };
      } catch {
        cfg.domain_box = 'auto';
      }
    } else {
      cfg.domain_box = 'auto';
    }

    const symPlane = parseFloat(this.getVal('cfg-symmetry-plane'));
    cfg.symmetry_plane = isNaN(symPlane) ? 0.0 : symPlane;

    const groundStyle = this.getVal('cfg-ground-style');
    if (groundStyle === 'relative') {
      const gClear = parseFloat(this.getVal('cfg-ground-clearance'));
      cfg.ground_clearance = isNaN(gClear) ? 0.035 : gClear;
      delete cfg.ground_plane;
      delete cfg._ground_clearance;
      delete cfg._ground_plane;
    } else if (groundStyle === 'absolute') {
      const gPlane = parseFloat(this.getVal('cfg-ground-plane'));
      cfg.ground_plane = isNaN(gPlane) ? 0.0 : gPlane;
      delete cfg.ground_clearance;
      delete cfg._ground_clearance;
      delete cfg._ground_plane;
    } else {
      // Style 0 / None (Default in config.json): Touching CAD Bottom
      delete cfg.ground_clearance;
      delete cfg.ground_plane;
      // Preserve commented example keys if they existed in activeConfig
      if (this.activeConfig._ground_comment !== undefined) {
        cfg._ground_comment = this.activeConfig._ground_comment;
      }
      if (this.activeConfig._ground_clearance !== undefined) {
        cfg._ground_clearance = this.activeConfig._ground_clearance;
      }
      if (this.activeConfig._ground_clearance_desc !== undefined) {
        cfg._ground_clearance_desc = this.activeConfig._ground_clearance_desc;
      }
      if (this.activeConfig._ground_plane !== undefined) {
        cfg._ground_plane = this.activeConfig._ground_plane;
      }
      if (this.activeConfig._ground_plane_desc !== undefined) {
        cfg._ground_plane_desc = this.activeConfig._ground_plane_desc;
      }
    }

    cfg.domain_faces = {
      "-x": this.getVal('cfg-face-neg-x'),
      "+x": this.getVal('cfg-face-pos-x'),
      "-y": this.getVal('cfg-face-neg-y'),
      "+y": this.getVal('cfg-face-pos-y'),
      "+z": this.getVal('cfg-face-pos-z'),
      "-z": this.getVal('cfg-face-neg-z'),
    };

    // Parallel (preserve untouched keys such as the decomposition method)
    const prevParallel = this.activeConfig.parallel || {};
    cfg.parallel = {
      ...prevParallel,
      n_procs: parseInt(this.getVal('cfg-parallel-procs'), 10) || 32,
      method: this.getVal('cfg-parallel-method') || prevParallel.method || 'scotch',
    };

    // SLURM
    const modStr = this.getVal('cfg-slurm-modules');
    const modules = modStr ? modStr.split(',').map((s) => s.trim()).filter(Boolean) : null;

    const prevSlurm = this.activeConfig.slurm || {};
    cfg.slurm = {
      ...prevSlurm,
      qos: this.getVal('cfg-slurm-qos'),
      partition: this.getVal('cfg-slurm-partition'),
      nodes: prevSlurm.nodes !== undefined ? prevSlurm.nodes : 1,
      time: this.getVal('cfg-slurm-time'),
      mem_per_cpu: this.getVal('cfg-slurm-mem'),
      openfoam_module: modules,
      openfoam_source: this.getVal('cfg-slurm-source'),
    };

    // Selective Overrides handling:
    // Only include fields that have a value entered. Blank fields follow presets / universal defaults.
    const getOptionalStr = (id) => {
      const v = this.getVal(id);
      return (v !== null && v !== undefined && String(v).trim() !== '') ? String(v).trim() : null;
    };
    const getOptionalFloat = (id) => {
      const v = getOptionalStr(id);
      if (v === null) return null;
      const parsed = parseFloat(v);
      return isNaN(parsed) ? null : parsed;
    };
    const getOptionalInt = (id) => {
      const v = getOptionalStr(id);
      if (v === null) return null;
      const parsed = parseInt(v, 10);
      return isNaN(parsed) ? null : parsed;
    };

    const overrides = {};

    // 1. Solver (Priority 1)
    const solverOverrides = {};
    const endTime = getOptionalInt('cfg-override-solver-endtime');
    if (endTime !== null) solverOverrides.end_time = endTime;
    const writeInterval = getOptionalInt('cfg-override-solver-writeinterval');
    if (writeInterval !== null) solverOverrides.write_interval = writeInterval;
    const purgeWrite = getOptionalInt('cfg-override-solver-purgewrite');
    if (purgeWrite !== null) solverOverrides.purge_write = purgeWrite;
    if (Object.keys(solverOverrides).length > 0) overrides.solver = solverOverrides;

    // 2. Force Refs (Priority 2)
    const refsOverrides = {};
    const Aref = getOptionalFloat('cfg-override-ref-aref');
    if (Aref !== null) refsOverrides.Aref = Aref;
    const lRef = getOptionalFloat('cfg-override-ref-lref');
    if (lRef !== null) refsOverrides.lRef = lRef;
    const cofrX = getOptionalFloat('cfg-override-ref-cofr-x');
    const cofrY = getOptionalFloat('cfg-override-ref-cofr-y');
    const cofrZ = getOptionalFloat('cfg-override-ref-cofr-z');
    if (cofrX !== null || cofrY !== null || cofrZ !== null) {
      refsOverrides.CofR = [cofrX ?? 0.0, cofrY ?? 0.0, cofrZ ?? 0.0];
    }
    if (Object.keys(refsOverrides).length > 0) overrides.force_refs = refsOverrides;

    // 3. Mesh Params (Priority 3)
    const meshOverrides = {};
    const baseCell = getOptionalFloat('cfg-override-basecell');
    if (baseCell !== null) meshOverrides.base_cell_size = baseCell;
    const surfMin = getOptionalInt('cfg-override-surf-min');
    const surfMax = getOptionalInt('cfg-override-surf-max');
    if (surfMin !== null || surfMax !== null) {
      meshOverrides.surface_level = [surfMin ?? 4, surfMax ?? 5];
    }
    const edgeLevel = getOptionalInt('cfg-override-edge');
    if (edgeLevel !== null) meshOverrides.edge_level = edgeLevel;
    const nearWake = getOptionalInt('cfg-override-nearwake');
    if (nearWake !== null) meshOverrides.near_wake_level = nearWake;
    const farWake = getOptionalInt('cfg-override-farwake');
    if (farWake !== null) meshOverrides.far_wake_level = farWake;
    if (Object.keys(meshOverrides).length > 0) overrides.mesh_params = meshOverrides;

    // 4. Boundary Layers (Priority 4)
    const layersOverrides = {};
    const nLayers = getOptionalInt('cfg-override-layer-nlayers');
    if (nLayers !== null) layersOverrides.n_layers = nLayers;
    const expansionRatio = getOptionalFloat('cfg-override-layer-expansion');
    if (expansionRatio !== null) layersOverrides.expansion_ratio = expansionRatio;
    const firstLayer = getOptionalFloat('cfg-override-layer-firstlayer');
    if (firstLayer !== null) layersOverrides.first_layer_thickness = firstLayer;
    const minThickness = getOptionalFloat('cfg-override-layer-minthickness');
    if (minThickness !== null) layersOverrides.min_thickness = minThickness;
    if (Object.keys(layersOverrides).length > 0) overrides.layers = layersOverrides;

    // 5. Fluid (Priority 5)
    const fluidOverrides = {};
    const rho = getOptionalFloat('cfg-override-fluid-rho');
    if (rho !== null) fluidOverrides.rho = rho;
    const nu = getOptionalFloat('cfg-override-fluid-nu');
    if (nu !== null) fluidOverrides.nu = nu;
    if (Object.keys(fluidOverrides).length > 0) overrides.fluid = fluidOverrides;

    // 6. Turbulence (Priority 6)
    const turbOverrides = {};
    const turbModel = getOptionalStr('cfg-override-turb-model');
    if (turbModel) turbOverrides.model = turbModel;
    const turbIntensity = getOptionalFloat('cfg-override-turb-intensity');
    if (turbIntensity !== null) turbOverrides.intensity = turbIntensity;
    const nutRatio = getOptionalFloat('cfg-override-turb-nut-ratio');
    if (nutRatio !== null) turbOverrides.nut_ratio = nutRatio;
    if (Object.keys(turbOverrides).length > 0) overrides.turbulence = turbOverrides;

    if (Object.keys(overrides).length > 0) {
      cfg.overrides = overrides;
      delete cfg._comment_overrides;
      delete cfg._optional_overrides_example;
    } else {
      delete cfg.overrides;
      cfg._comment_overrides = "Expert overrides — all fields below have built-in defaults in fidelity presets. Uncomment only if manual tuning is needed.";
      cfg._optional_overrides_example = {
        solver: {
          _end_time: 800,
          _write_interval: 400,
          _purge_write: 2,
        },
        force_refs: {
          _Aref: 1.0,
          _lRef: 1.0,
          _CofR: [0.0, 0.0, 0.0],
        },
        mesh_params: {
          _base_cell_size: 0.10,
          _surface_level: [4, 5],
          _edge_level: 6,
          _near_wake_level: 3,
          _far_wake_level: 1,
        },
        layers: {
          _n_layers: 5,
          _expansion_ratio: 1.2,
          _first_layer_thickness: 0.3,
          _min_thickness: 0.05,
        },
        fluid: {
          _rho: 1.225,
          _nu: 1.516e-5,
        },
        turbulence: {
          _model: "kOmegaSST",
          _intensity: 0.005,
          _nut_ratio: 10,
        },
      };
    }

    this.activeConfig = cfg;
    this.syncConfigToJsonDrawer();
    this.updateDomainBoxVisualization();
  }

  syncConfigToJsonDrawer() {
    const editor = document.getElementById('raw-json-editor');
    if (editor && document.activeElement !== editor) {
      editor.value = JSON.stringify(this.activeConfig, null, 4);
      this.setJsonStatus('JSON Valid & Synced', true);
    }
  }

  bindJsonDrawer() {
    const drawer = document.getElementById('json-drawer');
    const toggleBtn = document.getElementById('btn-toggle-json-drawer');
    const closeBtn = document.getElementById('btn-close-drawer');
    const copyBtn = document.getElementById('btn-copy-json');
    const applyBtn = document.getElementById('btn-apply-json');
    const editor = document.getElementById('raw-json-editor');

    toggleBtn?.addEventListener('click', () => drawer?.classList.toggle('open'));
    closeBtn?.addEventListener('click', () => drawer?.classList.remove('open'));

    copyBtn?.addEventListener('click', () => {
      if (editor) {
        navigator.clipboard.writeText(editor.value);
        this.showToast('JSON copied to clipboard', 'info');
      }
    });

    applyBtn?.addEventListener('click', () => this.applyJsonFromDrawer(true));

    // Real-time live auto-sync: automatically update visual form as user edits JSON
    let debounceTimer = null;
    editor?.addEventListener('input', () => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        this.applyJsonFromDrawer(false); // live sync without popup toast spam
      }, 250);
    });

    // Immediate sync on blur (e.g. clicking outside or switching focus)
    editor?.addEventListener('blur', () => {
      clearTimeout(debounceTimer);
      this.applyJsonFromDrawer(false);
    });
  }

  applyJsonFromDrawer(showToast = false) {
    const editor = document.getElementById('raw-json-editor');
    if (!editor) return;

    try {
      const parsed = JSON.parse(editor.value);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('Config root must be a JSON object');
      }

      this.isSyncingFromJson = true;
      this.activeConfig = parsed;
      this.updateVisualFormFromConfig(parsed);
      this.updateDomainBoxVisualization();
      this.isSyncingFromJson = false;

      this.setJsonStatus('Live Synced with Form', true);
      if (showToast) {
        // Pretty-format valid JSON on manual click
        editor.value = JSON.stringify(parsed, null, 4);
        this.showToast('Visual form updated from JSON', 'success');
      }
    } catch (err) {
      this.isSyncingFromJson = false;
      this.setJsonStatus(`Syntax Error: ${err.message}`, false);
      if (showToast) {
        this.showToast(`Invalid JSON: ${err.message}`, 'error');
      }
    }
  }

  setJsonStatus(text, ok) {
    const el = document.getElementById('json-parse-status');
    if (el) {
      el.textContent = text;
      el.className = `json-status ${ok ? 'ok' : 'error'}`;
    }
  }

  // -------------------------------------------------------------
  // STL Upload & Management
  // -------------------------------------------------------------
  bindSTLUpload() {
    const input = document.getElementById('stl-file-input');
    const placeholder = document.getElementById('viewer-empty-msg');
    const container = document.getElementById('stl-viewer-container');

    input?.addEventListener('change', (e) => {
      const files = e.target.files;
      if (files && files.length > 0) {
        this.handleSTLFiles(Array.from(files));
      }
    });

    // Drag and drop into 3D viewer
    ['dragenter', 'dragover'].forEach((eventName) => {
      container?.addEventListener(eventName, (e) => {
        e.preventDefault();
        placeholder?.classList.add('dragover');
      });
    });

    ['dragleave', 'drop'].forEach((eventName) => {
      container?.addEventListener(eventName, (e) => {
        e.preventDefault();
        placeholder?.classList.remove('dragover');
      });
    });

    container?.addEventListener('drop', (e) => {
      const files = e.dataTransfer.files;
      if (files && files.length > 0) {
        this.handleSTLFiles(Array.from(files));
      }
    });

    // 3D Viewer overlay tool checkboxes
    document.getElementById('chk-show-axes')?.addEventListener('change', (e) => {
      this.viewer.toggleAxes(e.target.checked);
    });
    document.getElementById('chk-show-domain')?.addEventListener('change', (e) => {
      this.viewer.toggleDomain(e.target.checked);
    });
    document.getElementById('chk-show-bounds')?.addEventListener('change', (e) => {
      this.viewer.toggleBounds(e.target.checked);
    });
    document.getElementById('chk-show-ground')?.addEventListener('change', (e) => {
      this.viewer.toggleGround(e.target.checked);
    });
    document.getElementById('chk-show-flow')?.addEventListener('change', (e) => {
      this.viewer.toggleFlow(e.target.checked);
    });

    // Framing buttons: Fit Domain vs Fit Model
    const btnFitDomain = document.getElementById('btn-fit-domain');
    const btnFitModel = document.getElementById('btn-fit-model');

    btnFitDomain?.addEventListener('click', () => {
      this.viewer.fitView('domain');
      btnFitDomain.classList.add('active');
      btnFitModel?.classList.remove('active');
    });

    btnFitModel?.addEventListener('click', () => {
      this.viewer.fitView('model');
      btnFitModel.classList.add('active');
      btnFitDomain?.classList.remove('active');
    });

    // Camera Reset / Recenter
    document.getElementById('btn-reset-camera')?.addEventListener('click', () => {
      this.viewer.resetCamera();
    });

    // Camera angle presets (Iso, Top, Side, Front)
    document.querySelectorAll('.btn-view-angle').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        const angle = e.target.dataset.angle;
        this.viewer.setViewAngle(angle);
      });
    });

    // Maximize / Expand 3D Viewer Area
    const expandBtn = document.getElementById('btn-toggle-expand-viewer');
    const viewerPanel = document.getElementById('viewer-panel');
    if (expandBtn && viewerPanel) {
      expandBtn.addEventListener('click', () => {
        const isMaximized = viewerPanel.classList.toggle('maximized');
        expandBtn.textContent = isMaximized ? 'Collapse' : 'Expand';
        expandBtn.className = isMaximized ? 'btn btn-primary btn-xs' : 'btn btn-secondary btn-xs';
        setTimeout(() => this.viewer.onResize(), 150);
      });
    }
  }

  updateOverridePlaceholders(fidelity = 'standard') {
    const presets = {
      fast: { base_cell: '0.15', surf_min: '3', surf_max: '4', edge: '5', nearwake: '3', farwake: '1', endtime: '800', writeint: '400', n_layers: '3', expansion: '1.30', first_layer: '0.40' },
      standard: { base_cell: '0.10', surf_min: '4', surf_max: '5', edge: '6', nearwake: '3', farwake: '1', endtime: '1500', writeint: '500', n_layers: '5', expansion: '1.20', first_layer: '0.30' },
      fine: { base_cell: '0.06', surf_min: '5', surf_max: '6', edge: '7', nearwake: '3', farwake: '1', endtime: '2500', writeint: '500', n_layers: '6', expansion: '1.15', first_layer: '0.20' },
    };
    const p = presets[fidelity] || presets.standard;

    const setPlaceholder = (id, text) => {
      const el = document.getElementById(id);
      if (el) el.placeholder = text;
    };

    setPlaceholder('cfg-override-basecell', `Auto / Preset (${p.base_cell})`);
    setPlaceholder('cfg-override-surf-min', `Min (${p.surf_min})`);
    setPlaceholder('cfg-override-surf-max', `Max (${p.surf_max})`);
    setPlaceholder('cfg-override-edge', `Auto / Preset (${p.edge})`);
    setPlaceholder('cfg-override-nearwake', `Auto / Preset (${p.nearwake})`);
    setPlaceholder('cfg-override-farwake', `Auto / Preset (${p.farwake})`);
    setPlaceholder('cfg-override-solver-endtime', `Auto / Preset (${p.endtime})`);
    setPlaceholder('cfg-override-solver-writeinterval', `Auto / Preset (${p.writeint})`);
    setPlaceholder('cfg-override-layer-nlayers', `Auto / Preset (${p.n_layers})`);
    setPlaceholder('cfg-override-layer-expansion', `Auto / Preset (${p.expansion})`);
    setPlaceholder('cfg-override-layer-firstlayer', `Auto / Preset (${p.first_layer})`);
  }

  clearAllOverrides() {
    const overrideIds = [
      'cfg-override-solver-endtime',
      'cfg-override-solver-writeinterval',
      'cfg-override-solver-purgewrite',
      'cfg-override-ref-aref',
      'cfg-override-ref-lref',
      'cfg-override-ref-cofr-x',
      'cfg-override-ref-cofr-y',
      'cfg-override-ref-cofr-z',
      'cfg-override-basecell',
      'cfg-override-surf-min',
      'cfg-override-surf-max',
      'cfg-override-edge',
      'cfg-override-nearwake',
      'cfg-override-farwake',
      'cfg-override-layer-nlayers',
      'cfg-override-layer-expansion',
      'cfg-override-layer-firstlayer',
      'cfg-override-layer-minthickness',
      'cfg-override-fluid-rho',
      'cfg-override-fluid-nu',
      'cfg-override-turb-model',
      'cfg-override-turb-intensity',
      'cfg-override-turb-nut-ratio',
    ];
    overrideIds.forEach((id) => this.setVal(id, ''));
    this.buildConfigFromVisualForm();
    this.showToast('All overrides cleared. Falling back to fidelity presets & defaults.', 'info');
  }

  async autoSymmetryPlaneCenter() {
    let center = null;
    let axisName = 'X';

    // 1. Try local Three.js or cached bounds
    let bounds = this.currentSTLBounds;
    if (!bounds && this.viewer && this.viewer.currentMesh) {
      const geo = this.viewer.currentMesh.geometry;
      if (geo && geo.boundingBox) {
        bounds = {
          min: [geo.boundingBox.min.x, geo.boundingBox.min.y, geo.boundingBox.min.z],
          max: [geo.boundingBox.max.x, geo.boundingBox.max.y, geo.boundingBox.max.z],
        };
      }
    }

    if (bounds && bounds.min && bounds.max) {
      const flowDir = this.activeConfig.flow?.direction || '-z';
      const dfAxis = this.activeConfig.outputs?.downforce_axis || '-y';
      const axisToIdx = { 'x': 0, 'y': 1, 'z': 2 };
      const flowIdx = axisToIdx[flowDir.replace(/^[+-]/, '').toLowerCase()] ?? 2;
      const upIdx = axisToIdx[dfAxis.replace(/^[+-]/, '').toLowerCase()] ?? 1;
      const lateralIdx = [0, 1, 2].find((i) => i !== flowIdx && i !== upIdx) ?? 0;
      axisName = ['X', 'Y', 'Z'][lateralIdx];

      const minVal = bounds.min[lateralIdx];
      const maxVal = bounds.max[lateralIdx];
      center = (minVal + maxVal) / 2.0;
      center = Math.round(center * 10000) / 10000;
      if (Math.abs(center) < 0.0001) center = 0.0;
    } else {
      // 2. Fetch from backend domain-box endpoint
      try {
        const res = await fetch('/api/geometry/domain-box', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ config: this.activeConfig }),
        });
        if (res.ok) {
          const data = await res.json();
          if (data.auto_symmetry_plane !== undefined) {
            center = data.auto_symmetry_plane;
            axisName = (data.lateral_axis || 'x').toUpperCase();
          }
        }
      } catch (err) {
        console.warn('Could not derive symmetry plane center:', err);
      }
    }

    if (center === null || isNaN(center)) {
      this.showToast('Could not calculate symmetry center (no geometry loaded)', 'warning');
      return;
    }

    // Apply to input and active config
    this.setVal('cfg-symmetry-plane', center);
    this.activeConfig.symmetry_plane = center;
    this.buildConfigFromVisualForm();

    this.showToast(`Auto Symmetry Plane set to center (${axisName} = ${center} m)`, 'success');
  }

  async updateDomainBoxVisualization(autoFit = false) {
    if (!this.viewer) return;
    try {
      const res = await fetch('/api/geometry/domain-box', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          config: this.activeConfig,
          bounds: this.currentSTLBounds || null,
        }),
      });
      if (!res.ok) return;
      const data = await res.json();
      if (data.domain_box && data.domain_box.min && data.domain_box.max) {
        const faces = this.activeConfig.domain_faces || {};
        const hasSymmetry = Object.values(faces).some((f) => String(f).toLowerCase().includes('symmetry'));
        const sym = (hasSymmetry && this.activeConfig.symmetry_plane !== undefined && this.activeConfig.symmetry_plane !== null)
          ? Number(this.activeConfig.symmetry_plane)
          : null;
        const flowDir = this.activeConfig.flow?.direction || '-z';
        const upAxis = (this.activeConfig.outputs?.downforce_axis || '-y').replace(/^[+-]/, '').toLowerCase();
        const flowAxisName = flowDir.replace(/^[+-]/, '').toLowerCase();
        const latAxis = ['x', 'y', 'z'].find((a) => a !== flowAxisName && a !== upAxis) || 'x';
        this.viewer.updateDomainBox(data.domain_box.min, data.domain_box.max, sym, flowDir, upAxis, latAxis);
        if (autoFit) {
          this.viewer.fitView('domain');
          const btnFitDomain = document.getElementById('btn-fit-domain');
          const btnFitModel = document.getElementById('btn-fit-model');
          btnFitDomain?.classList.add('active');
          btnFitModel?.classList.remove('active');
        }
      }
    } catch (err) {
      console.warn('Error updating domain box visualization:', err);
    }
  }

  isGenericPlaceholder(filename) {
    // 'geometry.stl' only acts as a placeholder when no such file actually
    // exists on the server. If it is a real uploaded/example geometry it must
    // be rendered like any other STL.
    if (filename !== 'geometry.stl') return false;
    const list = Array.isArray(this.availableSTLs) ? this.availableSTLs : null;
    if (list === null) return false; // availability unknown -> never hide
    return !list.some((s) => s.filename === filename);
  }

  async loadAllActiveSTLsFromServer(autoFit = true) {
    if (!this.viewer) return;

    const stlsToLoad = (this.activeConfig.stl_files || []).filter(
      (f) => f && !this.isGenericPlaceholder(f)
    );

    if (stlsToLoad.length === 0) {
      this.viewer.clearSTLs();
      this.currentSTLBounds = null;
      await this.updateDomainBoxVisualization(true);
      return;
    }

    this.viewer.clearSTLs();

    for (const filename of stlsToLoad) {
      try {
        const res = await fetch(`/api/stl/file/${encodeURIComponent(filename)}`);
        if (!res.ok) continue;
        const buffer = await res.arrayBuffer();
        this.viewer.addSTLFromArrayBuffer(buffer, filename);
      } catch (err) {
        console.warn(`Could not load STL '${filename}' from server:`, err);
      }
    }

    this.currentSTLBounds = this.viewer.getCombinedBoundingBox();
    await this.updateDomainBoxVisualization(autoFit);
  }

  async handleSTLFiles(files) {
    const stlFiles = Array.from(files).filter((f) => f.name.toLowerCase().endsWith('.stl'));
    if (stlFiles.length === 0) {
      this.showToast('Please select valid .stl files', 'warning');
      return;
    }

    let loadedCount = 0;
    for (const file of stlFiles) {
      try {
        let targetFilename = file.name;

        // Check if file already exists locally or on cluster
        let checkRes = null;
        try {
          const cRes = await fetch(`/api/stl/check-exists?filename=${encodeURIComponent(file.name)}`);
          if (cRes.ok) checkRes = await cRes.json();
        } catch (e) {
          console.warn('Could not check STL existence:', e);
        }

        if (checkRes && checkRes.exists) {
          const loc = checkRes.local_exists && checkRes.cluster_exists
            ? 'locally and on cluster'
            : checkRes.cluster_exists ? 'on the remote cluster' : 'in the local library';

          const dotIdx = file.name.lastIndexOf('.');
          const stem = dotIdx > 0 ? file.name.slice(0, dotIdx) : file.name;
          const ext = dotIdx > 0 ? file.name.slice(dotIdx) : '.stl';
          const suggestedName = `${stem}_v2${ext}`;

          const decision = await this.showConfirmDialog({
            title: `⚠️ STL File Already Exists: ${file.name}`,
            message: `A geometry file named "${file.name}" already exists ${loc}.`,
            warning: `Overwriting will replace the surface geometry for all future simulation cases referencing this filename.`,
            severity: 'warning',
            allowRename: true,
            suggestedName: suggestedName,
            confirmText: 'Overwrite File',
            confirmClass: 'btn-warning',
            cancelText: 'Skip / Cancel',
          });

          if (decision.action === 'cancel') {
            this.showToast(`Upload skipped for ${file.name}`, 'info');
            continue;
          }

          if (decision.action === 'rename' && decision.newName) {
            targetFilename = decision.newName;
            if (!targetFilename.toLowerCase().endsWith('.stl')) {
              targetFilename += '.stl';
            }
          }
        }

        // 1. Preview in Three.js locally via ArrayBuffer without overwriting other parts
        const buffer = await file.arrayBuffer();
        if (this.viewer) {
          this.viewer.addSTLFromArrayBuffer(buffer, targetFilename);
        }
        loadedCount++;

        // 2. Upload to server stl/ directory
        const formData = new FormData();
        formData.append('file', file);
        if (targetFilename !== file.name) {
          formData.append('override_name', targetFilename);
        }
        const res = await fetch('/api/stl/upload', {
          method: 'POST',
          body: formData,
        });
        const data = await res.json();
        if (data.success) {
          if (!this.activeConfig.stl_files) this.activeConfig.stl_files = [];
          // Drop the generic placeholder only when it is not a real file
          this.activeConfig.stl_files = this.activeConfig.stl_files.filter(
            (f) => !this.isGenericPlaceholder(f)
          );
          if (!this.activeConfig.stl_files.includes(targetFilename)) {
            this.activeConfig.stl_files.push(targetFilename);
          }
        }
      } catch (err) {
        console.error(`Error processing STL file ${file.name}:`, err);
        this.showToast(`Error processing ${file.name}: ${err.message}`, 'error');
      }
    }

    // 3. Recompute domain and update UI once all STLs are loaded
    if (this.viewer) {
      this.currentSTLBounds = this.viewer.getCombinedBoundingBox();
    }
    this.renderActiveSTLChips(this.activeConfig.stl_files);
    this.syncConfigToJsonDrawer();
    await this.updateDomainBoxVisualization(true);
    if (loadedCount > 0) {
      this.showToast(`Loaded ${loadedCount} STL ${loadedCount === 1 ? 'geometry' : 'geometries'} into 3D viewer`, 'success');
    }
  }

  renderActiveSTLChips(stlList) {
    const container = document.getElementById('stl-chip-list');
    const countBadge = document.getElementById('active-stl-count');
    if (!container) return;

    container.innerHTML = '';
    if (countBadge) countBadge.textContent = `${stlList.length} files`;

    const STL_CHIP_COLORS = [
      '#38bdf8', '#34d399', '#f472b6', '#a78bfa',
      '#fbbf24', '#2dd4bf', '#f87171', '#60a5fa'
    ];

    stlList.forEach((filename, idx) => {
      const color = STL_CHIP_COLORS[idx % STL_CHIP_COLORS.length];
      const chip = document.createElement('div');
      chip.className = 'stl-chip active';
      chip.style.borderColor = `${color}55`;
      chip.innerHTML = `
        <span class="stl-chip-dot" style="background: ${color}; width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px;"></span>
        <span>${filename}</span>
        <span class="stl-chip-remove btn-remove" title="Remove">&times;</span>
      `;

      // Clicking chip highlights part in 3D viewer
      chip.addEventListener('click', (e) => {
        if (!e.target.classList.contains('btn-remove')) {
          if (this.viewer) {
            this.viewer.highlightSTL(filename);
          }
        }
      });

      // Removing chip deletes from 3D scene and config
      chip.querySelector('.btn-remove').addEventListener('click', (e) => {
        e.stopPropagation();
        this.activeConfig.stl_files = this.activeConfig.stl_files.filter((f) => f !== filename);
        if (this.viewer) {
          this.viewer.removeSTL(filename);
          this.currentSTLBounds = this.viewer.getCombinedBoundingBox();
        }
        this.renderActiveSTLChips(this.activeConfig.stl_files);
        this.syncConfigToJsonDrawer();
        this.updateDomainBoxVisualization(false);
        this.showToast(`Removed ${filename}`, 'info');
      });

      container.appendChild(chip);
    });
  }

  async loadExistingSTLs() {
    try {
      const res = await fetch('/api/stl/list');
      const stls = await res.json();
      this.availableSTLs = stls || [];
      if (stls && stls.length > 0) {
        const availableNames = new Set(stls.map((s) => s.filename));
        const validActive = (this.activeConfig?.stl_files || []).filter((f) => availableNames.has(f));
        if (validActive.length > 0) {
          this.activeConfig.stl_files = validActive;
        } else {
          this.activeConfig.stl_files = [stls[0].filename];
        }
        this.renderActiveSTLChips(this.activeConfig.stl_files);
        await this.loadAllActiveSTLsFromServer(true);
      } else {
        // No STLs available in stl/ directory
        this.activeConfig.stl_files = [];
        this.renderActiveSTLChips([]);
        await this.updateDomainBoxVisualization(true);
      }
    } catch {}
  }

  // -------------------------------------------------------------
  // Template & Config Loading
  // -------------------------------------------------------------
  async loadTemplatesList() {
    try {
      const res = await fetch('/api/config/templates');
      const templates = await res.json();
      const select = document.getElementById('select-template');
      if (select && templates.length > 0) {
        select.innerHTML = '';
        templates.forEach((t) => {
          const opt = document.createElement('option');
          opt.value = t.filename;
          opt.textContent = `configs/${t.filename}`;
          select.appendChild(opt);
        });
      }
    } catch {}
  }

  async loadConfigFile(filename, quiet = false) {
    try {
      const res = await fetch(`/api/config/load-file?filename=${encodeURIComponent(filename)}`);
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      this.activeConfig = data.raw_config;
      this.updateVisualFormFromConfig(this.activeConfig);
      this.syncConfigToJsonDrawer();
      const select = document.getElementById('select-template');
      if (select) select.value = filename;

      if (this.activeConfig.stl_files && this.activeConfig.stl_files.length > 0) {
        this.renderActiveSTLChips(this.activeConfig.stl_files);
        await this.loadAllActiveSTLsFromServer(true);
      } else {
        if (this.viewer) this.viewer.clearSTLs();
        this.currentSTLBounds = null;
        await this.updateDomainBoxVisualization(true);
      }

      if (!quiet) this.showToast(`Loaded template ${filename}`, 'success');
    } catch (err) {
      if (!quiet) this.showToast(`Failed to load config: ${err.message}`, 'error');
    }
  }

  // -------------------------------------------------------------
  // Validation & Case Submission
  // -------------------------------------------------------------
  async validateCurrentConfig() {
    this.buildConfigFromVisualForm();
    try {
      const res = await fetch('/api/case/generate-and-submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          config: this.activeConfig,
          upload_to_cluster: false,
          generate_remotely: false,
          submit_slurm: false,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Validation failed');
      this.showToast('Configuration valid. Ready to generate.', 'success');
      if (data.warnings && data.warnings.length > 0) {
        data.warnings.forEach((w) => this.showToast(w, 'info'));
      }
    } catch (err) {
      this.showToast(err.message, 'error');
    }
  }

  async generateCaseLocally() {
    this.buildConfigFromVisualForm();
    const caseName = this.activeConfig.case_name || 'my_case';

    // Check if case already exists locally or on cluster
    const check = await this.checkCaseNameExists(caseName);
    if (check && check.exists) {
      const decision = await this.showConfirmDialog({
        title: `⚠️ Case Already Exists: "${caseName}"`,
        message: `A case named "${caseName}" was found in your local cases/ directory or configs.`,
        warning: `Generating locally will overwrite the existing case directory and system dictionaries in cases/${caseName}/.`,
        severity: 'warning',
        allowRename: false,
        confirmText: 'Overwrite Case',
        confirmClass: 'btn-warning',
        cancelText: 'Cancel & Change Name',
      });

      if (decision.action === 'cancel') {
        this.showToast('Generation cancelled. Please update Case Name.', 'info');
        const caseInput = document.getElementById('cfg-case-name');
        if (caseInput) {
          caseInput.focus();
          caseInput.select();
        }
        return;
      }
    }

    const btnGenLocal = document.getElementById('btn-generate-local');
    if (btnGenLocal) {
      btnGenLocal.disabled = true;
      btnGenLocal.textContent = 'Generating...';
    }
    try {
      const res = await fetch('/api/case/generate-and-submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          config: this.activeConfig,
          upload_to_cluster: false,
          generate_remotely: false,
          submit_slurm: false,
          generate_locally: true,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Generation failed');
      this.showToast(`Case '${data.case_name}' generated locally in cases/`, 'success');
      await this.loadCasesArchive();
    } catch (err) {
      this.showToast(`Generation error: ${err.message}`, 'error');
    } finally {
      if (btnGenLocal) {
        btnGenLocal.disabled = false;
        btnGenLocal.textContent = 'Generate Locally';
      }
    }
  }

  async saveCurrentConfig(submitToCluster = false) {
    this.buildConfigFromVisualForm();

    if (submitToCluster && !this.clusterConnected) {
      this.showToast('Please connect to the cluster via SSH first!', 'error');
      this.openSSHModal();
      return;
    }

    const caseName = this.activeConfig.case_name || 'my_case';

    // Check if case already exists locally or on cluster
    const check = await this.checkCaseNameExists(caseName);
    if (check && check.exists) {
      let warningMsg = `A case named "${caseName}" already exists. Overwriting will replace existing case dictionaries, mesh setup, and simulation logs.`;
      let severity = 'warning';
      let confirmBtnText = 'Overwrite & Submit';
      let confirmBtnClass = 'btn-warning';

      if (check.is_running) {
        warningMsg = `🚨 CRITICAL: Case "${caseName}" is currently RUNNING or PENDING on the SLURM cluster! Overwriting now may crash or corrupt the active simulation run.`;
        severity = 'danger';
        confirmBtnText = 'Force Overwrite';
        confirmBtnClass = 'btn-danger-solid';
      }

      const decision = await this.showConfirmDialog({
        title: `⚠️ Case Already Exists: "${caseName}"`,
        message: `Case "${caseName}" was found ${check.cluster_exists && check.local_exists ? 'both locally and on the cluster' : check.cluster_exists ? 'on the remote cluster' : 'in local storage'}.`,
        warning: warningMsg,
        severity: severity,
        allowRename: false,
        confirmText: confirmBtnText,
        confirmClass: confirmBtnClass,
        cancelText: 'Cancel & Change Name',
      });

      if (decision.action === 'cancel') {
        this.showToast('Submission cancelled. Please update Case Name.', 'info');
        const caseInput = document.getElementById('cfg-case-name');
        if (caseInput) {
          caseInput.focus();
          caseInput.select();
        }
        return;
      }
    }

    const btnSubmit = document.getElementById('btn-submit-case');
    if (btnSubmit) {
      btnSubmit.disabled = true;
      btnSubmit.textContent = submitToCluster ? 'Submitting to Cluster...' : 'Saving...';
    }

    try {
      const res = await fetch('/api/case/generate-and-submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          config: this.activeConfig,
          upload_to_cluster: submitToCluster,
          generate_remotely: submitToCluster,
          submit_slurm: submitToCluster,
          generate_locally: !submitToCluster,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Request failed');

      if (submitToCluster) {
        const slurmRes = data.cluster_actions?.slurm_submit;
        if (slurmRes && slurmRes.job_id) {
          this.showToast(`Simulation submitted. SLURM Job ID: ${slurmRes.job_id}`, 'success');
        } else {
          this.showToast(`Generated case ${data.case_name} on cluster!`, 'success');
        }
        // Switch to Telemetry tab to monitor
        document.getElementById('tab-btn-telemetry')?.click();
        this.addTelemetryCase(data.case_name);
        this.refreshQueue();
      } else {
        this.showToast(`Config saved locally for ${data.case_name}`, 'success');
      }
    } catch (err) {
      this.showToast(`Error: ${err.message}`, 'error');
    } finally {
      if (btnSubmit) {
        btnSubmit.disabled = false;
        btnSubmit.textContent = 'Launch Simulation on Cluster';
      }
    }
  }

  // -------------------------------------------------------------
  // Reusable Confirmation Modal
  // -------------------------------------------------------------
  showConfirmDialog({
    title = '⚠️ Confirmation Required',
    message = '',
    warning = '',
    severity = 'warning',
    allowRename = false,
    suggestedName = '',
    confirmText = 'Overwrite',
    confirmClass = 'btn-warning',
    cancelText = 'Cancel',
  } = {}) {
    return new Promise((resolve) => {
      const modal = document.getElementById('confirm-modal');
      const titleEl = document.getElementById('confirm-modal-title');
      const msgEl = document.getElementById('confirm-modal-message');
      const alertEl = document.getElementById('confirm-modal-alert');
      const renameBox = document.getElementById('confirm-modal-rename-container');
      const renameInput = document.getElementById('confirm-modal-new-name');
      const renameBtn = document.getElementById('btn-confirm-rename');
      const proceedBtn = document.getElementById('btn-confirm-proceed');
      const cancelBtn = document.getElementById('btn-confirm-cancel');
      const closeBtn = document.getElementById('btn-close-confirm-modal');
      const backdrop = document.getElementById('confirm-modal-backdrop');

      if (!modal) {
        const res = window.confirm(`${title}\n\n${warning ? warning + '\n\n' : ''}${message}`);
        resolve({ action: res ? 'overwrite' : 'cancel' });
        return;
      }

      if (titleEl) titleEl.textContent = title;
      if (msgEl) msgEl.textContent = message;

      if (alertEl) {
        if (warning) {
          alertEl.textContent = warning;
          alertEl.className = `alert alert-${severity}`;
          alertEl.style.display = 'block';
        } else {
          alertEl.style.display = 'none';
        }
      }

      if (renameBox) {
        if (allowRename) {
          renameBox.style.display = 'block';
          if (renameInput) renameInput.value = suggestedName || '';
        } else {
          renameBox.style.display = 'none';
        }
      }

      if (proceedBtn) {
        proceedBtn.textContent = confirmText;
        proceedBtn.className = `btn ${confirmClass}`;
      }
      if (cancelBtn) {
        cancelBtn.textContent = cancelText;
      }

      const cleanup = () => {
        modal.style.display = 'none';
        if (proceedBtn) proceedBtn.onclick = null;
        if (cancelBtn) cancelBtn.onclick = null;
        if (closeBtn) closeBtn.onclick = null;
        if (backdrop) backdrop.onclick = null;
        if (renameBtn) renameBtn.onclick = null;
      };

      if (proceedBtn) {
        proceedBtn.onclick = () => {
          cleanup();
          resolve({ action: 'overwrite' });
        };
      }

      if (allowRename && renameBtn) {
        renameBtn.onclick = () => {
          const val = renameInput ? renameInput.value.trim() : '';
          if (!val) {
            this.showToast('Please enter a valid new name', 'warning');
            return;
          }
          cleanup();
          resolve({ action: 'rename', newName: val });
        };
      }

      if (cancelBtn) {
        cancelBtn.onclick = () => {
          cleanup();
          resolve({ action: 'cancel' });
        };
      }

      if (closeBtn) {
        closeBtn.onclick = () => {
          cleanup();
          resolve({ action: 'cancel' });
        };
      }

      if (backdrop) {
        backdrop.onclick = () => {
          cleanup();
          resolve({ action: 'cancel' });
        };
      }

      modal.style.display = 'flex';
      if (allowRename && renameInput) {
        setTimeout(() => {
          renameInput.focus();
          renameInput.select();
        }, 80);
      }
    });
  }

  async checkCaseNameExists(caseName) {
    if (!caseName) return null;
    try {
      const res = await fetch(`/api/case/check-exists?case_name=${encodeURIComponent(caseName)}`);
      if (res.ok) return await res.json();
    } catch (err) {
      console.warn('Could not check case existence:', err);
    }
    return null;
  }

  // -------------------------------------------------------------
  // SSH Cluster Connection Modal
  // -------------------------------------------------------------
  bindSSHModal() {
    const openBtn = document.getElementById('btn-open-ssh-modal');
    const closeBtn = document.getElementById('btn-close-ssh-modal');
    const cancelBtn = document.getElementById('btn-cancel-ssh');
    const backdrop = document.getElementById('modal-backdrop');
    const connectSubmitBtn = document.getElementById('btn-connect-ssh-submit');
    const reconnectBtn = document.getElementById('btn-reconnect-ssh');

    openBtn?.addEventListener('click', () => this.openSSHModal());
    closeBtn?.addEventListener('click', () => this.closeSSHModal());
    cancelBtn?.addEventListener('click', () => this.closeSSHModal());
    backdrop?.addEventListener('click', () => this.closeSSHModal());
    reconnectBtn?.addEventListener('click', () => this.openSSHModal());

    connectSubmitBtn?.addEventListener('click', () => this.submitSSHConnect());

    document.getElementById('btn-refresh-queue')?.addEventListener('click', () => {
      if (!this.clusterConnected) {
        this.showToast('Not connected to a cluster', 'warning');
        return;
      }
      this.refreshQueue();
    });
  }

  async loadLocalClusterConfig() {
    let cfg = null;
    try {
      const res = await fetch('/api/cluster/saved-config');
      if (res.ok) {
        cfg = await res.json();
      }
    } catch {}

    if (cfg) {
      if (cfg.host) this.setValText('disp-cluster-host', cfg.host);
      if (cfg.username) this.setValText('disp-cluster-user', cfg.username);
      if (cfg.remote_repo_path) this.setValText('disp-remote-repo', cfg.remote_repo_path);

      this.setVal('ssh-host', cfg.host || '');
      this.setVal('ssh-username', cfg.username || '');
      this.setVal('ssh-remotepath', cfg.remote_repo_path || '');
      this.setVal('ssh-keypath', cfg.key_path || '');

      const pwHint = document.getElementById('ssh-saved-pw-hint');
      if (pwHint) {
        pwHint.style.display = cfg.has_saved_password ? 'block' : 'none';
      }
    } else {
      this.setValText('disp-cluster-host', 'Not Configured');
      this.setValText('disp-cluster-user', '--');
      this.setValText('disp-remote-repo', '--');
    }
    return cfg;
  }

  async openSSHModal() {
    const modal = document.getElementById('ssh-modal');
    if (!modal) return;

    await this.loadLocalClusterConfig();

    document.getElementById('ssh-error-alert').style.display = 'none';
    document.getElementById('ssh-success-alert').style.display = 'none';
    modal.style.display = 'flex';
  }

  closeSSHModal() {
    const modal = document.getElementById('ssh-modal');
    if (modal) modal.style.display = 'none';
  }

  async submitSSHConnect() {
    const host = this.getVal('ssh-host');
    const username = this.getVal('ssh-username');
    const password = this.getVal('ssh-password');
    const keyPath = this.getVal('ssh-keypath');
    const remoteRepo = this.getVal('ssh-remotepath');
    const savePw = this.getCheck('ssh-save-pw');

    const errAlert = document.getElementById('ssh-error-alert');
    const succAlert = document.getElementById('ssh-success-alert');
    const submitBtn = document.getElementById('btn-connect-ssh-submit');

    errAlert.style.display = 'none';
    succAlert.style.display = 'none';
    submitBtn.disabled = true;
    submitBtn.textContent = 'Connecting...';

    // Update display metrics immediately
    if (host) this.setValText('disp-cluster-host', host);
    if (username) this.setValText('disp-cluster-user', username);
    if (remoteRepo) this.setValText('disp-remote-repo', remoteRepo);

    try {
      const res = await fetch('/api/cluster/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          host,
          username,
          password: password || null,
          key_path: keyPath || null,
          remote_repo_path: remoteRepo,
          save_password: savePw,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Connection failed');

      succAlert.textContent = `Connected to ${data.remote_host || host}! Python: ${data.remote_python}`;
      succAlert.style.display = 'block';
      this.updateClusterStatusBadge(true, username, host);
      this.showToast(`Connected to ${host}`, 'success');

      setTimeout(() => {
        this.closeSSHModal();
        this.refreshQueue();
      }, 1000);
    } catch (err) {
      errAlert.textContent = `Connection error: ${err.message}`;
      errAlert.style.display = 'block';
      this.updateClusterStatusBadge(false);
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Connect & Test';
    }
  }

  async checkClusterStatus() {
    try {
      const res = await fetch('/api/cluster/status');
      const data = await res.json();
      this.updateClusterStatusBadge(data.connected, data.username, data.host);
      if (data.connected) {
        this.renderQueueTable(data.active_jobs || []);
      }
    } catch {
      this.updateClusterStatusBadge(false);
    }
  }

  updateClusterStatusBadge(connected, user = '', host = '') {
    this.clusterConnected = connected;
    const badge = document.getElementById('cluster-status-badge');
    if (!badge) return;

    const dot = badge.querySelector('.status-dot');
    const text = badge.querySelector('.status-text');

    if (connected) {
      dot.className = 'status-dot connected';
      text.textContent = `${user}@${host.split('.')[0]}`;
      if (host) this.setValText('disp-cluster-host', host);
      if (user) this.setValText('disp-cluster-user', user);
      this.setValText('disp-slurm-status', 'Active & Ready');
    } else {
      dot.className = 'status-dot disconnected';
      text.textContent = 'Disconnected';
      this.setValText('disp-slurm-status', 'Disconnected');
    }
  }

  // -------------------------------------------------------------
  // SLURM Queue Monitoring
  // -------------------------------------------------------------
  async refreshQueue() {
    if (!this.clusterConnected) return;
    try {
      const res = await fetch('/api/cluster/status');
      const data = await res.json();
      this.renderQueueTable(data.active_jobs || []);
    } catch {}
  }

  renderQueueTable(jobs) {
    const tbody = document.getElementById('slurm-queue-tbody');
    if (!tbody) return;

    if (!jobs || jobs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">No active cluster jobs found.</td></tr>';
      return;
    }

    tbody.innerHTML = '';
    jobs.forEach((job) => {
      const tr = document.createElement('tr');
      const isRunning = job.state === 'RUNNING' || job.state === 'R';
      const stateBadge = isRunning
        ? '<span class="tag-running">RUNNING</span>'
        : `<span class="tag-pending">${job.state}</span>`;

      tr.innerHTML = `
        <td><strong>${job.job_id}</strong></td>
        <td>${job.name}</td>
        <td>${job.partition}</td>
        <td>${stateBadge}</td>
        <td>${job.time_used}</td>
        <td>${job.time_limit}</td>
        <td>${job.nodes}</td>
        <td><button class="btn btn-outline btn-xs btn-cancel-job" data-id="${job.job_id}">Cancel</button></td>
      `;

      tr.querySelector('.btn-cancel-job').addEventListener('click', () => {
        this.cancelJob(job.job_id);
      });

      tbody.appendChild(tr);
    });
  }

  async cancelJob(jobId) {
    const proceed = confirm(
      `Stop SLURM job ${jobId}?\n\n` +
      `If it is solving, RapidFOAM asks simpleFoam to write the current iteration ` +
      `and reconstruct the results, then force-cancels if it does not stop within 60s.`
    );
    if (!proceed) return;
    this.showToast(`Stopping job ${jobId}...`, 'info');
    try {
      const res = await fetch('/api/case/cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: jobId }),
      });
      const data = await res.json();
      if (data.success) {
        const msg = data.mode === 'graceful'
          ? `Job ${jobId} stopped gracefully; results written and reconstructed`
          : `Cancelled job ${jobId}`;
        this.showToast(msg, 'info');
        this.refreshQueue();
      } else {
        this.showToast(`Could not cancel: ${data.error || 'unknown error'}`, 'error');
      }
    } catch (err) {
      this.showToast(`Cancel error: ${err.message}`, 'error');
    }
  }

  trackDownload(caseName, seed = null) {
    // Register (or refresh) one concurrent download and make sure the shared
    // poller is running. Multiple downloads are shown as separate rows.
    const existing = this.downloadStates.get(caseName) || {};
    this.downloadStates.set(caseName, {
      case_name: caseName,
      active: true,
      done: false,
      error: null,
      files: 0,
      dirs: 0,
      bytes: 0,
      total_bytes: 0,
      ...existing,
      ...(seed || {}),
    });
    this.renderDownloads();
    this.ensureDownloadPoll();
    this.pollDownloads();
  }

  ensureDownloadPoll() {
    if (this.downloadPollTimer) return;
    this.downloadPollTimer = setInterval(() => this.pollDownloads(), 500);
  }

  stopDownloadPoll() {
    if (this.downloadPollTimer) {
      clearInterval(this.downloadPollTimer);
      this.downloadPollTimer = null;
    }
  }

  async pollDownloads() {
    if (this.downloadStates.size === 0) {
      this.stopDownloadPoll();
      this.renderDownloads();
      return;
    }
    for (const caseName of Array.from(this.downloadStates.keys())) {
      const state = this.downloadStates.get(caseName);
      if (state && state.done) continue;
      try {
        const r = await fetch(`/api/case/download/progress?case_name=${encodeURIComponent(caseName)}`);
        if (!r.ok) continue;
        const p = await r.json();
        const merged = { ...(state || {}), ...p, case_name: caseName };
        if (p && !p.active) {
          merged.done = true;
          this.downloadStates.set(caseName, merged);
          if (p.error) {
            this.showToast(`Download failed (${caseName}): ${p.error}`, 'error');
          } else {
            const mb = ((p.bytes || 0) / 1e6).toFixed(1);
            this.showToast(`Downloaded ${caseName}: ${p.files || 0} files (${mb} MB)`, 'success');
            this.loadCasesArchive();
          }
          setTimeout(() => {
            this.downloadStates.delete(caseName);
            this.renderDownloads();
            if (this.downloadStates.size === 0) this.stopDownloadPoll();
          }, 1500);
        } else {
          this.downloadStates.set(caseName, merged);
        }
      } catch (e) {
        /* transient polling error; keep going */
      }
    }
    this.renderDownloads();
  }

  renderDownloads() {
    const overlay = document.getElementById('download-progress-overlay');
    const list = document.getElementById('download-progress-list');
    if (!overlay || !list) return;

    const states = Array.from(this.downloadStates.values());
    if (states.length === 0) {
      overlay.style.display = 'none';
      return;
    }

    overlay.style.display = 'flex';
    list.innerHTML = '';

    states.forEach((p) => {
      const bytes = p.bytes || 0;
      const total = p.total_bytes || 0;
      let pct = null;
      let statsText;
      if (p.error) {
        statsText = 'failed';
      } else if (total > 0) {
        pct = Math.min(100, (bytes / total) * 100);
        statsText = `${pct.toFixed(1)}% · ${(bytes / 1e6).toFixed(1)}/${(total / 1e6).toFixed(1)} MB`;
      } else {
        statsText = `${(bytes / 1e6).toFixed(1)} MB`;
      }

      const row = document.createElement('div');
      row.className = 'download-row';
      row.innerHTML = `
        <span class="download-row-name" title="${p.case_name}">${p.case_name}</span>
        <span class="download-row-track"><span class="download-row-fill${pct === null ? ' indeterminate' : ''}" style="width:${pct === null ? 100 : pct}%"></span></span>
        <span class="download-row-stats">${statsText}</span>
      `;
      list.appendChild(row);
    });
  }

  async resumeActiveDownloads() {
    try {
      const res = await fetch('/api/case/download/active');
      if (!res.ok) return;
      const data = await res.json();
      (data.downloads || []).forEach((d) => this.trackDownload(d.case_name, d));
    } catch (e) {
      /* ignore */
    }
  }

  async downloadCase(caseName) {
    const proceed = confirm(
      `Download case "${caseName}" from the cluster to local cases/${caseName}/?\n\n` +
      `The transfer runs in the background, so you can keep working. This mirrors the ` +
      `full case directory (including all solution time directories).`
    );
    if (!proceed) return;
    try {
      const res = await fetch('/api/case/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ case_name: caseName }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Download failed');
      this.trackDownload(caseName);
    } catch (err) {
      this.showToast(`Download error: ${err.message}`, 'error');
    }
  }

  // -------------------------------------------------------------
  // Live Telemetry & Console Tail
  // -------------------------------------------------------------
  bindTelemetryEvents() {
    document.getElementById('btn-refresh-telemetry')?.addEventListener('click', () => this.pollTelemetry());
    document.getElementById('telemetry-case-select')?.addEventListener('change', () => this.pollTelemetry());
    document.getElementById('btn-tail-log')?.addEventListener('click', () => this.fetchLogTail());
    document.getElementById('select-log-type')?.addEventListener('change', () => this.fetchLogTail());

    // Live Sync (5s) Toggle button
    const togglePollBtn = document.getElementById('btn-toggle-telemetry-polling');
    const liveDot = document.getElementById('telemetry-live-dot');
    const liveText = document.getElementById('telemetry-live-text');
    togglePollBtn?.addEventListener('click', () => {
      this.telemetryPollingActive = !this.telemetryPollingActive;
      if (this.telemetryPollingActive) {
        if (liveDot) liveDot.className = 'live-dot active';
        if (liveText) liveText.textContent = 'Live Sync (5s)';
        this.showToast('Telemetry live polling resumed', 'info');
        this.pollTelemetry();
      } else {
        if (liveDot) liveDot.className = 'live-dot paused';
        if (liveText) liveText.textContent = 'Paused';
        this.showToast('Telemetry live polling paused', 'info');
      }
    });

    // Copy Log text to clipboard
    document.getElementById('btn-copy-log')?.addEventListener('click', () => {
      const consoleBox = document.getElementById('console-output');
      const text = consoleBox ? consoleBox.textContent : '';
      if (!text || text.trim() === '') {
        this.showToast('No log content to copy', 'info');
        return;
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => {
          this.showToast('Console log copied to clipboard', 'success');
        }).catch(() => {
          this.showToast('Failed to copy to clipboard', 'error');
        });
      } else {
        this.showToast('Clipboard API unavailable in this browser', 'error');
      }
    });
  }

  addTelemetryCase(caseName) {
    const select = document.getElementById('telemetry-case-select');
    if (!select) return;

    let exists = false;
    for (const opt of select.options) {
      if (opt.value === caseName) {
        exists = true;
        break;
      }
    }
    if (!exists) {
      const opt = document.createElement('option');
      opt.value = caseName;
      opt.textContent = caseName;
      select.appendChild(opt);
    }
    select.value = caseName;
  }

  async pollTelemetry() {
    const select = document.getElementById('telemetry-case-select');
    const caseName = select ? select.value : '';
    if (!caseName) return;

    const pill = document.getElementById('telemetry-convergence-pill');
    const forcesOverlay = document.getElementById('forces-empty-overlay');
    const residualsOverlay = document.getElementById('residuals-empty-overlay');
    const emptyDesc = document.getElementById('forces-empty-desc');
    const emptyAction = document.getElementById('forces-empty-action');
    const statusSub = document.getElementById('kpi-status-sub');

    // 1. Fetch Forces
    try {
      const res = await fetch(`/api/telemetry/forces?case_name=${encodeURIComponent(caseName)}`);
      const data = await res.json();

      if (data.has_data) {
        if (forcesOverlay) forcesOverlay.style.display = 'none';

        const dragAxisLabel = data.drag_axis || '-z';
        const dfAxisLabel = data.downforce_axis || '-y';
        this.setValText('kpi-downforce-title', `Downforce (${dfAxisLabel})`);
        this.setValText('kpi-drag-title', `Drag (${dragAxisLabel})`);
        this.setValText('kpi-ld-title', `Aero Efficiency (${dfAxisLabel} / ${dragAxisLabel})`);
        this.setValText('chart-force-axes', `Downforce: ${dfAxisLabel} | Drag: ${dragAxisLabel}`);

        this.setKpiVal('kpi-downforce', data.downforce_avg, 'N');
        this.setValText('kpi-downforce-variation', `±${data.downforce_pct}% variation`);
        this.setKpiVal('kpi-drag', data.drag_avg, 'N');
        this.setValText('kpi-drag-variation', `±${data.drag_pct}% variation`);
        this.setValText('kpi-ld', data.ld_ratio);
        this.setValText('kpi-iter', data.latest_iteration);
        if (statusSub) statusSub.textContent = data.converged ? 'Status: Converged' : 'Status: Solving';

        if (pill) {
          if (data.converged) {
            pill.className = 'convergence-status-pill converged';
            pill.querySelector('.pill-text').textContent = 'CONVERGED (±0.5%)';
          } else {
            pill.className = 'convergence-status-pill running';
            pill.querySelector('.pill-text').textContent = `Solving (Iter ${data.latest_iteration})`;
          }
        }

        if (this.charts && data.series) {
          this.charts.updateForces(data.series, data.drag_axis, data.downforce_axis);
        }
      } else {
        // No forces data yet (case generated or meshed but simpleFoam not executed)
        if (this.charts) {
          this.charts.clear();
        }

        this.setValText('kpi-downforce-title', 'Downforce (-Fy)');
        this.setValText('kpi-drag-title', 'Drag (-Fz)');
        this.setValText('kpi-ld-title', 'Aero Efficiency (-Fy / -Fz)');
        this.setValText('chart-force-axes', 'Downforce: -y | Drag: -z');

        this.setKpiVal('kpi-downforce', '--', 'N');
        this.setValText('kpi-downforce-variation', '±--% variation');
        this.setKpiVal('kpi-drag', '--', 'N');
        this.setValText('kpi-drag-variation', '±--% variation');
        this.setValText('kpi-ld', '--');
        this.setValText('kpi-iter', '0');
        if (statusSub) statusSub.textContent = `Status: ${data.stage || 'Ready'}`;

        const stage = data.stage || 'Generated';
        if (pill) {
          pill.className = 'convergence-status-pill standby';
          pill.querySelector('.pill-text').textContent = stage.toUpperCase();
        }

        if (forcesOverlay) forcesOverlay.style.display = 'flex';
        if (residualsOverlay) residualsOverlay.style.display = 'flex';
        if (emptyDesc) {
          emptyDesc.textContent = `Case is in '${stage}' state. Run the OpenFOAM solver to stream live forces and residuals.`;
        }
        if (emptyAction) {
          emptyAction.innerHTML = `<code>${data.run_command || `./Allrun.parallel  # In cases/${caseName}`}</code>`;
        }
      }
    } catch (err) {
      console.error('Forces telemetry poll failed:', err);
    }

    // 2. Fetch Residuals
    try {
      const res = await fetch(`/api/telemetry/residuals?case_name=${encodeURIComponent(caseName)}`);
      const resData = await res.json();
      if (resData.has_data && this.charts) {
        if (residualsOverlay) residualsOverlay.style.display = 'none';
        this.charts.updateResiduals(resData.iterations, resData.residuals);
      } else {
        if (residualsOverlay) residualsOverlay.style.display = 'flex';
        if (this.charts && this.charts.residualsChart) {
          this.charts.residualsChart.data.labels = [];
          this.charts.residualsChart.data.datasets.forEach(ds => { ds.data = []; });
          this.charts.residualsChart.update('none');
        }
      }
    } catch (err) {
      console.error('Residuals telemetry poll failed:', err);
    }

    // 3. Tail log
    this.fetchLogTail();
  }

  async fetchLogTail() {
    const select = document.getElementById('telemetry-case-select');
    const logType = document.getElementById('select-log-type')?.value || 'simpleFoam';
    const caseName = select ? select.value : '';
    if (!caseName) return;

    try {
      const res = await fetch(`/api/telemetry/logs?case_name=${encodeURIComponent(caseName)}&log_type=${logType}&lines=60`);
      const data = await res.json();
      const consoleBox = document.getElementById('console-output');
      const autoscroll = document.getElementById('chk-console-autoscroll')?.checked;

      if (consoleBox) {
        consoleBox.textContent = data.content || `Log file (${logType}) is currently empty.`;
        if (autoscroll) {
          consoleBox.scrollTop = consoleBox.scrollHeight;
        }
      }

      const logStatus = document.getElementById('console-log-status');
      if (logStatus) {
        logStatus.textContent = `File: ${data.log_file} • Size: ${data.size_bytes} bytes`;
      }
    } catch {}
  }

  // -------------------------------------------------------------
  // Cases Archive
  // -------------------------------------------------------------
  bindCasesArchiveEvents() {
    document.getElementById('btn-refresh-cases')?.addEventListener('click', () => this.loadCasesArchive());

    const searchInput = document.getElementById('archive-search-input');
    searchInput?.addEventListener('input', (e) => {
      this.archiveSearchTerm = (e.target.value || '').toLowerCase().trim();
      this.renderCasesArchiveTable();
    });

    const filterPills = document.querySelectorAll('.archive-filter-group .filter-pill');
    filterPills.forEach((pill) => {
      pill.addEventListener('click', () => {
        filterPills.forEach((p) => p.classList.remove('active'));
        pill.classList.add('active');
        this.currentArchiveFilter = pill.dataset.filter || 'all';
        this.renderCasesArchiveTable();
      });
    });
  }

  async loadCasesArchive() {
    const tbody = document.getElementById('archive-tbody');
    const select = document.getElementById('telemetry-case-select');
    if (!tbody) return;

    try {
      const res = await fetch('/api/cases');
      const cases = await res.json();
      this.archiveCases = Array.isArray(cases) ? cases : [];

      // Update Summary Stat Cards
      const total = this.archiveCases.length;
      const converged = this.archiveCases.filter(c => {
        const s = (c.status || '').toLowerCase();
        return c.converged || s === 'converged' || s === 'completed';
      }).length;
      const solving = this.archiveCases.filter(c => {
        const s = (c.status || '').toLowerCase();
        return ['solving', 'meshing', 'queued', 'completing'].includes(s);
      }).length;
      const ready = this.archiveCases.filter(c => {
        const s = (c.status || '').toLowerCase();
        return ['generated', 'meshed', 'ready'].includes(s);
      }).length;

      this.setValText('stat-total-cases', total);
      this.setValText('stat-converged-cases', converged);
      this.setValText('stat-solving-cases', solving);
      this.setValText('stat-ready-cases', ready);

      // Populate Telemetry Case dropdown if cases exist
      if (select) {
        const currentVal = select.value;
        const newSig = this.archiveCases.map(c => `${c.name}:${c.location}:${c.status}:${c.converged}`).join('|');
        if (select.dataset.caseSig !== newSig) {
          select.dataset.caseSig = newSig;
          select.innerHTML = '';
          if (this.archiveCases.length === 0) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = '-- No cases generated yet --';
            select.appendChild(opt);
          } else {
            this.archiveCases.forEach((c) => {
              const opt = document.createElement('option');
              opt.value = c.name;
              const statusIcon = c.converged ? '✓' : (c.status && c.status.toLowerCase() === 'solving') ? '⚡' : '○';
              opt.textContent = `${statusIcon} ${c.name} (${c.location})`;
              select.appendChild(opt);
            });
            if (currentVal && this.archiveCases.some(c => c.name === currentVal)) {
              select.value = currentVal;
            } else {
              select.value = this.archiveCases[0].name;
            }
          }
        }
      }

      this.renderCasesArchiveTable();
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="7" class="text-center text-muted">Failed to load cases: ${err.message}</td></tr>`;
    }
  }

  renderCasesArchiveTable() {
    const tbody = document.getElementById('archive-tbody');
    const countBadge = document.getElementById('archive-count-badge');
    if (!tbody) return;

    let filtered = this.archiveCases;

    // 1. Status Filter
    if (this.currentArchiveFilter && this.currentArchiveFilter !== 'all') {
      const f = this.currentArchiveFilter.toLowerCase();
      filtered = filtered.filter(c => {
        const s = (c.status || '').toLowerCase();
        if (f === 'converged') return c.converged || s === 'converged';
        if (f === 'completed') return s === 'completed';
        if (f === 'solving') return ['solving', 'meshing', 'queued', 'completing'].includes(s);
        if (f === 'failed') return s === 'failed';
        if (f === 'generated') return ['generated', 'meshed', 'ready'].includes(s);
        return s === f;
      });
    }

    // 2. Search Filter
    if (this.archiveSearchTerm) {
      const term = this.archiveSearchTerm;
      filtered = filtered.filter(c => {
        return (c.name && c.name.toLowerCase().includes(term)) ||
               (c.location && c.location.toLowerCase().includes(term)) ||
               (c.fidelity && c.fidelity.toLowerCase().includes(term)) ||
               (c.stl_name && c.stl_name.toLowerCase().includes(term)) ||
               (c.status && c.status.toLowerCase().includes(term));
      });
    }

    if (countBadge) {
      countBadge.textContent = `${filtered.length} of ${this.archiveCases.length} cases`;
    }

    if (filtered.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted" style="padding: 2.5rem;">No matching simulation cases found in archive.</td></tr>';
      return;
    }

    tbody.innerHTML = '';
    filtered.forEach((c) => {
      const tr = document.createElement('tr');

      // Status Badge
      const statusLower = (c.status || '').toLowerCase();
      let badgeClass = 'status-ready';
      let statusIcon = '○';
      if (c.converged || statusLower === 'converged') {
        badgeClass = 'status-converged';
        statusIcon = '●';
      } else if (statusLower === 'solving') {
        badgeClass = 'status-solving';
        statusIcon = '⚡';
      } else if (statusLower === 'completed') {
        badgeClass = 'status-completed';
        statusIcon = '✓';
      } else if (statusLower === 'meshing' || statusLower === 'meshed') {
        badgeClass = 'status-meshed';
        statusIcon = '⬡';
      } else if (statusLower === 'queued') {
        badgeClass = 'status-queued';
        statusIcon = '⏱';
      } else if (statusLower === 'completing') {
        badgeClass = 'status-completing';
        statusIcon = '⏳';
      } else if (statusLower === 'failed') {
        badgeClass = 'status-failed';
        statusIcon = '✕';
      }
      const statusBadge = `<span class="status-badge ${badgeClass}">${statusIcon} ${c.status || 'Ready'}</span>`;

      // Flow conditions
      let velDisplay = '--';
      const velNum = typeof c.velocity === 'number' ? c.velocity : parseFloat(c.velocity);
      if (!isNaN(velNum)) {
        velDisplay = `${(velNum * 3.6).toFixed(1)} km/h (${velNum.toFixed(1)} m/s)`;
      } else if (c.velocity) {
        velDisplay = c.velocity;
      }
      const flowHtml = `
        <div class="flow-cell">
          <span class="flow-vel">${velDisplay}</span>
          <span class="flow-dir text-muted small">Dir: ${c.direction || '-z'}</span>
        </div>
      `;

      // Aero Results (Fy, Fz, L/D)
      let aeroHtml = '<span class="text-muted small">--</span>';
      if (c.downforce !== null && c.downforce !== undefined && c.drag !== null && c.drag !== undefined) {
        aeroHtml = `
          <div class="aero-results-pills">
            <span class="aero-pill downforce" title="Downforce (-Fy)">Fy: <strong>${c.downforce} N</strong></span>
            <span class="aero-pill drag" title="Drag (-Fz)">Fz: <strong>${c.drag} N</strong></span>
            <span class="aero-pill ld" title="Aero Efficiency (-Fy / -Fz)">L/D: <strong>${c.ld_ratio !== null ? c.ld_ratio : '--'}</strong></span>
          </div>
        `;
      }

      // Progress
      const progressHtml = c.latest_iter > 0
        ? `<span class="iter-count monospace"><strong>${c.latest_iter}</strong> iter</span>`
        : `<span class="text-muted small">0 iter</span>`;

      // Location & Date
      const hasLocal = (c.location || '').includes('Local');
      const hasCluster = (c.location || '').includes('Cluster');
      const isClusterOnly = c.location === 'Cluster';
      let locBadge;
      if (hasLocal && hasCluster) {
        locBadge = '<span class="badge badge-local">Local</span> <span class="badge badge-hpc">Cluster</span>';
      } else if (hasCluster) {
        locBadge = '<span class="badge badge-hpc">Cluster</span>';
      } else {
        locBadge = '<span class="badge badge-local">Local</span>';
      }
      const locDateHtml = `
        <div class="loc-date-cell">
          <div>${locBadge}</div>
          <div class="date-cell text-muted small">${c.modified}</div>
        </div>
      `;

      // Case name & setup chips
      const setupChips = `
        <div class="case-spec-chips">
          <span class="spec-chip fidelity-${(c.fidelity || 'standard').toLowerCase()}">${c.fidelity || 'standard'}</span>
          ${c.n_procs ? `<span class="spec-chip">${c.n_procs}p</span>` : ''}
          ${c.stl_name ? `<span class="spec-chip stl-chip-tag">${c.stl_name}</span>` : ''}
        </div>
      `;

      tr.innerHTML = `
        <td>
          <div class="case-name-cell">
            <strong class="case-title">${c.name}</strong>
            ${setupChips}
          </div>
        </td>
        <td>${statusBadge}</td>
        <td>${flowHtml}</td>
        <td>${aeroHtml}</td>
        <td>${progressHtml}</td>
        <td>${locDateHtml}</td>
        <td>
          <div class="action-btn-group">
            <button class="btn btn-outline btn-xs btn-inspect-case" data-name="${c.name}" title="Inspect Live Telemetry">📊 Live Telemetry</button>
            ${isClusterOnly ? `<button class="btn btn-outline btn-xs btn-download-case" data-name="${c.name}" title="Download case from cluster to local cases/">⬇ Download</button>` : ''}
          </div>
        </td>
      `;

      // Bind actions
      tr.querySelector('.btn-inspect-case')?.addEventListener('click', () => {
        document.querySelector('.nav-tab[data-tab="telemetry-tab"]')?.click();
        this.addTelemetryCase(c.name);
        this.pollTelemetry();
      });

      tr.querySelector('.btn-download-case')?.addEventListener('click', () => this.downloadCase(c.name));

      tbody.appendChild(tr);
    });
  }

  // -------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------
  getVal(id) {
    const el = document.getElementById(id);
    return el ? el.value : '';
  }

  setVal(id, val) {
    const el = document.getElementById(id);
    if (el) el.value = val;
  }

  setSelectValue(id, value) {
    // Populate a <select> without silently dropping values that are not among
    // its predefined options (e.g. a custom n_procs or decomposition method).
    const el = document.getElementById(id);
    if (!el) return;
    const strVal = value === null || value === undefined ? '' : String(value);
    if (el.tagName === 'SELECT') {
      el.querySelectorAll('option[data-config-custom="1"]').forEach((o) => {
        if (o.value !== strVal) o.remove();
      });
      if (strVal !== '' && !Array.from(el.options).some((o) => o.value === strVal)) {
        const opt = document.createElement('option');
        opt.value = strVal;
        opt.textContent = `${strVal} (from config)`;
        opt.dataset.configCustom = '1';
        el.appendChild(opt);
      }
    }
    el.value = strVal;
  }

  setValText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  setKpiVal(id, val, unit = 'N') {
    const el = document.getElementById(id);
    if (!el) return;
    const displayVal = (val !== null && val !== undefined) ? val : '--';
    el.innerHTML = `${displayVal} <span class="kpi-unit">${unit}</span>`;
  }

  getCheck(id) {
    const el = document.getElementById(id);
    return el ? el.checked : false;
  }

  setCheck(id, checked) {
    const el = document.getElementById(id);
    if (el) el.checked = checked;
  }

  showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    const icon = type === 'success' ? '✓' : type === 'error' ? '✗' : 'ℹ';
    toast.innerHTML = `<span>${icon}</span> <span>${message}</span>`;

    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      setTimeout(() => toast.remove(), 300);
    }, 4000);
  }
}

// Instantiate on load
window.addEventListener('DOMContentLoaded', () => {
  window.app = new CFDApp();
});
