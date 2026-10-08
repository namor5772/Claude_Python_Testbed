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
- resolve_costlog() puts the per-run API cost log in the same shared dir,
  but as ONE FILE PER MACHINE (APICostLog_<machine>.txt): an append-only log
  can't be key-level-unioned, so two machines must never write the same
  synced file — instead each appends only to its own, OneDrive syncs them
  all side by side, and the Cost Log viewers aggregate the folder. Unlike
  the stores, its one-shot repo→shared migration runs AT RESOLVE TIME (there
  is no load step to hang it on), claimed atomically by the local-file
  rename so concurrently-launching apps migrate exactly once.
- resolve_chats_dir() (2026-10-08) puts MyAgent's chat files — every run's
  <name>.json + .txt and the code-interpreter outputs — in <shared>/
  saved_chats, one folder for every machine: each run's files are named
  <Instruction>_<timestamp>, so machines never write the same name, and
  the transcript the cost log's CHAT column names can be opened anywhere.
  Its migration (migrate_local_chats, run by the app on a background
  thread at launch — not at resolve time, since the first pass moves a
  few hundred files) takes ONLY MyAgent's chats from the repo-root folder:
  SelfBot writes the same folder and stays per machine (its duo chats are
  the ones git tracks), told apart by content — a MyAgent chat carries
  agent_instruction_name at its top level — and never overwrites.
- The SKILLS library is the one store that is NOT a JSON file (since
  2026-08-07): skills are hand-authored prose documents, so each lives as
  its own Anthropic-Agent-Skills-shaped file — <shared>/skills/<Name>/
  SKILL.md with a small frontmatter block (name/description/mode) over the
  markdown content. Per-file storage aligns the data unit with OneDrive's
  sync unit: two machines editing different skills can no longer conflict
  at all, and a same-skill conflict forks ONE file (SKILL-<Computer>.md),
  absorbed by _scan_skills_tree the same way absorb_conflict_forks heals
  the JSON stores (identical fork → deleted; different → materialized as a
  "<name>__<label>" sibling skill for the user to reconcile in the UI).
  load_skills_tree runs the one-shot skills.json→tree migration (via
  load_store, so the legacy repo-root union still composes) and then scans;
  save_skills_tree is diff-aware (unchanged files are not rewritten — no
  OneDrive churn) and WRITE-ONLY — it never deletes folders, so a stale
  in-memory dict can't wipe a skill another machine just synced in;
  explicit deletion goes through delete_skill_tree_entry, called only from
  the UI/tool delete actions. The folder is the skill: bundled resource
  files ride along and are removed with it. Unknown frontmatter keys are
  tolerated on read but dropped if the entry is rewritten. The body
  round-trips exactly when it has no trailing newline (the writer ends it
  with one, the reader strips one); a body ending in a newline is
  normalized once and then stable — so a json-era entry whose content ends
  in "\\n" compares UNequal to its file-era twin in union_stores.
"""

import errno
import glob
import json
import os
import platform
import re
import shutil
import stat
import sys
import tempfile
import threading
import time

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


def _ensured_shared_dir():
    """The usable shared dir (created if needed), or None → use the repo
    root. THE fallback policy: resolve_store and resolve_costlog both route
    through this, so the stores and the cost log can never diverge on where
    runtime files live."""
    shared = _shared_dir()
    if not shared:
        return None
    try:
        os.makedirs(shared, exist_ok=True)
    except OSError:
        return None
    return shared


def resolve_store(filename):
    """Path for a shared store file: <shared dir>/<filename> when a shared dir
    is available (env override or OneDrive), else <repo root>/<filename>."""
    shared = _ensured_shared_dir()
    if not shared:
        return os.path.join(_BASE_DIR, filename)
    return os.path.join(shared, filename)


def is_shared(path):
    """True when the resolved store lives outside the repo root (i.e. the
    OneDrive / override dir is in use)."""
    return os.path.dirname(os.path.abspath(path)) != _BASE_DIR


def machine_label():
    """Short hostname-derived label used to name conflicting variants."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", platform.node() or "").strip("_.") or "local"


COSTLOG_BASENAME = "APICostLog.txt"

# Marker suffix a migrated-away local file is renamed to (stores and cost log
# alike) so deletions in the shared copy can't be resurrected by a re-merge.
_MIGRATED_SUFFIX = ".migrated.bak"


def resolve_costlog():
    """Path this machine's API cost log is written to: with a shared dir,
    <shared>/APICostLog_<machine>.txt — every machine's log syncs side by
    side and any machine can total ALL of them — else the classic repo-root
    APICostLog.txt. Runs the one-shot repo→shared migration of this
    machine's legacy log (so pre-move history counts in the aggregate)."""
    shared = _ensured_shared_dir()
    if not shared:
        return os.path.join(_BASE_DIR, COSTLOG_BASENAME)
    stem, ext = os.path.splitext(COSTLOG_BASENAME)
    path = os.path.join(shared, f"{stem}_{machine_label()}{ext}")
    _migrate_costlog(os.path.join(_BASE_DIR, COSTLOG_BASENAME), path)
    return path


def _migrate_costlog(local, shared_path):
    """One-shot: fold the legacy repo-root cost log into this machine's
    shared file, renaming the local copy to APICostLog.txt.migrated.bak (the
    stores' migration marker). The rename is the atomic CLAIM — it happens
    before the append, so two apps launching at once (LaunchSelfBot.bat
    starts a pair) migrate exactly once: the loser's rename raises and it
    backs off. The lines are appended, not copied — only this machine ever
    writes its label's file, but appending stays correct even if one exists
    (the viewers sort rows by timestamp, so in-file order is cosmetic). The
    rotation archive (.old) rides along by copy — os.replace into OneDrive's
    File Provider volume on macOS would be EXDEV. Best-effort: a read or
    claim failure leaves the local log in place for the next launch to
    retry; an append failing AFTER the claim is not retried — the history
    is then only in the .migrated.bak, to fold in by hand."""
    if os.path.exists(local):
        try:
            with open(local, encoding="utf-8") as f:
                content = f.read()
            os.replace(local, local + _MIGRATED_SUFFIX)
        except OSError:
            return
        try:
            if content and not content.endswith("\n"):
                content += "\n"
            if content:
                with open(shared_path, "a", encoding="utf-8") as f:
                    f.write(content)
        except OSError:
            pass  # history is preserved in the .migrated.bak
    old_local = local + ".old"
    if not os.path.exists(old_local):
        return
    try:
        if not os.path.exists(shared_path + ".old"):
            with open(old_local, encoding="utf-8") as f:
                old_content = f.read()
            with open(shared_path + ".old", "w", encoding="utf-8") as f:
                f.write(old_content)
        os.replace(old_local, old_local + _MIGRATED_SUFFIX)
    except OSError:
        pass


CHATS_DIRNAME = "saved_chats"
# A chat file touched within this many seconds is left where it is by
# migrate_local_chats: an instance of the pre-move code still running
# rewrites its chat every PERIODIC_SAVE_MS (state_mixin._periodic_save; 10 s,
# 5 s before 2026-10-08), and the next
# launch folds the finished file in.
CHATS_SETTLE_SECS = 120


def resolve_chats_dir():
    """Directory MyAgent writes its chat files to — <name>.json + <name>.txt
    per run, and the code-interpreter outputs (since 2026-10-08):
    <shared dir>/saved_chats when a shared dir is available (env override or
    OneDrive), so every machine's transcripts sync into ONE folder and the
    file the cost log's CHAT column names can be opened from any of them —
    else the classic <repo root>/saved_chats. Not created here: the first
    save creates it (OneDrive garbage-collects an empty new dir, see
    save_store). SelfBot keeps its own repo-root folder on purpose — its
    chats are per machine, and its duo chats are the files git tracks."""
    shared = _ensured_shared_dir()
    return os.path.join(shared if shared else _BASE_DIR, CHATS_DIRNAME)


def _is_myagent_chat(path):
    """True for a chat file MyAgent wrote: a JSON object carrying
    agent_instruction_name at its top level (chat_mixin._auto_save_on_close).
    SelfBot's chats — the same folder, the same <name>.json + .txt shape —
    carry system_prompt_name instead; an unparseable file is nobody's."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and "agent_instruction_name" in data


def _same_bytes(a, b):
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                ca, cb = fa.read(1 << 16), fb.read(1 << 16)
                if ca != cb:
                    return False
                if not ca:
                    return True
    except OSError:
        return False


def _move_file(src, dst):
    """Move src to dst, which the caller found free: a rename on one volume,
    else — the repo and OneDrive's File Provider volume on macOS are
    different filesystems, EXDEV, as the cost-log migration found — a copy
    into a temporary file BESIDE dst (mkstemp, exclusive: a dot-prefixed
    `.<name>.<random>.part`), the modification time carried across so the
    share lists the chats in their order, then ONE rename onto the final
    name — atomic within the share's volume — then the delete of src. The
    final name therefore never exists until it is complete: a process that
    dies mid-copy (a headless Heartbeat child ending while its fold-in
    still runs) leaves only the .part file, which _sweep_part_files removes
    once it is CHATS_PART_STALE_SECS old; a copy that FAILS is removed at
    once. A dst that appeared meanwhile is never replaced."""
    try:
        os.rename(src, dst)
        return
    except OSError:
        pass
    st = os.stat(src)
    fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(dst)}.", suffix=".part",
                               dir=os.path.dirname(dst) or ".")
    try:
        with os.fdopen(fd, "wb") as fdst, open(src, "rb") as fsrc:
            shutil.copyfileobj(fsrc, fdst, 1 << 20)
        try:
            os.utime(tmp, (st.st_atime, st.st_mtime))
        except OSError:
            pass
        if os.path.exists(dst):
            raise FileExistsError(errno.EEXIST, "destination appeared meanwhile", dst)
        os.rename(tmp, dst)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    os.remove(src)


# A `.part` file in the share older than this is a copy whose process died
# (_move_file) and is swept; a younger one may be a sibling instance's live
# copy, or another machine's arriving through the sync — left alone.
CHATS_PART_STALE_SECS = 3600


def _sweep_part_files(shared_dir, now):
    """Remove the `.<name>.<random>.part` temp files a cross-volume copy
    leaves when its process dies mid-copy (_move_file), once they are
    CHATS_PART_STALE_SECS old. Best-effort; returns the count removed."""
    try:
        names = os.listdir(shared_dir)
    except OSError:
        return 0
    swept = 0
    for fn in names:
        if not (fn.startswith(".") and fn.endswith(".part")):
            continue
        path = os.path.join(shared_dir, fn)
        try:
            if now - os.path.getmtime(path) >= CHATS_PART_STALE_SECS:
                os.remove(path)
                swept += 1
        except OSError:
            pass
    return swept


def _chat_slot(shared_dir, stem, ext, src, label):
    """Where the local chat file src (<stem><ext>) lands in shared_dir: its
    own name when free; None when a file of that name already holds the
    SAME bytes (the local copy is redundant — a half-done earlier pass);
    else <stem>__<label>, numbered <stem>__<label>_2, _3 … while that too is
    taken by other content — the stores' conflict rule (union_stores,
    _variant_slot)."""
    cand, n = stem, 1
    while True:
        dst = os.path.join(shared_dir, cand + ext)
        if not os.path.exists(dst):
            return dst
        if _same_bytes(src, dst):
            return None
        n += 1
        cand = f"{stem}__{label}" if n == 2 else f"{stem}__{label}_{n - 1}"


def _settle_chat_file(shared_dir, src, stem, ext, label, summary):
    """Move one local chat file into shared_dir under _chat_slot's name (or
    absorb it there); returns the stem it lives under in the share."""
    dst = _chat_slot(shared_dir, stem, ext, src, label)
    if dst is None:
        os.remove(src)
        summary["absorbed"] += 1
        return stem
    _move_file(src, dst)
    summary["moved"] += 1
    return os.path.splitext(os.path.basename(dst))[0]


def migrate_local_chats(local_dir, shared_dir, label=None, now=None):
    """Move this machine's MyAgent chats from the repo-root folder into the
    shared one — the history following the move, as the stores' and the
    cost log's did. Only MyAgent's files go: a <stem>.json whose top level
    carries agent_instruction_name (_is_myagent_chat) and its <stem>.txt
    twin; SelfBot's chats, the ci_output_* files (either app's), an
    unparseable file and a .txt without its .json all stay. Nothing is ever
    overwritten (_chat_slot: same bytes → the local copy is absorbed, other
    content → <stem>__<machine label>, numbered). A chat touched within
    CHATS_SETTLE_SECS waits for the next launch. Per chat best-effort: an
    error leaves it local for the next launch, which repeats the pass —
    idempotent, a folder with no MyAgent chat moves nothing. The .txt is
    moved after its .json and lands under the same stem (its own collision
    check, so even an orphan .txt in the share is never overwritten).
    Stale `.part` temp files in the share — a cross-volume copy whose
    process died (_move_file) — are swept first (_sweep_part_files).
    Returns the counts {"moved", "absorbed", "waiting", "errors"} — files,
    not chats; a .json and its .txt count twice."""
    summary = {"moved": 0, "absorbed": 0, "waiting": 0, "errors": 0}
    try:
        same = os.path.samefile(local_dir, shared_dir)
    except OSError:
        same = (os.path.normcase(os.path.abspath(local_dir))
                == os.path.normcase(os.path.abspath(shared_dir)))
    if same:
        return summary
    now = time.time() if now is None else now
    if os.path.isdir(shared_dir):
        _sweep_part_files(shared_dir, now)
    if not os.path.isdir(local_dir):
        return summary
    label = label or machine_label()
    try:
        names = sorted(os.listdir(local_dir))
    except OSError:
        return summary
    for fn in names:
        stem, ext = os.path.splitext(fn)
        if ext.lower() != ".json" or fn.startswith("ci_output_"):
            continue
        src = os.path.join(local_dir, fn)
        if not os.path.isfile(src) or not _is_myagent_chat(src):
            continue
        twin = os.path.join(local_dir, stem + ".txt")
        if not os.path.isfile(twin):
            twin = None
        try:
            if any(now - os.path.getmtime(p) < CHATS_SETTLE_SECS
                   for p in (src, twin) if p):
                summary["waiting"] += 1
                continue
            os.makedirs(shared_dir, exist_ok=True)
            used = _settle_chat_file(shared_dir, src, stem, ".json", label, summary)
            if twin:
                _settle_chat_file(shared_dir, twin, used, ".txt", label, summary)
        except OSError:
            summary["errors"] += 1
    return summary


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
    except (OSError, ValueError):   # ValueError: bad JSON, or bytes that are not UTF-8
        return {}
    return data if isinstance(data, dict) else {}


def store_unreadable(path):
    """True when the store file EXISTS but cannot be read or parsed as a JSON
    object — a half-synced cloud write, a hand edit's typo, a sharing
    violation, a cloud-only placeholder offline. load_store serves {} for such
    a file, and a caller must never save anything built on that {} over it:
    the whole library would be replaced by the stand-in (and synced to every
    machine). A missing file, or a valid empty object, is NOT unreadable."""
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            return not isinstance(json.load(f), dict)
    except (OSError, ValueError):
        return True


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
    except (OSError, ValueError):
        return  # unreadable local copy — leave it for the user to inspect
    if not isinstance(local_data, dict):
        return
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                shared_data = json.load(f)
        except (OSError, ValueError):
            return  # half-synced shared file — do NOT overwrite, retry next launch
        if not isinstance(shared_data, dict):
            return
        if union_stores(shared_data, local_data, machine_label()):
            save_store(path, shared_data)
    else:
        save_store(path, local_data)
    try:
        os.replace(local, local + _MIGRATED_SUFFIX)
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
        except (OSError, ValueError):
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


# ── Skills tree: one SKILL.md file per skill (Agent-Skills-shaped) ──────────

SKILLS_DIRNAME = "skills"
SKILL_BASENAME = "SKILL.md"
SKILL_MODES = ("disabled", "enabled", "on_demand")

# Windows device names that cannot be used as file/folder names.
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} \
    | {f"LPT{i}" for i in range(1, 10)}


def resolve_skills_dir():
    """Directory the per-skill SKILL.md tree lives in: <shared dir>/skills
    when a shared dir is available (env override or OneDrive), else
    <repo root>/skills. Not created here — the first write creates it."""
    shared = _ensured_shared_dir()
    return os.path.join(shared if shared else _BASE_DIR, SKILLS_DIRNAME)


def _skill_dirname(name):
    """Filesystem-safe folder name for a skill. The frontmatter `name:` is
    the source of truth; this is only the container's name, so lossy
    sanitization is fine as long as it is deterministic."""
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not safe:
        return "_skill"
    if safe.split(".")[0].upper() in _WIN_RESERVED:
        safe = "_" + safe
    return safe


def _serialize_skill_md(name, entry):
    """SKILL.md text for one skill entry. Frontmatter values are collapsed
    to single physical lines (the parser folds hand-wrapped continuations
    back); the body is written verbatim, ending in one newline (added when
    missing), which _parse_skill_md strips again — a body without a trailing
    newline round-trips exactly; one WITH it loses it once and is then
    stable (pinned by tests/test_skills_tree.py test_round_trip_exact)."""
    lines = ["---", "name: " + " ".join(name.split())]
    desc = " ".join((entry.get("description") or "").split())
    if desc:
        lines.append("description: " + desc)
    lines.append("mode: " + entry.get("mode", "disabled"))
    lines.append("---")
    body = entry.get("content", "")
    if body and not body.endswith("\n"):
        body += "\n"
    return "\n".join(lines) + "\n\n" + body


def _parse_skill_md(text):
    """(frontmatter dict, body) from SKILL.md text. Tolerant: no opening or
    closing --- fence → the whole text is the body. Key lines are
    `key: value`; a non-key line inside the fence folds into the previous
    key (hand-wrapped descriptions). Exactly one blank separator line and
    one trailing body newline are the serializer's — both are removed."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta, key, closed = {}, None, False
    i = 1
    while i < len(lines):
        raw = lines[i]
        if raw.strip() == "---":
            closed = True
            i += 1
            break
        m = re.match(r"([A-Za-z][A-Za-z0-9_-]*)[ \t]*:[ \t]?(.*)$", raw)
        if m:
            key = m.group(1).strip().lower()
            meta[key] = m.group(2).rstrip("\r").strip()
        elif key is not None and raw.strip():
            meta[key] = (meta[key] + " " + raw.strip()).strip()
        i += 1
    if not closed:
        return {}, text
    if i < len(lines) and not lines[i].strip():
        i += 1
    body = "\n".join(lines[i:])
    if body.endswith("\n"):
        body = body[:-1]
    return meta, body


def _entry_from_md(text, fallback_name):
    """(skill name, entry dict) from SKILL.md text. Unknown frontmatter keys
    are ignored; a missing/invalid mode is 'disabled'; a missing name falls
    back to the folder name."""
    meta, content = _parse_skill_md(text)
    name = " ".join((meta.get("name") or "").split()) or fallback_name
    mode = (meta.get("mode") or "").strip().lower()
    entry = {"content": content,
             "mode": mode if mode in SKILL_MODES else "disabled"}
    desc = (meta.get("description") or "").strip()
    if desc:
        entry["description"] = desc
    return name, entry


def _read_text(path):
    """A SKILL.md's text, newlines translated as text mode does. UTF-8 (a
    hand-added BOM tolerated) is what every writer here produces; a file some
    editor saved otherwise is read anyway instead of failing the whole tree —
    UTF-16 by its BOM (PowerShell 5.1's Out-File / >), else Windows ANSI
    (cp1252: 5.1's Set-Content). Before this, one such file's
    UnicodeDecodeError — not an OSError, which every caller catches — crashed
    MyAgent and SelfBot at startup on every machine sharing the tree. Still
    raises UnicodeDecodeError (a ValueError) for bytes no candidate decodes;
    the callers treat that like an unreadable file."""
    with open(path, "rb") as f:
        data = f.read()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _real_basename(dirpath, sub):
    """The ACTUAL on-disk entry of `dirpath` matching `sub` case-insensitively
    (its true casing), or None. Windows and default macOS filesystems fold
    case, so os.path checks on 'test-skill' silently resolve into an existing
    'Test-skill' — path existence alone cannot tell whose folder it is."""
    try:
        for entry in os.listdir(dirpath):
            if entry.lower() == sub.lower():
                return entry
    except OSError:
        pass
    return None


def _write_skill_file(dirpath, name, entry):
    """Write one skill's SKILL.md atomically, diff-aware (an identical file
    is left untouched — no OneDrive churn). The target folder is the
    sanitized name; if that path already belongs to a DIFFERENT skill —
    its frontmatter name disagrees, OR the directory exists under a
    different CASING (case-folding filesystems resolve 'test-skill' into an
    existing 'Test-skill'; writing there would mix two skills' folders,
    observed live 2026-08-07) — a numbered sibling is used instead."""
    sub = base = _skill_dirname(name)
    n = 2
    while True:
        d = os.path.join(dirpath, sub)
        md = os.path.join(d, SKILL_BASENAME)
        if os.path.isfile(md):
            try:
                existing_name, _ = _entry_from_md(_read_text(md), sub)
            except (OSError, ValueError):
                existing_name = None  # unreadable — claim a sibling, never clobber
            if existing_name == name:
                break
        elif os.path.isdir(d) and _real_basename(dirpath, sub) not in (None, sub):
            pass  # a differently-cased folder occupies this path — not ours
        else:
            break  # free, or an exact-cased SKILL.md-less husk we can adopt
        sub = f"{base}_{n}"
        n += 1
    new_text = _serialize_skill_md(name, entry)
    try:
        if os.path.isfile(md) and _read_text(md) == new_text:
            return
    except (OSError, ValueError):
        pass
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=SKILL_BASENAME + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_text)
        os.replace(tmp, md)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _absorb_skill_file_forks(d, dirpath):
    """Heal OneDrive conflict forks of one skill's SKILL.md (SKILL-<Computer>.md
    siblings). An identical fork is deleted; a different one is materialized
    as its own '<name>__<label>' skill folder — numbered like union_stores'
    variants when a DIFFERENT preserved variant already holds that name —
    and then deleted; a fork with no surviving SKILL.md is promoted to be the
    main file. Returns the newly materialized (name, entry) pairs so the
    running scan can include them."""
    extras = []
    stem, ext = os.path.splitext(SKILL_BASENAME)
    forks = sorted(glob.glob(os.path.join(d, stem + "-*" + ext)))
    if not forks:
        return extras
    md = os.path.join(d, SKILL_BASENAME)
    main_text = None
    if os.path.isfile(md):
        try:
            main_text = _read_text(md)
        except (OSError, ValueError):
            return extras  # can't compare this pass — retry at the next scan
    for fork in forks:
        try:
            fork_text = _read_text(fork)
        except (OSError, ValueError):
            continue  # half-synced — retry at the next scan
        if main_text is None:
            fd, tmp = tempfile.mkstemp(dir=d, prefix=SKILL_BASENAME + ".", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(fork_text)
                os.replace(tmp, md)
                main_text = fork_text
            except OSError:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                continue  # promotion failed — keep the fork for a retry
        else:
            main_name, main_entry = _entry_from_md(main_text, os.path.basename(d))
            fork_name, fork_entry = _entry_from_md(fork_text, os.path.basename(d))
            if fork_entry != main_entry or fork_name != main_name:
                label = os.path.basename(fork)[len(stem) + 1:-len(ext)]
                label = re.sub(r"[^A-Za-z0-9._ -]+", "_", label).strip(" _.") or "fork"
                variant, needs_write = _variant_slot(dirpath, f"{main_name}__{label}",
                                                     fork_entry)
                if needs_write:
                    # A NEW folder, which this scan's listing never reaches:
                    # hand it to the scan. (An identical variant already on
                    # disk surfaces through the scan itself.)
                    _write_skill_file(dirpath, variant, fork_entry)
                    extras.append((variant, fork_entry))
        try:
            os.remove(fork)
        except OSError:
            pass
    return extras


def _same_resources(a, b):
    """True when skill folders `a` and `b` bundle the same resource files,
    byte for byte (usually both none). False when either is unknown."""
    if a is None or b is None:
        return False
    listed = list(_iter_skill_resources(a))
    if listed != list(_iter_skill_resources(b)):
        return False
    try:
        for rel in listed:
            with open(os.path.join(a, rel), "rb") as fa, open(os.path.join(b, rel), "rb") as fb:
                if fa.read() != fb.read():
                    return False
    except OSError:
        return False
    return True


def _write_skill_md_in(folder, name, entry):
    """Atomically (re)write `folder`/SKILL.md for skill `name`. False when the
    write failed (the folder is left as it was)."""
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=SKILL_BASENAME + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_serialize_skill_md(name, entry))
        os.replace(tmp, os.path.join(folder, SKILL_BASENAME))
        return True
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def _claim_duplicate(folder, variant, entry, taken):
    """Give a folder whose frontmatter name another folder already holds a
    name of its own: `variant` (numbered while `taken` has it), written into
    its SKILL.md. Returns the name — kept in memory even if the write failed."""
    base, n = variant, 2
    while variant in taken:
        variant, n = f"{base}_{n}", n + 1
    _write_skill_md_in(folder, variant, entry)
    return variant


def _variant_slot(dirpath, base, entry):
    """(name, needs_write) for preserving a differing conflict fork: the first
    of `base`, `base_2`, … that no skill folder holds (write it there), or the
    one that already holds exactly `entry` (nothing to write). A second fork
    with the same label used to OVERWRITE the variant preserved from the
    first, whose folder the scan then deleted as a duplicate."""
    n = 1
    while True:
        name = base if n == 1 else f"{base}_{n}"
        folder = _skill_dir_for(dirpath, name)
        if folder is None:
            return name, True
        try:
            _, held = _entry_from_md(_read_text(os.path.join(folder, SKILL_BASENAME)),
                                     os.path.basename(folder))
        except (OSError, ValueError):
            held = None
        if held == entry:
            return name, False
        n += 1


def _scan_skills_tree(dirpath):
    """{name: entry} from every <dirpath>/<sub>/SKILL.md, healing file forks
    along the way. Unreadable files are skipped for this session (never
    deleted). Two folders claiming the same frontmatter name keep both —
    the later one becomes '<name>__<folder>', written into its SKILL.md, for
    the user to reconcile — unless it is an identical copy with the same
    bundled files, which is removed."""
    skills = {}
    folders = {}   # name -> the folder it was read from
    if not os.path.isdir(dirpath):
        return skills
    try:
        subs = sorted(os.listdir(dirpath))
    except OSError:
        return skills
    for sub in subs:
        d = os.path.join(dirpath, sub)
        if not os.path.isdir(d):
            continue
        extras = _absorb_skill_file_forks(d, dirpath)
        md = os.path.join(d, SKILL_BASENAME)
        if os.path.isfile(md):
            try:
                name, entry = _entry_from_md(_read_text(md), sub)
            except (OSError, ValueError):
                name = None
            if (name is not None and name in skills and skills[name] == entry
                    and _same_resources(folders.get(name), d)):
                # A second folder holding an IDENTICAL entry — and the same
                # bundled files, usually none — is junk, e.g. the numbered
                # sibling a case-collision left behind (live 2026-08-07:
                # Test-skill + test-skill_2 both said name: test-skill).
                # Removed like an identical conflict fork. Differing files
                # keep it (below): resource files are part of the skill.
                _rmtree_verified(d)
            elif name is not None:
                if name in skills:
                    # A second folder claiming a taken name surfaces as its
                    # own skill '<name>__<folder>', and that name is WRITTEN
                    # into its SKILL.md: the folder then IS that skill, so its
                    # delete, the resource tools and a rename act on it. (No
                    # folder held the variant's name before: deleting it
                    # removed nothing, deleting the original removed both.)
                    name = _claim_duplicate(d, f"{name}__{sub}", entry, skills)
                else:
                    # Heal folder naming: a case-only mismatch (folder
                    # Test-skill holding name test-skill) or a stranded
                    # numbered sibling moves to the natural folder once that
                    # slot is genuinely free — real==sub means the "occupant"
                    # is this very folder under other casing, i.e. a pure
                    # case-fix rename.
                    natural = _skill_dirname(name)
                    if sub != natural:
                        real = _real_basename(dirpath, natural)
                        if real is None or real == sub:
                            try:
                                os.rename(d, os.path.join(dirpath, natural))
                                d = os.path.join(dirpath, natural)
                            except OSError:
                                pass
                skills[name] = entry
                folders[name] = d
        else:
            # SKILL.md-less folder: a husk from a blocked delete, or a folder
            # OneDrive is still materializing / a user is hand-authoring a
            # future skill. Sweep only when it is EMPTY (no files anywhere —
            # nothing to lose) AND not brand-new (>60 s), so an incoming
            # sync or work-in-progress is never touched.
            try:
                has_files = any(files for _r, _d, files in os.walk(d))
                aged = (time.time() - os.path.getmtime(d)) > 60
            except OSError:
                has_files, aged = True, False
            if not has_files and aged:
                _rmtree_verified(d, attempts=1, defer=False)
        for extra_name, extra_entry in extras:
            skills.setdefault(extra_name, extra_entry)
    return skills


def _migrate_json_skills(dirpath):
    """One-shot per process: split a legacy skills.json (shared or repo-root
    — load_store composes the old repo→shared union underneath) into the
    per-skill tree, then rename it to skills.json.migrated.bak so entries
    later deleted from the tree cannot resurrect. Existing tree folders win
    their names; a genuinely different json entry survives as '<name>__json'
    (union_stores semantics). Pending skills.json conflict forks are folded
    in first, so nothing OneDrive was still reconciling is dropped."""
    if "skills-tree" in _migrated:
        return
    _migrated.add("skills-tree")
    json_path = resolve_store("skills.json")
    data = load_store(json_path)
    absorb_conflict_forks(json_path, data)
    data = {n: e for n, e in data.items() if isinstance(e, dict)}
    if not data:
        return
    for sdata in data.values():
        if "mode" not in sdata:
            sdata["mode"] = "enabled" if sdata.pop("enabled", False) else "disabled"
    existing = _scan_skills_tree(dirpath)
    merged = dict(existing)
    union_stores(merged, data, "json")
    for name, entry in merged.items():
        if name not in existing:
            _write_skill_file(dirpath, name, entry)
    try:
        os.replace(json_path, json_path + _MIGRATED_SUFFIX)
    except OSError:
        pass


def load_skills_tree(dirpath):
    """The skills library as {name: {content, mode[, description]}}. Runs the
    one-shot skills.json migration, then scans the tree (healing per-file
    conflict forks). Every entry always carries a valid mode."""
    _migrate_json_skills(dirpath)
    return _scan_skills_tree(dirpath)


def save_skills_tree(dirpath, skills):
    """Write every entry's SKILL.md (atomic per file, unchanged files left
    untouched). WRITE-ONLY by design: folders absent from `skills` are NOT
    deleted, so a stale in-memory dict cannot wipe a skill another machine
    synced in — deletion is an explicit user action via
    delete_skill_tree_entry."""
    try:
        os.makedirs(dirpath, exist_ok=True)
    except OSError:
        return
    for name, entry in skills.items():
        if isinstance(entry, dict):
            try:
                _write_skill_file(dirpath, name, entry)
            except OSError:
                pass  # best-effort per file; the next save retries


def _sweep_dir_later(path, tries=40, interval=3.0):
    """Background daemon that keeps retrying a blocked directory removal
    after the caller has returned. OneDrive's directory handle outlives any
    reasonable inline wait (observed live 2026-08-07, three times: the
    files delete instantly, the rmdir stays blocked — and a rename is
    refused by the same sharing check, so there is no instant workaround).
    The default budget is ~2 minutes; a husk that outlives the process is
    finished by the aged-empty sweep in _scan_skills_tree next launch."""
    def _sweep():
        for _ in range(tries):
            time.sleep(interval)
            if not os.path.exists(path):
                return
            try:
                shutil.rmtree(path)
            except OSError:
                continue
    threading.Thread(target=_sweep, daemon=True, name="skill-husk-sweep").start()


def _rmtree_verified(path, attempts=3, defer=True):
    """shutil.rmtree that survives the Windows/OneDrive handle race (observed
    live 2026-08-07): the sync client holds a directory handle while
    processing recent activity, so rmtree removes the files but the final
    rmdir fails — with the old ignore_errors=True that silently left an
    empty husk folder behind (SKILL.md gone, directory there). Clears
    read-only attributes and retries briefly inline (~0.75 s — the Skills
    Manager DELETE runs on the Tk main thread, so long inline waits freeze
    the UI); when the directory STILL can't go and defer=True, hands it to
    _sweep_dir_later, which retries in the background until the handle
    releases. Returns whether the path is gone RIGHT NOW — with a sweeper
    running, False still means 'seconds away', and the load-time
    aged-empty-husk sweep is the cross-launch backstop."""
    for attempt in range(attempts):
        if not os.path.exists(path):
            return True
        # Clear read-only on the root, every subdir AND every file: OneDrive
        # stamps a stuck-delete directory ReadOnly (+ReparsePoint), and a
        # read-only DIRECTORY defeats rmtree/rmdir on Windows — that, not
        # the transient handle, is what made husks survive later retries
        # (live test-skill-bak husk, 2026-08-07: empty, aged, ReadOnly).
        # Directories get S_IRWXU, files S_IREAD|S_IWRITE — NOT bare S_IWRITE
        # for both: Windows chmod honors only the write bit (either mode
        # clears ReadOnly identically), but on POSIX the mode is literal, and
        # 0o200 on a DIRECTORY strips read+execute — the directory can no
        # longer be listed or traversed, so rmtree fails and every retry
        # re-cripples it (the 2026-08-08 macOS regression: skill deletes left
        # every husk behind and each delete burned the full retry ladder).
        try:
            os.chmod(path, stat.S_IRWXU)
        except OSError:
            pass
        for root, dirs, files in os.walk(path):
            for entry in dirs:
                try:
                    os.chmod(os.path.join(root, entry), stat.S_IRWXU)
                except OSError:
                    pass
            for entry in files:
                try:
                    os.chmod(os.path.join(root, entry), stat.S_IREAD | stat.S_IWRITE)
                except OSError:
                    pass
        try:
            shutil.rmtree(path)
        except OSError:
            pass
        if not os.path.exists(path):
            return True
        if attempt < attempts - 1:
            time.sleep(0.25 * (attempt + 1))
    try:
        os.rmdir(path)
    except OSError:
        pass
    if not os.path.exists(path):
        return True
    if defer:
        _sweep_dir_later(path)
    return False


def delete_skill_tree_entry(dirpath, name):
    """Remove the folder(s) whose SKILL.md frontmatter name matches `name`
    (the folder is the skill — bundled resource files are removed with it).
    Also reclaims a SKILL.md-less husk folder sitting at this name's
    sanitized dirname — the residue of an earlier interrupted delete — so
    husks self-heal the next time their skill is deleted."""
    if not os.path.isdir(dirpath):
        return
    try:
        subs = os.listdir(dirpath)
    except OSError:
        return
    for sub in subs:
        d = os.path.join(dirpath, sub)
        md = os.path.join(d, SKILL_BASENAME)
        if os.path.isfile(md):
            try:
                fname, _ = _entry_from_md(_read_text(md), sub)
            except (OSError, ValueError):
                continue
            if fname == name:
                _rmtree_verified(d)
        elif sub.lower() == _skill_dirname(name).lower() and os.path.isdir(d):
            _rmtree_verified(d)  # husk match is case-insensitive (case-folding FS)


def _skill_dir_for(dirpath, name):
    """The folder currently holding skill `name` — the subfolder whose
    SKILL.md frontmatter name matches, which covers numbered siblings
    (`<dirname>_2`) and legacy folder names alike — or None. The read-only
    twin of delete_skill_tree_entry's resolution loop."""
    if not os.path.isdir(dirpath):
        return None
    try:
        subs = os.listdir(dirpath)
    except OSError:
        return None
    for sub in subs:
        d = os.path.join(dirpath, sub)
        md = os.path.join(d, SKILL_BASENAME)
        if os.path.isfile(md):
            try:
                fname, _ = _entry_from_md(_read_text(md), sub)
            except (OSError, ValueError):
                continue
            if fname == name:
                return d
    return None


def _iter_skill_resources(folder):
    """Yield the bundled resource files of a skill folder as relative paths,
    sorted — everything EXCEPT the root SKILL.md, its `SKILL-<label>.md`
    conflict forks and the writer's `SKILL.md.*.tmp` leftovers. The ONE
    definition of "what counts as a resource", shared by the save-as copy,
    the lister and (by omission) the path validator."""
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        rel_root = os.path.relpath(root, folder)
        for fname in sorted(files):
            if rel_root == os.curdir and _is_skill_md_family(fname):
                continue
            yield fname if rel_root == os.curdir else os.path.join(rel_root, fname)


def _is_skill_md_family(fname):
    """True for the root-level files that are the skill's OWN metadata rather
    than a bundled resource: SKILL.md itself, a `SKILL-<label>.md` OneDrive
    conflict fork, or a `SKILL.md.*.tmp` writer leftover. Case-INsensitive,
    like the filesystems: on Windows / macOS "skill.md" IS SKILL.md (a
    resource write of that name replaced the skill's own file), and the
    healer's SKILL-*.md glob matches "skill-notes.md" too."""
    low, base = fname.lower(), SKILL_BASENAME.lower()
    return (low == base
            or low.startswith(base + ".")
            or (low.startswith("skill-") and low.endswith(".md")))


def skill_dir_for(dirpath, name):
    """Public face of _skill_dir_for — the folder currently holding skill
    `name` (frontmatter-name match), or None. The manage_skills resource
    actions use it for size listings and empty-directory pruning."""
    return _skill_dir_for(dirpath, name)


def list_skill_resources(dirpath, name):
    """The bundled resource files of skill `name`, as sorted relative paths —
    or None when the skill has no folder on disk (never saved yet). The
    manage_skills `list_files` action's backend (2026-09-25)."""
    folder = _skill_dir_for(dirpath, name)
    if folder is None:
        return None
    return list(_iter_skill_resources(folder))


def skill_resource_path(dirpath, name, rel_path):
    """(absolute path, error) for a resource file inside skill `name`'s
    folder — the validation gate of the manage_skills resource actions
    (2026-09-25). The path must be RELATIVE and stay INSIDE the folder:
    absolute paths, drive letters and any `..` component are refused (a
    model-supplied path may be anything), as is the root SKILL.md family
    (SKILL.md is managed by the create/update actions, forks by the healer).
    Backslashes are accepted and normalised, so a Windows-style path from a
    model works on both OSes. Returns (path, None) or (None, reason)."""
    folder = _skill_dir_for(dirpath, name)
    if folder is None:
        return None, (f"skill '{name}' has no folder on disk yet — save the "
                      "skill first")
    rel = (rel_path or "").replace("\\", "/").strip()
    if not rel or rel.endswith("/"):
        return None, "file_path must name a file, not a directory"
    # A leading "/" is refused explicitly: since Python 3.13, os.path.isabs on
    # Windows no longer counts a rooted path without a drive ("/abs/x.txt",
    # "\abs\x.txt") as absolute, so it alone would accept one there.
    if rel.startswith("/") or os.path.isabs(rel) or re.match(r"[A-Za-z]:", rel):
        return None, "file_path must be RELATIVE to the skill's folder"
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return None, "file_path may not leave the skill's folder ('..')"
    # A drive (or stream) colon in ANY component, not only the first: on
    # Windows "sub/Z:evil.txt" joins to Z:evil.txt, outside the folder.
    if any(":" in p for p in parts):
        return None, "file_path must be RELATIVE to the skill's folder (no ':')"
    if len(parts) == 1 and _is_skill_md_family(parts[0]):
        return None, ("SKILL.md (and its conflict forks) are managed by the "
                      "create/update actions, not as resource files")
    return os.path.join(folder, *parts), None


def copy_skill_resources(dirpath, src_name, dst_name):
    """Copy skill `src_name`'s bundled resource files — everything in its
    folder EXCEPT the root SKILL.md, its `SKILL-<label>.md` conflict forks
    and the writer's `SKILL.md.*.tmp` leftovers — into `dst_name`'s folder,
    subdirectories (references/, scripts/, tests/, …) included.

    The completing half of the Skills Manager's save-under-a-new-name
    (2026-09-25): the save itself writes only the new SKILL.md, but THE
    FOLDER IS THE SKILL — bundled files ride along on delete and sync — so
    a "copy" that left them behind was not a copy of the skill. Existing
    destination files are never overwritten (a later re-save must not
    clobber files the new skill has since grown), and each file is
    best-effort like save_skills_tree's writes. Returns the copied paths
    relative to the destination folder, sorted — [] when either skill's
    folder is missing, both names resolve to the same folder, or the source
    carries no resources."""
    src = _skill_dir_for(dirpath, src_name)
    dst = _skill_dir_for(dirpath, dst_name)
    if src is None or dst is None or os.path.realpath(src) == os.path.realpath(dst):
        return []
    copied = []
    for rel in _iter_skill_resources(src):
        target = os.path.join(dst, rel)
        if os.path.exists(target):
            continue
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(os.path.join(src, rel), target)
        except OSError:
            continue
        copied.append(rel)
    return copied
