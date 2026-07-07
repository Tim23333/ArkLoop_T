import type { TickPosition } from '../hooks/useTimelineLayout'

interface RulerProps {
  ticks: TickPosition[]
  leftMargin: number
}

export function Ruler({ ticks, leftMargin }: RulerProps) {
  return (
    <svg className="absolute top-0 left-0 w-full h-full pointer-events-none">
      {ticks.map((t) => {
        const svgX = t.x - leftMargin
        const bottomY = 43
        const tickTop = t.isMajor ? 14 : 30
        const labelY = t.isMajor ? 11 : 27

        return (
          <g key={t.frame}>
            <line
              x1={svgX}
              y1={tickTop}
              x2={svgX}
              y2={bottomY}
              stroke={t.isMajor ? '#7A8490' : '#59616A'}
              strokeWidth={t.isMajor ? 1.5 : 1}
            />
            {t.isMajor && (
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
