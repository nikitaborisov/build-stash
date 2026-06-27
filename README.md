# build-stash

Redirect build-output directories (`target/`, `build/`, `node_modules/`, …) out of a cloud-synced tree (Dropbox, Box, iCloud, etc.) into a local cache under `$HOME`, then replace the originals with symlinks. Idempotent and safe to re-run.

## Why

Cloud sync on large, churn-heavy build artifacts is slow, expensive, and sometimes breaks builds. Keeping those directories on local disk while leaving your project in the synced folder avoids the sync overhead without moving the repo.

## Install

Requires Python 3.9+.

**Recommended** — install as an isolated CLI with [pipx](https://pipx.pypa.io/):

```bash
pipx install .
```

From a git checkout:

```bash
pipx install git+https://github.com/you/symink-target.git
```

**Editable dev install** (changes take effect immediately):

```bash
pip install -e .
```

**Plain install** into the active environment:

```bash
pip install .
```

This registers a `build-stash` console script on your `PATH` (typically `~/.local/bin` with `pip install --user`, or inside a virtualenv / pipx venv).

You can also run without installing:

```bash
python -m build_stash [options] [PATH]
```

## Usage

```
build-stash [-n] [-v] [-c CACHE_ROOT] [-d DIRNAME]... [PATH]
```

| Flag | Description |
|------|-------------|
| `PATH` | Directory to operate on (default: current directory) |
| `-d DIRNAME` | Build dir name to relink; repeatable (default: `target`) |
| `-c CACHE_ROOT` | Local cache root (default: `${XDG_CACHE_HOME:-~/.cache}/build-redirect`) |
| `-n` | Dry run — print actions, change nothing |
| `-v` | Verbose |
| `-h` | Help |

### Examples

Redirect the default `target/` directory in the current project:

```bash
cd ~/Dropbox/dev/my-rust-crate
build-stash
```

Preview first:

```bash
build-stash -n -v
```

Redirect multiple build dirs:

```bash
build-stash -d target -d build -d node_modules ~/Dropbox/dev/my-app
```

Use a custom cache location:

```bash
build-stash -c ~/.cache/my-builds ~/Dropbox/dev/my-app
```

## How it works

1. For each build directory name, compute a deterministic cache path from the project's absolute path (hashed to avoid collisions between projects).
2. If the directory exists and is real, migrate its contents into the cache and replace it with a symlink.
3. If it is already a symlink into the cache, leave it (or re-point if the cache layout changed).
4. If it does not exist yet, create the cache directory and symlink proactively so the next build writes locally from the start.

Re-running is safe: already-correct symlinks are left alone.

## Uninstall

```bash
pipx uninstall build-stash
# or
pip uninstall build-stash
```

Symlinks and cached data under the cache root are not removed automatically.
