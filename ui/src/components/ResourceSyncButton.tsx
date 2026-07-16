import { useCallback, useEffect, useRef, useState } from 'react'
import type { ResourceSyncStatus } from '../hooks/useBackend'


interface ResourceSyncButtonProps {
  disabled?: boolean
  startSync: () => Promise<ResourceSyncStatus>
  getStatus: () => Promise<ResourceSyncStatus>
  onSynced: () => void | Promise<void>
}


export function ResourceSyncButton({
  disabled = false,
  startSync,
  getStatus,
  onSynced,
}: ResourceSyncButtonProps) {
  const [status, setStatus] = useState<ResourceSyncStatus | null>(null)
  const [pollVersion, setPollVersion] = useState(0)
  const handledSequence = useRef<number | null>(null)

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined

    const poll = async () => {
      try {
        const next = await getStatus()
        if (cancelled) return
        setStatus(next)
        if (next.running) {
          timer = setTimeout(poll, 500)
        } else if (
          next.ok
          && next.phase === 'complete'
          && next.sequence !== handledSequence.current
        ) {
          handledSequence.current = next.sequence
          await onSynced()
        }
      } catch (error) {
        if (cancelled) return
        setStatus({
          ok: false,
          running: false,
          phase: 'error',
          progress: 0,
          message: '无法读取同步状态',
          error: error instanceof Error ? error.message : String(error),
          sequence: -1,
        })
      }
    }

    void poll()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [getStatus, onSynced, pollVersion])

  const handleSync = useCallback(async () => {
    if (disabled || status?.running) return
    const next = await startSync()
    setStatus(next)
    setPollVersion((value) => value + 1)
  }, [disabled, startSync, status?.running])

  const running = Boolean(status?.running)
  const feedback = status?.phase === 'error'
    ? status.error ?? status.message
    : status?.phase === 'complete'
      ? status.message
      : running
        ? status?.message
        : ''
  const title = feedback
    ? `${feedback}；下载默认使用 Windows 系统代理`
    : '同步最新干员头像和关卡格子数据；下载默认使用 Windows 系统代理'

  return (
    <div className="flex min-w-0 items-center gap-1.5 font-sans">
      <button
        type="button"
        onClick={handleSync}
        disabled={disabled || running}
        className={[
          'flex min-w-[76px] items-center justify-center gap-1.5 whitespace-nowrap rounded border px-2 py-0.5 text-xs font-semibold transition-colors',
          running
            ? 'cursor-wait border-accent-yellow/45 bg-accent-yellow/10 text-accent-yellow'
            : disabled
              ? 'cursor-not-allowed border-border-panel bg-black/10 text-text-dim'
              : 'border-accent-blue/40 bg-accent-blue/10 text-accent-blue hover:border-accent-blue hover:bg-accent-blue/15',
        ].join(' ')}
        title={title}
      >
        <span className={[
          'block h-2 w-2 rounded-full border border-current border-r-transparent',
          running ? 'animate-spin' : '',
        ].join(' ')} />
        {running ? `同步 ${Math.round(status?.progress ?? 0)}%` : '同步资源'}
      </button>
      {feedback && (
        <span
          className={`max-w-44 truncate text-[10px] ${status?.phase === 'error' ? 'text-[#ff8a86]' : 'text-accent-green'}`}
          title={title}
        >
          {feedback}
        </span>
      )}
    </div>
  )
}
