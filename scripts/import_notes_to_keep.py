#!/usr/bin/env python3
"""Bulk-import a folder tree of exported Apple Notes into Google Keep.

Google Keep has no import feature and no public API for consumer accounts,
so this drives the private mobile Keep API through gkeepapi.

Typical use (after exporting notes with Apple Notes Exporter or Apple's
Data & Privacy export):

    pip install gkeepapi
    python3 import_notes_to_keep.py ~/notes-export --dry-run
    python3 import_notes_to_keep.py ~/notes-export --email you@gmail.com

You will be prompted for a master token; see the accompanying research note
for how to obtain one. Treat that token like your password.

Notes:
  * Text only. Keep has no API for uploading images/attachments; attachment
    filenames are listed at the end of the note so nothing is silently lost.
  * Folder names become Keep labels (Keep labels are flat, max 50).
  * Notes longer than Keep's ~20k character limit are split.
  * Re-running is safe: imported files are recorded in a ledger and skipped.
  * A note is only written to the ledger after Keep echoes it back with a
    server id. A create can report success while nothing is stored, so
    "no error" is not proof that the note exists.
  * --prune-ledger rebuilds the ledger from the notes that actually exist in
    Keep, which repairs the bookkeeping when a ledger has drifted.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import signal
import socket
import sys
import time
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

TEXT_SUFFIXES = {".txt", ".text", ".md", ".markdown", ".html", ".htm", ".json"}
SKIP_DIRS = {"attachments", "recently deleted"}
ROOT_SEGMENTS = {"icloud", "notes", "on my mac"}
KEEP_TEXT_LIMIT = 19_000  # Keep's documented ceiling is 19,999 characters.
MAX_LABELS = 50
DATE_SUFFIX = re.compile(r"\s*[-–—(]\s*(\d{4}-\d{2}-\d{2})\s*\)?\s*$")
DATE_PREFIX = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})[\s_-]+")


class _HTMLToText(HTMLParser):
    """Minimal HTML -> text conversion for Apple Notes' HTML bodies."""

    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style"}:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        elif tag == "input":  # Notes checkboxes
            checked = dict(attrs).get("checked") is not None
            self.parts.append("[x] " if checked else "[ ] ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t]+\n", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def to_text(raw: str) -> str:
    if re.search(r"<\s*(html|body|div|p|br)\b", raw[:2000], re.I):
        parser = _HTMLToText()
        parser.feed(raw)
        return parser.text()
    return raw.replace("\r\n", "\n").strip()


def strip_markdown(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)          # images
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)  # links
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)         # headings
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)            # bold
    text = re.sub(r"(\*|_)(.*?)\1", r"\2", text)               # italic
    text = re.sub(r"^\s*[-*+]\s+\[([ xX])\]\s+", r"[\1] ", text, flags=re.M)
    return text.strip()


def clean_title(stem: str, body: str) -> str:
    title = DATE_PREFIX.sub("", stem)
    title = DATE_SUFFIX.sub("", title).strip(" -–—")
    if not title:
        for line in body.splitlines():
            if line.strip():
                title = line.strip()[:200]
                break
    return title or "Untitled note"


def find_date(stem: str) -> str | None:
    match = DATE_PREFIX.match(stem) or DATE_SUFFIX.search(stem)
    return match.group(1) if match else None


def attachments_for(path: Path) -> list[str]:
    folder = path.with_name(f"{path.stem} (Attachments)")
    if folder.is_dir():
        return sorted(p.name for p in folder.iterdir() if p.is_file())
    return []


def folder_label(parts: tuple[str, ...], depth: int = 0) -> str | None:
    """Turn the folder path into a label, dropping the export's root folders.

    depth=0 uses the whole path ("Work / Projects"); depth=1 uses just the
    first real folder ("Work"), which keeps the label count under Keep's 50.
    """
    index = 0
    while index < len(parts) and parts[index].lower() in ROOT_SEGMENTS:
        index += 1
    parts = parts[index:]
    if depth > 0:
        parts = parts[:depth]
    return " / ".join(parts) or None


PHANTOM_TITLE = re.compile(r"^(\(\d+\)|\d{4}-\d{2}-\d{2}\b.*)$")
HASHTAG = re.compile(r"(?<![\w])#(?=\w)", re.UNICODE)


def split_title_line(title: str, body: str) -> tuple[str, str, bool]:
    """Apple Notes derives the title from the first body line and repeats it.

    If the stored title is truncated ("…"), the full first line is promoted to
    the title before stripping it, otherwise the tail of that line would be
    lost.
    """
    first, sep, rest = body.partition("\n")
    line = first.strip()
    truncated = title.endswith("…")
    core = title[:-1].rstrip() if truncated else title
    if not core or not line:
        return title, body, False

    candidate = line.lstrip("•-* ").strip()
    core_cmp = core.lstrip("•-* ").strip()
    if not core_cmp:
        return title, body, False

    if candidate == core_cmp:
        # Duplicate line (possibly differing only by a bullet marker). Promote
        # the full line when the stored title was truncated or lost its marker.
        new_title = line[:999] if (truncated or candidate != core) else title
        return new_title, rest.lstrip("\n") if sep else "", True
    if truncated and candidate.startswith(core_cmp):
        return line[:999], rest.lstrip("\n") if sep else "", True
    if candidate.startswith(core_cmp + " ("):
        return line[:999], rest.lstrip("\n") if sep else "", True
    return title, body, False


def split_body(body: str, limit: int = KEEP_TEXT_LIMIT) -> list[str]:
    if len(body) <= limit:
        return [body]
    chunks, current = [], []
    size = 0
    for line in body.splitlines(keepends=True):
        while len(line) > limit:  # a single enormous line (minified HTML, etc.)
            if current:
                chunks.append("".join(current).rstrip())
                current, size = [], 0
            chunks.append(line[:limit])
            line = line[limit:]
        if size + len(line) > limit and current:
            chunks.append("".join(current).rstrip())
            current, size = [], 0
        current.append(line)
        size += len(line)
    if current:
        chunks.append("".join(current).rstrip())
    return chunks


def collect(root: Path, skip_dirs: set[str]) -> tuple[list[dict], int]:
    notes = []
    skipped_empty = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        rel = path.relative_to(root)
        parents = {p.lower() for p in rel.parts[:-1]}
        if parents & skip_dirs:
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"  skip (unreadable): {rel}: {exc}", file=sys.stderr)
            continue

        suffix = path.suffix.lower()
        files: list[str] = []
        created = None

        if suffix == ".json":
            # Apple Notes Exporter's JSON format: title, body (already plain
            # text), created/modified ISO dates, folder, account, attachments.
            try:
                record = json.loads(raw)
            except json.JSONDecodeError as exc:
                print(f"  skip (invalid JSON): {rel}: {exc}", file=sys.stderr)
                continue
            body = str(record.get("body") or "")
            title = str(record.get("title") or clean_title(path.stem, body))
            created = str(record.get("created") or "")[:10] or find_date(path.stem)
            files = [
                str(item.get("filename"))
                for item in record.get("attachments") or []
                if item.get("filename")
            ]
            if not files and int(record.get("attachment_count") or 0) > 0:
                files = [f"{record['attachment_count']} attachment(s)"]
        else:
            body = to_text(raw)
            if suffix in {".md", ".markdown"} and ARGS.strip_markdown:
                body = strip_markdown(body)
            title = clean_title(path.stem, body)
            created = find_date(path.stem)
            files = attachments_for(path)

        label = folder_label(rel.parts[:-1], ARGS.label_depth)

        deduped = False
        if not ARGS.keep_title_line:
            title, body, deduped = split_title_line(title, body)
        if (ARGS.skip_empty and not body.strip()
                and (not title.strip() or PHANTOM_TITLE.match(title))):
            skipped_empty += 1
            continue

        if ARGS.escape_hashtags:
            # Keep turns #tag into a label. A zero-width space keeps the text
            # readable without consuming one of the 50 label slots.
            body = HASHTAG.sub("#\u200b", body)
            title = HASHTAG.sub("#\u200b", title)

        if ARGS.keep_dates and created:
            body = f"Created: {created}\n\n{body}"
        if files:
            body = f"{body}\n\nAttachments (not imported): {', '.join(files)}".strip()

        chunks = split_body(body)
        for index, chunk in enumerate(chunks, 1):
            suffix = f" ({index}/{len(chunks)})" if len(chunks) > 1 else ""
            notes.append(
                {
                    "path": str(rel),
                    "title": (title + suffix)[:999],
                    "text": chunk,
                    "label": label,
                    "deduped": deduped,
                    "index": index,
                    "frags": len(chunks),
                    # Key on the source position, not the transformed text, so
                    # changing flags does not make imported notes look new.
                    "key": hashlib.sha256(
                        f"{rel}\0{index}\0{len(chunks)}".encode()
                    ).hexdigest()[:16],
                }
            )
    return notes, skipped_empty


def load_ledger(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_ledger(path: Path, ledger: dict) -> None:
    path.write_text(json.dumps(ledger, indent=2, sort_keys=True))


class SyncTimeout(Exception):
    """Raised when a sync exceeds --sync-timeout (usually a 429 backoff)."""


def _on_alarm(signum, frame):
    raise SyncTimeout("sync exceeded timeout")


def call_with_timeout(func, seconds: float, *args):
    """gkeepapi retries 429 forever; abort instead of hanging overnight."""
    if not seconds:
        return func(*args)
    previous = signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(int(seconds))
    try:
        return func(*args)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def sync_with_timeout(keep, seconds: float, resync: bool = False) -> None:
    call_with_timeout(keep.sync, seconds, resync)


def label_for(keep, name: str, cache: dict):
    if name in cache:
        return cache[name]
    label = keep.findLabel(name)
    if label is None:
        if len(cache) >= MAX_LABELS:
            print(f"  label limit reached, dropping label {name!r}", file=sys.stderr)
            cache[name] = None
            return None
        label = keep.createLabel(name)
    cache[name] = label
    return label


ACCOUNT_EMAIL = ""


def account_tag(email: str) -> str:
    """Short, non-reversible tag so a cached state is not reused cross-account."""
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]


def sync_state_path() -> Path | None:
    return Path(ARGS.sync_state).expanduser() if ARGS.sync_state else None


def load_sync_state(path: Path | None) -> dict | None:
    if not path or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: ignoring unreadable sync state: {exc}", file=sys.stderr)
        return None
    if not isinstance(payload, dict) or "state" not in payload:
        return None
    if payload.get("account") != account_tag(ACCOUNT_EMAIL):
        print("warning: sync state belongs to another account; ignoring it",
              file=sys.stderr)
        return None
    return payload["state"]


def save_sync_state(keep, path: Path | None) -> None:
    """Persist gkeepapi's cursor so the next run only fetches changes.

    The file holds note text, so it is written mode 600 and is gitignored.
    """
    if not path:
        return
    payload = {"account": account_tag(ACCOUNT_EMAIL), "state": keep.dump()}
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
    except OSError as exc:
        print(f"warning: could not save sync state: {exc}", file=sys.stderr)


def connect():
    """Authenticate, or raise ImportError / an auth error for the caller."""
    import gkeepapi  # imported lazily so --dry-run works without it

    global ACCOUNT_EMAIL
    email = ARGS.email or input("Google account email: ").strip()
    ACCOUNT_EMAIL = email
    token = os.environ.get("KEEP_MASTER_TOKEN") or getpass.getpass("Keep master token: ")
    cache = sync_state_path()

    state = load_sync_state(cache)
    if state is not None:
        try:
            keep = gkeepapi.Keep()
            call_with_timeout(keep.authenticate, ARGS.sync_timeout,
                              email, token.strip(), state)
            print("Resumed the cached Keep cursor; fetching changes only.",
                  file=sys.stderr)
            return keep
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ not in {"ResyncRequiredException",
                                          "UpgradeRecommendedException",
                                          "SyncException"}:
                raise
            print(f"warning: cached state rejected ({type(exc).__name__}); "
                  "doing a full sync", file=sys.stderr)

    keep = gkeepapi.Keep()
    call_with_timeout(keep.authenticate, ARGS.sync_timeout, email, token.strip())
    return keep


def do_sync(keep) -> None:
    """Sync, falling back to a full resync if the cached cursor is rejected."""
    try:
        sync_with_timeout(keep, ARGS.sync_timeout)
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ != "ResyncRequiredException":
            raise
        print("cached Keep cursor was rejected; re-syncing from scratch",
              file=sys.stderr)
        sync_with_timeout(keep, ARGS.sync_timeout, resync=True)
    save_sync_state(keep, sync_state_path())


def signature(title: str, text: str) -> tuple[str, str]:
    """Whitespace-insensitive identity for matching a local note to Keep."""
    return (" ".join(title.split()), " ".join(text.split()))


def prune_ledger(keep, notes: list[dict], ledger: dict, ledger_path: Path) -> tuple[int, int]:
    """Drop ledger entries whose note is not actually in Keep.

    The ledger is only as trustworthy as the create call: a create can report
    success while the note is never stored, which leaves the ledger ahead of
    reality. This rebuilds it from what a fresh sync can see.
    """
    server = Counter(signature(n.title or "", n.text or "") for n in keep.all())
    by_key = {n["key"]: n for n in notes}
    kept: dict = {}
    dropped = 0
    for key, path in ledger.items():
        item = by_key.get(key)
        if item is None:  # entry for a file that is no longer in the export
            dropped += 1
            continue
        sig = signature(item["title"], item["text"])
        if server[sig] > 0:
            server[sig] -= 1
            kept[key] = path
        else:
            dropped += 1
    save_ledger(ledger_path, kept)
    return len(kept), dropped


def main() -> int:
    global ARGS
    ARGS = parse_args()
    if ARGS.timeout:
        # gkeepapi sets no per-request timeout; without this a stalled Keep
        # response blocks the run indefinitely.
        socket.setdefaulttimeout(ARGS.timeout)
    root = ARGS.export_dir.expanduser().resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    notes, skipped_empty = collect(root, SKIP_DIRS | {d.lower() for d in ARGS.skip_dir})
    labels = sorted({n["label"] for n in notes if n["label"]})
    print(f"Found {len(notes)} note fragment(s) in {root}")
    if skipped_empty:
        print(f"Skipped {skipped_empty} blank phantom note(s)")
    deduped = sum(1 for n in notes if n.get("deduped"))
    if deduped:
        print(f"Removed duplicated title line from {deduped} note(s)")
    if labels:
        print(f"Labels ({len(labels)}): {', '.join(labels[:MAX_LABELS])}")
    if len(labels) > MAX_LABELS:
        print(f"warning: {len(labels)} labels exceeds Keep's limit of {MAX_LABELS}",
              file=sys.stderr)

    ledger_path = Path(ARGS.state)
    ledger = {} if ARGS.reimport else load_ledger(ledger_path)
    already = sum(1 for n in notes if n["key"] in ledger)
    pending = [n for n in notes if n["key"] not in ledger]
    if ARGS.limit:
        pending = pending[: ARGS.limit]
    print(f"{already} already imported, {len(pending)} to go")

    if ARGS.dry_run:
        for note in pending[:20]:
            label = f" [{note['label']}]" if note["label"] else ""
            print(f"  + {note['title']}{label} ({len(note['text'])} chars)")
        if len(pending) > 20:
            print(f"  ... and {len(pending) - 20} more")
        return 0

    if not pending and not ARGS.prune_ledger:
        print("Nothing pending; nothing to do.")
        return 0

    try:
        keep = connect()
    except ImportError:
        print("error: pip install gkeepapi", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - surface Google's message verbatim
        print(f"error: authentication failed: {exc}", file=sys.stderr)
        if isinstance(exc, SyncTimeout):
            print("Likely a Keep rate limit; try again next run.", file=sys.stderr)
        return 1

    if ARGS.prune_ledger:
        kept, dropped = prune_ledger(keep, notes, ledger, ledger_path)
        save_sync_state(keep, sync_state_path())
        print(f"Reconciled ledger against Keep: kept {kept}, dropped {dropped} "
              f"(was {len(ledger)})")
        print(f"Next import would create {len(notes) - kept} note(s).")
        return 0

    print(f"Authenticated at {time.strftime('%H:%M:%S')}; importing {len(pending)} note(s)",
          file=sys.stderr)

    cache: dict = {}
    failures = []
    created = 0
    confirmed = 0
    batch: list[tuple[dict, object]] = []

    def commit(items) -> None:
        """Record only the notes Keep confirmed, then save."""
        for item, _ in items:
            ledger[item["key"]] = item["path"]
        save_ledger(ledger_path, ledger)

    def flush() -> list[dict]:
        """Commit confirmed notes; return the ones Keep did not echo back."""
        nonlocal confirmed
        ok = [(item, node) for item, node in batch if getattr(node, "server_id", None)]
        missing = [item for item, node in batch if not getattr(node, "server_id", None)]
        commit(ok)
        confirmed += len(ok)
        batch.clear()
        return missing

    def unconfirmed_error(missing: list[dict]) -> None:
        print(f"error: Keep accepted the request but did not store {len(missing)} "
              f"note(s).", file=sys.stderr)
        print("They were not written to the ledger. This is the silent-drop "
              "behaviour seen at Google's creation ceiling: stop for a while and "
              "retry with a smaller --batch-size.", file=sys.stderr)

    aborted = False
    for note in pending:
        try:
            node = keep.createNote(note["title"], note["text"])
            if ARGS.archive:
                node.archived = True
            if note["label"] and not ARGS.no_labels:
                label = label_for(keep, note["label"], cache)
                if label is not None:
                    node.labels.add(label)
            batch.append((note, node))
            created += 1
        except KeyboardInterrupt:
            print("\ninterrupted, syncing what we have...")
            break
        except Exception as exc:  # noqa: BLE001 - one bad note should not stop the run
            failures.append((note["path"], repr(exc)))
            print(f"  failed: {note['path']}: {exc}", file=sys.stderr)
        if ARGS.delay:
            time.sleep(ARGS.delay)
        if len(batch) >= ARGS.batch_size:
            started = time.time()
            try:
                do_sync(keep)
            except Exception as exc:  # noqa: BLE001 - stop instead of cascading
                print(f"error: sync failed, stopping: {exc}", file=sys.stderr)
                aborted = True
                break
            missing = flush()
            print(f"  {created} note(s) sent, {confirmed} confirmed at "
                  f"{time.strftime('%H:%M:%S')} ({time.time() - started:.1f}s)",
                  file=sys.stderr)
            if missing:
                unconfirmed_error(missing)
                aborted = True
                break

    if batch:
        try:
            do_sync(keep)
        except Exception as exc:  # noqa: BLE001
            print(f"error: final sync failed: {exc}", file=sys.stderr)
            aborted = True
        else:
            missing = flush()
            if missing:
                unconfirmed_error(missing)
                aborted = True
    save_ledger(ledger_path, ledger)
    print(f"Created {created} note(s), {confirmed} confirmed by Keep; "
          f"{len(failures)} failed; ledger: {ledger_path}")
    if aborted:
        print("Stopped early. Re-run the same command to continue from here.",
              file=sys.stderr)
        return 1
    if failures:
        print("Failures:", file=sys.stderr)
        for path, error in failures:
            print(f"  {path}: {error}", file=sys.stderr)
        print("Re-run the same command to retry only what is missing.", file=sys.stderr)
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export_dir", type=Path, help="root of the exported notes tree")
    parser.add_argument("--email", help="Google account email (prompted if omitted)")
    parser.add_argument("--dry-run", action="store_true", help="list what would be imported")
    parser.add_argument("--limit", type=int, help="import at most N notes (for a test run)")
    parser.add_argument("--no-labels", action="store_true", help="do not create Keep labels")
    parser.add_argument("--label-depth", type=int, default=0, metavar="N",
                        help="use only the first N folder levels as the label "
                             "(default 0 = full path; use 1 to stay under 50 labels)")
    parser.add_argument("--archive", action="store_true",
                        help="import notes already archived in Keep")
    parser.add_argument("--strip-markdown", action="store_true",
                        help="remove Markdown syntax from .md files")
    parser.add_argument("--keep-dates", action="store_true",
                        help="prepend 'Created: YYYY-MM-DD' when the filename has a date")
    parser.add_argument("--skip-empty", action="store_true",
                        help="skip blank records (dated 2001-01-01 or untitled)")
    parser.add_argument("--keep-title-line", action="store_true",
                        help="keep the first body line even when it repeats the title")
    parser.add_argument("--escape-hashtags", action="store_true",
                        help="stop #tags from becoming Keep labels (adds a zero-width space)")
    parser.add_argument("--reimport", action="store_true", help="ignore the ledger")
    parser.add_argument("--prune-ledger", action="store_true",
                        help="check the ledger against the notes that actually exist "
                             "in Keep and drop entries for missing ones; use the same "
                             "flags you imported with")
    parser.add_argument("--sync-state", default=".keep-sync-state.json", metavar="PATH",
                        help="cache gkeepapi's sync cursor here so later runs fetch "
                             "only changes instead of the whole account; the file "
                             "contains note text, so keep it private "
                             "(default .keep-sync-state.json; pass an empty string "
                             "to disable)")
    parser.add_argument("--state", default=".keep-import-state.json", help="ledger path")
    parser.add_argument("--batch-size", type=int, default=20,
                        help="sync to Keep every N notes (default 20)")
    parser.add_argument("--delay", type=float, default=0.2,
                        help="seconds to wait between notes (default 0.2)")
    parser.add_argument("--timeout", type=float, default=180,
                        help="per-request socket timeout in seconds (0 disables)")
    parser.add_argument("--sync-timeout", type=float, default=180,
                        help="abort a sync after this many seconds, instead of "
                             "retrying a 429 forever (0 disables)")
    parser.add_argument("--skip-dir", action="append", default=[],
                        help="extra directory name to skip (repeatable)")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
