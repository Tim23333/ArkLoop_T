import type { AxisBlock, ActionRow } from '../types'
import type { TickPosition } from '../hooks/useTimelineLayout'
import { ChevronBlock, blockKey } from './ChevronBlock'

interface TrackProps {
  row: ActionRow
  y: number
  height: number
  blocks: AxisBlock[]
  ticks: TickPosition[]
  pointLength: number
  avatarHeight: number
  selectedBlockKey?: string | null
  draggingBlockKey?: string | null
  dragX?: number
  getAvatarUrl?: (oper: string) => Promise<string>
  onBlockClick?: (block: AxisBlock, e: React.MouseEvent) => void
  onBlockContextMenu?: (block: AxisBlock, e: React.MouseEvent) => void
  onBlockDragStart?: (block: AxisBlock, e: React.MouseEvent) => void
  onTrackDoubleClick?: (row: ActionRow, frame: number) => void
}

/** Find the tick nearest to x in scroll-container space. */
function nearestTick(ticks: TickPosition[], x: number): TickPosition | null {
  if (!ticks.length) return null
  return ticks.reduce((best, t) =>
    Math.abs(t.x - x) < Math.abs(best.x - x) ? t : best
  )
}

export function Track({
  row, y, height, blocks, ticks, pointLength, avatarHeight,
  selectedBlockKey, draggingBlockKey, dragX,
  getAvatarUrl,
  onBlockClick, onBlockContextMenu, onBlockDragStart,
  onTrackDoubleClick,
}: TrackProps) {
  const rowBlocks = blocks.filter((b) => b.row === row)
  const totalWidth = ticks.length
    ? ticks[ticks.length - 1].x + 40
    : 800

  const handleBackgroundDblClick = (e: React.MouseEvent<SVGRectElement>) => {
    if (!onTrackDoubleClick) return
    const rect = (e.currentTarget as SVGRectElement).ownerSVGElement!.getBoundingClientRect()
    const x = e.clientX - rect.left
    const t = nearestTick(ticks, x)
    if (t) onTrackDoubleClick(row, t.frame)
  }

  return (
    <g transform={`translate(0, ${y})`}>
      {/* Track background (capture double-click on empty space) */}
      <rect
        x={0}
        y={0}
        width={totalWidth}
        height={height}
        fill="transparent"
        onDoubleClick={handleBackgroundDblClick}
        style={{ cursor: 'crosshair' }}
      />

      {/* Separator line */}
      <line x1={0} y1={0} x2={totalWidth} y2={0} stroke="#222A31" strokeWidth={1} />

      {/* Tick dashed vertical lines */}
      {ticks.map((t, idx) => (
        <line
          key={idx}
          x1={t.x}
          y1={0}
          x2={t.x}
          y2={height}
          stroke="#222A31"
          strokeWidth={1}
          strokeDasharray="3 3"
          opacity={0.7}
        />
      ))}

      {/* Blocks */}
      <g transform={`translate(0, ${(height - avatarHeight - 8) / 2})`}>
        {rowBlocks.map((block, idx) => {
          const key = blockKey(block)
          return (
            <ChevronBlock
              key={idx}
              block={block}
              pointLength={pointLength}
              avatarHeight={avatarHeight}
              isSelected={selectedBlockKey === key}
              isDragging={draggingBlockKey === key}
              dragX={draggingBlockKey === key ? dragX : undefined}
              getAvatarUrl={getAvatarUrl}
              onSingleClick={onBlockClick}
              onContextMenu={onBlockContextMenu}
              onDragStart={onBlockDragStart}
            />
          )
        })}
      </g>
    </g>
  )
}
