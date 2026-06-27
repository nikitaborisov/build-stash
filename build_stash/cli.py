#!/usr/bin/env python3
#
# build-stash — Redirect build-output directories (target/, build/, ...) out
# of a cloud-synced tree (Dropbox/Box/etc.) into a local cache under $HOME, then
# replace the originals with symlinks. Idempotent and safe to re-run.

import argparse
import hashlib
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

PROG = "build-stash"


class AppError(Exception):
    """Fatal error -> message on stderr, exit 1."""


def warn(msg: str) -> None:
    print(f"{PROG}: warning: {msg}", file=sys.stderr)


@dataclass
class Relinker:
    cache_root: Path
    dir_names: list[str]
    src_base: Path
    dry_run: bool = False
    quiet: bool = False

    def info(self, msg: str) -> None:
        if not self.quiet:
            print(f"{PROG}: {msg}", file=sys.stderr)

    def _dry_run(self, msg: str) -> None:
        print(f"[dry-run] {msg}", file=sys.stderr)

    def _ensure_dir(self, path: Path) -> None:
        if self.dry_run:
            self._dry_run(f"create directory {path}")
        else:
            path.mkdir(parents=True, exist_ok=True)

    def _remove_symlink(self, path: Path) -> None:
        if self.dry_run:
            self._dry_run(f"remove symlink {path}")
        else:
            path.unlink()

    def _create_symlink(self, link: Path, target: Path) -> None:
        if self.dry_run:
            self._dry_run(f"symlink {link} -> {target}")
        else:
            link.symlink_to(target)

    @staticmethod
    def path_hash(s: str) -> str:
        return hashlib.sha256(s.encode()).hexdigest()[:12]

    def cache_dest_for(self, src: Path) -> Path:
        proj = re.sub(r"[^A-Za-z0-9._-]", "_", src.parent.name)
        return self.cache_root / f"{proj}-{src.name}-{self.path_hash(str(src))}"

    def _symlink_points_into_cache(self, target: Path) -> bool:
        if target.is_absolute():
            return target.is_relative_to(self.cache_root)
        try:
            return target.resolve().is_relative_to(self.cache_root.resolve())
        except OSError:
            return False

    def relink_one(self, name: str) -> None:
        src = self.src_base / name
        dest = self.cache_dest_for(src)

        if src.is_symlink():
            self._relink_existing_symlink(name, src, dest)
        elif src.is_dir():
            self._relink_existing_directory(name, src, dest)
        elif src.exists():
            warn(f"{name}: exists but is not a directory or symlink; skipping")
        else:
            self._relink_missing(name, src, dest)

    def _relink_existing_symlink(self, name: str, src: Path, dest: Path) -> None:
        current = src.readlink()
        if current == dest:
            self.info(f"{name}: already linked correctly -> {dest}")
            return
        if self._symlink_points_into_cache(current):
            warn(f"{name}: symlink points to {current}, re-pointing to {dest}")
            self._ensure_dir(dest)
            self._remove_symlink(src)
            self._create_symlink(src, dest)
        else:
            warn(
                f"{name}: existing symlink -> {current} is outside cache root; "
                "leaving untouched"
            )

    def _relink_existing_directory(self, name: str, src: Path, dest: Path) -> None:
        self._ensure_dir(dest)
        if self.dry_run:
            self._dry_run(f"migrate {src} -> {dest}, then symlink {src} -> {dest}")
            return
        self._migrate_contents(name, src, dest)
        try:
            src.symlink_to(dest)
        except OSError as e:
            raise AppError(f"{name}: failed to create symlink: {e}") from e
        self.info(f"{name}: migrated and linked -> {dest}")

    def _relink_missing(self, name: str, src: Path, dest: Path) -> None:
        self._ensure_dir(dest)
        self._create_symlink(src, dest)
        self.info(f"{name}: created and linked -> {dest}")

    @staticmethod
    def _migrate_contents(name: str, src: Path, dest: Path) -> None:
        """Merge-move contents of src into dest, then remove src."""
        try:
            for entry in src.iterdir():
                target = dest / entry.name
                if target.exists() or target.is_symlink():
                    if (
                        entry.is_dir()
                        and not entry.is_symlink()
                        and target.is_dir()
                        and not target.is_symlink()
                    ):
                        Relinker._merge_dir(entry, target)
                        continue
                    if target.is_dir() and not target.is_symlink():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                shutil.move(entry, target)
        except OSError as e:
            raise AppError(f"{name}: failed migrating contents to {dest}: {e}") from e
        try:
            shutil.rmtree(src)
        except OSError as e:
            raise AppError(f"{name}: cleanup failed: {e}") from e

    @staticmethod
    def _merge_dir(src: Path, dest: Path) -> None:
        for entry in src.iterdir():
            target = dest / entry.name
            if (
                (target.exists() or target.is_symlink())
                and entry.is_dir()
                and not entry.is_symlink()
                and target.is_dir()
                and not target.is_symlink()
            ):
                Relinker._merge_dir(entry, target)
            else:
                if target.exists() or target.is_symlink():
                    if target.is_dir() and not target.is_symlink():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                shutil.move(entry, target)
        src.rmdir()

    def main(self) -> int:
        self._ensure_dir(self.cache_root)
        rc = 0
        for name in self.dir_names:
            if name.startswith("/") or "/" in name:
                warn(f"skipping invalid dir name: {name}")
                rc = 1
                continue
            try:
                self.relink_one(name)
            except AppError as e:
                print(f"{PROG}: error: {e}", file=sys.stderr)
                rc = 1
        return rc


def _default_cache_root() -> Path:
    cache_home = os.environ.get("XDG_CACHE_HOME")
    base = Path(cache_home) if cache_home else Path.home() / ".cache"
    return base / "build-redirect"


def parse_args(argv: list[str] | None = None):
    p = argparse.ArgumentParser(
        prog=PROG,
        add_help=False,
        description="Redirect build-output directories into a local cache and "
        "replace the originals with symlinks.",
    )
    p.add_argument(
        "-d",
        dest="work_dir",
        default=Path.cwd(),
        metavar="DIR",
        type=Path,
        help="Working directory (default: current directory).",
    )
    p.add_argument(
        "-c",
        dest="cache_root",
        default=_default_cache_root(),
        metavar="CACHE_ROOT",
        type=Path,
        help="Local cache root.",
    )
    p.add_argument("-n", dest="dry_run", action="store_true", help="Dry run.")
    p.add_argument(
        "-q", dest="quiet", action="store_true", help="Quiet — do not print link actions."
    )
    p.add_argument("-h", dest="help", action="store_true", help="Help.")
    p.add_argument(
        "dir_names",
        nargs="*",
        metavar="DIRNAME",
        help="Build dir names to relink (default: target).",
    )
    return p.parse_args(argv), p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args, parser = parse_args(argv)

    if args.help:
        parser.print_help()
        return 0

    dir_names = args.dir_names or ["target"]

    try:
        src_base = args.work_dir.resolve()
    except OSError:
        print(f"{PROG}: error: cannot resolve: {args.work_dir}", file=sys.stderr)
        return 1

    if not src_base.is_dir():
        print(f"{PROG}: error: not a directory: {args.work_dir}", file=sys.stderr)
        return 1

    relinker = Relinker(
        cache_root=args.cache_root,
        dir_names=dir_names,
        src_base=src_base,
        dry_run=args.dry_run,
        quiet=args.quiet,
    )
    return relinker.main()
