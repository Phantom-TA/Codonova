import React, { useState, useEffect, useCallback } from 'react'

const STATUS_COLOR = {
  DONE: '#10b981', FAILED: '#ef4444', BLOCKED: '#f59e0b',
  IN_PROGRESS: '#3b82f6', PENDING: '#64748b',
}

function duration(seconds) {
  if (seconds == null) return '–'
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return m ? `${m}m ${String(s).padStart(2, '0')}s` : `${s}s`
}

const fmt = (v, suffix = '') => (v == null ? '–' : `${v}${suffix}`)

function Tile({ label, value, sub, color = '#e2e8f0' }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 min-w-0">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className="text-xl font-semibold mt-1 truncate" style={{ color }}>{value}</div>
      {sub && <div className="text-[11px] text-slate-500 mt-0.5 truncate">{sub}</div>}
    </div>
  )
}

function Bar({ label, pct, color }) {
  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="text-slate-400">{label}</span>
        <span className="font-semibold" style={{ color }}>{fmt(pct, '%')}</span>
      </div>
      <div className="h-2.5 rounded-full bg-white/5 overflow-hidden">
        <div className="h-full rounded-full transition-all duration-700"
             style={{ width: `${pct || 0}%`, background: color }} />
      </div>
    </div>
  )
}

export default function RunReport({ projectId, projectState }) {
  const [report, setReport] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    if (!projectId) return
    try {
      const res = await fetch(`/api/report/${projectId}`)
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Request failed (${res.status})`)
      setReport(await res.json())
      setError('')
    } catch (e) {
      setError(e.message)
    }
  }, [projectId])

  useEffect(() => {
    setReport(null)
    load()
    // keep refreshing while the pipeline is still working
    const id = setInterval(() => { if (projectState === 'RUNNING') load() }, 4000)
    return () => clearInterval(id)
  }, [load, projectState])

  const download = async () => {
    const res = await fetch(`/api/report/${projectId}/markdown`)
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `RUN_REPORT_${projectId.slice(0, 8)}.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (!projectId) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-slate-500">
        Select a project to see its run report.
      </div>
    )
  }
  if (!report) {
    return <div className="h-full flex items-center justify-center text-sm text-slate-500">
      {error || 'Building report…'}
    </div>
  }

  const { summary: s, llm, code, tasks, per_model: models, pass_mark: passMark } = report
  const maxModelCalls = Math.max(1, ...models.map(m => m.calls))

  return (
    <div className="h-full overflow-y-auto pr-1 space-y-5">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="flex-1 min-w-0">
          <h2 className="text-sm font-semibold text-slate-100 truncate">{report.project.title}</h2>
          <p className="text-xs text-slate-500">
            Status: <span className="text-slate-300">{report.project.status}</span>
            {' · '}passes an attempt at evaluator score ≥ {passMark}
          </p>
        </div>
        <button onClick={load}
                className="px-3 py-1.5 rounded-lg text-xs bg-white/5 hover:bg-white/10 text-slate-300">↻ Refresh</button>
        <button onClick={download}
                className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-brand-600 hover:bg-brand-500 text-white">
          ⬇ Download report (.md)
        </button>
      </div>

      {/* Headline: first pass vs after retries */}
      <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4 grid md:grid-cols-2 gap-5">
        <div className="space-y-3">
          <Bar label="First-pass rate (right on attempt 1)" pct={s.first_pass_rate_pct} color="#a78bfa" />
          <Bar label="Final pass rate (after self-correction)" pct={s.final_pass_rate_pct} color="#10b981" />
        </div>
        <div className="text-xs text-slate-400 leading-relaxed self-center">
          {s.tasks_retried > 0
            ? <>Self-correction retried <b className="text-slate-200">{s.tasks_retried}</b> task(s) and
                fixed <b className="text-emerald-400">{s.fixed_by_retry}</b> of them.</>
            : <>No task needed a retry in this run.</>}
          {llm.absorbed_errors > 0 && (
            <> The API rejected <b className="text-amber-300">{llm.absorbed_errors}</b> request(s) (quota or overload),
              and key/model rotation recovered every one.</>
          )}
        </div>
      </div>

      {/* KPI tiles */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Tile label="Tasks done" value={`${s.tasks_done} / ${s.tasks_total}`}
              sub={`${s.tasks_failed} failed · ${s.tasks_blocked} blocked`}
              color={s.tasks_failed ? '#f59e0b' : '#10b981'} />
        <Tile label="Avg quality" value={fmt(s.avg_quality_score, ' / 10')} sub="evaluator score" />
        <Tile label="Attempts" value={s.total_attempts} sub={`${s.retries} retr${s.retries === 1 ? 'y' : 'ies'}`} />
        <Tile label="Duration" value={duration(s.duration_s)} sub="first task to last task" />
        <Tile label="LLM calls" value={llm.calls} sub={`${llm.failed_calls} ended in failure`} />
        <Tile label="Tokens" value={llm.tokens.toLocaleString()} sub={`avg ${fmt(llm.avg_latency_s, 's')} per call`} />
        <Tile label="API errors absorbed" value={llm.absorbed_errors}
              sub={`${llm.quota_errors}× quota 429 · ${llm.overload_errors}× overload 503`}
              color={llm.absorbed_errors ? '#fbbf24' : '#e2e8f0'} />
        <Tile label="Generated code" value={`${code.lines} lines`} sub={`${code.files} files`} />
      </div>

      {/* Tasks */}
      <div>
        <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">Tasks</h3>
        <div className="rounded-xl border border-white/10 overflow-hidden">
          <table className="w-full text-xs">
            <thead className="bg-white/5 text-slate-500 text-left">
              <tr>
                <th className="px-3 py-2 font-medium">Task</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Attempts (evaluator score)</th>
                <th className="px-3 py-2 font-medium">Tests</th>
                <th className="px-3 py-2 font-medium text-right">Time</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map(t => (
                <tr key={t.id} className="border-t border-white/5">
                  <td className="px-3 py-2 text-slate-200 max-w-[260px]">
                    <div className="truncate">{t.title}</div>
                    <div className="text-[10px] text-slate-600 font-mono truncate">{t.filename || '–'}</div>
                  </td>
                  <td className="px-3 py-2">
                    <span style={{ color: STATUS_COLOR[t.status] || '#94a3b8' }}>{t.status}</span>
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex gap-1 flex-wrap">
                      {t.attempt_log.length === 0 && <span className="text-slate-600">–</span>}
                      {t.attempt_log.map(a => (
                        <span key={a.attempt}
                              className="px-1.5 py-0.5 rounded font-mono"
                              style={{
                                background: a.score >= passMark ? 'rgba(16,185,129,.15)' : 'rgba(239,68,68,.15)',
                                color: a.score >= passMark ? '#6ee7b7' : '#fca5a5',
                              }}>
                          #{a.attempt}: {fmt(a.score)}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-slate-400">
                    {t.tests_passed == null ? '–' : `${t.tests_passed} ✓ / ${t.tests_failed} ✗`}
                  </td>
                  <td className="px-3 py-2 text-right text-slate-400">{duration(t.duration_s)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Models */}
      {models.length > 0 && (
        <div>
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">Models used</h3>
          <div className="space-y-2">
            {models.map(m => (
              <div key={m.model} className="grid grid-cols-[190px_1fr_120px] items-center gap-3 text-xs">
                <span className="font-mono text-slate-300 truncate">{m.model}</span>
                <div className="h-2 rounded-full bg-white/5 overflow-hidden">
                  <div className="h-full bg-brand-500 rounded-full" style={{ width: `${(m.calls / maxModelCalls) * 100}%` }} />
                </div>
                <span className="text-slate-500 text-right">{m.calls} calls · {m.tokens.toLocaleString()} tok</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
