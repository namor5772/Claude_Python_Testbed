#!/usr/bin/env python3
"""Merge SelfBot / MyAgent name-keyed JSON stores across machines without data loss.

The problem this solves: system_prompts.json (and skills.json / agent_instructions.json)
are live runtime files that SelfBot/MyAgent rewrite constantly, yet they are ALSO tracked
in git. Between the rare commits, each machine accumulates its own prompts, so no single
machine's file is a strict superset of the others. A plain git pull / copy is
all-or-nothing and loses whichever side it didn't keep. This does a KEY-LEVEL union so
every prompt from every machine survives.

Usage
-----
    # merge this machine's file with one pulled from another machine:
    python merge_system_prompts.py system_prompts.json /path/to/other/system_prompts.json

    # give a source a short label (used to name conflicting variants):
    python merge_system_prompts.py system_prompts.json macmini=/tmp/macmini_prompts.json

    # more than two machines at once, custom output path:
    python merge_system_prompts.py a.json b.json c.json -o system_prompts.merged.json

    # works for skills.json / agent_instructions.json too (all are name-keyed dicts):
    python merge_system_prompts.py skills.json /tmp/other_skills.json

Semantics
---------
* The FIRST file is the "primary". Its prompts keep their names and win ties.
* A name present in only one file is carried over as-is.
* A name in several files with IDENTICAL content (compared as parsed JSON, so
  cosmetic re-serialization — key order / whitespace — never counts as a
  difference) is included once, no conflict.
* A name in several files with DIFFERENT content is a real conflict: the primary
  keeps the plain name; every other machine's variant is preserved under
  "<name>__<label>" so NOTHING is lost. You reconcile/delete the duplicates later
  in the app's UI.

Safety
------
* Never overwrites an input in place. Writes to a NEW file (default
  system_prompts.merged.json, or whatever -o names) that you review, then swap in.
* Safe to run while SelfBot/MyAgent is open (it only reads the live file). But
  before you SWAP the merged file into place you MUST quit the app, or its 5-second
  auto-save will clobber your merge on the next tick / on close.
"""
import argparse
import json
import os
import re
import sys


def _label_for(path, explicit):
    """Short, filesystem-derived label used to name conflicting variants."""
    if explicit:
        return explicit
    stem = os.path.splitext(os.path.basename(path))[0]
    # parent dir often disambiguates identically-named files from two machines
    parent = os.path.basename(os.path.dirname(os.path.abspath(path)))
    raw = f"{parent}_{stem}" if parent else stem
    return re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_") or "src"


def _parse_source(arg):
    """Accept 'path' or 'label=path' (label before '=' to avoid clashing with
    Windows drive letters / paths that contain '=')."""
    if "=" in arg:
        label, path = arg.split("=", 1)
        return path, label
    return arg, None


def _load(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        sys.exit(f"error: {path} is not a name-keyed JSON object (got {type(data).__name__})")
    return data


def main():
    ap = argparse.ArgumentParser(
        description="Union name-keyed JSON stores (system_prompts.json etc.) across machines.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("sources", nargs="+",
                    help="Input files; first is primary. Each may be 'path' or 'label=path'.")
    ap.add_argument("-o", "--output", default=None,
                    help="Output path (default: <primary-stem>.merged.json).")
    args = ap.parse_args()

    parsed = [_parse_source(s) for s in args.sources]
    if len(parsed) < 2:
        sys.exit("error: give at least two files to merge (primary + at least one other).")

    primary_path = parsed[0][0]
    out_path = args.output or (
        os.path.splitext(primary_path)[0] + ".merged.json"
    )
    if os.path.abspath(out_path) in {os.path.abspath(p) for p, _ in parsed}:
        sys.exit(f"error: output '{out_path}' is one of the inputs — refusing to overwrite in place.")

    merged = {}           # name -> entry  (insertion order preserved)
    origin = {}           # name -> label that first supplied this exact entry
    added, identical, conflicts = [], [], []

    for path, explicit in parsed:
        label = _label_for(path, explicit)
        store = _load(path)
        print(f"• {label:<20} {len(store):>3} prompts  ({path})")
        for name, entry in store.items():
            if name not in merged:
                merged[name] = entry
                origin[name] = label
                added.append((name, label))
            elif merged[name] == entry:
                identical.append(name)          # same prompt on another machine — no-op
            else:
                # genuine content conflict — preserve the other machine's variant
                variant = f"{name}__{label}"
                n = 2
                while variant in merged:
                    variant = f"{name}__{label}{n}"
                    n += 1
                merged[variant] = entry
                conflicts.append((name, origin[name], variant, label))

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    print("\n" + "=" * 60)
    print(f"MERGED {len(merged)} unique prompts -> {out_path}")
    print(f"  carried over : {len(added)}")
    print(f"  identical dups skipped : {len(identical)}")
    if conflicts:
        print(f"  CONFLICTS preserved as variants : {len(conflicts)}")
        for name, keep_label, variant, other_label in conflicts:
            print(f"    '{name}' kept from [{keep_label}]; "
                  f"[{other_label}]'s differing copy saved as '{variant}'")
    print("=" * 60)
    print("\nNext: QUIT SelfBot/MyAgent (its auto-save will clobber an open file),")
    print(f"then:  mv {out_path} {primary_path}")
    print("then relaunch and reconcile any __<label> conflict variants in the UI.")


if __name__ == "__main__":
    main()
