import { useMemo } from 'react'
import type { ActionRow, AxisAction, AxisBlock } from '../types'

const ROW_MAP: Record<string, ActionRow> = {
  '部署': 'deploy',
  '技能': 'skill',
  '撤退': 'retreat',
}

export interface LayoutConfig {
  tickWidth: number
  eventGap: number
  pointLength: number
  avatarWidth: number
  avatarHeight: number
  leftMargin: number
}

export const DEFAULT_LAYOUT: LayoutConfig = {
  tickWidth: 9,
  eventGap: 8,
  pointLength: 24,
  avatarWidth: 54,
  avatarHeight: 22,
  leftMargin: 0,
}

// Width of one action slot in the pentagon body, per row type.
const DEPLOY_SLOT_W = 54
const OTHER_SLOT_W  = 30

export interface TickPosition {
  frame: number
  x: number
  isMajor: boolean
}

export interface LayoutResult {
  ticks: TickPosition[]
  blocks: AxisBlock[]
  totalWidth: number
  startFrame: number
  endFrame: number
}

export function useTimelineLayout(
  actions: AxisAction[],
  config: Partial<LayoutConfig> = {},
  /** Render the timeline up to (but not including) this frame. */
  extendToFrame: number = 0,
): LayoutResult {
  const cfg = { ...DEFAULT_LAYOUT, ...config }

  return useMemo(() => {
    const { tickWidth, eventGap, leftMargin, pointLength } = cfg
    const majorStep = 10  // major label every 10 frames

    // Group actions into blocks by (row, frame)
    const blockMap = new Map<string, AxisBlock>()
    for (const action of actions) {
      const row = ROW_MAP[action.action_type]
      if (!row) continue
      const frame = action.frame ?? 0
      const key = `${row}:${frame}`
      let block = blockMap.get(key)
      if (!block) {
        block = { row, frame, actions: [] }
        blockMap.set(key, block)
      }
      block.actions.push(action)
    }

    const blocks = Array.from(blockMap.values())
    blocks.forEach((b) => {
      const slotW = b.row === 'deploy' ? DEPLOY_SLOT_W : OTHER_SLOT_W
      b.width = slotW * Math.max(1, b.actions.length)
    })

    // Visible frame range: always extend past the live position.
    const framesInActions = actions.map((a) => a.frame ?? 0)
    const minActionFrame = framesInActions.length ? Math.min(...framesInActions) : 0
    const maxActionFrame = framesInActions.length ? Math.max(...framesInActions) : 0
    const startFrame = Math.min(0, minActionFrame)
    const endFrame = Math.max(startFrame + 30, maxActionFrame + 20, extendToFrame)

    // Compute tick positions starting at x = leftMargin (= 0)
    const ticks: TickPosition[] = []
    let x = leftMargin

    for (let frame = startFrame; frame < endFrame; frame++) {
      const isMajor = frame % majorStep === 0
      ticks.push({ frame, x, isMajor })

      const tickBlocks = blocks.filter((b) => b.frame === frame)
      let blockEnd = x
      for (const block of tickBlocks) {
        block.x = x
        block.endX = x + pointLength + (block.width ?? OTHER_SLOT_W)
        blockEnd = Math.max(blockEnd, block.endX)
      }

      x = Math.max(x + tickWidth, blockEnd + eventGap)
    }

    return {
      ticks,
      blocks,
      totalWidth: x + 40,
      startFrame,
      endFrame,
    }
  }, [actions, cfg.tickWidth, cfg.eventGap, cfg.leftMargin, cfg.pointLength, extendToFrame])
}
