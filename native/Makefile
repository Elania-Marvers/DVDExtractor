.PHONY: all clean bench

CC = clang
CXX = clang++
PKG_CONFIG = pkg-config
CFLAGS = -O3 -fPIC
CPPFLAGS = -O3 -std=c++17 -Iinclude
AR = ar
BUILD_DIR = build
PKG_CFLAGS = $(shell $(PKG_CONFIG) --cflags dvdread 2>/dev/null)
PKG_LDFLAGS = $(shell $(PKG_CONFIG) --libs dvdread 2>/dev/null)

ifeq ($(strip $(PKG_CFLAGS)),)
PKG_CFLAGS = -I/usr/local/Cellar/libdvdread/7.0.1/include
endif

ifeq ($(strip $(PKG_LDFLAGS)),)
PKG_LDFLAGS = -L/usr/local/Cellar/libdvdread/7.0.1/lib -ldvdread
endif

CPP_SOURCES = \
	src/analysis/analyze_entropy.cpp \
	src/analysis/entropy.cpp \
	src/homebrew/commands.cpp \
	src/homebrew/file_copier.cpp \
	src/homebrew/native_extractor.cpp \
	src/homebrew/process_runner.cpp \
	src/homebrew/progress_ticker.cpp \
	src/homebrew/transfer_engines.cpp \
	src/homebrew/vob_scanner.cpp \
	src/homebrew/main.cpp \
	src/reader_dump/dvd_reader_dump.cpp \
	src/signal_probe/dvd_signal_probe.cpp \
	src/vob_manifest/vob_manifest.cpp

CPP_OBJECTS = $(CPP_SOURCES:src/%.cpp=build/%.o)

C_SOURCES = src/asm/entropy_bridge.c
C_OBJECTS = $(C_SOURCES:src/%.c=build/%.o)

ASM_SOURCES = src/asm/asm_sum.s \
	src/asm/mem_ops.s \
	src/asm/string_ops.s \
	src/asm/mem_ops_avx2.s \
	src/asm/io_ops.s
ASM_OBJECTS = $(ASM_SOURCES:src/%.s=build/%.o)

TARGETS = $(BUILD_DIR)/dvd_entropy \
		$(BUILD_DIR)/dvd_vob_manifest \
		$(BUILD_DIR)/dvd_reader_dump \
		$(BUILD_DIR)/dvd_homebrew \
		$(BUILD_DIR)/dvd_signal_probe \
		$(BUILD_DIR)/byte_sum_bench

all: $(TARGETS)

$(BUILD_DIR)/dvd_entropy: build/analysis/analyze_entropy.o build/analysis/entropy.o $(C_OBJECTS) $(ASM_OBJECTS)
	mkdir -p $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) -o $@ $^

$(BUILD_DIR)/dvd_vob_manifest: build/vob_manifest/vob_manifest.o
	mkdir -p $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) -o $@ $^

$(BUILD_DIR)/dvd_reader_dump: build/reader_dump/dvd_reader_dump.o
	mkdir -p $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) $^ -o $@ $(PKG_LDFLAGS)

$(BUILD_DIR)/dvd_homebrew: build/homebrew/main.o \
		build/homebrew/commands.o \
		build/homebrew/file_copier.o \
		build/homebrew/native_extractor.o \
		build/homebrew/process_runner.o \
		build/homebrew/progress_ticker.o \
		build/homebrew/transfer_engines.o \
		build/homebrew/vob_scanner.o \
		build/asm/io_ops.o
	mkdir -p $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) $^ -o $@

$(BUILD_DIR)/dvd_signal_probe: build/signal_probe/dvd_signal_probe.o build/analysis/entropy.o $(C_OBJECTS) $(ASM_OBJECTS)
	mkdir -p $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) -o $@ $^

$(BUILD_DIR)/byte_sum_bench: build/analysis/byte_sum_bench.o build/analysis/entropy.o $(C_OBJECTS) $(ASM_OBJECTS)
	mkdir -p $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) -o $@ $^

bench: $(BUILD_DIR)/byte_sum_bench
	$(BUILD_DIR)/byte_sum_bench $(BENCH_ARGS)

build/%.o: src/%.cpp
	mkdir -p $(dir $@)
	$(CXX) $(CPPFLAGS) $(PKG_CFLAGS) -c $< -o $@

build/%.o: src/%.c
	mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c $< -o $@

build/%.o: src/%.s
	mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c $< -o $@

build/asm/mem_ops_avx2.o: src/asm/mem_ops_avx2.s
	mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -mavx2 -c $< -o $@

clean:
	rm -rf $(BUILD_DIR)
