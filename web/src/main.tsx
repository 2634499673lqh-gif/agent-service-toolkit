import { useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import * as maplibregl from 'maplibre-gl';
import proj4 from 'proj4';
import 'maplibre-gl/dist/maplibre-gl.css';
import './styles.css';
import { pollTaskRun } from './run-progress.js';

proj4.defs('EPSG:32649', '+proj=utm +zone=49 +datum=WGS84 +units=m +no_defs');

type Capability = { indicator: string; label: string; scientific_limit: string; aoi_id: string; aoi_label: string; data_source: string; periods: string[]; quick_action: string };
type Proposal = { proposal_id: string; title: string; description: string; analysis_area?: string; analysis_type: string; indicator: string; period_a?: { start: string; end: string }; period_b?: { start: string; end: string }; required_parameters: Record<string, string>; data_mode: string };
type HistoryItem = { task_id: string; title: string; status: string; created_at?: string; runs: { id: string; status: string; run_number?: number; created_at?: string }[] };
type QualityReport = { status: string; aoi: { id: string; rasterized_pixels: number }; actual_scenes: Record<string, { scene_ids: string[]; dates: string[] }>; provider?: string; sensor?: string; crs?: string; resolution_m?: number; grid_dimensions?: number[]; target_transform?: number[]; metrics_digest?: string; artifact_integrity?: Record<string, string>; coverage: Record<string, any>; common_mask_means: { period_a: number; period_b: number; delta: number }; verification: Record<string, string>; qa_policy?: string | null; qa_warnings?: string[] | null; qa_status?: string; qa_warnings_available?: boolean; qa_periods?: Record<string, { period: string; policy: string | null; policy_available: boolean; warnings: string[] | null; warnings_available: boolean; status: string }>; limitations: string[] };
type MapResult = { indicator: string; period: Record<string, string>; aoi_label: string; data_source: string; bounds: number[]; crs: string; native_bounds: Record<string, number[]>; raster_dimensions: Record<string, number[]>; scene_identity: Record<string, string>; target_transform?: number[]; fixture_version: string; valid_value_summary?: Record<string, number>; artifacts: Record<string, string>; artifact_urls: Record<string, string>; scientific_limit: string; quality_report?: QualityReport };
function QualityPanel({ report }: { report?: QualityReport }) { if (!report) return null; const c = report.coverage; const gate = (v: any) => v.passes ? '通过' : '未通过'; const periodQa = (key: string) => { const qa = report.qa_periods?.[key]; if (!qa) return `${key.toUpperCase()}：不可用`; const policy = qa.policy_available ? `policy=${qa.policy}` : 'policy=不可用'; const warnings = qa.warnings_available ? (qa.warnings?.length ? qa.warnings.join('；') : '已验证无警告') : '警告不可用'; return `${key.toUpperCase()}：${policy}；${warnings}（${qa.status}）`; }; const qaText = report.qa_status === 'verified' ? '两期 QA 证据均已验证' : report.qa_status === 'partial' ? 'QA 证据部分可用，缺失字段未被填充' : 'QA policy/警告证据不可用'; return <section className="quality-panel"><strong>科学质量报告 · {report.status}</strong><span>AOI：{report.aoi.id} · 栅格像元：{report.aoi.rasterized_pixels.toLocaleString()}</span><span>数据：{report.provider ?? '未记录'} · {report.sensor ?? '未记录'} · {report.crs ?? '未记录'} · {report.resolution_m ?? '?'} m · {report.grid_dimensions?.join('×') ?? '未记录'}</span><span>Affine：{report.target_transform?.join(', ') ?? '未记录'}</span><span>场景 A：{report.actual_scenes.a?.scene_ids.join(', ')}（{report.actual_scenes.a?.dates.join(', ')}）</span><span>场景 B：{report.actual_scenes.b?.scene_ids.join(', ')}（{report.actual_scenes.b?.dates.join(', ')}）</span><span>准备：A {c.preparation.period_a.pixels} / {c.preparation.period_a.pct.toFixed(1)}%（{gate(c.preparation.period_a)}）；B {c.preparation.period_b.pixels} / {c.preparation.period_b.pct.toFixed(1)}%（{gate(c.preparation.period_b)}）</span><span>NDVI 有效：A {c.final_ndvi.period_a.pixels} / {c.final_ndvi.period_a.pct.toFixed(1)}%（{gate(c.final_ndvi.period_a)}）；B {c.final_ndvi.period_b.pixels} / {c.final_ndvi.period_b.pct.toFixed(1)}%（{gate(c.final_ndvi.period_b)}）</span><span>共同：{c.common_comparison.pixels} / {c.common_comparison.pct.toFixed(1)}%（{gate(c.common_comparison)}）</span><span>共同掩膜均值：A {report.common_mask_means.period_a.toFixed(3)} → B {report.common_mask_means.period_b.toFixed(3)}，Δ {report.common_mask_means.delta.toFixed(3)}</span><span>QA：{periodQa('a')}；{periodQa('b')}</span><small>{report.verification.existing}；独立数值一致性：{report.verification.independent_numerical_agreement}。QA 状态：{qaText}；metrics digest：{report.metrics_digest ?? '不可用'}；artifact 完整性：{report.artifact_integrity ? Object.keys(report.artifact_integrity).length : 0} 项。有效数据覆盖不是植被覆盖、FVC 或准确率。</small>{report.limitations.map((item, i) => <small key={i}>{item}</small>)}</section> }
type ChatMessage = { from: 'assistant' | 'user'; text: string; proposal?: Proposal };
type ProgressPhase = { name: 'planning' | 'preparation' | 'computation' | 'validation' | 'finalization'; status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled' };
type RunProgress = { attempt_id: string; attempt_number: number; retry_count: number; replan_count: number; reset_reason: 'start' | 'resume' | 'retry' | 'replan'; state: string; current_phase: ProgressPhase['name'] | null; completed_phase_count: number; total_phase_count: 5; phase_completion_pct: number; phases: ProgressPhase[]; last_event: string; last_event_at: string };
type RunPollState = 'idle' | 'polling' | 'timeout' | 'error' | 'terminal';

const phaseNames: Record<ProgressPhase['name'], string> = { planning: '规划', preparation: '影像准备', computation: 'NDVI 计算', validation: '验证', finalization: '结果提交' };
const phaseStates: Record<ProgressPhase['status'], string> = { pending: '等待中', running: '进行中', succeeded: '已完成', failed: '失败', cancelled: '已取消' };
const runStates: Record<string, string> = { pending: '排队中', running: '执行中', succeeded: '已成功', failed: '失败', cancelled: '已取消' };
const resetReasons: Record<RunProgress['reset_reason'], string> = { start: '首次执行', resume: '恢复执行', retry: '重试', replan: '重新规划' };

function TaskRunProgressPanel({ progress, runStatus, pollState, onContinue }: { progress: RunProgress | null; runStatus: string | null; pollState: RunPollState; onContinue: () => void }) {
  const terminal = runStatus === 'succeeded' || runStatus === 'failed' || runStatus === 'cancelled';
  return <section className="run-progress-panel" aria-live="polite">
    <div className="run-progress-heading"><strong>执行进度</strong><span>{runStates[runStatus ?? ''] ?? '正在读取状态'}</span></div>
    {progress ? <>
      <div className="run-progress-phase">当前阶段：{progress.current_phase ? phaseNames[progress.current_phase] : terminal ? '已结束' : '等待阶段事件'}</div>
      <div className="run-progress-count">已完成阶段 {progress.completed_phase_count}/{progress.total_phase_count} · 阶段完成 {progress.phase_completion_pct}%</div>
      <progress max={100} value={progress.phase_completion_pct} aria-label="阶段完成百分比" />
      <small>百分比表示阶段完成度，不代表耗时或剩余时间。</small>
      <ol className="run-progress-phases">{progress.phases.map(phase => <li key={phase.name} className={phase.status}><span>{phaseNames[phase.name]}</span><span>{phaseStates[phase.status]}</span></li>)}</ol>
      <div className="run-progress-attempt">第 {progress.attempt_number} 次执行 · {resetReasons[progress.reset_reason]}{progress.retry_count ? ` · 重试 ${progress.retry_count}` : ''}{progress.replan_count ? ` · 重新规划 ${progress.replan_count}` : ''}</div>
      <small>最近事件：{progress.last_event} · {new Date(progress.last_event_at).toLocaleString('zh-CN')}</small>
    </> : <p className="run-progress-unavailable">当前阶段进度暂不可用；仍可查询运行状态，不会估算耗时。</p>}
    {pollState === 'polling' && <small>正在查询服务端状态…</small>}
    {(pollState === 'timeout' || pollState === 'error') && !terminal && <div className="run-progress-continue"><span>{pollState === 'timeout' ? '本轮查询已超时。' : '暂时无法读取状态。'}</span><button onClick={onContinue}>继续查询</button></div>}
  </section>;
}

async function api<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) } });
  if (!response.ok) { const detail = (await response.json().catch(() => null))?.detail; throw new Error(response.status === 401 ? '登录状态已失效，请重新登录。' : detail ?? '请求未完成，请稍后重试。'); }
  return response.status === 204 ? (undefined as T) : response.json();
}

function Login({ onLogin }: { onLogin: (token: string) => void }) {
  const [email, setEmail] = useState(''); const [password, setPassword] = useState(''); const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) { e.preventDefault(); setBusy(true); setError(''); try { const result = await api<{ access_token: string }>('/api/v1/auth/login', '', { method: 'POST', body: JSON.stringify({ email, password }) }); onLogin(result.access_token); } catch (err) { setError(err instanceof Error ? err.message : '登录失败。'); } finally { setBusy(false); } }
  return <main className="login-shell"><form className="login-card" onSubmit={submit}><div className="brand">✦ TaskPilot</div><h1>进入分析工作台</h1><p className="muted">使用你的 TaskPilot 账号登录，开始一段有依据的遥感分析。</p><label>邮箱<input type="email" autoComplete="username" value={email} onChange={e => setEmail(e.target.value)} required /></label><label>密码<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label>{error && <p className="error">{error}</p>}<button className="primary" disabled={busy}>{busy ? '登录中…' : '登录并进入工作台'}</button></form></main>;
}

type LayerStatus = 'idle' | 'fetching' | 'fetched' | 'rendered' | 'error';

function formatMetricSummary(indicator: string, metrics?: Record<string, number>): string {
  if (!metrics) return '暂无';
  const labels: Record<string, string> = {
    valid_pixels: '有效分析像元数',
    valid_analysis_area_m2: '有效分析面积',
    significant_decline_area_m2: '下降阈值像元面积',
    final_ndvi_coverage_period_a_pct: '时段 A NDVI 有效覆盖率',
    final_ndvi_coverage_period_b_pct: '时段 B NDVI 有效覆盖率',
    final_common_comparison_coverage_pct: '共同比较覆盖率',
    final_common_comparison_pixels: '共同比较像元数',
  };
  const index = indicator.toLowerCase();
  return Object.entries(metrics).filter(([key, value]) => Number.isFinite(value) && (key in labels || key.startsWith(`mean_${index}`) || key === `mean_delta_${index}`)).map(([key, value]) => {
    const label = labels[key] ?? (key === `mean_${index}_period_a` ? `时段 A 平均 ${indicator}` : key === `mean_${index}_period_b` ? `时段 B 平均 ${indicator}` : key === `mean_delta_${index}` ? `平均 ${indicator} 变化` : '其他统计');
    const formatted = key.endsWith('_pct') ? `${Number(value).toFixed(4)}%` : key.endsWith('_pixels') ? Math.round(Number(value)).toLocaleString('zh-CN') : key.endsWith('_m2') ? `${Number(value).toFixed(1)} m²` : key === 'valid_pixels' ? Math.round(Number(value)).toLocaleString('zh-CN') : Number(value).toFixed(6);
    return `${label}：${formatted}`;
  }).join(' · ');
}

function MapView({ result, taskRun, token, onLayerStatus }: { result: MapResult | null; taskRun: { taskId: string; runId: string } | null; token: string; onLayerStatus: (status: LayerStatus, message: string) => void }) {
  const node = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const [mapError, setMapError] = useState('');
  const [layer, setLayer] = useState('change');
  const [styleReady, setStyleReady] = useState(false);
  const imageUrl = useRef<string | null>(null);
  const resultRendered = useRef(false);

  useEffect(() => {
    if (!node.current) return;
    const m = new maplibregl.Map({
      container: node.current,
      center: [114.375, 30.56],
      zoom: 10,
      style: { version: 8, sources: { osm: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize: 256, attribution: '© OpenStreetMap contributors' } }, layers: [{ id: 'osm', type: 'raster', source: 'osm' }] },
    });
    map.current = m;
    let mapReady = false;
    const loadTimeout = window.setTimeout(() => { if (!mapReady) setMapError('地图初始化超时，请检查底图网络后刷新。'); }, 10000);
    m.addControl(new maplibregl.NavigationControl(), 'top-right');
    m.addControl(new maplibregl.GeolocateControl({ trackUserLocation: false }), 'top-right');
    m.on('error', (event) => {
      const sourceId = (event as { sourceId?: string }).sourceId;
      if (sourceId === 'osm') setMapError('底图暂时不可用，已验证结果图层仍可查看。请检查网络后重试。');
      else if (sourceId === 'result-raster' && !resultRendered.current) setMapError('结果图层暂时不可用，请稍后重试。');
      else if (event.error && !mapReady) setMapError('地图初始化失败，请刷新后重试。');
    });
    m.on('sourcedata', (event) => { if (event.sourceId === 'osm' && event.isSourceLoaded) setMapError(''); });
    m.on('load', () => {
      mapReady = true;
      setStyleReady(true);
      setMapError('');
    });
    return () => { window.clearTimeout(loadTimeout); setStyleReady(false); m.remove(); map.current = null; if (imageUrl.current) URL.revokeObjectURL(imageUrl.current); };
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m || !styleReady) return;
    if (!result || !taskRun) {
      if (m.getLayer('result-raster')) m.removeLayer('result-raster');
      if (m.getSource('result-raster')) m.removeSource('result-raster');
      return;
    }
    const key = `${result.indicator.toLowerCase()}_${layer}`;
    const artifactUrl = result.artifact_urls[key];
    const native = result.native_bounds[layer === 'before' ? 'period_a' : 'period_b'];
    if (!artifactUrl || !native) return;
    let cancelled = false;
    setMapError('');
    const sourceId = 'result-raster';
    const waitForSource = () => new Promise<void>((resolve, reject) => {
      const started = performance.now();
      const check = () => {
        if (cancelled) { reject(new Error('layer request superseded')); return; }
        if (m.getSource(sourceId) && m.isSourceLoaded(sourceId)) { resolve(); return; }
        if (performance.now() - started > 10000) { reject(new Error('raster source load timeout')); return; }
        window.setTimeout(check, 100);
      };
      check();
    });
    (async () => {
      try {
        resultRendered.current = false;
        if (cancelled) return;
        onLayerStatus('fetching', '正在获取结果图层…');
        const response = await fetch(artifactUrl, { headers: { Authorization: `Bearer ${token}` } });
        if (!response.ok) throw new Error(`artifact HTTP ${response.status}`);
        const blob = await response.blob();
        if (!blob.size || !((response.headers.get('content-type') ?? blob.type).toLowerCase().includes('image/png'))) throw new Error('artifact is not PNG');
        const bitmap = await createImageBitmap(blob);
        const dimensions = `${bitmap.width}×${bitmap.height}`;
        bitmap.close();
        if (cancelled) return;
        onLayerStatus('fetched', `结果图层已获取（${dimensions}），正在定位地图…`);
        const blobUrl = URL.createObjectURL(blob);
        if (cancelled) { URL.revokeObjectURL(blobUrl); return; }
        if (imageUrl.current) URL.revokeObjectURL(imageUrl.current);
        imageUrl.current = blobUrl;
        const [minX, minY, maxX, maxY] = native;
        const corners = [[minX, maxY], [maxX, maxY], [maxX, minY], [minX, minY]].map(([x, y]) => proj4(result.crs, 'EPSG:4326', [x, y]));
        if (m.getLayer(sourceId)) m.removeLayer(sourceId);
        if (m.getSource(sourceId)) m.removeSource(sourceId);
        m.addSource(sourceId, { type: 'image', url: blobUrl, coordinates: corners as unknown as [[number, number], [number, number], [number, number], [number, number]] });
        m.addLayer({ id: sourceId, type: 'raster', source: sourceId, paint: { 'raster-opacity': 0.78 } });
        if (!m.getSource(sourceId) || !m.getLayer(sourceId)) throw new Error('map source or layer missing');
        m.fitBounds([[Math.min(...corners.map(c => c[0])), Math.min(...corners.map(c => c[1]))], [Math.max(...corners.map(c => c[0])), Math.max(...corners.map(c => c[1]))]], { padding: 60, duration: 500 });
        await waitForSource();
        if (cancelled) return;
        resultRendered.current = true;
        setMapError('');
        onLayerStatus('rendered', '地图图层加载成功，已按原生范围定位。');
      } catch (err) {
        if (!cancelled) { const reason = err instanceof Error && err.message.startsWith('artifact HTTP') ? `（${err.message}）` : ''; setMapError(`结果图层加载失败${reason}，请稍后重试。`); onLayerStatus('error', `结果图层未加载${reason}。`); }
      }
    })();
    return () => { cancelled = true; };
  }, [result, layer, taskRun, token, styleReady, onLayerStatus]);

  const legend = result ? <div className="map-legend"><strong>{result.indicator} 连续指数（-1.0 至 1.0）</strong><div className="legend-gradient" /><div className="legend-labels"><span>{layer === 'change' ? '下降' : '低值'}</span><span>{layer === 'change' ? '上升' : '高值'}</span></div><small>透明区域为 NoData 或分析范围外</small></div> : null;
  return <div className="map-wrap"><div ref={node} className="map-canvas" />{mapError && <div className="map-error">{mapError}</div>}{legend}<div className="map-toolbar"><span>{result ? `${result.aoi_label} · 已验证结果` : '武汉遥感分析 · 等待确认方案'}</span>{result && <><button className={layer === 'before' ? 'active' : ''} onClick={() => setLayer('before')}>时段 A</button><button className={layer === 'after' ? 'active' : ''} onClick={() => setLayer('after')}>时段 B</button><button className={layer === 'change' ? 'active' : ''} onClick={() => setLayer('change')}>变化</button></>}</div><div className="map-attribution">© OpenStreetMap contributors</div></div>;
}
function App() {
  const [token, setToken] = useState<string | null>(null); const [capabilities, setCapabilities] = useState<Capability[]>([]); const [messages, setMessages] = useState<ChatMessage[]>([]); const [draft, setDraft] = useState(''); const [proposal, setProposal] = useState<Proposal | null>(null); const [dates, setDates] = useState({ aStart: '', aEnd: '', bStart: '', bEnd: '' }); const [result, setResult] = useState<MapResult | null>(null); const [taskRun, setTaskRun] = useState<{ taskId: string; runId: string } | null>(null); const [history, setHistory] = useState<HistoryItem[]>([]); const [busy, setBusy] = useState(false); const [confirming, setConfirming] = useState(false); const [historyLoadingTask, setHistoryLoadingTask] = useState<string | null>(null); const [notice, setNotice] = useState(''); const [view, setView] = useState<'workspace' | 'history' | 'help'>('workspace'); const [layerStatus, setLayerStatus] = useState<LayerStatus>('idle'); const [layerMessage, setLayerMessage] = useState(''); const [interpretation, setInterpretation] = useState(''); const [interpretationBusy, setInterpretationBusy] = useState(false); const [llmStatus, setLlmStatus] = useState<'unknown' | 'available' | 'unavailable'>('unknown');
  const [historyNoticePending, setHistoryNoticePending] = useState(false);
  const [runStatus, setRunStatus] = useState<string | null>(null);
  const [runProgress, setRunProgress] = useState<RunProgress | null>(null);
  const [runPollState, setRunPollState] = useState<RunPollState>('idle');
  const [runPollEnabled, setRunPollEnabled] = useState(false);
  const [pollGeneration, setPollGeneration] = useState(0);
  const authEpoch = useRef(0);
  const selectionEpoch = useRef(0);
  const handleLayerStatus = useCallback((status: LayerStatus, message: string) => { setLayerStatus(status); setLayerMessage(message); if (status === 'rendered' && historyNoticePending) { setNotice('历史结果已加载，地图已按真实栅格范围定位。'); setHistoryNoticePending(false); } else if (status === 'error' && historyNoticePending) { setNotice(message || '历史结果图层加载失败。'); setHistoryNoticePending(false); } }, [historyNoticePending]);
  async function loadInterpretation() {
    if (!token || !taskRun) return;
    const auth = token;
    const authVersion = authEpoch.current;
    const selectionVersion = selectionEpoch.current;
    setInterpretationBusy(true);
    try {
      const response = await api<{ text: string }>(`/api/v1/tasks/${taskRun.taskId}/runs/${taskRun.runId}/interpretation`, auth, { method: 'POST', body: JSON.stringify({ question: '请解释当前结果' }) });
      if (authEpoch.current === authVersion && selectionEpoch.current === selectionVersion) setInterpretation(response.text);
    } catch (err) {
      if (authEpoch.current === authVersion && selectionEpoch.current === selectionVersion) setInterpretation(err instanceof Error ? err.message : '分析解读暂不可用。');
    } finally {
      if (authEpoch.current === authVersion && selectionEpoch.current === selectionVersion) setInterpretationBusy(false);
    }
  }

  async function loadHome(auth: string, authVersion: number) {
    try {
      const caps = await api<{ capabilities: Capability[] }>('/api/v1/geochange/capabilities', auth);
      if (authEpoch.current !== authVersion) return;
      setCapabilities(caps.capabilities);
      setMessages([{ from: 'assistant', text: '你好，我支持武汉市江汉区 Landsat NDVI 双时相分析，以及武汉东湖 Sentinel-2 缓存 NDVI、NDWI、NDBI 分析。可以直接选择下方方案，再确认区域和时段。' }]);
      const items = await api<HistoryItem[]>('/api/v1/conversation/history', auth);
      if (authEpoch.current === authVersion) setHistory(items);
    } catch (err) {
      if (authEpoch.current !== authVersion) return;
      if (err instanceof Error && err.message.includes('登录状态已失效')) {
        authEpoch.current += 1;
        selectionEpoch.current += 1;
        setToken(null); setTaskRun(null); setResult(null); setRunProgress(null); setRunStatus(null); setRunPollEnabled(false); setRunPollState('idle'); setHistory([]);
      }
      else setNotice(err instanceof Error ? err.message : '历史记录暂时不可用。');
    }
  }

  async function refreshHistory(auth: string | null = token, authVersion = authEpoch.current) {
    if (!auth) return;
    try {
      const items = await api<HistoryItem[]>('/api/v1/conversation/history', auth);
      if (authEpoch.current === authVersion) setHistory(items);
    } catch (err) {
      if (authEpoch.current === authVersion) setNotice(err instanceof Error ? err.message : '历史记录暂时不可用。');
    }
  }

  function login(auth: string) {
    const authVersion = ++authEpoch.current;
    selectionEpoch.current += 1;
    setToken(auth);
    setCapabilities([]); setMessages([]); setHistory([]); setResult(null); setTaskRun(null); setRunProgress(null); setRunStatus(null); setRunPollEnabled(false); setRunPollState('idle'); setProposal(null); setNotice(''); setInterpretation(''); setBusy(false); setConfirming(false);
    void loadHome(auth, authVersion);
  }

  async function logout() {
    const auth = token;
    authEpoch.current += 1;
    selectionEpoch.current += 1;
    setToken(null); setCapabilities([]); setMessages([]); setHistory([]); setResult(null); setTaskRun(null); setRunProgress(null); setRunStatus(null); setRunPollEnabled(false); setRunPollState('idle'); setProposal(null); setInterpretation(''); setNotice(''); setBusy(false); setConfirming(false); setView('workspace');
    if (auth) await api('/api/v1/auth/logout', auth, { method: 'POST' }).catch(() => undefined);
  }

  async function ask(text = draft) {
    if (!token || !text.trim()) return;
    const auth = token;
    const authVersion = authEpoch.current;
    setDraft('');
    const context = messages.slice(-8).map(message => ({ role: message.from, content: message.proposal ? `${message.text} 已识别指标${message.proposal.indicator}、区域${message.proposal.analysis_area ?? ''}、时段A${message.proposal.period_a ? `${message.proposal.period_a.start}至${message.proposal.period_a.end}` : '缺失'}、时段B${message.proposal.period_b ? `${message.proposal.period_b.start}至${message.proposal.period_b.end}` : '缺失'}` : message.text }));
    setMessages(value => [...value, { from: 'user', text }]);
    setBusy(true);
    try {
      const response = await api<any>('/api/v1/conversation', auth, { method: 'POST', body: JSON.stringify({ message: text, context }) });
      if (authEpoch.current !== authVersion) return;
      setLlmStatus(response.kind === 'llm_unavailable' ? 'unavailable' : 'available');
      setMessages(value => [...value, { from: 'assistant', text: response.message, proposal: response.proposal }]);
      if (response.proposal) {
        setProposal(response.proposal);
        setDates({ aStart: response.proposal.period_a?.start ?? '', aEnd: response.proposal.period_a?.end ?? '', bStart: response.proposal.period_b?.start ?? '', bEnd: response.proposal.period_b?.end ?? '' });
      }
    } catch (err) {
      if (authEpoch.current === authVersion) {
        setLlmStatus('unavailable');
        setMessages(value => [...value, { from: 'assistant', text: err instanceof Error ? err.message : '请求失败。' }]);
      }
    } finally {
      if (authEpoch.current === authVersion) setBusy(false);
    }
  }

  async function confirm() {
    if (!token || !proposal || confirming) return;
    const auth = token;
    const authVersion = authEpoch.current;
    const periodA = dates.aStart && dates.aEnd ? { start: dates.aStart, end: dates.aEnd } : proposal.period_a;
    const periodB = dates.bStart && dates.bEnd ? { start: dates.bStart, end: dates.bEnd } : proposal.period_b;
    if (!periodA || !periodB) { setNotice('请先补充时段 A 和时段 B。'); return; }
    const selectionVersion = ++selectionEpoch.current;
    const complete = { ...proposal, period_a: periodA, period_b: periodB };
    setBusy(true); setConfirming(true); setNotice(''); setResult(null); setTaskRun(null); setRunProgress(null); setRunStatus(null); setRunPollEnabled(false); setRunPollState('idle'); setInterpretation(''); setLayerStatus('idle'); setLayerMessage('');
    try {
      const created = await api<any>('/api/v1/conversation/confirm', auth, { method: 'POST', body: JSON.stringify({ proposal: complete }) });
      if (authEpoch.current !== authVersion || selectionEpoch.current !== selectionVersion) return;
      const taskId = String(created.result?.task_id ?? '');
      if (created.kind === 'existing_analysis') {
        setMessages(value => [...value, { from: 'assistant', text: created.message }]);
        setNotice('该方案已有历史记录，请在“我的分析”中重新打开结果。');
        setProposal(null);
        await refreshHistory(auth, authVersion);
        return;
      }
      if (created.kind !== 'task_created' || !taskId) throw new Error('分析方案未创建成功，请重新发送分析请求。');
      setMessages(value => [...value, { from: 'assistant', text: '方案已确认，正在启动分析。' }]);
      const run = await api<any>(`/api/v1/tasks/${taskId}/runs`, auth, { method: 'POST' });
      if (authEpoch.current !== authVersion || selectionEpoch.current !== selectionVersion) return;
      setTaskRun({ taskId, runId: String(run.id) });
      setRunStatus(String(run.status ?? 'pending'));
      setRunProgress(run.progress ?? null);
      setRunPollState('polling');
      setRunPollEnabled(true);
      setProposal(null);
    } catch (err) {
      if (authEpoch.current === authVersion && selectionEpoch.current === selectionVersion) setMessages(value => [...value, { from: 'assistant', text: err instanceof Error ? err.message : '分析启动失败。' }]);
    } finally {
      if (authEpoch.current === authVersion && selectionEpoch.current === selectionVersion) { setBusy(false); setConfirming(false); }
    }
  }

  async function openHistory(item: HistoryItem) {
    if (!token || historyLoadingTask) return;
    const auth = token;
    const authVersion = authEpoch.current;
    const candidates = [...item.runs].reverse().filter(run => run.status === 'succeeded');
    if (!candidates.length) { setNotice('该历史任务没有已通过验证的成功运行。'); return; }
    const selectionVersion = ++selectionEpoch.current;
    setHistoryLoadingTask(item.task_id); setNotice('正在加载历史结果…'); setInterpretation(''); setResult(null); setTaskRun(null); setRunProgress(null); setRunStatus(null); setRunPollEnabled(false); setRunPollState('idle'); setLayerStatus('idle'); setLayerMessage(''); setHistoryNoticePending(true);
    let lastError = '';
    try {
      for (const run of candidates) {
        try {
          const mapResult = await api<MapResult>(`/api/v1/tasks/${item.task_id}/runs/${run.id}/map`, auth);
          if (authEpoch.current !== authVersion || selectionEpoch.current !== selectionVersion) return;
          setResult(mapResult); setTaskRun({ taskId: item.task_id, runId: run.id }); setRunStatus('succeeded'); setRunPollState('terminal'); setView('workspace');
          return;
        } catch (err) {
          if (authEpoch.current !== authVersion || selectionEpoch.current !== selectionVersion) return;
          lastError = err instanceof Error ? err.message : '';
        }
      }
      if (authEpoch.current !== authVersion || selectionEpoch.current !== selectionVersion) return;
      const lower = lastError.toLowerCase();
      if (lastError.includes('尚未验证') || lower.includes('verifier')) setNotice('历史运行尚未完成结果验证。');
      else if (lastError.includes('artifact') || lastError.includes('图层')) setNotice('历史结果元数据存在，但影像 artifact 暂不可用。');
      else if (lastError.includes('登录状态') || lower.includes('401') || lower.includes('403')) setNotice('当前登录无权读取该历史结果。');
      else if (lower.includes('timeout') || lastError.includes('超时')) setNotice('历史结果加载超时，请稍后重试。');
      else setNotice('历史结果暂时不可用，请稍后重试。');
      setHistoryNoticePending(false);
    } finally {
      if (authEpoch.current === authVersion && selectionEpoch.current === selectionVersion) setHistoryLoadingTask(null);
    }
  }

  useEffect(() => {
    if (!token || !taskRun || !runPollEnabled) return;
    const auth = token;
    const { taskId, runId } = taskRun;
    const authVersion = authEpoch.current;
    const selectionVersion = selectionEpoch.current;
    const controller = new AbortController();
    let active = true;
    const isCurrent = () => active && authEpoch.current === authVersion && selectionEpoch.current === selectionVersion;
    setRunPollState('polling');

    void pollTaskRun({
      fetchRun: () => api<{ status: string; progress?: RunProgress | null }>(`/api/v1/tasks/${taskId}/runs/${runId}`, auth, { signal: controller.signal }),
      isCurrent,
      onUpdate: current => {
        setRunStatus(current.status);
        setRunProgress(current.progress ?? null);
      },
      maxPolls: 240,
      intervalMs: 1000,
      wait: duration => new Promise(resolve => window.setTimeout(resolve, duration)),
    }).then(async resultCode => {
      if (!isCurrent() || resultCode === 'stale') return;
      if (resultCode === 'timeout') {
        setRunPollState('timeout');
        return;
      }
      setRunPollState('terminal');
      if (resultCode === 'succeeded') {
        try {
          const mapResult = await api<MapResult>(`/api/v1/tasks/${taskId}/runs/${runId}/map`, auth, { signal: controller.signal });
          if (!isCurrent()) return;
          setResult(mapResult);
          setMessages(value => [...value, { from: 'assistant', text: '遥感计算完成，正在获取地图图层。' }]);
        } catch {
          if (!isCurrent()) return;
          setNotice('分析计算已成功，但地图结果暂时不可用；运行状态仍为成功，可稍后重新打开历史结果。');
        }
        await refreshHistory(auth, authVersion);
      } else if (resultCode === 'failed' || resultCode === 'cancelled') {
        setNotice(`本次分析${resultCode === 'cancelled' ? '已取消' : '执行失败'}，请从历史分析查看详情。`);
      }
    }).catch(() => {
      if (isCurrent()) setRunPollState('error');
    });

    return () => {
      active = false;
      controller.abort();
    };
  }, [token, taskRun?.taskId, taskRun?.runId, runPollEnabled, pollGeneration]);

  const overlay = view === 'history' ? <div className="view-overlay"><div className="view-card"><div className="view-heading"><h2>我的分析</h2><button onClick={() => setView('workspace')}>返回工作台</button></div><p className="muted">以下任务来自当前账号的真实历史记录。</p><div className="history-list">{history.length === 0 ? <p className="muted">暂时没有历史分析。</p> : history.slice().reverse().map(item => <div className="history-row" key={item.task_id}><div><strong>{item.title}</strong><span>{item.status === 'succeeded' ? '已完成' : item.status}</span></div><button onClick={() => openHistory(item)} disabled={Boolean(historyLoadingTask) || !item.runs.some(run => run.status === 'succeeded')}>重新打开结果</button></div>)}</div></div></div> : view === 'help' ? <div className="view-overlay"><div className="view-card help-card"><div className="view-heading"><h2>使用帮助</h2><button onClick={() => setView('workspace')}>返回工作台</button></div><ol><li>登录后在左侧输入自然语言问题，先查看分析方案。</li><li>补充两个不重叠的比较时段，再点击确认方案并开始分析。</li><li>右侧地图支持缩放、定位和时段 A / 时段 B / 变化图层切换。</li><li>武汉市江汉区：Landsat 8/9 NDVI，支持 2023–2025 年完整月比较，默认 2023 年 7 月与 2024 年 7 月。武汉东湖：Sentinel-2 缓存 NDVI、NDWI、NDBI 样例。暂不支持其他区域或江汉区 NDWI/NDBI。</li></ol><p><strong>科学限制：</strong>NDVI 是植被指数；NDWI 和 NDBI 仅展示连续指数变化，不能直接证明水域、建设用地或城市扩张面积。</p></div></div> : null;
  if (!token) return <Login onLogin={login} />;
  const metricSummary = result ? formatMetricSummary(result.indicator, result.valid_value_summary) : '暂无';
  const llmLabel = llmStatus === 'available' ? '● AI 在线' : llmStatus === 'unavailable' ? '● AI 暂不可用' : '● AI 状态待检测';
  return <main><header><strong>✦ TaskPilot</strong><span>AI 遥感智能分析平台</span><nav className="top-nav"><button className={view === 'workspace' ? 'selected' : ''} onClick={() => setView('workspace')}>分析工作台</button><button className={view === 'history' ? 'selected' : ''} onClick={() => { setView('history'); refreshHistory(); }}>我的分析</button><button className={view === 'help' ? 'selected' : ''} onClick={() => setView('help')}>使用帮助</button><button onClick={logout}>退出</button></nav></header><section className="workspace"><aside className="chat-panel"><div className="panel-title"><h1>AI 遥感助手</h1><span className={`online ${llmStatus}`}>{llmLabel}</span></div><p className="muted">用自然语言描述想比较的指标和时段，我会先给你一张分析方案卡。</p><div className="messages">{messages.map((m, i) => <div className={`message ${m.from}`} key={i}><div className="bubble">{m.text}</div>{m.proposal && <div className="proposal"><h3>分析方案</h3><p>{m.proposal.indicator} · {m.proposal.analysis_area ?? '区域待确认'}</p><p className="muted">数据源：{m.proposal.required_parameters.source ?? '待确认'}</p><p className="muted">{m.proposal.period_a ? `${m.proposal.period_a.start} 至 ${m.proposal.period_a.end}` : '请选择时段 A'}　→　{m.proposal.period_b ? `${m.proposal.period_b.start} 至 ${m.proposal.period_b.end}` : '请选择时段 B'}</p>{(!m.proposal.period_a || !m.proposal.period_b) && <div className="date-grid"><label>时段 A<input type="date" value={dates.aStart} onChange={e => setDates({ ...dates, aStart: e.target.value })} /><input type="date" value={dates.aEnd} onChange={e => setDates({ ...dates, aEnd: e.target.value })} /></label><label>时段 B<input type="date" value={dates.bStart} onChange={e => setDates({ ...dates, bStart: e.target.value })} /><input type="date" value={dates.bEnd} onChange={e => setDates({ ...dates, bEnd: e.target.value })} /></label></div>}<button className="primary" onClick={confirm} disabled={busy || confirming || proposal !== m.proposal}>{proposal === m.proposal ? (confirming ? '分析启动中…' : '确认方案并开始分析') : '该方案已过期'}</button></div>}</div>)}</div><div className="examples">{capabilities.map(c => <button key={`${c.aoi_id}-${c.indicator}`} title={`${c.data_source} · ${c.periods.join(' / ')}`} onClick={() => ask(c.quick_action)}>{c.aoi_label} · {c.indicator} · {c.label}</button>)}</div><form className="composer" onSubmit={e => { e.preventDefault(); ask(); }}><input value={draft} onChange={e => setDraft(e.target.value)} placeholder="例如：比较两个时段的 NDVI 变化" /><button disabled={busy}>发送</button></form></aside><article className="result-panel"><MapView result={result} taskRun={taskRun} token={token} onLayerStatus={handleLayerStatus} />{taskRun && <div className="run-progress-overlay"><TaskRunProgressPanel progress={runProgress} runStatus={runStatus} pollState={runPollState} onContinue={() => { setRunPollState('polling'); setPollGeneration(value => value + 1); }} /></div>}<div className="result-card"><div><h2>{result ? `${result.indicator} 结果概览` : '分析工作台'}</h2><p>{result ? `${result.aoi_label} · ${result.data_source}` : '地图可以先浏览，完成分析后会显示已验证的结果图层。'}</p>{result && <><p>{result.period.period_a}　→　{result.period.period_b}</p><small>{result.scientific_limit}</small><p className={`layer-status ${layerStatus}`}>{layerMessage}</p><p className="muted">有效像元统计：{metricSummary}</p><QualityPanel report={result.quality_report} /><button onClick={loadInterpretation} disabled={interpretationBusy || layerStatus !== 'rendered'}>{interpretationBusy ? '解读生成中…' : '生成分析解读'}</button>{interpretation && <p className="interpretation">{interpretation}</p>}</>}</div><div className="history"><strong>历史分析</strong>{history.slice(-4).reverse().map(item => <button key={item.task_id} onClick={() => openHistory(item)}>{item.title}</button>)}</div></div>{notice && <div className="notice">{notice}</div>}</article></section>{overlay}</main>;
}

createRoot(document.getElementById('root')!).render(<App />);
