import { useEffect, useState } from 'react'
import type { AxisBlock, AxisAction } from '../types'

interface ChevronBlockProps {
  block: AxisBlock
  pointLength: number
  avatarHeight: number
  isSelected?: boolean
  isDragging?: boolean
  dragX?: number
  getAvatarUrl?: (oper: string) => Promise<string>
  onSingleClick?: (block: AxisBlock, e: React.MouseEvent) => void
  onDoubleClick?: (block: AxisBlock, e: React.MouseEvent) => void
  onContextMenu?: (block: AxisBlock, e: React.MouseEvent) => void
  onDragStart?: (block: AxisBlock, e: React.MouseEvent) => void
}

const ROW_GRADIENT: Record<string, string> = {
  deploy:  'url(#trackGradient-deploy)',
  skill:   'url(#trackGradient-skill)',
  retreat: 'url(#trackGradient-retreat)',
}
const ROW_STROKE: Record<string, string> = {
  deploy:  '#3A7AB8',
  skill:   '#A07828',
  retreat: '#A03838',
}
const DIR_ARROW: Record<string, string> = {
  '上': '↑', '下': '↓', '左': '←', '右': '→',
}

function Avatar({
  action,
  showPos,
  avatarHeight,
  getAvatarUrl,
}: {
  action: AxisAction
  showPos?: boolean
  avatarHeight: number
  getAvatarUrl?: (oper: string) => Promise<string>
}) {
  const size = avatarHeight
  const [avatarUrl, setAvatarUrl] = useState<string | undefined>(undefined)

  useEffect(() => {
    let mounted = true
    if (getAvatarUrl && action.oper) {
      getAvatarUrl(action.oper).then((url) => {
        if (mounted) setAvatarUrl(url || undefined)
      })
    }
    return () => { mounted = false }
  }, [action.oper, getAvatarUrl])

  const dirChar = action.direction ? (DIR_ARROW[action.direction] ?? action.direction) : null

  return (
    <div className="flex items-center gap-[3px] shrink-0">
      {avatarUrl ? (
        <img
          src={avatarUrl}
          alt={action.oper}
          width={size}
          height={size}
          className="rounded-sm object-cover bg-white shrink-0"
          onError={(e) => { e.currentTarget.style.display = 'none' }}
        />
      ) : (
        <div className="rounded-sm bg-white/30 shrink-0" style={{ width: size, height: size }} />
      )}
      {showPos && (action.pos || dirChar) && (
        <span className="text-[8px] text-white/90 font-mono leading-none whitespace-nowrap">
          {action.pos}{dirChar ? dirChar : ''}
        </span>
      )}
    </div>
  )
}

export function ChevronBlock({
  block,
  pointLength,
  avatarHeight,
  isSelected = false,
  isDragging = false,
  dragX,
  getAvatarUrl,
  onSingleClick,
  onDoubleClick,
  onContextMenu,
  onDragStart,
}: ChevronBlockProps) {
  if (block.x == null || block.width == null) return null

  // When dragging, render at dragX instead of block.x
  const tipX = isDragging && dragX != null ? dragX : block.x
  const bodyX = tipX + pointLength
  const bodyWidth = block.width
  const height = avatarHeight + 8
  const halfH = height / 2
  const topY = 0
  const bottomY = height

  const points = [
    `${tipX},${halfH}`,
    `${bodyX},${topY}`,
    `${bodyX + bodyWidth},${topY}`,
    `${bodyX + bodyWidth},${bottomY}`,
    `${bodyX},${bottomY}`,
  ].join(' ')

  const fill = ROW_GRADIENT[block.row] ?? 'url(#trackGradient-deploy)'
  const stroke = isSelected
    ? '#FFFFFF'
    : (ROW_STROKE[block.row] ?? '#3A7AB8')
  const showPos = block.row === 'deploy'

  const handleMouseDown = (e: React.MouseEvent) => {
    e.stopPropagation()
    onDragStart?.(block, e)
  }
  const handleClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    onSingleClick?.(block, e)
  }
  const handleDblClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    onDoubleClick?.(block, e)
  }
  const handleContextMenu = (e: React.MouseEvent) => {
    e.stopPropagation()
    e.preventDefault()
    onContextMenu?.(block, e)
  }

  return (
    <g
      style={{ cursor: isDragging ? 'grabbing' : 'grab', opacity: isDragging ? 0.7 : 1 }}
      onMouseDown={handleMouseDown}
      onClick={handleClick}
      onDoubleClick={handleDblClick}
      onContextMenu={handleContextMenu}
    >
      {/* Shadow */}
      <polygon points={points} fill="rgba(0,0,0,0.35)" transform="translate(0,1)" />
      {/* Body */}
      <polygon
        points={points}
        fill={fill}
        stroke={stroke}
        strokeWidth={isSelected ? 1.2 : 0.8}
      />
      {/* Content */}
      <foreignObject x={bodyX + 3} y={2} width={bodyWidth - 6} height={height - 4}>
        <div className="flex items-center h-full gap-1 overflow-hidden">
          {block.actions.map((action, idx) => (
            <Avatar
              key={idx}
              action={action}
              showPos={showPos}
              avatarHeight={avatarHeight}
              getAvatarUrl={getAvatarUrl}
            />
          ))}
        </div>
      </foreignObject>
    </g>
  )
}

export function blockKey(b: { row: string; frame: number }) {
  return `${b.row}:${b.frame}`
}
