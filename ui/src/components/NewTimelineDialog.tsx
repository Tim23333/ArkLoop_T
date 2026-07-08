import { useEffect, useMemo, useRef, useState } from 'react'
import type { MapDevice, TimelinePreset, TimelineSettings } from '../hooks/useBackend'
import { ConfirmDialog, PromptDialog } from './Modal'

export interface MapInfo {
  code: string
  name: string
}

export interface NewTimelineResult {
  mapCode: string
  mapName: string
  maxTick: number
  devices: MapDevice[]
}

interface NewTimelineDialogProps {
  maps: MapInfo[]
  presets?: TimelinePreset[]
  onConfirm: (result: NewTimelineResult) => void
  onDismiss: () => void
  onSavePreset?: (name: string, settings: TimelineSettings) => Promise<boolean> | void
  onDeletePreset?: (name: string) => Promise<boolean> | void
}

export function NewTimelineDialog({
  maps,
  presets = [],
  onConfirm,
  onDismiss,
  onSavePreset,
  onDeletePreset,
}: NewTimelineDialogProps) {
  const [query, setQuery] = useState('')
  const [selectedMap, setSelectedMap] = useState<MapInfo | null>(null)
  const [showDropdown, setShowDropdown] = useState(false)
  const [maxTick, setMaxTick] = useState(30)
  const [devices, setDevices] = useState<MapDevice[]>([])
  const [selectedPreset, setSelectedPreset] = useState<string>('')
  const [showSavePresetPrompt, setShowSavePresetPrompt] = useState(false)
  const [showDeletePresetConfirm, setShowDeletePresetConfirm] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return maps.slice(0, 50)
    return maps.filter(
      (m) => m.code.toLowerCase().includes(q) || m.name.toLowerCase().includes(q),
    ).slice(0, 50)
  }, [maps, query])

  const handleSelectMap = (m: MapInfo) => {
    setSelectedMap(m)
    setQuery(`${m.code}${m.name ? ` ${m.name}` : ''}`)
    setShowDropdown(false)
  }

  const buildCurrentSettings = (): TimelineSettings => ({
    map_code: selectedMap?.code ?? query.trim() ?? undefined,
    map_name: selectedMap?.name || undefined,
    max_tick: maxTick,
    devices: devices.length > 0 ? devices : undefined,
  })

  const handleApplyPreset = (name: string) => {
    setSelectedPreset(name)
    if (!name) return
    const p = presets.find((x) => x.name === name)
    if (!p) return
    const s = p.settings ?? {}
    if (s.map_code) {
      const matched = maps.find((m) => m.code === s.map_code)
      if (matched) {
        setSelectedMap(matched)
        setQuery(`${matched.code}${matched.name ? ` ${matched.name}` : ''}`)
      } else {
        setSelectedMap(null)
        setQuery(s.map_code)
      }
    }
    if (typeof s.max_tick === 'number' && s.max_tick > 0) setMaxTick(s.max_tick)
    setDevices(Array.isArray(s.devices) ? s.devices.map((d) => ({ ...d })) : [])
  }

  const handleSavePreset = async (name: string) => {
    setShowSavePresetPrompt(false)
    if (!onSavePreset) return
    const clean = name.trim()
    if (!clean) return
    await onSavePreset(clean, buildCurrentSettings())
  }

  const handleDeletePreset = async () => {
    setShowDeletePresetConfirm(false)
    if (!onDeletePreset || !selectedPreset) return
    await onDeletePreset(selectedPreset)
    setSelectedPreset('')
  }

  const handleConfirm = () => {
    const code = selectedMap?.code ?? query.trim()
    const name = selectedMap?.name ?? ''
    if (!code) return
    onConfirm({
      mapCode: code,
      mapName: name,
      maxTick,
      devices: devices.map((d, i) => ({
        name: (d.name || `装置${i + 1}`).trim(),
        pos: d.pos.trim(),
      })),
    })
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      if (showDropdown && filtered.length > 0) {
        handleSelectMap(filtered[0])
      } else {
        handleConfirm()
      }
    }
    if (e.key === 'Escape') onDismiss()
    if (e.key === 'ArrowDown' && filtered.length > 0) {
      setShowDropdown(true)
      ;(dropdownRef.current?.querySelector('button') as HTMLButtonElement | null)?.focus()
    }
  }

  const addDevice = () => {
    setDevices((prev) => [...prev, { name: `装置${prev.length + 1}`, pos: '' }])
  }
  const updateDevice = (idx: number, patch: Partial<MapDevice>) => {
    setDevices((prev) => prev.map((d, i) => (i === idx ? { ...d, ...patch } : d)))
  }
  const removeDevice = (idx: number) => {
    setDevices((prev) => prev.filter((_, i) => i !== idx))
  }

  return (
    <div
      className="fixed inset-0 z-[9000] flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.6)' }}
      onMouseDown={onDismiss}
    >
      <div
        className="w-[420px] rounded-lg border border-border-panel bg-panel shadow-2xl p-5 flex flex-col gap-4"
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* Header: title + preset controls */}
        <div className="flex items-center justify-between gap-2">
          <div className="text-text-primary text-sm font-medium">新建轴</div>
          <div className="flex items-center gap-1.5">
            <button
              onClick={() => setShowSavePresetPrompt(true)}
              className="text-xs px-2 py-1 rounded border border-border-panel text-text-muted hover:border-accent-blue/50 hover:text-accent-blue transition-colors"
              title="把当前配置保存为预设"
            >
              保存为预设
            </button>
            <select
              value={selectedPreset}
              onChange={(e) => handleApplyPreset(e.target.value)}
              className="bg-[#0B0F13] border border-border-panel rounded px-2 py-1 text-xs text-text-primary outline-none focus:border-accent-blue/60 max-w-[120px]"
            >
              <option value="">载入预设…</option>
              {presets.map((p) => (
                <option key={p.name} value={p.name}>{p.name}</option>
              ))}
            </select>
            <button
              onClick={() => setShowDeletePresetConfirm(true)}
              disabled={!selectedPreset}
              className="text-xs w-6 h-6 leading-none rounded border border-border-panel text-text-dim hover:border-accent-red/50 hover:text-accent-red disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:border-border-panel disabled:hover:text-text-dim transition-colors"
              title={selectedPreset ? `删除预设 "${selectedPreset}"` : '先选中一个预设'}
            >
              ×
            </button>
          </div>
        </div>

        {/* Map search */}
        <div className="flex flex-col gap-1.5 relative">
          <label className="text-xs text-text-dim">地图</label>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
              setSelectedMap(null)
              setShowDropdown(true)
            }}
            onFocus={() => setShowDropdown(true)}
            onBlur={() => setTimeout(() => setShowDropdown(false), 150)}
            onKeyDown={handleKeyDown}
            placeholder="输入地图代号或中文名搜索…"
            className="w-full bg-[#0B0F13] border border-border-panel rounded px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent-blue/60"
          />
          {showDropdown && filtered.length > 0 && (
            <div
              ref={dropdownRef}
              className="absolute top-full left-0 right-0 mt-1 max-h-48 overflow-y-auto rounded border border-border-panel bg-panel shadow-xl z-10"
            >
              {filtered.map((m) => (
                <button
                  key={m.code}
                  onMouseDown={() => handleSelectMap(m)}
                  className="w-full text-left px-3 py-1.5 text-sm hover:bg-[#1a2330] flex gap-2 items-baseline"
                >
                  <span className="text-accent-blue font-mono text-xs">{m.code}</span>
                  {m.name && <span className="text-text-muted text-xs">{m.name}</span>}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Timing compatibility */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs text-text-dim">max_tick (legacy timeline compatibility)</label>
          <input
            type="number"
            min={1}
            value={maxTick}
            onChange={(e) => {
              const v = parseInt(e.target.value, 10)
              setMaxTick(Number.isFinite(v) && v > 0 ? v : 30)
            }}
            className="w-full bg-[#0B0F13] border border-border-panel rounded px-3 py-1.5 text-sm text-text-primary font-mono outline-none focus:border-accent-blue/60"
          />
        </div>

        {/* Devices */}
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between">
            <label className="text-xs text-text-dim">局内装置</label>
            <button
              onClick={addDevice}
              className="text-xs w-5 h-5 leading-none rounded border border-border-panel text-text-muted hover:border-accent-blue/50 hover:text-accent-blue transition-colors"
              title="新增一个装置"
            >
              +
            </button>
          </div>
          {devices.length === 0 ? (
            <div className="text-xs text-text-dim italic">无</div>
          ) : (
            <div className="flex flex-col gap-1">
              {devices.map((d, i) => (
                <div key={i} className="flex items-center gap-1.5">
                  <input
                    value={d.name}
                    placeholder={`装置${i + 1}`}
                    onChange={(e) => updateDevice(i, { name: e.target.value })}
                    className="flex-1 bg-[#0B0F13] border border-border-panel rounded px-2 py-1 text-xs text-text-primary outline-none focus:border-accent-blue/60"
                  />
                  <input
                    value={d.pos}
                    placeholder="坐标 如 C3"
                    onChange={(e) => updateDevice(i, { pos: e.target.value })}
                    className="w-20 bg-[#0B0F13] border border-border-panel rounded px-2 py-1 text-xs font-mono text-text-primary outline-none focus:border-accent-blue/60"
                  />
                  <button
                    onClick={() => removeDevice(i)}
                    className="text-xs w-5 h-5 leading-none rounded border border-border-panel text-text-dim hover:border-accent-red/50 hover:text-accent-red transition-colors"
                    title="删除该装置"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="flex gap-2">
          <button
            onClick={handleConfirm}
            disabled={!query.trim()}
            className="flex-1 py-1.5 rounded text-sm text-white font-medium bg-accent-blue/80 hover:bg-accent-blue disabled:opacity-40 transition-colors"
          >
            创建
          </button>
          <button
            onClick={onDismiss}
            className="px-4 py-1.5 rounded text-sm text-text-muted border border-border-panel hover:border-accent-red/50 hover:text-accent-red transition-colors"
          >
            取消
          </button>
        </div>
      </div>

      <PromptDialog
        open={showSavePresetPrompt}
        title="保存为预设"
        placeholder="给当前配置取个名字…"
        confirmLabel="保存"
        onConfirm={handleSavePreset}
        onCancel={() => setShowSavePresetPrompt(false)}
      />
      <ConfirmDialog
        open={showDeletePresetConfirm}
        title="删除预设"
        message={`确定要删除预设 "${selectedPreset}" 吗？`}
        confirmLabel="删除"
        destructive
        onConfirm={handleDeletePreset}
        onCancel={() => setShowDeletePresetConfirm(false)}
      />
    </div>
  )
}
