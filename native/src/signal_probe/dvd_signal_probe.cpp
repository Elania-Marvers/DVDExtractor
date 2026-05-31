// dvd_signal_probe.cpp
//
// Outil C++ "pre-flight" pour classifier rapidement une source média.
//
// Objectifs:
// - lire une fenêtre fixe de données en entrée
// - calculer des heuristiques de sécurité (entropie, somme, signatures MPEG)
// - retourner un JSON minimal pour l'orchestrateur Python
//
// Remarque perf:
// la somme des octets est externalisée via la stratégie AsmByteSum, le reste
// est optimisé pour être pipeline-friendly (itérations monotones + pointeurs).

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <cmath>
#include <iomanip>
#include <ios>
#include <iostream>
#include <string>

#include "analysis/entropy.h"
#include "common/asm_fmt.h"
#include "common/json_escape.h"
#include "common/native_error.h"
#include "common/data_pool.h"
#include "common/arena.h"
#include "common/perf.h"
#include "common/mem_ops.h"

namespace {

constexpr std::size_t kDefaultProbeBytes = dvdextractor::common::kDefaultProbeBytes;
constexpr std::array<std::uint8_t, 3> kPackSyncPrefix = {0x00, 0x00, 0x01};
constexpr std::size_t kReadChunkPoolCount = 2u;

}  // namespace

class SignalProbe {
public:
    explicit SignalProbe(std::string input_path, std::size_t sample_bytes)
        : input_path_(std::move(input_path)),
          sample_limit_(sample_bytes),
          sample_arena_(std::max(sample_bytes, dvdextractor::common::kDefaultProbeBytes)
                              + dvdextractor::common::kDefaultChunkBytes),
          sample_(nullptr),
          sample_size_(0u),
          bytes_read_(0),
          pack_sync_count_(0),
          ts_sync_count_(0),
          max_zero_run_(0),
          statistics_() {}

    void scan() {
        if (sample_limit_ == 0) {
            sample_limit_ = kDefaultProbeBytes;
        }
        DVD_NATIVE_ASSERT(
            !input_path_.empty(),
            dvdextractor::common::NativeErrorCode::kUsage,
            "scan",
            "dvd_signal_probe",
            "empty input path");

        std::ifstream input(input_path_, std::ios::binary);
        if (!input) {
            dvdextractor::common::raise_native_error(
                dvdextractor::common::NativeErrorCode::kIo,
                "scan",
                "dvd_signal_probe",
                "cannot open input",
                input_path_);
        }

        sample_ = sample_arena_.allocate<std::uint8_t>(sample_limit_);
        if (sample_ == nullptr) {
            dvdextractor::common::raise_native_error(
                dvdextractor::common::NativeErrorCode::kRuntime,
                "scan",
                "dvd_signal_probe",
                "sample arena allocation failed");
        }
        sample_size_ = 0u;
        dvdextractor::common::ByteChunkPool chunk_pool(
            dvdextractor::common::kDefaultChunkBytes,
            kReadChunkPoolCount);
        auto* chunk = chunk_pool.acquire();
        if (chunk == nullptr) {
            dvdextractor::common::raise_native_error(
                dvdextractor::common::NativeErrorCode::kRuntime,
                "scan",
                "dvd_signal_probe",
                "chunk pool depleted unexpectedly");
        }

        struct ChunkLease {
            dvdextractor::common::ByteChunkPool* pool;
            std::uint8_t* block;
            ~ChunkLease() {
                if (pool != nullptr && block != nullptr) {
                    pool->release(block);
                }
            }
        } lease{&chunk_pool, chunk};

        while (bytes_read_ < sample_limit_ && input.good()) {
            const auto remain = sample_limit_ - bytes_read_;
            const auto batch = std::min(chunk_pool.block_size(), remain);
            input.read(reinterpret_cast<char*>(chunk), static_cast<std::streamsize>(batch));
            const auto n = static_cast<std::size_t>(input.gcount());
            if (DVD_UNLIKELY(n == 0)) {
                break;
            }

            dvdextractor::common::memcpy_fast(sample_ + bytes_read_, chunk, n);
            sample_size_ += n;
            bytes_read_ += n;
        }

        if (sample_size_ == 0u) {
            dvdextractor::common::raise_native_error(
                dvdextractor::common::NativeErrorCode::kNoData,
                "scan",
                "dvd_signal_probe",
                "no readable data",
                input_path_);
        }

        pack_sync_count_ = count_pack_sync(sample_, sample_size_);
        ts_sync_count_ = count_ts_sync(sample_, sample_size_);
        max_zero_run_ = count_max_zero_run(sample_, sample_size_);
    }

    [[nodiscard]] std::size_t bytes_read() const {
        return sample_size_;
    }

    [[nodiscard]] std::size_t pack_sync_count() const {
        return pack_sync_count_;
    }

    [[nodiscard]] std::size_t ts_sync_count() const {
        return ts_sync_count_;
    }

    [[nodiscard]] std::size_t max_zero_run() const {
        return max_zero_run_;
    }

    [[nodiscard]] double entropy() const {
        return statistics_.entropy(sample_, sample_size_);
    }

    [[nodiscard]] std::size_t byte_sum() const {
        return static_cast<std::size_t>(statistics_.sum(sample_, sample_size_));
    }

    void dump_json() const {
        const double ent = entropy();
        std::cout << '{';
        std::cout << "\"ok\":true,";
        std::cout << "\"path\":" << '"' << dvdextractor::common::json_escape(input_path_) << "\",";
        std::cout << "\"bytes\":" << dvdextractor::common::u64_to_decimal(bytes_read_) << ',';
        std::cout << "\"entropy\":" << std::fixed << std::setprecision(6) << ent << ',';
        std::cout << "\"byte_sum\":" << dvdextractor::common::u64_to_decimal(byte_sum()) << ',';
        std::cout << "\"pack_sync_count\":" << dvdextractor::common::u64_to_decimal(pack_sync_count_) << ',';
        std::cout << "\"ts_sync_count\":" << dvdextractor::common::u64_to_decimal(ts_sync_count_) << ',';
        std::cout << "\"max_zero_run\":" << dvdextractor::common::u64_to_decimal(max_zero_run_);
        std::cout << "}\n";
    }

private:
    static std::size_t count_pack_sync(const std::uint8_t* data, std::size_t size) {
        if (size < 4) {
            return 0;
        }

        std::size_t count = 0;
        const std::uint8_t* ptr = data;
        const std::uint8_t* end = data + size - 3;

        while (ptr < end) {
            if (dvdextractor::common::memcmp_fast(ptr, kPackSyncPrefix.data(), kPackSyncPrefix.size()) != 0) {
                ++ptr;
                continue;
            }

            const auto code = ptr[3];
            if (code == 0xBA || code == 0xBB || code == 0xB9 || code == 0xB3) {
                ++count;
            }

            ++ptr;
        }

        return count;
    }

    static std::size_t count_ts_sync(const std::uint8_t* data, std::size_t size) {
        if (size < 188) {
            return 0;
        }

        std::size_t count = 0;
        const std::uint8_t* ptr = data;
        const std::uint8_t* end = data + size - 188;

        while (ptr < end) {
            if (ptr[0] == 0x47 && ptr[188] == 0x47) {
                ++count;
            }
            ++ptr;
        }

        return count;
    }

    static std::size_t count_max_zero_run(const std::uint8_t* data, std::size_t size) {
        return dvdextractor::common::max_zero_run_fast(data, size);
    }

    std::string input_path_;
    std::size_t sample_limit_;
    std::uint8_t* sample_;
    std::size_t sample_size_;
    std::size_t bytes_read_;
    dvdextractor::common::ByteArena sample_arena_;
    std::size_t pack_sync_count_;
    std::size_t ts_sync_count_;
    std::size_t max_zero_run_;
    dvdextractor::analysis::ByteStatistics<std::vector<std::uint8_t>> statistics_;
};

int main(int argc, char** argv) {
    if (argc < 2) {
        dvdextractor::common::write_error_json(
            std::cerr,
            dvdextractor::common::NativeExecError(
                dvdextractor::common::NativeErrorCode::kUsage,
                "startup",
                "dvd_signal_probe",
                "usage: dvd_signal_probe <path> [sample_bytes]"),
            "dvd_signal_probe",
            2);
        return 2;
    }

    try {
        std::size_t limit = kDefaultProbeBytes;
        if (argc >= 3) {
            const auto parsed = std::strtoull(argv[2], nullptr, 10);
            if (parsed > 0) {
                limit = static_cast<std::size_t>(parsed);
            }
        }

        SignalProbe probe(argv[1], limit);
        probe.scan();
        probe.dump_json();
        return probe.bytes_read() == 0 ? 4 : 0;
    } catch (const dvdextractor::common::NativeExecError& err) {
        dvdextractor::common::write_error_json(std::cerr, err, "dvd_signal_probe", static_cast<int>(err.code()));
        std::cerr << '\n';
        return static_cast<int>(err.code());
    } catch (const std::exception& exc) {
        std::cerr << "{";
        std::cerr << "\"ok\":false,";
        std::cerr << "\"analyzer\":\"dvd_signal_probe\",";
        std::cerr << "\"message\":\"";
        std::cerr << dvdextractor::common::json_escape(exc.what());
        std::cerr << "\"}\n";
        return 1;
    } catch (...) {
        std::cerr << "{";
        std::cerr << "\"ok\":false,";
        std::cerr << "\"analyzer\":\"dvd_signal_probe\",";
        std::cerr << "\"return_code\":6,";
        std::cerr << "\"message\":\"unexpected error\"}\n";
        return 1;
    }
}
