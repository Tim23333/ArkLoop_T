import { useEffect, useRef, useState } from 'react'
import { Settings } from './Icons'

interface SidebarProps {
  timelines: string[]
  pinnedTimelines: string[]
  selected?: string
  isLoading: boolean
  loadingDone: boolean
  onSelect: (name: string) => void
  onNewTimeline: () => void
  onPin: (name: string) => void
  onUnpin: (name: string) => void
  onRename: (oldName: string, newName: string) => void
  onDelete: (name: string) => void
  onDuplicate: (name: string) => void
  onExport: (name: string) => void
  onImport: () => void
  onOpenSettings?: () => void
}

interface MenuState {
  name: string
  x: number
  y: number
}

function displayName(name: string) {
  return name.replace(/\.json$/, '')
}

export function Sidebar({
  timelines,
  pinnedTimelines,
  selected,
  isLoading,
  loadingDone,
  onSelect,
  onNewTimeline,
  onPin,
  onUnpin,
  onRename,
  onDelete,
  onDuplicate,
  onExport,
  onImport,
  onOpenSettings,
}: SidebarProps) {
  const [menuState, setMenuState] = useState<MenuState | null>(null)
  const [renamingItem, setRenamingItem] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const menuRef = useRef<HTMLDivElement>(null)
  const renameInputRef = useRef<HTMLInputElement>(null)

  // Close dropdown on outside click
  useEffect(() => {
    if (!menuState) return
    const close = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuState(null)
      }
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [menuState])

  // Focus rename input when it appears
  useEffect(() => {
    if (renamingItem) renameInputRef.current?.select()
  }, [renamingItem])

  const openMenu = (e: React.MouseEvent, name: string) => {
    e.stopPropagation()
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
    setMenuState({ name, x: rect.right - 96, y: rect.bottom + 4 })
  }

  const confirmRename = () => {
    if (!renamingItem) return
    const trimmed = renameValue.trim()
    if (trimmed && trimmed !== displayName(renamingItem)) {
      onRename(renamingItem, trimmed)
    }
    setRenamingItem(null)
  }

  const startRename = (name: string) => {
    setMenuState(null)
    setRenamingItem(name)
    setRenameValue(displayName(name))
  }

  const isPinned = (name: string) => pinnedTimelines.includes(name)

  // Timelines not in pinned list
  const unpinnedTimelines = timelines.filter((n) => !pinnedTimelines.includes(n))

  const renderItem = (name: string) => (
    <div
      key={name}
      onClick={() => onSelect(name)}
      className={[
        'relative flex items-center px-2.5 py-[5px] rounded cursor-pointer text-xs group',
        selected === name ? 'bg-[#2A313A]' : 'hover:bg-[#1A1E24]',
      ].join(' ')}
    >
      {selected === name && (
        <div className="absolute left-0 top-1 bottom-1 w-0.5 bg-accent-blue rounded-full" />
      )}

      {/* Name or rename input */}
      {renamingItem === name ? (
        <input
          ref={renameInputRef}
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
          onBlur={confirmRename}
          onKeyDown={(e) => {
            if (e.key === 'Enter') confirmRename()
            if (e.key === 'Escape') setRenamingItem(null)
            e.stopPropagation()
          }}
          onClick={(e) => e.stopPropagation()}
          className="flex-1 min-w-0 bg-[#0B0F13] border border-accent-blue/50 rounded px-1.5 py-0.5 text-text-primary outline-none text-xs"
        />
      ) : (
        <span
          className={[
            'flex-1 min-w-0 truncate',
            selected === name ? 'text-text-primary' : 'text-text-muted',
          ].join(' ')}
        >
          {displayName(name)}
        </span>
      )}

      {/* Three-dot menu button */}
      <button
        onClick={(e) => openMenu(e, name)}
        className="ml-1.5 text-text-dim hover:text-text-muted opacity-0 group-hover:opacity-100 transition-opacity shrink-0 px-0.5"
        onMouseDown={(e) => e.stopPropagation()}
      >
        ⋯
      </button>
    </div>
  )

  return (
    <div className="w-sidebar h-full flex flex-col bg-gradient-to-br from-panel to-[#080C10] border-r border-border-panel overflow-hidden">
      {/* Header row: settings + loading indicator */}
      <div className="px-3 pt-3 pb-2 flex items-center justify-between shrink-0">
        <button
          onClick={onOpenSettings}
          disabled={!onOpenSettings}
          title="设置"
          className="text-text-dim hover:text-text-primary disabled:cursor-default transition-colors"
        >
          <Settings className="w-4 h-4" />
        </button>
        {isLoading && (
          <svg className="w-3.5 h-3.5 text-text-dim animate-spin" viewBox="0 0 24 24" fill="none">
            <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2" strokeOpacity="0.25" />
            <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        )}
        {!isLoading && loadingDone && (
          <svg className="w-3.5 h-3.5 text-accent-green" viewBox="0 0 20 20" fill="none">
            <circle cx="10" cy="10" r="9" stroke="currentColor" strokeWidth="1.5" strokeOpacity="0.5" />
            <path d="M6 10l3 3 5-5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
      </div>

      {/* New timeline + Import buttons */}
      <div className="px-2.5 pb-2 shrink-0 flex gap-1.5">
        <button
          onClick={onNewTimeline}
          disabled={isLoading}
          className="flex-1 h-8 flex items-center gap-2.5 px-2.5 rounded bg-[#15171A] border border-border-panel hover:border-[#2A313A] transition-colors disabled:opacity-40"
        >
          <span className="text-base leading-none text-text-dim">+</span>
          <span className="text-xs text-text-dim">new TimeLine</span>
        </button>
        <button
          onClick={onImport}
          disabled={isLoading}
          title="导入时间轴 JSON"
          className="shrink-0 h-8 px-2.5 rounded bg-[#15171A] border border-border-panel hover:border-[#2A313A] transition-colors disabled:opacity-40 text-xs text-text-dim"
        >
          导入
        </button>
      </div>

      <hr className="border-divider shrink-0" />

      {/* Scrollable list area */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden min-h-0 flex flex-col">
        {/* Pinned section */}
        {pinnedTimelines.length > 0 && (
          <>
            <div className="px-3 pt-3 pb-1 shrink-0">
              <div className="text-[10px] uppercase tracking-widest text-text-dim font-medium">Pinned</div>
            </div>
            <div className="px-2 flex flex-col gap-0.5 shrink-0">
              {pinnedTimelines.map((name) => renderItem(name))}
            </div>
            <hr className="border-divider mx-2 my-2 shrink-0" />
          </>
        )}

        {/* Timelines section */}
        <div className="px-3 pt-2 pb-1 shrink-0">
          <div className="text-[10px] uppercase tracking-widest text-text-dim font-medium">TimeLines</div>
        </div>
        <div className="px-2 pb-2 flex flex-col gap-0.5">
          {unpinnedTimelines.map((name) => renderItem(name))}
        </div>
      </div>

      {/* Floating dropdown menu — position: fixed to escape overflow clipping */}
      {menuState && (
        <div
          ref={menuRef}
          className="fixed z-[9999] w-24 rounded border border-border-panel bg-[#1A1E24] shadow-xl py-0.5 text-xs"
          style={{ top: menuState.y, left: menuState.x }}
        >
          <button
            className="w-full text-left px-3 py-1.5 text-text-muted hover:bg-[#222A31] hover:text-text-primary"
            onClick={() => {
              isPinned(menuState.name)
                ? onUnpin(menuState.name)
                : onPin(menuState.name)
              setMenuState(null)
            }}
          >
            {isPinned(menuState.name) ? 'Unpin' : 'Pin'}
          </button>
          <button
            className="w-full text-left px-3 py-1.5 text-text-muted hover:bg-[#222A31] hover:text-text-primary"
            onClick={() => startRename(menuState.name)}
          >
            Rename
          </button>
          <button
            className="w-full text-left px-3 py-1.5 text-text-muted hover:bg-[#222A31] hover:text-text-primary"
            onClick={() => {
              onDuplicate(menuState.name)
              setMenuState(null)
            }}
          >
            Duplicate
          </button>
          <button
            className="w-full text-left px-3 py-1.5 text-text-muted hover:bg-[#222A31] hover:text-text-primary"
            onClick={() => {
              onExport(menuState.name)
              setMenuState(null)
            }}
          >
            Export
          </button>
          <button
            className="w-full text-left px-3 py-1.5 text-text-muted hover:bg-[#222A31] hover:text-accent-red"
            onClick={() => {
              onDelete(menuState.name)
              setMenuState(null)
            }}
          >
            Delete
          </button>
        </div>
      )}
    </div>
  )
}
