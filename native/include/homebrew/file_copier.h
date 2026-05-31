#ifndef DVDEXTRACTOR_HOMEBREW_FILE_COPIER_H_
#define DVDEXTRACTOR_HOMEBREW_FILE_COPIER_H_

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <system_error>

#include "common/perf.h"

namespace fs = std::filesystem;

namespace dvdextractor::homebrew {

// Copieur bas niveau: utilisé par les moteurs copy/concat.
// La classe reste simple pour permettre un swap futur vers sendfile.
class FileCopier {
public:
    explicit FileCopier(std::size_t block_bytes = common::kTransferChunkBytes);

    [[nodiscard]] std::uint64_t copy_file(const fs::path& source, const fs::path& destination);
    void reset_counter();
    [[nodiscard]] std::uint64_t copied_bytes() const;

private:
    std::size_t block_bytes_{common::kTransferChunkBytes};
    mutable std::atomic<std::uint64_t> bytes_written_{0};
    std::error_code ec_;
};

}  // namespace dvdextractor::homebrew

#endif  // DVDEXTRACTOR_HOMEBREW_FILE_COPIER_H_
