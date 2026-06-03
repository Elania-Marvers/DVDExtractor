#ifndef DVDEXTRACTOR_HOMEBREW_NATIVE_EXTRACTOR_H_
#define DVDEXTRACTOR_HOMEBREW_NATIVE_EXTRACTOR_H_

#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

#include "homebrew/models.h"
#include "homebrew/program_stream_demuxer.h"
#include "homebrew/segment_preflight.h"

namespace fs = std::filesystem;

namespace dvdextractor::homebrew {

class NativeDvdExtractor final {
public:
    struct Options {
        fs::path video_ts;
        fs::path output;
        fs::path work_dir;
        std::string ffmpeg{"ffmpeg"};
        int title{0};
        bool keep_temp{false};
        std::string preferred_audio_language{"fr"};
    };

    struct Result {
        int title{0};
        std::uint64_t bytes_prepared{0};
        fs::path temp_vob;
        fs::path output;
    };

    explicit NativeDvdExtractor(Options options);

    [[nodiscard]] Result extract() const;

private:
    [[nodiscard]] TitleManifest pick_title(const std::vector<TitleManifest>& titles) const;
    [[nodiscard]] fs::path build_temp_path(int title) const;
    [[nodiscard]] fs::path build_demux_dir(int title) const;
    [[nodiscard]] std::vector<SegmentProbeReport> preflight_title(const TitleManifest& title) const;
    [[nodiscard]] std::uint64_t prepare_program_stream(const TitleManifest& title, const fs::path& temp_vob) const;
    [[nodiscard]] std::uint64_t prepare_program_stream_with_dvdread(int title, const fs::path& temp_vob) const;
    void inspect_program_stream(const fs::path& input_vob) const;
    [[nodiscard]] bool demux_then_transcode_to_mp4(const fs::path& input_vob, const fs::path& demux_dir, int title) const;
    [[nodiscard]] bool transcode_to_mp4(const fs::path& input_vob, int title) const;
    [[nodiscard]] std::vector<std::string> build_ffmpeg_demux_args(const DemuxSummary& summary, int title) const;
    [[nodiscard]] std::vector<std::string> build_ffmpeg_args(
        const fs::path& input_vob,
        int title,
        bool with_audio,
        bool force_mpeg_input) const;
    [[nodiscard]] bool valid_output() const;

    Options options_;
};

}  // namespace dvdextractor::homebrew

#endif  // DVDEXTRACTOR_HOMEBREW_NATIVE_EXTRACTOR_H_
