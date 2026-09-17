/**
 * High-Precision Telemetry & Convergence Charts using Chart.js
 */

class TelemetryCharts {
  constructor() {
    this.forcesChart = null;
    this.residualsChart = null;
    this.initForcesChart();
    this.initResidualsChart();
  }

  computeRollingAverage(values, windowSize = 35) {
    if (!values || values.length === 0) return [];
    const result = [];
    let sum = 0;
    for (let i = 0; i < values.length; i++) {
      sum += values[i];
      if (i >= windowSize) {
        sum -= values[i - windowSize];
        result.push(Number((sum / windowSize).toFixed(3)));
      } else {
        result.push(Number((sum / (i + 1)).toFixed(3)));
      }
    }
    return result;
  }

  initForcesChart() {
    const ctx = document.getElementById('chart-forces');
    if (!ctx || typeof Chart === 'undefined') return;

    this.forcesChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: [],
        datasets: [
          {
            label: 'Downforce (-Fy)',
            data: [],
            borderColor: 'rgba(0, 210, 255, 0.35)',
            backgroundColor: 'transparent',
            borderWidth: 1.2,
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0.1,
            yAxisID: 'y',
          },
          {
            label: 'Downforce (Smoothed)',
            data: [],
            borderColor: '#00d2ff',
            backgroundColor: 'rgba(0, 210, 255, 0.08)',
            borderWidth: 2.2,
            pointRadius: 0,
            pointHoverRadius: 5,
            tension: 0.2,
            yAxisID: 'y',
          },
          {
            label: 'Drag (-Fz)',
            data: [],
            borderColor: 'rgba(244, 63, 94, 0.35)',
            backgroundColor: 'transparent',
            borderWidth: 1.2,
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0.1,
            yAxisID: 'y1',
          },
          {
            label: 'Drag (Smoothed)',
            data: [],
            borderColor: '#f43f5e',
            backgroundColor: 'rgba(244, 63, 94, 0.08)',
            borderWidth: 2.2,
            pointRadius: 0,
            pointHoverRadius: 5,
            tension: 0.2,
            yAxisID: 'y1',
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: {
          mode: 'index',
          intersect: false,
        },
        plugins: {
          legend: {
            position: 'top',
            labels: {
              color: '#94a3b8',
              font: { family: "'Inter', sans-serif", size: 11, weight: '500' },
              boxWidth: 12,
              padding: 10,
              usePointStyle: true,
              pointStyle: 'circle',
            },
          },
          tooltip: {
            backgroundColor: '#0c0e14',
            titleColor: '#38bdf8',
            bodyColor: '#f1f5f9',
            borderColor: '#252c3c',
            borderWidth: 1,
            padding: 10,
            cornerRadius: 6,
            callbacks: {
              label: (context) => {
                const label = context.dataset.label || '';
                const val = context.parsed.y;
                return `${label}: ${val !== null ? val.toFixed(2) + ' N' : '--'}`;
              },
            },
          },
        },
        scales: {
          x: {
            title: { display: true, text: 'Iteration', color: '#64748b', font: { size: 10 } },
            ticks: { color: '#64748b', maxTicksLimit: 10, font: { family: "'JetBrains Mono', monospace", size: 10 } },
            grid: { color: '#161b26' },
          },
          y: {
            type: 'linear',
            display: true,
            position: 'left',
            title: { display: true, text: 'Downforce (N)', color: '#00d2ff', font: { size: 10, weight: 'bold' } },
            ticks: { color: '#00d2ff', font: { family: "'JetBrains Mono', monospace", size: 10 } },
            grid: { color: '#161b26' },
          },
          y1: {
            type: 'linear',
            display: true,
            position: 'right',
            title: { display: true, text: 'Drag (N)', color: '#f43f5e', font: { size: 10, weight: 'bold' } },
            ticks: { color: '#f43f5e', font: { family: "'JetBrains Mono', monospace", size: 10 } },
            grid: { drawOnChartArea: false },
          },
        },
      },
    });
  }

  initResidualsChart() {
    const ctx = document.getElementById('chart-residuals');
    if (!ctx || typeof Chart === 'undefined') return;

    this.residualsChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: [],
        datasets: [
          { label: 'p', data: [], borderColor: '#38bdf8', borderWidth: 1.8, pointRadius: 0, spanGaps: true },
          { label: 'Ux', data: [], borderColor: '#a855f7', borderWidth: 1.8, pointRadius: 0, spanGaps: true },
          { label: 'Uy', data: [], borderColor: '#10b981', borderWidth: 1.8, pointRadius: 0, spanGaps: true },
          { label: 'Uz', data: [], borderColor: '#f59e0b', borderWidth: 1.8, pointRadius: 0, spanGaps: true },
          { label: 'k', data: [], borderColor: '#ec4899', borderWidth: 1.8, pointRadius: 0, spanGaps: true },
          { label: 'omega', data: [], borderColor: '#6366f1', borderWidth: 1.8, pointRadius: 0, spanGaps: true },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: {
          legend: {
            position: 'top',
            labels: {
              color: '#94a3b8',
              font: { family: "'Inter', sans-serif", size: 10, weight: '500' },
              boxWidth: 10,
              padding: 8,
              usePointStyle: true,
              pointStyle: 'circle',
            },
          },
          tooltip: {
            backgroundColor: '#0c0e14',
            titleColor: '#38bdf8',
            bodyColor: '#f1f5f9',
            borderColor: '#252c3c',
            borderWidth: 1,
            padding: 10,
            cornerRadius: 6,
            callbacks: {
              label: (context) => {
                const label = context.dataset.label || '';
                const val = context.parsed.y;
                return `${label}: ${val !== null && val !== undefined ? val.toExponential(3) : '--'}`;
              },
            },
          },
        },
        scales: {
          x: {
            title: { display: true, text: 'Iteration', color: '#64748b', font: { size: 10 } },
            ticks: { color: '#64748b', maxTicksLimit: 10, font: { family: "'JetBrains Mono', monospace", size: 10 } },
            grid: { color: '#161b26' },
          },
          y: {
            type: 'logarithmic',
            suggestedMin: 1e-6,
            suggestedMax: 1.0,
            title: { display: true, text: 'Initial Residual', color: '#94a3b8', font: { size: 10 } },
            ticks: {
              color: '#64748b',
              font: { family: "'JetBrains Mono', monospace", size: 10 },
              callback: function (val) {
                const num = Number(val);
                if (num > 0) {
                  const log10 = Math.log10(num);
                  if (Math.abs(log10 - Math.round(log10)) < 1e-4) {
                    return `1e${Math.round(log10)}`;
                  }
                }
                return '';
              },
            },
            grid: { color: '#161b26' },
          },
        },
      },
    });
  }

  updateForces(series, dragAxis = null, downforceAxis = null) {
    if (!this.forcesChart || !series) return;
    const iters = series.iterations || [];
    const downforces = series.downforce || [];
    const drags = series.drag || [];

    if (dragAxis || downforceAxis) {
      const dfLabel = downforceAxis || '-y';
      const dragLabel = dragAxis || '-z';
      this.forcesChart.data.datasets[0].label = `Downforce (${dfLabel})`;
      this.forcesChart.data.datasets[1].label = `Downforce (${dfLabel}) (Smoothed)`;
      this.forcesChart.data.datasets[2].label = `Drag (${dragLabel})`;
      this.forcesChart.data.datasets[3].label = `Drag (${dragLabel}) (Smoothed)`;
      if (this.forcesChart.options.scales.y.title) {
        this.forcesChart.options.scales.y.title.text = `Downforce (${dfLabel}) [N]`;
      }
      if (this.forcesChart.options.scales.y1.title) {
        this.forcesChart.options.scales.y1.title.text = `Drag (${dragLabel}) [N]`;
      }
    }

    const smoothedDf = this.computeRollingAverage(downforces);
    const smoothedDrag = this.computeRollingAverage(drags);

    this.forcesChart.data.labels = iters;
    this.forcesChart.data.datasets[0].data = downforces;
    this.forcesChart.data.datasets[1].data = smoothedDf;
    this.forcesChart.data.datasets[2].data = drags;
    this.forcesChart.data.datasets[3].data = smoothedDrag;

    this.forcesChart.update('none');
  }

  updateResiduals(iterations, residualsMap) {
    if (!this.residualsChart || !residualsMap) return;
    this.residualsChart.data.labels = iterations || [];

    this.residualsChart.data.datasets.forEach((ds) => {
      if (residualsMap[ds.label]) {
        ds.data = residualsMap[ds.label];
      } else {
        ds.data = [];
      }
    });
    this.residualsChart.update('none');
  }

  clear() {
    if (this.forcesChart) {
      this.forcesChart.data.labels = [];
      this.forcesChart.data.datasets.forEach((ds) => {
        ds.data = [];
      });
      this.forcesChart.update('none');
    }
    if (this.residualsChart) {
      this.residualsChart.data.labels = [];
      this.residualsChart.data.datasets.forEach((ds) => {
        ds.data = [];
      });
      this.residualsChart.update('none');
    }
  }
}

window.TelemetryCharts = TelemetryCharts;
