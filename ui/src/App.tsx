import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Sidebar } from './components/Sidebar'
import { Workspace } from './components/Workspace'
import { Timeline } from './components/Timeline'
import { SaveDialog } from './components/SaveDialog'
import { NewTimelineDialog } from './components/NewTimelineDialog'
import { SettingsDialog } from './components/SettingsDialog'
import { OperatorSearchDialog } from './components/OperatorSearchDialog'
import { ResizeHandles } from './components/ResizeHandles'
import { useBackend } from './hooks/useBackend'
import type { AxisAction, AxisBlock, ActionRow, OperatorInfo, RecognizerState } from './types'
import type { TimelineSettings, TimelinePreset } from './hooks/useBackend'
import type { NewTimelineResult } from './components/NewTimelineDialog'

function compareActionTime(a: AxisAction, b: AxisAction): number {
  return (a.frame ?? 0) - (b.frame ?? 0)
}

/** Format a game-time value (float seconds) as M:SS.cs for the live readout. */
function formatGameTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00.00'
  const totalCs = Math.floor(seconds * 100)
  const cs = totalCs % 100
  const totalSec = Math.floor(totalCs / 100)
  const s = totalSec % 60
  const m = Math.floor(totalSec / 60)
  return `${m}:${String(s).padStart(2, '0')}.${String(cs).padStart(2, '0')}`
}

/** Insert a single action into a chronologically sorted action list.
 *  Finds the last action whose (cycle, tick) <= the new action and places
 *  the new action right after it; if every action is later, it goes first.
 */
function insertActionSorted(actions: AxisAction[], action: AxisAction): AxisAction[] {
  let insertIndex = 0
  for (let i = actions.length - 1; i >= 0; i--) {
    if (compareActionTime(actions[i], action) <= 0) {
      insertIndex = i + 1
      break
    }
  }
  const next = actions.slice()
  next.splice(insertIndex, 0, action)
  return next
}

/** Insert a group of actions (sharing the same target time) while preserving
 *  the relative order of the group and of the existing actions.
 */
function insertActionsAtTime(actions: AxisAction[], group: AxisAction[]): AxisAction[] {
  if (group.length === 0) return actions
  const target = group[0]
  let insertIndex = 0
  for (let i = actions.length - 1; i >= 0; i--) {
    if (compareActionTime(actions[i], target) <= 0) {
      insertIndex = i + 1
      break
    }
  }
  const next = actions.slice()
  next.splice(insertIndex, 0, ...group)
  return next
}

/** Move every action that belongs to a timeline block to a new frame
 *  and re-position the whole group in chronological order.
 */
function moveBlockToFrame(
  actions: AxisAction[],
  block: AxisBlock,
  newFrame: number,
): AxisAction[] {
  const typeStr = block.row === 'deploy' ? '部署' : block.row === 'skill' ? '技能' : '撤退'
  const moving: AxisAction[] = []
  const remaining = actions.filter((a) => {
    if (a.action_type === typeStr && a.frame === block.frame) {
      moving.push({ ...a, frame: newFrame })
      return false
    }
    return true
  })
  return insertActionsAtTime(remaining, moving)
}

interface EditDialogState {
  mode: 'add' | 'edit'
  row: ActionRow
  frame: number
  existingAction?: AxisAction
  blockIndex?: number  // index in loadedAxis for edit mode
}

export default function App() {
  const {
    api,
    axis: backendAxis,
    state,
    initApp,
    startRecording,
    stopRecording,
    pauseRecording,
    getAvatarUrl,
    listTimelines,
    loadTimeline,
    createTimeline,
    saveTimeline,
    appendToTimeline,
    saveBreakpoints,
    deleteTimeline,
    renameTimeline,
    duplicateTimeline,
    exportTimeline,
    importTimeline,
    getPinnedTimelines,
    setPinnedTimelines,
    getWindowBounds,
    setBounds,
    startPlayback,
    stopPlayback,
    pausePlayback,
    resetPlaybackState,
    listOperators,
    listMaps,
    listCalibrations,
    getCalibrationInfo,
    listTimelinePresets,
    saveTimelinePreset,
    deleteTimelinePreset,
    captureWithGrid,
    getAppConfig,
    updateAppConfig,
    getWsStatus,
    restartWsSource,
    wsStatus,
  } = useBackend()

  // ── init ────────────────────────────────────────────────────
  const [isLoading, setIsLoading] = useState(false)
  const [loadingDone, setLoadingDone] = useState(false)
  const didInit = useRef(false)

  useEffect(() => {
    if (!api || didInit.current) return
    didInit.current = true
    setIsLoading(true)
    initApp()
      .then(() => setLoadingDone(true))
      .catch(() => {})
      .finally(() => setIsLoading(false))
  }, [api, initApp])

  // ── operator list (for edit dialog) ────────────────────────
  const [operatorList, setOperatorList] = useState<OperatorInfo[]>([])
  useEffect(() => {
    if (!api) return
    listOperators().then((ops) => setOperatorList(ops)).catch(() => {})
  }, [api, listOperators])

  // ── map & calibration data (for new timeline dialog) ────────
  const [mapList, setMapList] = useState<Array<{ code: string; name: string }>>([])
  const [calibrationList, setCalibrationList] = useState<string[]>([])
  useEffect(() => {
    if (!api) return
    listMaps().then(setMapList).catch(() => {})
    listCalibrations().then(setCalibrationList).catch(() => {})
  }, [api, listMaps, listCalibrations])

  const [showNewTimelineDialog, setShowNewTimelineDialog] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [presets, setPresets] = useState<TimelinePreset[]>([])

  const refreshPresets = useCallback(async () => {
    const list = await listTimelinePresets().catch(() => [] as TimelinePreset[])
    setPresets(list)
  }, [listTimelinePresets])

  useEffect(() => {
    if (!api) return
    refreshPresets()
  }, [api, refreshPresets])

  // ── timeline list ────────────────────────────────────────────
  const [timelines, setTimelines] = useState<string[]>([])
  const [pinnedTimelines, setPinnedLocal] = useState<string[]>([])
  const [selectedTimeline, setSelectedTimeline] = useState<string>('')
  const [loadedAxis, setLoadedAxis] = useState<AxisAction[]>([])
  const [timelineSettings, setTimelineSettings] = useState<TimelineSettings>({})

  // ── operators deployed in the current timeline (avatar strip) ──
  // Read-only summary derived from the displayed axis; updates live as the
  // user records / edits actions.

  const refreshTimelines = useCallback(async () => {
    const names = await listTimelines().catch(() => [] as string[])
    setTimelines(names)
    return names
  }, [listTimelines])

  useEffect(() => {
    if (!api) return
    refreshTimelines()
    getPinnedTimelines().then(setPinnedLocal).catch(() => {})
  }, [api, refreshTimelines, getPinnedTimelines])

  const handleSelectTimeline = useCallback(
    async (name: string) => {
      setSelectedTimeline(name)
      // Switching timeline drops any pending pause / resume state — including
      // the deployed set the backend would carry into a resume.
      setFrameOffset(0)
      setAppendingTo('')
      setRecognizerState({})
      void resetPlaybackState()
      try {
        const data = await loadTimeline(name)
        const actions = (data.actions as AxisAction[]) ?? []
        actions.forEach((a) => { if (a.frame == null) a.frame = 0 })
        actions.sort((a, b) => compareActionTime(a, b))
        setLoadedAxis(actions)
        setTimelineSettings(data.settings ?? {})
      } catch {
        setLoadedAxis([])
        setTimelineSettings({})
      }
    },
    [loadTimeline, resetPlaybackState],
  )

  // ── recording state ──────────────────────────────────────────
  const [isRecording, setIsRecording] = useState(false)
  const [showSaveDialog, setShowSaveDialog] = useState(false)
  const [pendingAxis, setPendingAxis] = useState<AxisAction[]>([])

  // Resume / pause coordination:
  //   frameOffset   — bias applied to the next record / play session.
  //                   Editable directly in the toolbar's 全局费用 input when
  //                   idle.  Pause writes the current cycle into it.
  //   appendingTo   — if non-empty, the next stop_recording silently appends
  //                   into that timeline (used by "resume recording" flow).
  //   recognizerState — snapshot of the recognizer state machine at the
  //                     moment playback stopped/paused, used to warm up the
  //                     next recording session.
  const [frameOffset, setFrameOffset] = useState(0)
  const [appendingTo, setAppendingTo] = useState<string>('')
  const [recognizerState, setRecognizerState] = useState<RecognizerState>({})

  const handleRecord = useCallback(async () => {
    try {
      const mapCode = timelineSettings.map_code ?? '1-7'
      // Decide append-vs-fresh BEFORE calling backend so the offset is
      // consistent. If a timeline is loaded and either (a) a previous session
      // left a non-zero cycle offset or (b) the timeline already has actions,
      // treat the new recording as a continuation that appends to the same
      // timeline. This supports the "record after playback" flow where the
      // user wants to keep adding actions to the existing axis.
      const isResume = !!selectedTimeline && (frameOffset > 0 || loadedAxis.length > 0)
      if (isResume) setAppendingTo(selectedTimeline)
      else setAppendingTo('')
      await startRecording(
        mapCode,
        timelineSettings.max_tick,
        timelineSettings.calibration_path,
        frameOffset,
        isResume ? recognizerState : {},
        timelineSettings.devices,
      )
      setIsRecording(true)
    } catch (e) {
      console.error(e)
    }
  }, [startRecording, timelineSettings, frameOffset, selectedTimeline, loadedAxis.length, recognizerState])

  const handleStop = useCallback(async () => {
    try {
      const axis = await stopRecording()
      setIsRecording(false)
      const newActions = axis ?? []
      if (appendingTo) {
        // Resume-record flow: silently append into the original timeline.
        await appendToTimeline(appendingTo, newActions)
        setLoadedAxis((prev) => [...prev, ...newActions])
        setAppendingTo('')
        setFrameOffset(0)
        setRecognizerState({})
      } else {
        setPendingAxis(newActions)
        setShowSaveDialog(true)
      }
    } catch (e) {
      console.error(e)
    }
  }, [stopRecording, appendingTo, appendToTimeline])

  const handlePauseRecord = useCallback(async () => {
    try {
      const result = await pauseRecording()
      setIsRecording(false)
      const newActions = result.axis ?? []
      // Always append paused-recording into the timeline (it's not "done", just paused).
      if (selectedTimeline) {
        await appendToTimeline(selectedTimeline, newActions)
        setLoadedAxis((prev) => [...prev, ...newActions])
      }
      setFrameOffset(result.frame)
      setAppendingTo('')
    } catch (e) {
      console.error(e)
    }
  }, [pauseRecording, selectedTimeline, appendToTimeline])

  const handleSaveDialog = useCallback(
    async (newName: string) => {
      const oldFile = selectedTimeline
      const newFile = newName.endsWith('.json') ? newName : `${newName}.json`
      try {
        await saveTimeline(newFile, pendingAxis, timelineSettings)
        if (oldFile && oldFile !== newFile) {
          await deleteTimeline(oldFile).catch(() => {})
        }
        const names = await refreshTimelines()
        setSelectedTimeline(newFile)
        setLoadedAxis(pendingAxis)
        setPinnedLocal((prev) => {
          if (!prev.includes(oldFile)) return prev
          const next = prev.filter((n) => n !== oldFile)
          next.unshift(newFile)
          setPinnedTimelines(next)
          return next
        })
        void names
      } catch (e) {
        console.error(e)
      }
      setShowSaveDialog(false)
    },
    [selectedTimeline, pendingAxis, timelineSettings, saveTimeline, deleteTimeline, refreshTimelines, setPinnedTimelines],
  )

  const handleDeleteDialog = useCallback(async () => {
    if (selectedTimeline) {
      await deleteTimeline(selectedTimeline).catch(() => {})
      await refreshTimelines()
      setSelectedTimeline('')
      setLoadedAxis([])
    }
    setShowSaveDialog(false)
  }, [selectedTimeline, deleteTimeline, refreshTimelines])

  // ── new timeline ─────────────────────────────────────────────
  const handleNewTimeline = useCallback(() => {
    setShowNewTimelineDialog(true)
  }, [])

  const handleNewTimelineConfirm = useCallback(
    async (result: NewTimelineResult) => {
      const { mapCode, mapName, calibration, maxTick, devices } = result
      setShowNewTimelineDialog(false)
      try {
        const name = await createTimeline()
        const settings: TimelineSettings = {
          map_code: mapCode,
          map_name: mapName || undefined,
          max_tick: maxTick,
          calibration_path: calibration || undefined,
          wait_time1: 0.02,
          wait_time2: 0.1,
          wait_time3: 0.3,
          bullet_threshold: 15.0,
          frame_threshold: 2.0,
          devices: devices.length > 0 ? devices : undefined,
        }
        await saveTimeline(name, [], settings)
        await refreshTimelines()
        setSelectedTimeline(name)
        setLoadedAxis([])
        setTimelineSettings(settings)
      } catch (e) {
        console.error(e)
      }
    },
    [createTimeline, saveTimeline, refreshTimelines],
  )

  const handleSavePreset = useCallback(
    async (name: string, settings: TimelineSettings) => {
      const ok = await saveTimelinePreset(name, settings)
      if (ok) await refreshPresets()
      return ok
    },
    [saveTimelinePreset, refreshPresets],
  )

  const handleDeletePreset = useCallback(
    async (name: string) => {
      const ok = await deleteTimelinePreset(name)
      if (ok) await refreshPresets()
      return ok
    },
    [deleteTimelinePreset, refreshPresets],
  )

  // ── sidebar actions ──────────────────────────────────────────
  const handlePin = useCallback(
    async (name: string) => {
      const next = [name, ...pinnedTimelines.filter((n) => n !== name)]
      setPinnedLocal(next)
      await setPinnedTimelines(next)
    },
    [pinnedTimelines, setPinnedTimelines],
  )

  const handleUnpin = useCallback(
    async (name: string) => {
      const next = pinnedTimelines.filter((n) => n !== name)
      setPinnedLocal(next)
      await setPinnedTimelines(next)
    },
    [pinnedTimelines, setPinnedTimelines],
  )

  const handleRename = useCallback(
    async (oldName: string, newName: string) => {
      const actual = await renameTimeline(oldName, newName)
      if (selectedTimeline === oldName) setSelectedTimeline(actual)
      setPinnedLocal((prev) => {
        const next = prev.map((n) => (n === oldName ? actual : n))
        setPinnedTimelines(next)
        return next
      })
      await refreshTimelines()
    },
    [renameTimeline, selectedTimeline, refreshTimelines, setPinnedTimelines],
  )

  const handleDuplicate = useCallback(
    async (name: string) => {
      const created = await duplicateTimeline(name)
      if (created) {
        await refreshTimelines()
      }
    },
    [duplicateTimeline, refreshTimelines],
  )

  const handleExport = useCallback(
    async (name: string) => {
      await exportTimeline(name)
    },
    [exportTimeline],
  )

  const handleImport = useCallback(async () => {
    const imported = await importTimeline()
    if (imported) {
      await refreshTimelines()
      setSelectedTimeline(imported)
    }
  }, [importTimeline, refreshTimelines])

  const handleDelete = useCallback(
    async (name: string) => {
      await deleteTimeline(name)
      if (selectedTimeline === name) {
        setSelectedTimeline('')
        setLoadedAxis([])
      }
      setPinnedLocal((prev) => {
        const next = prev.filter((n) => n !== name)
        setPinnedTimelines(next)
        return next
      })
      await refreshTimelines()
    },
    [deleteTimeline, selectedTimeline, refreshTimelines, setPinnedTimelines],
  )

  // ── playback state ───────────────────────────────────────────
  const [isPlaying, setIsPlaying] = useState(false)
  const [autoEnter, setAutoEnter] = useState(false)

  const handlePlay = useCallback(async () => {
    if (!selectedTimeline || isPlaying || isRecording) return
    try {
      const bps = timelineSettings.breakpoints ?? []
      await startPlayback(
        selectedTimeline,
        autoEnter,
        frameOffset,
        bps,
        timelineSettings.calibration_path,
      )
      setIsPlaying(true)
    } catch (e) {
      console.error(e)
    }
  }, [selectedTimeline, isPlaying, isRecording, autoEnter, startPlayback, frameOffset, timelineSettings])

  const handleStopPlay = useCallback(async () => {
    try {
      await stopPlayback()
    } catch (e) {
      console.error(e)
    }
    setIsPlaying(false)
    // Full stop (red ■), unlike pause, abandons the run: drop the carried
    // deployed/recognizer state and rewind the resume offset so the next Play
    // / Record starts clean. (stop_playback already cleared the backend copy.)
    setRecognizerState({})
    setFrameOffset(0)
  }, [stopPlayback])

  const handlePausePlay = useCallback(async () => {
    try {
      await pausePlayback()
    } catch (e) {
      console.error(e)
    }
    // setIsPlaying(false) will be applied by the 'paused' / 'playback_done'
    // event handler below.  We also snapshot the latest cycle into offset.
    setFrameOffset((c) => state?.frame_count ?? c)
  }, [pausePlayback, state])

  // Dispatch Pause to whichever session is active.
  const handlePause = useCallback(() => {
    if (isRecording) void handlePauseRecord()
    else if (isPlaying) void handlePausePlay()
  }, [isRecording, isPlaying, handlePauseRecord, handlePausePlay])

  // ── breakpoints ──────────────────────────────────────────────
  const breakpoints = timelineSettings.breakpoints ?? []

  const updateBreakpoints = useCallback(
    async (next: number[]) => {
      setTimelineSettings((prev) => ({ ...prev, breakpoints: next }))
      if (selectedTimeline) {
        await saveBreakpoints(selectedTimeline, next).catch(() => {})
      }
    },
    [selectedTimeline, saveBreakpoints],
  )

  const handleAddBreakpoint = useCallback(
    (frame: number) => {
      if (breakpoints.includes(frame)) return
      void updateBreakpoints([...breakpoints, frame].sort((a, b) => a - b))
    },
    [breakpoints, updateBreakpoints],
  )

  const handleRemoveBreakpoint = useCallback(
    (frame: number) => {
      void updateBreakpoints(breakpoints.filter((f) => f !== frame))
    },
    [breakpoints, updateBreakpoints],
  )

  // Listen for playback_done + paused events
  useEffect(() => {
    const prev = window.__onBackendEvent
    window.__onBackendEvent = (data: unknown) => {
      prev?.(data)
      const ev = data as {
        event_type?: string
        data?: {
          source?: string
          frame?: number
          state?: RecognizerState
        }
      }
      if (ev?.event_type === 'playback_done') {
        setIsPlaying(false)
        if (typeof ev.data?.frame === 'number') setFrameOffset(ev.data.frame)
        if (ev.data?.state) setRecognizerState(ev.data.state as RecognizerState)
      } else if (ev?.event_type === 'paused') {
        if (ev.data?.source === 'playback') setIsPlaying(false)
        if (ev.data?.source === 'recording') setIsRecording(false)
        if (typeof ev.data?.frame === 'number') setFrameOffset(ev.data.frame)
        if (ev.data?.state) setRecognizerState(ev.data.state as RecognizerState)
      }
    }
    return () => { window.__onBackendEvent = prev }
  }, [])

  // ── live game time / frame count from the WS time source ─────
  const gameTimeSec = state?.game_time_sec ?? 0
  const frameCount  = state?.frame_count   ?? 0
  const wsConnected = state?.ws_connected  ?? false

  // ── displayed axis ───────────────────────────────────────────
  const displayedAxis = useMemo(() => {
    if (isRecording) {
      // When resuming a recording (frameOffset > 0), merge previously saved
      // actions with the live backend stream so the timeline shows the full
      // axis instead of only the current recording session.
      if (frameOffset > 0 && loadedAxis.length > 0) {
        return [...loadedAxis, ...backendAxis]
      }
      return backendAxis
    }
    if (isPlaying && frameOffset > 0) {
      // During playback the runner subtracts frameOffset from each action's
      // frame so that execution happens at game frame (action.frame - offset).
      // Show the adjusted frames so the timeline blocks align with the playhead
      // (which tracks the live game frame from WS).
      return loadedAxis.map((a) => ({ ...a, frame: (a.frame ?? 0) - frameOffset }))
    }
    return loadedAxis
  }, [isRecording, isPlaying, backendAxis, loadedAxis, frameOffset])

  // Unique operator names deployed in the current timeline, in first-seen
  // order.  Derived from displayedAxis so it updates live during recording
  // and after edits without an extra backend round-trip.
  const deployedOperatorNames = useMemo(() => {
    const seen = new Set<string>()
    const ordered: string[] = []
    for (const action of displayedAxis) {
      if (action.action_type !== '部署') continue
      const name = action.oper
      if (name && !seen.has(name)) {
        seen.add(name)
        ordered.push(name)
      }
    }
    return ordered
  }, [displayedAxis])

  const [mapOperators, setMapOperators] = useState<Array<{ name: string; url: string }>>([])
  useEffect(() => {
    if (!api) { setMapOperators([]); return }
    let cancelled = false
    Promise.all(
      deployedOperatorNames.map(async (name) => ({
        name,
        url: await getAvatarUrl(name).catch(() => ''),
      })),
    ).then((list) => { if (!cancelled) setMapOperators(list) })
    return () => { cancelled = true }
  }, [api, deployedOperatorNames, getAvatarUrl])

  // ── editing ──────────────────────────────────────────────────
  const [editDialog, setEditDialog] = useState<EditDialogState | null>(null)

  const saveAxis = useCallback(async (newAxis: AxisAction[]) => {
    setLoadedAxis(newAxis)
    if (selectedTimeline) {
      await saveTimeline(selectedTimeline, newAxis, timelineSettings).catch(() => {})
    }
  }, [selectedTimeline, saveTimeline, timelineSettings])

  const handleAddAction = useCallback((row: ActionRow, frame: number) => {
    if (isRecording || isPlaying) return
    setEditDialog({ mode: 'add', row, frame })
  }, [isRecording, isPlaying])

  const handleEditAction = useCallback((block: AxisBlock) => {
    if (isRecording || isPlaying) return
    const action = block.actions[0]
    if (!action) return
    setEditDialog({
      mode: 'edit',
      row: block.row,
      frame: block.frame,
      existingAction: action,
    })
  }, [isRecording, isPlaying])

  const handleMoveAction = useCallback(async (block: AxisBlock, newFrame: number) => {
    if (isRecording || isPlaying) return
    if (block.frame === newFrame) return
    const newAxis = moveBlockToFrame(loadedAxis, block, newFrame)
    await saveAxis(newAxis)
  }, [isRecording, isPlaying, loadedAxis, saveAxis])

  const handleDeleteAction = useCallback(async (block: AxisBlock) => {
    if (isRecording || isPlaying) return
    const typeStr = block.row === 'deploy' ? '部署' : block.row === 'skill' ? '技能' : '撤退'
    const newAxis = loadedAxis.filter(
      (a) => !(a.action_type === typeStr && a.frame === block.frame)
    )
    await saveAxis(newAxis)
  }, [isRecording, isPlaying, loadedAxis, saveAxis])

  const handleEditConfirm = useCallback(async (action: AxisAction) => {
    if (!editDialog) return
    let newAxis: AxisAction[]
    if (editDialog.mode === 'add') {
      newAxis = insertActionSorted(loadedAxis, action)
    } else {
      const typeStr = editDialog.row === 'deploy' ? '部署' : editDialog.row === 'skill' ? '技能' : '撤退'
      const timeChanged = action.frame !== editDialog.frame
      if (!timeChanged) {
        let replaced = false
        newAxis = loadedAxis.map((a) => {
          if (!replaced && a.action_type === typeStr && a.frame === editDialog.frame) {
            replaced = true
            return action
          }
          return a
        })
        if (!replaced) newAxis = insertActionSorted(loadedAxis, action)
      } else {
        let removed = false
        const remaining = loadedAxis.filter((a) => {
          if (!removed && a.action_type === typeStr && a.frame === editDialog.frame) {
            removed = true
            return false
          }
          return true
        })
        newAxis = insertActionSorted(remaining, action)
      }
    }
    await saveAxis(newAxis)
    setEditDialog(null)
  }, [editDialog, loadedAxis, saveAxis])

  return (
    <div className="w-full h-full min-w-[946px] bg-root flex flex-col overflow-hidden">
      {/* Top: sidebar + workspace — flex-[11] to make timeline ~0.8× smaller */}
      <div className="flex flex-[11] min-h-0">
        <Sidebar
          timelines={timelines}
          pinnedTimelines={pinnedTimelines}
          selected={selectedTimeline}
          isLoading={isLoading}
          loadingDone={loadingDone}
          onSelect={handleSelectTimeline}
          onNewTimeline={handleNewTimeline}
          onPin={handlePin}
          onUnpin={handleUnpin}
          onRename={handleRename}
          onDelete={handleDelete}
          onDuplicate={handleDuplicate}
          onExport={handleExport}
          onImport={handleImport}
          onOpenSettings={() => setShowSettings(true)}
        />
        <Workspace
          mapCode={timelineSettings.map_code}
          onCapture={captureWithGrid}
          wsTimeSec={gameTimeSec}
          wsFrameCount={frameCount}
          wsConnected={wsConnected}
          wsMemOk={state?.ws_mem_ok ?? false}
        />
      </div>

      {/* Bottom: toolbar + timeline — flex-[4] */}
      <div className="flex flex-[4] min-h-[212px] flex-col">
        {/* Toolbar row */}
        <div className="h-8 bg-panel border-y border-border-panel shrink-0 flex items-center gap-4 px-3">
          {/* Auto-enter toggle — always visible, only functional during playback */}
          <button
            onClick={() => setAutoEnter((v) => !v)}
            className={[
              'text-xs border border-border-panel rounded px-2 py-0.5 transition-colors whitespace-nowrap',
              isPlaying ? 'text-text-muted hover:text-text-primary' : 'text-text-dim opacity-50 cursor-default',
            ].join(' ')}
          >
            自动进图: {autoEnter ? '开' : '关'}
          </button>
          {/* Live game time + frame count from the WS time source.
              Idle: also shows the 续录偏移 (frameOffset) input for resume. */}
          <div className="flex items-center gap-4 text-xs font-mono">
            <span
              className="text-text-dim flex items-center gap-1"
              title="游戏实时时间（WS 时间源）"
            >
              游戏时间:
              <span className={wsConnected ? 'text-text-primary' : 'text-text-dim'}>
                {wsConnected ? formatGameTime(gameTimeSec) : '未连接'}
              </span>
            </span>
            <span className="text-text-dim" title="游戏逻辑帧（WS 时间源）">
              帧数:{' '}
              <span className={wsConnected ? 'text-text-primary' : 'text-text-dim'}>
                {wsConnected ? frameCount : '--'}
              </span>
            </span>
            {!(isRecording || isPlaying) && (
              <span className="text-text-dim flex items-center gap-1" title="续录/续播起始周期">
                续录偏移:
                <input
                  type="number"
                  min={0}
                  value={frameOffset}
                  onChange={(e) => {
                    const v = parseInt(e.target.value, 10)
                    setFrameOffset(Number.isFinite(v) && v >= 0 ? v : 0)
                  }}
                  title="编辑后下次录制/执行从这里开始"
                  className="w-14 bg-[#11161B] border border-border-panel rounded text-accent-blue text-xs font-mono px-1 py-0.5 focus:outline-none focus:border-accent-blue"
                />
              </span>
            )}
            {appendingTo && (
              <span className="text-accent-yellow text-xs">续录入: {appendingTo}</span>
            )}
          </div>
          {/* Operators ever deployed on this map (across saved timelines).
              Read-only hint strip — clicking does nothing. */}
          {mapOperators.length > 0 && (
            <div
              className="flex items-center gap-1 ml-2 overflow-x-auto"
              title={`本图历史部署过的干员 (${mapOperators.length})`}
            >
              {mapOperators.map((op) => (
                <img
                  key={op.name}
                  src={op.url}
                  alt={op.name}
                  title={op.name}
                  className="w-6 h-6 rounded-sm border border-border-panel shrink-0 object-cover"
                />
              ))}
            </div>
          )}
        </div>

        <Timeline
          actions={displayedAxis}
          recording={isRecording}
          playing={isPlaying}
          currentFrame={frameCount}
          breakpoints={breakpoints}
          getAvatarUrl={getAvatarUrl}
          isLoading={isLoading}
          onRecord={handleRecord}
          onStop={handleStop}
          onPlay={handlePlay}
          onStopPlay={handleStopPlay}
          onPause={handlePause}
          onAddAction={handleAddAction}
          onEditAction={handleEditAction}
          onMoveAction={handleMoveAction}
          onDeleteAction={handleDeleteAction}
          onAddBreakpoint={handleAddBreakpoint}
          onRemoveBreakpoint={handleRemoveBreakpoint}
        />
      </div>

      {showNewTimelineDialog && (
        <NewTimelineDialog
          maps={mapList}
          calibrations={calibrationList}
          presets={presets}
          getCalibrationInfo={getCalibrationInfo}
          onConfirm={handleNewTimelineConfirm}
          onDismiss={() => setShowNewTimelineDialog(false)}
          onSavePreset={handleSavePreset}
          onDeletePreset={handleDeletePreset}
        />
      )}

      {showSaveDialog && (
        <SaveDialog
          defaultName={selectedTimeline.replace(/\.json$/, '')}
          onSave={handleSaveDialog}
          onDelete={handleDeleteDialog}
          onDismiss={() => setShowSaveDialog(false)}
        />
      )}

      {editDialog && (
        <OperatorSearchDialog
          mode={editDialog.mode}
          row={editDialog.row}
          targetFrame={editDialog.frame}
          existingAction={editDialog.existingAction}
          operators={operatorList}
          getAvatarUrl={getAvatarUrl}
          onConfirm={handleEditConfirm}
          onDismiss={() => setEditDialog(null)}
        />
      )}

      <SettingsDialog
        open={showSettings}
        getConfig={getAppConfig}
        updateConfig={updateAppConfig}
        getWsStatus={getWsStatus}
        restartWsSource={restartWsSource}
        wsStatus={wsStatus}
        onDismiss={() => setShowSettings(false)}
      />

      <ResizeHandles getWindowBounds={getWindowBounds} setBounds={setBounds} />
    </div>
  )
}
