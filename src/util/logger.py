"""JSON logger with tslog-compatible format."""
import datetime
import json
import logging
import os
import sys
from pathlib import Path
from typing import Protocol

MAIN_LOG_DIR = os.path.join(os.path.expanduser("~"), ".wechat-acp", "tmp")
SUBSYSTEM = "gateway/channels/wechat-acp"
RUNTIME = "python"
RUNTIME_VERSION = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
HOSTNAME = os.getenv("HOSTNAME") or os.getenv("COMPUTERNAME") or "unknown"
PARENT_NAMES = ["wechat-acp"]

LEVEL_IDS = {
    "TRACE": 1,
    "DEBUG": 2,
    "INFO": 3,
    "WARN": 4,
    "ERROR": 5,
    "FATAL": 6,
}

DEFAULT_LOG_LEVEL = "INFO"


class Logger(Protocol):
    """Logger protocol matching the TypeScript interface."""

    def info(self, message: str) -> None: ...
    def debug(self, message: str) -> None: ...
    def warn(self, message: str) -> None: ...
    def error(self, message: str) -> None: ...
    def withAccount(self, accountId: str) -> "Logger": ...
    def getLogFilePath(self) -> str: ...
    def close(self) -> None: ...


def resolve_min_level() -> int:
    env = os.getenv("WECHATACP_LOG_LEVEL", "").upper()
    if env and env in LEVEL_IDS:
        return LEVEL_IDS[env]
    return LEVEL_IDS[DEFAULT_LOG_LEVEL]


_min_level_id = resolve_min_level()


def to_local_iso(now: datetime.datetime) -> str:
    """Shift a Date into local time so toISOString() renders local clock digits."""
    offset_ms = -now.utcoffset().total_seconds() * 60 if now.utcoffset() else 0
    sign = "+" if offset_ms >= 0 else "-"
    abs_offset = abs(int(offset_ms))
    off_str = f"{sign}{abs_offset // 60:02d}:{abs_offset % 60:02d}"
    local = now.timestamp() - (now.utcoffset().total_seconds() if now.utcoffset() else 0)
    return datetime.datetime.fromtimestamp(local).isoformat().replace("Z", off_str)


def local_date_key(now: datetime.datetime) -> str:
    return to_local_iso(now)[:10]


def resolve_main_log_path() -> str:
    date_key = local_date_key(datetime.datetime.now())
    return os.path.join(MAIN_LOG_DIR, f"wechat-acp-{date_key}.log")


_log_dir_ensured = False


def _ensure_log_dir() -> None:
    global _log_dir_ensured
    if not _log_dir_ensured:
        os.makedirs(MAIN_LOG_DIR, exist_ok=True)
        _log_dir_ensured = True


def build_logger_name(accountId: str | None = None) -> str:
    return f"{SUBSYSTEM}/{accountId}" if accountId else SUBSYSTEM


def write_log(level: str, message: str, accountId: str | None = None) -> None:
    """Write a tslog-compatible JSON log line."""
    level_id = LEVEL_IDS.get(level, LEVEL_IDS["INFO"])
    if level_id < _min_level_id:
        return

    now = datetime.datetime.now()
    logger_name = build_logger_name(accountId)
    prefixed_message = f"[{accountId}] {message}" if accountId else message

    entry = json.dumps(
        {
            "0": logger_name,
            "1": prefixed_message,
            "_meta": {
                "runtime": RUNTIME,
                "runtimeVersion": RUNTIME_VERSION,
                "hostname": HOSTNAME,
                "name": logger_name,
                "parentNames": PARENT_NAMES,
                "date": now.isoformat(),
                "logLevelId": level_id,
                "logLevelName": level,
            },
            "time": to_local_iso(now),
        },
        ensure_ascii=False,
    )

    try:
        _ensure_log_dir()
        log_path = resolve_main_log_path()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass  # Best-effort

    # Console output (readable format)
    if level_id >= _min_level_id:
        account_str = f"[{accountId}] " if accountId else ""
        console_msg = f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {account_str}{message}"
        if level in ("ERROR", "FATAL"):
            print(console_msg, file=sys.stderr)
        else:
            print(console_msg, file=sys.stdout)


def create_logger(accountId: str | None = None) -> Logger:
    """Creates a logger instance, optionally bound to a specific account."""
    return _LoggerImpl(accountId)


class _LoggerImpl:
    __slots__ = ("_account_id",)

    def __init__(self, accountId: str | None) -> None:
        self._account_id = accountId

    def info(self, message: str) -> None:
        write_log("INFO", message, self._account_id)

    def debug(self, message: str) -> None:
        write_log("DEBUG", message, self._account_id)

    def warn(self, message: str) -> None:
        write_log("WARN", message, self._account_id)

    def error(self, message: str) -> None:
        write_log("ERROR", message, self._account_id)

    def withAccount(self, accountId: str) -> Logger:
        return create_logger(accountId)

    def getLogFilePath(self) -> str:
        return resolve_main_log_path()

    def close(self) -> None:
        pass


# Module-level singleton
logger: Logger = create_logger()
