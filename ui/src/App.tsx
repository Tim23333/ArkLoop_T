import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Sidebar } from './components/Sidebar'
import { Workspace } from './components/Workspace'
import { Timeline } from './components/Timeline'
import { SaveDialog } from './components/SaveDialog'
import { NewTimelineDialog } from './components/NewTimelineDialog'
import { SettingsDialog } from './components/SettingsDialog'
import { OperatorSearchDialog } from './components/OperatorSearchDialog'
import { ResizeHandles } from './components/ResizeHandles'
import { MiniOverlay } from './components/MiniOverlay'
import { ResourceSyncButton } from './components/ResourceSyncButton'
import { useBackend } from './hooks/useBackend'
import { useTimelineEditor } from './hooks/useTimelineEditor'
import { compareActionTime, formatGameTime } from './utils/timeline'
import type { AxisAction, OperatorInfo, RecognizerState } from './types'
import type { TimelineSettings, TimelinePreset } from './hooks/useBackend'
import type { NewTimelineResult } from './components/NewTimelineDialog'


export default function App() {
  const {
    api,
    axis: backendAxis,
    state,
    initApp,
    setAccelerationMode,
    startResourceSync,
    getResourceSyncStatus,
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
    setOverlayMode,
    setOverlayLocked,
    startPlayback,
    stopPlayback,
    pausePlayback,
    resetPlaybackState,
    listOperators,
    listMaps,
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
  const [runtimeMode, setRuntimeMode] = useState<'cpu' | 'gpu' | null>(null)
  const [isSwitchingRuntimeMode, setIsSwitchingRuntimeMode] = useState(false)
  const [runtimeModeFeedback, setRuntimeModeFeedback] = useState<{ message: string; error: boolean } | null>(null)
  const didInit = useRef(false)

  useEffect(() => {
    if (!api || didInit.current) return
    didInit.current = true
    setIsLoading(true)
    initApp()
      .then((result) => {
        setLoadingDone(true)
        setRuntimeMode(result.runtime_mode ?? null)
      })
      .catch(() => {})
      .finally(() => setIsLoading(false))
  }, [api, initApp])

  // ── operator list (for edit dialog) ────────────────────────
  const [operatorList, setOperatorList] = useState<OperatorInfo[]>([])
  useEffect(() => {
    if (!api) return
    listOperators().then((ops) => setOperatorList(ops)).catch(() => {})
  }, [api, listOperators])

  // ── map data (for new timeline dialog) ────────
  const [mapList, setMapList] = useState<Array<{ code: string; name: string }>>([])
  const [resourceRevision, setResourceRevision] = useState(0)
  useEffect(() => {
    if (!api) return
    listMaps().then(setMapList).catch(() => {})
  }, [api, listMaps])

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
  const [isCompactOverlay, setCompactOverlay] = useState(false)
  const [isOverlayLocked, setOverlayLockedState] = useState(false)
  const [overlayError, setOverlayError] = useState('')

  const handleRecord = useCallback(async () => {
    try {
      const mapCode = timelineSettings.map_code ?? '1-7'
      // Decide append-vs-fresh BEFORE calling backend so the offset is
      // consistent. If a timeline is loaded and either (a) a previous session
      // left a non-zero cycle offset or (b) the timeline already has actions,
      // treat the new recording as a continuation that appends to the same
      // timeline. This supports the "record after playback" flow where the
      // user wants to keep adding actions to the existing axis.
      const isResume = !!selectedTimeline && (isCompactOverlay || frameOffset > 0 || loadedAxis.length > 0)
      if (isResume) setAppendingTo(selectedTimeline)
      else setAppendingTo('')
      await startRecording(
        mapCode,
        timelineSettings.max_tick,
        frameOffset,
        isResume ? recognizerState : {},
        timelineSettings.devices,
      )
      setIsRecording(true)
    } catch (e) {
      console.error(e)
    }
  }, [startRecording, timelineSettings, frameOffset, selectedTimeline, loadedAxis.length, recognizerState, isCompactOverlay])

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
        if (isCompactOverlay) {
          const result = await setOverlayMode(false)
          if (result.ok) {
            setCompactOverlay(false)
            setOverlayLockedState(false)
          } else {
            setOverlayError(result.error ?? '无法打开保存页面')
            return
          }
        }
        setPendingAxis(newActions)
        setShowSaveDialog(true)
      }
    } catch (e) {
      console.error(e)
    }
  }, [stopRecording, appendingTo, appendToTimeline, isCompactOverlay, setOverlayMode])

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
      const { mapCode, mapName, maxTick, devices } = result
      setShowNewTimelineDialog(false)
      try {
        const name = await createTimeline()
        const settings: TimelineSettings = {
          map_code: mapCode,
          map_name: mapName || undefined,
          max_tick: maxTick,
          wait_time1: 0.02,
          wait_time2: 0.1,
          wait_time3: 0.3,
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

  const handlePlay = useCallback(async () => {
    if (!selectedTimeline || isPlaying || isRecording) return
    try {
      const bps = timelineSettings.breakpoints ?? []
      await startPlayback(
        selectedTimeline,
        frameOffset,
        bps,
      )
      setIsPlaying(true)
    } catch (e) {
      console.error(e)
    }
  }, [selectedTimeline, isPlaying, isRecording, startPlayback, frameOffset, timelineSettings])

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
      } else if (ev?.event_type === 'overlay_lock_changed') {
        const lockData = ev.data as { locked?: boolean }
        setOverlayLockedState(Boolean(lockData?.locked))
        setOverlayError('')
      } else if (ev?.event_type === 'overlay_mode_changed') {
        const modeData = ev.data as { enabled?: boolean; locked?: boolean }
        setCompactOverlay(Boolean(modeData?.enabled))
        setOverlayLockedState(Boolean(modeData?.locked))
      }
    }
    return () => { window.__onBackendEvent = prev }
  }, [])

  // ── live game time / frame count from the WS time source ─────
  const gameTimeSec = state?.game_time_sec ?? 0
  const frameCount  = state?.frame_count   ?? 0
  const wsConnected = state?.ws_connected  ?? false

  const handleToggleRuntimeMode = useCallback(async () => {
    if (!runtimeMode || isSwitchingRuntimeMode) return
    const requestedMode = runtimeMode === 'cpu' ? 'gpu' : 'cpu'
    setIsSwitchingRuntimeMode(true)
    setRuntimeModeFeedback(null)
    try {
      const result = await setAccelerationMode(requestedMode)
      setRuntimeMode(result.mode)
      setRuntimeModeFeedback({
        message: result.message ?? result.error ?? `无法切换到 ${requestedMode.toUpperCase()} 模式`,
        error: !result.ok,
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setRuntimeModeFeedback({ message: `切换失败：${message}`, error: true })
    } finally {
      setIsSwitchingRuntimeMode(false)
    }
  }, [runtimeMode, isSwitchingRuntimeMode, setAccelerationMode])

  const handleResourcesSynced = useCallback(async () => {
    const [operators, maps] = await Promise.all([
      listOperators().catch(() => [] as OperatorInfo[]),
      listMaps().catch(() => [] as Array<{ code: string; name: string }>),
    ])
    setOperatorList(operators)
    setMapList(maps)
    setResourceRevision((value) => value + 1)
  }, [listMaps, listOperators])

  const handleShowOverlay = useCallback(async () => {
    setOverlayError('')
    const result = await setOverlayMode(true)
    if (result.ok) {
      setCompactOverlay(true)
      setOverlayLockedState(false)
    } else {
      setOverlayError(result.error ?? '无法切换展示模式')
    }
  }, [setOverlayMode])

  const handleRestoreFullView = useCallback(async () => {
    setOverlayError('')
    const result = await setOverlayMode(false)
    if (result.ok) {
      setCompactOverlay(false)
      setOverlayLockedState(false)
    } else {
      setOverlayError(result.error ?? '无法恢复完整页面')
    }
  }, [setOverlayMode])

  const handleToggleOverlayLock = useCallback(async () => {
    setOverlayError('')
    const result = await setOverlayLocked(!isOverlayLocked)
    if (result.ok) {
      setOverlayLockedState(Boolean(result.locked))
    } else {
      setOverlayError(result.error ?? '无法切换窗口锁定')
    }
  }, [isOverlayLocked, setOverlayLocked])

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
  }, [api, deployedOperatorNames, getAvatarUrl, resourceRevision])

  const {
    editDialog,
    setEditDialog,
    handleAddAction,
    handleEditAction,
    handleMoveAction,
    handleDeleteAction,
    handleEditConfirm,
  } = useTimelineEditor({
    loadedAxis,
    setLoadedAxis,
    selectedTimeline,
    timelineSettings,
    saveTimeline,
    isRecording,
    isPlaying,
  })

  if (isCompactOverlay) {
    return (
      <MiniOverlay
        timelineName={selectedTimeline}
        frameCount={frameCount}
        gameTimeSec={gameTimeSec}
        wsConnected={wsConnected}
        frameOffset={frameOffset}
        isRecording={isRecording}
        isPlaying={isPlaying}
        isLoading={isLoading}
        locked={isOverlayLocked}
        lockError={overlayError}
        actions={displayedAxis}
        breakpoints={breakpoints}
        getAvatarUrl={getAvatarUrl}
        onRecord={handleRecord}
        onStop={handleStop}
        onPlay={handlePlay}
        onStopPlay={handleStopPlay}
        onPause={handlePause}
        onToggleLock={handleToggleOverlayLock}
        onRestore={handleRestoreFullView}
        onAddAction={handleAddAction}
        onEditAction={handleEditAction}
        onMoveAction={handleMoveAction}
        onDeleteAction={handleDeleteAction}
        onAddBreakpoint={handleAddBreakpoint}
        onRemoveBreakpoint={handleRemoveBreakpoint}
        getWindowBounds={getWindowBounds}
        setBounds={setBounds}
      />
    )
  }

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
          {/* Live game time + frame count from the WS time source.
              Idle: also shows the 续录偏移 (frameOffset) input for resume. */}
          <div className="flex items-center gap-4 text-xs font-mono">
            <button
              type="button"
              onClick={handleToggleRuntimeMode}
              disabled={!runtimeMode || isSwitchingRuntimeMode}
              className={[
                'flex min-w-[84px] items-center justify-center gap-1.5 rounded border px-2 py-0.5 font-sans font-semibold tracking-wide transition-colors',
                runtimeMode && !isSwitchingRuntimeMode ? 'cursor-pointer hover:brightness-125' : 'cursor-wait',
                runtimeMode === 'gpu'
                  ? 'border-accent-green/45 bg-accent-green/10 text-accent-green'
                  : runtimeMode === 'cpu'
                    ? 'border-accent-blue/40 bg-accent-blue/10 text-accent-blue'
                    : 'border-border-panel bg-black/10 text-text-dim',
              ].join(' ')}
              title={runtimeModeFeedback?.message ?? (runtimeMode === 'gpu' ? '点击尝试切换到 CPU 模式' : '点击尝试切换到 GPU 模式')}
            >
              <span
                className={[
                  'h-1.5 w-1.5 rounded-full',
                  runtimeMode === 'gpu'
                    ? 'bg-accent-green'
                    : runtimeMode === 'cpu'
                      ? 'bg-accent-blue'
                      : 'bg-text-dim animate-pulse',
                  isSwitchingRuntimeMode ? 'animate-pulse' : '',
                ].join(' ')}
              />
              {isSwitchingRuntimeMode
                ? '切换中'
                : runtimeMode === 'gpu'
                  ? 'GPU 模式'
                  : runtimeMode === 'cpu'
                    ? 'CPU 模式'
                    : '模式检测中'}
            </button>
            {runtimeModeFeedback && (
              <span
                className={`max-w-64 truncate text-[10px] ${runtimeModeFeedback.error ? 'text-[#ff8a86]' : 'text-accent-green'}`}
                title={runtimeModeFeedback.message}
              >
                {runtimeModeFeedback.message}
              </span>
            )}
            <ResourceSyncButton
              disabled={isRecording || isPlaying || isSwitchingRuntimeMode}
              startSync={startResourceSync}
              getStatus={getResourceSyncStatus}
              onSynced={handleResourcesSynced}
            />
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
            <button
              onClick={handleShowOverlay}
              className="whitespace-nowrap rounded border border-accent-blue/40 px-2 py-0.5 text-xs text-accent-blue transition-colors hover:border-accent-blue hover:bg-accent-blue/10"
              title="切换到半透明迷你展示模式"
            >
              展示切换
            </button>
            {overlayError && (
              <span className="max-w-48 truncate text-[10px] text-[#ff8a86]" title={overlayError}>
                {overlayError}
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
          presets={presets}
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
