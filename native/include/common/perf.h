#ifndef DVDEXTRACTOR_COMMON_PERF_H_
#define DVDEXTRACTOR_COMMON_PERF_H_

#include <cstddef>
#include <cstdint>

namespace dvdextractor::common {

#if defined(__GNUC__) || defined(__clang__)
# define DVD_ALWAYS_INLINE __attribute__((always_inline)) inline
# define DVD_HOT __attribute__((hot))
# define DVD_PURE __attribute__((pure))
# define DVD_LIKELY(expr) __builtin_expect(!!(expr), 1)
# define DVD_UNLIKELY(expr) __builtin_expect(!!(expr), 0)
# define DVD_IFUNC_ATTR __attribute__((target("default")))
#else
# define DVD_ALWAYS_INLINE inline
# define DVD_HOT
# define DVD_PURE
# define DVD_UNLIKELY(expr) (expr)
# define DVD_LIKELY(expr) (expr)
# define DVD_IFUNC_ATTR
#endif

// ASM disponible uniquement pour compilation x86_64 GCC/Clang et tant que la désactivation
// explicite n'est pas demandée.
#if defined(__x86_64__) && (defined(__GNUC__) || defined(__clang__)) && !defined(DVD_DISABLE_ASM_SUM)
# define DVD_HAS_ASM 1
#else
# define DVD_HAS_ASM 0
#endif

// Tailles mémoire exprimées avec des shifts (anti-litteral flottant / stable compilateur).
constexpr std::size_t kKiB = 1u << 10;
constexpr std::size_t kMiB = 1u << 20;
constexpr std::size_t kDefaultProbeBytes = 4u * kMiB;
constexpr std::size_t kDefaultChunkBytes = 64u * 1024u;
constexpr std::size_t kTransferChunkBytes = 1u * kMiB;

// Alignement utile pour réduire les défauts de cache-line sur parcours séquentiels.
constexpr std::size_t kTransferAlignBytes = 1u << 12;

template <typename T>
constexpr T align_down(T value, std::size_t align) {
    if (DVD_UNLIKELY(align == 0)) {
        return value;
    }
    return value & ~(static_cast<T>(align) - 1);
}

template <typename T>
constexpr T align_up(T value, std::size_t align) {
    if (DVD_UNLIKELY(align == 0)) {
        return value;
    }
    return (value + (static_cast<T>(align) - 1)) & ~(static_cast<T>(align) - 1);
}

}  // namespace dvdextractor::common

#endif  // DVDEXTRACTOR_COMMON_PERF_H_
