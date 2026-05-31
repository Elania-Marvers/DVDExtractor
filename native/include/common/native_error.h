#ifndef DVDEXTRACTOR_COMMON_NATIVE_ERROR_H_
#define DVDEXTRACTOR_COMMON_NATIVE_ERROR_H_

#include <cstddef>
#include <cstdint>
#include <exception>
#include <string>
#include <string_view>

#include "common/json_escape.h"

namespace dvdextractor::common {

enum class NativeErrorCode : std::uint32_t {
    kUsage = 2,
    kIo = 3,
    kNoData = 4,
    kData = 5,
    kRuntime = 6,
};

class NativeExecError final : public std::exception {
public:
    NativeExecError(
        NativeErrorCode code,
        std::string_view stage,
        std::string_view component,
        std::string_view message,
        std::string_view detail = {})
        : code_(code)
        , stage_(stage)
        , component_(component)
        , message_(message.empty() ? "native failure" : message)
        , detail_(detail) {}

    [[nodiscard]] NativeErrorCode code() const noexcept { return code_; }
    [[nodiscard]] const char* what() const noexcept override { return message_.c_str(); }
    [[nodiscard]] const char* stage() const noexcept { return stage_.c_str(); }
    [[nodiscard]] const char* component() const noexcept { return component_.c_str(); }
    [[nodiscard]] const char* detail() const noexcept { return detail_.c_str(); }

private:
    NativeErrorCode code_;
    std::string stage_;
    std::string component_;
    std::string message_;
    std::string detail_;
};

template <typename Stream>
inline void write_error_json(
    Stream& out,
    const NativeExecError& err,
    std::string_view analyzer,
    std::int32_t return_code) {
    out << '{';
    out << "\"ok\":false,";
    out << "\"analyzer\":\"" << json_escape(analyzer) << "\",";
    out << "\"error_code\":" << static_cast<std::uint32_t>(err.code()) << ',';
    out << "\"stage\":\"" << json_escape(err.stage()) << "\",";
    out << "\"component\":\"" << json_escape(err.component()) << "\",";
    out << "\"return_code\":" << return_code << ',';
    out << "\"message\":\"" << json_escape(err.what()) << "\",";
    out << "\"detail\":\"" << json_escape(err.detail()) << "\"";
    out << '}';
}

[[noreturn]] inline void raise_native_error(
    NativeErrorCode code,
    std::string_view stage,
    std::string_view component,
    std::string_view message,
    std::string_view detail = {}) {
    throw NativeExecError(code, stage, component, message, detail);
}

#define DVD_NATIVE_ASSERT(cond, code, stage, component, message) \
    do { \
        if (!(cond)) { \
            dvdextractor::common::raise_native_error(code, stage, component, message); \
        } \
    } while (0)

}  // namespace dvdextractor::common

#endif  // DVDEXTRACTOR_COMMON_NATIVE_ERROR_H_
