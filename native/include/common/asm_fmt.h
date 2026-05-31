#ifndef DVDEXTRACTOR_COMMON_ASM_FMT_H_
#define DVDEXTRACTOR_COMMON_ASM_FMT_H_

#include <cstddef>
#include <cstdint>
#include <string>

namespace dvdextractor::common {

extern "C" std::size_t dvd_u64_to_decimal(std::uint64_t value, char* out, std::size_t out_cap);

inline std::string u64_to_decimal(std::uint64_t value) {
    char tmp[32];
    const std::size_t len = dvd_u64_to_decimal(value, tmp, sizeof(tmp));
    return std::string(tmp, tmp + len);
}

}  // namespace dvdextractor::common

#endif  // DVDEXTRACTOR_COMMON_ASM_FMT_H_

