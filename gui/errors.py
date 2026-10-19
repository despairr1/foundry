"""Errors in the window are never swallowed: every Tk callback exception is written to <foundry>/.foundry/gui-errors.log
with its traceback, and the window shows a one-line banner with the last line of it."""
import os
import traceback
from datetime import datetime

LOG_NAME = os.path.join(".foundry", "gui-errors.log")
MAX_LOG_BYTES = 512 * 1024


def log_path(root):
    return os.path.join(root, LOG_NAME)


def install(root_window, foundry_root, on_error=None):
    """Routes Tk callback exceptions to the log and to on_error(one-line summary)."""
    path = log_path(foundry_root)

    def report(exc, value, tb):
        text = "".join(traceback.format_exception(exc, value, tb))
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if os.path.exists(path) and os.path.getsize(path) > MAX_LOG_BYTES:
                os.replace(path, path + ".old")
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.now().isoformat(timespec='seconds')}]\n{text}")
        except OSError:
            pass
        try:
            import sys
            sys.stderr.write(text)
        except Exception:
            pass
        if on_error:
            last = text.strip().splitlines()[-1] if text.strip() else str(value)
            try:
                on_error(last[:220])
            except Exception:
                pass

    root_window.report_callback_exception = report


def recent_errors(foundry_root, max_entries=5):
    """The last few logged errors' final lines (for the Diagnostics card)."""
    try:
        with open(log_path(foundry_root), "r", encoding="utf-8", errors="replace") as f:
            text = f.read()[-60000:]
    except OSError:
        return []
    entries = [e.strip() for e in text.split("\n[") if e.strip()]
    out = []
    for entry in entries[-max_entries:]:
        lines = entry.splitlines()
        when = lines[0].rstrip("]") if lines else ""
        last = next((l for l in reversed(lines) if l.strip() and not l.startswith(" ")), "")
        out.append((when, last[:200]))
    return out
