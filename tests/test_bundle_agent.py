import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "bundle_agent", Path(__file__).parents[1] / "scripts/bundle_agent.py")
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


def test_ldd_alias_and_host_loader():
    assert bundle.parse_ldd('''
    linux-vdso.so.1 (0xff)
    libfastcdr.so.2 => /opt/ros/jazzy/lib/libfastcdr.so.2 (0xaa)
    /lib/ld-linux-aarch64.so.1 (0xbb)
    ''') == {
        "libfastcdr.so.2": "/opt/ros/jazzy/lib/libfastcdr.so.2",
        "ld-linux-aarch64.so.1": "/lib/ld-linux-aarch64.so.1",
    }


def test_missing_transitive_dependency_fails():
    with pytest.raises(RuntimeError, match="Unresolved dependency"):
        bundle.parse_ldd("libmissing.so => not found")


def test_inventory_includes_dynamic_dependencies_and_excludes_glibc(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle.platform, "machine", lambda: "aarch64")
    install = tmp_path / "ros_ws/install"
    exe = install / "micro_ros_agent/lib/micro_ros_agent/micro_ros_agent"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"agent")
    extra = tmp_path / "libextra.so"
    extra.write_bytes(b"dependency")
    for package in bundle.PACKAGES:
        directory = install / package / "lib"
        directory.mkdir(parents=True)
        for backend in bundle.BACKENDS:
            (directory / f"lib{package}__rosidl_{backend}.so").write_bytes(b"type")
        (directory / f"lib{package}__rosidl_generator_py.so").write_bytes(b"exclude")
    def capture(args):
        if args[0] == "getconf":
            return "glibc 2.39"
        if "typesupport" in args[1]:
            return f"libextra.so => {extra} (0x1)\nlibc.so.6 => /lib/libc.so.6 (0x2)"
        return ""
    monkeypatch.setattr(bundle, "capture", capture)
    monkeypatch.setattr(bundle.subprocess, "run", lambda *a, **k:
                        bundle.subprocess.CompletedProcess(a, 0, "--dev --discovery", ""))
    files, manifest = bundle.inventory(tmp_path)
    assert "lib/libextra.so" in files
    assert not any("generator_py" in name or name == "lib/libc.so.6" for name in files)
    assert "libc.so.6" in manifest["host_provided_glibc"]
    assert len(manifest["dynamic_typesupport"]) == 8
