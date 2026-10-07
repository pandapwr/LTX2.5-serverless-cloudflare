"""Expose Runpod's host-cached Hugging Face weights to ComfyUI."""

import argparse
import os
from pathlib import Path
import re
import sys
import tempfile


DEFAULT_CACHE_ROOT = "/runpod-volume/huggingface-cache/hub"


def find_cached_file(url: str, cache_root: Path) -> Path | None:
    """Resolve a download URL without contacting Hugging Face.

    Prefer the requested ref or commit. A single snapshot without refs is also
    accepted for main, as Runpod can mount a pinned snapshot without refs/main.
    Ambiguous snapshots and files absent from the selected revision are misses.
    """
    match = re.fullmatch(
        r"https://huggingface\.co/([^/]+/[^/]+)/resolve/([^/]+)/(.+)", url
    )
    if not match:
        return None

    repo_id, revision, filename = match.groups()
    repo_root = cache_root / f"models--{repo_id.replace('/', '--')}"
    snapshots = repo_root / "snapshots"
    ref = repo_root / "refs" / revision

    if ref.is_file():
        commit = ref.read_text().strip()
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", commit):
            return None
        snapshot = snapshots / commit
    elif re.fullmatch(r"[0-9a-fA-F]{40,64}", revision):
        snapshot = snapshots / revision
    elif revision == "main" and snapshots.is_dir():
        versions = [path for path in snapshots.iterdir() if path.is_dir()]
        if len(versions) != 1:
            return None
        snapshot = versions[0]
    else:
        return None

    cached_file = snapshot / filename
    if not cached_file.is_file() or cached_file.stat().st_size == 0:
        return None
    return cached_file.absolute()


def link_cached_model(
    url: str, output_path: Path, cache_root: Path
) -> bool:
    """Atomically point the ComfyUI filename at cached weights, without copying."""
    cached_file = find_cached_file(url, cache_root)
    if cached_file is None:
        return False

    existing_link = (
        output_path.is_symlink() and output_path.readlink() == cached_file
    )
    if not existing_link:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".runpod-cache-", dir=output_path.parent
        ) as temporary_dir:
            link = Path(temporary_dir) / output_path.name
            link.symlink_to(cached_file)
            link.replace(output_path)

    print(f"worker-comfyui: Using Runpod cached model: {cached_file}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("output_path", type=Path)
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path(
            os.environ.get("RUNPOD_HF_CACHE_ROOT") or DEFAULT_CACHE_ROOT
        ),
    )
    args = parser.parse_args()
    try:
        linked = link_cached_model(args.url, args.output_path, args.cache_root)
        return 0 if linked else 1
    except OSError as error:
        print(
            f"worker-comfyui: Cannot use Runpod model cache; "
            f"falling back to existing assets/downloads: {error}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
