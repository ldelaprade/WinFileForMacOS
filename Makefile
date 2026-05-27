# ── Configuration ────────────────────────────────────────────────────────────
APP_NAME      := eXPlorer
APP_BUNDLE    := dist/$(APP_NAME).app
DMG_NAME      := dist/$(APP_NAME).dmg
ENTRY         := main.py

# Code-signing: fill in before running `make release`
DEV_ID        ?= Developer ID Application: Your Name (TEAMID)
PROFILE       ?= notarytool-profile
BUNDLE_ID     ?= com.yourname.explorer

.PHONY: all build release dmg notarize staple clean clean-all

all: build

# ── Local unsigned build ──────────────────────────────────────────────────────
build:
	pip install -q pyinstaller
	pyinstaller --windowed \
	            --name "$(APP_NAME)" \
	            --noconfirm \
	            $(ENTRY)
	@echo
	@echo "Built: $(APP_BUNDLE)"
	@echo "Drag to /Applications or run: open $(APP_BUNDLE)"

# ── Signed release build ─────────────────────────────────────────────────────
# Prerequisites:
#   1. Apple Developer ID certificate in Keychain
#   2. App-specific password stored: xcrun notarytool store-credentials $(PROFILE)
#   3. brew install create-dmg  (for the dmg target)

release: build sign dmg notarize staple
	@echo "✓ Signed, notarized and stapled: $(DMG_NAME)"

sign:
	codesign --deep --force --options runtime \
	         --sign "$(DEV_ID)" \
	         --entitlements entitlements.plist \
	         "$(APP_BUNDLE)"

dmg:
	create-dmg \
	  --volname "$(APP_NAME)" \
	  --window-size 540 380 \
	  --icon-size 96 \
	  --app-drop-link 380 150 \
	  "$(DMG_NAME)" \
	  "$(APP_BUNDLE)"

notarize:
	xcrun notarytool submit "$(DMG_NAME)" \
	      --keychain-profile "$(PROFILE)" \
	      --bundle-id "$(BUNDLE_ID)" \
	      --wait

staple:
	xcrun stapler staple "$(DMG_NAME)"

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache

clean-all: clean
	rm -rf build dist *.spec
