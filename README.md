# statuslinedex

One status line for the coding agent CLIs you actually switch between:
**Claude Code**, **MiniMax Code (mcode)** and **Codex CLI**.

Same layout everywhere: provider, model, context used, every plan quota window
with its time to reset, and a pace margin that tells you if you are burning the
long window faster than an even split.

- Each CLI stands on its own: Claude Code shows its own plan, mcode its MiniMax
  token plan, Codex its own limits. None needs another CLI installed.
- One Python file, standard library only, Python 3.8+.
- Window labels come from the real window length (`5h`, `7d`, `30d`...), not
  hardcoded, so plan changes show up correctly.
- Quota calls are cached for 90 s, so the line refreshes instantly.
- `NO_COLOR=1` for plain text, `--tmux` for tmux status bars.

## How it looks

Captured from real runs on 23-Oct-2026 with `NO_COLOR=1`. In a terminal the
percentages are green under 60 %, yellow from 60 % and red from 85 %; the pace
margin is green when positive, red when negative.

**Claude Code** (Anthropic plan, sample stdin payload)

```text
Opus 5.5 * high | ctx:42% | 5h:23% [2h29m] | 7d:61% [1d23h] | pace[+10.4%]
```

**MiniMax Code / mcode** (live token plan)

```text
MMX | M3.1-Flash | ctx:- | 5h:13% [3h18m] | 7d:2% [6d22h] | pace[-1.0%]
```

**Codex CLI** (live session, free plan with a single 30-day window)

```text
CDX | gpt-6-luna | ctx:67% | 30d:6% [29d20h] | pace[-5.5%]
```

How to read it:

```
  piece           | meaning
  ----------------|-----------------------------------------------------------
  MMX / CDX       | mcode / Codex (nothing for Claude Code)
  Opus 5.5 * high | model and reasoning effort, when the CLI sends it
  ctx:42%         | context window used ("-" when the CLI does not send it)
  5h:23% [2h29m]  | quota used in that window and time until it resets
  pace[+10.4%]    | longest window: even-split usage minus real usage;
                  | positive = you have room, negative = ahead of pace
```

## Install

```bash
pipx install git+https://github.com/<owner>/statuslinedex
# or copy statuslinedex.py anywhere and call it with python3
```

## Setup per CLI

**Claude Code**, in `~/.claude/settings.json`:

```json
{
  "statusLine": { "type": "command", "command": "statuslinedex claude" }
}
```

**MiniMax Code (mcode)**, in `~/.minimax/config.yaml`:

```yaml
tui:
  statusLine:
  - custom-command
  customStatusLine:
    command: statuslinedex mcode
    display: inline
    colorMode: ansi
    timeoutMs: 5000
    intervalSeconds: 30
```

`intervalSeconds` matters: mcode runs the command once at startup, before the
model is loaded, and otherwise only on session events. statuslinedex falls back
to `defaultModel` from that same file for the first run.

**Codex CLI** has no command hook for its status line (`tui.status_line` only
takes built-in items), so the line is drawn by tmux. Use the bundled wrapper:

```bash
cp codexbar.sh ~/.local/bin/codexbar && chmod +x ~/.local/bin/codexbar
codexbar            # same arguments as codex
```

It reads the newest sessions in `~/.codex/sessions` (or `$CODEX_HOME`),
refreshes every 10 s and hides Codex's native footer.

## Where the numbers come from

```
  provider        | model / context              | quota
  ----------------|------------------------------|--------------------------------------------
  Claude Code     | stdin model, context_window  | stdin rate_limits.five_hour / seven_day
  mcode           | stdin model, defaultModel    | GET https://api.minimax.io/v1/token_plan/remains
  Codex           | turn_context / config.toml   | token_count.rate_limits in session files
```

For mcode the MiniMax key is read, never stored: `MINIMAX_API_KEY` if set,
otherwise the key mcode itself saved (`minimax_api.apiKey` in
`~/.minimax/config.yaml`), so a working mcode login is all it needs.

The cache file (`<tmp>/statuslinedex_mcode.json`) holds only the quota
response, never the key. If the API call fails the last cached value is shown.

## Options

```bash
statuslinedex claude          # Claude Code (stdin)
statuslinedex mcode           # MiniMax Code (stdin)
statuslinedex codex           # Codex session files
statuslinedex codex --tmux    # tmux style codes
statuslinedex                 # guess claude or mcode from stdin
```

## Tested with

Claude Code 2.1.310, MiniMax Code 0.6.13, Codex CLI 0.164.0, Linux. Field names
of each CLI change between versions; issues with a sample stdin payload are
welcome.

## License

MIT
