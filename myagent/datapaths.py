"""Shared-store path resolution and IO for the name-keyed JSON stores.

agent_instructions.json / skills.json / system_prompts.json are authored
content that should follow the user across machines. Historically they were
git-tracked, but they are also live runtime files the apps rewrite constantly,
so every machine accumulated local-only entries between commits and a
"remote wins" pull discarded them. Mirroring TodoList's todos.json move
(commit 6097dc5), the stores now live in <OneDrive>/MyAppShare/ when a
OneDrive sync client is present — OneDrive, not git, is the sync channel —
falling back to the repo root on machines without OneDrive. (The dir was
named MyAgent until 2026-07-19; _shared_dir adopts a legacy dir by renaming
it in place, so machines migrate in any order with no manual step.)

Design invariants:
- resolve_store(name) decides the path once at import time (mkdir of the
  shared dir is the only side effect). A MYAGENT_DATA_DIR env var overrides
  the OneDrive discovery (any shared folder works, e.g. for tests).
- load_store(path) is where the ONE-SHOT migration runs (lazily, once per
  process): a leftover repo-root copy is unioned into the shared file, then
  renamed to <name>.migrated.bak so entries later deleted from the shared
  store cannot resurrect at the next launch. A shared file that exists but
  does not parse is NEVER overwritten or migrated over — it may be a
  half-synced cloud write; the caller gets {} and the next launch retries.
- save_store(path, data) is atomic (unique mkstemp + os.replace — two
  processes saving the same store concurrently cannot interleave a shared
  .tmp name) and recreates the parent dir first: OneDrive garbage-collects a
  still-empty shared dir within seconds of creation (observed 2026-07-18).
- absorb_conflict_forks(path, data) heals OneDrive's concurrent-write
  resolution (the losing machine's copy reappears as <stem>-<Computer>.json
  beside the main file): each fork is key-level-unioned into data and then
  deleted. Union semantics match merge_system_prompts.py — new names are
  adopted, identical entries dedupe, a genuinely different entry under an
  existing name is preserved as "<name>__<label>" for the user to reconcile
  in the app UI. The union is idempotent, so concurrent absorbers converge.
"""

import glob
import json
import os
import platform
import re
import sys
import tempfile

# Repo root (parent of the myagent/ package) — same expression as constants.py,
# duplicated here so constants can import datapaths without a cycle.
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR_ENV = "MYAGENT_DATA_DIR"
SHARED_SUBDIR = "MyAppShare"
# Previous names of the shared dir. A machine whose OneDrive still holds one
# (rename not yet made/synced anywhere) adopts it by renaming it in place —
# OneDrive syncs a rename cheaply (no re-upload) and every other machine
# receives it as a rename too, so the transition needs no manual step.
LEGACY_SHARED_SUBDIRS = ("MyAgent",)

# Store basenames whose one-shot local→shared migration already ran (or was
# deliberately skipped) in this process.
_migrated = set()


def find_onedrive_root():
    """This machine's OneDrive sync root, or None. Same discovery as
    TodoList.py: the Windows client publishes it in the OneDrive /
    OneDriveConsumer / OneDriveCommercial env vars; the macOS File Provider
    client syncs under ~/Library/CloudStorage/OneDrive-* (legacy installs
    used ~/OneDrive)."""
    if sys.platform == "win32":
        for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
            root = os.environ.get(var)
            if root and os.path.isdir(root):
                return root
        return None
    home = os.path.expanduser("~")
    candidates = sorted(glob.glob(os.path.join(home, "Library", "CloudStorage", "OneDrive-*")))
    for cand in candidates:  # prefer the personal account when several are synced
        if cand.endswith("OneDrive-Personal"):
            return cand
    if candidates:
        return candidates[0]
    legacy = os.path.join(home, "OneDrive")
    return legacy if os.path.isdir(legacy) else None


def _shared_dir():
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return override
    onedrive = find_onedrive_root()
    if not onedrive:
        return None
    shared = os.path.join(onedrive, SHARED_SUBDIR)
    # One-time adoption of a pre-rename shared dir (e.g. MyAgent →
    # MyAppShare, 2026-07-19). Must run BEFORE resolve_store's makedirs:
    # once an empty new-name dir exists the whole-dir rename can't happen and
    # the legacy data would be stranded.
    for old in LEGACY_SHARED_SUBDIRS:
        legacy = os.path.join(onedrive, old)
        if not os.path.isdir(legacy):
            continue
        if not os.path.isdir(shared):
            # Cheap whole-dir rename (OneDrive syncs it without re-upload).
            # If it fails (file lock, mid-sync), keep working out of the
            # legacy dir this session and retry at the next launch — never
            # serve an empty store while the data sits under the old name.
            try:
                os.rename(legacy, shared)
            except OSError:
                return legacy
        else:
            # The new dir already exists — e.g. TodoList.py (which shares
            # this dir) created it first, or an old-code machine recreated
            # the legacy dir after the rename synced. Move the legacy files
            # across individually; a colliding name stays put for manual
            # review (the shared side is the live store). rmdir succeeds
            # only once the legacy dir is empty.
            try:
                for fn in os.listdir(legacy):
                    src, dst = os.path.join(legacy, fn), os.path.join(shared, fn)
                    if not os.path.exists(dst):
                        os.rename(src, dst)
                os.rmdir(legacy)
            except OSError:
                pass
        break
    return shared


def resolve_store(filename):
    """Path for a shared store file: <shared dir>/<filename> when a shared dir
    is available (env override or OneDrive), else <repo root>/<filename>."""
    shared = _shared_dir()
    if not shared:
        return os.path.join(_BASE_DIR, filename)
    try:
        os.makedirs(shared, exist_ok=True)
        return os.path.join(shared, filename)
    except OSError:
        return os.path.join(_BASE_DIR, filename)


def is_shared(path):
    """True when the resolved store lives outside the repo root (i.e. the
    OneDrive / override dir is in use)."""
    return os.path.dirname(os.path.abspath(path)) != _BASE_DIR


def machine_label():
    """Short hostname-derived label used to name conflicting variants."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", platform.node() or "").strip("_.") or "local"


def union_stores(primary, other, label):
    """Key-level union of two name-keyed dicts, mutating primary in place.

    Names only in `other` are adopted; identical entries (compared as parsed
    JSON) dedupe; a different entry under an existing name is preserved as
    "<name>__<label>" (numbered if that too is taken by different content).
    Returns True when primary changed. Idempotent: re-unioning the same
    `other` is a no-op."""
    changed = False
    for name, entry in other.items():
        if name not in primary:
            primary[name] = entry
            changed = True
        elif primary[name] == entry:
            continue
        else:
            variant = f"{name}__{label}"
            n = 2
            while variant in primary and primary[variant] != entry:
                variant = f"{name}__{label}{n}"
                n += 1
            if variant not in primary:
                primary[variant] = entry
                changed = True
    return changed


def save_store(path, data):
    """Atomic write: unique temp file in the target dir + os.replace, so a
    concurrent saver (SelfBot and MyAgent share skills.json) can't interleave,
    and OneDrive never syncs a half-written JSON. Recreates the parent dir —
    OneDrive GCs a still-empty shared dir out from under the first save."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def load_store(path):
    """Tolerant read: the parsed dict, or {} when the file is missing,
    unreadable, or not a dict (a half-synced cloud write must never crash the
    app or get overwritten with defaults — the caller can retry next launch).
    Runs the one-shot repo-root migration first when the store is shared."""
    _migrate_local_into(path)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _migrate_local_into(path):
    """One-shot per process: fold a leftover repo-root copy of this store into
    the shared file — seed it outright when the shared slot is empty, union
    otherwise (shared entries win their names; the local variant of a
    conflicting name survives as "<name>__<hostname>") — then rename the local
    copy to <name>.migrated.bak (gitignored) so a later deletion in the shared
    store cannot be resurrected by re-migrating. An existing shared file that
    fails to parse aborts the migration untouched (half-synced cloud write);
    the rename is skipped so the next launch retries."""
    name = os.path.basename(path)
    if name in _migrated or not is_shared(path):
        return
    _migrated.add(name)
    local = os.path.join(_BASE_DIR, name)
    if not os.path.exists(local):
        return
    try:
        with open(local, encoding="utf-8") as f:
            local_data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return  # unreadable local copy — leave it for the user to inspect
    if not isinstance(local_data, dict):
        return
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                shared_data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return  # half-synced shared file — do NOT overwrite, retry next launch
        if not isinstance(shared_data, dict):
            return
        if union_stores(shared_data, local_data, machine_label()):
            save_store(path, shared_data)
    else:
        save_store(path, local_data)
    try:
        os.replace(local, local + ".migrated.bak")
    except OSError:
        pass


def absorb_conflict_forks(path, data):
    """Fold OneDrive conflict forks (<stem>-<Computer><ext> beside the main
    file) into `data` via key-level union, then delete each absorbed fork (the
    delete syncs, clearing it everywhere). Returns True when `data` changed —
    the caller is responsible for saving and refreshing its UI. Unreadable
    forks are kept for a retry at the next call; only shared stores are
    touched (a repo-root fallback never has OneDrive forks, and globbing
    there could catch unrelated files)."""
    if not is_shared(path):
        return False
    stem, ext = os.path.splitext(os.path.basename(path))
    changed = False
    for fork in sorted(glob.glob(os.path.join(os.path.dirname(path), stem + "-*" + ext))):
        try:
            with open(fork, encoding="utf-8") as f:
                fork_data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue  # half-synced — retry on the next absorb
        if isinstance(fork_data, dict):
            label = os.path.basename(fork)[len(stem) + 1:-len(ext)]
            label = re.sub(r"[^A-Za-z0-9._ -]+", "_", label).strip(" _.") or "fork"
            if union_stores(data, fork_data, label):
                changed = True
        try:
            os.remove(fork)
        except OSError:
            pass
    return changed
