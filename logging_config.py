import logging
import logging.config
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
from typing import Optional

def configure_logging(app_name: str = "app",
                      level: str = "INFO",
                      log_dir: Optional[str] = None,
                      max_bytes: int = 5 * 1024 * 1024,
                      backup_count: int = 5) -> None:
    """
    Configure global logging for the project.

    - Uses env var `LOG_DIR` or `log_dir` argument, default `./logs`.
    - Creates the directory if missing when possible.
    - Adds a RotatingFileHandler and Console handler when file logging is available.
    - Falls back to console-only logging if file handler cannot be created.
    - Call this once at application startup.
    """
    # Resolve log directory
    env_dir = os.getenv("LOG_DIR")
    resolved_path = Path(env_dir or log_dir or Path.cwd() / "logs")

    logfile = None
    try:
        # Try to create the directory (may raise on permission denied)
        resolved_path.mkdir(parents=True, exist_ok=True)
        logfile = resolved_path / f"{app_name}.log"
        # Try to open the file for append to verify writability
        with open(logfile, "a", encoding="utf-8"):
            pass
    except Exception as e:
        # If we cannot create the directory or open the file, disable file logging
        logfile = None
        # Use basicConfig temporarily so we can log the problem to console
        logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        logging.getLogger(__name__).exception("Failed to prepare log file %s, falling back to console logging: %s", resolved_path, e)

    handlers = {
        "console": {
            "class": "logging.StreamHandler",
            "level": level,
            "stream": "ext://sys.stdout",
            "formatter": "standard",
        }
    }
    root_handlers = ["console"]

    if logfile is not None:
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "level": level,
            "formatter": "detailed",
            "filename": str(logfile),
            "mode": "a",
            "maxBytes": max_bytes,
            "backupCount": backup_count,
            "encoding": "utf-8",
        }
        root_handlers.append("file")

    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {"format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"},
            "detailed": {"format": "%(asctime)s - %(name)s - %(levelname)s - %(pathname)s:%(lineno)d - %(message)s"},
        },
        "handlers": handlers,
        "root": {"level": level, "handlers": root_handlers},
    }

    try:
        logging.config.dictConfig(LOGGING)
        logging.getLogger(__name__).info("Logging configured. Log file: %s", logfile)
    except Exception as e:
        # Fallback to console-only logging if dictConfig fails for any reason
        logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        logging.getLogger(__name__).exception("Failed to configure logging via dictConfig, falling back to basicConfig: %s", e)
