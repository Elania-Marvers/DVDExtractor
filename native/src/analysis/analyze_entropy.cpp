#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <string>

#include <fcntl.h>
#include <sys/types.h>
#include <unistd.h>

#include "analysis/entropy.h"
#include "common/data_pool.h"
#include "common/asm_fmt.h"
#include "common/arena.h"
#include "common/json_escape.h"
#include "common/native_error.h"
#include "common/perf.h"
#include "common/mem_ops.h"

namespace {

constexpr std::size_t kDefaultSampleBytes = dvdextractor::common::kDefaultProbeBytes;

[[noreturn]] inline void fail(
    dvdextractor::common::NativeErrorCode code,
    const char* stage,
    const char* component,
    const char* message,
    std::string detail = {}) {
    dvdextractor::common::raise_native_error(code, stage, component, message, detail);
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc < 2) {
            fail(
                dvdextractor::common::NativeErrorCode::kUsage,
                "startup",
                "dvd_entropy",
                "usage: dvd_entropy <device_or_file> [sample_bytes]");
        }

        const std::string path = argv[1];
        DVD_NATIVE_ASSERT(
            !path.empty(),
            dvdextractor::common::NativeErrorCode::kUsage,
            "startup",
            "dvd_entropy",
            "empty path argument");

        std::size_t limit = kDefaultSampleBytes;
        if (argc >= 3) {
            const auto parsed = std::strtoull(argv[2], nullptr, 10);
            if (parsed > 0) {
                limit = static_cast<std::size_t>(parsed);
            }
        }

        const int fd = ::open(path.c_str(), O_RDONLY);
        if (fd < 0) {
            fail(
                dvdextractor::common::NativeErrorCode::kIo,
                "open",
                "dvd_entropy",
                "Cannot open input",
                path);
        }

        dvdextractor::common::ByteArena arena(limit + dvdextractor::common::kDefaultChunkBytes);
        auto* buffer = arena.allocate<std::uint8_t>(limit);
        if (buffer == nullptr) {
            fail(
                dvdextractor::common::NativeErrorCode::kRuntime,
                "alloc",
                "dvd_entropy",
                "arena allocation failed");
        }

        dvdextractor::common::ByteChunkPool chunk_pool(
            dvdextractor::common::kDefaultChunkBytes,
            2u);

        auto* chunk = chunk_pool.acquire();
        if (chunk == nullptr) {
            fail(
                dvdextractor::common::NativeErrorCode::kRuntime,
                "alloc",
                "dvd_entropy",
                "No chunk available from pool");
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

        std::size_t bytes_read = 0u;
        while (bytes_read < limit) {
            const auto to_read = std::min(chunk_pool.block_size(), limit - bytes_read);
            const auto n = ::read(fd, chunk, static_cast<ssize_t>(to_read));
            if (n <= 0) {
                break;
            }

            const auto bytes_read_batch = static_cast<std::size_t>(n);
            dvdextractor::common::memcpy_fast(buffer + bytes_read, chunk, bytes_read_batch);
            bytes_read += bytes_read_batch;
        }
        ::close(fd);

        if (bytes_read == 0u) {
            fail(
                dvdextractor::common::NativeErrorCode::kNoData,
                "read",
                "dvd_entropy",
                "No data");
        }

        const dvdextractor::analysis::ByteStatistics<std::vector<std::uint8_t>> stats;
        const double ent = stats.entropy(buffer, bytes_read);
        const std::size_t byte_sum = static_cast<std::size_t>(stats.sum(buffer, bytes_read));
        std::cout << '{';
        std::cout << "\"ok\":true,";
        std::cout << "\"path\":" << '"' << dvdextractor::common::json_escape(path) << "\",";
        std::cout << "\"bytes\":" << dvdextractor::common::u64_to_decimal(bytes_read) << ',';
        std::cout << "\"entropy\":" << std::fixed << std::setprecision(6) << ent << ',';
        std::cout << "\"byte_sum\":" << dvdextractor::common::u64_to_decimal(byte_sum) << ',';
        std::cout << "\"version\":\"asm+arena+pool\"";
        std::cout << "}\n";
        return 0;
    } catch (const dvdextractor::common::NativeExecError& err) {
        dvdextractor::common::write_error_json(std::cerr, err, "dvd_entropy", static_cast<int>(err.code()));
        std::cerr << '\n';
        return static_cast<int>(err.code());
    } catch (const std::exception& exc) {
        dvdextractor::common::write_error_json(
            std::cerr,
            dvdextractor::common::NativeExecError(
                dvdextractor::common::NativeErrorCode::kRuntime,
                "runtime",
                "dvd_entropy",
                exc.what()),
            "dvd_entropy",
            static_cast<int>(dvdextractor::common::NativeErrorCode::kRuntime));
        std::cerr << '\n';
        return static_cast<int>(dvdextractor::common::NativeErrorCode::kRuntime);
    }

    return 1;
}
