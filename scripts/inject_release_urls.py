"""Inject release_url в data/manifest.json после создания GitHub Releases.

Использовать после `gh release create` или curl + API. Скрипт читает текущий
манифест, и для каждого сценария добавляет release_url вида:

  https://github.com/<owner>/<repo>/releases/download/<tag>/<archive>

Аргументы:
  --owner <github-org>     (default: Eynor-K)
  --repo <repo-name>       (default: blocksnet-agent)
  --tag-spb <tag>          (default: v2026-data-spb)
  --tag-yuzhno <tag>       (default: v2026-data-yuzhno-sakhalinsk)

Без аргументов — дефолты для текущего репозитория.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[1] / "data" / "manifest.json"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--owner", default="Eynor-K")
    p.add_argument("--repo", default="blocksnet-agent")
    p.add_argument("--tag-spb", default="v2026-data-spb")
    p.add_argument("--tag-yuzhno", default="v2026-data-yuzhno-sakhalinsk")
    args = p.parse_args()

    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for sid, entry in m["scenarios"].items():
        archive = entry["archive"]
        if sid == "saint-petersburg":
            tag = args.tag_spb
        elif sid == "yuzhno-sakhalinsk":
            tag = args.tag_yuzhno
        elif sid == "vasilievsky-island":
            continue  # vasilievsky-island сейчас не архивируется
        else:
            continue
        url = f"https://github.com/{args.owner}/{args.repo}/releases/download/{tag}/{archive}"
        if entry.get("release_url") == url:
            print(f"{sid}: уже заполнен")
            continue
        entry["release_url"] = url
        print(f"{sid}: {url}")

    MANIFEST.write_text(
        json.dumps(m, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\n[manifest] обновлён: {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())