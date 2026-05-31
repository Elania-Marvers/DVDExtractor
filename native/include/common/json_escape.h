#ifndef DVDEXTRACTOR_COMMON_JSON_ESCAPE_H_
#define DVDEXTRACTOR_COMMON_JSON_ESCAPE_H_

#include <string>
#include <string_view>

namespace dvdextractor::common {

inline std::string json_escape(std::string_view input) {
    std::string out;
    out.reserve(input.size() + 16);

    for (const unsigned char ch : input) {
        switch (ch) {
            case '\\':
                out += "\\\\";
                break;
            case '"':
                out += "\\\"";
                break;
            case '\n':
                out += "\\n";
                break;
            case '\r':
                out += "\\r";
                break;
            case '\t':
                out += "\\t";
                break;
            default:
                out.push_back(static_cast<char>(ch));
        }
    }

    return out;
}

}  // namespace dvdextractor::common

#endif  // DVDEXTRACTOR_COMMON_JSON_ESCAPE_H_
