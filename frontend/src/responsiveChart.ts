import * as echarts from 'echarts/core';

const resolutionChecks = new Set<() => void>();
let resolutionTimer: number | undefined;
function checkResolutions() { if (document.visibilityState === 'visible') for (const check of resolutionChecks) check(); }
function observeResolution(check: () => void) {
  resolutionChecks.add(check);
  if (resolutionTimer === undefined) {
    // Some browsers do not emit a media-query change when moving between screens.
    resolutionTimer = window.setInterval(checkResolutions, 500);
    window.addEventListener('resize', checkResolutions);
  }
  return () => {
    resolutionChecks.delete(check);
    if (!resolutionChecks.size) {
      window.clearInterval(resolutionTimer); resolutionTimer = undefined;
      window.removeEventListener('resize', checkResolutions);
    }
  };
}

export function createResponsiveChart(element: HTMLDivElement, onResize?: (width: number) => void, onRecreate?: (chart: echarts.ECharts) => void) {
  const ratio = () => Math.max(1, (window.devicePixelRatio || 1) * (window.visualViewport?.scale || 1));
  let pixelRatio = ratio();
  let chart = echarts.init(element, undefined, { renderer: 'canvas', devicePixelRatio: pixelRatio });
  let resolution: MediaQueryList;
  let disposed = false;
  const watchResolution = () => {
    resolution = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
    resolution.addEventListener('change', resolutionChanged, { once: true });
  };
  function refreshResolution() {
    if (disposed || ratio() === pixelRatio) return;
    // Canvas backing stores retain their initial DPI unless the renderer is recreated.
    const option = chart.getOption();
    chart.dispose();
    pixelRatio = ratio();
    chart = echarts.init(element, undefined, { renderer: 'canvas', devicePixelRatio: pixelRatio });
    chart.setOption(option, true);
    onRecreate?.(chart);
  }
  function resolutionChanged() { if (!disposed) { refreshResolution(); watchResolution(); } }
  const observer = new ResizeObserver(entries => {
    if (disposed) return;
    chart.resize();
    onResize?.(entries[0].contentRect.width);
  });
  observer.observe(element);
  const stopResolution = observeResolution(refreshResolution);
  window.visualViewport?.addEventListener('resize', refreshResolution);
  watchResolution();
  return {
    get chart() { return chart; },
    dispose() {
      disposed = true;
      observer.disconnect();
      stopResolution();
      resolution.removeEventListener('change', resolutionChanged);
      window.visualViewport?.removeEventListener('resize', refreshResolution);
      chart.dispose();
    },
  };
}
