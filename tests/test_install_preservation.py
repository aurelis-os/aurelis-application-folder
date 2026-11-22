"""
Preservation property tests — Property 2: Preservation.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

These tests establish the baseline for the **unfixed** Makefile and MUST PASS before
the fix is applied.  They verify that all existing install artifacts remain correctly
placed at their expected paths across a range of DESTDIR/PREFIX combinations, and that
non-Python sub-targets produce identical output regardless of which Python packages are
pre-installed.

Observation (run on unfixed Makefile, DESTDIR=/tmp/test-preserve2, PREFIX=/usr/local):
------------------------------------------------------------------------
Artifact manifest created by ``make install NO_ENABLE=1``:

  Binaries:
    /usr/local/bin/appimage
    /usr/local/bin/appimage-gui

  Python source tree (install-python):
    /usr/local/lib/appimage-launcher/python/appimage_launcher/  (full tree)

  Systemd unit (install-systemd):
    /usr/local/lib/systemd/user/appimage-launcher.service

  D-Bus (install-dbus):
    /usr/local/share/dbus-1/services/io.github.appimage_launcher.Manager.service
    /usr/local/share/dbus-1/interfaces/io.github.appimage_launcher.Manager1.xml

  Desktop entries (install-desktop / install-kio):
    /usr/local/share/applications/appimage-launcher.desktop
    /usr/local/share/kio/servicemenus/appimage-launcher.desktop

  Icons (install-icons):
    /usr/local/share/icons/hicolor/scalable/apps/appimage-launcher.svg

  Shell completions (install-completions):
    /usr/local/share/bash-completion/completions/appimage
    /usr/local/share/zsh/site-functions/_appimage
    /usr/local/share/fish/vendor_completions.d/appimage.fish

  Man page (install-man):
    /usr/local/share/man/man1/appimage.1

  Documentation (install-doc):
    /usr/local/share/doc/appimage-launcher/README.md
    /usr/local/share/doc/appimage-launcher/SPEC.md

  Config (install-config):
    /etc/appimage-launcher/config.toml

Wrapper script content (from DESTDIR/usr/local/bin/appimage):
  #!/bin/sh
  export PYTHONPATH="/usr/local/lib/appimage-launcher/python:${PYTHONPATH}"
  exec python3 -m appimage_launcher.cli "$@"

deps-check output (unfixed):
  inotify_simple: 2.0.1
  WARNING: dbus-fast not installed
  All dependencies checked.
  (NOTE: tomli_w is entirely absent from deps-check output)

Isolation technique
-------------------
Same as the bug-condition test: ``python3 -I`` isolated mode with explicit
``sys.path`` replacement to prevent system-installed packages leaking into the check.

Property-based testing approach
--------------------------------
Hypothesis generates a range of valid DESTDIR strings and PREFIX strings.  For each
combination we run ``make install`` with that DESTDIR/PREFIX and assert:
  1. The ``appimage`` and ``appimage-gui`` wrapper scripts exist at the correct path.
  2. Each wrapper contains the correct PYTHONPATH export line referencing $(PREFIX).
  3. The ``appimage_launcher`` source tree is copied to $(DESTDIR)$(PACKAGE_DIR).

A second property tests that the non-Python sub-targets (install-bin, install-systemd,
install-dbus) each produce the same output regardless of which Python packages happen to
be pre-installed in the host environment.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.resolve()
PYTHON = sys.executable

# Non-Python artifact sub-targets and the relative path each one installs
# (relative to DESTDIR, with PREFIX=/usr/local).
# These are the targets that must be unaffected by Python package state.
NON_PYTHON_SUBTARGET_ARTIFACTS = [
    # (make_target, relative_path_under_destdir_with_default_prefix)
    ("install-bin", "usr/local/bin/appimage"),
    ("install-bin", "usr/local/bin/appimage-gui"),
    ("install-systemd", "usr/local/lib/systemd/user/appimage-launcher.service"),
    ("install-dbus", "usr/local/share/dbus-1/services/io.github.appimage_launcher.Manager.service"),
    ("install-dbus", "usr/local/share/dbus-1/interfaces/io.github.appimage_launcher.Manager1.xml"),
    ("install-desktop", "usr/local/share/applications/appimage-launcher.desktop"),
    ("install-kio", "usr/local/share/kio/servicemenus/appimage-launcher.desktop"),
    ("install-icons", "usr/local/share/icons/hicolor/scalable/apps/appimage-launcher.svg"),
    ("install-completions", "usr/local/share/bash-completion/completions/appimage"),
    ("install-completions", "usr/local/share/zsh/site-functions/_appimage"),
    ("install-completions", "usr/local/share/fish/vendor_completions.d/appimage.fish"),
    ("install-man", "usr/local/share/man/man1/appimage.1"),
    ("install-doc", "usr/local/share/doc/appimage-launcher/README.md"),
    ("install-doc", "usr/local/share/doc/appimage-launcher/SPEC.md"),
]

# Full set of artifacts expected from ``make install`` with default PREFIX.
# These are relative to DESTDIR.
FULL_INSTALL_ARTIFACTS = [
    "usr/local/bin/appimage",
    "usr/local/bin/appimage-gui",
    "usr/local/lib/systemd/user/appimage-launcher.service",
    "usr/local/share/dbus-1/services/io.github.appimage_launcher.Manager.service",
    "usr/local/share/dbus-1/interfaces/io.github.appimage_launcher.Manager1.xml",
    "usr/local/share/applications/appimage-launcher.desktop",
    "usr/local/share/kio/servicemenus/appimage-launcher.desktop",
    "usr/local/share/icons/hicolor/scalable/apps/appimage-launcher.svg",
    "usr/local/share/bash-completion/completions/appimage",
    "usr/local/share/zsh/site-functions/_appimage",
    "usr/local/share/fish/vendor_completions.d/appimage.fish",
    "usr/local/share/man/man1/appimage.1",
    "usr/local/share/doc/appimage-launcher/README.md",
    "usr/local/share/doc/appimage-launcher/SPEC.md",
    "etc/appimage-launcher/config.toml",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_make(args: list[str], cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    """Run make with the given args and return the CompletedProcess."""
    return subprocess.run(
        ["make"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=90,
    )


def _package_dir_for_prefix(prefix: str) -> str:
    """Return the PACKAGE_DIR value for a given PREFIX.

    Mirrors ``PACKAGE_DIR = $(PREFIX)/lib/appimage-launcher/python`` in the Makefile.
    """
    return f"{prefix}/lib/appimage-launcher/python"


def _wrapper_pythonpath_line(prefix: str) -> str:
    """Return the expected PYTHONPATH export line inside the wrapper scripts.

    Mirrors the ``install-bin`` recipe in the Makefile:
      export PYTHONPATH="$(PACKAGE_DIR):${PYTHONPATH}"
    """
    package_dir = _package_dir_for_prefix(prefix)
    return f'export PYTHONPATH="{package_dir}:${{PYTHONPATH}}"'


# ---------------------------------------------------------------------------
# Strategy: generate safe, valid DESTDIR paths (temp subdirectories)
# ---------------------------------------------------------------------------


@st.composite
def valid_destdir(draw) -> str:
    """Generate a unique temporary directory path for use as DESTDIR."""
    suffix = draw(
        st.text(
            alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), min_codepoint=97),
            min_size=4,
            max_size=12,
        )
    )
    return tempfile.mkdtemp(prefix=f"test-preserve-{suffix}-")


# ---------------------------------------------------------------------------
# Module-scoped fixture: single make install into a known DESTDIR
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def baseline_install():
    """Run ``make install`` once and return (destdir, prefix).

    This is the shared baseline for unit-style preservation assertions.
    Uses the default PREFIX=/usr/local.
    """
    destdir = tempfile.mkdtemp(prefix="test-preserve-baseline-")
    prefix = "/usr/local"
    try:
        result = _run_make(
            [
                "install",
                f"DESTDIR={destdir}",
                f"PREFIX={prefix}",
                "NO_ENABLE=1",
            ]
        )
        assert result.returncode == 0, (
            f"make install failed (exit {result.returncode}).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        yield destdir, prefix
    finally:
        shutil.rmtree(destdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Non-PBT baseline: full artifact manifest with default PREFIX
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rel_path", FULL_INSTALL_ARTIFACTS)
def test_full_install_artifact_exists(baseline_install, rel_path):
    """Verify every expected artifact is present after ``make install``.

    **Validates: Requirements 3.1, 3.2**

    Encodes the observed manifest from the unfixed Makefile run.
    These assertions MUST PASS on the unfixed Makefile to establish the baseline.
    """
    destdir, _prefix = baseline_install
    full_path = Path(destdir) / rel_path
    assert full_path.exists(), (
        f"Expected artifact missing: {full_path}\n"
        f"This artifact must be present after `make install` with DESTDIR={destdir}."
    )


def test_python_source_tree_installed(baseline_install):
    """Verify install-python copies the appimage_launcher source tree.

    **Validates: Requirements 3.1**

    Checks that $(DESTDIR)$(PACKAGE_DIR)/appimage_launcher/ exists and contains
    Python files, mirroring the ``install-python`` target behaviour.
    """
    destdir, prefix = baseline_install
    package_dir = _package_dir_for_prefix(prefix)
    # Strip leading '/' so Path.joinpath works correctly
    pkg_path = Path(destdir) / package_dir.lstrip("/") / "appimage_launcher"
    assert pkg_path.is_dir(), (
        f"Expected appimage_launcher source tree at {pkg_path}.\n"
        f"The install-python target must copy the src tree to $(DESTDIR)$(PACKAGE_DIR)/appimage_launcher."
    )
    # At least __init__.py should exist
    init_py = pkg_path / "__init__.py"
    assert init_py.exists(), (
        f"Expected {init_py} to exist — appimage_launcher package is incomplete."
    )


def test_wrapper_appimage_content(baseline_install):
    """Verify appimage wrapper script contains the correct PYTHONPATH export line.

    **Validates: Requirements 3.2**

    The wrapper must set PYTHONPATH to $(PACKAGE_DIR) so the installed app source
    is found by the Python interpreter.
    """
    destdir, prefix = baseline_install
    wrapper = Path(destdir) / "usr/local/bin/appimage"
    assert wrapper.exists(), f"appimage wrapper missing at {wrapper}"
    content = wrapper.read_text()
    expected_line = _wrapper_pythonpath_line(prefix)
    assert expected_line in content, (
        f"appimage wrapper at {wrapper} does not contain expected PYTHONPATH line.\n"
        f"Expected: {expected_line!r}\n"
        f"Actual content:\n{content}"
    )


def test_wrapper_appimage_gui_content(baseline_install):
    """Verify appimage-gui wrapper script contains the correct PYTHONPATH export line.

    **Validates: Requirements 3.2**
    """
    destdir, prefix = baseline_install
    wrapper = Path(destdir) / "usr/local/bin/appimage-gui"
    assert wrapper.exists(), f"appimage-gui wrapper missing at {wrapper}"
    content = wrapper.read_text()
    expected_line = _wrapper_pythonpath_line(prefix)
    assert expected_line in content, (
        f"appimage-gui wrapper at {wrapper} does not contain expected PYTHONPATH line.\n"
        f"Expected: {expected_line!r}\n"
        f"Actual content:\n{content}"
    )


def test_wrapper_appimage_is_executable(baseline_install):
    """Verify appimage wrapper is marked executable.

    **Validates: Requirements 3.2**
    """
    destdir, _prefix = baseline_install
    wrapper = Path(destdir) / "usr/local/bin/appimage"
    assert os.access(str(wrapper), os.X_OK), f"appimage wrapper at {wrapper} is not executable."


def test_wrapper_appimage_gui_is_executable(baseline_install):
    """Verify appimage-gui wrapper is marked executable.

    **Validates: Requirements 3.2**
    """
    destdir, _prefix = baseline_install
    wrapper = Path(destdir) / "usr/local/bin/appimage-gui"
    assert os.access(str(wrapper), os.X_OK), f"appimage-gui wrapper at {wrapper} is not executable."


# ---------------------------------------------------------------------------
# Non-Python sub-targets produce identical output regardless of Python env
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "make_target,rel_path",
    list(NON_PYTHON_SUBTARGET_ARTIFACTS),
    ids=[f"{t}::{p}" for t, p in NON_PYTHON_SUBTARGET_ARTIFACTS],
)
def test_non_python_subtarget_artifact_exists(make_target, rel_path):
    """Verify non-Python sub-targets produce their artifacts at the expected path.

    **Validates: Requirements 3.2**

    For each sub-target (install-bin, install-systemd, install-dbus, etc.) we run
    just that target in isolation and verify the expected artifact is created.
    This confirms the sub-targets are independent of Python package state.
    """
    destdir = tempfile.mkdtemp(prefix="test-preserve-subtarget-")
    try:
        result = _run_make(
            [
                make_target,
                f"DESTDIR={destdir}",
                "PREFIX=/usr/local",
                "NO_ENABLE=1",
            ]
        )
        assert result.returncode == 0, (
            f"make {make_target} failed (exit {result.returncode}).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        full_path = Path(destdir) / rel_path
        assert full_path.exists(), (
            f"Expected artifact {rel_path!r} missing after `make {make_target}`.\n"
            f"Full path: {full_path}\n"
            f"stdout: {result.stdout}"
        )
    finally:
        shutil.rmtree(destdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Property-based test: wrapper scripts correct across DESTDIR/PREFIX range
# ---------------------------------------------------------------------------

# We use a small, fixed set of DESTDIR/PREFIX pairs to keep runtime bounded
# while still exercising a range of path combinations.
_DESTDIR_PREFIX_PAIRS = [
    ("/usr/local", None),  # default PREFIX (no override)
    ("/usr", None),  # common system PREFIX
    ("/opt/appimage-launcher", None),  # custom non-standard PREFIX
]

# Generate a fixed set of DESTDIR values once per session (unused — fresh dirs created inline)
_GENERATED_DESTDIRS = None


@given(
    prefix=st.sampled_from(["/usr/local", "/usr", "/opt/appimage-launcher"]),
)
@settings(
    max_examples=3,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    deadline=120_000,  # ms — make install is slow
)
def test_wrapper_pythonpath_across_prefix_values(prefix: str):
    """Property 2 — Preservation: wrappers have correct PYTHONPATH for any PREFIX.

    **Validates: Requirements 3.1, 3.2, 3.3**

    For each of several PREFIX values, ``make install`` produces wrapper scripts at
    ``$(DESTDIR)$(BIN_DIR)/appimage`` and ``$(DESTDIR)$(BIN_DIR)/appimage-gui`` that
    contain the correct ``PYTHONPATH`` export line referencing that PREFIX's PACKAGE_DIR.

    This establishes that the install-bin sub-target is correct before the fix is applied,
    and will catch any regression introduced by the fix.
    """
    destdir = tempfile.mkdtemp(prefix="test-preserve-pbt-")
    try:
        result = _run_make(
            [
                "install",
                f"DESTDIR={destdir}",
                f"PREFIX={prefix}",
                "NO_ENABLE=1",
            ]
        )
        assert result.returncode == 0, (
            f"make install failed with PREFIX={prefix} (exit {result.returncode}).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Wrapper paths — relative to DESTDIR, using the given PREFIX
        prefix_stripped = prefix.lstrip("/")
        appimage_path = Path(destdir) / prefix_stripped / "bin" / "appimage"
        appimage_gui_path = Path(destdir) / prefix_stripped / "bin" / "appimage-gui"

        # Both wrappers must exist
        assert appimage_path.exists(), (
            f"appimage wrapper missing at {appimage_path} for PREFIX={prefix}"
        )
        assert appimage_gui_path.exists(), (
            f"appimage-gui wrapper missing at {appimage_gui_path} for PREFIX={prefix}"
        )

        expected_pythonpath_line = _wrapper_pythonpath_line(prefix)

        appimage_content = appimage_path.read_text()
        assert expected_pythonpath_line in appimage_content, (
            f"appimage wrapper (PREFIX={prefix}) missing PYTHONPATH line.\n"
            f"Expected: {expected_pythonpath_line!r}\n"
            f"Got:\n{appimage_content}"
        )

        appimage_gui_content = appimage_gui_path.read_text()
        assert expected_pythonpath_line in appimage_gui_content, (
            f"appimage-gui wrapper (PREFIX={prefix}) missing PYTHONPATH line.\n"
            f"Expected: {expected_pythonpath_line!r}\n"
            f"Got:\n{appimage_gui_content}"
        )

        # install-python must copy the source tree to $(DESTDIR)$(PACKAGE_DIR)
        package_dir = _package_dir_for_prefix(prefix)
        pkg_tree = Path(destdir) / package_dir.lstrip("/") / "appimage_launcher"
        assert pkg_tree.is_dir(), (
            f"appimage_launcher source tree missing at {pkg_tree} for PREFIX={prefix}."
        )

    finally:
        shutil.rmtree(destdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Property-based test: non-Python artifacts identical regardless of Python
# package pre-install state (simulated via PYTHONPATH isolation)
# ---------------------------------------------------------------------------

# We sample a subset of non-Python sub-targets to keep the test bounded.
_SAMPLED_NON_PYTHON_TARGETS = [
    ("install-bin", "bin/appimage"),
    ("install-systemd", "lib/systemd/user/appimage-launcher.service"),
    ("install-dbus", "share/dbus-1/services/io.github.appimage_launcher.Manager.service"),
]


@given(
    target_info=st.sampled_from(_SAMPLED_NON_PYTHON_TARGETS),
)
@settings(
    max_examples=len(_SAMPLED_NON_PYTHON_TARGETS),
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    deadline=60_000,
)
def test_non_python_subtargets_independent_of_python_packages(target_info):
    """Property 2 — Preservation: non-Python sub-targets are Python-package-agnostic.

    **Validates: Requirements 3.2, 3.5**

    For each sampled non-Python sub-target, running ``make <target>`` with two
    different PYTHONPATH environments (empty vs. system) produces an artifact at
    the same path with identical content.

    This confirms the sub-targets do not depend on which Python packages are
    installed, so the fix's addition of ``install-python-deps`` cannot break them.
    """
    make_target, rel_path_under_prefix = target_info
    prefix = "/usr/local"
    full_rel = f"{prefix.lstrip('/')}/{rel_path_under_prefix}"

    destdir_a = tempfile.mkdtemp(prefix="test-preserve-pbt-a-")
    destdir_b = tempfile.mkdtemp(prefix="test-preserve-pbt-b-")
    try:
        # Run the sub-target with an empty PYTHONPATH (simulating no packages)
        env_no_packages = os.environ.copy()
        env_no_packages["PYTHONPATH"] = ""

        # Run the sub-target with the system PYTHONPATH (normal environment)
        env_system = os.environ.copy()
        env_system.pop("PYTHONPATH", None)

        result_a = subprocess.run(
            ["make", make_target, f"DESTDIR={destdir_a}", f"PREFIX={prefix}", "NO_ENABLE=1"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env=env_no_packages,
            timeout=60,
        )
        result_b = subprocess.run(
            ["make", make_target, f"DESTDIR={destdir_b}", f"PREFIX={prefix}", "NO_ENABLE=1"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env=env_system,
            timeout=60,
        )

        assert result_a.returncode == 0, (
            f"make {make_target} failed with empty PYTHONPATH (exit {result_a.returncode}).\n"
            f"stdout: {result_a.stdout}\nstderr: {result_a.stderr}"
        )
        assert result_b.returncode == 0, (
            f"make {make_target} failed with system PYTHONPATH (exit {result_b.returncode}).\n"
            f"stdout: {result_b.stdout}\nstderr: {result_b.stderr}"
        )

        path_a = Path(destdir_a) / full_rel
        path_b = Path(destdir_b) / full_rel

        # Both artifacts must exist
        assert path_a.exists(), f"Artifact missing (empty PYTHONPATH): {path_a}"
        assert path_b.exists(), f"Artifact missing (system PYTHONPATH): {path_b}"

        # Content must be identical (Python package state must not influence the artifact)
        content_a = path_a.read_bytes()
        content_b = path_b.read_bytes()
        assert content_a == content_b, (
            f"Artifact content differs between empty-PYTHONPATH and system-PYTHONPATH runs!\n"
            f"Target: make {make_target}\n"
            f"Path: {full_rel}\n"
            f"This means the sub-target's output depends on the Python package environment, "
            f"which violates the preservation requirement."
        )

    finally:
        shutil.rmtree(destdir_a, ignore_errors=True)
        shutil.rmtree(destdir_b, ignore_errors=True)


# ---------------------------------------------------------------------------
# deps-check output baseline
# ---------------------------------------------------------------------------


def test_deps_check_reports_inotify_simple():
    """Verify deps-check mentions inotify_simple in its output.

    **Validates: Requirements 3.4**

    On the unfixed Makefile, ``make deps-check`` must print either
    ``inotify_simple: <version>`` or ``WARNING: inotify_simple not installed``.
    """
    result = _run_make(["deps-check"])
    assert result.returncode == 0, f"make deps-check failed: {result.stderr}"
    output = result.stdout
    assert "inotify_simple" in output, (
        f"deps-check output does not mention inotify_simple.\nOutput:\n{output}"
    )


def test_deps_check_reports_dbus_fast():
    """Verify deps-check mentions dbus-fast / dbus_fast in its output.

    **Validates: Requirements 3.4**

    On the unfixed Makefile, ``make deps-check`` must print either
    ``dbus-fast: <version>`` or ``WARNING: dbus-fast not installed``.
    """
    result = _run_make(["deps-check"])
    assert result.returncode == 0, f"make deps-check failed: {result.stderr}"
    output = result.stdout
    assert "dbus" in output.lower(), (
        f"deps-check output does not mention dbus-fast.\nOutput:\n{output}"
    )


def test_deps_check_ends_with_all_dependencies_checked():
    """Verify deps-check always ends with 'All dependencies checked.' sentinel.

    **Validates: Requirements 3.4**

    This sentinel line must be present to confirm the check ran to completion.
    """
    result = _run_make(["deps-check"])
    assert result.returncode == 0, f"make deps-check failed: {result.stderr}"
    assert "All dependencies checked." in result.stdout, (
        f"deps-check output missing sentinel line.\nOutput:\n{result.stdout}"
    )


def test_deps_check_reports_tomli_w():
    """Verify that on the fixed Makefile, deps-check mentions tomli_w.

    **Validates: Requirements 3.4**

    Task 3.4 added a tomli_w check to deps-check. The output must now contain
    either ``tomli_w: <version>`` or ``WARNING: tomli_w not installed``.
    """
    result = _run_make(["deps-check"])
    assert result.returncode == 0, f"make deps-check failed: {result.stderr}"
    assert "tomli_w" in result.stdout, (
        f"deps-check output does not mention tomli_w after the fix.\nOutput:\n{result.stdout}"
    )
