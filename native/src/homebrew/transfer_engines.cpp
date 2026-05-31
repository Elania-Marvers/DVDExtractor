#include "homebrew/transfer_engines.h"

#include <fstream>
#include <vector>

#include "homebrew/errors.h"
#include "homebrew/progress_ticker.h"
#include "common/perf.h"

namespace dvdextractor::homebrew {

ConcatEngine::ConcatEngine(std::size_t block_bytes)
    : copier_(block_bytes) {}

std::uint64_t ConcatEngine::concat(const fs::path& output, const std::vector<fs::path>& inputs) {
    if (inputs.empty()) {
        throw HomebrewError("no source files provided");
    }

    if (!output.parent_path().empty()) {
        fs::create_directories(output.parent_path());
    }

    std::ofstream out(output, std::ios::binary | std::ios::trunc);
    if (!out) {
        throw HomebrewError("cannot create destination: " + output.string());
    }

    copier_.reset_counter();
    bytes_.store(0, std::memory_order_relaxed);
    ProgressTicker ticker(&bytes_, "concat", output);

    std::uint64_t total = 0;
    for (const auto& input : inputs) {
        if (DVD_UNLIKELY(!fs::exists(input) || !fs::is_regular_file(input))) {
            throw HomebrewError("invalid source: " + input.string());
        }

        std::ifstream in(input, std::ios::binary);
        if (!in) {
            throw HomebrewError("cannot open source: " + input.string());
        }

        std::vector<char> buffer(dvdextractor::common::kTransferChunkBytes);
        while (in.good()) {
            in.read(buffer.data(), static_cast<std::streamsize>(buffer.size()));
            const auto n = static_cast<std::size_t>(in.gcount());
            if (n == 0) {
                break;
            }

            out.write(buffer.data(), static_cast<std::streamsize>(n));
            if (!out) {
                throw HomebrewError("write failure on destination: " + output.string());
            }

            bytes_.fetch_add(static_cast<std::uint64_t>(n), std::memory_order_relaxed);
            total += static_cast<std::uint64_t>(n);
        }
    }

    out.flush();
    return total;
}

std::uint64_t ConcatEngine::run(const fs::path& output, const std::vector<fs::path>& inputs) {
    return concat(output, inputs);
}

std::uint64_t ConcatEngine::bytes_written() const {
    return bytes_.load(std::memory_order_relaxed);
}

CopyEngine::CopyEngine(std::size_t block_bytes)
    : copier_(block_bytes) {}

std::uint64_t CopyEngine::copy(const fs::path& output, const fs::path& source) {
    if (!output.parent_path().empty()) {
        fs::create_directories(output.parent_path());
    }
    ProgressTicker ticker(&bytes_, "copy", output);

    copier_.reset_counter();
    bytes_.store(0, std::memory_order_relaxed);

    const std::uint64_t written = copier_.copy_file(source, output);
    bytes_.store(written, std::memory_order_relaxed);

    return written;
}

std::uint64_t CopyEngine::run(const fs::path& output, const std::vector<fs::path>& inputs) {
    if (inputs.empty()) {
        throw HomebrewError("copy command expects exactly one source");
    }
    if (inputs.size() > 1) {
        throw HomebrewError("copy command expects exactly one source");
    }
    return copy(output, inputs.front());
}

std::uint64_t CopyEngine::bytes_written() const {
    return bytes_.load(std::memory_order_relaxed);
}

}  // namespace dvdextractor::homebrew
