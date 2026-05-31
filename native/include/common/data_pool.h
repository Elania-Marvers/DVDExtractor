#ifndef DVDEXTRACTOR_COMMON_DATA_POOL_H_
#define DVDEXTRACTOR_COMMON_DATA_POOL_H_

#include <cstddef>
#include <cstdint>
#include <vector>

#include "common/native_error.h"
#include "common/perf.h"

namespace dvdextractor::common {

// Pool LIFO réutilisable (pile des blocs libres + zone contiguë).
// Objectif: éviter l'usage de malloc/free sur les chemins critiques.
template <typename T>
class StackPoolBase {
public:
    explicit StackPoolBase(std::size_t block_size, std::size_t block_count, std::size_t align = alignof(std::max_align_t))
        : block_size_(align_up(block_size, align))
        , block_count_(block_count)
        , storage_(block_size_ * block_count_)
        , free_stack_(block_count_) {
        DVD_NATIVE_ASSERT(block_size_ != 0u && block_count_ != 0u, NativeErrorCode::kRuntime, "stack_pool", "StackPoolBase", "invalid pool shape");
        DVD_NATIVE_ASSERT(
            storage_.size() / block_size_ >= block_count_,
            NativeErrorCode::kRuntime,
            "stack_pool",
            "StackPoolBase",
            "pool capacity overflow");
        for (std::size_t i = 0; i < block_count_; ++i) {
            free_stack_[i] = (block_count_ - 1u) - i;
        }
    }

    StackPoolBase(const StackPoolBase&) = delete;
    StackPoolBase& operator=(const StackPoolBase&) = delete;
    StackPoolBase(StackPoolBase&&) = delete;
    StackPoolBase& operator=(StackPoolBase&&) = delete;

    [[nodiscard]] std::size_t block_size() const noexcept {
        return block_size_;
    }

    [[nodiscard]] std::size_t block_count() const noexcept {
        return block_count_;
    }

    [[nodiscard]] bool has_free() const noexcept {
        return !free_stack_.empty();
    }

    [[nodiscard]] std::size_t capacity() const noexcept {
        return storage_.size();
    }

    [[nodiscard]] T* acquire() noexcept {
        if (free_stack_.empty()) {
            return nullptr;
        }

        const auto index = free_stack_.back();
        free_stack_.pop_back();
        return storage_.data() + index * block_size_;
    }

    void release(T* block) noexcept {
        if (block == nullptr) {
            return;
        }
        const auto base = storage_.data();
        const auto limit = base + storage_.size();
        if (block < base || block >= limit) {
            return;
        }

        const auto offset = static_cast<std::size_t>(block - base);
        if (offset % block_size_ != 0) {
            return;
        }

        const auto index = offset / block_size_;
        if (index >= block_count_) {
            return;
        }

        // "Push" sur la pile des blocs libres.
        free_stack_.push_back(index);
    }

    void reset() noexcept {
        free_stack_.clear();
        for (std::size_t i = 0; i < block_count_; ++i) {
            free_stack_.push_back((block_count_ - 1u) - i);
        }
    }

protected:
    std::size_t block_size_;
    std::size_t block_count_;
    std::vector<T> storage_;
    std::vector<std::size_t> free_stack_;
};

// Pool de blocs d'octets, spécialisation explicite pour éviter des casts partout.
class ByteChunkPool final : public StackPoolBase<std::uint8_t> {
public:
    ByteChunkPool(std::size_t block_size, std::size_t block_count)
        : StackPoolBase<std::uint8_t>(
            block_size,
            block_count,
            alignof(std::max_align_t)) {}
};

}  // namespace dvdextractor::common

#endif  // DVDEXTRACTOR_COMMON_DATA_POOL_H_
