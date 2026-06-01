.PHONY: all run build-native build-go-runner test logs clean-logs clean-media-temp clean-runtime clean storage clean-cache

PYTHON ?= python3
GO ?= go
GO_CACHE := $(CURDIR)/.cache/go-build

all: run

LOG_PATTERNS := *.log *.log.[0-9]* ffmpeg-*.log dvd_*.log dvdvob_*.txt
LOG_DIR := logs
LOG_SEARCH_PATHS := . $(LOG_DIR)
GO_TARGET := bin/dvd_homebrew_runner

build-native:
	$(MAKE) -C native

build-go-runner:
	@if ! command -v $(GO) >/dev/null 2>&1; then \
		echo "Go not installed"; \
		exit 1; \
	fi
	@mkdir -p bin $(GO_CACHE)
	cd tools/go_homebrew_runner && GOCACHE=$(GO_CACHE) $(GO) build -o ../../$(GO_TARGET) .

run: build-native build-go-runner clean-logs
	$(PYTHON) main.py

test: build-native build-go-runner clean-logs
	$(PYTHON) -m py_compile main.py dvdapp/*.py dvdapp/execution/*.py dvdapp/execution/runners/*.py dvdapp/extraction/*.py dvdapp/extraction/strategies/*.py dvdapp/models/*.py dvdapp/native_runtime/*.py
	$(PYTHON) -m unittest discover -s tests -p "test_*.py"

logs:
	@mkdir -p $(LOG_DIR)
	@echo "Server logs:"
	@ls -1 $(LOG_DIR) | sed -n '1,20p'

clean-logs:
	@for path in $(LOG_SEARCH_PATHS); do \
		for pattern in $(LOG_PATTERNS); do \
			find "$$path" -type f -name "$$pattern" -delete 2>/dev/null || true; \
		done; \
	done
	@find . -maxdepth 5 -type f \( -name "*.log" -o -name "*.log.[0-9]*" -o -name "*.log.*" \) -delete 2>/dev/null || true
	@echo "Logs cleaned"

clean-media-temp:
	@find storage_local build_go_homebrew /tmp -maxdepth 1 -type f \( -name ".dvd_native_title_*.vob" -o -name "homebrew_title_*.vob" \) -delete 2>/dev/null || true
	@find /tmp -maxdepth 1 -type f \( -name "dvdvob_concat_*.txt" -o -name "dvdvob_copy_*.txt" \) -delete 2>/dev/null || true
	@echo "Temporary media artifacts cleaned"

clean-runtime:
	@$(MAKE) clean-logs
	@$(MAKE) clean-media-temp
	@rm -rf $(GO_CACHE)
	@$(MAKE) -C native clean
	@find . -name "__pycache__" -type d -prune -exec rm -rf {} +
	@find . \( -name "*.pyc" -o -name "*.pyo" \) -type f -delete
	@rm -f $(GO_TARGET)
	@rm -rf build_go_homebrew
	@echo "Python runtime cleaned"

clean-cache:
	@rm -rf $(GO_CACHE)
	@find . -name "__pycache__" -type d -prune -exec rm -rf {} +
	@find . \( -name "*.pyc" -o -name "*.pyo" \) -type f -delete
	@echo "Cache cleaned"

storage:
	$(PYTHON) -c "from dvdapp.config import build_settings; s = build_settings('127.0.0.1', 8080, 2.0); print(s.storage_path)"

clean: clean-runtime
	rm -rf storage_local
	@echo "all cleaned"
