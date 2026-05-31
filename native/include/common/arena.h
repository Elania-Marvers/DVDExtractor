#ifndef DVDEXTRACTOR_COMMON_ARENA_H_
#define DVDEXTRACTOR_COMMON_ARENA_H_

#include <cstddef>
#include <cstdint>
#include <vector>

#include "common/perf.h"

namespace dvdextractor::common {

class IByteArena {
public:
    virtual ~IByteArena() = default;

    [[nodiscard]] virtual std::size_t used() const noexcept = 0;
    virtual void reset() noexcept = 0;
    virtual bool rewind(std::size_t checkpoint) noexcept = 0;
};

// Arena monotone (type "stack allocator").
// But:
// - allocation O(1) (bump pointer),
// - rewind/rollback O(1),
// - zéro malloc/free dynamique sur les chemins critiques.
class ByteArena final : public IByteArena {
public:
    class Checkpoint {
    public:
        Checkpoint(ByteArena& arena, std::size_t cursor) noexcept
            : arena_(arena)
            , cursor_(cursor) {}

        Checkpoint(const Checkpoint&) = delete;
        Checkpoint& operator=(const Checkpoint&) = delete;

        ~Checkpoint() {
            arena_.rewind(cursor_);
        }

    private:
        ByteArena& arena_;
        std::size_t cursor_;
    };

    ByteArena(std::size_t total_bytes, std::size_t align = alignof(std::max_align_t))
        : storage_(total_bytes + align), align_(align), head_(0u) {
        if (align_ == 0u) {
            align_ = 1u;
        }
    }

    ByteArena(const ByteArena&) = delete;
    ByteArena& operator=(const ByteArena&) = delete;

    void reset() noexcept override {
        head_ = 0u;
    }

    [[nodiscard]] std::size_t used() const noexcept override {
        return head_;
    }

    [[nodiscard]] std::size_t capacity() const noexcept {
        return storage_.size();
    }

    [[nodiscard]] Checkpoint snapshot() {
        return Checkpoint(*this, head_);
    }

    bool rewind(std::size_t checkpoint) noexcept override {
        if (checkpoint <= head_) {
            head_ = checkpoint;
            return true;
        }
        return false;
    }

    [[nodiscard]] std::uint8_t* allocate(std::size_t bytes) {
        if (bytes == 0u) {
            return nullptr;
        }
        const auto aligned_head = align_up(head_, align_);
        if (aligned_head >= storage_.size() || bytes > storage_.size() - aligned_head) {
            return nullptr;
        }
        auto* out = storage_.data() + aligned_head;
        head_ = aligned_head + bytes;
        return out;
    }

    template <typename T>
    [[nodiscard]] T* allocate(std::size_t count) {
        if (count == 0u) {
            return nullptr;
        }
        const auto bytes = count * sizeof(T);
        return reinterpret_cast<T*>(allocate(bytes));
    }

    template <typename T>
    void rewind_to(std::size_t checkpoint) {
        (void)rewind(checkpoint);
    }

private:
    std::vector<std::uint8_t> storage_;
    std::size_t align_;
    std::size_t head_;
};

}  // namespace dvdextractor::common

#endif  // DVDEXTRACTOR_COMMON_ARENA_H_
