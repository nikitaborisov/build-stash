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

PROG = "build-stash"


class AppError(Exception):
    """Fatal error -> message on stderr, exit 1 (mirrors bash die)."""


def warn(msg):
    print(f"{PROG}: warning: {msg}", file=sys.stderr)


class Relinker:
    def __init__(self, cache_root, dir_names, src_base, dry_run, verbose):
        self.cache_root = cache_root
        self.dir_names = dir_names
        self.src_base = src_base
        self.dry_run = dry_run
        self.verbose = verbose

    def info(self, msg):
        if self.verbose:
            print(f"{PROG}: {msg}", file=sys.stderr)

    def run(self, description, action):
        """Execute action() unless dry-run, in which case just describe it."""
        if self.dry_run:
            print(f"[dry-run] {description}", file=sys.stderr)
        else:
            action()

    # --- hashing helper ---------------------------------------------------
    # Deterministic short hash of a path, used to namespace the cache.
    @staticmethod
    def path_hash(s):
        return hashlib.sha256(s.encode()).hexdigest()[:12]

    # Build a human-readable, collision-resistant cache name for a source dir.
    def cache_dest_for(self, src):
        proj = os.path.basename(os.path.dirname(src))  # project dir name
        base = os.path.basename(src)                   # e.g. target
        # sanitize project name for filesystem friendliness
        proj = re.sub(r"[^A-Za-z0-9._-]", "_", proj)
        return os.path.join(self.cache_root, f"{proj}-{base}-{self.path_hash(src)}")

    # --- core -------------------------------------------------------------
    def relink_one(self, name):
        src = os.path.join(self.src_base, name)
        dest = self.cache_dest_for(src)

        # Case 1: already a symlink.
        if os.path.islink(src):
            cur = os.readlink(src)
            if cur == dest:
                self.info(f"{name}: already linked correctly -> {dest}")
                return
            # Points somewhere else. Only re-point if it's into our cache root.
            if cur.startswith(self.cache_root + os.sep):
                warn(f"{name}: symlink points to {cur}, re-pointing to {dest}")
                self.run(f"mkdir -p {dest}", lambda: os.makedirs(dest, exist_ok=True))
                self.run(f"rm {src}", lambda: os.unlink(src))
                self.run(f"ln -s {dest} {src}", lambda: os.symlink(dest, src))
            else:
                warn(f"{name}: existing symlink -> {cur} is outside cache root; "
                     "leaving untouched")
            return

        # Case 2: a real directory — move contents into cache, replace with symlink.
        if os.path.isdir(src):
            self.run(f"mkdir -p {dest}", lambda: os.makedirs(dest, exist_ok=True))
            if self.dry_run:
                print(f"[dry-run] migrate {src} -> {dest}, then symlink",
                      file=sys.stderr)
                return
            # Move contents into dest (merge), preserving anything already cached.
            self._migrate_contents(name, src, dest)
            try:
                os.symlink(dest, src)
            except OSError as e:
                raise AppError(f"{name}: failed to create symlink: {e}")
            self.info(f"{name}: migrated and linked -> {dest}")
            return

        # Case 3: exists but not a dir/symlink.
        if os.path.exists(src):
            warn(f"{name}: exists but is not a directory or symlink; skipping")
            return

        # Case 4: doesn't exist — create cache + link proactively.
        self.run(f"mkdir -p {dest}", lambda: os.makedirs(dest, exist_ok=True))
        self.run(f"ln -s {dest} {src}", lambda: os.symlink(dest, src))
        self.info(f"{name}: created and linked -> {dest}")

    @staticmethod
    def _migrate_contents(name, src, dest):
        """Merge-move contents of src into dest, then remove src."""
        try:
            for entry in os.listdir(src):
                s = os.path.join(src, entry)
                d = os.path.join(dest, entry)
                if os.path.exists(d) or os.path.islink(d):
                    # Destination already has this entry; merge dirs, replace files.
                    if os.path.isdir(s) and not os.path.islink(s) \
                            and os.path.isdir(d) and not os.path.islink(d):
                        Relinker._merge_dir(s, d)
                        continue
                    if os.path.isdir(d) and not os.path.islink(d):
                        shutil.rmtree(d)
                    else:
                        os.unlink(d)
                shutil.move(s, d)
        except OSError as e:
            raise AppError(f"{name}: failed migrating contents to {dest}: {e}")
        # remove now-empty source tree
        try:
            shutil.rmtree(src)
        except OSError as e:
            raise AppError(f"{name}: cleanup failed: {e}")

    @staticmethod
    def _merge_dir(s, d):
        for entry in os.listdir(s):
            ss = os.path.join(s, entry)
            dd = os.path.join(d, entry)
            if (os.path.exists(dd) or os.path.islink(dd)) and \
                    os.path.isdir(ss) and not os.path.islink(ss) and \
                    os.path.isdir(dd) and not os.path.islink(dd):
                Relinker._merge_dir(ss, dd)
            else:
                if os.path.exists(dd) or os.path.islink(dd):
                    if os.path.isdir(dd) and not os.path.islink(dd):
                        shutil.rmtree(dd)
                    else:
                        os.unlink(dd)
                shutil.move(ss, dd)
        os.rmdir(s)

    def main(self):
        self.run(f"mkdir -p {self.cache_root}",
                 lambda: os.makedirs(self.cache_root, exist_ok=True))
        rc = 0
        for name in self.dir_names:
            # Disallow nested/absolute names; these are simple basenames.
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


def parse_args(argv):
    default_cache = os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache"),
        "build-redirect",
    )
    p = argparse.ArgumentParser(
        prog=PROG,
        add_help=False,
        description="Redirect build-output directories into a local cache and "
                    "replace the originals with symlinks.",
    )
    p.add_argument("-d", dest="dir_names", action="append", metavar="DIRNAME",
                   help="Build dir name to relink. Repeatable. Default: target.")
    p.add_argument("-c", dest="cache_root", default=default_cache, metavar="CACHE_ROOT",
                   help="Local cache root.")
    p.add_argument("-n", dest="dry_run", action="store_true", help="Dry run.")
    p.add_argument("-v", dest="verbose", action="store_true", help="Verbose.")
    p.add_argument("-h", dest="help", action="store_true", help="Help.")
    p.add_argument("path", nargs="?", default=os.getcwd(),
                   help="Directory to operate on (default: current directory).")
    return p.parse_args(argv), p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args, parser = parse_args(argv)

    if args.help:
        parser.print_help()
        return 0

    dir_names = args.dir_names or ["target"]

    src_base = args.path
    if not os.path.isdir(src_base):
        print(f"{PROG}: error: not a directory: {src_base}", file=sys.stderr)
        return 1
    # Absolute, symlink-resolved base.
    try:
        src_base = os.path.realpath(src_base)
    except OSError:
        print(f"{PROG}: error: cannot resolve: {src_base}", file=sys.stderr)
        return 1

    relinker = Relinker(
        cache_root=args.cache_root,
        dir_names=dir_names,
        src_base=src_base,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )
    return relinker.main()
