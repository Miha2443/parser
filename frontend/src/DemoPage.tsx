import { Component, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from 'react';
import { createPortal, flushSync } from 'react-dom';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { DataZoomComponent, MarkLineComponent, MarkPointComponent } from 'echarts/components';
import { AlertCircle, ChevronLeft, ChevronRight, ExternalLink, Maximize, Minimize, Pause, Pin, PinOff, Play, RefreshCw, SlidersHorizontal, X } from 'lucide-react';
import { Chart } from './Chart';
import { demoPresentationOption, layoutDemoLineLabels } from './demoPresentation';
import { demoBlocks, demoBlockSelection, demoBlockSources, demoMainCharts, type DemoBlock } from './demoCatalog';
import { DEMO_DEFAULT_SECONDS, DEMO_MAX_SECONDS, DEMO_MIN_SECONDS, demoIndex, demoInterval, demoIntervalLabel, demoMonthNames } from './demoData';
import type { DemoChart as DemoChartEntry, DemoOptions, DemoSource } from './demoData';
import { demoPanelContext, demoPanelTitle } from './demoSlides';
import { commonDemoLegend, demoPalette, demoSummaryText } from './demoComposition';
import type { DemoSlide } from './demoSlides';
import type { Theme } from './types';
import './demo.css';

echarts.use([LineChart, DataZoomComponent, MarkLineComponent, MarkPointComponent]);
type SourceState = { loading: boolean; charts: DemoChartEntry[]; error: string; optionsKey: string };
const initialSources = () => Object.fromEntries(demoBlockSources.map(source => [source.id, { loading: true, charts: [], error: '', optionsKey: '{}' }])) as Record<string, SourceState>;

function useDemoSources(options: Record<string, DemoOptions>) {
  const [states, setStates] = useState(initialSources), jobs = useRef(new Map<string, AbortController>()), alive = useRef(false);
  const filters = useRef(options), previousFilters = useRef<Record<string, string>>({});
  filters.current = options;
  const load = useCallback(async (source: DemoSource) => {
    jobs.current.get(source.id)?.abort();
    const controller = new AbortController(); jobs.current.set(source.id, controller);
    const options = filters.current[source.id], optionsKey = JSON.stringify(options ?? {});
    setStates(previous => ({ ...previous, [source.id]: { loading: true, charts: [], error: '', optionsKey } }));
    const timeout = window.setTimeout(() => controller.abort(new Error('Сервер данных не ответил вовремя.')), 60000);
    try {
      const charts = demoMainCharts(await source.load(controller.signal, options));
      if (alive.current && jobs.current.get(source.id) === controller && !controller.signal.aborted) setStates(previous => ({ ...previous, [source.id]: { loading: false, charts, error: '', optionsKey } }));
    } catch (error) {
      if (alive.current && jobs.current.get(source.id) === controller) setStates(previous => ({ ...previous, [source.id]: { loading: false, charts: [], error: controller.signal.aborted ? 'Сервер данных не ответил вовремя.' : error instanceof Error ? error.message : 'Не удалось загрузить графики.', optionsKey } }));
    } finally { window.clearTimeout(timeout); }
  }, []);
  useEffect(() => {
    alive.current = true;
    let next = 0, cancelled = false;
    const worker = async () => { while (!cancelled && next < demoBlockSources.length) await load(demoBlockSources[next++]); };
    // Bound API pressure; each source shares its report between every preview and the viewer.
    for (let i = 0; i < 3; i++) void worker();
    return () => { cancelled = true; alive.current = false; for (const controller of jobs.current.values()) controller.abort(); };
  }, [load]);
  useEffect(() => {
    for (const source of demoBlockSources) {
      const key = JSON.stringify(options[source.id] ?? {}), previous = previousFilters.current[source.id] ?? '{}';
      previousFilters.current[source.id] = key;
      if (key !== previous) void load(source);
    }
  }, [options, load]);
  const current = Object.fromEntries(demoBlockSources.map(source => {
    const key = JSON.stringify(options[source.id] ?? {}), state = states[source.id];
    return [source.id, state.optionsKey === key ? state : { loading: true, charts: [], error: '', optionsKey: key }];
  }));
  return { states: current, retry: load };
}

function SourceOptions({ source, value, change }: { source: DemoSource; value: DemoOptions; change: (patch: DemoOptions) => void }) {
  const regions = ['domrf', 'sales', 'annual', 'mortgage', 'preferential'].includes(source.id) ? ['msk', 'rf'] as const : [];
  const hasMonth = ['permits', 'operational', 'mortgage', 'preferential'].includes(source.id);
  if (!regions.length && !hasMonth) return null;
  return <div className="demo-source-options">
    {regions.length > 0 && <label><span>Регион</span><select aria-label={`Регион демонстрации: ${source.title}`} value={value.region ?? 'msk'} onChange={event => change({ region: event.target.value as 'msk' | 'rf' })}>{regions.map(region => <option key={region} value={region}>{region === 'msk' ? 'Москва' : 'Российская Федерация'}</option>)}</select></label>}
    {hasMonth && <label><span>Накопленный период</span><select aria-label={`Период демонстрации: ${source.title}`} value={value.month ?? ''} onChange={event => change({ month: event.target.value ? Number(event.target.value) : undefined })}><option value="">Актуальный месяц</option>{demoMonthNames.map((month, index) => <option key={month} value={index + 1}>{index ? `январь–${month}` : month}</option>)}</select></label>}
  </div>;
}

class ChartBoundary extends Component<{ children: ReactNode; resetKey: string }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidUpdate(previous: Readonly<{ children: ReactNode; resetKey: string }>) { if (previous.resetKey !== this.props.resetKey && this.state.failed) this.setState({ failed: false }); }
  render() { return this.state.failed ? <div className="demo-state" role="alert"><AlertCircle size={20} /><span>Не удалось построить график.</span><button type="button" className="icon-button" title="Повторить построение графика" aria-label="Повторить построение графика" onClick={() => this.setState({ failed: false })}><RefreshCw size={16} /></button></div> : this.props.children; }
}
function LiveChart({ entry, theme, sharedHidden, sharedLegend = false }: { entry: DemoChartEntry; theme: Theme; sharedHidden?: readonly string[]; sharedLegend?: boolean }) {
  const container = useRef<HTMLDivElement>(null), [size, setSize] = useState({ width: 600, height: 500 }), [hidden, setHidden] = useState<string[]>([]);
  const { width, height } = size, activeHidden = sharedHidden ?? hidden;
  useLayoutEffect(() => {
    if (!container.current) return;
    const element = container.current, canvas = element.querySelector<HTMLElement>(':scope > .chart-block > .chart-canvas');
    const measure = () => {
      const surface = canvas ?? element, width = surface.clientWidth, height = surface.clientHeight;
      if (width > 0 && height > 0) setSize(previous => previous.width === width && previous.height === height ? previous : { width, height });
    };
    const observer = new ResizeObserver(measure);
    measure();
    observer.observe(element);
    if (canvas) observer.observe(canvas);
    return () => observer.disconnect();
  }, []);
  const option = useMemo(() => {
    const built = demoPalette(entry.option(theme, Math.max(240, width), [...activeHidden], height), entry.id, theme);
    const presented = demoPresentationOption(built, width, height, activeHidden, entry.id);
    const tooltip = presented.tooltip && typeof presented.tooltip === 'object' && !Array.isArray(presented.tooltip) ? presented.tooltip : {};
    return { ...presented, tooltip: { ...tooltip, appendTo: undefined, appendToBody: false, confine: true } };
  }, [entry, theme, width, height, activeHidden]);
  return <div ref={container} className="demo-live demo-full-chart" data-demo-chart={entry.id}>
    {entry.empty ? <div className="demo-state">Нет данных</div> : <Chart theme={theme} label={entry.title} option={option} rows={entry.rows} filename={`demo-${entry.id.replaceAll('/', '-')}`} height={500} onLegendChange={setHidden} hiddenSeries={sharedHidden} showLegend={!sharedLegend} afterLayout={layoutDemoLineLabels} />}
  </div>;
}
function DemoSlideContent({ entry, theme, sharedHidden = [], onLegendChange, resetKey = '', notice = '', miniature = false }: {
  entry: DemoSlide; theme: Theme; sharedHidden?: readonly string[]; onLegendChange?: (hidden: string[]) => void; resetKey?: string; notice?: string; miniature?: boolean;
}) {
  const legend = useMemo(() => commonDemoLegend(entry.charts, theme), [entry, theme]);
  const hasSummaries = entry.charts.length > 1 && entry.charts.some(chart => chart.summary !== undefined);
  return <>
    <div className="demo-slide-heading"><div aria-live={miniature ? undefined : 'polite'} aria-atomic="true"><p className="demo-slide-source">{entry.sourceTitle}</p><h1 id={miniature ? undefined : 'demo-slide-title'}>{entry.title}</h1><p id={miniature ? undefined : 'demo-slide-context'}>{entry.context}</p>{notice && <p role="status" className="demo-screen-error">{notice}</p>}</div>
      {legend.length > 0 && <div className="legend demo-shared-legend" role="group" aria-label="Общая легенда экрана">{legend.map(item => <button type="button" key={item.name} className={sharedHidden.includes(item.name) ? 'legend-off' : ''} aria-pressed={!sharedHidden.includes(item.name)} title={`${sharedHidden.includes(item.name) ? 'Показать' : 'Скрыть'}: ${item.name}`} onClick={() => onLegendChange?.(sharedHidden.includes(item.name) ? sharedHidden.filter(name => name !== item.name) : [...sharedHidden, item.name])}><i style={{ background: item.color, ...(item.borderType === 'dashed' ? { border: `1px dashed ${item.color ?? 'currentColor'}`, backgroundImage: 'repeating-linear-gradient(135deg, transparent, transparent 2px, currentColor 2px, currentColor 3px)' } : {}) }} />{item.name}</button>)}</div>}
    </div>
    <div className={`demo-stage demo-layout-${entry.layout}${hasSummaries ? ' demo-has-summaries' : ''}`} data-demo-slide={entry.id} data-demo-panels={entry.charts.length} style={{ '--demo-ring-columns': Math.max(1, entry.charts.filter(chart => !chart.id.endsWith('/annual') && chart.id !== 'domrf/realization').length), '--demo-focus-rows': Math.max(1, entry.charts.length - 1) } as import('react').CSSProperties}>{entry.charts.map(chart => <section className={`demo-panel ${entry.layout === 'overview' && (chart.id.endsWith('/annual') || chart.id === 'domrf/realization') ? 'demo-panel-history' : ''}${entry.layout === 'focus' ? chart.id === entry.primaryId ? ' demo-panel-primary' : ' demo-panel-detail' : ''}`} key={chart.id} aria-label={chart.title} data-demo-role={entry.layout === 'focus' ? chart.id === entry.primaryId ? 'primary' : 'detail' : 'peer'}>
      {entry.charts.length > 1 && <h2>{demoPanelTitle(chart, entry)}</h2>}
      {entry.charts.length > 1 && !(hasSummaries && entry.layout === 'grid') && demoPanelContext(chart, entry) && demoPanelContext(chart, entry) !== chart.summary?.unit && <p className="demo-panel-context">{demoPanelContext(chart, entry)}</p>}
      {chart.summary && <div className="demo-summary"><div><strong>{demoSummaryText(chart.summary)}</strong><span>{chart.summary.unit}</span></div><p>{chart.summary.context}</p></div>}
      {chart.highlights && <div className="demo-highlights">{chart.highlights.map(highlight => <div key={highlight.label}><span>{highlight.label}</span><div><strong style={{ color: highlight.color }}>{demoSummaryText(highlight)}</strong><span>{highlight.unit}</span></div><small>{highlight.context}</small></div>)}</div>}
      {!chart.summary && hasSummaries && entry.layout === 'grid' && <div className="demo-summary demo-summary-placeholder"><span>{demoPanelContext(chart, entry)}</span></div>}
      <ChartBoundary resetKey={`${chart.id}-${resetKey}`}><LiveChart entry={chart} theme={theme} sharedLegend={legend.length > 0} sharedHidden={legend.length ? sharedHidden : undefined} /></ChartBoundary>
    </section>)}</div>
  </>;
}
function BlockPreview({ entry, checked, toggle, theme }: { entry: DemoBlock; checked: boolean; toggle: () => void; theme: Theme }) {
  const root = useRef<HTMLDivElement>(null), [visible, setVisible] = useState(false), [width, setWidth] = useState(600);
  const inputId = `demo-block-${encodeURIComponent(entry.id)}`;
  useLayoutEffect(() => {
    const element = root.current;
    if (!element) return;
    const measure = () => setWidth(element.clientWidth);
    const resize = new ResizeObserver(measure), intersection = new IntersectionObserver(entries => setVisible(entries.some(entry => entry.isIntersecting)), { rootMargin: '200px' });
    measure(); resize.observe(element); intersection.observe(element);
    return () => { resize.disconnect(); intersection.disconnect(); };
  }, []);
  return <article className={`demo-chart-choice demo-block-choice ${checked ? 'is-selected' : ''} ${entry.empty ? 'is-empty' : ''}`} data-demo-block={entry.id}>
    <div className="demo-choice-heading"><label htmlFor={inputId}><input id={inputId} type="checkbox" checked={checked} disabled={entry.empty} onChange={toggle} /><span>{entry.title}</span></label><a href={entry.charts[0].href} title={`Открыть раздел: ${entry.title}`} aria-label={`Открыть раздел: ${entry.title}`}><ExternalLink size={15} /></a></div>
    <label htmlFor={inputId} className="demo-block-picture"><div ref={root} className="demo-block-preview" aria-hidden="true">
      {visible ? <div className="demo-viewer demo-miniature" ref={node => { if (node) node.inert = true; }} style={{ transform: `scale(${width / 1600})` }}><DemoSlideContent entry={entry} theme={theme} miniature /></div> : <div className="demo-block-placeholder" />}
    </div></label><p className="demo-context">{entry.context}</p>
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

function DemoViewer({ slides, seconds, theme, onExit, viewerRef, notice }: { slides: DemoSlide[]; seconds: number; theme: Theme; onExit: () => void; viewerRef: RefObject<HTMLDivElement>; notice: string }) {
  const [step, setStep] = useState(0), [paused, setPaused] = useState(false), [fullscreen, setFullscreen] = useState(false), [screenError, setScreenError] = useState(notice);
  const [controlsVisible, setControlsVisible] = useState(true), [controlsPinned, setControlsPinned] = useState(false);
  const [hiddenBySlide, setHiddenBySlide] = useState<Record<string, string[]>>({});
  const exitButton = useRef<HTMLButtonElement>(null), controls = useRef<HTMLElement>(null), hideTimer = useRef<number>(), enteredFullscreen = useRef(false), index = demoIndex(step, slides.length), entry = slides[index];
  const next = useCallback(() => setStep(value => value + 1), []), previous = useCallback(() => setStep(value => value - 1), []);
  const sharedHidden = useMemo(() => hiddenBySlide[entry.id] ?? [], [hiddenBySlide, entry.id]);
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
    <DemoSlideContent entry={entry} theme={theme} sharedHidden={sharedHidden} onLegendChange={hidden => setHiddenBySlide(previous => ({ ...previous, [entry.id]: hidden }))} resetKey={String(step)} notice={screenError} />
    <footer ref={controls} id="demo-playback-controls" className="demo-viewer-controls" hidden={!controlsVisible} onFocusCapture={() => window.clearTimeout(hideTimer.current)} onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget)) scheduleHide(); }}><span className="demo-slide-count" aria-label={`График ${index + 1} из ${slides.length}`}>{index + 1} / {slides.length}</span><button type="button" className="icon-button" title="Предыдущий график (←)" aria-label="Предыдущий график" onClick={previous}><ChevronLeft size={19} /></button><button type="button" className="icon-button" title={paused ? 'Продолжить показ' : 'Приостановить показ'} aria-label={paused ? 'Продолжить показ' : 'Приостановить показ'} onClick={() => setPaused(value => !value)}>{paused ? <Play size={18} /> : <Pause size={18} />}</button><button type="button" className="icon-button" title="Следующий график (→)" aria-label="Следующий график" onClick={next}><ChevronRight size={19} /></button><span className="demo-time">{demoIntervalLabel(seconds)}</span><button type="button" className="icon-button demo-pin-button" title={controlsPinned ? 'Открепить управление' : 'Закрепить управление'} aria-label={controlsPinned ? 'Открепить управление' : 'Закрепить управление'} aria-pressed={controlsPinned} onClick={() => setControlsPinned(value => !value)}>{controlsPinned ? <PinOff size={17} /> : <Pin size={17} />}</button><button type="button" className="icon-button demo-fullscreen-button" title={fullscreen ? 'Выйти из полного экрана' : 'Полный экран браузера'} aria-label={fullscreen ? 'Выйти из полного экрана' : 'Полный экран браузера'} onClick={() => void toggleFullscreen()}>{fullscreen ? <Minimize size={17} /> : <Maximize size={17} />}</button></footer>
  </div>, document.body);
}

export default function DemoPage({ theme }: { theme: Theme }) {
  const [options, setOptions] = useState<Record<string, DemoOptions>>({});
  const { states, retry } = useDemoSources(options), [selected, setSelected] = useState<Set<string>>(() => new Set()), [seconds, setSeconds] = useState(DEMO_DEFAULT_SECONDS), [browserFullscreen, setBrowserFullscreen] = useState(true), [slides, setSlides] = useState<DemoSlide[]>([]), [notice, setNotice] = useState('');
  const viewerRef = useRef<HTMLDivElement>(null), startButton = useRef<HTMLButtonElement>(null), wasRunning = useRef(false);
  const blocksBySource = demoBlockSources.map(source => ({ source, state: states[source.id], blocks: demoBlocks(states[source.id].charts) }));
  const blocks = blocksBySource.flatMap(source => source.blocks), available = blocks.filter(block => !block.empty), queue = demoBlockSelection(blocks, selected), loading = demoBlockSources.filter(source => states[source.id].loading).length;
  const exit = useCallback(() => {
    if (document.fullscreenElement === viewerRef.current) void document.exitFullscreen().catch(() => {});
    setSlides([]);
  }, []);
  useEffect(() => {
    if (wasRunning.current && !slides.length) startButton.current?.focus();
    wasRunning.current = !!slides.length;
  }, [slides.length]);
  useEffect(() => () => { if (document.fullscreenElement === viewerRef.current) void document.exitFullscreen().catch(() => {}); }, []);
  function toggle(id: string) { setSelected(previous => { const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next; }); }
  function toggleSource(entries: DemoBlock[], checked: boolean) {
    setSelected(previous => {
      const next = new Set(previous);
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
  return <main className="demo-page"><div className="page-heading"><div><div className="eyebrow">СЕРВИС</div><h1>Демонстрация</h1></div><span className="demo-selection-count" role="status">Блоков: {queue.length} / {available.length}</span></div>
    <div className="demo-settings"><label className="demo-interval"><span>Интервал</span><input type="range" min={DEMO_MIN_SECONDS} max={DEMO_MAX_SECONDS} step={15} value={seconds} aria-label="Интервал показа" aria-valuetext={demoIntervalLabel(seconds)} onChange={event => setSeconds(demoInterval(Number(event.target.value)))} /><output>{demoIntervalLabel(seconds)}</output></label><label className="demo-fullscreen-choice"><input type="checkbox" checked={browserFullscreen} onChange={event => setBrowserFullscreen(event.target.checked)} /><span>Полный экран браузера</span></label><button ref={startButton} className="demo-start" type="button" disabled={!queue.length} onClick={() => void start()}><Play size={17} />Начать показ</button></div>
    <div className="demo-selection-tools"><label><input type="checkbox" checked={available.length > 0 && queue.length === available.length} ref={node => { if (node) node.indeterminate = queue.length > 0 && queue.length < available.length; }} disabled={!available.length} onChange={event => setSelected(new Set(event.target.checked ? available.map(block => block.id) : []))} /><span>Все блоки</span></label>{loading > 0 && <span role="status">Загрузка разделов: {loading}</span>}</div>
    {blocksBySource.map(({ source, state, blocks }) => { const available = blocks.filter(block => !block.empty), count = demoBlockSelection(available, selected).length; return <section className="demo-source" key={source.id} aria-labelledby={`demo-source-${source.id}`} aria-busy={state.loading} data-demo-source={source.id}><div className="demo-source-heading"><h2 id={`demo-source-${source.id}`}>{source.title}</h2><div className="demo-source-actions"><label className="demo-source-selection"><input type="checkbox" aria-label={`Все блоки раздела: ${source.title}`} checked={available.length > 0 && count === available.length} ref={node => { if (node) node.indeterminate = count > 0 && count < available.length; }} disabled={state.loading || !available.length} onChange={event => toggleSource(blocks, event.target.checked)} /><span>Все в разделе</span><span className="demo-source-count">{count} / {available.length}</span></label><button className="icon-button" type="button" title={`Обновить: ${source.title}`} aria-label={`Обновить: ${source.title}`} disabled={state.loading} onClick={() => void retry(source)}><RefreshCw size={16} /></button></div></div>
      <SourceOptions source={source} value={options[source.id] ?? {}} change={patch => { setOptions(previous => ({ ...previous, [source.id]: { ...previous[source.id], ...patch } })); }} />
      {state.loading ? <div className="demo-state" role="status"><RefreshCw size={18} className="loading-icon" /><span>Загрузка блоков…</span></div> : state.error ? <div className="demo-state demo-error" role="alert"><AlertCircle size={19} /><span>{state.error}</span><button type="button" title={`Повторить: ${source.title}`} aria-label={`Повторить: ${source.title}`} className="icon-button" onClick={() => void retry(source)}><RefreshCw size={16} /></button></div> : !blocks.length ? <div className="demo-state">Нет доступных блоков</div> : <div className="demo-chart-grid">{blocks.map(entry => <BlockPreview key={entry.id} entry={entry} checked={!entry.empty && selected.has(entry.id)} toggle={() => toggle(entry.id)} theme={theme} />)}</div>}
    </section>; })}
  </main>;
}
