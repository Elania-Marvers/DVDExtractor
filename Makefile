.PHONY: all run build-native logs clean-logs clean storage

PYTHON ?= python3

all: run

build-native:
	$(MAKE) -C native

run: build-native
	@for file in ffmpeg-*.log; do [ -f "$$file" ] && rm -f "$$file" || true; done
	@mkdir -p logs
	$(PYTHON) main.py

logs:
	@mkdir -p logs
	@echo "Server logs:"
	@ls -1 logs | sed -n '1,20p'

clean-logs:
	@rm -f ffmpeg-*.log || true
	@rm -f dvd_*.log || true
	@mkdir -p logs && rm -f logs/*.log || true
	@echo "Logs cleaned"

storage:
	$(PYTHON) -c "from dvdapp.config import build_settings; s = build_settings('127.0.0.1', 8080, 2.0); print(s.storage_path)"

clean:
	$(MAKE) -C native clean
	rm -rf storage_local
