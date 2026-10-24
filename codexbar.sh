#!/usr/bin/env bash
# codexbar: run Codex inside tmux with statuslinedex as the status bar.
# Codex has no status line command hook, so tmux draws it instead.
# Usage: codexbar [any codex args]
set -euo pipefail

status_command='#(statuslinedex codex --tmux)'
codex_bin="$(command -v codex)"

if [[ -n "${TMUX:-}" ]]; then
  tmux set-option status on
  tmux set-option status-interval 10
  tmux set-option status-style 'bg=default,fg=brightwhite'
  tmux set-option 'status-format[0]' "$status_command"
  # hide the native footer to avoid two bars
  exec "$codex_bin" -c 'tui.status_line=[]' "$@"
fi

printf -v codex_command '%q ' "$codex_bin" -c 'tui.status_line=[]' "$@"
exec tmux new-session -s "codexbar-$$" "$codex_command" \; \
  set-option status on \; \
  set-option status-interval 10 \; \
  set-option status-style 'bg=default,fg=brightwhite' \; \
  set-option 'status-format[0]' "$status_command"
