# Apple Notes to Google Keep migration (bulk, iCloud, macOS)

**Bulk-migrate Apple Notes / iCloud Notes to Google Keep** — with a resumable
Python importer, a real export pipeline, and a scheduler that survives Google's
undocumented rate limits. Neither vendor offers this: Apple exports one note at
a time, and **Google Keep has no import feature**.

<p>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-blue">
  <img alt="Platform macOS" src="https://img.shields.io/badge/platform-macOS-lightgrey">
  <img alt="Destination Google Keep" src="https://img.shields.io/badge/destination-Google%20Keep-yellow">
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-green">
</p>

> **TL;DR — let a coding agent drive it.**
> Point an agent at this checkout and paste the prompt in
> [Using an agent](#using-an-agent). It checks your export, sets up Python,
> walks you through the one-time Google token, runs a dry run and a 20-note
> test, then imports the rest within Google's rate limits.
>
> You only do three things: **export your notes**, **sign in to Google once**,
> and **approve the test batch**.

## How it works

```mermaid
flowchart TD
    A[Apple Notes / iCloud Notes] --> C[Apple Notes Exporter<br/>JSON (tested)]
    C --> E[(Export folder)]
    E --> F[import_notes_to_keep.py]
    F --> G[Fix titles, folder labels, dates<br/>split notes over 19,999 chars]
    G --> H[gkeepapi]
    H --> I[(Google Keep)]
    H -.->|HTTP 429| J[drip-guard.sh<br/>adaptive batches]
    J -.-> H
    F --> K[(.keep-import-state.json<br/>resume ledger)]
```

Tested path: macOS + Apple Notes Exporter JSON. Apple also documents an iCloud
Notes export at privacy.apple.com, but this project has not tested it (see
[Tested scope](#tested-scope)).

1. **Export** — Apple Notes Exporter on macOS (the tested path).
2. **Transform** — the importer fixes Apple's truncated titles, removes the
   duplicated title line, maps folders to labels, keeps dates, and splits
   oversized notes.
3. **Import** — `gkeepapi` creates the notes through the private Keep API.
4. **Adapt** — when Google returns 429, the drip guard backs off and resumes
   automatically, ramping the batch size up again after idle periods.

## Tested scope

What this repository was actually exercised against:

- macOS 12.7.6 (Intel), Apple Notes Exporter with a **JSON** export.
- One Google account, `gkeepapi` 0.17.1, Python 3.14.10.
- The adaptive drip guard against Google's HTTP 429 rate limit.

**Not tested here:** Apple's privacy.apple.com export, non-macOS systems, and
the TXT/Markdown/HTML readers against a real export (those readers were only
exercised with fixtures). Treat those paths as unverified.

## Contents

- [Tested scope](#tested-scope)
- [Using an agent](#using-an-agent)
- [Quick start](#quick-start)
- [Importer behaviour](#importer-behaviour)
- [Limits and rate limits](#limits-and-rate-limits)
- [FAQ](#faq)
- [Security](#security)
- [Attribution](#attribution)

## Using an agent

The migration is mostly mechanical but has sharp edges: a single-use token, a
resumable ledger, and a private API that rate-limits. If you want a coding
agent to drive it, paste this prompt into an agent with shell access to this
checkout. It works with any agent; it is written to ask before doing anything
destructive.

```text
Help me migrate my Apple Notes to Google Keep using this repository.

First read README.md and research/apple-notes-to-google-keep.md, then inspect
the scripts in scripts/. Follow them rather than inventing a new importer.
Work step by step and show me the output of each check.

1. Environment
   - Confirm macOS version, Python >= 3.10, and available disk space.
   - Ask me for the export directory (default ~/notes-export) and confirm the
     export has finished: no exporter process running and the file count
     stable. The tested path is Apple Notes Exporter JSON.
   - Check for invalid JSON, zero-byte files, and notes over 19,999 characters.

2. Setup
   - Create a virtualenv and install requirements.txt.
   - If the default python3 is older than 3.10, find a newer interpreter and
     tell me which one you used.

3. Token
   - Walk me through scripts/get-token.py and the EmbeddedSetup cookie step.
   - I will paste the oauth_token cookie and the master token myself. Never ask
     me to paste a master token into chat, never print it, never write it into
     this repository, and never commit it.
   - If the exchange fails with BadAuthentication, explain that the cookie is
     single-use and ask me for a fresh one; do not loop.

4. Import
   - Run the dry run and show me: fragments, labels, duplicates removed,
     pending count, and any skipped notes.
   - Ask before importing more than 100 notes.
   - Run a 20-note test first, then either the full import or
     scripts/install-agent.sh for drip mode.
   - On repeated 429s, stop and use the drip guard; do not hammer the API.

5. Safety
   - Never delete, trash or edit my Apple Notes.
   - Never empty Google Keep's trash or reset the ledger without my explicit
     confirmation.
   - Never commit credentials, tokens, cookies, note content, usernames,
     absolute paths, logs or state files. Keep the pre-commit hook enabled.
   - Do not push to a remote unless I ask.

6. Verification
   - After the run, reconcile the ledger against Keep (imported vs pending) and
     spot-check a few notes: title, body, labels, dates.
   - Report what remains, expected wait time under the rate limit, and the
     exact command to resume.
```

If the agent supports repository instructions, put the same text in `AGENTS.md`
or your tool's project-instructions file so it is picked up automatically.

## Quick start

### 1. Export

```sh
notes-export export --output ~/notes-export --format json --account iCloud
```

JSON is preferred: it carries the exact title, plain-text body, creation date
and folder. TXT is also supported; titles then come from filenames, but the TXT
reader was only exercised with fixtures. This project was tested end to end
with the Apple Notes Exporter JSON output. Apple documents an iCloud
Notes export at [privacy.apple.com](https://privacy.apple.com), but that path
is untested here.

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
it in `~/.config/keep/token` (mode 600). The token grants full access to the
account — read [Security](#security).

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
scripts/install-agent.sh       # adaptive background runs with a daily budget
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

## Limits and rate limits

| Google Keep limit | Value |
|---|---|
| Characters per note | 19,999 (title 1,000) |
| Labels | 50, flat |
| Attachments via API | not supported |
| Import | none |

The character limit was exercised by splitting oversized notes on the account
tested. The label cap and attachment behaviour are from Google's documentation.

On the one account tested, the private API refilled at roughly **100 notes per
5 minutes**, with a larger burst available on the first run against a long-idle
account. The drip guard is built around that observed behaviour.

## FAQ

### Can I bulk import Apple Notes into Google Keep?

Not with either vendor's tools. This project exports Apple Notes to JSON
(tested) or TXT and creates the notes in Keep through `gkeepapi`, with a
resumable ledger.

### Does Google Keep have an import feature?

No. Keep only exports (via Google Takeout); there is no import menu and no
consumer API. The official Keep API is enterprise-only (Workspace + domain-wide
delegation) and cannot upload attachments or set labels.

### How do I export iCloud Notes in bulk?

On macOS we used Apple Notes Exporter with JSON output — the path this project
was tested with. Apple also documents an iCloud Notes copy at
[privacy.apple.com](https://privacy.apple.com); this project has not tested that
export or its folder layout.

### Why did the import stop, or return HTTP 429?

Google rate-limits the private Keep API per account. `gkeepapi` retries 429
forever by default; this project aborts the run and the drip guard resumes in
adaptive batches instead of hammering the endpoint.

### Do images, checklists, and formatting transfer?

The library tested here was text-only, so images, checklists and handwriting
were not exercised. The API creates text notes: it cannot upload attachments, so
image/PDF filenames are listed at the end of the note. Checklist-looking text is
kept as lines (`[ ]` / `[x]`) rather than converted to a Keep list. If
formatting or handwriting matters, export those notes as PDF first.

### Is it safe to use my Google account?

The master token grants full account access; it is stored locally with mode 600
and never committed. Use a dedicated account if you prefer, and change the
Google password afterwards to revoke it.

### Does it work without a Mac?

Unverified. The import step is plain Python and reads files, but this project
was only run on macOS 12.7.6 and did not use the privacy.apple.com export.
Treat Linux/Windows as untested.

### How long does a 3,000-note migration take?

On the account tested, export took minutes and the import was limited to
roughly 100 notes per 5 minutes, so a few thousand notes needed hours of
machine uptime spread across sessions. Your account's refill rate may differ.

## Security

- The master token is account-wide. Remove it when done
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
