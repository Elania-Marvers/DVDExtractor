.PHONY: all clean

CC = clang
CXX = clang++
PKG_CONFIG = pkg-config
CFLAGS = -O3 -fPIC
CPPFLAGS = -O3 -std=c++17
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
TARGETS = $(BUILD_DIR)/dvd_entropy $(BUILD_DIR)/dvd_vob_manifest $(BUILD_DIR)/dvd_reader_dump

all: $(TARGETS)

$(BUILD_DIR)/dvd_entropy: analyze_entropy.o entropy_bridge.o asm_sum.o
	mkdir -p $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) -o $@ $^

$(BUILD_DIR)/dvd_vob_manifest: vob_manifest.o
	mkdir -p $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) -o $@ $^

$(BUILD_DIR)/dvd_reader_dump: dvd_reader_dump.o
	mkdir -p $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) $^ -o $@ $(PKG_LDFLAGS)

analyze_entropy.o: analyze_entropy.cpp
	$(CXX) $(CPPFLAGS) -c $< -o $@

entropy_bridge.o: entropy_bridge.c
	$(CC) $(CFLAGS) -c $< -o $@

asm_sum.o: asm_sum.s
	$(CC) $(CFLAGS) -c $< -o $@

vob_manifest.o: vob_manifest.cpp
	$(CXX) $(CPPFLAGS) -c $< -o $@

dvd_reader_dump.o: dvd_reader_dump.cpp
	$(CXX) $(CPPFLAGS) $(PKG_CFLAGS) -c $< -o $@

clean:
	rm -f analyze_entropy.o entropy_bridge.o asm_sum.o vob_manifest.o dvd_reader_dump.o $(TARGETS)
	rm -rf $(BUILD_DIR)
