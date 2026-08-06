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
  round-trips byte-exactly (writer appends exactly one trailing newline,
  reader strips exactly one), so json-era and file-era entries compare
  equal in union_stores.
"""

import glob
import json
import os
import platform
import re
import shutil
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
    File Provider volume on macOS would be EXDEV. Best-effort: on any
    failure the history survives locally and the next launch retries."""
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
    back); the body is written verbatim plus exactly one trailing newline,
    which _parse_skill_md strips again — the round-trip is byte-exact."""
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
    with open(path, encoding="utf-8-sig") as f:  # tolerate a hand-added BOM
        return f.read()


def _write_skill_file(dirpath, name, entry):
    """Write one skill's SKILL.md atomically, diff-aware (an identical file
    is left untouched — no OneDrive churn). The target folder is the
    sanitized name; if that folder already belongs to a DIFFERENT skill
    (its frontmatter name disagrees), a numbered sibling is used instead."""
    sub = base = _skill_dirname(name)
    n = 2
    while True:
        d = os.path.join(dirpath, sub)
        md = os.path.join(d, SKILL_BASENAME)
        if not os.path.isfile(md):
            break
        try:
            existing_name, _ = _entry_from_md(_read_text(md), sub)
        except OSError:
            existing_name = None  # unreadable — claim a sibling, never clobber
        if existing_name == name:
            break
        sub = f"{base}_{n}"
        n += 1
    new_text = _serialize_skill_md(name, entry)
    try:
        if os.path.isfile(md) and _read_text(md) == new_text:
            return
    except OSError:
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
    as its own '<name>__<label>' skill folder (the per-file analog of
    union_stores' conflict preservation) and then deleted; a fork with no
    surviving SKILL.md is promoted to be the main file. Returns the
    materialized (name, entry) pairs so the running scan can include them."""
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
        except OSError:
            return extras  # can't compare this pass — retry at the next scan
    for fork in forks:
        try:
            fork_text = _read_text(fork)
        except OSError:
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
                variant = f"{main_name}__{label}"
                _write_skill_file(dirpath, variant, fork_entry)
                extras.append((variant, fork_entry))
        try:
            os.remove(fork)
        except OSError:
            pass
    return extras


def _scan_skills_tree(dirpath):
    """{name: entry} from every <dirpath>/<sub>/SKILL.md, healing file forks
    along the way. Unreadable files are skipped for this session (never
    deleted). Two folders claiming the same frontmatter name keep both —
    the later one surfaces as '<name>__<folder>' for the user to reconcile."""
    skills = {}
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
            except OSError:
                name = None
            if name is not None:
                if name in skills and skills[name] != entry:
                    name = f"{name}__{sub}"
                skills[name] = entry
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


def delete_skill_tree_entry(dirpath, name):
    """Remove the folder(s) whose SKILL.md frontmatter name matches `name`.
    The folder is the skill — bundled resource files are removed with it."""
    if not os.path.isdir(dirpath):
        return
    try:
        subs = os.listdir(dirpath)
    except OSError:
        return
    for sub in subs:
        d = os.path.join(dirpath, sub)
        md = os.path.join(d, SKILL_BASENAME)
        if not os.path.isfile(md):
            continue
        try:
            fname, _ = _entry_from_md(_read_text(md), sub)
        except OSError:
            continue
        if fname == name:
            shutil.rmtree(d, ignore_errors=True)
