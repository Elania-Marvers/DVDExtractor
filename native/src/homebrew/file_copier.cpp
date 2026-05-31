#include "homebrew/file_copier.h"
#include "homebrew/errors.h"

#include "common/perf.h"

#include <fstream>
#include <vector>

namespace dvdextractor::homebrew {

FileCopier::FileCopier(std::size_t block_bytes)
    : block_bytes_(block_bytes) {
    block_bytes_ = dvdextractor::common::align_up(block_bytes_, dvdextractor::common::kTransferAlignBytes);
    if (block_bytes_ == 0) {
        block_bytes_ = dvdextractor::common::kTransferChunkBytes;
    }
}

std::uint64_t FileCopier::copy_file(const fs::path& source, const fs::path& destination) {
    std::ifstream in(source, std::ios::binary);
    if (!in) {
        throw HomebrewError("cannot open source: " + source.string());
    }

    if (!destination.parent_path().empty()) {
        fs::create_directories(destination.parent_path(), ec_);
    }

    std::ofstream out(destination, std::ios::binary | std::ios::trunc);
    if (!out) {
        throw HomebrewError("cannot open destination: " + destination.string());
    }

    std::vector<char> buffer(block_bytes_);
    std::uint64_t total = 0;
    auto* data = buffer.data();

    while (true) {
        in.read(buffer.data(), static_cast<std::streamsize>(buffer.size()));
        const auto readed = in.gcount();
        if (readed < 0) {
            throw HomebrewError("read failure on source: " + source.string());
        }
        if (readed == 0) {
            break;
        }

        out.write(data, readed);
        if (!out) {
            throw HomebrewError("write failure on destination: " + destination.string());
        }

        total += static_cast<std::uint64_t>(readed);
        bytes_written_.fetch_add(static_cast<std::uint64_t>(readed), std::memory_order_relaxed);
    }

    out.flush();
    return total;
}

void FileCopier::reset_counter() {
    bytes_written_.store(0, std::memory_order_relaxed);
}

std::uint64_t FileCopier::copied_bytes() const {
    return bytes_written_.load(std::memory_order_relaxed);
}

}  // namespace dvdextractor::homebrew
