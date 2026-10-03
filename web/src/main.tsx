import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import 'maplibre-gl/dist/maplibre-gl.css';
import './styles.css';

type Capability = { indicator: string; label: string; analysis_type: string; scientific_limit: string };
type RunMap = { indicator: string; period: Record<string, string>; aoi_label: string; data_source: string; bounds?: number[]; scientific_limit: string; artifacts: Record<string, string> };

async function api<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) } });
  if (!response.ok) throw new Error(response.status === 401 ? '登录状态已失效，请重新输入访问令牌。' : (await response.json().catch(() => null))?.detail ?? '请求未完成，请稍后重试。');
  return response.json();
}

function App() {
  const [token, setToken] = useState('');
  const [input, setInput] = useState('请分析武汉东湖 2023 年 7 月和 2024 年 7 月的 NDVI 变化');
  const [capabilities, setCapabilities] = useState<Capability[]>([]);
  const [result, setResult] = useState<RunMap | null>(null);
  const [message, setMessage] = useState('请输入访问令牌后开始分析。');
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (token) api<{capabilities: Capability[]}>('/api/v1/geochange/capabilities', token).then(v => setCapabilities(v.capabilities)).catch(e => setMessage(e.message)); }, [token]);
  async function submit() {
    if (!token) { setMessage('请先输入访问令牌。'); return; }
    setBusy(true); setMessage('正在整理分析请求…');
    try {
      const proposal = await api<any>('/api/v1/conversation', token, { method: 'POST', body: JSON.stringify({ message: input }) });
      if (proposal.kind !== 'proposal' || !proposal.proposal) { setMessage(proposal.message ?? '请补充分析时段。'); return; }
      const created = await api<any>('/api/v1/conversation/confirm', token, { method: 'POST', body: JSON.stringify({ proposal: proposal.proposal }) });
      const taskId = created.result?.task_id;
      if (!taskId) throw new Error('任务未创建成功。');
      const run = await api<any>(`/api/v1/tasks/${taskId}/runs`, token, { method: 'POST' });
      setMessage('分析已提交，正在等待结果…');
      for (let i = 0; i < 30; i++) { await new Promise(r => setTimeout(r, 1000)); const current = await api<any>(`/api/v1/tasks/${taskId}/runs/${run.id}`, token); if (current.status === 'succeeded') { setResult(await api<RunMap>(`/api/v1/tasks/${taskId}/runs/${run.id}/map`, token)); setMessage('分析完成。'); break; } if (current.status === 'failed' || current.status === 'cancelled') throw new Error('分析未完成，请查看任务历史。'); }
    } catch (e) { setMessage(e instanceof Error ? e.message : '分析请求失败。'); } finally { setBusy(false); }
  }
  return <main><header><strong>✦ TaskPilot</strong><span>AI 遥感智能分析平台</span><nav>分析工作台　我的分析　使用帮助</nav></header><section className="workspace"><aside><h1>AI 遥感助手</h1><p className="muted">在受验证的数据范围内，用一句话描述你想比较的指标和时段。</p><label>访问令牌<input type="password" value={token} onChange={e => setToken(e.target.value)} placeholder="仅保存在当前页面内" /></label><label>分析请求<textarea value={input} onChange={e => setInput(e.target.value)} /></label><div className="chips">{capabilities.map(c => <button key={c.indicator} onClick={() => setInput(`请分析武汉东湖 2023 年 7 月和 2024 年 7 月的 ${c.indicator} 变化`)}>{c.indicator} · {c.label}</button>)}</div><button className="primary" onClick={submit} disabled={busy}>{busy ? '分析中…' : '开始分析'}</button><p className="status">{message}</p></aside><article><div className="map"><div className="map-empty"><span>◎</span><h2>{result ? `${result.indicator} · ${result.aoi_label}` : '地图结果将在这里显示'}</h2><p>{result ? '当前 API 未返回可信地图范围或真实栅格 artifact。' : '提交分析后，这里会加载已验证的地图结果。'}</p></div></div><div className="summary"><h2>{result ? '结果摘要' : '分析结果'}</h2>{result ? <><p>{result.data_source}</p><p>{result.period.period_a ?? ''} → {result.period.period_b ?? ''}</p><small>{result.scientific_limit}</small></> : <p className="muted">暂无可视化数据</p>}</div></article></section></main>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
