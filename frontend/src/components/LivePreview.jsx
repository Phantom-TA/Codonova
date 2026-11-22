import React, { useState, useEffect, useCallback, useRef } from 'react'

const STATUS_META = {
  idle:       { label: 'Not running', color: '#64748b' },
  installing: { label: 'Installing dependencies…', color: '#3b82f6' },
  starting:   { label: 'Starting…', color: '#3b82f6' },
  running:    { label: 'Running', color: '#10b981' },
  crashed:    { label: 'Crashed', color: '#ef4444' },
  failed:     { label: 'Failed to start', color: '#ef4444' },
  stopped:    { label: 'Stopped', color: '#f59e0b' },
}

const btn =
  'px-3 py-1.5 rounded-lg text-xs font-medium transition-all disabled:opacity-40 disabled:cursor-not-allowed'

export default function LivePreview({ projectId, projectState }) {
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [showLogs, setShowLogs] = useState(true)
  const [frameKey, setFrameKey] = useState(0)
  const logRef = useRef(null)

  const load = useCallback(async () => {
    if (!projectId) return
    try {
      const res = await fetch(`/api/preview/${projectId}/status`)
      if (res.ok) setPreview(await res.json())
    } catch { /* backend busy, try again on next tick */ }
  }, [projectId])

  useEffect(() => {
    setPreview(null)
    setError('')
    load()
    const id = setInterval(load, 1500)
    return () => clearInterval(id)
  }, [load])

  // Keep the log view scrolled to the newest line
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [preview?.logs?.length])

  const call = async (action) => {
    setBusy(true)
    setError('')
    try {
      const res = await fetch(`/api/preview/${projectId}/${action}`, { method: 'POST' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`)
      setPreview(data)
      if (action !== 'stop') setFrameKey(k => k + 1)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (!projectId) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-slate-500">
        Select or create a project first. After the pipeline finishes you can run the generated app here.
      </div>
    )
  }

  const status = preview?.status || 'idle'
  const meta = STATUS_META[status] || STATUS_META.idle
  const active = ['installing', 'starting', 'running'].includes(status)
  const pipelineBusy = projectState === 'RUNNING'
  const noCode = preview && preview.has_code === false

  return (
    <div className="h-full flex flex-col gap-3 min-h-0">
      {/* Toolbar */}
      <div className="flex items-center gap-3 flex-wrap">
        <span className="inline-flex items-center gap-2 text-xs px-3 py-1.5 rounded-full bg-white/5">
          <span className="w-2 h-2 rounded-full"
                style={{ background: meta.color, boxShadow: active ? `0 0 8px ${meta.color}` : 'none' }} />
          <span style={{ color: meta.color }} className="font-medium">{meta.label}</span>
        </span>

        {status === 'running' && preview.url && (
          <span className="text-xs font-mono text-slate-400 truncate">{preview.url}</span>
        )}

        <div className="ml-auto flex items-center gap-2">
          {!active && (
            <button className={`${btn} bg-brand-600 hover:bg-brand-500 text-white font-semibold`}
                    disabled={busy || noCode} onClick={() => call('start')}>
              ▶ Run App
            </button>
          )}
          {active && (
            <>
              <button className={`${btn} bg-white/5 hover:bg-white/10 text-slate-200`}
                      disabled={busy} onClick={() => call('restart')}>↻ Restart</button>
              <button className={`${btn} bg-red-500/10 hover:bg-red-500/20 text-red-300`}
                      disabled={busy} onClick={() => call('stop')}>■ Stop</button>
            </>
          )}
          {status === 'running' && (
            <>
              <button className={`${btn} bg-white/5 hover:bg-white/10 text-slate-200`}
                      onClick={() => setFrameKey(k => k + 1)}>⟳ Reload page</button>
              <a href={preview.url} target="_blank" rel="noopener noreferrer"
                 className={`${btn} bg-white/5 hover:bg-white/10 text-slate-200`}>Open in new tab ↗</a>
            </>
          )}
        </div>
      </div>

      {pipelineBusy && (
        <div className="text-xs text-amber-300/90 bg-amber-500/5 border border-amber-500/15 rounded-lg px-3 py-2">
          ⚠ The pipeline is still generating code, so the app may be incomplete. Run it again when it finishes.
        </div>
      )}
      {noCode && (
        <div className="text-xs text-slate-400 bg-white/[0.03] border border-white/5 rounded-lg px-3 py-2">
          No code has been generated for this project yet.
        </div>
      )}
      {(error || preview?.error) && (
        <div className="text-xs text-red-300 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
          {error || preview.error}
        </div>
      )}

      {/* App view */}
      <div className="flex-1 min-h-0 rounded-xl border border-white/10 bg-white overflow-hidden relative">
        {status === 'running' && preview.url ? (
          <iframe key={frameKey} title="Generated app" src={preview.url}
                  className="w-full h-full border-0 bg-white" />
        ) : (
          <div className="absolute inset-0 bg-[var(--bg-secondary)] flex flex-col items-center justify-center gap-3 text-center px-6">
            {active ? (
              <>
                <div className="w-8 h-8 rounded-full border-2 border-brand-500/30 border-t-brand-400 animate-spin" />
                <p className="text-sm text-slate-300">{meta.label}</p>
                <p className="text-xs text-slate-500">The first run installs the app's npm packages and takes a little longer.</p>
              </>
            ) : (
              <>
                <div className="text-3xl">🖥️</div>
                <p className="text-sm text-slate-300">
                  {status === 'idle' ? 'The generated app is not running.' : meta.label}
                </p>
                <p className="text-xs text-slate-500 max-w-sm">
                  Press “Run App” to install its dependencies and start it. It runs inside the backend
                  container and is shown here live.
                </p>
              </>
            )}
          </div>
        )}
      </div>

      {/* Logs */}
      <div className="flex-shrink-0">
        <button className="text-xs text-slate-500 hover:text-slate-300 mb-1.5"
                onClick={() => setShowLogs(v => !v)}>
          {showLogs ? '▼' : '▶'} Process log ({preview?.logs?.length || 0} lines)
        </button>
        {showLogs && (
          <pre ref={logRef}
               className="h-32 overflow-auto rounded-lg bg-black/40 border border-white/5 p-3 text-[11px]
                          leading-relaxed font-mono text-slate-300 whitespace-pre-wrap">
            {preview?.logs?.length ? preview.logs.join('\n') : 'No output yet.'}
          </pre>
        )}
      </div>
    </div>
  )
}
