class MuMuEmulatorConfig:
    WINDOW_NAME = "MuMu模拟器12"
    SUB_WINDOW_NAME = "MuMuPlayer"

class GameRatioConfig:
    COST_AREA_RATIO = (0.906, 0.685, 1, 0.755) # (left, top, right, bottom)
    COST_NUMBER_AREA_RATIO = (0.33, 0, 1, 0.9) # (left, top, right, bottom)
    OPERATOR_AREA_RATIO = (0, 0.8, 1, 1) # (left, top, right, bottom)
    LAST_OPER_RATIO = (0.95, 0.9) # (x, y)
    RETREAT_RATIO = (0.4569, 0.3352) # (x, y)
    SKILL_RATIO = (0.6412, 0.5857) # (x, y)
    START_BUTTON_RATIO = (0.87, 0.74) # (x, y)
    SPEED_BUTTON_RATIO = (0.86, 0.07) # (x, y)
    PAUSE_BUTTON_RATIO = (0.94, 0.07) # (x, y)
    # Detection boxes for UI buttons (left, top, right, bottom).
    # Initialized from the point ratios above with the old 0.05 tolerance.
    PAUSE_BUTTON_BOX = (0.89, 0.02, 0.99, 0.12)
    SPEED_BUTTON_BOX = (0.81, 0.02, 0.91, 0.12)
    DIRECTION_RATIO = 0.2
    DEPLOY_DRAG_RATIO = 0.03
    DEPLOY_DELTA_RATIO = 0.02
    OPERATOR_SELECTED_RATIO = 0.9


class SlotDetectionConfig:
    """Flag + OCR + mouse-zone based operator-slot detection."""

    # Horizontal gap threshold for flag deduplication and minimum mouse-zone width.
    MIN_FLAG_X_GAP = 0.04

    # Mouse-zone geometry, relative to the BattleOpersFlag center (cx, cy).
    MOUSE_ZONE_MIDLINE_OFFSET = 0.0117  # zone midline is at cx - offset
    MOUSE_ZONE_BOTTOM_OFFSET = 0.1653   # bottom = cy + offset

    # Fixed cost-number OCR ROI around the flag center.
    OCR_ROI_HALF_WIDTH = 0.0120   # left/right half-width
    OCR_ROI_TOP_OFFSET = 0.0030      # top = cy + offset (negative moves up)
    OCR_ROI_BOTTOM_OFFSET = 0.0380  # bottom = cy + offset

    # When True, each detected flag is validated by OCR-reading the cost number
    # above it; slots that fail to produce a parseable integer are logged as
    # warnings (but still included — the check is advisory, not a hard filter).
    # Disabled by default: the flag-detection + deduplication pipeline is
    # reliable enough on its own and OCR adds latency with no benefit.
    OCR_FLAG_VALIDATION: bool = False


class DebugConfig:
    """Centralized diagnostic switches for logging and debug artifacts."""

    # Per-feature logging. Most default to False to avoid noisy output in
    # scripts that run with global DEBUG level.
    LOG_RESOURCE_LOAD: bool = False       # avatar / map / metadata load info
    LOG_TICK_DETECTION: bool = False      # reserved legacy debug flag

    # Keyframe / artifact saving.
    SAVE_ACTION_KEYFRAMES: bool = False
    SAVE_ACTION_KEYFRAMES_ALL: bool = False  # also archive IGNORE actions
    ACTION_ARCHIVE_DIR: str = "recordings/actions"
    DEBUG_OUTPUT_DIR: str = "debug"

    # Recognition warnings (e.g. operator-area click not in any slot).
    SAVE_RECOGNITION_WARNINGS: bool = True
    RECOGNITION_WARNING_DIR: str = "recordings/warnings/operator_area"


class ImageProcessingConfig:
    WHITE_THRESHOLD = 160
    SCREEN_STANDARD_SIZE = (1280, 720)
    AVATAR_STANDARD_SIZE = (120, 120)
    AVATAR_CROP_SIZE = (60, 60)
    OCR_CONFIDENCE_THRESHOLD = 60
    TEMPLATE_MATCH_THRESHOLD = 0.75

class ViewCalculationConfig:
    FROM_RATIO = 9 / 16
    TO_RATIO = 3 / 4
    NEAR = 0.3
    FAR = 1000

class RecordingConfig:
    OUTPUT_DIR = "recordings"
    FPS = 60
    CODEC_PRESET = "ultrafast"
    PIXEL_FORMAT = "yuv420p"

class InputRecordingConfig:
    """Configuration for mouse / keyboard input recording."""

    # Standardized game canvas size used for normalized coordinates.
    SCREEN_STANDARD_SIZE = (1280, 720)

    # Buttons we are interested in recording.
    RECORDED_MOUSE_BUTTONS = {"left", "right", "middle"}

    # Debounce window (seconds) used when aggregating click sequences.
    CLICK_AGGREGATION_WINDOW = 0.050

    # Minimum drag distance in normalized screen coordinates to count as a drag.
    DRAG_THRESHOLD_RATIO = 0.03

class PerformActionConfig:
    BULLET_TIME_FRAMES = 30
    PRECISE_PAUSE_FRAMES = 10
    FRAME_STEP_INTERVAL = 0.008
    PAUSE_TOGGLE_SETTLE = 0.05
    PAUSE_VERIFY_RETRIES = 3
    LATE_SKIP_TOLERANCE_FRAMES = 2
    MINIMUM_WAITTIME = 0.02
    FRAME_WAITTIME = 0.1
    GENERAL_WAITTIME = 0.3
    DRAG_HOLD_TIME = 0.16
    DRAG_STEP_WAIT = 0.055
    DRAG_STEPS = 10
    DEPLOY_TO_DIRECTION_WAIT = 0.12


class LocateAvatarFallbackConfig:
    """Fallback detection by clicking each deployment slot and OCR-ing the detail page name."""

    ENABLED: bool = True
    DETAIL_WAIT_TIME: float = 0.6
    CLOSE_WAIT_TIME: float = 0.3
    # MAA pipeline node ROI for operator name on the detail page (1280x720 absolute pixels).
    OCR_OPER_NAME_ROI: tuple[int, int, int, int] = (3, 178, 192, 35)
    # Offset from BattleOpersFlag rect to the operator avatar crop region.
    AVATAR_ROI_OFFSET: tuple[int, int, int, int] = (-39, 35, 53, 54)
