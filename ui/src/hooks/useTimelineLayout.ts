import { useMemo } from 'react'
import type { ActionRow, AxisAction, AxisBlock } from '../types'

const ROW_MAP: Record<string, ActionRow> = {
  '部署': 'deploy',
  '技能': 'skill',
  '撤退': 'retreat',
}

export interface LayoutConfig {
  maxTick: number
  tickWidth: number
  eventGap: number
  pointLength: number
  avatarWidth: number
  avatarHeight: number
  // leftMargin is no longer used for content offset — the scroll container
  // itself is positioned after the fixed left column.  Keep it as 0.
  leftMargin: number
}

export const DEFAULT_LAYOUT: LayoutConfig = {
  maxTick: 30,
  tickWidth: 9,
  eventGap: 8,
  pointLength: 24,
  avatarWidth: 54,   // deploy slot width (avatar + pos + direction)
  avatarHeight: 22,
  leftMargin: 0,     // ticks start at x=0 in scroll-container space
}

// Width of one action slot in the pentagon body, per row type.
// Deploy needs room for avatar + pos text + direction arrow.
// Others just need room for the avatar.
const DEPLOY_SLOT_W = 54   // ≈ avatarH(22) + gap(4) + pos+dir text(22) + padding(6)
const OTHER_SLOT_W  = 30   // ≈ avatarH(22) + padding(8)

export interface TickPosition {
  cycle: number
  tick: number
  /** Absolute frame count within the timeline (= cycle * maxTick + tick). */
  frame: number
  x: number
  isCycleStart: boolean
  displayTick: number
}

export interface LayoutResult {
  ticks: TickPosition[]
  blocks: AxisBlock[]
  totalWidth: number
  startCycle: number
  endCycle: number
}

export function useTimelineLayout(
  actions: AxisAction[],
  config: Partial<LayoutConfig> = {},
  /** Render the timeline up to (but not including) this cycle, regardless of
   *  where the actions or the live playhead are. The caller passes a *quantized*
   *  value (e.g. rounded up to chunks of 10) so this memo stays stable while the
   *  playhead moves — pre-rendering a big chunk ahead instead of re-laying-out
   *  on every tick. */
  extendToCycle: number = 0,
): LayoutResult {
  const cfg = { ...DEFAULT_LAYOUT, ...config }

  return useMemo(() => {
    const { maxTick, tickWidth, eventGap, leftMargin, pointLength } = cfg

    // Group actions into blocks by (row, cycle, tick)
    const blockMap = new Map<string, AxisBlock>()
    for (const action of actions) {
      const row = ROW_MAP[action.action_type]
      if (!row) continue
      const key = `${row}:${action.cycle}:${action.tick}`
      let block = blockMap.get(key)
      if (!block) {
        block = { row, cycle: action.cycle, tick: action.tick, actions: [] }
        blockMap.set(key, block)
      }
      block.actions.push(action)
    }

    const blocks = Array.from(blockMap.values())
    blocks.forEach((b) => {
      const slotW = b.row === 'deploy' ? DEPLOY_SLOT_W : OTHER_SLOT_W
      b.width = slotW * Math.max(1, b.actions.length)
    })

    // Visible cycle range: always extend at least 3 cycles past the live position
    // so the timeline feels infinite while recording/playing.
    const cyclesInActions = actions.map((a) => a.cycle ?? 0)
    const minActionCycle = cyclesInActions.length ? Math.min(...cyclesInActions) : 0
    const maxActionCycle = cyclesInActions.length ? Math.max(...cyclesInActions) : 0
    const startCycle = Math.min(0, minActionCycle)
    const endCycle = Math.max(startCycle + 3, maxActionCycle + 2, extendToCycle)

    // Compute tick positions starting at x = leftMargin (= 0)
    const ticks: TickPosition[] = []
    let x = leftMargin

    for (let cycle = startCycle; cycle < endCycle; cycle++) {
      for (let tick = 0; tick < maxTick; tick++) {
        ticks.push({ cycle, tick, frame: cycle * maxTick + tick, x, isCycleStart: tick === 0, displayTick: tick })

        const tickBlocks = blocks.filter((b) => b.cycle === cycle && b.tick === tick)
        let blockEnd = x
        for (const block of tickBlocks) {
          block.x = x
          // endX = tip + body — next tick must start after here to avoid overlap.
          block.endX = x + pointLength + (block.width ?? OTHER_SLOT_W)
          blockEnd = Math.max(blockEnd, block.endX)
        }

        x = Math.max(x + tickWidth, blockEnd + eventGap)
      }
    }

    return {
      ticks,
      blocks,
      totalWidth: x + 40,
      startCycle,
      endCycle,
    }
  }, [actions, cfg.maxTick, cfg.tickWidth, cfg.eventGap, cfg.leftMargin, cfg.pointLength, extendToCycle])
}
