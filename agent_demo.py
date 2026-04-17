#!/usr/bin/env python3
"""Small demo script created by the chat agent.

Usage:
  python3 agent_demo.py [name]
"""

import sys
from datetime import datetime


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "Roman"
    now = datetime.now().isoformat(timespec="seconds")
    print(f"Hello, {name}!")
    print(f"Time (local): {now}")
    print(f"Python: {sys.version.split()[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
