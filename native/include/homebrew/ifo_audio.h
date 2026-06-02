#ifndef DVDEXTRACTOR_HOMEBREW_IFO_AUDIO_H_
#define DVDEXTRACTOR_HOMEBREW_IFO_AUDIO_H_

#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace dvdextractor::homebrew {

struct IfoAudioStream {
    std::uint8_t substream_id{0};
    std::string language;
    std::string format;
    unsigned int channels{0};
};

class IfoAudioReader final {
public:
    [[nodiscard]] static std::vector<IfoAudioStream> read_title_audio(const fs::path& video_ts, int title);
};

}  // namespace dvdextractor::homebrew

#endif  // DVDEXTRACTOR_HOMEBREW_IFO_AUDIO_H_
