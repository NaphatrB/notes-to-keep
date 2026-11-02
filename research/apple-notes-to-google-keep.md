# Bulk migrating Apple / iCloud Notes to Google Keep

Researched 2026-11-02. Short version: **there is no official path in either
direction**. Apple exports one note at a time from the app; Google Keep has no
import feature at all. A bulk migration means "export to files, then push the
files into Keep with an unofficial client". Text survives; images, formatting,
checklists and dates do not survive cleanly.

## What the two vendors actually offer

| | Apple Notes | Google Keep |
|---|---|---|
| Bulk export | Only via **Apple Data & Privacy** (iCloud Notes), or third-party Mac apps. In-app: File → Export as PDF, **one note at a time**. | Only via **Google Takeout** (HTML + JSON per note). |
| Bulk import | n/a | **None.** No import menu, no consumer API. Google Product Experts confirm you must transfer manually. |
| Public API | None | [Google Keep API](https://developers.google.com/workspace/keep/api) exists, but it is **enterprise-only**: Workspace + domain-wide delegation via a service account or admin-approved OAuth client. It creates text/list notes; attachments are read-only and the `Note` resource has no labels. Useless for a personal @gmail.com account. |

So every working migration is: **Apple → files → unofficial Keep client
(`gkeepapi`) → Keep.**

## Option comparison

| Approach | Bulk? | Attachments | Effort | Notes |
|---|---|---|---|---|
| [Apple Notes Exporter](https://github.com/kzaremski/apple-notes-exporter) (free, GPLv3, macOS 11+) | yes | yes, beside each note | low | Reads the local Notes database, keeps folder tree, 15+ formats incl. Markdown/HTML/JSON, CLI + Shortcuts + MCP. Best export tool. Does **not** cover Gmail/Yahoo/Outlook note accounts (move those to iCloud/On My Mac first). |
| [Exporter](https://apps.apple.com/app/exporter/id1099120373) (free, App Store, Chintan Ghate) | yes | yes | low | Markdown/HTML, preserves folders and note dates. Reviewers report hours for ~3,000 notes and occasional charset failures. |
| [Apple Data & Privacy](https://privacy.apple.com) iCloud Notes export | yes | yes (separate files) | low, but slow | Official. Plain `.txt` in your folder tree + attachments; includes Recently Deleted. Takes up to 7 days (typically ~2–5). Formatting **and handwriting** are lost. |
| [notes-exporter](https://github.com/storizzi/notes-exporter) (AppleScript + Python) | yes | yes, inline | medium | Good when you want Markdown/PDF/DOCX with images inline. Needs Automation permissions. |
| Manual copy/paste, or Docs → right-click → **Save to Keep** | no | manual | high | Fine for a handful of notes. Keep's Docs/Slides side panel is the only "import-ish" feature Google ships. |
| [adamyi/notes_to_keep](https://github.com/adamyi/notes_to_keep) | yes | **no** | medium | 2018 proof of concept. Alpha quality, text only, and its password login no longer works. Read it as a reference, not a tool to run as-is. |

## Runbook for this case: Monterey, ~3,500 text-only notes

Text-only is the best case: no attachment workaround, no handwriting loss.
Expect the export to take minutes and the Keep import to take roughly 45–120
minutes (API-bound, batched).

```sh
# 0. In Notes.app: open every account once so iCloud has downloaded everything.
#    Do not delete anything yet.

# 1. Export as JSON (title + plain-text body + dates + folder per note).
#    Prefer the GUI: iCloud account -> JSON -> folder output. Plain Text (TXT)
#    is the fallback if any JSON file turns out malformed (see below).
#
#    CLI alternative (same engine). Note: running it from Terminal means
#    granting Full Disk Access to Terminal itself, which is broader than
#    granting it to the app. If you use it, revoke Terminal's FDA afterwards.
"/Applications/Apple Notes Exporter.app/Contents/SharedSupport/notes-export" \
  export --output ~/notes-export --format json --account iCloud

# 2. Check nothing was left behind (compare with Notes.app's note count).
find ~/notes-export -name '*.json' | wc -l

# 3. See the plan: label count, split notes, titles. No account access.
#    Add --skip-empty if the export contains blank "2001-01-01 (n)" records
#    (a known issue on some libraries, see issue #45 upstream).
python3 scripts/import_notes_to_keep.py ~/notes-export --dry-run --label-depth 1

# 4. Test 20 notes, then look at keep.google.com.
python3 scripts/import_notes_to_keep.py ~/notes-export --label-depth 1 --limit 20

# 5. Full run. Batch 50 keeps sync overhead down; re-running resumes.
python3 scripts/import_notes_to_keep.py ~/notes-export --label-depth 1 --batch-size 50
```

Notes specific to this size:

- **`--label-depth 1`** uses only the top-level folder as the label. With nested
  folders you will otherwise exceed Keep's 50-label cap. Check the dry-run's
  `Labels (N)` line.
- **JSON beats TXT.** Both carry the same plain-text body, but JSON also carries
  the real title, ISO creation date and attachment list, so nothing depends on
  filename guesswork. In TXT mode the title comes from the filename and dates
  need `--add-date-prefix` at export time.
- **Why not Markdown?** Keep stores plain text via this API (bold/italic exist
  in the UI but are not in the text payload), so Markdown syntax would show up
  literally. Exporting JSON/TXT avoids a stripping step entirely. If you do
  export Markdown, add `--strip-markdown`.
- **Apple truncates long titles.** The stored title is roughly the first 70
  characters plus `…`, while the body keeps the full first line. The importer
  promotes that full line to the Keep title before removing the duplicate body
  line; without that step the tail of the line is silently lost (it affected
  1,282 of 3,253 notes in this library).
- **Dates.** Keep stamps every imported note with the import time. To keep the
  original date searchable, import with `--keep-dates`: with a JSON export it
  reads the `created` field; with TXT it needs `--add-date-prefix` at export
  time and strips `YYYY-MM-DD ` from the title. Without it, dates are lost.
- **Archive option.** 3,500 cards is a lot of wall. `--archive` imports
  everything already archived; you can also archive old years in the Keep UI
  later (select multiple notes → archive).
- **Verify before deleting.** The ledger prints `Created N note(s)`. Re-run the
  same command: it should say `3500 already imported, 0 to go`. Keep the export
  folder and iCloud Notes until you have spot-checked search, labels and a few
  long notes.
- **Interruptions are safe.** Each completed batch is written to
  `.keep-import-state.json` only after the server accepts it, so `Ctrl-C` or a
  dropped connection costs at most one batch. Re-run to continue (a failed
  batch can duplicate up to 50 notes; delete those from Keep).

### Tool trust check (v2.1 source, reviewed 2026-11-02)

Verified by reading the tagged source, not the marketing:

- **No network code.** No `URLSession`, `Network`, `WebKit` or `CFNetwork` in
  the app, CLI or MCP target; no telemetry, no update checker, no Sparkle.
  The only `NSWorkspace.open` calls open local files or the project's GitHub
  pages from the Help menu.
- **Read-only on your notes.** The SQLite handle is opened
  `SQLITE_OPEN_READONLY` + `PRAGMA query_only`, copied to a 0700 temp directory
  via `sqlite3_backup`, and the copy is deleted on close. There is no
  INSERT/UPDATE/DELETE anywhere in the parser.
- **Writes are confined to the output you pick.** Deletions are its own archive
  staging directory, plus files named in its sync manifest (incremental mode
  only).
- **No sandbox.** Entitlements are empty and App Sandbox is off, because it
  needs Full Disk Access. Once granted, the binary *could* read everything TCC
  protects; the source says it only reads the Notes database, but you are
  trusting the binary, not a sandbox.
- **MCP server is opt-in and stdio-only** (no listening port). Its write guard
  allows `$HOME` and `/tmp` by path prefix; a symlink inside `$HOME` could
  point elsewhere, so don't enable it unless you need it.
- **Release integrity:** notarized Developer ID app, hardened runtime. But
  releases are built on the maintainer's machine, CI only builds unsigned, the
  `v2.1` tag is unsigned, and no checksums are published. Notarization is a
  malware scan, not proof that the binary matches this source.
- **Known bugs:** #45 phantom empty notes dated `2001-01-01` on some libraries
  (use `--skip-empty`); #49 incremental sync can leave stale files (don't use
  `--incremental` for this migration).

On your Mac, verify before launching:
`spctl -a -vv "/Applications/Apple Notes Exporter.app"` should say
`accepted, source=Notarized Developer ID`, and
`codesign -dv --verbose=4` should show a consistent Team ID.

## Recommended pipeline (Mac, keep as much as possible)

### 1. Export

Use **Apple Notes Exporter** (GUI or CLI). It preserves the folder tree and
writes attachments next to each note:

```sh
# GUI: pick iCloud → format Markdown or HTML → folder output
# CLI (inside the app bundle):
notes-export export --output ~/notes-export --format markdown --account iCloud
```

Fallback if you cannot install software: request **iCloud Notes** at
[privacy.apple.com](https://privacy.apple.com) → *Request a copy of your data*.
You get `.txt` files organised by folder plus attachment files. Expect days,
not minutes.

Either way you end up with something like:

```
notes-export/
  iCloud/Notes/Work/Meeting notes.md
  iCloud/Notes/Work/Meeting notes (Attachments)/whiteboard.jpg
  iCloud/Notes/Personal/Recipe.txt
```

### No Mac?

Skip step 1's exporter. The **privacy.apple.com** route is pure browser and
works from Linux/Windows, so the whole pipeline (export → this script) runs
without a Mac. `icloud.com/notes` itself has no bulk selection or export — it is
copy-paste only. Every third-party bulk exporter is macOS-only because it reads
the local Notes database.

### 2. Sanity-check the export

- Count files against Notes.app's note count.
- Open a few notes with images, checklists and handwriting. Handwriting often
  exports as an empty text file (Apple's export) or as an image (third-party
  tools) — decide what you want to do with those before importing.
- Check the tree includes only what you want. Delete or move anything private
  you do not want in a Google account.

### 3. Import into Keep

`scripts/import_notes_to_keep.py` in this repo reads that tree and creates Keep
notes through `gkeepapi`:

```sh
pip install gkeepapi

# 1. see what will happen, no account access:
python3 scripts/import_notes_to_keep.py ~/notes-export --dry-run

# 2. small real test (5 notes), then check them in keep.google.com:
python3 scripts/import_notes_to_keep.py ~/notes-export --limit 5

# 3. full run:
python3 scripts/import_notes_to_keep.py ~/notes-export
```

It handles: folder → label (dropping `iCloud/Notes` roots), title clean-up
(including promoting Apple's truncated `…` titles to the full first line),
`Title - YYYY-MM-DD` filenames, HTML → text, splitting notes over Keep's
~20,000-character limit, attachment filenames appended to the note body,
per-note error isolation, and a JSON ledger so a re-run resumes instead of
duplicating. `--strip-markdown` removes `**`/`#`/link syntax (Keep has no
Markdown rendering); `--keep-dates` prepends the creation date from the
filename; `--label-depth 1` keeps the label count under Keep's 50;
`--archive` imports every note already archived.

### 4. Deal with what did not migrate

- **Images / PDFs / scans**: Google Keep has no write API for attachments.
  Add them by hand in the Keep UI for the notes that matter, and keep the
  export folder (or upload it to Drive) as the complete archive.
- **Handwriting**: typically lost or turned into an image. Export those notes
  as PDF from Notes.app before deleting anything.
- **Checklists**: become plain text lines (`[ ]` / `[x]`). Re-create the
  important ones as Keep lists.
- **Dates**: Keep sets the creation time to the import time. Sorting by date
  will not reflect history unless you add the date to the title/body.

## Authentication (the annoying part)

`gkeepapi` needs a **master token**. Google killed password/app-password logins
for this flow; `BadAuthentication` is the usual symptom.

1. Open <https://accounts.google.com/EmbeddedSetup> in a browser and sign in.
2. Accept the "I agree" screen. The page may hang on a loading screen — that is
   expected; the cookie is already set.
3. Copy the `oauth_token` cookie (starts with `oauth2_4/`). It is `HttpOnly`, so
   use DevTools → Application/Storage → Cookies, not `document.cookie`.
4. Exchange it immediately (it is short-lived, single-use):

```python
import gpsoauth
print(gpsoauth.exchange_token("you@gmail.com", "oauth2_4/...", "0123456789abcdef"))
# -> {'Token': 'aas_et/...'}   <- this is the master token
```

5. Pass it to the script via the `KEEP_MASTER_TOKEN` environment variable or the
   prompt.

**Treat the master token like your password: it grants full account access.**
Keep it out of shell history and git, prefer `keyring`, and if it leaks change
your Google password and review *Security → Your devices*. This is also why the
whole approach is a bit fragile: it depends on a private API Google can change
without notice.

## Limitations to expect in Keep

| Limit | Value |
|---|---|
| Characters per note | 19,999 (title < 1,000) |
| Labels | 50, flat (no folders/nesting) |
| Import | none |
| Markdown | not rendered; bold/italic/H1–H2 only since May 2025 |
| Attachments via API | not supported |

Notes bigger than the limit are split by the script (`Title (1/2)`), which is
lossy but better than silent truncation. If you have more than 50 Apple
folders, decide which labels matter — otherwise everything lands unlabelled or
the script drops the extras.

## If you have a Google Workspace account

The official Keep API is the clean option, but it requires a Workspace domain,
a service account with domain-wide delegation (or an admin-approved OAuth
client), and it cannot set labels or upload attachments. It is designed for
CASB/compliance tooling, not migration. For a one-off personal migration it is
more work than `gkeepapi`, not less.

## Honest recommendation

If the notes are mostly short text, the pipeline above works and takes an
afternoon plus the export wait. If a large share of the value is in photos,
scans, handwriting or long formatted documents, Google Keep is the wrong
destination: it caps notes at 20k characters, has no folders, and its API cannot
carry attachments. In that case export to Markdown/PDF and keep the archive in
Drive (or move to a notes app with a real importer), and copy only the notes
that belong in a quick-capture tool into Keep.

## Sources

- Apple, *Import, export, and print notes on Mac* (Export as PDF, one note at a time): <https://support.apple.com/guide/notes/not201900c07/mac>
- Apple, Data & Privacy — request a copy of your data (iCloud Notes included): <https://privacy.apple.com>
- Apple iCloud Notes export experience (plain text, attachments, ~5 days, formatting/handwriting lost): <https://richard.dallaway.com/apple-icloud-notes-export-isn-t-great>
- Step-by-step iCloud Notes export: <https://alekzandriia.com/how-to-export-icloud-notes/>
- Google Keep, *Export your data* (Takeout; no import counterpart): <https://support.google.com/keep/answer/10017039>
- Google Keep API overview (enterprise, domain-wide delegation): <https://developers.google.com/workspace/keep/api/guides>
- Keep API `Note` resource (no labels, attachments output-only, title <1,000): <https://developers.google.com/workspace/keep/api/reference/rest/v1/notes>
- Keep labels limit (50): <https://support.google.com/keep/answer/6191044>
- Keep 19,999-character limit (and May 2025 formatting): <https://www.androidpolice.com/i-switched-from-evernote-to-keep-heres-what-i-miss-and-dont/>
- No Keep import (Google Product Expert): <https://support.google.com/docs/thread/16294446/i-want-to-import-notes-into-keep>
- gkeepapi (unofficial client, master-token auth, no blob upload): <https://github.com/kiwiz/gkeepapi> · docs: <https://gkeepapi.readthedocs.io/en/latest/>
- gpsoauth alternative login flow (`oauth_token` → master token): <https://github.com/simon-weber/gpsoauth#alternative-flow>
- `BadAuthentication` on 2FA accounts, cookie-vs-password detail: <https://errno.jofrey.eu/posts/gpsoauth-badauthentication-2fa-master-token/>
- Apple Notes Exporter (bulk, folder structure, attachments, CLI/MCP): <https://github.com/kzaremski/apple-notes-exporter>
- Exporter (App Store, Markdown/HTML + attachments): <https://apps.apple.com/app/exporter/id1099120373>
- notes-exporter (AppleScript/Python, images inline): <https://github.com/storizzi/notes-exporter>
- notes_to_keep (2018, text-only reference implementation): <https://github.com/adamyi/notes_to_keep>
- Migration reality check from a switcher: <https://www.androidpolice.com/switch-google-keep-apple-notes/>
