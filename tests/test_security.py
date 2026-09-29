from pathlib import Path

import pytest

from term5.security.paths import PathGuard
from term5.security.network import NetworkGuard


def test_path_escape_blocked(tmp_path: Path):
    guard = PathGuard(tmp_path)
    with pytest.raises(PermissionError):
        guard.resolve(tmp_path.parent / "escape.txt")


def test_dotdot_escape_blocked(tmp_path: Path):
    guard = PathGuard(tmp_path)
    with pytest.raises(PermissionError):
        guard.resolve("../escape.txt")


def test_protected_paths_blocked(tmp_path: Path):
    guard = PathGuard(tmp_path)
    for name in [".env", ".git/config", "id_rsa", "secret.pem", ".term5/memory.json"]:
        with pytest.raises(PermissionError):
            guard.resolve(name)


def test_symlink_escape_blocked(tmp_path: Path):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir(exist_ok=True)
    link = tmp_path / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    guard = PathGuard(tmp_path)
    with pytest.raises(PermissionError):
        guard.resolve("link/file.txt")


def test_network_loopback_blocked():
    guard = NetworkGuard()
    with pytest.raises(PermissionError):
        guard.validate_url("http://127.0.0.1/test")
