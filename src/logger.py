import logging
import sys
from enum import Enum
from logging.handlers import RotatingFileHandler
from pathlib import Path

class LogLevelColor(Enum):
    DEBUG = '\033[94m'  # Blue
    INFO = '\033[0m'   # White
    WARNING = '\033[93m' # Yellow
    ERROR = '\033[91m'  # Red
    CRITICAL = '\033[95m' # Purple
    RESET = '\033[0m'    # Reset color

class ColoredFormatter(logging.Formatter):
    def format(self, record):
        levelname = record.levelname
        message = logging.Formatter.format(self, record)
        color = LogLevelColor[levelname].value if levelname in LogLevelColor.__members__ else LogLevelColor.RESET.value
        return f'{color}{message}{LogLevelColor.RESET.value}'

logger = logging.getLogger("DebugLogger")
logger.setLevel(logging.DEBUG)
logger.propagate = False

formatter = logging.Formatter('%(asctime)s - %(levelname)-7s - %(message)s')

if not logger.handlers:
    if sys.stderr is not None:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(
            ColoredFormatter('%(asctime)s - %(levelname)-7s - %(message)s')
        )
        logger.addHandler(console_handler)

    log_root = (
        Path(sys.executable).parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent.parent
    )
    try:
        log_dir = log_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "arkloop.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        pass

if __name__ == "__main__":
    logger.debug("Debug message")
    logger.info("Info message")
    logger.warning("Warning message")
    logger.error("Error message")
    logger.critical("Critical message")
