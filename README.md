# notes-to-keep

Bulk migration of Apple Notes / iCloud Notes to Google Keep.

The two vendors don't provide a usable path — Apple exports one note at a time,
and Keep has no import at all — so this project does the missing parts: a
repeatable export pipeline, a resumable importer, and a scheduler that survives
Google's undocumented rate limits.

Project home: <https://github.com/NaphatrB/notes-to-keep>

## What it does

- Reads an Apple Notes export (JSON from Apple Notes Exporter, or the `.txt`
  export from Apple's Data & Privacy portal).
- Creates notes in Google Keep through `gkeepapi` (the private mobile API).
- Keeps a ledger so runs are resumable and never duplicate.
- Works around the awkward parts: truncated titles, duplicated title lines,
  folder → label mapping, dates, oversized notes, attachments, and the Keep
  label/character limits.
- Schedules itself in small bursts when the account is rate limited, ramping up
  automatically after idle periods.

## Repository layout

```
research/apple-notes-to-google-keep.md   research, options, runbook, sources
scripts/import_notes_to_keep.py          the importer
scripts/get-token.py                     oauth_token -> Keep master token
scripts/run-import.sh                    wrapper with the recommended flags
scripts/drip-guard.sh                    adaptive rate-limit scheduler
scripts/install-agent.sh                 installs the launchd agent (local paths)
```

Scripts use `~/` and `$HOME`; the agent is generated at install time, so the
repository contains no usernames or absolute home paths.

## Requirements

- macOS for the export step (Apple Notes Exporter runs there), Linux/Windows is
  fine for the importer once you have the files.
- Python 3.10+ (`gkeepapi` requires it).
- [Apple Notes Exporter](https://github.com/kzaremski/apple-notes-exporter)
  (external, GPL-3.0) for a bulk JSON/TXT export, or Apple's Data & Privacy
  portal.

## Quick start

### 1. Export

```sh
notes-export export --output ~/notes-export --format json --account iCloud
```

JSON is preferred: it carries the exact title, plain-text body, creation date
and folder. TXT works too; titles then come from filenames.

### 2. Install the Python environment

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

### 3. Get a Keep master token

```sh
python scripts/get-token.py
```

It asks for the Google account and the `oauth_token` cookie from
`accounts.google.com/EmbeddedSetup`, verifies the token against Keep, and stores
it in `~/.config/keep/token` (mode 600).

The token grants full access to the account. Read the security section.

### 4. Dry run, test, then import

```sh
python scripts/import_notes_to_keep.py ~/notes-export --dry-run --label-depth 1 --keep-dates
python scripts/import_notes_to_keep.py ~/notes-export --label-depth 1 --keep-dates --limit 20
python scripts/import_notes_to_keep.py ~/notes-export --label-depth 1 --keep-dates
```

### 5. Large libraries: drip mode

The private Keep API rate-limits writes. `run-import.sh` uses safe batching, and
`install-agent.sh` installs a launchd agent that runs it in short bursts while
the machine is on:

```sh
scripts/run-import.sh          # one manual run
scripts/install-agent.sh       # daily-budgeted, adaptive background runs
```

The guard estimates how much quota has refilled since the last success, asks for
that many notes (50–400), grows a cap on success, halves it and backs off 15
minutes on a zero run, and caps runs per day.

## Importer behaviour

- Inputs: `.json` (Apple Notes Exporter), `.txt`, `.md`, `.html`.
- **Truncated titles**: Apple stores the first ~70 characters plus `…`. The full
  first line is promoted to the Keep title, so the tail is not lost.
- **Duplicated first line**: removed only when it matches the title; no content
  is dropped.
- **Folders** become labels (`--label-depth 1` keeps only the top level, to stay
  under Keep's 50-label limit).
- **Dates**: `--keep-dates` prepends `Created: YYYY-MM-DD` from the JSON export.
- **Long notes** are split at Keep's 19,999-character limit, with `(1/2)` titles.
- **Attachments** cannot be uploaded through the API; their filenames are listed
  at the end of the note.
- **Ledger**: `.keep-import-state.json` records source path + fragment index, so
  flag changes do not cause duplicates.
- `--skip-empty` drops blank `2001-01-01` phantom records but keeps title-only
  notes.
- `--escape-hashtags` prevents `#tag` from becoming a Keep label.
- Timeouts on auth and sync abort stalled 429 backoff instead of hanging.

## Limits worth knowing

| Keep limit | Value |
|---|---|
| Characters per note | 19,999 (title 1,000) |
| Labels | 50, flat |
| Attachments via API | not supported |
| Import | none |

Observed private-API refill: about **100 notes per 5 minutes**, with a much
larger burst available on a long-idle account. The drip guard is built around
that.

## Security

- The master token is account-wide. Keep it off disk when you are done
  (`rm ~/.config/keep/token`), and change the Google password if it leaks.
- This repository never contains credentials, tokens, cookies, note content,
  usernames, or absolute home paths.
- `.gitignore` excludes tokens, state, logs and exports.
- `.githooks/pre-commit` blocks staged credentials, personal paths and state
  files. Enable it with `git config core.hooksPath .githooks`.

## Attribution

- **notes-to-keep** — project home and upstream:
  <https://github.com/NaphatrB/notes-to-keep>
- **gkeepapi** — unofficial Google Keep client (MIT):
  <https://github.com/kiwiz/gkeepapi>
- **gpsoauth** — Google Play Services OAuth / token exchange (MIT):
  <https://github.com/simon-weber/gpsoauth>
- **Apple Notes Exporter** — external export tool, not bundled (GPL-3.0):
  <https://github.com/kzaremski/apple-notes-exporter>
- Research sources are cited inline in
  `research/apple-notes-to-google-keep.md`.

## License

MIT — see [LICENSE](LICENSE).
