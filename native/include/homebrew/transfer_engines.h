#ifndef DVDEXTRACTOR_HOMEBREW_TRANSFER_ENGINES_H_
#define DVDEXTRACTOR_HOMEBREW_TRANSFER_ENGINES_H_

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <vector>

#include "common/perf.h"
#include "homebrew/file_copier.h"

namespace fs = std::filesystem;

namespace dvdextractor::homebrew {

// Interface unique pour les moteurs de transfert (copy / concat).
class TransferEngine {
public:
    virtual ~TransferEngine() = default;
    [[nodiscard]] virtual std::uint64_t run(const fs::path& output, const std::vector<fs::path>& inputs) = 0;
    [[nodiscard]] virtual std::uint64_t bytes_written() const = 0;
};

// Concatène des segments VOB en un flux contigu.
class ConcatEngine final : public TransferEngine {
public:
    explicit ConcatEngine(std::size_t block_bytes = common::kTransferChunkBytes);

    [[nodiscard]] std::uint64_t run(const fs::path& output, const std::vector<fs::path>& inputs) override;
    [[nodiscard]] std::uint64_t concat(const fs::path& output, const std::vector<fs::path>& inputs);
    [[nodiscard]] std::uint64_t bytes_written() const override;

private:
    FileCopier copier_;
    mutable std::atomic<std::uint64_t> bytes_{0};
};

// Copie 1:1 d'une source vers une destination.
class CopyEngine final : public TransferEngine {
public:
    explicit CopyEngine(std::size_t block_bytes = common::kTransferChunkBytes);

    [[nodiscard]] std::uint64_t run(const fs::path& output, const std::vector<fs::path>& inputs) override;
    [[nodiscard]] std::uint64_t copy(const fs::path& output, const fs::path& source);
    [[nodiscard]] std::uint64_t bytes_written() const override;

private:
    FileCopier copier_;
    mutable std::atomic<std::uint64_t> bytes_{0};
};

}  // namespace dvdextractor::homebrew

#endif  // DVDEXTRACTOR_HOMEBREW_TRANSFER_ENGINES_H_
