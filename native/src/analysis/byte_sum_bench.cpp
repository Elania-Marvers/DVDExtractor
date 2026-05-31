// byte_sum_bench.cpp
//
// Micro-benchmark ciblé:
// - AsmByteSum (asm) vs FallbackByteSum (byte-wise)
// - fallback 16-bit via bit-shifts
// - fallback 32-bit via bit-shifts
//
// Usage:
//   byte_sum_bench --size 64 --iter 128 --verify

#include "analysis/entropy.h"

#include <algorithm>
#include <charconv>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <limits>
#include <random>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

#include "common/mem_ops.h"
#include "common/perf.h"

struct BenchConfig {
    std::size_t sample_bytes = 4u * 1024u * 1024u;
    int iterations = 128;
    bool verify = false;
};

struct BenchResult {
    std::uint64_t checksum = 0;
    long long elapsed_ns = 0;
};

BenchConfig parse_args(int argc, char** argv) {
    BenchConfig cfg;
    for (int i = 1; i < argc; ++i) {
        const std::string_view arg = argv[i];
        if (arg == "--size" && i + 1 < argc) {
            const char* text = argv[++i];
            int parsed = 0;
            const auto [_, ec] = std::from_chars(text, text + std::char_traits<char>::length(text), parsed);
            if (ec == std::errc{} && parsed > 0) {
                cfg.sample_bytes = static_cast<std::size_t>(parsed) * (1024u * 1024u);
            }
            continue;
        }
        if (arg == "--iter" && i + 1 < argc) {
            const char* text = argv[++i];
            int parsed = 0;
            const auto [_, ec] = std::from_chars(text, text + std::char_traits<char>::length(text), parsed);
            if (ec == std::errc{} && parsed > 0) {
                cfg.iterations = parsed;
            }
            continue;
        }
        if (arg == "--verify") {
            cfg.verify = true;
        }
    }

    return cfg;
}

template <typename Strategy>
BenchResult run_bench(const Strategy& strategy, const std::vector<std::uint8_t>& data, int iterations) {
    std::uint64_t acc = 0;
    const auto start = std::chrono::high_resolution_clock::now();

    for (int i = 0; i < iterations; ++i) {
        acc += strategy.compute(data.data(), data.size());
    }

    const auto end = std::chrono::high_resolution_clock::now();
    return {
        acc,
        std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count()
    };
}

struct Fallback16BitSum final {
    [[nodiscard]] std::uint64_t compute(const std::uint8_t* data, std::size_t len) const {
        std::uint64_t total = 0;
        std::size_t i = 0;
        const std::size_t main_len = len & ~std::size_t{1};
        for (; i < main_len; i += 2) {
            std::uint16_t packed = 0;
            dvdextractor::common::memcpy_fast(&packed, data + i, sizeof(packed));
            total += packed & 0xFFULL;
            total += (packed >> 8u) & 0xFFULL;
        }

        if (i < len) {
            total += static_cast<std::uint64_t>(data[i]);
        }

        return total;
    }
};

struct Fallback32BitSum final {
    [[nodiscard]] std::uint64_t compute(const std::uint8_t* data, std::size_t len) const {
        std::uint64_t total = 0;
        std::size_t i = 0;
        const std::size_t main_len = len & ~std::size_t{3};
        for (; i < main_len; i += 4) {
            std::uint32_t packed = 0;
            dvdextractor::common::memcpy_fast(&packed, data + i, sizeof(packed));
            total += packed & 0xFFULL;
            total += (packed >> 8u) & 0xFFULL;
            total += (packed >> 16u) & 0xFFULL;
            total += (packed >> 24u) & 0xFFULL;
        }

        for (; i < len; ++i) {
            total += static_cast<std::uint64_t>(data[i]);
        }
        return total;
    }
};

double ns_per_byte(const BenchResult& result, std::size_t size_bytes, int iterations) {
    const double total_bytes = static_cast<double>(size_bytes) * static_cast<double>(iterations);
    if (result.elapsed_ns <= 0 || total_bytes <= 0.0) {
        return 0.0;
    }
    return static_cast<double>(result.elapsed_ns) / total_bytes;
}

double gb_per_s(const BenchResult& result, std::size_t size_bytes, int iterations) {
    const double total_bytes = static_cast<double>(size_bytes) * static_cast<double>(iterations);
    if (result.elapsed_ns <= 0) {
        return 0.0;
    }
    return total_bytes / (static_cast<double>(result.elapsed_ns) / 1e9) / (1024.0 * 1024.0 * 1024.0);
}

int main(int argc, char** argv) {
    const BenchConfig cfg = parse_args(argc, argv);
    if (cfg.sample_bytes == 0 || cfg.iterations <= 0) {
        std::cerr << "usage: byte_sum_bench [--size MIB] [--iter N] [--verify]\n";
        return 2;
    }

    std::vector<std::uint8_t> buffer(cfg.sample_bytes);
    std::mt19937_64 rng(0xC0DECAFEULL);
    std::uniform_int_distribution<int> dist(0, 255);
    for (auto& byte : buffer) {
        byte = static_cast<std::uint8_t>(dist(rng));
    }

    const int iterations = std::max(1, cfg.iterations);

    const dvdextractor::analysis::AsmByteSum asm_strategy;
    const dvdextractor::analysis::FallbackByteSum fallback_scalar_strategy;
    const Fallback16BitSum fallback_16_strategy;
    const Fallback32BitSum fallback_32_strategy;

    const bool asm_enabled = (DVD_HAS_ASM != 0);
    const auto asm_result = asm_enabled ? run_bench(asm_strategy, buffer, iterations) : BenchResult{0, std::numeric_limits<long long>::max()};
    const auto fallback_scalar_result = run_bench(fallback_scalar_strategy, buffer, iterations);
    const auto fallback_16_result = run_bench(fallback_16_strategy, buffer, iterations);
    const auto fallback_32_result = run_bench(fallback_32_strategy, buffer, iterations);

    const bool checksums_match = fallback_scalar_result.checksum == fallback_16_result.checksum
                             && fallback_scalar_result.checksum == fallback_32_result.checksum
                             && (asm_enabled ? asm_result.checksum == fallback_scalar_result.checksum : true);
    const long long best_fallback_ns = std::min({
        fallback_scalar_result.elapsed_ns,
        fallback_16_result.elapsed_ns,
        fallback_32_result.elapsed_ns
    });

    const long long best_ns = asm_enabled ? std::min(best_fallback_ns, asm_result.elapsed_ns) : best_fallback_ns;
    const char* best_name = "fallback_scalar";
    if (asm_enabled && asm_result.elapsed_ns == best_ns) {
        best_name = "asm";
    } else if (fallback_16_result.elapsed_ns <= fallback_scalar_result.elapsed_ns && fallback_16_result.elapsed_ns <= fallback_32_result.elapsed_ns) {
        best_name = "fallback_16";
    } else if (fallback_32_result.elapsed_ns <= fallback_scalar_result.elapsed_ns && fallback_32_result.elapsed_ns <= fallback_16_result.elapsed_ns) {
        best_name = "fallback_32";
    }

    auto gain_pct = [](long long faster_ns, long long slower_ns) {
        if (slower_ns <= 0 || faster_ns <= 0) {
            return 0.0;
        }
        return (1.0 - static_cast<double>(faster_ns) / static_cast<double>(slower_ns)) * 100.0;
    };

    std::cout << std::fixed << std::setprecision(6) << '{';
    std::cout << "\"size_bytes\":" << cfg.sample_bytes << ',';
    std::cout << "\"iterations\":" << iterations << ',';
    std::cout << "\"verify\":" << (cfg.verify ? "true" : "false") << ',';

    if (asm_enabled) {
        std::cout << "\"asm\":{\"ns\":" << asm_result.elapsed_ns << ",\"ns_per_byte\":" << ns_per_byte(asm_result, cfg.sample_bytes, iterations)
                  << ",\"gbps\":" << gb_per_s(asm_result, cfg.sample_bytes, iterations)
                  << ",\"checksum\":" << asm_result.checksum << "},";
    } else {
        std::cout << "\"asm\":null,";
    }

    std::cout << "\"fallback_scalar\":{\"ns\":" << fallback_scalar_result.elapsed_ns << ",\"ns_per_byte\":" << ns_per_byte(fallback_scalar_result, cfg.sample_bytes, iterations)
              << ",\"gbps\":" << gb_per_s(fallback_scalar_result, cfg.sample_bytes, iterations)
              << ",\"checksum\":" << fallback_scalar_result.checksum << "},";
    std::cout << "\"fallback_16\":{\"ns\":" << fallback_16_result.elapsed_ns << ",\"ns_per_byte\":" << ns_per_byte(fallback_16_result, cfg.sample_bytes, iterations)
              << ",\"gbps\":" << gb_per_s(fallback_16_result, cfg.sample_bytes, iterations)
              << ",\"checksum\":" << fallback_16_result.checksum << "},";
    std::cout << "\"fallback_32\":{\"ns\":" << fallback_32_result.elapsed_ns << ",\"ns_per_byte\":" << ns_per_byte(fallback_32_result, cfg.sample_bytes, iterations)
              << ",\"gbps\":" << gb_per_s(fallback_32_result, cfg.sample_bytes, iterations)
              << ",\"checksum\":" << fallback_32_result.checksum << "},";

    std::cout << "\"best\":{\"name\":\"" << best_name << "\",\"ns\":" << best_ns << "},";
    std::cout << "\"checksum_match\":" << (checksums_match ? "true" : "false") << ',';

    std::cout << "\"speedup_pct\":{"
              << "\"asm_vs_fallback_scalar\":" << gain_pct(asm_enabled ? asm_result.elapsed_ns : best_fallback_ns, fallback_scalar_result.elapsed_ns) << ','
              << "\"asm_vs_fallback16\":" << gain_pct(asm_enabled ? asm_result.elapsed_ns : best_fallback_ns, fallback_16_result.elapsed_ns) << ','
              << "\"asm_vs_fallback32\":" << gain_pct(asm_enabled ? asm_result.elapsed_ns : best_fallback_ns, fallback_32_result.elapsed_ns) << ","
              << "\"best_fallback_vs_asm\":" << (asm_enabled ? gain_pct(best_fallback_ns, asm_result.elapsed_ns) : 0.0) << "}";

    std::cout << "}\n";

    if (cfg.verify && !checksums_match) {
        std::cerr << "checksum mismatch between strategies\n";
        return 1;
    }

    return 0;
}
