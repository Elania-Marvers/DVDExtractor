#ifndef DVDEXTRACTOR_COMMON_MEM_OPS_H_
#define DVDEXTRACTOR_COMMON_MEM_OPS_H_

#include <cstddef>
#include <cstdint>

#include "common/perf.h"

namespace dvdextractor::common {

constexpr std::size_t kMemcpyAvx2Threshold = 1u << 6;
constexpr std::size_t kMemsetAvx2Threshold = 1u << 8;

#if DVD_HAS_ASM && !defined(DVD_DISABLE_ASM_MEMOPS)
#if defined(__GNUC__) || defined(__clang__)
inline bool has_avx2_cpu() {
    static const bool kHasAvx2 = []() -> bool {
        __builtin_cpu_init();
        return __builtin_cpu_supports("avx2");
    }();
    return kHasAvx2;
}
#endif

extern "C" {
// Copie mémoire brute minimale en ASM.
// Convention SysV x86-64: dst=rdi, src=rsi, len=rdx, return=rax(dst).
void* dvd_memcpy(void* dst, const void* src, std::size_t len);
// Version AVX2 (si CPU supporte AVX2).
void* dvd_memcpy_avx2(void* dst, const void* src, std::size_t len);

// Copie une chaîne C (jusqu'au '\0', inclu).
// Convention SysV x86-64: dst=rdi, src=rsi, return=rax(dst).
char* dvd_strcpy(char* dst, const char* src);

// Limite la longueur à max_len si la chaîne n'est pas terminée.
// Convention SysV x86-64: text=rdi, max_len=rsi, return=rax.
std::size_t dvd_strnlen(const char* text, std::size_t max_len);

// Mesure de longueur C-string en ASM.
// Convention SysV x86-64: text=rdi, return=rax.
std::size_t dvd_strlen(const char* text);

// Compare deux chaînes C (style strcmp).
// Convention SysV x86-64: left=rdi, right=rsi, return=eax.
int dvd_strcmp(const char* left, const char* right);

// Compare mémoire (style memcmp).
// Convention SysV x86-64: left=rdi, right=rsi, len=rdx, return=eax.
int dvd_memcmp(const void* left, const void* right, std::size_t len);

// Remplissage mémoire en ASM.
// Convention SysV x86-64: dst=rdi, value=rsi, len=rdx, return=rax(dst).
void* dvd_memset(void* dst, int value, std::size_t len);
// Version AVX2 (si CPU supporte AVX2).
void* dvd_memset_avx2(void* dst, int value, std::size_t len);

// Retourne la plus longue séquence consécutive d'octets zéro.
// Convention SysV x86-64: data=rdi, len=rsi, return=rax.
std::size_t dvd_max_zero_run(const std::uint8_t* data, std::size_t len);
}

DVD_ALWAYS_INLINE void* memcpy_fast(void* dst, const void* src, std::size_t len) {
    if (len == 0) {
        return dst;
    }
    if (len >= kMemcpyAvx2Threshold) {
#if defined(__GNUC__) || defined(__clang__)
        if (has_avx2_cpu()) {
            return dvd_memcpy_avx2(dst, src, len);
        }
#endif
    }
    return dvd_memcpy(dst, src, len);
}

DVD_ALWAYS_INLINE std::size_t strlen_fast(const char* text) {
    return text == nullptr ? 0u : dvd_strlen(text);
}

DVD_ALWAYS_INLINE std::size_t strnlen_fast(const char* text, std::size_t max_len) {
    return (text == nullptr || max_len == 0) ? 0u : dvd_strnlen(text, max_len);
}

DVD_ALWAYS_INLINE std::size_t max_zero_run_fast(const std::uint8_t* data, std::size_t len) {
    return (data == nullptr || len == 0) ? 0u : dvd_max_zero_run(data, len);
}

DVD_ALWAYS_INLINE char* strcpy_fast(char* dst, const char* src) {
    return dvd_strcpy(dst, src);
}

DVD_ALWAYS_INLINE int strcmp_fast(const char* left, const char* right) {
    return dvd_strcmp(left, right);
}

DVD_ALWAYS_INLINE int memcmp_fast(const void* left, const void* right, std::size_t len) {
    return dvd_memcmp(left, right, len);
}

DVD_ALWAYS_INLINE void* memset_fast(void* dst, int value, std::size_t len) {
    if (len == 0) {
        return dst;
    }
    if (len >= kMemsetAvx2Threshold) {
#if defined(__GNUC__) || defined(__clang__)
        if (has_avx2_cpu()) {
            return dvd_memset_avx2(dst, value, len);
        }
#endif
    }
    return dvd_memset(dst, value, len);
}
#else
#include <cstring>

DVD_ALWAYS_INLINE void* memcpy_fast(void* dst, const void* src, std::size_t len) {
    return std::memcpy(dst, src, len);
}

DVD_ALWAYS_INLINE std::size_t strlen_fast(const char* text) {
    return text == nullptr ? 0u : std::strlen(text);
}

DVD_ALWAYS_INLINE std::size_t max_zero_run_fast(const std::uint8_t* data, std::size_t len) {
    if (data == nullptr || len == 0) {
        return 0u;
    }

    std::size_t max_run = 0;
    std::size_t cur_run = 0;

    for (std::size_t i = 0; i < len; ++i) {
        const auto is_zero = (data[i] == 0u);
        cur_run = is_zero ? (cur_run + 1u) : 0u;
        if (is_zero) {
            max_run = (cur_run > max_run) ? cur_run : max_run;
        }
    }

    return max_run;
}

DVD_ALWAYS_INLINE void* memset_fast(void* dst, int value, std::size_t len) {
    return std::memset(dst, value, len);
}

DVD_ALWAYS_INLINE std::size_t strnlen_fast(const char* text, std::size_t max_len) {
    if (text == nullptr || max_len == 0) {
        return 0u;
    }
    std::size_t len = 0;
    while (len < max_len && text[len] != '\0') {
        ++len;
    }
    return len;
}

DVD_ALWAYS_INLINE char* strcpy_fast(char* dst, const char* src) {
    if (dst == nullptr || src == nullptr) {
        return dst;
    }
    auto* out = dst;
    do {
        *out = *src;
        ++out;
        ++src;
    } while (out[-1] != '\0');
    return dst;
}

DVD_ALWAYS_INLINE int strcmp_fast(const char* left, const char* right) {
    if (left == nullptr || right == nullptr) {
        return (left == right) ? 0 : (left ? 1 : -1);
    }
    while (*left != '\0' && *left == *right) {
        ++left;
        ++right;
    }
    return static_cast<int>(static_cast<unsigned char>(*left))
        - static_cast<int>(static_cast<unsigned char>(*right));
}

DVD_ALWAYS_INLINE int memcmp_fast(const void* left, const void* right, std::size_t len) {
    if (len == 0 || left == right) {
        return 0;
    }
    const auto* l = static_cast<const std::uint8_t*>(left);
    const auto* r = static_cast<const std::uint8_t*>(right);
    for (std::size_t i = 0; i < len; ++i) {
        if (l[i] != r[i]) {
            return static_cast<int>(l[i]) - static_cast<int>(r[i]);
        }
    }
    return 0;
}
#endif

}  // namespace dvdextractor::common

#endif  // DVDEXTRACTOR_COMMON_MEM_OPS_H_
