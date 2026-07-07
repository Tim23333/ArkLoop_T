import { useEffect, useLayoutEffect, useRef, useState, useCallback } from 'react'
import { TransportControls } from './TransportControls'
import { Ruler } from './Ruler'
import { Track } from './Track'
import { blockKey } from './ChevronBlock'
import { useTimelineLayout, DEFAULT_LAYOUT } from '../hooks/useTimelineLayout'
import type { AxisAction, AxisBlock, ActionRow } from '../types'
import type { TickPosition } from '../hooks/useTimelineLayout'

interface TimelineProps {
  actions?: AxisAction[]
  recording?: boolean
  playing?: boolean
  currentFrame?: number
  breakpoints?: number[]
  getAvatarUrl?: (oper: string) => Promise<string>
  isLoading?: boolean
  onRecord?: () => void
  onStop?: () => void
  onPlay?: () => void
  onStopPlay?: () => void
  onPause?: () => void
  onAddAction?: (row: ActionRow, frame: number) => void
  onEditAction?: (block: AxisBlock) => void
  onMoveAction?: (block: AxisBlock, newFrame: number) => void
  onDeleteAction?: (block: AxisBlock) => void
  onAddBreakpoint?: (frame: number) => void
  onRemoveBreakpoint?: (frame: number) => void
}

type ContextMenuState =
  | { kind: 'block'; x: number; y: number; block: AxisBlock }
  | { kind: 'empty'; x: number; y: number; frame: number }
  | { kind: 'breakpoint'; x: number; y: number; frame: number }

const TOP_BAR_HEIGHT = 43
const ROW_COUNT = 3
const PLAYHEAD_VIEWPORT_X = 160
const CHUNK_FRAMES = 300  // pre-render 300 frames at a time

export function Timeline({
  actions = [],
  recording = false,
  playing = false,
  currentFrame = 0,
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

  const [selectedBlockKey, setSelectedBlockKey] = useState<string | null>(null)
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null)
  const contextMenuRef = useRef<HTMLDivElement>(null)

  const [draggingBlock, setDraggingBlock] = useState<AxisBlock | null>(null)
  const [dragX, setDragX] = useState(0)

  const [scrollChunks, setScrollChunks] = useState(1)

  // Pre-render in chunks of CHUNK_FRAMES so the layout memo stays stable.
  const liveChunkFrame = liveMode
    ? (Math.floor(currentFrame / CHUNK_FRAMES) + 2) * CHUNK_FRAMES
    : 0
  const extendToFrame = Math.max(scrollChunks * CHUNK_FRAMES, liveChunkFrame)

  const { ticks, blocks, totalWidth } = useTimelineLayout(actions, {}, extendToFrame)

  const playheadTick = ticks.find((t) => t.frame === currentFrame)
  const playheadContentX = playheadTick?.x ?? 0

  useEffect(() => {
    if (!liveMode || !scrollRef.current) return
    scrollRef.current.scrollLeft = Math.max(0, playheadContentX - PLAYHEAD_VIEWPORT_X)
  }, [currentFrame, liveMode, playheadContentX])

  const handleScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    if (el.scrollLeft + el.clientWidth >= el.scrollWidth - 300) {
      setScrollChunks((c) => c + 1)
    }
  }, [])

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
      if (nearest) onMoveAction?.(draggingBlock, nearest.frame)
      setDraggingBlock(null)
    }
    setIsPanning(false)
  }, [draggingBlock, ticks, onMoveAction])

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
  const handleTrackDoubleClick = useCallback((row: ActionRow, frame: number) => {
    onAddAction?.(row, frame)
  }, [onAddAction])
  const handleSvgClick = useCallback(() => {
    setSelectedBlockKey(null)
    setContextMenu(null)
  }, [])

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
    const existing = breakpoints.find((f) => f === nearest.frame)
    setContextMenu(
      existing !== undefined
        ? { kind: 'breakpoint', x: e.clientX, y: e.clientY, frame: existing }
        : { kind: 'empty', x: e.clientX, y: e.clientY, frame: nearest.frame },
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
          <div className="h-[43px] bg-timeline-top border-b border-grid-light relative">
            <Ruler ticks={ticks} leftMargin={0} />
          </div>

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

          {breakpoints.map((bp) => {
            const t = ticks.find((tp) => tp.frame === bp)
            if (!t) return null
            return (
              <div
                key={`bp-${bp}`}
                className="absolute z-[5] cursor-pointer"
                style={{ top: TOP_BAR_HEIGHT, bottom: 0, left: t.x - 6, width: 12 }}
                onMouseDown={(e) => {
                  if (e.button !== 0) return
                  if (liveMode) return
                  e.stopPropagation()
                  setContextMenu({ kind: 'breakpoint', x: e.clientX, y: e.clientY, frame: bp })
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

      {contextMenu && (
        <div
          ref={contextMenuRef}
          className="fixed z-[9999] min-w-[80px] rounded border border-border-panel bg-[#1A1E24] shadow-xl py-0.5 text-xs"
          style={(() => {
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
                onAddBreakpoint?.(contextMenu.frame)
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
                onRemoveBreakpoint?.(contextMenu.frame)
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
