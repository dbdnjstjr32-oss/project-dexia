"""Package the Dexia Drone Wargame Simulator into a single deployment ZIP.

Compresses the whole project (source, frontend, checkpoints, scripts, telemetry,
docs) into ``Dexia_Wargame_Sim_Final.zip`` while excluding heavy / regenerable
artifacts (virtual envs, node_modules, caches, the archive itself).

Run:
    python package_project.py
"""

from __future__ import annotations

import os
import sys
import zipfile

# Windows consoles often default to a legacy codepage (e.g. cp949) that cannot
# encode the em-dash below. Force UTF-8 where supported.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
ARCHIVE_NAME = "Dexia_Wargame_Sim_Final.zip"

# Directory names to prune anywhere in the tree.
EXCLUDE_DIRS = {
    ".venv312",
    "venv",
    ".venv",
    "node_modules",
    "__pycache__",
    ".next",
    ".git",
    ".pytest_cache",
    ".ipynb_checkpoints",
    "ray_results",
}

# Individual files to skip.
EXCLUDE_FILES = {ARCHIVE_NAME}

# File-name suffixes to skip (compiled / transient).
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".tmp")


def should_skip_file(name: str) -> bool:
    if name in EXCLUDE_FILES:
        return True
    return name.endswith(EXCLUDE_SUFFIXES)


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"


def main() -> int:
    print("=" * 70)
    print("DEXIA — packaging project for deployment")
    print("=" * 70)
    print(f"Project root : {PROJECT_ROOT}")
    print(f"Archive      : {ARCHIVE_NAME}")
    print(f"Excluding    : {', '.join(sorted(EXCLUDE_DIRS))}\n")

    archive_path = os.path.join(PROJECT_ROOT, ARCHIVE_NAME)
    if os.path.exists(archive_path):
        os.remove(archive_path)  # rebuild cleanly

    file_count = 0
    raw_bytes = 0
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for dirpath, dirnames, filenames in os.walk(PROJECT_ROOT):
            # prune excluded directories in-place so os.walk does not descend
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]

            for fname in filenames:
                if should_skip_file(fname):
                    continue
                abs_path = os.path.join(dirpath, fname)
                # never archive the archive itself
                if os.path.abspath(abs_path) == os.path.abspath(archive_path):
                    continue
                if not os.path.isfile(abs_path):
                    continue  # skip symlinks / special files

                rel_path = os.path.relpath(abs_path, PROJECT_ROOT)
                # store under a top-level "Dexia/" folder for a clean unzip
                arcname = os.path.join("Dexia", rel_path)
                zf.write(abs_path, arcname)
                file_count += 1
                raw_bytes += os.path.getsize(abs_path)

    archive_bytes = os.path.getsize(archive_path)
    ratio = (1.0 - archive_bytes / raw_bytes) * 100.0 if raw_bytes else 0.0

    print(f"Files archived   : {file_count}")
    print(f"Uncompressed     : {human_size(raw_bytes)}")
    print(f"Compressed (zip) : {human_size(archive_bytes)}  ({archive_bytes} bytes)")
    print(f"Compression      : {ratio:.1f}% saved")
    print(f"\nCreated -> {archive_path}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
