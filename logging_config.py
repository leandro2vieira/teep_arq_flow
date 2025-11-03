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
    - Creates the directory if missing.
    - Adds a RotatingFileHandler and Console handler.
    - Call this once at application startup.
    """
    # Resolve log directory
    env_dir = os.getenv("LOG_DIR")
    log_path = Path(env_dir or log_dir or Path.cwd() / "logs")
    log_path.mkdir(parents=True, exist_ok=True)

    logfile = log_path / f"{app_name}.log"

    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            },
            "detailed": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(pathname)s:%(lineno)d - %(message)s"
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": level,
                "stream": "ext://sys.stdout",
                "formatter": "standard",
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": level,
                "formatter": "detailed",
                "filename": str(logfile),
                "mode": "a",
                "maxBytes": max_bytes,
                "backupCount": backup_count,
                "encoding": "utf-8",
            },
        },
        "root": {
            "level": level,
            "handlers": ["console", "file"]
        },
    }

    logging.config.dictConfig(LOGGING)
    logging.getLogger(__name__).info("Logging configured. Log file: %s", logfile)