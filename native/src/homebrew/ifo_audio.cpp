#include "homebrew/ifo_audio.h"

#include <dvdread/dvd_reader.h>
#include <dvdread/ifo_read.h>

#include <stdexcept>

namespace dvdextractor::homebrew {

namespace {

class DvdHandle final {
public:
    explicit DvdHandle(const fs::path& source)
        : handle_(DVDOpen(source.string().c_str())) {}

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
        : handle_(dvd == nullptr ? nullptr : ifoOpenVTSI(dvd, title)) {}

    ~IfoHandle() {
        if (handle_ != nullptr) {
            ifoClose(handle_);
        }
    }

    IfoHandle(const IfoHandle&) = delete;
    IfoHandle& operator=(const IfoHandle&) = delete;

    [[nodiscard]] const vtsi_mat_t* vtsi_mat() const {
        return handle_ == nullptr ? nullptr : handle_->vtsi_mat;
    }

private:
    ifo_handle_t* handle_{nullptr};
};

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

}  // namespace

std::vector<IfoAudioStream> IfoAudioReader::read_title_audio(const fs::path& video_ts, int title) {
    if (title <= 0) {
        return {};
    }

    fs::path source = video_ts;
    if (source.filename() == "VIDEO_TS" || source.filename() == "video_ts") {
        source = source.parent_path();
    }
    if (source.empty()) {
        return {};
    }

    DvdHandle dvd(source);
    IfoHandle ifo(dvd.get(), title);
    const auto* mat = ifo.vtsi_mat();
    if (mat == nullptr) {
        return {};
    }

    std::vector<IfoAudioStream> streams;
    const auto count = static_cast<unsigned int>(mat->nr_of_vts_audio_streams);
    streams.reserve(count);
    for (unsigned int i = 0; i < count && i < 8u; ++i) {
        const audio_attr_t& attr = mat->vts_audio_attr[i];
        streams.push_back(IfoAudioStream{
            static_cast<std::uint8_t>(0x80u + i),
            language_code(attr),
            audio_format_name(attr.audio_format),
            static_cast<unsigned int>(attr.channels) + 1u,
        });
    }

    return streams;
}

}  // namespace dvdextractor::homebrew
