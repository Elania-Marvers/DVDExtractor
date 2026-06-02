#ifndef DVDEXTRACTOR_HOMEBREW_PROGRAM_STREAM_DEMUXER_H_
#define DVDEXTRACTOR_HOMEBREW_PROGRAM_STREAM_DEMUXER_H_

#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

#include "homebrew/ps_demux.h"

namespace fs = std::filesystem;

namespace dvdextractor::homebrew {

struct DemuxStreamSummary {
    std::uint8_t stream_id{0};
    std::uint8_t substream_id{0};
    bool has_substream{false};
    std::string kind;
    std::uint64_t packets{0};
    std::uint64_t payload_bytes{0};
    fs::path output_path;
};

struct DemuxSummary {
    fs::path input;
    fs::path output_dir;
    std::uint64_t input_bytes{0};
    std::uint64_t consumed_bytes{0};
    std::uint64_t pack_headers{0};
    std::uint64_t system_headers{0};
    std::uint64_t pes_packets{0};
    std::uint64_t video_packets{0};
    std::uint64_t audio_packets{0};
    std::uint64_t private_packets{0};
    std::uint64_t skipped_packets{0};
    std::uint64_t truncated_packets{0};
    std::vector<DemuxStreamSummary> streams;
};

class ProgramStreamDemuxer final {
public:
    struct Options {
        fs::path input;
        fs::path output_dir;
        bool extract_payloads{true};
        std::uint64_t max_bytes{0};
    };

    explicit ProgramStreamDemuxer(Options options);

    [[nodiscard]] DemuxSummary run() const;
    [[nodiscard]] DemuxSummary inspect() const;

private:
    [[nodiscard]] DemuxSummary execute(bool extract_payloads) const;

    Options options_;
};

}  // namespace dvdextractor::homebrew

#endif  // DVDEXTRACTOR_HOMEBREW_PROGRAM_STREAM_DEMUXER_H_
