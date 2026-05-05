from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.crawler import run_job_sync
from app.db import init_db


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a crawler job from the command line.")
    parser.add_argument("kind", choices=["full", "publishers", "news"])
    args = parser.parse_args()

    init_db()
    stats = run_job_sync(args.kind)
    print(
        f"done kind={args.kind} "
        f"publishers={stats.publishers_seen} "
        f"links={stats.resource_links_seen} "
        f"created={stats.resources_created} "
        f"updated={stats.resources_updated} "
        f"publisher_created={stats.publishers_created} "
        f"errors={stats.errors}"
    )


if __name__ == "__main__":
    main()
