import { useCallback, useEffect, useRef, useState } from 'react'

interface WorkspaceProps {
  mapCode?: string
  onCapture?: (mapCode: string) => Promise<string>
}

const RECAPTURE_MESSAGES = [
  '是否重新截图',
  '这张图打完了？',
  '想截一张漂亮点的？',
  '超级无敌大截图',
  '没办法，就再给你截一次吧...',
]

/**
 * Right-pane preview area.
 *
 * - Empty: clicking the area captures a MuMu screenshot with chess-style tile
 *   labels overlaid in red.
 * - Image shown: clicking the image opens a small confirm bubble whose text
 *   cycles through {@link RECAPTURE_MESSAGES} on each open. Click the bubble
 *   to recapture; click anywhere else (or the close ×) to dismiss the bubble.
 */
export function Workspace({ mapCode, onCapture }: WorkspaceProps) {
  const [imgUrl, setImgUrl] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string>('')
  // Bubble state — once an image is shown, clicking the image opens this.
  const [bubble, setBubble] = useState<{ x: number; y: number; messageIdx: number } | null>(null)
  // Persists across bubble open/close so the message advances every time
  // the user re-opens the bubble (not just on session start).
  const recaptureCountRef = useRef(0)
  const bubbleRef = useRef<HTMLDivElement>(null)

  const doCapture = useCallback(async () => {
    if (!onCapture) return
    if (!mapCode) {
      setError('请先选择或新建一个轴（需要地图代号）')
      return
    }
    setError('')
    setLoading(true)
    try {
      const data = await onCapture(mapCode)
      if (data) {
        setImgUrl(data)
      } else {
        setError('截图失败，请确认 MuMu 正在运行')
      }
    } catch (e) {
      console.error(e)
      setError('截图出错')
    } finally {
      setLoading(false)
    }
  }, [onCapture, mapCode])

  // Close bubble on outside click. Mirrors the timeline's context-menu pattern.
  useEffect(() => {
    if (!bubble) return
    const close = (e: MouseEvent) => {
      if (bubbleRef.current && !bubbleRef.current.contains(e.target as Node)) {
        setBubble(null)
      }
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [bubble])

  // ── Empty placeholder ────────────────────────────────────────────
  if (!imgUrl) {
    return (
      <div
        onClick={() => { if (!loading) void doCapture() }}
        className={[
          'flex-1 h-full bg-workspace border-b border-border-panel flex flex-col items-center justify-center gap-2',
          loading ? 'cursor-wait' : 'cursor-pointer',
          'hover:bg-[#0d1217] transition-colors',
        ].join(' ')}
      >
        <span className="text-text-dim opacity-40 text-sm">
          {loading ? '截图中…' : '点击此处截取 MuMu 并显示坐标'}
        </span>
        {error && (
          <span className="text-accent-red text-xs opacity-80">{error}</span>
        )}
      </div>
    )
  }

  // ── Image shown ──────────────────────────────────────────────────
  const message = RECAPTURE_MESSAGES[
    Math.min(recaptureCountRef.current, RECAPTURE_MESSAGES.length - 1)
  ]

  const openBubble = (e: React.MouseEvent) => {
    // Cycle the message index on every open: 0 → 1 → ... → last (sticky).
    setBubble({
      x: e.clientX,
      y: e.clientY,
      messageIdx: recaptureCountRef.current,
    })
    recaptureCountRef.current += 1
  }

  return (
    <div
      className="flex-1 h-full bg-workspace border-b border-border-panel relative"
    >
      <img
        src={imgUrl}
        alt="MuMu 截图 + 坐标"
        onClick={openBubble}
        className={['absolute inset-0 w-full h-full object-contain', loading ? 'opacity-60 cursor-wait' : 'cursor-pointer'].join(' ')}
      />

      <button
        onClick={() => { setImgUrl(''); setError(''); setBubble(null); recaptureCountRef.current = 0 }}
        className="absolute top-2 right-2 w-7 h-7 leading-none rounded-full bg-accent-blue text-white shadow-md hover:bg-accent-blue/80 transition-colors text-base font-semibold flex items-center justify-center"
        title="清除截图"
      >
        ×
      </button>

      {loading && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <span className="text-text-primary bg-panel/80 border border-border-panel rounded px-3 py-1 text-xs">截图中…</span>
        </div>
      )}

      {bubble && (
        <div
          ref={bubbleRef}
          className="fixed z-[9999] rounded border border-border-panel bg-[#1A1E24] shadow-xl py-0.5 text-xs"
          style={{ top: bubble.y, left: bubble.x }}
        >
          <button
            className="block w-full text-left px-3 py-1.5 text-text-muted hover:bg-[#222A31] hover:text-accent-blue whitespace-nowrap"
            onClick={() => {
              setBubble(null)
              void doCapture()
            }}
          >
            {RECAPTURE_MESSAGES[Math.min(bubble.messageIdx, RECAPTURE_MESSAGES.length - 1)]}
          </button>
        </div>
      )}

      {error && (
        <div className="absolute left-2 bottom-2 pointer-events-none">
          <span className="text-accent-red text-xs bg-panel/80 border border-border-panel rounded px-2 py-1">{error}</span>
        </div>
      )}

      {/* Reference suppresses TS "unused" warning if a future refactor drops
          the `message` variable; keeps the public meaning of recaptureCountRef
          tied to the same source of truth used by RECAPTURE_MESSAGES. */}
      <span className="hidden" data-current-message={message} />
    </div>
  )
}
