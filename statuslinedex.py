#!/usr/bin/env python3
# statuslinedex: one status line for several coding agent CLIs.
# Shows model, context used and plan quota windows with time to reset.
#
# Each CLI is independent, none needs another one installed:
#   statuslinedex claude          Claude Code: JSON on stdin, its own rate_limits
#   statuslinedex mcode           MiniMax Code: JSON on stdin, MiniMax token plan
#   statuslinedex codex           Codex CLI: ~/.codex/sessions (no command hook)
#   statuslinedex codex --tmux    same, with tmux style codes
#   statuslinedex                 guess claude or mcode from the stdin payload
#
# Checked 23-Oct-2026: Claude Code 2.1.310, MiniMax Code 0.6.13, Codex CLI 0.164.0.

import argparse
import json
import os
import re
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

__version__ = "0.1.0"

CACHE_TTL = 90
HTTP_TIMEOUT = 3.0
CODEX_TAIL_BYTES = 512 * 1024
CODEX_MAX_FILES = 12
MINIMAX_QUOTA_URL = "https://api.minimax.io/v1/token_plan/remains"

# Tag, ANSI color and tmux color per CLI
TAGS = {}
TAGS["claude"] = ("", "", "")
TAGS["mcode"] = ("MMX", "35", "fg=colour5")
TAGS["codex"] = ("CDX", "34", "fg=colour4")

# Labels vendors add to model names
MODEL_NOISE = [" (1M context)", "(1M context)", "[1m]", "[1M]", " (Free)", "(Free)"]


class Style:
    # ANSI, tmux or no color
    def __init__(self, tmux=False):
        self.tmux = tmux
        self.plain = bool(os.getenv("NO_COLOR"))

    def code(self, ansi, tmux_name):
        if self.plain:
            return ""
        if self.tmux:
            return f"#[{tmux_name}]"
        return f"\033[{ansi}m"

    def reset(self):
        return self.code("0", "default")

    def paint(self, text, ansi, tmux_name):
        if not text:
            return ""
        return f"{self.code(ansi, tmux_name)}{text}{self.reset()}"

    def pct(self, value):
        if value >= 85:
            return self.paint(f"{value}%", "31", "fg=red")
        if value >= 60:
            return self.paint(f"{value}%", "33", "fg=yellow")
        return self.paint(f"{value}%", "32", "fg=green")


# Window length in minutes -> 5h / 7d / 30d
def window_label(minutes):
    minutes = int(round(minutes))
    if minutes >= 1440 and minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes >= 60 and minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


# Seconds -> 6d23h / 4h9m / 12m
def time_left(seconds):
    seconds = max(0, int(seconds))
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{minutes}m"
    return f"{minutes}m"


# One quota window, normalized
def make_window(minutes, used_pct, reset_epoch):
    win = {}
    win["minutes"] = minutes
    win["used"] = int(round(float(used_pct)))
    win["reset"] = reset_epoch
    return win


# Dotted lookup in nested dicts, None if absent
def dig(data, path):
    cur = data
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


# Strip vendor labels from model names
def clean_model(name):
    name = str(name or "")
    for noise in MODEL_NOISE:
        name = name.replace(noise, "")
    return name.strip()


# GET json with a small file cache
def cached_get(name, url, headers):
    cache = Path(tempfile.gettempdir()) / f"statuslinedex_{name}.json"
    now = time.time()
    stale = None
    try:
        saved = json.loads(cache.read_text())
        if now - float(saved["ts"]) < CACHE_TTL:
            return saved["data"]
        stale = saved["data"]
    except (OSError, ValueError, KeyError):
        pass
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception:
        # keep the line alive with the last value
        return stale
    try:
        cache.write_text(json.dumps({"ts": now, "data": data}))
    except OSError:
        pass
    return data


# Claude Code rate_limits from its own stdin
def claude_windows(data):
    windows = []
    spans = [("five_hour", 300), ("seven_day", 10080)]
    for key, minutes in spans:
        used = dig(data, f"rate_limits.{key}.used_percentage")
        if used is None:
            continue
        windows.append(make_window(minutes, used, dig(data, f"rate_limits.{key}.resets_at")))
    return windows


# MiniMax key: env or mcode's own config
def mcode_key():
    if os.getenv("MINIMAX_API_KEY"):
        return os.getenv("MINIMAX_API_KEY")
    config = Path.home() / ".minimax" / "config.yaml"
    try:
        text = config.read_text()
    except OSError:
        return ""
    match = re.search(r"^minimax_api:\s*\n\s+apiKey:\s*(\S+)", text, re.M)
    if not match:
        return ""
    return match.group(1).strip("'\"")


# MiniMax token plan windows (5h and weekly)
def mcode_windows():
    key = mcode_key()
    if not key:
        return []
    data = cached_get("mcode", MINIMAX_QUOTA_URL, {"Authorization": "Bearer " + key})
    if not isinstance(data, dict):
        return []
    general = None
    for bucket in data.get("model_remains") or []:
        if bucket.get("model_name") == "general":
            general = bucket
    if general is None:
        return []
    now = time.time()
    windows = []
    pairs = [("start_time", "end_time", "remains_time", "current_interval_remaining_percent"),
             ("weekly_start_time", "weekly_end_time", "weekly_remains_time",
              "current_weekly_remaining_percent")]
    for start, end, remains, remaining_pct in pairs:
        if remaining_pct not in general or end not in general:
            continue
        minutes = (general[end] - general[start]) / 60000
        reset = now + general[remains] / 1000
        windows.append(make_window(minutes, 100 - float(general[remaining_pct]), reset))
    return windows


# Model fallback for mcode startup event
def mcode_default_model():
    config = Path.home() / ".minimax" / "config.yaml"
    try:
        match = re.search(r"^defaultModel:\s*(\S+)", config.read_text(), re.M)
    except OSError:
        return ""
    if not match:
        return ""
    return match.group(1).split("/")[-1].replace("MiniMax-", "")


# Which stdin CLI sent this payload
def guess_cli(data):
    if "protocol" in data and "tui_version" in data:
        return "mcode"
    return "claude"


# Status from Claude Code or mcode stdin
def status_from_stdin(data, cli):
    status = {}
    status["provider"] = cli
    model = data.get("model")
    if isinstance(model, dict):
        model = model.get("display_name") or model.get("id")
    if not model and cli == "mcode":
        # first run happens before the model loads
        model = mcode_default_model()
    status["model"] = clean_model(model) or "?"
    status["effort"] = dig(data, "effort.level")
    ctx = dig(data, "context_window.used_percentage")
    status["ctx"] = None if ctx is None else int(round(float(ctx)))
    if cli == "claude":
        status["windows"] = claude_windows(data)
    else:
        status["windows"] = mcode_windows()
    return status


# Model from config.toml when the session tail lacks it
def codex_config_model(home):
    try:
        match = re.search(r'^model\s*=\s*"([^"]+)"', (home / "config.toml").read_text(), re.M)
    except OSError:
        return ""
    return match.group(1) if match else ""


# Last token_count and model from Codex sessions
def status_from_codex():
    status = {}
    status["provider"] = "codex"
    status["model"] = "Codex"
    status["effort"] = None
    status["ctx"] = None
    status["windows"] = []
    home = Path(os.getenv("CODEX_HOME") or Path.home() / ".codex")
    files = sorted((home / "sessions").rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[:CODEX_MAX_FILES]:
        model = None
        info = None
        limits = None
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - CODEX_TAIL_BYTES))
            lines = handle.read().decode("utf-8", "ignore").splitlines()
        for line in reversed(lines):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            payload = event.get("payload") or {}
            if model is None and event.get("type") == "turn_context":
                model = payload.get("model")
            if payload.get("type") == "token_count":
                info = info or payload.get("info")
                limits = limits or payload.get("rate_limits")
            if model and info and limits:
                break
        if not (info or limits):
            continue
        status["model"] = clean_model(model or codex_config_model(home)) or "Codex"
        if info and info.get("model_context_window"):
            used = (info.get("last_token_usage") or {}).get("total_tokens") or 0
            status["ctx"] = int(round(used * 100 / info["model_context_window"]))
        for key in ["primary", "secondary"]:
            limit = (limits or {}).get(key)
            if not limit:
                continue
            status["windows"].append(make_window(limit.get("window_minutes") or 0,
                                                 limit.get("used_percent") or 0,
                                                 limit.get("resets_at")))
        break
    return status


# Pace margin: expected even use minus real use
def pace_margin(win):
    if not win["reset"] or win["minutes"] <= 0:
        return None
    total = win["minutes"] * 60
    left = max(0.0, float(win["reset"]) - time.time())
    expected = (total - left) * 100 / total
    return expected - win["used"]


# Final text line
def render(status, style):
    sep = " | "
    parts = []
    tag, color, tmux_color = TAGS[status["provider"]]
    if tag:
        parts.append(style.paint(tag, color, tmux_color))
    model = status["model"]
    if status["effort"]:
        model = f"{model} * {status['effort']}"
    parts.append(style.paint(model, "1", "bold"))
    parts.append("ctx:-" if status["ctx"] is None else f"ctx:{style.pct(status['ctx'])}")
    longest = None
    for win in status["windows"]:
        part = f"{window_label(win['minutes'])}:{style.pct(win['used'])}"
        if win["reset"]:
            part += f" [{time_left(float(win['reset']) - time.time())}]"
        parts.append(part)
        if longest is None or win["minutes"] > longest["minutes"]:
            longest = win
    if longest is not None and longest["minutes"] >= 1440:
        margin = pace_margin(longest)
        if margin is not None:
            text = f"{margin:+.1f}%"
            colored = style.paint(text, "32", "fg=green") if margin >= 0 else style.paint(text, "31", "fg=red")
            parts.append(f"pace[{colored}]")
    return sep.join(parts)


# Command line flags
def parse_args(argv):
    parser = argparse.ArgumentParser(prog="statuslinedex",
                                     description="One status line for Claude Code, MiniMax Code (mcode) and Codex.")
    parser.add_argument("cli", nargs="?", choices=["claude", "mcode", "codex"],
                        help="which CLI is calling (default: guess claude or mcode from stdin)")
    parser.add_argument("--tmux", action="store_true", help="tmux style codes instead of ANSI")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    style = Style(tmux=args.tmux)
    if args.cli == "codex":
        status = status_from_codex()
    else:
        raw = sys.stdin.read()
        try:
            data = json.loads(raw) if raw.strip() else {}
        except ValueError:
            data = {}
        status = status_from_stdin(data, args.cli or guess_cli(data))
    print(render(status, style))
    return 0


if __name__ == "__main__":
    sys.exit(main())
