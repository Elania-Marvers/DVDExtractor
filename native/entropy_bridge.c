#include <stddef.h>
#include <stdint.h>

extern size_t asm_byte_sum(const uint8_t* data, size_t len);

size_t fast_byte_sum(const uint8_t* data, size_t len) {
    return asm_byte_sum(data, len);
}
