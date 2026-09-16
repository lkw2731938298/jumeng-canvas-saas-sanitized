"""按类别 + 东八区日期写入文本日志文件（append，重部署不删历史）。

唯一对外入口：write_file_log(category, content, tag=..., extra=...)
示例：write_file_log("payment", params, tag="SUBMIT") → logs/payment/20260710.log
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from ...core.config import get_settings
from ...core.datetime_util import now_cst_aware, now_cst_naive

_std_logger = logging.getLogger(__name__)
_write_lock = threading.Lock()
_CATEGORY_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _api_root() -> Path:
    """packages/api 根目录。"""
    return Path(__file__).resolve().parents[3]


def file_log_base_dir() -> Path:
    """日志根目录（默认 logs/，可 FILE_LOG_BASE_DIR 覆盖）。"""
    raw = (get_settings().file_log_base_dir or "logs").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = _api_root() / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def file_log_path(category: str, *, day: datetime | None = None) -> Path:
    """当日日志路径：logs/{category}/YYYYMMDD.log。"""
    name = (category or "").strip()
    if not name or not _CATEGORY_RE.fullmatch(name):
        raise ValueError(f"invalid log category: {category!r}")
    dt = day or now_cst_naive()
    dir_path = file_log_base_dir() / name
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path / (dt.strftime("%Y%m%d") + ".log")


def _ts_label() -> str:
    aware = now_cst_aware()
    return aware.strftime("%Y-%m-%d %H:%M:%S %z").replace("+0800", "+08:00")


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, (dict, list, tuple)):
        return json.dumps(content, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return str(content)


def write_file_log(
    category: str,
    content: Any,
    *,
    tag: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """追加一行文本日志到 logs/{category}/YYYYMMDD.log。

    :param category: 日志子目录名，如 payment、sms
    :param content: 正文；str 原样，dict/list 自动 JSON
    :param tag: 可选事件标签，如 SUBMIT、NOTIFY
    :param extra: 可选键值 metadata（拼在行内）
    """
    try:
        parts: list[str] = [_ts_label()]
        if tag:
            parts.append(tag)
        if extra:
            for key in sorted(extra.keys()):
                val = extra[key]
                if val is None:
                    continue
                if isinstance(val, (dict, list, tuple)):
                    val = _content_to_text(val)
                parts.append(f"{key}={val}")
        parts.append(_content_to_text(content))
        line = " | ".join(parts)

        path = file_log_path(category)
        with _write_lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line.rstrip("\n") + "\n")
                fh.flush()
    except Exception as exc:
        _std_logger.warning("write_file_log failed category=%s: %s", category, exc)
