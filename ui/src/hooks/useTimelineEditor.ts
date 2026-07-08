import { useCallback, useState } from 'react'
import type { TimelineSettings } from './useBackend'
import type { AxisAction, AxisBlock, ActionRow } from '../types'
import { actionTypeForRow, insertActionSorted, moveBlockToFrame } from '../utils/timeline'

export interface EditDialogState {
  mode: 'add' | 'edit'
  row: ActionRow
  frame: number
  existingAction?: AxisAction
}

interface UseTimelineEditorArgs {
  loadedAxis: AxisAction[]
  setLoadedAxis: (actions: AxisAction[] | ((prev: AxisAction[]) => AxisAction[])) => void
  selectedTimeline: string
  timelineSettings: TimelineSettings
  saveTimeline: (name: string, actions: AxisAction[], settings: TimelineSettings) => Promise<boolean>
  isRecording: boolean
  isPlaying: boolean
}

export function useTimelineEditor({
  loadedAxis,
  setLoadedAxis,
  selectedTimeline,
  timelineSettings,
  saveTimeline,
  isRecording,
  isPlaying,
}: UseTimelineEditorArgs) {
  const [editDialog, setEditDialog] = useState<EditDialogState | null>(null)

  const saveAxis = useCallback(async (newAxis: AxisAction[]) => {
    setLoadedAxis(newAxis)
    if (selectedTimeline) {
      await saveTimeline(selectedTimeline, newAxis, timelineSettings).catch(() => false)
    }
  }, [selectedTimeline, saveTimeline, timelineSettings, setLoadedAxis])

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
    await saveAxis(moveBlockToFrame(loadedAxis, block, newFrame))
  }, [isRecording, isPlaying, loadedAxis, saveAxis])

  const handleDeleteAction = useCallback(async (block: AxisBlock) => {
    if (isRecording || isPlaying) return
    const typeStr = actionTypeForRow(block.row)
    await saveAxis(
      loadedAxis.filter((a) => !(a.action_type === typeStr && a.frame === block.frame)),
    )
  }, [isRecording, isPlaying, loadedAxis, saveAxis])

  const handleEditConfirm = useCallback(async (action: AxisAction) => {
    if (!editDialog) return
    let newAxis: AxisAction[]
    if (editDialog.mode === 'add') {
      newAxis = insertActionSorted(loadedAxis, action)
    } else {
      const typeStr = actionTypeForRow(editDialog.row)
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

  return {
    editDialog,
    setEditDialog,
    saveAxis,
    handleAddAction,
    handleEditAction,
    handleMoveAction,
    handleDeleteAction,
    handleEditConfirm,
  }
}
