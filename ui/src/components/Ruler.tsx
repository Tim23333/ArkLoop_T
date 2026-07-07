import type { TickPosition } from '../hooks/useTimelineLayout'

interface RulerProps {
  ticks: TickPosition[]
  maxTick: number
  /** Pixels from the scroll container's left edge to where this SVG starts.
   *  Tick x-values are in scroll-container coordinates; subtracting this gives
   *  the position within the ruler's own SVG coordinate space. */
  leftMargin: number
}

export function Ruler({ ticks, maxTick, leftMargin }: RulerProps) {
  // Major tick interval in frames: every 10 frames, or every 5 if the cycle
  // is short enough that 10 would be too sparse.
  const majorStep = maxTick <= 10 ? 5 : 10

  return (
    <svg className="absolute top-0 left-0 w-full h-full pointer-events-none">
      {ticks.map((t) => {
        const isMajor = t.frame % majorStep === 0
        const svgX = t.x - leftMargin
        const bottomY = 43
        const tickTop = isMajor ? 14 : 30
        const labelY = isMajor ? 11 : 27

        return (
          <g key={t.frame}>
            <line
              x1={svgX}
              y1={tickTop}
              x2={svgX}
              y2={bottomY}
              stroke={isMajor ? '#7A8490' : '#59616A'}
              strokeWidth={isMajor ? 1.5 : 1}
            />
            {isMajor && (
              <text
                x={svgX}
                y={labelY}
                fill="#A0A8B0"
                fontSize={12}
                textAnchor="middle"
                fontFamily="Consolas, monospace"
              >
                {t.frame}
              </text>
            )}
          </g>
        )
      })}
    </svg>
  )
}
