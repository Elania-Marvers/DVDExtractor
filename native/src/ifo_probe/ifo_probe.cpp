#include <dvdread/dvd_reader.h>
#include <dvdread/ifo_read.h>

#include <charconv>
#include <cstdint>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>

#include "common/json_escape.h"

namespace {

constexpr int kMaxVtsTitle = 99;

class ProbeError final : public std::runtime_error {
public:
    explicit ProbeError(const std::string& message)
        : std::runtime_error(message) {}
};

class DvdHandle final {
public:
    explicit DvdHandle(const std::string& source)
        : handle_(DVDOpen(source.c_str())) {
        if (handle_ == nullptr) {
            throw ProbeError("cannot open DVD source: " + source);
        }
    }

    ~DvdHandle() {
        if (handle_ != nullptr) {
            DVDClose(handle_);
        }
    }

    DvdHandle(const DvdHandle&) = delete;
    DvdHandle& operator=(const DvdHandle&) = delete;

    [[nodiscard]] dvd_reader_t* get() const {
        return handle_;
    }

private:
    dvd_reader_t* handle_{nullptr};
};

class IfoHandle final {
public:
    IfoHandle(dvd_reader_t* dvd, int title)
        : handle_(ifoOpenVTSI(dvd, title)) {
        if (handle_ == nullptr || handle_->vtsi_mat == nullptr) {
            throw ProbeError("cannot open VTS IFO for title " + std::to_string(title));
        }
    }

    ~IfoHandle() {
        if (handle_ != nullptr) {
            ifoClose(handle_);
        }
    }

    IfoHandle(const IfoHandle&) = delete;
    IfoHandle& operator=(const IfoHandle&) = delete;

    [[nodiscard]] const vtsi_mat_t* vtsi_mat() const {
        return handle_->vtsi_mat;
    }

private:
    ifo_handle_t* handle_{nullptr};
};

bool parse_title(std::string_view text, int& out) {
    int value = 0;
    const auto* begin = text.data();
    const auto* end = begin + text.size();
    const auto parsed = std::from_chars(begin, end, value);
    if (parsed.ec != std::errc{} || parsed.ptr != end || value <= 0 || value > kMaxVtsTitle) {
        return false;
    }
    out = value;
    return true;
}

std::string audio_format_name(unsigned int value) {
    switch (value) {
        case 0: return "ac3";
        case 2: return "mpeg1";
        case 3: return "mpeg2ext";
        case 4: return "lpcm";
        case 6: return "dts";
        default: return "unknown";
    }
}

std::string stream_id_for(unsigned int index, const std::string& format) {
    std::ostringstream out;
    if (format == "mpeg1" || format == "mpeg2ext") {
        out << "0x" << std::hex << std::setw(2) << std::setfill('0') << (0xC0u + index);
    } else {
        out << "0xbd/0x" << std::hex << std::setw(2) << std::setfill('0') << (0x80u + index);
    }
    return out.str();
}

std::string language_code(const audio_attr_t& attr) {
    if (attr.lang_type == 0u || attr.lang_code == 0u) {
        return "";
    }

    const auto high = static_cast<char>((attr.lang_code >> 8u) & 0xFFu);
    const auto low = static_cast<char>(attr.lang_code & 0xFFu);
    if ((high >= 'a' && high <= 'z') && (low >= 'a' && low <= 'z')) {
        return std::string{high, low};
    }
    if ((low >= 'a' && low <= 'z') && (high >= 'a' && high <= 'z')) {
        return std::string{low, high};
    }
    return "";
}

std::string probe_json(const std::string& source, int title) {
    DvdHandle dvd(source);
    IfoHandle ifo(dvd.get(), title);
    const auto* mat = ifo.vtsi_mat();

    std::ostringstream out;
    out << '{';
    out << "\"source\":\"" << dvdextractor::common::json_escape(source) << "\",";
    out << "\"title\":" << title << ',';
    out << "\"audio_streams\":" << static_cast<unsigned int>(mat->nr_of_vts_audio_streams) << ',';
    out << "\"subpicture_streams\":" << static_cast<unsigned int>(mat->nr_of_vts_subp_streams) << ',';
    out << "\"audio\":[";

    const auto count = static_cast<unsigned int>(mat->nr_of_vts_audio_streams);
    for (unsigned int i = 0; i < count && i < 8u; ++i) {
        const audio_attr_t& attr = mat->vts_audio_attr[i];
        const auto format = audio_format_name(attr.audio_format);
        if (i > 0u) {
            out << ',';
        }

        out << '{';
        out << "\"index\":" << i << ',';
        out << "\"stream_id\":\"" << stream_id_for(i, format) << "\",";
        out << "\"format\":\"" << format << "\",";
        out << "\"language\":\"" << dvdextractor::common::json_escape(language_code(attr)) << "\",";
        out << "\"channels\":" << (static_cast<unsigned int>(attr.channels) + 1u) << ',';
        out << "\"sample_frequency_code\":" << static_cast<unsigned int>(attr.sample_frequency) << ',';
        out << "\"quantization_code\":" << static_cast<unsigned int>(attr.quantization) << ',';
        out << "\"lang_extension\":" << static_cast<unsigned int>(attr.lang_extension) << ',';
        out << "\"code_extension\":" << static_cast<unsigned int>(attr.code_extension);
        out << '}';
    }

    out << "]}";
    return out.str();
}

void usage(const char* exe) {
    std::cerr << "usage: " << (exe ? exe : "dvd_ifo_probe")
              << " --source <dvd mount/device> --title <VTS number>\n";
}

}  // namespace

int main(int argc, char** argv) {
    try {
        std::string source;
        int title = 0;

        for (int i = 1; i < argc; ++i) {
            const std::string_view arg = argv[i];
            if (arg == "--source" && i + 1 < argc) {
                source = argv[++i];
            } else if (arg == "--title" && i + 1 < argc) {
                if (!parse_title(argv[++i], title)) {
                    throw ProbeError("invalid --title value");
                }
            }
        }

        if (source.empty() || title <= 0) {
            usage(argv[0]);
            return 2;
        }

        std::cout << probe_json(source, title) << '\n';
        return 0;
    } catch (const ProbeError& exc) {
        std::cerr << "IFO_PROBE_ERROR: " << exc.what() << '\n';
        return 1;
    } catch (const std::exception& exc) {
        std::cerr << "IFO_PROBE_ERROR: " << exc.what() << '\n';
        return 1;
    }
}
