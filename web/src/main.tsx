import { useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import * as maplibregl from 'maplibre-gl';
import proj4 from 'proj4';
import 'maplibre-gl/dist/maplibre-gl.css';
import './styles.css';

type Capability = { indicator: string; label: string; scientific_limit: string };
type Proposal = { title: string; description: string; analysis_area?: string; analysis_type: string; indicator: string; period_a?: { start: string; end: string }; period_b?: { start: string; end: string }; required_parameters: Record<string, string> };
type HistoryItem = { task_id: string; title: string; status: string; created_at?: string; runs: { id: string; status: string; run_number?: number; created_at?: string }[] };
type MapResult = { indicator: string; period: Record<string, string>; aoi_label: string; data_source: string; bounds: number[]; crs: string; native_bounds: Record<string, number[]>; raster_dimensions: Record<string, number[]>; scene_identity: Record<string, string>; target_transform?: number[]; fixture_version: string; valid_value_summary?: Record<string, number>; artifacts: Record<string, string>; artifact_urls: Record<string, string>; scientific_limit: string };
type ChatMessage = { from: 'assistant' | 'user'; text: string; proposal?: Proposal };

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
    significant_decline_area_m2: '显著下降像元面积',
  };
  const index = indicator.toLowerCase();
  return Object.entries(metrics).map(([key, value]) => {
    const label = labels[key] ?? (key === `mean_${index}_period_a` ? `时段 A 平均 ${indicator}` : key === `mean_${index}_period_b` ? `时段 B 平均 ${indicator}` : key === `mean_delta_${index}` ? `平均 ${indicator} 变化` : '其他统计');
    const formatted = key.endsWith('_m2') ? `${Number(value).toFixed(1)} m²` : key === 'valid_pixels' ? Math.round(Number(value)).toLocaleString('zh-CN') : Number(value).toFixed(3);
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
      if (sourceId === 'osm' || (sourceId === 'result-raster' && !resultRendered.current)) setMapError('地图数据暂时不可用，请检查网络后重试。');
      else if (event.error && !mapReady) setMapError('地图初始化失败，请刷新后重试。');
    });
    m.on('sourcedata', (event) => { if (event.sourceId === 'osm' && event.isSourceLoaded) setMapError(''); });
    m.on('load', () => {
      mapReady = true;
      setStyleReady(true);
      setMapError('');
      m.addSource('aoi', { type: 'geojson', data: { type: 'Feature', geometry: { type: 'Polygon', coordinates: [[[114.3, 30.5], [114.45, 30.5], [114.45, 30.62], [114.3, 30.62], [114.3, 30.5]]] }, properties: {} } });
      m.addLayer({ id: 'aoi-fill', type: 'fill', source: 'aoi', paint: { 'fill-color': '#5ab8bd', 'fill-opacity': 0.12 } });
      m.addLayer({ id: 'aoi-line', type: 'line', source: 'aoi', paint: { 'line-color': '#2a9299', 'line-width': 2 } });
    });
    return () => { window.clearTimeout(loadTimeout); setStyleReady(false); m.remove(); map.current = null; if (imageUrl.current) URL.revokeObjectURL(imageUrl.current); };
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m || !result || !taskRun || !styleReady) return;
    const key = `${result.indicator.toLowerCase()}_${layer}`;
    const artifactUrl = result.artifact_urls[key];
    const native = result.native_bounds[layer === 'before' ? 'period_a' : 'period_b'];
    if (!artifactUrl || !native) return;
    let cancelled = false;
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
  return <div className="map-wrap"><div ref={node} className="map-canvas" />{mapError && <div className="map-error">{mapError}</div>}{legend}<div className="map-toolbar"><span>武汉东湖 · 已验证覆盖区</span>{result && <><button className={layer === 'before' ? 'active' : ''} onClick={() => setLayer('before')}>时段 A</button><button className={layer === 'after' ? 'active' : ''} onClick={() => setLayer('after')}>时段 B</button><button className={layer === 'change' ? 'active' : ''} onClick={() => setLayer('change')}>变化</button></>}</div><div className="map-attribution">© OpenStreetMap contributors</div></div>;
}
function App() {
  const [token, setToken] = useState<string | null>(null); const [capabilities, setCapabilities] = useState<Capability[]>([]); const [messages, setMessages] = useState<ChatMessage[]>([]); const [draft, setDraft] = useState(''); const [proposal, setProposal] = useState<Proposal | null>(null); const [dates, setDates] = useState({ aStart: '', aEnd: '', bStart: '', bEnd: '' }); const [result, setResult] = useState<MapResult | null>(null); const [taskRun, setTaskRun] = useState<{ taskId: string; runId: string } | null>(null); const [history, setHistory] = useState<HistoryItem[]>([]); const [busy, setBusy] = useState(false); const [confirming, setConfirming] = useState(false); const [historyLoadingTask, setHistoryLoadingTask] = useState<string | null>(null); const [notice, setNotice] = useState(''); const [view, setView] = useState<'workspace' | 'history' | 'help'>('workspace'); const [layerStatus, setLayerStatus] = useState<LayerStatus>('idle'); const [layerMessage, setLayerMessage] = useState(''); const [interpretation, setInterpretation] = useState(''); const [interpretationBusy, setInterpretationBusy] = useState(false); const [llmStatus, setLlmStatus] = useState<'unknown' | 'available' | 'unavailable'>('unknown');
  const [historyNoticePending, setHistoryNoticePending] = useState(false);
  const handleLayerStatus = useCallback((status: LayerStatus, message: string) => { setLayerStatus(status); setLayerMessage(message); if (status === 'rendered' && historyNoticePending) { setNotice('历史结果已加载，地图已按真实栅格范围定位。'); setHistoryNoticePending(false); } else if (status === 'error' && historyNoticePending) { setNotice(message || '历史结果图层加载失败。'); setHistoryNoticePending(false); } }, [historyNoticePending]);
  async function loadInterpretation() { if (!token || !taskRun) return; setInterpretationBusy(true); try { const response = await api<{ text: string }>('/api/v1/tasks/' + taskRun.taskId + '/runs/' + taskRun.runId + '/interpretation', token, { method: 'POST', body: JSON.stringify({ question: '请解释当前结果' }) }); setInterpretation(response.text); } catch (err) { setInterpretation(err instanceof Error ? err.message : '分析解读暂不可用。'); } finally { setInterpretationBusy(false); } }
  async function loadHome(auth: string) { try { const caps = await api<{ capabilities: Capability[] }>('/api/v1/geochange/capabilities', auth); setCapabilities(caps.capabilities); setMessages([{ from: 'assistant', text: '你好，我可以帮你比较武汉东湖已验证缓存中的 NDVI、NDWI 或 NDBI。你想先了解哪项变化？' }]); const h = await api<HistoryItem[]>('/api/v1/conversation/history', auth); setHistory(h); } catch (err) { if (err instanceof Error && err.message.includes('登录状态已失效')) setToken(null); else setNotice(err instanceof Error ? err.message : '历史记录暂时不可用。'); } }
  async function refreshHistory() { if (!token) return; try { setHistory(await api<HistoryItem[]>('/api/v1/conversation/history', token)); } catch (err) { setNotice(err instanceof Error ? err.message : '历史记录暂时不可用。'); } }
  function login(auth: string) { setToken(auth); loadHome(auth); }
  async function logout() { if (token) await api('/api/v1/auth/logout', token, { method: 'POST' }).catch(() => undefined); setToken(null); setMessages([]); setHistory([]); setResult(null); setProposal(null); setView('workspace'); }
  async function ask(text = draft) { if (!token || !text.trim()) return; setDraft(''); const context = messages.slice(-8).map(message => ({ role: message.from, content: message.proposal ? `${message.text} 已识别指标${message.proposal.indicator}、区域${message.proposal.analysis_area ?? ''}、时段A${message.proposal.period_a ? `${message.proposal.period_a.start}至${message.proposal.period_a.end}` : '缺失'}、时段B${message.proposal.period_b ? `${message.proposal.period_b.start}至${message.proposal.period_b.end}` : '缺失'}` : message.text })); setMessages(v => [...v, { from: 'user', text }]); setBusy(true); try { const response = await api<any>('/api/v1/conversation', token, { method: 'POST', body: JSON.stringify({ message: text, context }) }); setLlmStatus(response.kind === 'llm_unavailable' ? 'unavailable' : 'available'); setMessages(v => [...v, { from: 'assistant', text: response.message, proposal: response.proposal }]); if (response.proposal) { setProposal(response.proposal); setDates({ aStart: response.proposal.period_a?.start ?? '', aEnd: response.proposal.period_a?.end ?? '', bStart: response.proposal.period_b?.start ?? '', bEnd: response.proposal.period_b?.end ?? '' }); } } catch (err) { setLlmStatus('unavailable'); setMessages(v => [...v, { from: 'assistant', text: err instanceof Error ? err.message : '请求失败。' }]); } finally { setBusy(false); } }
  async function confirm() { if (!token || !proposal || confirming) return; const periodA = dates.aStart && dates.aEnd ? { start: dates.aStart, end: dates.aEnd } : proposal.period_a; const periodB = dates.bStart && dates.bEnd ? { start: dates.bStart, end: dates.bEnd } : proposal.period_b; if (!periodA || !periodB) { setNotice('请先补充时段 A 和时段 B。'); return; } const complete = { ...proposal, period_a: periodA, period_b: periodB }; setBusy(true); setConfirming(true); setNotice(''); setResult(null); setInterpretation(''); setLayerStatus('idle'); setLayerMessage(''); try { const created = await api<any>('/api/v1/conversation/confirm', token, { method: 'POST', body: JSON.stringify({ proposal: complete }) }); const taskId = String(created.result.task_id); setMessages(v => [...v, { from: 'assistant', text: '方案已确认，正在启动分析。' }]); const run = await api<any>(`/api/v1/tasks/${taskId}/runs`, token, { method: 'POST' }); setTaskRun({ taskId, runId: String(run.id) }); setProposal(null); let terminal = false; for (let i = 0; i < 45; i++) { await new Promise(r => setTimeout(r, 1000)); const current = await api<any>(`/api/v1/tasks/${taskId}/runs/${run.id}`, token); if (current.status === 'succeeded') { terminal = true; setResult(await api<MapResult>(`/api/v1/tasks/${taskId}/runs/${run.id}/map`, token)); setMessages(v => [...v, { from: 'assistant', text: '遥感计算完成，正在获取地图图层。' }]); await refreshHistory(); break; } if (current.status === 'failed' || current.status === 'cancelled') { terminal = true; throw new Error(`本次分析${current.status === 'cancelled' ? '已取消' : '执行失败'}，请从历史分析查看详情。`); } } if (!terminal) throw new Error('任务仍在排队或执行中，页面轮询已超时；可稍后从历史分析查看状态。'); } catch (err) { setMessages(v => [...v, { from: 'assistant', text: err instanceof Error ? err.message : '分析启动失败。' }]); } finally { setBusy(false); setConfirming(false); } }
  async function openHistory(item: HistoryItem) { if (!token || historyLoadingTask) return; const candidates = [...item.runs].reverse().filter(run => run.status === 'succeeded'); if (!candidates.length) { setNotice('该历史任务没有已通过验证的成功运行。'); return; } setHistoryLoadingTask(item.task_id); setNotice('正在加载历史结果…'); setInterpretation(''); setResult(null); setTaskRun(null); setLayerStatus('idle'); setLayerMessage(''); setHistoryNoticePending(true); let lastError = ''; try { for (const run of candidates) { try { const mapResult = await api<MapResult>(`/api/v1/tasks/${item.task_id}/runs/${run.id}/map`, token); setResult(mapResult); setTaskRun({ taskId: item.task_id, runId: run.id }); setView('workspace'); return; } catch (err) { lastError = err instanceof Error ? err.message : ''; } } const lower = lastError.toLowerCase(); if (lastError.includes('尚未验证') || lower.includes('verifier')) setNotice('历史运行尚未完成结果验证。'); else if (lastError.includes('artifact') || lastError.includes('图层')) setNotice('历史结果元数据存在，但影像 artifact 暂不可用。'); else if (lastError.includes('登录状态') || lower.includes('401') || lower.includes('403')) setNotice('当前登录无权读取该历史结果。'); else if (lower.includes('timeout') || lastError.includes('超时')) setNotice('历史结果加载超时，请稍后重试。'); else setNotice('历史结果暂时不可用，请稍后重试。'); setHistoryNoticePending(false); } finally { setHistoryLoadingTask(null); } }
  const overlay = view === 'history' ? <div className="view-overlay"><div className="view-card"><div className="view-heading"><h2>我的分析</h2><button onClick={() => setView('workspace')}>返回工作台</button></div><p className="muted">以下任务来自当前账号的真实历史记录。</p><div className="history-list">{history.length === 0 ? <p className="muted">暂时没有历史分析。</p> : history.slice().reverse().map(item => <div className="history-row" key={item.task_id}><div><strong>{item.title}</strong><span>{item.status === 'succeeded' ? '已完成' : item.status}</span></div><button onClick={() => openHistory(item)} disabled={Boolean(historyLoadingTask) || !item.runs.some(run => run.status === 'succeeded')}>重新打开结果</button></div>)}</div></div></div> : view === 'help' ? <div className="view-overlay"><div className="view-card help-card"><div className="view-heading"><h2>使用帮助</h2><button onClick={() => setView('workspace')}>返回工作台</button></div><ol><li>登录后在左侧输入自然语言问题，先查看分析方案。</li><li>补充两个不重叠的比较时段，再点击确认方案并开始分析。</li><li>右侧地图支持缩放、定位和时段 A / 时段 B / 变化图层切换。</li><li>当前仅支持已验证的武汉东湖 Sentinel-2 缓存场景。</li></ol><p><strong>科学限制：</strong>NDVI 是植被指数；NDWI 和 NDBI 仅展示连续指数变化，不能直接证明水域、建设用地或城市扩张面积。</p></div></div> : null;
  if (!token) return <Login onLogin={login} />;
  const metricSummary = result ? formatMetricSummary(result.indicator, result.valid_value_summary) : '暂无';
  const llmLabel = llmStatus === 'available' ? '● AI 在线' : llmStatus === 'unavailable' ? '● AI 暂不可用' : '● AI 状态待检测';
  return <main><header><strong>✦ TaskPilot</strong><span>AI 遥感智能分析平台</span><nav className="top-nav"><button className={view === 'workspace' ? 'selected' : ''} onClick={() => setView('workspace')}>分析工作台</button><button className={view === 'history' ? 'selected' : ''} onClick={() => { setView('history'); refreshHistory(); }}>我的分析</button><button className={view === 'help' ? 'selected' : ''} onClick={() => setView('help')}>使用帮助</button><button onClick={logout}>退出</button></nav></header><section className="workspace"><aside className="chat-panel"><div className="panel-title"><h1>AI 遥感助手</h1><span className={`online ${llmStatus}`}>{llmLabel}</span></div><p className="muted">用自然语言描述想比较的指标和时段，我会先给你一张分析方案卡。</p><div className="messages">{messages.map((m, i) => <div className={`message ${m.from}`} key={i}><div className="bubble">{m.text}</div>{m.proposal && <div className="proposal"><h3>分析方案</h3><p>{m.proposal.indicator} · {m.proposal.analysis_area ?? '武汉东湖'}</p><p className="muted">{m.proposal.period_a ? `${m.proposal.period_a.start} 至 ${m.proposal.period_a.end}` : '请选择时段 A'}　→　{m.proposal.period_b ? `${m.proposal.period_b.start} 至 ${m.proposal.period_b.end}` : '请选择时段 B'}</p>{(!m.proposal.period_a || !m.proposal.period_b) && <div className="date-grid"><label>时段 A<input type="date" value={dates.aStart} onChange={e => setDates({ ...dates, aStart: e.target.value })} /><input type="date" value={dates.aEnd} onChange={e => setDates({ ...dates, aEnd: e.target.value })} /></label><label>时段 B<input type="date" value={dates.bStart} onChange={e => setDates({ ...dates, bStart: e.target.value })} /><input type="date" value={dates.bEnd} onChange={e => setDates({ ...dates, bEnd: e.target.value })} /></label></div>}<button className="primary" onClick={confirm} disabled={busy || confirming || proposal !== m.proposal}>{proposal === m.proposal ? (confirming ? '分析启动中…' : '确认方案并开始分析') : '该方案已过期'}</button></div>}</div>)}</div><div className="examples">{capabilities.map(c => <button key={c.indicator} onClick={() => ask(`请比较武汉东湖 2023-07-01 至 2023-07-31 和 2024-07-01 至 2024-07-31 的 ${c.indicator} 变化`)}>{c.indicator} · {c.label}</button>)}</div><form className="composer" onSubmit={e => { e.preventDefault(); ask(); }}><input value={draft} onChange={e => setDraft(e.target.value)} placeholder="例如：比较两个时段的 NDVI 变化" /><button disabled={busy}>发送</button></form></aside><article className="result-panel"><MapView result={result} taskRun={taskRun} token={token} onLayerStatus={handleLayerStatus} /><div className="result-card"><div><h2>{result ? `${result.indicator} 结果概览` : '分析工作台'}</h2><p>{result ? `${result.aoi_label} · ${result.data_source}` : '地图可以先浏览，完成分析后会显示已验证的结果图层。'}</p>{result && <><p>{result.period.period_a}　→　{result.period.period_b}</p><small>{result.scientific_limit}</small><p className={`layer-status ${layerStatus}`}>{layerMessage}</p><p className="muted">有效像元统计：{metricSummary}</p><button onClick={loadInterpretation} disabled={interpretationBusy || layerStatus !== 'rendered'}>{interpretationBusy ? '解读生成中…' : '生成分析解读'}</button>{interpretation && <p className="interpretation">{interpretation}</p>}</>}</div><div className="history"><strong>历史分析</strong>{history.slice(-4).reverse().map(item => <button key={item.task_id} onClick={() => openHistory(item)}>{item.title}</button>)}</div></div>{notice && <div className="notice">{notice}</div>}</article></section>{overlay}</main>;
}

createRoot(document.getElementById('root')!).render(<App />);
