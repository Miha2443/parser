import { Component, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from 'react';
import { createPortal, flushSync } from 'react-dom';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { DataZoomComponent, MarkLineComponent, MarkPointComponent } from 'echarts/components';
import { AlertCircle, ChevronLeft, ChevronRight, ExternalLink, Maximize, Minimize, Pause, Pin, PinOff, Play, RefreshCw, SlidersHorizontal, X } from 'lucide-react';
import { Chart } from './Chart';
import { demoPresentationOption } from './demoPresentation';
import { DEMO_DEFAULT_SECONDS, DEMO_MAX_SECONDS, DEMO_MIN_SECONDS, demoIndex, demoInterval, demoIntervalLabel, demoSelection, demoSources } from './demoData';
import type { DemoChart as DemoChartEntry, DemoSource } from './demoData';
import type { Theme } from './types';
import './demo.css';

echarts.use([LineChart, DataZoomComponent, MarkLineComponent, MarkPointComponent]);
type SourceState = { loading: boolean; charts: DemoChartEntry[]; error: string };
const initialSources = () => Object.fromEntries(demoSources.map(source => [source.id, { loading: true, charts: [], error: '' }])) as Record<string, SourceState>;

function useDemoSources() {
  const [states, setStates] = useState(initialSources), jobs = useRef(new Map<string, AbortController>()), alive = useRef(false);
  const load = useCallback(async (source: DemoSource) => {
    jobs.current.get(source.id)?.abort();
    const controller = new AbortController(); jobs.current.set(source.id, controller);
    setStates(previous => ({ ...previous, [source.id]: { loading: true, charts: [], error: '' } }));
    const timeout = window.setTimeout(() => controller.abort(new Error('Сервер данных не ответил вовремя.')), 60000);
    try {
      const charts = await source.load(controller.signal);
      if (alive.current && jobs.current.get(source.id) === controller && !controller.signal.aborted) setStates(previous => ({ ...previous, [source.id]: { loading: false, charts, error: '' } }));
    } catch (error) {
      if (alive.current && jobs.current.get(source.id) === controller) setStates(previous => ({ ...previous, [source.id]: { loading: false, charts: [], error: controller.signal.aborted ? 'Сервер данных не ответил вовремя.' : error instanceof Error ? error.message : 'Не удалось загрузить графики.' } }));
    } finally { window.clearTimeout(timeout); }
  }, []);
  useEffect(() => {
    alive.current = true;
    let next = 0, cancelled = false;
    const worker = async () => { while (!cancelled && next < demoSources.length) await load(demoSources[next++]); };
    // Bound API pressure; each source shares its report between every preview and the viewer.
    for (let i = 0; i < 3; i++) void worker();
    return () => { cancelled = true; alive.current = false; for (const controller of jobs.current.values()) controller.abort(); };
  }, [load]);
  return { states, retry: load };
}

class ChartBoundary extends Component<{ children: ReactNode; resetKey: string }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidUpdate(previous: Readonly<{ children: ReactNode; resetKey: string }>) { if (previous.resetKey !== this.props.resetKey && this.state.failed) this.setState({ failed: false }); }
  render() { return this.state.failed ? <div className="demo-state" role="alert"><AlertCircle size={20} /><span>Не удалось построить график.</span><button type="button" className="icon-button" title="Повторить построение графика" aria-label="Повторить построение графика" onClick={() => this.setState({ failed: false })}><RefreshCw size={16} /></button></div> : this.props.children; }
}
function LiveChart({ entry, theme, preview = false }: { entry: DemoChartEntry; theme: Theme; preview?: boolean }) {
  const previewHeight = entry.id.startsWith('permits/') ? 280 : 190;
  const container = useRef<HTMLDivElement>(null), [visible, setVisible] = useState(!preview), [size, setSize] = useState({ width: 600, height: preview ? previewHeight : 500 }), [hidden, setHidden] = useState<string[]>([]);
  const { width, height } = size;
  useLayoutEffect(() => {
    if (!container.current) return;
    const element = container.current, canvas = element.querySelector<HTMLElement>(':scope > .chart-block > .chart-canvas');
    const measure = () => {
      const bounds = (canvas ?? element).getBoundingClientRect(), width = Math.round(bounds.width), height = Math.round(bounds.height);
      if (width > 0 && height > 0) setSize(previous => previous.width === width && previous.height === height ? previous : { width, height });
    };
    const observer = new ResizeObserver(measure);
    measure();
    observer.observe(element);
    if (canvas) observer.observe(canvas);
    if (!preview) return () => observer.disconnect();
    const intersection = new IntersectionObserver(entries => setVisible(entries.some(item => item.isIntersecting)), { rootMargin: '160px' });
    intersection.observe(element);
    return () => { observer.disconnect(); intersection.disconnect(); };
  }, [preview, visible]);
  const option = useMemo(() => {
    const built = entry.option(theme, Math.max(240, width), hidden, height);
    const presented = preview ? built : demoPresentationOption(built, width, height, hidden);
    const tooltip = presented.tooltip && typeof presented.tooltip === 'object' && !Array.isArray(presented.tooltip) ? presented.tooltip : {};
    return { ...presented, tooltip: { ...tooltip, appendTo: undefined, appendToBody: false, confine: true } };
  }, [entry, theme, width, height, hidden, preview]);
  return <div ref={container} className={preview ? `demo-live demo-preview${previewHeight > 190 ? ' demo-preview-tall' : ''}` : 'demo-live demo-full-chart'} data-demo-chart={entry.id}>
    {entry.empty ? <div className="demo-state">Нет данных</div> : visible ? <Chart theme={theme} label={entry.title} option={option} rows={entry.rows} filename={`demo-${entry.id.replaceAll('/', '-')}`} height={preview ? previewHeight : 500} onLegendChange={setHidden} /> : <div className="demo-preview-placeholder" aria-hidden="true" />}
  </div>;
}
function Preview({ entry, checked, toggle, theme }: { entry: DemoChartEntry; checked: boolean; toggle: () => void; theme: Theme }) {
  return <article className={`demo-chart-choice ${checked ? 'is-selected' : ''} ${entry.empty ? 'is-empty' : ''}`}>
    <div className="demo-choice-heading"><label><input type="checkbox" checked={checked} disabled={entry.empty} onChange={toggle} /><span>{entry.title}</span></label><a href={entry.href} title={`Открыть раздел: ${entry.title}`} aria-label={`Открыть раздел: ${entry.title}`}><ExternalLink size={15} /></a></div>
    <ChartBoundary resetKey={entry.id}><LiveChart entry={entry} theme={theme} preview /></ChartBoundary><p className="demo-context">{entry.context}</p>
  </article>;
}
function useSlideClock(step: number, seconds: number, paused: boolean, next: () => void) {
  const carried = useRef(0);
  useEffect(() => { carried.current = 0; }, [step, seconds]);
  useEffect(() => {
    if (paused) return;
    const start = performance.now();
    const timer = window.setInterval(() => {
      const value = carried.current + performance.now() - start;
      if (value >= seconds * 1000) { window.clearInterval(timer); next(); }
    }, 200);
    return () => { carried.current += performance.now() - start; window.clearInterval(timer); };
  }, [paused, step, seconds, next]);
}

function DemoViewer({ slides, seconds, theme, onExit, viewerRef, notice }: { slides: DemoChartEntry[]; seconds: number; theme: Theme; onExit: () => void; viewerRef: RefObject<HTMLDivElement>; notice: string }) {
  const [step, setStep] = useState(0), [paused, setPaused] = useState(false), [fullscreen, setFullscreen] = useState(false), [screenError, setScreenError] = useState(notice);
  const [controlsVisible, setControlsVisible] = useState(true), [controlsPinned, setControlsPinned] = useState(false);
  const exitButton = useRef<HTMLButtonElement>(null), controls = useRef<HTMLElement>(null), hideTimer = useRef<number>(), enteredFullscreen = useRef(false), index = demoIndex(step, slides.length), entry = slides[index];
  const next = useCallback(() => setStep(value => value + 1), []), previous = useCallback(() => setStep(value => value - 1), []);
  useSlideClock(step, seconds, paused, next);
  const scheduleHide = useCallback(() => {
    window.clearTimeout(hideTimer.current);
    if (!controlsPinned) hideTimer.current = window.setTimeout(() => {
      if (!controls.current?.contains(document.activeElement)) setControlsVisible(false);
    }, 3000);
  }, [controlsPinned]);
  const revealControls = useCallback(() => { setControlsVisible(true); scheduleHide(); }, [scheduleHide]);
  function toggleControls() {
    if (controlsVisible) { window.clearTimeout(hideTimer.current); setControlsPinned(false); setControlsVisible(false); }
    else revealControls();
  }
  useEffect(() => { scheduleHide(); return () => window.clearTimeout(hideTimer.current); }, [scheduleHide]);
  useEffect(() => { if (notice) setScreenError(notice); }, [notice]);
  useLayoutEffect(() => {
    const element = viewerRef.current!, previousOverflow = document.body.style.overflow;
    const siblings = [...document.body.children].filter((node): node is HTMLElement => node instanceof HTMLElement && node !== element && !node.contains(element));
    const inert = siblings.map(node => [node, node.inert] as const);
    for (const [node] of inert) node.inert = true;
    document.body.style.overflow = 'hidden'; exitButton.current?.focus();
    return () => { for (const [node, value] of inert) node.inert = value; document.body.style.overflow = previousOverflow; };
  }, [viewerRef]);
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onExit(); return; }
      if (event.altKey || event.ctrlKey || event.metaKey || event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return;
      if (event.key === 'ArrowRight') { event.preventDefault(); next(); }
      if (event.key === 'ArrowLeft') { event.preventDefault(); previous(); }
      if (event.key === 'Tab') {
        const focusable = [...viewerRef.current!.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input:not(:disabled),[tabindex="0"]')].filter(node => !node.closest('[hidden], [inert]') && node.getClientRects().length > 0);
        const first = focusable[0], last = focusable.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    const screenChange = () => {
      const active = document.fullscreenElement === viewerRef.current;
      setFullscreen(active);
      if (active) enteredFullscreen.current = true;
      else if (enteredFullscreen.current) onExit();
    };
    document.addEventListener('keydown', keydown, true); document.addEventListener('fullscreenchange', screenChange);
    return () => { document.removeEventListener('keydown', keydown, true); document.removeEventListener('fullscreenchange', screenChange); };
  }, [onExit, next, previous, viewerRef]);
  async function toggleFullscreen() {
    try {
      if (document.fullscreenElement === viewerRef.current) await document.exitFullscreen();
      else if (viewerRef.current?.requestFullscreen) { await viewerRef.current.requestFullscreen(); setScreenError(''); }
      else setScreenError('Полный экран браузера недоступен.');
    } catch { setScreenError('Полный экран браузера недоступен.'); }
  }
  return createPortal(<div ref={viewerRef} className="demo-viewer" role="dialog" aria-modal="true" aria-labelledby="demo-slide-title" aria-describedby="demo-slide-context" onPointerMove={event => { if (event.pointerType === 'mouse' && event.clientY >= event.currentTarget.getBoundingClientRect().bottom - 120) revealControls(); }} onPointerDown={event => { if (event.pointerType === 'touch' && !(event.target instanceof Element && event.target.closest('button, a, input'))) revealControls(); }}>
    <header className="demo-viewer-header"><div className="demo-wordmark"><img src="/brand-moscow.svg" alt="" /><strong>Аналитика Москвы</strong></div><div className="institutional-brand"><img src="/brand-gk.svg" alt="" /><span>Градостроительный<br />комплекс Москвы</span><img src="/brand-dgp.svg" alt="" /><span>Департамент градостроительной<br />политики города Москвы</span></div><div className="demo-header-actions"><button type="button" className="icon-button" title={controlsVisible ? 'Скрыть управление' : 'Показать управление'} aria-label={controlsVisible ? 'Скрыть управление' : 'Показать управление'} aria-controls="demo-playback-controls" aria-expanded={controlsVisible} onClick={toggleControls}><SlidersHorizontal size={19} /></button><button ref={exitButton} type="button" className="icon-button" title="Завершить демонстрацию (Esc)" aria-label="Завершить демонстрацию" onClick={onExit}><X size={19} /></button></div></header>
    <div className="demo-slide-heading" aria-live="polite" aria-atomic="true"><h1 id="demo-slide-title">{entry.title}</h1><p id="demo-slide-context">{entry.context}</p>{screenError && <p role="status" className="demo-screen-error">{screenError}</p>}</div>
    <div className="demo-stage"><ChartBoundary resetKey={`${entry.id}-${step}`}><LiveChart key={entry.id} entry={entry} theme={theme} /></ChartBoundary></div>
    <footer ref={controls} id="demo-playback-controls" className="demo-viewer-controls" hidden={!controlsVisible} onFocusCapture={() => window.clearTimeout(hideTimer.current)} onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget)) scheduleHide(); }}><span className="demo-slide-count" aria-label={`График ${index + 1} из ${slides.length}`}>{index + 1} / {slides.length}</span><button type="button" className="icon-button" title="Предыдущий график (←)" aria-label="Предыдущий график" onClick={previous}><ChevronLeft size={19} /></button><button type="button" className="icon-button" title={paused ? 'Продолжить показ' : 'Приостановить показ'} aria-label={paused ? 'Продолжить показ' : 'Приостановить показ'} onClick={() => setPaused(value => !value)}>{paused ? <Play size={18} /> : <Pause size={18} />}</button><button type="button" className="icon-button" title="Следующий график (→)" aria-label="Следующий график" onClick={next}><ChevronRight size={19} /></button><span className="demo-time">{demoIntervalLabel(seconds)}</span><button type="button" className="icon-button demo-pin-button" title={controlsPinned ? 'Открепить управление' : 'Закрепить управление'} aria-label={controlsPinned ? 'Открепить управление' : 'Закрепить управление'} aria-pressed={controlsPinned} onClick={() => setControlsPinned(value => !value)}>{controlsPinned ? <PinOff size={17} /> : <Pin size={17} />}</button><button type="button" className="icon-button demo-fullscreen-button" title={fullscreen ? 'Выйти из полного экрана' : 'Полный экран браузера'} aria-label={fullscreen ? 'Выйти из полного экрана' : 'Полный экран браузера'} onClick={() => void toggleFullscreen()}>{fullscreen ? <Minimize size={17} /> : <Maximize size={17} />}</button></footer>
  </div>, document.body);
}

export default function DemoPage({ theme }: { theme: Theme }) {
  const { states, retry } = useDemoSources(), [selected, setSelected] = useState<Set<string> | null>(() => new Set()), [seconds, setSeconds] = useState(DEMO_DEFAULT_SECONDS), [browserFullscreen, setBrowserFullscreen] = useState(true), [slides, setSlides] = useState<DemoChartEntry[]>([]), [notice, setNotice] = useState('');
  const viewerRef = useRef<HTMLDivElement>(null), startButton = useRef<HTMLButtonElement>(null), wasRunning = useRef(false);
  const charts = demoSources.flatMap(source => states[source.id].charts), queue = demoSelection(charts, selected), available = charts.filter(chart => !chart.empty), loading = demoSources.filter(source => states[source.id].loading).length;
  const exit = useCallback(() => {
    if (document.fullscreenElement === viewerRef.current) void document.exitFullscreen().catch(() => {});
    setSlides([]);
  }, []);
  useEffect(() => {
    if (wasRunning.current && !slides.length) startButton.current?.focus();
    wasRunning.current = !!slides.length;
  }, [slides.length]);
  useEffect(() => () => { if (document.fullscreenElement === viewerRef.current) void document.exitFullscreen().catch(() => {}); }, []);
  function toggle(id: string) { setSelected(previous => { const next = new Set(previous ?? available.map(chart => chart.id)); if (next.has(id)) next.delete(id); else next.add(id); return next; }); }
  function toggleSource(entries: DemoChartEntry[], checked: boolean) {
    setSelected(previous => {
      const next = new Set(previous ?? available.map(chart => chart.id));
      for (const entry of entries) if (!entry.empty) { if (checked) next.add(entry.id); else next.delete(entry.id); }
      return next;
    });
  }
  async function start() {
    if (!queue.length) return;
    setNotice(''); flushSync(() => setSlides(queue));
    if (browserFullscreen) {
      try { if (viewerRef.current?.requestFullscreen) await viewerRef.current.requestFullscreen(); else setNotice('Полный экран браузера недоступен.'); }
      catch { setNotice('Полный экран браузера недоступен.'); }
    }
  }
  if (slides.length) return <DemoViewer slides={slides} seconds={seconds} theme={theme} onExit={exit} viewerRef={viewerRef} notice={notice} />;
  return <main className="demo-page"><div className="page-heading"><div><div className="eyebrow">СЕРВИС</div><h1>Демонстрация</h1></div><span className="demo-selection-count" role="status">Выбрано: {queue.length} / {available.length}</span></div>
    <div className="demo-settings"><label className="demo-interval"><span>Интервал</span><input type="range" min={DEMO_MIN_SECONDS} max={DEMO_MAX_SECONDS} step={15} value={seconds} aria-label="Интервал показа" aria-valuetext={demoIntervalLabel(seconds)} onChange={event => setSeconds(demoInterval(Number(event.target.value)))} /><output>{demoIntervalLabel(seconds)}</output></label><label className="demo-fullscreen-choice"><input type="checkbox" checked={browserFullscreen} onChange={event => setBrowserFullscreen(event.target.checked)} /><span>Полный экран браузера</span></label><button ref={startButton} className="demo-start" type="button" disabled={!queue.length} onClick={() => void start()}><Play size={17} />Начать показ</button></div>
    <div className="demo-selection-tools"><label><input type="checkbox" checked={available.length > 0 && queue.length === available.length} ref={node => { if (node) node.indeterminate = queue.length > 0 && queue.length < available.length; }} disabled={!available.length} onChange={event => setSelected(event.target.checked ? null : new Set())} /><span>Все графики</span></label>{loading > 0 && <span role="status">Загрузка разделов: {loading}</span>}</div>
    {demoSources.map(source => { const state = states[source.id], available = state.charts.filter(chart => !chart.empty), count = demoSelection(available, selected).length; return <section className="demo-source" key={source.id} aria-labelledby={`demo-source-${source.id}`} aria-busy={state.loading} data-demo-source={source.id}><div className="demo-source-heading"><h2 id={`demo-source-${source.id}`}>{source.title}</h2><div className="demo-source-actions"><label className="demo-source-selection"><input type="checkbox" aria-label={`Все графики раздела: ${source.title}`} checked={available.length > 0 && count === available.length} ref={node => { if (node) node.indeterminate = count > 0 && count < available.length; }} disabled={state.loading || !available.length} onChange={event => toggleSource(state.charts, event.target.checked)} /><span>Все в разделе</span><span className="demo-source-count">{count} / {available.length}</span></label><button className="icon-button" type="button" title={`Обновить: ${source.title}`} aria-label={`Обновить: ${source.title}`} disabled={state.loading} onClick={() => void retry(source)}><RefreshCw size={16} /></button></div></div>
      {state.loading ? <div className="demo-state" role="status"><RefreshCw size={18} className="loading-icon" /><span>Загрузка графиков…</span></div> : state.error ? <div className="demo-state demo-error" role="alert"><AlertCircle size={19} /><span>{state.error}</span><button type="button" title={`Повторить: ${source.title}`} aria-label={`Повторить: ${source.title}`} className="icon-button" onClick={() => void retry(source)}><RefreshCw size={16} /></button></div> : !state.charts.length ? <div className="demo-state">Нет доступных графиков</div> : <div className="demo-chart-grid">{state.charts.map(entry => <Preview key={entry.id} entry={entry} checked={!entry.empty && (selected === null || selected.has(entry.id))} toggle={() => toggle(entry.id)} theme={theme} />)}</div>}
    </section>; })}
  </main>;
}
