# PodFlow — common developer and packaging tasks.
#
#   make run            run from the source tree
#   make test           unit tests (UI/MPRIS tests run under xvfb + dbus when available)
#   make install        install system-wide (PREFIX=/usr/local, DESTDIR supported)
#   make deb            build dist/podflow_<version>_all.deb
#   make flatpak        build and install the Flatpak locally (needs flatpak-builder)

APP_ID      := io.github.euvinicios.PodFlow
PREFIX      ?= /usr/local
PYTHON      ?= /usr/bin/python3
DESTDIR     ?=
BINDIR      := $(PREFIX)/bin
DATADIR     := $(PREFIX)/share
PKGDATADIR  := $(DATADIR)/podflow
VERSION     := $(shell sed -n 's/^VERSION = "\(.*\)"/\1/p' src/config.py)

HEADLESS := $(shell command -v dbus-run-session >/dev/null 2>&1 && echo "dbus-run-session --") \
            $(shell command -v xvfb-run >/dev/null 2>&1 && echo "xvfb-run -a -s '-screen 0 1600x1000x24'")

.PHONY: all run test test-live smoke screenshots validate install uninstall deb flatpak clean version

all: run

run:
	$(PYTHON) -m src

version:
	@echo $(VERSION)

test:
	$(HEADLESS) env GDK_BACKEND=x11 GSK_RENDERER=cairo $(PYTHON) -W ignore::DeprecationWarning \
		-m unittest discover -s tests -t . -v

test-live:
	PODFLOW_LIVE_TESTS=1 PODFLOW_OFFLINE=0 $(PYTHON) -m unittest tests.test_apple_service -v

smoke:
	$(HEADLESS) env GDK_BACKEND=x11 $(PYTHON) tools/screenshots.py --smoke

screenshots:
	$(HEADLESS) env GDK_BACKEND=x11 $(PYTHON) tools/screenshots.py --narrow

validate:
	desktop-file-validate data/$(APP_ID).desktop
	appstreamcli validate --no-net --explain data/$(APP_ID).metainfo.xml

install:
	install -d "$(DESTDIR)$(PKGDATADIR)/podflow"
	cp -R src/. "$(DESTDIR)$(PKGDATADIR)/podflow/"
	find "$(DESTDIR)$(PKGDATADIR)" -name __pycache__ -prune -exec rm -rf {} +
	install -d "$(DESTDIR)$(BINDIR)"
	sed -e 's|@PYTHON@|$(PYTHON)|' -e 's|@PKGDATADIR@|$(PKGDATADIR)|' build-aux/podflow.in \
		> "$(DESTDIR)$(BINDIR)/podflow"
	chmod 755 "$(DESTDIR)$(BINDIR)/podflow"
	install -Dm644 data/$(APP_ID).desktop "$(DESTDIR)$(DATADIR)/applications/$(APP_ID).desktop"
	install -Dm644 data/$(APP_ID).metainfo.xml "$(DESTDIR)$(DATADIR)/metainfo/$(APP_ID).metainfo.xml"
	install -Dm644 data/icons/hicolor/scalable/apps/$(APP_ID).svg \
		"$(DESTDIR)$(DATADIR)/icons/hicolor/scalable/apps/$(APP_ID).svg"
	install -Dm644 data/icons/hicolor/symbolic/apps/$(APP_ID)-symbolic.svg \
		"$(DESTDIR)$(DATADIR)/icons/hicolor/symbolic/apps/$(APP_ID)-symbolic.svg"

uninstall:
	rm -rf "$(DESTDIR)$(PKGDATADIR)"
	rm -f "$(DESTDIR)$(BINDIR)/podflow" \
		"$(DESTDIR)$(DATADIR)/applications/$(APP_ID).desktop" \
		"$(DESTDIR)$(DATADIR)/metainfo/$(APP_ID).metainfo.xml" \
		"$(DESTDIR)$(DATADIR)/icons/hicolor/scalable/apps/$(APP_ID).svg" \
		"$(DESTDIR)$(DATADIR)/icons/hicolor/symbolic/apps/$(APP_ID)-symbolic.svg"

deb:
	build-aux/deb/build-deb.sh

flatpak:
	flatpak-builder --user --install --force-clean build/flatpak build-aux/flatpak/$(APP_ID).json

clean:
	rm -rf build dist .test-data
	find . -name __pycache__ -prune -exec rm -rf {} +
