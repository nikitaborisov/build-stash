import os
import subprocess
import sys
from pathlib import Path

import pytest

from build_stash.cli import Relinker, main, parse_args


@pytest.fixture
def project(tmp_path):
    """A fake project directory with a parent name used in cache paths."""
    root = tmp_path / "my-rust-crate"
    root.mkdir()
    return root


@pytest.fixture
def cache_root(tmp_path):
    return tmp_path / "cache"


def make_relinker(cache_root, project, *, dry_run=False, quiet=False, dir_names=None):
    return Relinker(
        cache_root=str(cache_root),
        dir_names=dir_names or ["target"],
        src_base=str(project),
        dry_run=dry_run,
        quiet=quiet,
    )


class TestCacheDest:
    def test_cache_dest_is_deterministic(self, project, cache_root):
        relinker = make_relinker(cache_root, project)
        src = str(project / "target")
        assert relinker.cache_dest_for(src) == relinker.cache_dest_for(src)

    def test_cache_dest_includes_project_and_dir_name(self, project, cache_root):
        relinker = make_relinker(cache_root, project)
        dest = relinker.cache_dest_for(str(project / "target"))
        assert dest.startswith(str(cache_root))
        assert "my-rust-crate-target-" in os.path.basename(dest)

    def test_cache_dest_sanitizes_project_name(self, tmp_path, cache_root):
        project = tmp_path / "weird name!"
        project.mkdir()
        relinker = make_relinker(cache_root, project)
        dest = relinker.cache_dest_for(str(project / "build"))
        assert "weird_name_-build-" in os.path.basename(dest)


class TestRelinkOne:
    def test_creates_symlink_when_missing(self, project, cache_root, capsys):
        relinker = make_relinker(cache_root, project)
        assert relinker.main() == 0

        err = capsys.readouterr().err
        assert "created and linked ->" in err

        target = project / "target"
        assert target.is_symlink()
        dest = os.readlink(target)
        assert dest == relinker.cache_dest_for(str(target))
        assert os.path.isdir(dest)

    def test_migrates_existing_directory(self, project, cache_root):
        target = project / "target"
        target.mkdir()
        artifact = target / "debug" / "my-app"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("built")

        relinker = make_relinker(cache_root, project)
        assert relinker.main() == 0

        assert target.is_symlink()
        dest = Path(os.readlink(target))
        assert (dest / "debug" / "my-app").read_text() == "built"
        assert not (project / "target" / "debug").exists() or target.is_symlink()

    def test_idempotent_when_already_linked(self, project, cache_root, capsys):
        relinker = make_relinker(cache_root, project)
        assert relinker.main() == 0
        assert relinker.main() == 0

        target = project / "target"
        first_dest = os.readlink(target)
        assert relinker.main() == 0
        assert os.readlink(target) == first_dest

        err = capsys.readouterr().err
        assert err.count("already linked correctly ->") == 2

    def test_quiet_suppresses_link_messages(self, project, cache_root, capsys):
        relinker = make_relinker(cache_root, project, quiet=True)
        assert relinker.main() == 0
        assert "created and linked ->" not in capsys.readouterr().err

    def test_repairs_symlink_within_cache_root(self, project, cache_root):
        relinker = make_relinker(cache_root, project)
        correct_dest = relinker.cache_dest_for(str(project / "target"))
        os.makedirs(correct_dest, exist_ok=True)

        stale_dest = os.path.join(str(cache_root), "stale-target")
        os.makedirs(stale_dest, exist_ok=True)
        (project / "target").symlink_to(stale_dest)

        assert relinker.main() == 0
        assert os.readlink(project / "target") == correct_dest

    def test_leaves_external_symlink_untouched(self, project, cache_root):
        external = project / "elsewhere"
        external.mkdir()
        (project / "target").symlink_to(external)

        relinker = make_relinker(cache_root, project)
        assert relinker.main() == 0
        assert os.readlink(project / "target") == str(external)

    def test_skips_non_directory_file(self, project, cache_root, capsys):
        (project / "target").write_text("not a dir")

        relinker = make_relinker(cache_root, project)
        assert relinker.main() == 0
        assert (project / "target").is_file()

        err = capsys.readouterr().err
        assert "not a directory or symlink" in err

    def test_dry_run_does_not_modify(self, project, cache_root, capsys):
        target = project / "target"
        target.mkdir()
        (target / "artifact").write_text("stay")

        relinker = make_relinker(cache_root, project, dry_run=True)
        assert relinker.main() == 0

        assert target.is_dir()
        assert (target / "artifact").read_text() == "stay"
        assert not (cache_root).exists() or not any(cache_root.iterdir())

        err = capsys.readouterr().err
        assert "[dry-run]" in err

    def test_multiple_dir_names(self, project, cache_root):
        (project / "build").mkdir()
        (project / "build" / "output").write_text("x")

        relinker = make_relinker(cache_root, project, dir_names=["target", "build"])
        assert relinker.main() == 0

        for name in ("target", "build"):
            path = project / name
            assert path.is_symlink()
            dest = Path(os.readlink(path))
            if name == "build":
                assert (dest / "output").read_text() == "x"


class TestMainValidation:
    def test_invalid_dir_name_sets_exit_code(self, project, cache_root, capsys):
        relinker = make_relinker(cache_root, project, dir_names=["nested/path"])
        assert relinker.main() == 1
        err = capsys.readouterr().err
        assert "invalid dir name" in err

    def test_cli_rejects_missing_directory(self, tmp_path, capsys):
        missing = tmp_path / "nope"
        rc = main([str(missing)])
        assert rc == 1
        assert "not a directory" in capsys.readouterr().err


class TestParseArgs:
    def test_defaults(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        args, _ = parse_args([])
        assert args.dir_names is None
        assert args.dry_run is False
        assert args.quiet is False
        assert args.path == str(tmp_path)

    def test_custom_flags(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        args, _ = parse_args(["-n", "-q", "-c", "/tmp/cache", "-d", "build", str(project)])
        assert args.dry_run is True
        assert args.quiet is True
        assert args.cache_root == "/tmp/cache"
        assert args.dir_names == ["build"]
        assert args.path == str(project)


class TestModuleEntrypoint:
    def test_module_runs(self, project, cache_root, monkeypatch):
        monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "build_stash",
                "-c",
                str(cache_root),
                str(project),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert (project / "target").is_symlink()
