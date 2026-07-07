import { useEffect, useRef, useState } from 'react'
import type { AxisAction, ActionRow, OperatorInfo } from '../types'

interface OperatorSearchDialogProps {
  mode: 'add' | 'edit'
  row: ActionRow
  targetFrame: number
  existingAction?: AxisAction
  operators: OperatorInfo[]
  getAvatarUrl?: (oper: string) => Promise<string>
  onConfirm: (action: AxisAction) => void
  onDismiss: () => void
}

const DIR_OPTIONS = ['上', '下', '左', '右'] as const

function OperatorAvatar({ id, getAvatarUrl }: { id: string; getAvatarUrl?: (id: string) => Promise<string> }) {
  const [url, setUrl] = useState<string | undefined>(undefined)
  useEffect(() => {
    let mounted = true
    getAvatarUrl?.(id).then((u) => { if (mounted) setUrl(u || undefined) })
    return () => { mounted = false }
  }, [id, getAvatarUrl])
  return url
    ? <img src={url} alt={id} className="w-6 h-6 rounded-sm object-cover bg-white shrink-0" />
    : <div className="w-6 h-6 rounded-sm bg-white/20 shrink-0" />
}

export function OperatorSearchDialog({
  mode,
  row,
  targetFrame,
  existingAction,
  operators,
  getAvatarUrl,
  onConfirm,
  onDismiss,
}: OperatorSearchDialogProps) {
  const [query, setQuery] = useState('')
  const [selectedOper, setSelectedOper] = useState(existingAction?.oper ?? '')
  const [pos, setPos] = useState(existingAction?.pos ?? '')
  const [direction, setDirection] = useState(existingAction?.direction ?? '')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { inputRef.current?.focus() }, [])

  const filtered = query.trim()
    ? operators.filter((o) => o.name.toLowerCase().includes(query.toLowerCase()))
    : operators.slice(0, 40)

  const canConfirm = !!selectedOper

  const handleConfirm = () => {
    if (!canConfirm) return
    const action: AxisAction = {
      action_type: row === 'deploy' ? '部署' : row === 'skill' ? '技能' : '撤退',
      oper: selectedOper,
      frame: targetFrame,
    }
    if (row === 'deploy') {
      if (pos.trim()) action.pos = pos.trim()
      if (direction) action.direction = direction
    }
    onConfirm(action)
  }

  return (
    <div
      className="fixed inset-0 z-[9500] flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.65)' }}
      onMouseDown={onDismiss}
    >
      <div
        className="w-80 rounded-lg border border-border-panel bg-panel shadow-2xl flex flex-col gap-3 p-4 max-h-[520px]"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="text-text-primary text-sm font-medium">
          {mode === 'add' ? '添加操作' : '编辑操作'}
          <span className="ml-2 text-[10px] text-text-dim">
            {row === 'deploy' ? '部署' : row === 'skill' ? '技能' : '撤退'}
            {' · '}帧 {targetFrame}
          </span>
        </div>

        {/* Operator search */}
        <div className="flex flex-col gap-1.5">
          <label className="text-[10px] text-text-dim uppercase tracking-wide">干员</label>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索干员名称…"
            className="w-full bg-[#0B0F13] border border-border-panel rounded px-2.5 py-1.5 text-sm text-text-primary outline-none focus:border-accent-blue/60 placeholder:text-text-dim"
          />
          <div className="flex flex-col gap-0.5 max-h-40 overflow-y-auto">
            {filtered.map((o) => (
              <button
                key={o.id}
                onClick={() => setSelectedOper(o.id)}
                className={[
                  'flex items-center gap-2 px-2 py-1 rounded text-xs text-left transition-colors',
                  selectedOper === o.id
                    ? 'bg-accent-blue/20 text-text-primary'
                    : 'hover:bg-[#1A1E24] text-text-muted',
                ].join(' ')}
              >
                <OperatorAvatar id={o.id} getAvatarUrl={getAvatarUrl} />
                <span className="truncate">{o.name}</span>
              </button>
            ))}
            {filtered.length === 0 && (
              <p className="text-xs text-text-dim px-2 py-2">无匹配干员</p>
            )}
          </div>
        </div>

        {/* Deploy-only fields */}
        {row === 'deploy' && (
          <>
            <div className="flex gap-3">
              <div className="flex flex-col gap-1 flex-1">
                <label className="text-[10px] text-text-dim uppercase tracking-wide">坐标</label>
                <input
                  value={pos}
                  onChange={(e) => setPos(e.target.value)}
                  placeholder="如 B3"
                  className="w-full bg-[#0B0F13] border border-border-panel rounded px-2.5 py-1.5 text-sm text-text-primary outline-none focus:border-accent-blue/60 placeholder:text-text-dim"
                />
              </div>
              <div className="flex flex-col gap-1 w-24">
                <label className="text-[10px] text-text-dim uppercase tracking-wide">方向</label>
                <select
                  value={direction}
                  onChange={(e) => setDirection(e.target.value)}
                  className="w-full bg-[#0B0F13] border border-border-panel rounded px-2 py-1.5 text-sm text-text-primary outline-none focus:border-accent-blue/60"
                >
                  <option value="">无</option>
                  {DIR_OPTIONS.map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
              </div>
            </div>
          </>
        )}

        {/* Actions */}
        <div className="flex gap-2 mt-1">
          <button
            onClick={handleConfirm}
            disabled={!canConfirm}
            className="flex-1 py-1.5 rounded text-sm text-white font-medium bg-accent-blue/80 hover:bg-accent-blue disabled:opacity-40 transition-colors"
          >
            确认
          </button>
          <button
            onClick={onDismiss}
            className="px-4 py-1.5 rounded text-sm text-text-muted border border-border-panel hover:text-text-primary transition-colors"
          >
            取消
          </button>
        </div>
      </div>
    </div>
  )
}
