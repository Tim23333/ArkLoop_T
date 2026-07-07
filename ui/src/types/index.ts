export interface AxisAction {
  action_type: '部署' | '技能' | '撤退' | string
  oper: string
  pos?: string
  direction?: string
  cost?: number
  frame: number
}

export interface RecognizerState {
  current_view?: boolean
  selected_oper?: string | null
  side_source?: string | null
  deployed?: Record<string, [number, number]>
  pending_deploy?: Record<string, unknown> | null
}

export interface BackendState {
  current_view?: 'front' | 'side' | string
  selected_oper?: string | null
  side_source?: string | null
  deployed?: Record<string, string>
  pending_oper?: string | null
  queue_size?: number
  frame_count?: number
  // Live game-time feed from the WebSocket time source.
  game_time_sec?: number
  ws_connected?: boolean
  ws_mem_ok?: boolean
}

export interface OperatorInfo {
  id: string
  name: string
}

export interface BackendEvent {
  event_type: string
  data: Record<string, unknown>
}

export interface AxisBlock {
  row: ActionRow
  frame: number
  actions: AxisAction[]
  x?: number
  width?: number
  endX?: number
}

export type ActionRow = 'deploy' | 'skill' | 'retreat'

export interface TimelineItem {
  id: string
  name: string
}
