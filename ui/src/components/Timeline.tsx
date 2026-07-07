import { useEffect, useLayoutEffect, useRef, useState, useCallback } from 'react'
import { TransportControls } from './TransportControls'
import { Ruler } from './Ruler'
import { Track } from './Track'
import { blockKey } from './ChevronBlock'
import { useTimelineLayout, DEFAULT_LAYOUT } from '../hooks/useTimelineLayout'
import type { AxisAction, AxisBlock, ActionRow } from '../types'
import type { TickPosition } from '../hooks/useTimelineLayout'
import type { Breakpoint } from '../hooks/useBackend'

interface TimelineProps {
  actions?: AxisAction[]
  recording?: boolean
  playing?: boolean
  /** Current absolute frame count from the WS time source. */
  currentFrame?: number
  currentCycle?: number
  currentTick?: number
  maxTick?: number
  breakpoints?: Breakpoint[]
  getAvatarUrl?: (oper: string) => Promise<string>
  isLoading?: boolean
  onRecord?: () => void
  onStop?: () => void
  onPlay?: () => void
  onStopPlay?: () => void
  onPause?: () => void
  onAddAction?: (row: ActionRow, cycle: number, tick: number) => void
  onEditAction?: (block: AxisBlock) => void
  onMoveAction?: (block: AxisBlock, newCycle: number, newTick: number) => void
  onDeleteAction?: (block: AxisBlock) => void
  onAddBreakpoint?: (cycle: number, tick: number) => void
  onRemoveBreakpoint?: (cycle: number, tick: number) => void
}

type ContextMenuState =
  | { kind: 'block'; x: number; y: number; block: AxisBlock }
  | { kind: 'empty'; x: number; y: number; cycle: number; tick: number }
  | { kind: 'breakpoint'; x: number; y: number; cycle: number; tick: number }

const TOP_BAR_HEIGHT = 43
const ROW_COUNT = 3
// Fixed viewport position of the playhead within the scroll area (pixels from its left edge)
const PLAYHEAD_VIEWPORT_X = 160
// We pre-render the timeline in chunks of this many cycles so the layout memo
// stays stable while the playhead moves — it only recomputes once per chunk,
// never per tick. Also the minimum amount of empty timeline you can always
// scroll/edit into, even on a brand-new empty timeline.
const CHUNK_CYCLES = 10

export function Timeline({
  actions = [],
  recording = false,
  playing = false,
  currentFrame = 0,
  currentCycle: _currentCycle = 0,
  currentTick: _currentTick = 0,
  maxTick = DEFAULT_LAYOUT.maxTick,
  breakpoints = [],
  getAvatarUrl,
  isLoading = false,
  onRecord,
  onStop,
  onPlay,
  onStopPlay,
  onPause,
  onAddAction,
  onEditAction,
  onMoveAction,
  onDeleteAction,
  onAddBreakpoint,
  onRemoveBreakpoint,
}: TimelineProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [containerHeight, setContainerHeight] = useState(0)
  const [isPanning, setIsPanning] = useState(false)
  const panStart = useRef({ x: 0, scrollLeft: 0 })
  const liveMode = recording || playing

  // Editing state
  const [selectedBlockKey, setSelectedBlockKey] = useState<string | null>(null)
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null)
  const contextMenuRef = useRef<HTMLDivElement>(null)

  // Drag state
  const [draggingBlock, setDraggingBlock] = useState<AxisBlock | null>(null)
  const [dragX, setDragX] = useState(0)

  // How far the user has scrolled the timeline open, in extra chunks. Grows when
  // the user scrolls near the right edge → effectively infinite for editing.
  const [scrollChunks, setScrollChunks] = useState(1)

  // Derive cycle from frame_count for chunk-based pre-rendering.
  const derivedCycle = Math.floor(currentFrame / (maxTick || 1))
  // Render up to this cycle. Quantized to CHUNK_CYCLES so the layout memo only
  // recomputes once per chunk (pre-render), not on every tick of the playhead.
  const liveChunkCycle = liveMode
    ? (Math.floor(derivedCycle / CHUNK_CYCLES) + 2) * CHUNK_CYCLES
    : 0
  const extendToCycle = Math.max(scrollChunks * CHUNK_CYCLES, liveChunkCycle)

  // Layout — pre-rendered well ahead of both the live playhead and the scroll edge
  const { ticks, blocks, totalWidth } = useTimelineLayout(
    actions,
    { maxTick },
    extendToCycle,
  )

  // Current tick position in content space. The WS time source pushes
  // frame_count at ~125 Hz; the playhead tracks the absolute frame.
  const playheadTick = ticks.find((t) => t.frame === currentFrame)
  const playheadContentX = playheadTick?.x ?? 0

  // ── Keep the playhead at PLAYHEAD_VIEWPORT_X: scroll the content under it ──
  useEffect(() => {
    if (!liveMode || !scrollRef.current) return
    scrollRef.current.scrollLeft = Math.max(0, playheadContentX - PLAYHEAD_VIEWPORT_X)
  }, [currentFrame, liveMode, playheadContentX])

  // ── Grow the timeline when the user scrolls near the right edge ─────
  const handleScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    if (el.scrollLeft + el.clientWidth >= el.scrollWidth - 300) {
      setScrollChunks((c) => c + 1)
    }
  }, [])

  // Measure track height — useLayoutEffect runs before paint, avoiding one frame
  // where containerHeight=0 initial would render an empty SVG, and more importantly
  // preventing the initial containerHeight=180 from clipping the retreat row under
  // overflow-y:hidden before the scrollbar-adjusted clientHeight is known.
  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const measure = () => setContainerHeight(Math.max(1, el.clientHeight - TOP_BAR_HEIGHT))
    measure()
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null
    ro?.observe(el)
    window.addEventListener('resize', measure)
    return () => { ro?.disconnect(); window.removeEventListener('resize', measure) }
  }, [])

  const rowHeight = containerHeight / ROW_COUNT

  // Close context menu on outside click
  useEffect(() => {
    if (!contextMenu) return
    const close = (e: MouseEvent) => {
      if (contextMenuRef.current && !contextMenuRef.current.contains(e.target as Node)) {
        setContextMenu(null)
      }
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [contextMenu])

  // Del / Backspace deletes selected block
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedBlockKey && !contextMenu) {
        const block = blocks.find((b) => blockKey(b) === selectedBlockKey)
        if (block) onDeleteAction?.(block)
        setSelectedBlockKey(null)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [selectedBlockKey, blocks, contextMenu, onDeleteAction])

  // ── Panning ─────────────────────────────────────────────────────────
  const startPan = (e: React.MouseEvent) => {
    if (draggingBlock || liveMode) return
    setIsPanning(true)
    panStart.current = { x: e.clientX, scrollLeft: scrollRef.current?.scrollLeft ?? 0 }
  }
  const onMouseMove = useCallback((e: React.MouseEvent) => {
    if (draggingBlock && scrollRef.current) {
      const rect = scrollRef.current.getBoundingClientRect()
      setDragX(e.clientX - rect.left + scrollRef.current.scrollLeft)
      return
    }
    if (!isPanning || !scrollRef.current) return
    scrollRef.current.scrollLeft = panStart.current.scrollLeft - (e.clientX - panStart.current.x)
  }, [draggingBlock, isPanning])

  const onMouseUp = useCallback((e: React.MouseEvent) => {
    if (draggingBlock && scrollRef.current) {
      const rect = scrollRef.current.getBoundingClientRect()
      const x = e.clientX - rect.left + scrollRef.current.scrollLeft
      const nearest = ticks.reduce<TickPosition | null>(
        (best, t) => best === null || Math.abs(t.x - x) < Math.abs(best.x - x) ? t : best,
        null,
      )
      if (nearest) onMoveAction?.(draggingBlock, nearest.cycle, nearest.tick)
      setDraggingBlock(null)
    }
    setIsPanning(false)
  }, [draggingBlock, ticks, onMoveAction])

  // ── Block interaction handlers ───────────────────────────────────────
  const handleBlockClick = useCallback((block: AxisBlock) => {
    setSelectedBlockKey(blockKey(block))
    setContextMenu(null)
  }, [])
  const handleBlockContextMenu = useCallback((block: AxisBlock, e: React.MouseEvent) => {
    setSelectedBlockKey(blockKey(block))
    setContextMenu({ kind: 'block', x: e.clientX, y: e.clientY, block })
  }, [])
  const handleBlockDragStart = useCallback((block: AxisBlock, e: React.MouseEvent) => {
    e.stopPropagation()
    setSelectedBlockKey(blockKey(block))
    setContextMenu(null)
    setDraggingBlock(block)
    if (scrollRef.current) {
      const rect = scrollRef.current.getBoundingClientRect()
      setDragX(e.clientX - rect.left + scrollRef.current.scrollLeft)
    }
  }, [])
  const handleTrackDoubleClick = useCallback((row: ActionRow, cycle: number, tick: number) => {
    onAddAction?.(row, cycle, tick)
  }, [onAddAction])
  const handleSvgClick = useCallback(() => {
    setSelectedBlockKey(null)
    setContextMenu(null)
  }, [])

  // Right-click on the empty timeline area: offer "add breakpoint" at the
  // nearest tick. Note: this fires only when the click does NOT land on a
  // block (block onContextMenu calls stopPropagation up the chain).
  const handleEmptyContextMenu = useCallback((e: React.MouseEvent) => {
    if (liveMode) return
    if (!scrollRef.current) return
    e.preventDefault()
    const rect = scrollRef.current.getBoundingClientRect()
    const x = e.clientX - rect.left + scrollRef.current.scrollLeft
    const nearest = ticks.reduce<TickPosition | null>(
      (best, t) => best === null || Math.abs(t.x - x) < Math.abs(best.x - x) ? t : best,
      null,
    )
    if (!nearest) return
    // Check if a breakpoint already exists there → show "remove" instead.
    const existing = breakpoints.find((b) => b.cycle === nearest.cycle && b.tick === nearest.tick)
    setContextMenu(
      existing
        ? { kind: 'breakpoint', x: e.clientX, y: e.clientY, cycle: existing.cycle, tick: existing.tick }
        : { kind: 'empty', x: e.clientX, y: e.clientY, cycle: nearest.cycle, tick: nearest.tick },
    )
  }, [liveMode, ticks, breakpoints])

  const trackProps = {
    ticks,
    pointLength: DEFAULT_LAYOUT.pointLength,
    avatarHeight: DEFAULT_LAYOUT.avatarHeight,
    selectedBlockKey,
    draggingBlockKey: draggingBlock ? blockKey(draggingBlock) : null,
    dragX,
    getAvatarUrl,
    onBlockClick: handleBlockClick,
    onBlockContextMenu: handleBlockContextMenu,
    onBlockDragStart: handleBlockDragStart,
    onTrackDoubleClick: handleTrackDoubleClick,
  }

  const innerWidth = Math.max(totalWidth, scrollRef.current?.clientWidth ?? 0)

  return (
    <div
      className="relative flex-1 min-h-[180px] bg-gradient-to-br from-timeline-bg to-timeline-bg-end flex select-none outline-none"
      tabIndex={-1}
    >
      {/* ── Fixed left column ── */}
      <div className="w-timeline-left shrink-0 flex flex-col z-20 bg-gradient-to-br from-timeline-bg to-timeline-bg-end border-r border-grid-light">
        <div className="h-[43px] shrink-0 flex items-center px-5 bg-timeline-top border-b border-grid-light">
          <TransportControls
            isRecording={recording}
            isPlaying={playing}
            isLoading={isLoading}
            onRecord={onRecord}
            onStop={onStop}
            onPlay={onPlay}
            onStopPlay={onStopPlay}
            onPause={onPause}
          />
        </div>
        <div className="flex-1 flex flex-col min-h-0">
          <div className="flex-1 flex items-center px-4 border-b border-grid-light">
            <span className="text-[15px] font-medium text-text-muted font-ui">部署</span>
          </div>
          <div className="flex-1 flex items-center px-4 border-b border-grid-light">
            <span className="text-[15px] font-medium text-text-muted font-ui">技能</span>
          </div>
          <div className="flex-1 flex items-center px-4">
            <span className="text-[15px] font-medium text-text-muted font-ui">撤退</span>
          </div>
        </div>
      </div>

      {/* ── Right scrollable area ── */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-x-auto overflow-y-hidden relative"
        style={{ cursor: draggingBlock ? 'grabbing' : (isPanning ? 'grabbing' : (liveMode ? 'default' : 'grab')) }}
        onScroll={handleScroll}
        onMouseDown={startPan}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
        onContextMenu={handleEmptyContextMenu}
      >
        <div className="relative min-h-full" style={{ minWidth: innerWidth }}>
          {/* Ruler */}
          <div className="h-[43px] bg-timeline-top border-b border-grid-light relative">
            <Ruler ticks={ticks} maxTick={maxTick} leftMargin={0} />
          </div>

          {/* Tracks */}
          <svg
            width={innerWidth}
            height={containerHeight}
            className="block"
            onClick={handleSvgClick}
          >
            <defs>
              <linearGradient id="trackGradient-deploy" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stopColor="#1E4A7A" />
                <stop offset="100%" stopColor="#0D2840" />
              </linearGradient>
              <linearGradient id="trackGradient-skill" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stopColor="#5A4010" />
                <stop offset="100%" stopColor="#2E2008" />
              </linearGradient>
              <linearGradient id="trackGradient-retreat" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stopColor="#5A1818" />
                <stop offset="100%" stopColor="#2E0C0C" />
              </linearGradient>
            </defs>
            <Track row="deploy"  {...trackProps} y={0}             height={rowHeight}                       blocks={blocks} />
            <Track row="skill"   {...trackProps} y={rowHeight}     height={rowHeight}                       blocks={blocks} />
            <Track row="retreat" {...trackProps} y={rowHeight * 2} height={containerHeight - rowHeight * 2}  blocks={blocks} />
          </svg>

          <div className="absolute top-[43px] left-0 right-0 h-px bg-highlight opacity-70 pointer-events-none" />

          {/* ── Breakpoint markers: dashed yellow line at each breakpoint's
              tick position. Sits inside the tracks area (below the ruler) so
              it never overlaps the cost-bar. Left-click opens a delete menu.
              The wider invisible hit-strip makes the thin line easy to click. */}
          {breakpoints.map((bp) => {
            const t = ticks.find((tp) => tp.cycle === bp.cycle && tp.tick === bp.tick)
            if (!t) return null
            return (
              <div
                key={`bp-${bp.cycle}-${bp.tick}`}
                className="absolute z-[5] cursor-pointer"
                style={{ top: TOP_BAR_HEIGHT, bottom: 0, left: t.x - 6, width: 12 }}
                onMouseDown={(e) => {
                  if (e.button !== 0) return
                  if (liveMode) return
                  e.stopPropagation()
                  setContextMenu({
                    kind: 'breakpoint',
                    x: e.clientX,
                    y: e.clientY,
                    cycle: bp.cycle,
                    tick: bp.tick,
                  })
                }}
              >
                <svg width="12" height="100%" className="pointer-events-none">
                  <circle cx="6" cy="6" r="3" fill="#E8B83C" />
                  <line
                    x1="6" y1="0"
                    x2="6" y2="100%"
                    stroke="#E8B83C"
                    strokeWidth={1}
                    strokeDasharray="3 3"
                    opacity={0.85}
                  />
                </svg>
              </div>
            )
          })}

          {/* ── Playhead: in content space at the current (cycle,tick). The
              auto-scroll effect keeps it pinned at a fixed viewport position
              during recording/playback; idle it sits at the real position.
              Aligned to the deploy row top so it doesn't reach into the
              cost-bar ruler. */}
          <div
            className="absolute pointer-events-none z-10"
            style={{ top: TOP_BAR_HEIGHT, bottom: 0, left: playheadContentX - 4, width: 9 }}
          >
            <svg width="9" height="100%">
              <polygon
                points="4,0 8,8 0,8"
                fill={recording ? '#FF3B36' : '#4AA3D8'}
              />
              <line
                x1="4" y1="0"
                x2="4" y2="100%"
                stroke={recording ? '#FF3B36' : '#6FC6FF'}
                strokeWidth={1.1}
                opacity={0.72}
              />
            </svg>
          </div>
        </div>
      </div>

      {/* Context menu */}
      {contextMenu && (
        <div
          ref={contextMenuRef}
          className="fixed z-[9999] min-w-[80px] rounded border border-border-panel bg-[#1A1E24] shadow-xl py-0.5 text-xs"
          style={(() => {
            // Flip upward if the menu would overflow the viewport bottom.
            // 'block' has 2 items (~56px); others have 1 (~28px). Use 70px as
            // a safe upper bound so the menu never clips off-screen.
            const estimatedH = contextMenu.kind === 'block' ? 70 : 40
            const flipY = contextMenu.y + estimatedH > window.innerHeight
            return {
              left: contextMenu.x,
              ...(flipY
                ? { bottom: window.innerHeight - contextMenu.y }
                : { top: contextMenu.y }),
            }
          })()}
        >
          {contextMenu.kind === 'block' && (
            <>
              <button
                className="w-full text-left px-3 py-1.5 text-text-muted hover:bg-[#222A31] hover:text-accent-blue"
                onClick={() => {
                  onEditAction?.(contextMenu.block)
                  setContextMenu(null)
                }}
              >
                编辑
              </button>
              <button
                className="w-full text-left px-3 py-1.5 text-text-muted hover:bg-[#222A31] hover:text-accent-red"
                onClick={() => {
                  onDeleteAction?.(contextMenu.block)
                  setContextMenu(null)
                  setSelectedBlockKey(null)
                }}
              >
                删除
              </button>
            </>
          )}
          {contextMenu.kind === 'empty' && (
            <button
              className="w-full text-left px-3 py-1.5 text-text-muted hover:bg-[#222A31] hover:text-accent-yellow whitespace-nowrap"
              onClick={() => {
                onAddBreakpoint?.(contextMenu.cycle, contextMenu.tick)
                setContextMenu(null)
              }}
            >
              添加断点
            </button>
          )}
          {contextMenu.kind === 'breakpoint' && (
            <button
              className="w-full text-left px-3 py-1.5 text-text-muted hover:bg-[#222A31] hover:text-accent-red whitespace-nowrap"
              onClick={() => {
                onRemoveBreakpoint?.(contextMenu.cycle, contextMenu.tick)
                setContextMenu(null)
              }}
            >
              删除断点
            </button>
          )}
        </div>
      )}
    </div>
  )
}
