"""Fetch and unpack per-scenario data archives.

Resolves a scenario's archive either from a GitHub Releases URL baked
into ``data/manifest.json`` or from a local file path, verifies SHA256,
and unpacks into ``data/<scenario_id>/``.

Usage:
    # one scenario
    python scripts/fetch_data.py saint-petersburg
    python scripts/fetch_data.py saint-petersburg --local data/_releases/saint-petersburg-data.zip

    # all scenarios listed in manifest
    python scripts/fetch_data.py --all

    # pin a specific release tag (overrides manifest URL)
    python scripts/fetch_data.py saint-petersburg --tag v2026-data-spb

Manifest schema (scripts/package_data.py writes this):
    {
      "schema_version": 1,
      "scenarios": {
        "saint-petersburg": {
          "archive": "saint-petersburg-data.zip",
          "size_bytes": <int>,
          "sha256": "<hex>",
          "files": [{"path": "...", "size_bytes": <int>}, ...],
          "release_url": "https://github.com/<org>/<repo>/releases/download/<tag>/saint-petersburg-data.zip"  # optional
        }
      }
    }

The ``release_url`` field is normally injected post-release by the
``scripts/inject_release_urls.py`` helper (or by hand) so that this
script does not depend on GitHub API access.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
MANIFEST_PATH = DATA_DIR / "manifest.json"

CHUNK = 1 << 20


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        sys.exit(
            f"manifest не найден: {MANIFEST_PATH}\n"
            "сначала запустите scripts/package_data.py или положите manifest.json вручную"
        )
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _resolve_source(
    entry: dict,
    *,
    local: Optional[Path],
    tag: Optional[str],
    repo: Optional[str],
) -> Path:
    """Resolve archive path: local > tagged URL > manifest URL > auto-discover next to manifest."""
    if local is not None:
        if not local.is_file():
            sys.exit(f"--local архив не найден: {local}")
        return local

    url = entry.get("release_url")
    if tag and repo:
        url = f"https://github.com/{repo}/releases/download/{tag}/{entry['archive']}"

    if url:
        cache = DATA_DIR / "_cache" / entry["archive"]
        cache.parent.mkdir(parents=True, exist_ok=True)
        if not cache.is_file() or _sha256(cache) != entry["sha256"]:
            print(f"[fetch] {url}")
            with urllib.request.urlopen(url) as r:
                with cache.open("wb") as f:
                    shutil.copyfileobj(r, f)
        return cache

    # fallback: archive sitting next to manifest (same dir as _releases/)
    candidate = DATA_DIR / "_releases" / entry["archive"]
    if candidate.is_file():
        return candidate
    sys.exit(
        f"не удалось определить источник для {entry['archive']!r}.\n"
        "Укажите --local <path> или --tag <tag> --repo <owner/repo> "
        "либо пропишите release_url в manifest.json"
    )


def _verify(path: Path, expected_sha: str) -> None:
    actual = _sha256(path)
    if actual != expected_sha:
        sys.exit(
            f"SHA256 mismatch для {path}:\n"
            f"  ожидалось: {expected_sha}\n"
            f"  получено:  {actual}\n"
            "Архив повреждён или версия устарела."
        )


def _unpack(archive: Path, scenario_id: str) -> Path:
    target = DATA_DIR / scenario_id
    if target.exists():
        # safety: refuse to clobber unless --force
        # argparse handles this via callback
        pass
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        z.extractall(target)
    return target


def fetch_one(
    scenario_id: str,
    manifest: dict,
    *,
    local: Optional[Path],
    tag: Optional[str],
    repo: Optional[str],
    force: bool,
) -> None:
    entries = manifest.get("scenarios", {})
    if scenario_id not in entries:
        sys.exit(f"сценарий {scenario_id!r} отсутствует в manifest.json")
    entry = entries[scenario_id]

    archive = _resolve_source(entry, local=local, tag=tag, repo=repo)
    size = archive.stat().st_size
    print(f"[{scenario_id}] archive={archive.name} size={size:,} B")

    _verify(archive, entry["sha256"])
    print(f"[{scenario_id}] sha256 OK")

    target = DATA_DIR / scenario_id
    if target.exists() and any(target.iterdir()):
        if not force:
            sys.exit(
                f"{target} уже существует и не пуст. "
                "Используйте --force чтобы перезаписать."
            )
        print(f"[{scenario_id}] --force: очищаю {target}")
        shutil.rmtree(target)

    _unpack(archive, scenario_id)
    print(f"[{scenario_id}] распаковано в {target.relative_to(ROOT)}")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Fetch scenario archives by SHA256 from manifest.json",
    )
    p.add_argument("scenario", nargs="?", help="scenario_id для скачивания")
    p.add_argument("--all", action="store_true", help="скачать все сценарии из manifest")
    p.add_argument("--local", type=Path, help="путь к локальному zip вместо URL")
    p.add_argument("--tag", help="release tag, например v2026-data-spb")
    p.add_argument("--repo", help="owner/repo для формирования URL (нужен с --tag)")
    p.add_argument(
        "--force", action="store_true",
        help="перезаписать существующий data/<scenario>/",
    )
    args = p.parse_args()

    if not args.all and not args.scenario:
        p.error("укажите scenario_id или --all")

    manifest = _load_manifest()
    targets = list(manifest["scenarios"].keys()) if args.all else [args.scenario]
    for sid in targets:
        fetch_one(
            sid, manifest,
            local=args.local, tag=args.tag, repo=args.repo, force=args.force,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())