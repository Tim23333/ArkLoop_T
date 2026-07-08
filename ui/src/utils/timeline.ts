import type { AxisAction, AxisBlock } from '../types'

export function compareActionTime(a: AxisAction, b: AxisAction): number {
  return (a.frame ?? 0) - (b.frame ?? 0)
}

export function formatGameTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00.00'
  const totalCs = Math.floor(seconds * 100)
  const cs = totalCs % 100
  const totalSec = Math.floor(totalCs / 100)
  const s = totalSec % 60
  const m = Math.floor(totalSec / 60)
  return `${m}:${String(s).padStart(2, '0')}.${String(cs).padStart(2, '0')}`
}

export function insertActionSorted(actions: AxisAction[], action: AxisAction): AxisAction[] {
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

export function insertActionsAtTime(actions: AxisAction[], group: AxisAction[]): AxisAction[] {
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

export function actionTypeForRow(row: AxisBlock['row']): string {
  return row === 'deploy' ? '部署' : row === 'skill' ? '技能' : '撤退'
}

export function moveBlockToFrame(
  actions: AxisAction[],
  block: AxisBlock,
  newFrame: number,
): AxisAction[] {
  const typeStr = actionTypeForRow(block.row)
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

