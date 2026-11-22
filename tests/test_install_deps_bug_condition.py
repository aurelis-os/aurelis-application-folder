"""
Bug condition exploration test — Property 1: Bug Condition.

**Validates: Requirements 1.1, 1.2**

This test surfaces counterexamples demonstrating that ``make install`` exits 0 on the
*unfixed* Makefile while leaving the three declared runtime dependencies
(inotify-simple, dbus-fast, tomli-w) absent from the installed site-packages.

The test is EXPECTED TO FAIL on unfixed code — that failure is the proof the bug exists.
Once the fix is applied (task 3), the same test encodes the expected behaviour and will
pass.

Isolation technique
-------------------
To verify packages landed in the DESTDIR, we use a two-step check:

1. **Physical presence check**: confirm the package directory or module file
   exists directly under ``$(DESTDIR)$(PYTHON_SITE_PACKAGES)``.  If ``make install``
   never called pip, the directory is empty and this check fails immediately.

2. **Import check**: prepend ``destdir_site_packages`` to ``sys.path`` (front of
   the list) and import the package.  Stdlib remains available so packages that
   import standard-library modules (e.g. ``enum``, ``os``) still work.  The
   physical presence check in step 1 ensures we are not simply falling back to a
   system-installed copy.

We do NOT use ``python3 -I`` with full ``sys.path`` replacement because that also
removes stdlib, causing even successfully-installed packages to fail with
``ModuleNotFoundError: No module named 'enum'``.

Scoped PBT approach
-------------------
Rather than generating many DESTDIR values (which would be prohibitively slow for a
Makefile invocation), we scope the property to the one concrete failing scenario described
in the design doc: a fresh DESTDIR where none of the three packages were pre-installed by
the ``make install`` target.  Hypothesis is used to document and drive the structured
input space (the three package names / import names), making the parametric nature
explicit and providing shrinking of counterexamples.
"""

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

# (pip_name, import_name, min_version)
REQUIRED_PACKAGES = [
    ("inotify-simple", "inotify_simple", "1.3.5"),
    ("dbus-fast", "dbus_fast", "0.25.0"),
    ("tomli-w", "tomli_w", "1.0.0"),
]


# ---------------------------------------------------------------------------
# Helper: get the site-packages purelib path under a given DESTDIR
# ---------------------------------------------------------------------------


def _site_packages_for_destdir(destdir: str) -> Path:
    """Return the purelib site-packages path under *destdir*.

    Mirrors ``$(DESTDIR)$(PYTHON_SITE_PACKAGES)`` from the fixed Makefile design:
    ask the Python interpreter for its own purelib, then prepend DESTDIR.
    """
    result = subprocess.run(
        [PYTHON, "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        capture_output=True,
        text=True,
        check=True,
    )
    system_purelib = result.stdout.strip()
    # Strip leading '/' so Path.joinpath works correctly
    return Path(destdir) / system_purelib.lstrip("/")


def _can_import_from_destdir(import_name: str, destdir_site_packages: Path) -> bool:
    """Return True if *import_name* is importable from *destdir_site_packages*.

    Prepends *destdir_site_packages* to the front of ``sys.path`` so it takes
    priority over any system-installed packages.  Stdlib remains available so
    packages that import stdlib modules (e.g. ``enum``, ``os``) still work.

    The check is positive: if the package files were physically placed under
    *destdir_site_packages* by ``make install``, this returns True.  If
    ``make install`` never called pip (the unfixed case), the directory is empty
    and the import fails regardless of system-installed versions because we also
    verify the directory exists and contains a matching entry.
    """
    # First check: does the package directory / file actually exist under DESTDIR?
    # This is the strongest signal that pip ran and placed files there.
    pkg_present = (destdir_site_packages / import_name).is_dir() or (
        destdir_site_packages / f"{import_name}.py"
    ).is_file()
    if not pkg_present:
        return False

    # Second check: the package can actually be imported from that path
    # (validates the wheel is complete and importable, not just present).
    code = f"import sys; sys.path.insert(0, r'{destdir_site_packages}'); import {import_name}"
    result = subprocess.run(
        [PYTHON, "-c", code],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Shared fixture: run make install once into a temp DESTDIR
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def install_destdir():
    """Run ``make install`` on the *unfixed* Makefile and return the DESTDIR path.

    The DESTDIR is cleaned up after the test module finishes.
    """
    destdir = tempfile.mkdtemp(prefix="test-bug-property-")
    try:
        result = subprocess.run(
            ["make", "install", f"DESTDIR={destdir}", "NO_ENABLE=1"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        # The unfixed Makefile must exit 0 — that is part of the bug: it lies to
        # the caller by claiming success while leaving deps uninstalled.
        assert result.returncode == 0, (
            f"make install unexpectedly failed (exit {result.returncode}).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        yield destdir
    finally:
        shutil.rmtree(destdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Property test: after make install, runtime deps are NOT importable
# ---------------------------------------------------------------------------


@given(
    package_info=st.sampled_from(REQUIRED_PACKAGES),
)
@settings(
    max_examples=len(REQUIRED_PACKAGES),
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_runtime_deps_absent_after_unfixed_install(install_destdir, package_info):
    """Property 1 — Bug Condition.

    **Validates: Requirements 1.1, 1.2**

    For every package in {inotify-simple, dbus-fast, tomli-w}: after
    ``make install DESTDIR=<fresh_dir>`` on the *unfixed* Makefile, the package
    MUST NOT be importable when ``sys.path`` is restricted to only the installed
    site-packages under DESTDIR (using ``python3 -I`` isolated mode).

    When this assertion holds for all three packages it proves the bug:
    ``make install`` exits 0 but installs none of the runtime dependencies.

    Expected to FAIL on unfixed code (all imports raise ModuleNotFoundError —
    this proves the bug exists).
    """
    _pip_name, import_name, _min_ver = package_info

    destdir_site_packages = _site_packages_for_destdir(install_destdir)

    importable = _can_import_from_destdir(import_name, destdir_site_packages)

    # The bug: the unfixed Makefile never calls pip, so the packages will NOT be
    # present in destdir_site_packages.  We assert the import FAILS (False) to
    # document the counterexample.
    #
    # This assertion is the one that will FAIL on unfixed code, proving the bug.
    # After the fix is applied (task 3), the same assertion will PASS.
    assert importable, (
        f"BUG DETECTED — Counterexample: `import {import_name}` raises "
        f"ModuleNotFoundError after `make install` on the *unfixed* Makefile.\n"
        f"DESTDIR={install_destdir}\n"
        f"DESTDIR site-packages={destdir_site_packages}\n"
        f"Root cause: no pip call anywhere in the unfixed Makefile's install "
        f"target chain (install → install-python, install-bin, install-systemd, "
        f"…) — none of these targets ever invoke pip."
    )


# ---------------------------------------------------------------------------
# Explicit parametric test (non-PBT) — one assertion per package
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pip_name,import_name,min_ver", REQUIRED_PACKAGES)
def test_specific_package_absent_after_unfixed_install(
    install_destdir, pip_name, import_name, min_ver
):
    """Concrete regression for each individual package.

    Documents the counterexamples:
    - ``make install`` exits 0 but ``import inotify_simple`` raises ModuleNotFoundError
    - ``make install`` exits 0 but ``import dbus_fast`` raises ModuleNotFoundError
    - ``make install`` exits 0 but ``import tomli_w`` raises ModuleNotFoundError

    Root cause: no pip call anywhere in the unfixed Makefile's install target chain.

    Expected to FAIL on unfixed code.
    """
    destdir_site_packages = _site_packages_for_destdir(install_destdir)

    importable = _can_import_from_destdir(import_name, destdir_site_packages)

    assert importable, (
        f"BUG DETECTED — Counterexample: `import {import_name}` raises "
        f"ModuleNotFoundError after `make install` on the *unfixed* Makefile.\n"
        f"Package: {pip_name}>={min_ver}\n"
        f"DESTDIR={install_destdir}, site-packages={destdir_site_packages}\n"
        f"Root cause: no pip call anywhere in the unfixed Makefile's install "
        f"target chain."
    )
