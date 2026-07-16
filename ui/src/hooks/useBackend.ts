import { useCallback, useEffect, useState } from 'react'
import type { AxisAction, BackendEvent, BackendState, RecognizerState } from '../types'

export interface MapDevice {
  name: string
  pos: string
}

export interface TimelineSettings {
  map_code?: string
  max_tick?: number
  map_name?: string
  wait_time1?: number
  wait_time2?: number
  wait_time3?: number
  breakpoints?: number[]
  devices?: MapDevice[]
}

export interface TimelinePreset {
  name: string
  settings: TimelineSettings
}

export interface MapInfo {
  code: string
  name: string
}

export interface TimelineData {
  settings: TimelineSettings
  actions: AxisAction[]
}

export type AccelerationMode = 'cpu' | 'gpu'

export interface AccelerationModeResult {
  ok: boolean
  mode: AccelerationMode
  changed?: boolean
  installer_started?: boolean
  message?: string
  error?: string
}

export type ResourceSyncPhase =
  | 'idle'
  | 'checking'
  | 'downloading'
  | 'installing'
  | 'reloading'
  | 'complete'
  | 'error'

export interface ResourceSyncStatus {
  ok: boolean
  running: boolean
  phase: ResourceSyncPhase
  progress: number
  message: string
  checked_files?: number
  changed_files?: number
  downloaded_files?: number
  downloaded_bytes?: number
  total_download_bytes?: number
  avatar_files?: number
  map_files?: number
  proxy_mode?: 'system' | 'direct'
  commits?: Record<string, string>
  error?: string | null
  sequence: number
}

export interface PyWebviewApi {
  init_app: () => Promise<{ ok: boolean; error?: string; avatars_loaded?: number; runtime_mode?: 'cpu' | 'gpu' }>
  get_acceleration_mode: () => Promise<AccelerationModeResult>
  set_acceleration_mode: (mode: AccelerationMode) => Promise<AccelerationModeResult>
  start_resource_sync: () => Promise<ResourceSyncStatus>
  get_resource_sync_status: () => Promise<ResourceSyncStatus>
  start_recording: (mapCode: string, maxTick?: number, fakeAvatar?: boolean, frameOffset?: number, recognizerState?: RecognizerState, devices?: MapDevice[]) => Promise<void>
  stop_recording: () => Promise<AxisAction[]>
  pause_recording: () => Promise<{ frame: number; axis: AxisAction[] }>
  get_state: () => Promise<BackendState>
  get_axis: () => Promise<AxisAction[]>
  save_axis: (path: string) => Promise<boolean>
  get_avatar_url: (oper: string) => Promise<string>
  list_timelines: () => Promise<string[]>
  load_timeline: (name: string) => Promise<TimelineData>
  create_timeline: () => Promise<string>
  save_timeline: (name: string, actions: AxisAction[], settings: TimelineSettings) => Promise<boolean>
  append_to_timeline: (name: string, newActions: AxisAction[]) => Promise<boolean>
  save_breakpoints: (name: string, breakpoints: number[]) => Promise<boolean>
  delete_timeline: (name: string) => Promise<boolean>
  duplicate_timeline: (name: string) => Promise<string>
  rename_timeline: (oldName: string, newName: string) => Promise<string>
  export_timeline: (name: string) => Promise<boolean>
  import_timeline: () => Promise<string>
  get_pinned_timelines: () => Promise<string[]>
  set_pinned_timelines: (pinned: string[]) => Promise<boolean>
  get_window_bounds: () => Promise<{ x: number; y: number; width: number; height: number }>
  set_bounds: (x: number, y: number, width: number, height: number) => Promise<void>
  begin_window_drag: () => Promise<{ ok: boolean; error?: string }>
  set_overlay_mode: (enabled: boolean) => Promise<OverlayState>
  set_overlay_locked: (locked: boolean) => Promise<OverlayState>
  set_overlay_opacity: (opacity: number) => Promise<OverlayState>
  start_playback: (name: string, frameOffset?: number, breakpoints?: number[]) => Promise<boolean>
  stop_playback: () => Promise<void>
  pause_playback: () => Promise<{ ok: boolean }>
  reset_playback_state: () => Promise<void>
  list_operators: () => Promise<Array<{ id: string; name: string }>>
  list_maps: () => Promise<Array<{ code: string; name: string }>>
  list_timeline_presets: () => Promise<TimelinePreset[]>
  save_timeline_preset: (name: string, settings: TimelineSettings) => Promise<boolean>
  delete_timeline_preset: (name: string) => Promise<boolean>
  capture_with_grid: (mapCode: string) => Promise<string>
  get_app_config: () => Promise<AppConfig>
  update_app_config: (patch: Partial<AppConfig>) => Promise<boolean>
  get_ws_status: () => Promise<WSStatus>
  restart_ws_source: (url?: string) => Promise<boolean>
}

export interface OverlayState {
  ok: boolean
  enabled?: boolean
  locked?: boolean
  hotkey_available?: boolean
  hotkey?: string
  opacity?: number
  error?: string
}

export interface AppConfig {
  capture_type?: 'auto' | 'mumu' | 'win32' | string
  mumu?: {
    install_path?: string
    instance_index?: number
    window_name?: string
    sub_window_name?: string
  }
  time_source?: {
    ws_url?: string
  }
}

export interface WSStatus {
  connected?: boolean
  transport_connected?: boolean
  mem_ok?: boolean
  url?: string
  frame_count?: number
  game_time?: number
  ever_received?: boolean
}

export function useBackend() {
  const [events, setEvents] = useState<BackendEvent[]>([])
  const [state, setState] = useState<BackendState | null>(null)
  const [axis, setAxis] = useState<AxisAction[]>([])
  const [wsStatus, setWsStatus] = useState<WSStatus | null>(null)
  const [api, setApi] = useState<PyWebviewApi | undefined>(() =>
    typeof window !== 'undefined' && window.pywebview?.api
      ? (window.pywebview.api as unknown as PyWebviewApi)
      : undefined,
  )

  useEffect(() => {
    const onReady = () => {
      if (typeof window !== 'undefined' && window.pywebview?.api) {
        setApi(window.pywebview.api as unknown as PyWebviewApi)
      }
    }
    window.addEventListener('pywebviewready', onReady)
    onReady()
    return () => window.removeEventListener('pywebviewready', onReady)
  }, [])

  useEffect(() => {
    const handler = (data: unknown) => {
      const event = data as BackendEvent
      setEvents((prev) => [...prev, event])
      if (event.event_type === 'state') {
        // Merge (not replace) so WS fields are preserved.
        setState((prev) => ({ ...(prev ?? {}), ...event.data }) as BackendState)
      } else if (event.event_type === 'game_time') {
        const gt = event.data as {
          frame_count?: number
          game_time?: number
          connected?: boolean
          mem_ok?: boolean
        }
        setState((prev) => ({
          ...(prev ?? {}),
          frame_count: gt.frame_count ?? 0,
          game_time_sec: gt.game_time ?? 0,
          ws_connected: gt.connected ?? false,
          ws_mem_ok: gt.mem_ok ?? false,
        }) as BackendState)
      } else if (event.event_type === 'axis') {
        setAxis((event.data as unknown as AxisAction[]) ?? [])
      } else if (event.event_type === 'ws_status') {
        setWsStatus(event.data as WSStatus)
      } else if (event.event_type === 'playback_state') {
        const playback = event.data as {
          phase?: string
          target_frame?: number | null
          game_paused?: boolean
        }
        setState((prev) => ({
          ...(prev ?? {}),
          playback_phase: playback.phase,
          playback_target_frame: playback.target_frame,
          playback_game_paused: playback.game_paused,
        }) as BackendState)
      }
    }
    window.__onBackendEvent = handler
    return () => { window.__onBackendEvent = undefined }
  }, [])

  const initApp = useCallback(async () => {
    if (!api) return { ok: false }
    return api.init_app()
  }, [api])

  const getAccelerationMode = useCallback(async () => {
    if (!api) return { ok: false, mode: 'cpu', error: 'pywebview.api not available' } as AccelerationModeResult
    return api.get_acceleration_mode()
  }, [api])

  const setAccelerationMode = useCallback(async (mode: AccelerationMode) => {
    if (!api) return { ok: false, mode: 'cpu', error: 'pywebview.api not available' } as AccelerationModeResult
    return api.set_acceleration_mode(mode)
  }, [api])

  const startResourceSync = useCallback(async () => {
    if (!api) {
      return {
        ok: false,
        running: false,
        phase: 'error',
        progress: 0,
        message: '后端尚未就绪',
        error: 'pywebview.api not available',
        sequence: -1,
      } as ResourceSyncStatus
    }
    return api.start_resource_sync()
  }, [api])

  const getResourceSyncStatus = useCallback(async () => {
    if (!api) {
      return {
        ok: false,
        running: false,
        phase: 'error',
        progress: 0,
        message: '后端尚未就绪',
        error: 'pywebview.api not available',
        sequence: -1,
      } as ResourceSyncStatus
    }
    return api.get_resource_sync_status()
  }, [api])

  const startRecording = useCallback(
    async (
      mapCode: string,
      maxTick?: number,
      frameOffset?: number,
      recognizerState?: RecognizerState,
      devices?: MapDevice[],
    ) => {
      if (!api) throw new Error('pywebview.api not available')
      return api.start_recording(mapCode, maxTick, undefined, frameOffset, recognizerState, devices)
    },
    [api],
  )

  const stopRecording = useCallback(async () => {
    if (!api) throw new Error('pywebview.api not available')
    return api.stop_recording()
  }, [api])

  const pauseRecording = useCallback(async () => {
    if (!api) return { frame: 0, axis: [] }
    return api.pause_recording()
  }, [api])

  const getState = useCallback(async () => {
    if (!api) return {} as BackendState
    return api.get_state()
  }, [api])

  const getAxis = useCallback(async () => {
    if (!api) return [] as AxisAction[]
    return api.get_axis()
  }, [api])

  const saveAxis = useCallback(async (path: string) => {
    if (!api) return false
    return api.save_axis(path)
  }, [api])

  const getAvatarUrl = useCallback(async (oper: string) => {
    if (!api) return ''
    return api.get_avatar_url(oper)
  }, [api])

  const listTimelines = useCallback(async () => {
    if (!api) return [] as string[]
    return api.list_timelines()
  }, [api])

  const loadTimeline = useCallback(async (name: string) => {
    if (!api) return { settings: {}, actions: [] } as TimelineData
    return api.load_timeline(name)
  }, [api])

  const createTimeline = useCallback(async () => {
    if (!api) return ''
    return api.create_timeline()
  }, [api])

  const saveTimeline = useCallback(async (name: string, actions: AxisAction[], settings: TimelineSettings) => {
    if (!api) return false
    return api.save_timeline(name, actions, settings)
  }, [api])

  const appendToTimeline = useCallback(
    async (name: string, newActions: AxisAction[]) => {
      if (!api) return false
      return api.append_to_timeline(name, newActions)
    },
    [api],
  )

  const saveBreakpoints = useCallback(
    async (name: string, breakpoints: number[]) => {
      if (!api) return false
      return api.save_breakpoints(name, breakpoints)
    },
    [api],
  )

  const deleteTimeline = useCallback(async (name: string) => {
    if (!api) return false
    return api.delete_timeline(name)
  }, [api])

  const renameTimeline = useCallback(async (oldName: string, newName: string) => {
    if (!api) return ''
    return api.rename_timeline(oldName, newName)
  }, [api])

  const duplicateTimeline = useCallback(async (name: string) => {
    if (!api) return ''
    return api.duplicate_timeline(name)
  }, [api])

  const exportTimeline = useCallback(async (name: string) => {
    if (!api) return false
    return api.export_timeline(name)
  }, [api])

  const importTimeline = useCallback(async () => {
    if (!api) return ''
    return api.import_timeline()
  }, [api])

  const getPinnedTimelines = useCallback(async () => {
    if (!api) return []
    return api.get_pinned_timelines()
  }, [api])

  const setPinnedTimelines = useCallback(async (pinned: string[]) => {
    if (!api) return false
    return api.set_pinned_timelines(pinned)
  }, [api])

  const getWindowBounds = useCallback(async () => {
    if (!api) return { x: 0, y: 0, width: 0, height: 0 }
    return api.get_window_bounds()
  }, [api])

  const setBounds = useCallback(async (x: number, y: number, width: number, height: number) => {
    if (!api) return
    return api.set_bounds(x, y, width, height)
  }, [api])

  const beginWindowDrag = useCallback(async () => {
    if (!api) return { ok: false, error: 'pywebview.api not available' }
    return api.begin_window_drag()
  }, [api])

  const setOverlayMode = useCallback(async (enabled: boolean) => {
    if (!api) return { ok: false, error: 'pywebview.api not available' } as OverlayState
    return api.set_overlay_mode(enabled)
  }, [api])

  const setOverlayLocked = useCallback(async (locked: boolean) => {
    if (!api) return { ok: false, error: 'pywebview.api not available' } as OverlayState
    return api.set_overlay_locked(locked)
  }, [api])

  const setOverlayOpacity = useCallback(async (opacity: number) => {
    if (!api) return { ok: false, error: 'pywebview.api not available' } as OverlayState
    return api.set_overlay_opacity(opacity)
  }, [api])

  const startPlayback = useCallback(
    async (name: string, frameOffset?: number, breakpoints?: number[]) => {
      if (!api) return false
      return api.start_playback(name, frameOffset, breakpoints)
    },
    [api],
  )

  const stopPlayback = useCallback(async () => {
    if (!api) return
    return api.stop_playback()
  }, [api])

  const pausePlayback = useCallback(async () => {
    if (!api) return { ok: false }
    return api.pause_playback()
  }, [api])

  const resetPlaybackState = useCallback(async () => {
    if (!api) return
    return api.reset_playback_state()
  }, [api])

  const listOperators = useCallback(async () => {
    if (!api) return [] as Array<{ id: string; name: string }>
    return api.list_operators()
  }, [api])

  const listMaps = useCallback(async () => {
    if (!api) return [] as Array<{ code: string; name: string }>
    return api.list_maps()
  }, [api])

  const listTimelinePresets = useCallback(async () => {
    if (!api) return [] as TimelinePreset[]
    return api.list_timeline_presets()
  }, [api])

  const saveTimelinePreset = useCallback(async (name: string, settings: TimelineSettings) => {
    if (!api) return false
    return api.save_timeline_preset(name, settings)
  }, [api])

  const deleteTimelinePreset = useCallback(async (name: string) => {
    if (!api) return false
    return api.delete_timeline_preset(name)
  }, [api])

  const captureWithGrid = useCallback(async (mapCode: string) => {
    if (!api) return ''
    return api.capture_with_grid(mapCode)
  }, [api])

  const getAppConfig = useCallback(async () => {
    if (!api) return {} as AppConfig
    return api.get_app_config()
  }, [api])

  const updateAppConfig = useCallback(async (patch: Partial<AppConfig>) => {
    if (!api) return false
    return api.update_app_config(patch)
  }, [api])

  const getWsStatus = useCallback(async () => {
    if (!api) return { connected: false } as WSStatus
    return api.get_ws_status()
  }, [api])

  const restartWsSource = useCallback(async (url?: string) => {
    if (!api) return false
    return api.restart_ws_source(url)
  }, [api])

  return {
    api,
    events,
    state,
    axis,
    initApp,
    getAccelerationMode,
    setAccelerationMode,
    startResourceSync,
    getResourceSyncStatus,
    startRecording,
    stopRecording,
    pauseRecording,
    getState,
    getAxis,
    saveAxis,
    getAvatarUrl,
    listTimelines,
    loadTimeline,
    createTimeline,
    saveTimeline,
    deleteTimeline,
    renameTimeline,
    duplicateTimeline,
    exportTimeline,
    importTimeline,
    getPinnedTimelines,
    setPinnedTimelines,
    getWindowBounds,
    setBounds,
    beginWindowDrag,
    setOverlayMode,
    setOverlayLocked,
    setOverlayOpacity,
    startPlayback,
    stopPlayback,
    pausePlayback,
    resetPlaybackState,
    appendToTimeline,
    saveBreakpoints,
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
  }
}
