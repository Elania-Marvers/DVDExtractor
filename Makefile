.PHONY: all run build-native clean storage

PYTHON ?= python3

all: run

build-native:
	$(MAKE) -C native

run: build-native
	$(PYTHON) main.py

storage:
	$(PYTHON) -c "from dvdapp.config import build_settings; s = build_settings('127.0.0.1', 8080, 2.0); print(s.storage_path)"

clean:
	$(MAKE) -C native clean
	rm -rf storage_local
