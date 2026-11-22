# AppImage Launcher Makefile
# Variables
PREFIX ?= /usr/local
DESTDIR ?=
SYSCONFDIR ?= /etc
PYTHON ?= python3
NO_ENABLE ?=

# Directories
SRC_DIR = src/appimage_launcher
PACKAGE_DIR = $(PREFIX)/lib/appimage-launcher/python
BIN_DIR = $(PREFIX)/bin
SYSTEMD_USER_DIR = $(PREFIX)/lib/systemd/user
DBUS_SERVICE_DIR = $(PREFIX)/share/dbus-1/services
DBUS_INTERFACE_DIR = $(PREFIX)/share/dbus-1/interfaces
APPLICATIONS_DIR = $(PREFIX)/share/applications
KIO_SERVICEMENU_DIR = $(PREFIX)/share/kio/servicemenus
ICONS_DIR = $(PREFIX)/share/icons/hicolor/scalable/apps
BASH_COMPLETION_DIR = $(PREFIX)/share/bash-completion/completions
ZSH_COMPLETION_DIR = $(PREFIX)/share/zsh/site-functions
FISH_COMPLETION_DIR = $(PREFIX)/share/fish/vendor_completions.d
MAN_DIR = $(PREFIX)/share/man/man1
DOC_DIR = $(PREFIX)/share/doc/appimage-launcher
SYSCONFIG_DIR = $(SYSCONFDIR)/appimage-launcher

# Python package
PYTHON_PACKAGE = appimage_launcher

# Default target
.DEFAULT_GOAL := help

# Help target
.PHONY: help
help:
	@echo "AppImage Launcher - Build and Install"
	@echo ""
	@echo "Targets:"
	@echo "  make help           - Show this help"
	@echo "  make deps-check     - Verify Python version and runtime tools"
	@echo "  make lint           - Run ruff and mypy"
	@echo "  make fmt            - Format code with ruff"
	@echo "  make test           - Run pytest (unit + integration)"
	@echo "  make ci             - Run full CI pipeline (deps-check + lint + test + coverage)"
	@echo "  make run            - Run daemon in development mode"
	@echo "  make install        - Install system-wide"
	@echo "  make uninstall      - Uninstall system-wide"
	@echo "  make package        - Build sdist/tarball"
	@echo "  make clean          - Remove build artifacts"

# Dependency check
.PHONY: deps-check
deps-check:
	@echo "Checking dependencies..."
	@$(PYTHON) --version
	@which unsquashfs >/dev/null || echo "WARNING: unsquashfs not found (squashfs-tools)"
	@which squashfuse >/dev/null || echo "WARNING: squashfuse not found"
	@which appimageupdatetool >/dev/null || echo "WARNING: appimageupdatetool not found"
	@which zsync >/dev/null || echo "WARNING: zsync not found"
	@echo "Checking Python packages..."
	@$(PYTHON) -c "import inotify_simple; print('inotify_simple:', inotify_simple.__version__)" 2>/dev/null || echo "WARNING: inotify_simple not installed"
	@$(PYTHON) -c "import dbus_fast; print('dbus-fast:', dbus_fast.__version__)" 2>/dev/null || echo "WARNING: dbus-fast not installed"
	@echo "All dependencies checked."

# Linting
.PHONY: lint
lint:
	@echo "Running ruff check..."
	$(PYTHON) -m ruff check .
	@echo "Running ruff format check..."
	$(PYTHON) -m ruff format --check .
	@echo "Running mypy..."
	$(PYTHON) -m mypy src

# Formatting
.PHONY: fmt
fmt:
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

# Testing
.PHONY: test
test:
	@if [ "$(COV)" = "1" ]; then \
		PYTHONPATH=src $(PYTHON) -m pytest -q --cov=src/appimage_launcher/core --cov-fail-under=30; \
	else \
		PYTHONPATH=src $(PYTHON) -m pytest -q; \
	fi

# CI pipeline
.PHONY: ci
ci: deps-check lint test
	@echo "Running additional CI checks..."
	@which shellcheck >/dev/null && shellcheck packaging/*.sh 2>/dev/null || true
	@which desktop-file-validate >/dev/null && desktop-file-validate packaging/applications/*.desktop 2>/dev/null || true
	@which systemd-analyze >/dev/null && systemd-analyze --user verify packaging/systemd/appimage-launcher.service 2>/dev/null || true
	@echo "Dry-run install..."
	@$(MAKE) install DESTDIR=/tmp/appimage-launcher-test NO_ENABLE=1
	@echo "Verifying installed files..."
	@test -f /tmp/appimage-launcher-test$(BIN_DIR)/appimage
	@test -f /tmp/appimage-launcher-test$(SYSTEMD_USER_DIR)/appimage-launcher.service
	@test -f /tmp/appimage-launcher-test$(DBUS_SERVICE_DIR)/io.github.appimage_launcher.Manager.service
	@test -f /tmp/appimage-launcher-test$(DBUS_INTERFACE_DIR)/io.github.appimage_launcher.Manager1.xml
	@test -f /tmp/appimage-launcher-test$(APPLICATIONS_DIR)/appimage-launcher.desktop
	@test -f /tmp/appimage-launcher-test$(KIO_SERVICEMENU_DIR)/appimage-launcher.desktop
	@test -f /tmp/appimage-launcher-test$(ICONS_DIR)/appimage-launcher.svg
	@test -f /tmp/appimage-launcher-test$(BASH_COMPLETION_DIR)/appimage
	@test -f /tmp/appimage-launcher-test$(ZSH_COMPLETION_DIR)/_appimage
	@test -f /tmp/appimage-launcher-test$(FISH_COMPLETION_DIR)/appimage.fish
	@test -f /tmp/appimage-launcher-test$(MAN_DIR)/appimage.1
	@test -f /tmp/appimage-launcher-test$(DOC_DIR)/README.md
	@test -f /tmp/appimage-launcher-test$(DOC_DIR)/SPEC.md
	@echo "CI pipeline passed!"

# Development run
.PHONY: run
run:
	@mkdir -p .dev/Applications .dev/.config/appimage-launcher .dev/.local/state/appimage-launcher .dev/.local/share/applications .dev/.local/share/icons/hicolor
	APPIMAGE_LAUNCHER_HOME=./.dev PYTHONPATH=src $(PYTHON) -m appimage_launcher.daemon --dir .dev/Applications --dev

# Installation
.PHONY: install
install: install-python install-bin install-systemd install-dbus install-desktop install-kio install-icons install-completions install-man install-doc install-config
	@if [ -z "$(DESTDIR)" ] && [ -z "$(NO_ENABLE)" ]; then \
		echo "Enabling systemd user service globally..."; \
		systemctl --global enable appimage-launcher.service; \
		echo "Enabled for all users. It starts at each user's next login."; \
		echo "To start now: systemctl --user enable --now appimage-launcher"; \
	fi

.PHONY: install-python
install-python:
	@echo "Installing Python package..."
	@mkdir -p $(DESTDIR)$(PACKAGE_DIR)
	@cp -r $(SRC_DIR) $(DESTDIR)$(PACKAGE_DIR)/
	@$(PYTHON) -m compileall -q $(DESTDIR)$(PACKAGE_DIR)/$(PYTHON_PACKAGE)

.PHONY: install-bin
install-bin:
	@echo "Installing binaries..."
	@mkdir -p $(DESTDIR)$(BIN_DIR)
	@printf '#!/bin/sh\nexport PYTHONPATH="$(PACKAGE_DIR):$${PYTHONPATH}"\nexec $(PYTHON) -m appimage_launcher.cli "$$@"\n' > $(DESTDIR)$(BIN_DIR)/appimage
	@chmod +x $(DESTDIR)$(BIN_DIR)/appimage
	@printf '#!/bin/sh\nexport PYTHONPATH="$(PACKAGE_DIR):$${PYTHONPATH}"\nexec $(PYTHON) -m appimage_launcher.gui.main "$$@"\n' > $(DESTDIR)$(BIN_DIR)/appimage-gui
	@chmod +x $(DESTDIR)$(BIN_DIR)/appimage-gui

.PHONY: install-systemd
install-systemd:
	@echo "Installing systemd service..."
	@mkdir -p $(DESTDIR)$(SYSTEMD_USER_DIR)
	@sed "s|@PREFIX@|$(PREFIX)|g" packaging/systemd/appimage-launcher.service > $(DESTDIR)$(SYSTEMD_USER_DIR)/appimage-launcher.service

.PHONY: install-dbus
install-dbus:
	@echo "Installing D-Bus service and interface..."
	@mkdir -p $(DESTDIR)$(DBUS_SERVICE_DIR)
	@mkdir -p $(DESTDIR)$(DBUS_INTERFACE_DIR)
	@printf '[D-BUS Service]\nName=io.github.appimage_launcher.Manager\nExec=$(PREFIX)/bin/appimage daemon\nSystemdService=appimage-launcher.service\n' > $(DESTDIR)$(DBUS_SERVICE_DIR)/io.github.appimage_launcher.Manager.service
	@cp packaging/dbus/io.github.appimage_launcher.Manager1.xml $(DESTDIR)$(DBUS_INTERFACE_DIR)/

.PHONY: install-desktop
install-desktop:
	@echo "Installing desktop entry..."
	@mkdir -p $(DESTDIR)$(APPLICATIONS_DIR)
	@cp packaging/applications/appimage-launcher.desktop $(DESTDIR)$(APPLICATIONS_DIR)/

.PHONY: install-kio
install-kio:
	@echo "Installing Dolphin service menu..."
	@mkdir -p $(DESTDIR)$(KIO_SERVICEMENU_DIR)
	@cp packaging/kio/appimage-launcher.desktop $(DESTDIR)$(KIO_SERVICEMENU_DIR)/

.PHONY: install-icons
install-icons:
	@echo "Installing icons..."
	@mkdir -p $(DESTDIR)$(ICONS_DIR)
	@cp packaging/icons/appimage-launcher.svg $(DESTDIR)$(ICONS_DIR)/

.PHONY: install-completions
install-completions:
	@echo "Installing shell completions..."
	@mkdir -p $(DESTDIR)$(BASH_COMPLETION_DIR)
	@mkdir -p $(DESTDIR)$(ZSH_COMPLETION_DIR)
	@mkdir -p $(DESTDIR)$(FISH_COMPLETION_DIR)
	@cp packaging/completions/appimage.bash $(DESTDIR)$(BASH_COMPLETION_DIR)/appimage
	@cp packaging/completions/_appimage $(DESTDIR)$(ZSH_COMPLETION_DIR)/
	@cp packaging/completions/appimage.fish $(DESTDIR)$(FISH_COMPLETION_DIR)/

.PHONY: install-man
install-man:
	@echo "Installing man page..."
	@mkdir -p $(DESTDIR)$(MAN_DIR)
	@cp packaging/man/appimage.1 $(DESTDIR)$(MAN_DIR)/

.PHONY: install-doc
install-doc:
	@echo "Installing documentation..."
	@mkdir -p $(DESTDIR)$(DOC_DIR)
	@cp README.md $(DESTDIR)$(DOC_DIR)/
	@cp docs/aurelis-application-folder-spec.md $(DESTDIR)$(DOC_DIR)/SPEC.md

.PHONY: install-config
install-config:
	@echo "Installing default config..."
	@mkdir -p $(DESTDIR)$(SYSCONFIG_DIR)
	@if [ ! -f $(DESTDIR)$(SYSCONFIG_DIR)/config.toml ]; then \
		cp packaging/config/config.toml $(DESTDIR)$(SYSCONFIG_DIR)/; \
	fi

# Uninstallation
.PHONY: uninstall
uninstall:
	@echo "Uninstalling..."
	@if [ -z "$(DESTDIR)" ] && [ -z "$(NO_ENABLE)" ]; then \
		systemctl --global disable appimage-launcher.service 2>/dev/null || true; \
	fi
	@rm -f $(DESTDIR)$(BIN_DIR)/appimage
	@rm -f $(DESTDIR)$(BIN_DIR)/appimage-gui
	@rm -rf $(DESTDIR)$(PACKAGE_DIR)
	@rm -f $(DESTDIR)$(SYSTEMD_USER_DIR)/appimage-launcher.service
	@rm -f $(DESTDIR)$(DBUS_SERVICE_DIR)/io.github.appimage_launcher.Manager.service
	@rm -f $(DESTDIR)$(DBUS_INTERFACE_DIR)/io.github.appimage_launcher.Manager1.xml
	@rm -f $(DESTDIR)$(APPLICATIONS_DIR)/appimage-launcher.desktop
	@rm -f $(DESTDIR)$(KIO_SERVICEMENU_DIR)/appimage-launcher.desktop
	@rm -f $(DESTDIR)$(ICONS_DIR)/appimage-launcher.svg
	@rm -f $(DESTDIR)$(BASH_COMPLETION_DIR)/appimage
	@rm -f $(DESTDIR)$(ZSH_COMPLETION_DIR)/_appimage
	@rm -f $(DESTDIR)$(FISH_COMPLETION_DIR)/appimage.fish
	@rm -f $(DESTDIR)$(MAN_DIR)/appimage.1
	@rm -rf $(DESTDIR)$(DOC_DIR)
	@echo "Uninstall complete. User config and data preserved."

# Packaging
.PHONY: package
package:
	@echo "Building package..."
	@$(PYTHON) -m build --sdist --wheel

# Clean
.PHONY: clean
clean:
	@echo "Cleaning build artifacts..."
	@rm -rf build dist *.egg-info
	@rm -rf .dev
	@rm -rf /tmp/appimage-launcher-test
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete
	@find . -type f -name "*.pyo" -delete
	@find . -type f -name ".coverage" -delete
	@rm -rf .pytest_cache .mypy_cache .ruff_cache
	@echo "Clean complete."