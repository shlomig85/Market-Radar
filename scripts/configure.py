#!/usr/bin/env python3
"""Point a local .env at the real data sources.

This exists because the alternative — telling an operator to paste five lines into a
terminal — has a trap in it. The contact string publishers require contains a space, so
``MARKETRADAR_FEED_USER_AGENT=Market Radar you@example.com`` pasted at a shell prompt is a
command whose second word is ``Radar``, and the error you get back is
``command not found: Radar``, which points at nothing useful.

Editing the file in place also means an operator who copied .env before the defaults changed
gets the new values, which a change to .env.example alone can never reach.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"

#: Everything a live run needs. The contact string is the operator's; the rest is the
#: choice of real sources over the synthetic corpus.
def settings(contact: str) -> dict[str, str]:
    return {
        "MARKETRADAR_NEWS_PROVIDER": "feeds",
        "MARKETRADAR_FILINGS_PROVIDER": "sec_edgar",
        "MARKETRADAR_COMPANY_PROVIDER": "sec",
        "MARKETRADAR_FEED_USER_AGENT": contact,
        "MARKETRADAR_SEC_USER_AGENT": contact,
    }


def apply(lines: list[str], values: dict[str, str]) -> list[str]:
    """Rewrite each key in place, keeping comments and ordering; append what is missing."""
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in values and not line.lstrip().startswith("#"):
            out.append(f"{key}={values[key]}\n")
            seen.add(key)
        else:
            out.append(line)
    missing = [k for k in values if k not in seen]
    if missing:
        out.append("\n# Set by `make configure`.\n")
        out.extend(f"{k}={values[k]}\n" for k in missing)
    return out


def main() -> int:
    if len(sys.argv) != 2 or "@" not in sys.argv[1]:
        print("Usage: make configure EMAIL=you@example.com", file=sys.stderr)
        return 1
    email = sys.argv[1]
    contact = f"Market Radar {email}"

    if not ENV.exists():
        if not EXAMPLE.exists():  # pragma: no cover - a broken checkout
            print(f"Neither {ENV} nor {EXAMPLE} exists.", file=sys.stderr)
            return 1
        ENV.write_text(EXAMPLE.read_text())

    ENV.write_text("".join(apply(ENV.read_text().splitlines(keepends=True), settings(contact))))

    print(f"Wrote {ENV.relative_to(ROOT)}:")
    for key, value in settings(contact).items():
        print(f"  {key}={value}")
    print()
    print("Docker (the containers read this file now):")
    print("    docker compose down -v && docker compose up -d")
    print("    make docker-cli cmd=\"pipeline --rebuild\"")
    print()
    print("Host processes:")
    print("    make reset && make pipeline")
    print()
    print("Either way, then open http://localhost:3000/trending")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
