#include "analysis/entropy.h"

#include <cstddef>
#include <cstdint>
#include <cstring>

#include "common/mem_ops.h"

namespace {
extern "C" std::size_t fast_byte_sum(const std::uint8_t* data, std::size_t len);
}

namespace dvdextractor::analysis {

std::uint64_t AsmByteSum::compute(const std::uint8_t* data, std::size_t len) const {
    return static_cast<std::uint64_t>(fast_byte_sum(data, len));
}

std::uint64_t FallbackByteSum::compute(const std::uint8_t* data, std::size_t len) const {
    std::uint64_t total = 0;
    std::size_t i = 0;

    // Traitement du bloc rapide en 8 octets avec des shifts pour réduire les
    // itérations et limiter les dépendances du pipeline.
    for (; i + 8 <= len; i += 8) {
        std::uint64_t chunk = 0;
        dvdextractor::common::memcpy_fast(&chunk, data + i, sizeof(chunk));

        total += (chunk & 0xFFULL);
        total += ((chunk >> 8u) & 0xFFULL);
        total += ((chunk >> 16u) & 0xFFULL);
        total += ((chunk >> 24u) & 0xFFULL);
        total += ((chunk >> 32u) & 0xFFULL);
        total += ((chunk >> 40u) & 0xFFULL);
        total += ((chunk >> 48u) & 0xFFULL);
        total += ((chunk >> 56u) & 0xFFULL);
    }

    // Queue de reste (tail) : 0..7 octets.
    for (; i < len; ++i) {
        total += static_cast<std::uint64_t>(data[i]);
    }

    return total;
}

}  // namespace dvdextractor::analysis
