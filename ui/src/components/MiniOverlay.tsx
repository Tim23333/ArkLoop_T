import { useEffect, useRef, useState } from 'react'
import { Timeline } from './Timeline'
import { formatGameTime } from '../utils/timeline'
import type { ActionRow, AxisAction, AxisBlock } from '../types'

interface MiniOverlayProps {
  timelineName: string
  frameCount: number
  gameTimeSec: number
  wsConnected: boolean
  frameOffset: number
  isRecording: boolean
  isPlaying: boolean
  isLoading: boolean
  locked: boolean
  lockError?: string
  actions: AxisAction[]
  breakpoints: number[]
  getAvatarUrl: (oper: string) => Promise<string>
  onRecord: () => void
  onStop: () => void
  onPlay: () => void
  onStopPlay: () => void
  onPause: () => void
  onToggleLock: () => void
  onRestore: () => void
  onAddAction: (row: ActionRow, frame: number) => void
  onEditAction: (block: AxisBlock) => void
  onMoveAction: (block: AxisBlock, newFrame: number) => void
  onDeleteAction: (block: AxisBlock) => void
  onAddBreakpoint: (frame: number) => void
  onRemoveBreakpoint: (frame: number) => void
  getWindowBounds: () => Promise<{ x: number; y: number; width: number; height: number }>
  setBounds: (x: number, y: number, width: number, height: number) => Promise<void> | void
}

export function MiniOverlay({
  timelineName,
  frameCount,
  gameTimeSec,
  wsConnected,
  frameOffset,
  isRecording,
  isPlaying,
  isLoading,
  locked,
  lockError,
  actions,
  breakpoints,
  getAvatarUrl,
  onRecord,
  onStop,
  onPlay,
  onStopPlay,
  onPause,
  onToggleLock,
  onRestore,
  onAddAction,
  onEditAction,
  onMoveAction,
  onDeleteAction,
  onAddBreakpoint,
  onRemoveBreakpoint,
  getWindowBounds,
  setBounds,
}: MiniOverlayProps) {
  const [dragging, setDragging] = useState(false)
  const dragStart = useRef({ mouseX: 0, mouseY: 0, x: 0, y: 0, width: 0, height: 0 })

  const beginDrag = async (event: React.MouseEvent) => {
    if (locked || event.button !== 0) return
    const target = event.target as HTMLElement
    if (target.closest('button, input')) return
    event.preventDefault()
    const bounds = await getWindowBounds()
    dragStart.current = {
      mouseX: (window.screenX ?? 0) + event.clientX,
      mouseY: (window.screenY ?? 0) + event.clientY,
      ...bounds,
    }
    setDragging(true)
  }

  useEffect(() => {
    if (!dragging) return
    const move = (event: MouseEvent) => {
      const start = dragStart.current
      const mouseX = (window.screenX ?? 0) + event.clientX
      const mouseY = (window.screenY ?? 0) + event.clientY
      void setBounds(
        Math.round(start.x + mouseX - start.mouseX),
        Math.round(start.y + mouseY - start.mouseY),
        start.width,
        start.height,
      )
    }
    const end = () => setDragging(false)
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', end)
    return () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', end)
    }
  }, [dragging, setBounds])

  const status = isRecording ? '录轴中' : isPlaying ? '播放中' : '已就绪'
  const statusClass = isRecording
    ? 'text-[#ff6b67]'
    : isPlaying
      ? 'text-[#5ee271]'
      : 'text-white/60'

  return (
    <div className="mini-overlay-shell">
      <div className="mini-overlay-header" onMouseDown={beginDrag}>
        <div className="flex min-w-0 items-center gap-2">
          <span className={`h-1.5 w-1.5 rounded-full ${isRecording ? 'bg-accent-red animate-pulse' : isPlaying ? 'bg-accent-green animate-pulse' : 'bg-white/40'}`} />
          <span className={`text-[11px] font-semibold tracking-[0.12em] ${statusClass}`}>{status}</span>
          <span className="max-w-[230px] truncate text-[11px] text-white/45" title={timelineName || '未选择时间轴'}>
            {timelineName || '未选择时间轴'}
          </span>
        </div>
        {!locked && (
          <div className="flex items-center gap-1.5">
            <button className="mini-overlay-button" onClick={onToggleLock} title="锁定并启用鼠标穿透">
              锁定
            </button>
            <button className="mini-overlay-button mini-overlay-button-primary" onClick={onRestore} title="切换回完整页面">
              返回原版
            </button>
          </div>
        )}
        {locked && <span className="text-[10px] tracking-wide text-accent-blue/90">已锁定 · Ctrl+Alt+L 解锁</span>}
      </div>

      <div className="flex h-[76px] shrink-0 items-center px-4 py-2">
        <div className="flex min-w-[340px] items-end gap-6">
          <div>
            <div className="text-[10px] tracking-[0.15em] text-white/40">游戏时间</div>
            <div className="font-mono text-[28px] font-semibold leading-none tracking-tight text-white/95">
              {wsConnected ? formatGameTime(gameTimeSec) : '--:--.--'}
            </div>
          </div>
          <div>
            <div className="text-[10px] tracking-[0.15em] text-white/40">帧数</div>
            <div className="font-mono text-xl font-semibold leading-none text-accent-blue">
              {wsConnected ? frameCount : '--'}
            </div>
          </div>
          <div>
            <div className="text-[10px] tracking-[0.15em] text-white/40">续录偏移</div>
            <div className="font-mono text-base leading-none text-white/75">{frameOffset}</div>
          </div>
        </div>

      </div>

      <div className="flex min-h-0 flex-1 border-t border-white/10">
        <Timeline
          overlay
          actions={actions}
          recording={isRecording}
          playing={isPlaying}
          currentFrame={frameCount}
          breakpoints={breakpoints}
          getAvatarUrl={getAvatarUrl}
          isLoading={isLoading}
          onRecord={onRecord}
          onStop={onStop}
          onPlay={onPlay}
          onStopPlay={onStopPlay}
          onPause={onPause}
          onAddAction={onAddAction}
          onEditAction={onEditAction}
          onMoveAction={onMoveAction}
          onDeleteAction={onDeleteAction}
          onAddBreakpoint={onAddBreakpoint}
          onRemoveBreakpoint={onRemoveBreakpoint}
        />
      </div>

      {lockError && !locked && (
        <div className="absolute bottom-1 left-4 text-[10px] text-[#ff8a86]">{lockError}</div>
      )}
    </div>
  )
}
