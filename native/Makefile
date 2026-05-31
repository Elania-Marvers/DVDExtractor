.PHONY: all clean

CC = clang
CXX = clang++
CFLAGS = -O3 -fPIC
CPPFLAGS = -O3 -std=c++17
AR = ar
BUILD_DIR = build
TARGET = $(BUILD_DIR)/dvd_entropy

all: $(TARGET)

$(TARGET): analyze_entropy.o entropy_bridge.o asm_sum.o
	mkdir -p $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) -o $(TARGET) $^

analyze_entropy.o: analyze_entropy.cpp
	$(CXX) $(CPPFLAGS) -c $< -o $@

entropy_bridge.o: entropy_bridge.c
	$(CC) $(CFLAGS) -c $< -o $@

asm_sum.o: asm_sum.s
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f analyze_entropy.o entropy_bridge.o asm_sum.o $(TARGET)
	rm -rf $(BUILD_DIR)
