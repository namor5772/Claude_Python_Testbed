#!/bin/bash
# Build TodoList.exe (the native C++/Cocoa port of TodoList.py) and run its
# test suite first. macOS-only; needs the Xcode Command Line Tools (clang++).
#
#   ./build_todolist_native.sh          # test + build ./TodoList.exe
#   SKIP_TESTS=1 ./build_todolist_native.sh   # build only
#
# The interop stage proves both implementations round-trip the same
# todos.json: OneDrive syncs ONE file between machines that may run either
# TodoList.py or TodoList.exe, so the JSON each writes must be readable by
# the other. Python is taken from the repo venv when present.
set -euo pipefail
cd "$(dirname "$0")"

TMP="$(mktemp -d /tmp/todolist_native_build.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

CXXFLAGS=(-std=c++17 -fobjc-arc -Wall -Wextra -O2)

if [ "${SKIP_TESTS:-0}" != "1" ]; then
  echo "── compiling tests"
  clang++ "${CXXFLAGS[@]}" -framework Foundation \
      tests/test_todolist_native.mm -o "$TMP/todolist_tests"

  echo "── running unit tests"
  "$TMP/todolist_tests"

  PY=".venv/bin/python"
  [ -x "$PY" ] || PY="$(command -v python3 || true)"
  if [ -n "$PY" ]; then
    echo "── python <-> native JSON interop"
    # Python writes (json.dump indent=2, ensure_ascii escapes) -> native
    # reads and rewrites -> Python verifies the data survived unchanged.
    "$PY" - "$TMP/py.json" <<'EOF'
import json, sys
data = {"todos": [
    {"text": "unicode café ✓ — em dash", "priority": "High",
     "category": "General", "due": "22/07/2026", "done": False, "created": "19/07/2026"},
    {"text": "plain", "priority": "Low", "category": "Errands", "due": "",
     "done": True, "created": "01/01/2026", "future_key": [1, {"nested": None}]},
], "categories": ["General", "Errands"]}
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
EOF
    "$TMP/todolist_tests" roundtrip "$TMP/py.json" "$TMP/native.json"
    "$PY" - "$TMP/py.json" "$TMP/native.json" <<'EOF'
import json, sys
a = json.load(open(sys.argv[1], encoding="utf-8"))
b = json.load(open(sys.argv[2], encoding="utf-8"))
assert a == b, f"interop mismatch:\n{a}\n{b}"
print("interop OK: python-written and native-rewritten todos.json are identical")
EOF
  else
    echo "── (no python found; interop check skipped)"
  fi
fi

echo "── building TodoList.exe"
clang++ "${CXXFLAGS[@]}" -framework Cocoa TodoList.mm -o TodoList.exe
echo "built $(pwd)/TodoList.exe"
