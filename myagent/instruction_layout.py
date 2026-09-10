"""Sections and row order for the Load Instruction list (2026-09-11).

The saved instructions in agent_instructions.json are a name-keyed dict, so
the store itself has no order. The Instruction Editor's Load Instruction list
shows them the way OneNote shows pages in sections: every instruction (a
"page") sits in a named section, the sections are listed in a fixed order,
and the pages within a section keep the order the user gave them.

That layout is stored ON THE ENTRIES, not beside them: an entry may carry

    "section": "Banking"    # absent (or "") for an unfiled page
    "order": 7              # its position in one flat sequence of all pages

so it rides along with the instruction through the OneDrive sync and the
key-level union that heals conflict forks, and every other reader of the
store — the manage_instructions tool, Heartbeat.py — keeps working
unchanged: an entry written without the two keys simply lands unfiled, at the
end, in alphabetical order.

A section exists exactly while some page names it (there is no separate
section list to keep consistent), sections appear in the order of their
first page, and the unfiled pages are always listed last. Every edit here
rewrites the order numbers of ALL pages from the displayed sequence
(`renumber`), so the numbers are 0..n-1 in display order afterwards.

Pure functions on the instructions dict — no Tk, no IO — unit-tested in
tests/test_instruction_layout.py; instructions_mixin.py does the widgets.
"""

UNFILED = ""                 # the section key of a page that is in no section
UNFILED_LABEL = "Unfiled"    # how the list shows that pseudo-section


def section_of(entry):
    """The section a store entry belongs to (UNFILED when it has none)."""
    section = entry.get("section") if isinstance(entry, dict) else None
    return normalize_section(section) if isinstance(section, str) else UNFILED


def normalize_section(section):
    """A section name as typed: whitespace-collapsed; blank or the Unfiled
    label (any case) means UNFILED."""
    section = " ".join((section or "").split())
    return UNFILED if section.lower() == UNFILED_LABEL.lower() else section


def _sort_key(item):
    name, entry = item
    order = entry.get("order") if isinstance(entry, dict) else None
    if isinstance(order, bool) or not isinstance(order, int):
        return (1, 0, name.lower(), name)   # unordered: after every ordered page, A-Z
    return (0, order, name.lower(), name)


def layout(instructions):
    """[(section, [name, ...]), ...] in display order: sections in the order
    of their first page, pages by order number (unordered pages last, A-Z),
    and the unfiled pages as a final pseudo-section — only when there are any."""
    sections = {}
    for name, entry in sorted(instructions.items(), key=_sort_key):
        sections.setdefault(section_of(entry), []).append(name)
    unfiled = sections.pop(UNFILED, None)
    out = list(sections.items())
    if unfiled:
        out.append((UNFILED, unfiled))
    return out


def sections(instructions):
    """The real section names, in display order."""
    return [sec for sec, _ in layout(instructions) if sec != UNFILED]


def rows(instructions):
    """The flat display sequence: [(section, name), ...]."""
    return _flatten(layout(instructions))


def _flatten(lay):
    return [(sec, name) for sec, names in lay for name in names]


def renumber(instructions, seq):
    """Write the flat sequence `seq` — [(section, name), ...] — back onto the
    entries as their order numbers and sections. Unfiled pages carry no
    "section" key at all, so an untouched store stays byte-for-byte familiar."""
    for i, (sec, name) in enumerate(seq):
        entry = instructions[name]
        entry["order"] = i
        if sec:
            entry["section"] = sec
        else:
            entry.pop("section", None)


def _commit(instructions, lay):
    """Renumber from a layout list, keeping every unfiled block last."""
    real = [(sec, names) for sec, names in lay if sec != UNFILED and names]
    unfiled = [(sec, names) for sec, names in lay if sec == UNFILED and names]
    renumber(instructions, _flatten(real + unfiled))


def move_page(instructions, name, delta):
    """Move one page a step up (delta -1) or down (+1). Within its section
    the page swaps with its neighbour; at the section's edge it steps over
    into the adjacent section instead — as that section's last / first
    page — so a page can be walked anywhere in the list one step at a time.
    Returns True when something changed."""
    seq = rows(instructions)
    names = [n for _, n in seq]
    if name not in names or delta not in (-1, 1):
        return False
    i = names.index(name)
    j = i + delta
    if not 0 <= j < len(seq):
        return False
    if seq[j][0] == seq[i][0]:
        seq[i], seq[j] = seq[j], seq[i]
    else:
        seq[i] = (seq[j][0], name)   # relabel: it joins the neighbouring section
    renumber(instructions, seq)
    return True


def move_section(instructions, section, delta):
    """Move a whole section a step up / down among the real sections. The
    unfiled pseudo-section stays last and cannot be moved or passed."""
    lay = layout(instructions)
    names = [sec for sec, _ in lay]
    if section == UNFILED or section not in names or delta not in (-1, 1):
        return False
    i = names.index(section)
    j = i + delta
    if not 0 <= j < len(lay) or lay[j][0] == UNFILED:
        return False
    lay[i], lay[j] = lay[j], lay[i]
    _commit(instructions, lay)
    return True


def file_page(instructions, name, section):
    """Put a page into `section` as its last page. A name no section has yet
    creates one, after the last existing section; blank / the Unfiled label
    unfiles the page (to the end of the list). A page already in that section
    is left where it is. Returns True when something changed."""
    section = normalize_section(section)
    entry = instructions.get(name)
    if entry is None or section_of(entry) == section:
        return False
    seq = [(s, n) for s, n in rows(instructions) if n != name]
    if section == UNFILED:
        pos = len(seq)
    else:
        same = [k for k, (s, _) in enumerate(seq) if s == section]
        real = [k for k, (s, _) in enumerate(seq) if s != UNFILED]
        pos = (same[-1] + 1) if same else ((real[-1] + 1) if real else 0)
    seq.insert(pos, (section, name))
    renumber(instructions, seq)
    return True


def rename_section(instructions, old, new):
    """Rename a section on every page in it. Renaming onto an existing
    section merges into it (the renamed pages go after that section's own);
    renaming to blank / the Unfiled label unfiles them all. The unfiled
    pseudo-section itself cannot be renamed. Returns True when changed."""
    new = normalize_section(new)
    lay = layout(instructions)
    names = [sec for sec, _ in lay]
    if old == UNFILED or old == new or old not in names:
        return False
    pages = dict(lay)[old]
    if new in names:
        lay = [(sec, (ps + pages) if sec == new else ps) for sec, ps in lay if sec != old]
    else:
        lay = [((new if sec == old else sec), ps) for sec, ps in lay]
    _commit(instructions, lay)
    return True


def drop(instructions, kind, key, target_kind, target_key):
    """Drag-and-drop: put `key` (kind "page" — an instruction name — or
    "section") where the row (`target_kind`, `target_key`) sits.

    A page dropped on a page lands beside it in that page's section — after
    it when dragged down the list, before it when dragged up; dropped on a
    section header it becomes that section's first page. A section dropped
    on any row takes that row's section's place (after it when dragged down).
    The unfiled pseudo-section can be neither dragged nor passed. Returns
    True when something changed."""
    lay = layout(instructions)
    secs = [sec for sec, _ in lay]
    if kind == "page":
        seq = _flatten(lay)
        names = [n for _, n in seq]
        if key not in names or (target_kind, target_key) == ("page", key):
            return False
        src = names.index(key)
        if target_kind == "page":
            if target_key not in names:
                return False
            dst = names.index(target_key)
            sec = seq[dst][0]
            seq.pop(src)
            seq.insert(dst, (sec, key))   # after the target going down, before it going up
        elif target_kind == "section" and target_key in secs:
            seq.pop(src)
            first = next((k for k, (s, _) in enumerate(seq) if s == target_key), None)
            if first is None:
                return False   # the dragged page was that section's only one
            seq.insert(first, (target_key, key))
        else:
            return False
        renumber(instructions, seq)
        return True
    if kind == "section":
        if key == UNFILED or key not in secs:
            return False
        if target_kind == "section":
            target = target_key
        else:
            target = next((sec for sec, ps in lay if target_key in ps), None)
        if target is None or target not in secs or target == key:
            return False
        src, dst = secs.index(key), secs.index(target)
        block = lay.pop(src)
        if target == UNFILED:
            lay.insert(len(lay) - 1, block)   # nothing goes after Unfiled
        else:
            lay.insert(dst, block)
        _commit(instructions, lay)
        return True
    return False
