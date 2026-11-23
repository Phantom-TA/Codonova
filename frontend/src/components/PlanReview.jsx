import React, { useState, useEffect, useCallback, useRef } from 'react'

// ─── API helper ───────────────────────────────────────────────────────────────
async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  let data = {}
  try { data = await res.json() } catch { /* empty body */ }
  if (!res.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map(d => d.msg).join('; ')
      : data.detail
    throw new Error(detail || `Request failed (${res.status})`)
  }
  return data
}

const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']
const METHOD_COLOR = {
  GET: 'text-emerald-400', POST: 'text-blue-400', PUT: 'text-amber-400',
  PATCH: 'text-amber-400', DELETE: 'text-red-400',
}

const inputCls =
  'w-full px-3 py-2 bg-white/[0.04] border border-white/10 rounded-lg text-sm text-slate-200 ' +
  'placeholder-slate-600 focus:outline-none focus:border-brand-500/50 disabled:opacity-50'
const btnGhost =
  'px-3 py-1.5 rounded-lg text-xs font-medium text-slate-300 bg-white/5 hover:bg-white/10 ' +
  'transition-all disabled:opacity-40 disabled:cursor-not-allowed'
const btnPrimary =
  'px-3 py-1.5 rounded-lg text-xs font-semibold text-white bg-brand-600 hover:bg-brand-500 ' +
  'transition-all disabled:opacity-40 disabled:cursor-not-allowed'
const btnDanger =
  'px-3 py-1.5 rounded-lg text-xs font-medium text-red-300 bg-red-500/10 hover:bg-red-500/20 ' +
  'transition-all disabled:opacity-40 disabled:cursor-not-allowed'

// ─── Task editor (expanded row) ───────────────────────────────────────────────
function TaskEditor({ task, features, allTasks, editable, run }) {
  const [title, setTitle] = useState(task.title)
  const [description, setDescription] = useState(task.description || '')
  const [priority, setPriority] = useState(task.priority)
  const [featureId, setFeatureId] = useState(task.feature_id)
  const [depPick, setDepPick] = useState('')

  const titleOf = id => allTasks.find(t => t.id === id)?.title || id.slice(0, 8)
  const dirty =
    title !== task.title || description !== (task.description || '') ||
    Number(priority) !== task.priority || featureId !== task.feature_id
  const candidates = allTasks.filter(t => t.id !== task.id && !task.depends_on.includes(t.id))

  const save = () => run(() => api('PATCH', `/api/tasks/${task.id}`, {
    title, description, priority: Number(priority),
    ...(featureId !== task.feature_id ? { feature_id: featureId } : {}),
  }))

  return (
    <div className="mt-3 space-y-3 border-t border-white/5 pt-3">
      <div className="grid grid-cols-[1fr_90px] gap-3">
        <div>
          <label className="text-[10px] uppercase tracking-wider text-slate-500">Title</label>
          <input className={inputCls} value={title} disabled={!editable}
                 onChange={e => setTitle(e.target.value)} />
        </div>
        <div>
          <label className="text-[10px] uppercase tracking-wider text-slate-500">Priority</label>
          <input className={inputCls} type="number" min={1} max={99} value={priority}
                 disabled={!editable} onChange={e => setPriority(e.target.value)} />
        </div>
      </div>
      <div>
        <label className="text-[10px] uppercase tracking-wider text-slate-500">Description</label>
        <textarea className={inputCls + ' h-20 resize-none'} value={description}
                  disabled={!editable} onChange={e => setDescription(e.target.value)} />
      </div>
      <div>
        <label className="text-[10px] uppercase tracking-wider text-slate-500">Feature</label>
        <select className={inputCls} value={featureId} disabled={!editable}
                onChange={e => setFeatureId(e.target.value)}>
          {features.map(f => <option key={f.id} value={f.id}>{f.title}</option>)}
        </select>
      </div>

      <div>
        <label className="text-[10px] uppercase tracking-wider text-slate-500">
          Depends on (runs after these tasks)
        </label>
        <div className="flex flex-wrap gap-1.5 mt-1.5">
          {task.depends_on.length === 0 && (
            <span className="text-xs text-slate-600">No dependencies — can start immediately</span>
          )}
          {task.depends_on.map(id => (
            <span key={id}
                  className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md bg-brand-600/15 text-brand-300 text-xs">
              {titleOf(id)}
              {editable && (
                <button title="Remove dependency" className="hover:text-white"
                        onClick={() => run(() => api('DELETE', `/api/tasks/${task.id}/dependencies/${id}`))}>
                  ×
                </button>
              )}
            </span>
          ))}
        </div>
        {editable && candidates.length > 0 && (
          <div className="flex gap-2 mt-2">
            <select className={inputCls} value={depPick} onChange={e => setDepPick(e.target.value)}>
              <option value="">Add a dependency…</option>
              {candidates.map(t => <option key={t.id} value={t.id}>{t.title}</option>)}
            </select>
            <button className={btnGhost} disabled={!depPick}
                    onClick={() => run(async () => {
                      await api('POST', `/api/tasks/${task.id}/dependencies`, { depends_on: depPick })
                      setDepPick('')
                    })}>
              Add
            </button>
          </div>
        )}
      </div>

      {editable && (
        <div className="flex justify-between pt-1">
          <button className={btnDanger}
                  onClick={() => {
                    if (window.confirm(`Delete task "${task.title}"?`))
                      run(() => api('DELETE', `/api/tasks/${task.id}`))
                  }}>
            Delete task
          </button>
          <button className={btnPrimary} disabled={!dirty || !title.trim()} onClick={save}>
            Save changes
          </button>
        </div>
      )}
    </div>
  )
}

// ─── Add-task form ────────────────────────────────────────────────────────────
function AddTaskForm({ featureId, run, onDone }) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  return (
    <div className="mt-2 p-3 rounded-lg border border-dashed border-brand-500/30 space-y-2">
      <input className={inputCls} placeholder="New task title" value={title}
             onChange={e => setTitle(e.target.value)} autoFocus />
      <textarea className={inputCls + ' h-16 resize-none'} placeholder="What should be built? (one file per task)"
                value={description} onChange={e => setDescription(e.target.value)} />
      <div className="flex justify-end gap-2">
        <button className={btnGhost} onClick={onDone}>Cancel</button>
        <button className={btnPrimary} disabled={!title.trim()}
                onClick={() => run(async () => {
                  await api('POST', `/api/projects/${featureId.projectId}/tasks`, {
                    feature_id: featureId.id, title: title.trim(), description,
                  })
                  onDone()
                })}>
          Add task
        </button>
      </div>
    </div>
  )
}

// ─── Feature card ─────────────────────────────────────────────────────────────
function FeatureCard({ feature, projectId, features, allTasks, editable, run, expanded, setExpanded }) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(feature.title)
  const [description, setDescription] = useState(feature.description || '')
  const [adding, setAdding] = useState(false)

  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          {editing ? (
            <div className="space-y-2">
              <input className={inputCls} value={title} onChange={e => setTitle(e.target.value)} />
              <textarea className={inputCls + ' h-16 resize-none'} value={description}
                        onChange={e => setDescription(e.target.value)} />
              <div className="flex gap-2">
                <button className={btnPrimary} disabled={!title.trim()}
                        onClick={() => run(async () => {
                          await api('PATCH', `/api/features/${feature.id}`, { title, description })
                          setEditing(false)
                        })}>Save</button>
                <button className={btnGhost} onClick={() => setEditing(false)}>Cancel</button>
              </div>
            </div>
          ) : (
            <>
              <h3 className="font-semibold text-slate-100 text-sm">{feature.title}</h3>
              {feature.description && (
                <p className="text-xs text-slate-500 mt-0.5">{feature.description}</p>
              )}
            </>
          )}
        </div>
        {editable && !editing && (
          <div className="flex gap-1.5 flex-shrink-0">
            <button className={btnGhost} onClick={() => setEditing(true)}>Edit</button>
            <button className={btnDanger}
                    onClick={() => {
                      const n = feature.tasks.length
                      if (window.confirm(`Delete feature "${feature.title}" and its ${n} task(s)?`))
                        run(() => api('DELETE', `/api/features/${feature.id}`))
                    }}>
              Delete
            </button>
          </div>
        )}
      </div>

      <div className="mt-3 space-y-2">
        {feature.tasks.length === 0 && (
          <p className="text-xs text-amber-400/80">No tasks in this feature yet.</p>
        )}
        {feature.tasks.map(task => {
          const open = expanded === task.id
          return (
            <div key={task.id} id={`plan-task-${task.id}`}
                 className={`rounded-lg border p-3 transition-all ${
                   open ? 'border-brand-500/40 bg-brand-600/[0.06]' : 'border-white/5 bg-white/[0.02] hover:border-white/15'
                 }`}>
              <button className="w-full flex items-center gap-3 text-left"
                      onClick={() => setExpanded(open ? null : task.id)}>
                <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-white/5 text-slate-400">
                  P{task.priority}
                </span>
                <span className="flex-1 min-w-0 text-sm text-slate-200 truncate">{task.title}</span>
                {task.file && (
                  <span className="text-[10px] font-mono text-slate-500 flex-shrink-0">{task.file}</span>
                )}
                {task.depends_on.length > 0 && (
                  <span className="text-[10px] text-brand-300 flex-shrink-0">
                    ⛓ {task.depends_on.length} dep{task.depends_on.length > 1 ? 's' : ''}
                  </span>
                )}
                <span className="text-slate-600 text-xs">{open ? '▲' : '▼'}</span>
              </button>
              {!open && task.description && (
                <p className="text-xs text-slate-500 mt-1.5 line-clamp-2">{task.description}</p>
              )}
              {open && (
                <TaskEditor task={task} features={features} allTasks={allTasks}
                            editable={editable} run={run} />
              )}
            </div>
          )
        })}

        {editable && (adding
          ? <AddTaskForm featureId={{ id: feature.id, projectId }} run={run} onDone={() => setAdding(false)} />
          : <button className={btnGhost + ' w-full'} onClick={() => setAdding(true)}>+ Add task</button>
        )}
      </div>
    </div>
  )
}

// ─── API contract editor ──────────────────────────────────────────────────────
function ApiContract({ projectId, schema, editable, run }) {
  const [rows, setRows] = useState(schema.endpoints || [])
  const [port, setPort] = useState(schema.base_port || 4000)

  useEffect(() => {
    setRows(schema.endpoints || [])
    setPort(schema.base_port || 4000)
  }, [schema])

  const update = (i, patch) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))

  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-500">
        The Developer Agent must follow this contract exactly. The backend returns these exact JSON shapes and
        the frontend reads those exact field names.
      </p>
      <div className="space-y-2">
        {rows.length === 0 && <p className="text-xs text-slate-600">No endpoints defined.</p>}
        {rows.map((r, i) => (
          <div key={i} className="rounded-lg border border-white/5 bg-white/[0.02] p-2.5 space-y-2">
            <div className="grid grid-cols-[95px_1fr_1.2fr_auto] gap-2 items-center">
              <select className={inputCls + ' font-mono ' + (METHOD_COLOR[r.method] || '')}
                      value={r.method} disabled={!editable}
                      onChange={e => update(i, { method: e.target.value })}>
                {METHODS.map(m => <option key={m}>{m}</option>)}
              </select>
              <input className={inputCls + ' font-mono'} value={r.path} disabled={!editable}
                     placeholder="/api/items" onChange={e => update(i, { path: e.target.value })} />
              <input className={inputCls} value={r.description || ''} disabled={!editable}
                     placeholder="What it does" onChange={e => update(i, { description: e.target.value })} />
              {editable && (
                <button className="text-slate-600 hover:text-red-400 px-2" title="Remove"
                        onClick={() => setRows(rows.filter((_, j) => j !== i))}>×</button>
              )}
            </div>
            <div className="grid grid-cols-2 gap-2">
              <input className={inputCls + ' font-mono text-xs'} value={r.request || ''} disabled={!editable}
                     placeholder='request body, e.g. {"name": "Ana"}'
                     onChange={e => update(i, { request: e.target.value })} />
              <input className={inputCls + ' font-mono text-xs'} value={r.response || ''} disabled={!editable}
                     placeholder='response JSON, e.g. {"count": 0}'
                     onChange={e => update(i, { response: e.target.value })} />
            </div>
          </div>
        ))}
      </div>
      {editable && (
        <div className="flex items-center justify-between gap-3 pt-1">
          <div className="flex items-center gap-2">
            <button className={btnGhost}
                    onClick={() => setRows([...rows, { method: 'GET', path: '/api/', description: '' }])}>
              + Add endpoint
            </button>
            <label className="text-xs text-slate-500 ml-2">Port</label>
            <input className={inputCls + ' !w-24'} type="number" value={port}
                   onChange={e => setPort(e.target.value)} />
          </div>
          <button className={btnPrimary}
                  onClick={() => run(() => api('PUT', `/api/projects/${projectId}/api-schema`,
                    { endpoints: rows, base_port: Number(port) }))}>
            Save contract
          </button>
        </div>
      )}
    </div>
  )
}

// ─── History ──────────────────────────────────────────────────────────────────
function History({ projectId, version }) {
  const [items, setItems] = useState([])
  useEffect(() => {
    api('GET', `/api/projects/${projectId}/revisions`).then(setItems).catch(() => setItems([]))
  }, [projectId, version])

  const summarize = r => {
    let after = {}, before = {}
    try { after = r.after ? JSON.parse(r.after) : {} } catch { /* ignore */ }
    try { before = r.before ? JSON.parse(r.before) : {} } catch { /* ignore */ }
    const name = after.title || before.title || ''
    return name ? `“${name}”` : ''
  }

  if (!items.length) return <p className="text-xs text-slate-600">No edits yet — the plan is exactly as the Planning Agent produced it.</p>
  return (
    <ul className="space-y-1.5">
      {items.map(r => (
        <li key={r.seq} className="flex items-center gap-3 text-xs px-3 py-2 rounded-lg bg-white/[0.02] border border-white/5">
          <span className="font-mono text-slate-600 w-6">#{r.seq}</span>
          <span className="px-1.5 py-0.5 rounded bg-white/5 text-slate-300">{r.action.replace('_', ' ')}</span>
          <span className="text-slate-400">{r.entity_type.replace('_', ' ')}</span>
          <span className="text-slate-300 truncate flex-1">{summarize(r)}</span>
          <span className="text-slate-600 flex-shrink-0">{new Date(r.created_at + 'Z').toLocaleTimeString()}</span>
        </li>
      ))}
    </ul>
  )
}

// ─── Main panel ───────────────────────────────────────────────────────────────
export default function PlanReview({ projectId, focusTask, projectState, onApproved }) {
  const [plan, setPlan] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState('tasks')
  const [expanded, setExpanded] = useState(null)
  const [version, setVersion] = useState(0)
  const [addingFeature, setAddingFeature] = useState(false)
  const [newFeature, setNewFeature] = useState('')
  const scrolled = useRef(null)

  const load = useCallback(async () => {
    if (!projectId) return
    try {
      setPlan(await api('GET', `/api/projects/${projectId}/plan`))
    } catch (e) {
      setError(e.message)
    }
  }, [projectId])

  useEffect(() => { setPlan(null); setError(''); setExpanded(null); load() }, [load, projectState])

  // Run a mutation, then reload the plan. Errors (e.g. "would create a cycle") are shown.
  const run = useCallback(async (fn) => {
    setBusy(true)
    setError('')
    try {
      await fn()
      setVersion(v => v + 1)
      await load()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }, [load])

  // Opened from a Task Graph node click: expand and scroll to that task
  useEffect(() => {
    if (!focusTask || !plan || scrolled.current === focusTask.nonce) return
    scrolled.current = focusTask.nonce
    setTab('tasks')
    setExpanded(focusTask.id)
    setTimeout(() => {
      document.getElementById(`plan-task-${focusTask.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, 80)
  }, [focusTask, plan])

  const approve = async () => {
    const n = plan.features.reduce((s, f) => s + f.tasks.length, 0)
    if (!window.confirm(`Approve this plan and start implementing ${n} task(s)? The plan will be locked.`)) return
    setBusy(true)
    setError('')
    try {
      await api('POST', `/api/execute/${projectId}`)
      onApproved && onApproved()
      await load()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (!projectId) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-slate-500">
        Plan a project first — use “Plan Only” in the launcher, then review and edit the plan here.
      </div>
    )
  }
  if (!plan) {
    return <div className="h-full flex items-center justify-center text-sm text-slate-500">
      {error || 'Loading plan…'}
    </div>
  }

  const { project, features, api_schema, warnings } = plan
  const editable = project.editable
  const allTasks = features.flatMap(f => f.tasks)

  return (
    <div className="h-full flex flex-col gap-3 min-h-0">
      {/* Header */}
      <div className="flex items-start gap-4">
        <div className="flex-1 min-w-0">
          <h2 className="text-sm font-semibold text-slate-100 truncate">{project.title}</h2>
          <p className="text-xs text-slate-500 mt-0.5 line-clamp-2">{project.description}</p>
        </div>
        {editable ? (
          <button className={btnPrimary + ' !px-4 !py-2 flex-shrink-0'} disabled={busy || allTasks.length === 0}
                  onClick={approve}>
            🚀 Approve &amp; Implement
          </button>
        ) : (
          <span className="flex-shrink-0 text-xs px-3 py-1.5 rounded-lg bg-amber-500/10 text-amber-300 border border-amber-500/20">
            🔒 Plan locked — {project.status}
          </span>
        )}
      </div>

      {error && (
        <div className="text-xs text-red-300 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2 flex justify-between">
          <span>{error}</span>
          <button onClick={() => setError('')} className="text-red-400 hover:text-white">×</button>
        </div>
      )}
      {editable && warnings.length > 0 && (
        <div className="text-xs text-amber-300/90 bg-amber-500/5 border border-amber-500/15 rounded-lg px-3 py-2">
          {warnings.map((w, i) => <div key={i}>⚠ {w}</div>)}
        </div>
      )}

      {/* Tabs */}
      <div className="flex items-center gap-1 p-1 bg-white/5 rounded-lg self-start">
        {[['tasks', `Tasks (${allTasks.length})`], ['api', `API Contract (${api_schema.endpoints?.length || 0})`], ['history', 'History']]
          .map(([id, label]) => (
            <button key={id} onClick={() => setTab(id)}
                    className={`px-3 py-1.5 rounded text-xs font-medium transition-all ${
                      tab === id ? 'bg-brand-600 text-white' : 'text-slate-400 hover:text-white'}`}>
              {label}
            </button>
          ))}
      </div>

      {/* Body */}
      <div className={`flex-1 overflow-y-auto pr-1 space-y-3 ${busy ? 'opacity-70 pointer-events-none' : ''}`}>
        {tab === 'tasks' && (
          <>
            {features.map(f => (
              <FeatureCard key={f.id} feature={f} projectId={projectId} features={features}
                           allTasks={allTasks} editable={editable} run={run}
                           expanded={expanded} setExpanded={setExpanded} />
            ))}
            {editable && (addingFeature ? (
              <div className="p-3 rounded-lg border border-dashed border-brand-500/30 space-y-2">
                <input className={inputCls} placeholder="New feature name" value={newFeature}
                       onChange={e => setNewFeature(e.target.value)} autoFocus />
                <div className="flex justify-end gap-2">
                  <button className={btnGhost} onClick={() => setAddingFeature(false)}>Cancel</button>
                  <button className={btnPrimary} disabled={!newFeature.trim()}
                          onClick={() => run(async () => {
                            await api('POST', `/api/projects/${projectId}/features`, { title: newFeature.trim() })
                            setNewFeature('')
                            setAddingFeature(false)
                          })}>Add feature</button>
                </div>
              </div>
            ) : (
              <button className={btnGhost + ' w-full'} onClick={() => setAddingFeature(true)}>+ Add feature</button>
            ))}
          </>
        )}
        {tab === 'api' && (
          <ApiContract projectId={projectId} schema={api_schema} editable={editable} run={run} />
        )}
        {tab === 'history' && <History projectId={projectId} version={version} />}
      </div>
    </div>
  )
}
