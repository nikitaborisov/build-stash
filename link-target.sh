#!/usr/bin/env bash
#
# relink-builds.sh — Redirect build-output directories (target/, build/, ...) out
# of a cloud-synced tree (Dropbox/Box/etc.) into a local cache under $HOME, then
# replace the originals with symlinks. Idempotent and safe to re-run.
#
# Usage:
#   relink-builds.sh [-n] [-v] [-c CACHE_ROOT] [-d DIRNAME]... [PATH]
#
#   PATH            Directory to operate on (default: current directory).
#   -d DIRNAME      Build dir name to relink. Repeatable. Default: target.
#                   e.g. -d target -d build -d node_modules
#   -c CACHE_ROOT   Local cache root (default: ${XDG_CACHE_HOME:-$HOME/.cache}/build-redirect).
#   -n              Dry run; print what would happen, change nothing.
#   -v              Verbose.
#   -h              Help.
#
# The cache location for each dir is derived deterministically from the absolute
# source path (so re-runs map to the same place), namespaced by a path hash to
# avoid collisions between like-named dirs in different projects.

set -euo pipefail

PROG=${0##*/}

CACHE_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}/build-redirect"
DRY_RUN=0
VERBOSE=0
declare -a DIR_NAMES=()

die()  { printf '%s: error: %s\n' "$PROG" "$*" >&2; exit 1; }
warn() { printf '%s: warning: %s\n' "$PROG" "$*" >&2; }
info() { (( VERBOSE )) && printf '%s: %s\n' "$PROG" "$*" >&2 || true; }
run()  { if (( DRY_RUN )); then printf '[dry-run] %s\n' "$*" >&2; else eval "$@"; fi; }

usage() { sed -n '2,/^$/{/^#/p}' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

# --- arg parsing ----------------------------------------------------------
while getopts ':d:c:nvh' opt; do
  case "$opt" in
    d) DIR_NAMES+=("$OPTARG") ;;
    c) CACHE_ROOT="$OPTARG" ;;
    n) DRY_RUN=1 ;;
    v) VERBOSE=1 ;;
    h) usage 0 ;;
    :) die "option -$OPTARG requires an argument" ;;
    \?) die "unknown option -$OPTARG (try -h)" ;;
  esac
done
shift $((OPTIND - 1))

(( ${#DIR_NAMES[@]} )) || DIR_NAMES=(target)

SRC_BASE="${1:-$PWD}"
[[ -d "$SRC_BASE" ]] || die "not a directory: $SRC_BASE"

# Absolute, symlink-resolved base.
SRC_BASE=$(cd "$SRC_BASE" && pwd -P) || die "cannot resolve: $SRC_BASE"

# --- hashing helper -------------------------------------------------------
# Deterministic short hash of a path, used to namespace the cache.
path_hash() {
  local h
  if command -v sha256sum >/dev/null 2>&1; then
    h=$(printf '%s' "$1" | sha256sum)
  elif command -v shasum >/dev/null 2>&1; then
    h=$(printf '%s' "$1" | shasum -a 256)
  else
    die "need sha256sum or shasum for hashing"
  fi
  printf '%s' "${h%% *}" | cut -c1-12
}

# Build a human-readable, collision-resistant cache name for a source dir.
cache_dest_for() {
  local src="$1"
  local proj base
  proj=$(basename "$(dirname "$src")")   # project dir name
  base=$(basename "$src")                # e.g. target
  # sanitize project name for filesystem friendliness
  proj=${proj//[^A-Za-z0-9._-]/_}
  printf '%s/%s-%s-%s' "$CACHE_ROOT" "$proj" "$base" "$(path_hash "$src")"
}

# --- core -----------------------------------------------------------------
relink_one() {
  local name="$1"
  local src="$SRC_BASE/$name"
  local dest; dest=$(cache_dest_for "$src")

  # Case 1: already a symlink.
  if [[ -L "$src" ]]; then
    local cur; cur=$(readlink "$src")
    if [[ "$cur" == "$dest" ]]; then
      info "$name: already linked correctly -> $dest"
      return 0
    fi
    # Points somewhere else. Only re-point if it's into our cache root.
    if [[ "$cur" == "$CACHE_ROOT"/* ]]; then
      warn "$name: symlink points to $cur, re-pointing to $dest"
      run "mkdir -p ${dest@Q}"
      run "rm ${src@Q}"
      run "ln -s ${dest@Q} ${src@Q}"
    else
      warn "$name: existing symlink -> $cur is outside cache root; leaving untouched"
    fi
    return 0
  fi

  # Case 2: a real directory — move contents into cache, replace with symlink.
  if [[ -d "$src" ]]; then
    run "mkdir -p ${dest@Q}"
    if (( DRY_RUN )); then
      printf '[dry-run] migrate %s -> %s, then symlink\n' "$src" "$dest" >&2
      return 0
    fi
    # Move contents into dest (merge), preserving anything already cached.
    # rsync if available for robust merge; else mv with fallback.
    if command -v rsync >/dev/null 2>&1; then
      rsync -a --remove-source-files "$src"/ "$dest"/ \
        || die "$name: failed migrating contents to $dest"
      # remove now-empty source tree
      find "$src" -type d -empty -delete 2>/dev/null || true
      rmdir "$src" 2>/dev/null || rm -rf "$src"
    else
      # Best-effort move; if dest non-empty this may collide, so guard.
      if [[ -z "$(ls -A "$dest" 2>/dev/null)" ]]; then
        rmdir "$dest"
        mv "$src" "$dest" || die "$name: mv failed"
      else
        cp -a "$src"/. "$dest"/ || die "$name: copy-merge failed"
        rm -rf "$src" || die "$name: cleanup failed"
      fi
    fi
    ln -s "$dest" "$src" || die "$name: failed to create symlink"
    info "$name: migrated and linked -> $dest"
    return 0
  fi

  # Case 3: exists but not a dir/symlink.
  if [[ -e "$src" ]]; then
    warn "$name: exists but is not a directory or symlink; skipping"
    return 0
  fi

  # Case 4: doesn't exist — create cache + link proactively.
  run "mkdir -p ${dest@Q}"
  run "ln -s ${dest@Q} ${src@Q}"
  info "$name: created and linked -> $dest"
}

main() {
  run "mkdir -p ${CACHE_ROOT@Q}"
  local rc=0
  for name in "${DIR_NAMES[@]}"; do
    # Disallow nested/absolute names; these are simple basenames.
    [[ "$name" != /* && "$name" != */* ]] || { warn "skipping invalid dir name: $name"; rc=1; continue; }
    relink_one "$name" || rc=1
  done
  return $rc
}

main
