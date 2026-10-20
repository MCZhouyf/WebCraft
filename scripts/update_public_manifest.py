#!/usr/bin/env python3
"""Hash tracked publication files, never ignored credentials or local caches."""
from hashlib import sha256
from pathlib import Path
import subprocess


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "PUBLIC_EXPORT_FILES.sha256"
    names = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=root
    ).decode().split("\0")
    lines = []
    for name in sorted(filter(None, names)):
        if name == output.name:
            continue
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"Not a regular tracked publication file: {name}")
        lines.append(f"{sha256(path.read_bytes()).hexdigest()}  {name}\n")
    output.write_text("".join(lines), encoding="utf-8")
    print(f"Hashed {len(lines)} tracked files.")


if __name__ == "__main__":
    main()
