import { Record, Stop, Play, Pause } from './Icons'

interface TransportControlsProps {
  isRecording?: boolean
  isPlaying?: boolean
  isLoading?: boolean
  onRecord?: () => void
  onResumeRecord?: () => void
  canResumeRecord?: boolean
  onStop?: () => void
  onPlay?: () => void
  onStopPlay?: () => void
  onPause?: () => void
  compact?: boolean
}

export function TransportControls({
  isRecording = false,
  isPlaying = false,
  isLoading = false,
  onRecord,
  onResumeRecord,
  canResumeRecord = false,
  onStop,
  onPlay,
  onStopPlay,
  onPause,
  compact = false,
}: TransportControlsProps) {
  const busy = isRecording || isPlaying
  const disabled = isLoading
  const resumeDisabled = disabled || busy || !canResumeRecord

  return (
    <div className={`flex items-center ${compact ? 'gap-2' : 'gap-5'}`}>
      {/* Record — disabled when loading or recording/playing */}
      <button
        onClick={onRecord}
        disabled={disabled || busy}
        title={isLoading ? '初始化中…' : isRecording ? '录制中' : '开始新录制（不使用续录偏移）'}
        className={disabled || busy ? 'opacity-30 cursor-not-allowed' : 'hover:opacity-80'}
      >
        <Record className="w-3 h-3" />
      </button>

      {/* Resume recording is deliberately separate from fresh recording. */}
      <button
        onClick={onResumeRecord}
        disabled={resumeDisabled}
        title={
          isLoading
            ? '初始化中…'
            : !canResumeRecord
              ? '请先选择时间轴并设置大于 0 的续录偏移'
              : '续录（使用当前偏移并合并原轴）'
        }
        className={resumeDisabled ? 'cursor-not-allowed opacity-30' : 'hover:opacity-80'}
      >
        <span className="rounded border border-accent-yellow/60 px-1 py-0.5 text-[10px] font-semibold leading-none text-accent-yellow">
          续录
        </span>
      </button>

      {/* Stop — stops recording or playback, whichever is active */}
      <button
        onClick={isRecording ? onStop : isPlaying ? onStopPlay : undefined}
        disabled={disabled || !busy}
        title={isRecording ? '停止录制' : isPlaying ? '停止播放' : '停止'}
        className={disabled || !busy ? 'opacity-30 cursor-not-allowed' : 'hover:opacity-80'}
      >
        <Stop className="w-2.5 h-2.5" />
      </button>

      {/* Play — disabled when loading or recording */}
      <button
        onClick={onPlay}
        disabled={disabled || isRecording}
        title={isPlaying ? '运行中…' : '运行轴'}
        className={disabled || isRecording ? 'opacity-30 cursor-not-allowed' : isPlaying ? 'opacity-60 cursor-default' : 'hover:opacity-80'}
      >
        <Play className="w-3 h-3.5 text-accent-green" />
      </button>

      {/* Pause — only active while recording/playing.  Stops the active session
          and records the current cycle as offset so the next Record/Play
          resumes from that point. */}
      <button
        onClick={onPause}
        disabled={disabled || !busy}
        title={busy ? '暂停（保留 cycle offset）' : '暂停'}
        className={disabled || !busy ? 'opacity-30 cursor-not-allowed' : 'hover:opacity-80'}
      >
        <Pause className="w-3 h-3.5 text-accent-yellow" />
      </button>
    </div>
  )
}
