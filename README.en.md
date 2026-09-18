# AI Usage Pulse

English · [한국어](README.md)

AI Usage Pulse reads local usage logs from Codex, Claude Code, Gemini CLI, and other AI coding tools, then generates SVG dashboards for your GitHub profile. It can merge device ledgers from multiple computers, uses only the Python 3.10+ standard library, and makes no external API calls while collecting usage.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./previews/dashboard-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="./previews/dashboard-light.png">
  <img alt="AI coding token usage" src="./previews/dashboard-light.png" width="900">
</picture>

## Card design

The dashboard shows lifetime tokens, a 30-day pulse chart, the change from the previous seven days, current streak, today's usage, token composition, per-tool usage, and a 26-week activity map. The values are snapshots generated from local logs, not a live feed.

SVG cards include subtle CSS animations for chart entry and status indicators. They respect reduced-motion preferences and remain fully readable in viewers that do not run animation.

## Layouts

Every run generates seven layouts in both light and dark themes: 14 SVG files in total. `dashboard` and `combo` provide the same complete dashboard under different filenames.

| Layout | Size | Filename (`THEME` is `dark` or `light`) |
| --- | --- | --- |
| dashboard | 900 × 626 or taller | `ai-usage-THEME.svg` |
| full | 846 × 225 | `ai-usage-full-THEME.svg` |
| combo | 900 × 626 or taller | `ai-usage-combo-THEME.svg` |
| compact | 846 × 195 | `ai-usage-compact-THEME.svg` |
| half | 423 × 195 | `ai-usage-half-THEME.svg` |
| grass | 423 × 195 | `ai-usage-grass-THEME.svg` |
| half-grass | 423 × 335 | `ai-usage-half-grass-THEME.svg` |

- `full` combines a large lifetime total, wide pulse chart, recent usage, and token composition.
- `combo` is the complete dashboard with the large total, 30-day pulse, metric tiles, token flow, and activity map. It is the default.
- `compact` places the lifetime total, pulse chart, and activity map in one row.
- `half` shows the headline total, sparkline, this week, today, and active days.
- `grass` combines active days, a 30-day pulse, and the 26-week activity map.
- `half-grass` is a vertical mini dashboard with metrics, chart, tiles, and activity map.

After generation, copy a layout block from `README-snippet.md` in the output directory, or use the installer described below.

## Supported data sources

The normalized JSON importer can accept data from any tool that exposes token counts. This does not mean every AI product has a built-in automatic connector.

| Tool | Collection source | Status |
| --- | --- | --- |
| Codex CLI / desktop | `$CODEX_HOME/sessions`, `archived_sessions` JSONL | Tested with local logs |
| Claude Code | `$CLAUDE_CONFIG_DIR/projects` JSONL | Tested with local logs |
| Gemini CLI | `~/.gemini/tmp/*/chats/session-*.json`, `.jsonl` | Official schema, synthetic fixture tested |
| Cursor, GitHub Copilot, Windsurf, OpenCode, Cline, Roo Code, Aider, etc. | Convert exports or API responses to the normalized JSON format | Generic importer available |
| ChatGPT, Claude, Gemini web, and other products | Import when an export or API includes token counts | Exact analysis is impossible when only conversation text is available |

Request counts, credits, and quota percentages are not converted into tokens. If the same request appears in both an API log and a client log, choose one source to avoid double counting.

## Run

```sh
python3 src/usage_card.py --device macbook-home --title "My AI Coding Usage"
```

`--device` is a permanent unique ID for one computer, such as `macbook-home`, `macbook-work`, or `desktop`. If omitted, the script uses `USAGE_CARD_DEVICE`, then falls back to the current hostname.

To collect a single tool, use a separate device-ledger directory:

```sh
python3 src/usage_card.py --tools codex --device macbook --state-dir .usage-state/codex-devices
python3 src/usage_card.py --tools gemini --device macbook --state-dir .usage-state/gemini-devices
```

Override log locations with `--codex-home`, `--claude-home`, and `--gemini-home`. The default timezone is `Asia/Seoul`; change it with `--timezone`. Do not mix different timezone or `--tools` settings in the same ledger directory. A legacy `.usage-state/ledger.json` is copied into the current device ledger on the first default run.

Generate sample cards without touching the real ledger:

```sh
python3 src/usage_card.py --demo --output /tmp/ai-usage-demo
```

## Import usage from other tools

Convert an export or API response to the format shown in [examples/import-usage.json](examples/import-usage.json). The example values are fictional.

```sh
python3 src/usage_card.py \
  --import-json /path/to/cursor-normalized.json \
  --import-json /path/to/copilot-normalized.json

# Imported data only
python3 src/usage_card.py \
  --tools imports \
  --device macbook \
  --import-json /path/to/usage.json \
  --state-dir .usage-state/import-devices
```

Every record needs nonempty `source`, `id`, `tool`, `model`, and timezone-aware `timestamp` values, plus four non-overlapping nonnegative integer fields:

| Field | Meaning |
| --- | --- |
| `input` | Input tokens excluding cache reads and writes |
| `output` | Output tokens; include reasoning when the product reports it separately |
| `cache_read` | Input tokens read from cache |
| `cache_write` | Input tokens written to cache |

`source + tool + id` is the stable identity of one request. Re-importing the same record does not add it twice. Use different `source` values for separate accounts or computers. Importing the same request under another source, or importing a request already found by automatic local collection, can count it twice.

## Install on a GitHub profile

1. Clone the repository.

```sh
git clone https://github.com/92pino/ai-usage-pulse.git
cd ai-usage-pulse
```

2. Sign in to GitHub.

```sh
gh auth login
```

3. Choose a name for this computer and run the initial setup.

```sh
./setup.sh macbook-home
```

The device name can be anything unique, such as `macbook-home`, `work-macbook`, or `desktop`. Setup automatically installs the card in your GitHub profile README.

4. Refresh your usage whenever you want.

```sh
./update.sh
```

`update.sh` collects logs, merges multiple computers, generates the SVGs, commits, and pushes automatically.

## Multiple computers, one card

Clone this repository on each computer and use a different permanent device name:

```sh
# First computer
./setup.sh macbook-home

# Second computer
./setup.sh work-desktop
```

After that, run the same command on either computer:

```sh
./update.sh
```

On first setup, the script creates a private `GITHUB_USER/ai-usage-pulse-data` repository. Device ledgers are merged there. Only the selected light and dark SVG files and the managed README block are pushed to the public profile repository. Prompts, source code, request identifiers, and model-level ledgers remain private.

Use a unique device name on every computer and stagger scheduled runs by a few minutes to reduce push conflicts.

## Aggregation rules and limitations

- The summary includes lifetime tokens, last 30 days, active days, input/output/cache composition, a 26-week activity map, and per-tool totals. The JSON output also contains daily and model-level analysis.
- Codex uses cumulative counters, so the collector calculates increments. Cached input and reasoning output already included in another field are not added twice.
- Claude Code streaming updates are merged by message ID. Gemini messages are merged by message ID, and separately reported thought tokens are included in output.
- Device ledgers are stored at `.usage-state/devices/<device>.json` by default. Matching request hashes are counted once when ledgers are merged. Deleting a device ledger loses its retained history.
- Logs deleted before the first collection cannot be recovered. Partially truncated Codex logs and forked sessions that copy prior counters may limit accuracy. This is an analysis of readable local logs, not a billing statement.
- Subscription or API costs are not estimated. Token totals include cache traffic and do not map directly to cost.
- Generated cards never contain prompts, responses, source code, or project paths. They do contain dates, model names, and tool names.
- Malformed JSONL lines are reported and skipped. Unreadable files make the run fail without saving a replacement ledger.

## Verify

```sh
python3 -m unittest discover -s tests -v
```

The tests cover duplicate events, copied logs, cache and reasoning accounting, ledger retention, timezone conversion, Gemini JSON/JSONL, imported-data validation, multi-device merging, README installation, and SVG validity.

## References

The Gemini parser follows the official [chat recording service](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/chatRecordingService.ts). GitHub Copilot data must be converted from the available [official usage metrics](https://docs.github.com/en/copilot/reference/copilot-usage-metrics/copilot-usage-metrics) according to the account's access level.
