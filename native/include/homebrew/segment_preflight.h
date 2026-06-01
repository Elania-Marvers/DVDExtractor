#ifndef DVDEXTRACTOR_HOMEBREW_SEGMENT_PREFLIGHT_H_
#define DVDEXTRACTOR_HOMEBREW_SEGMENT_PREFLIGHT_H_

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

#include "homebrew/mpeg_probe.h"

namespace fs = std::filesystem;

namespace dvdextractor::homebrew {

struct SegmentProbeReport {
    fs::path path;
    std::uint64_t file_size{0};
    dvd_mpeg_probe_stats stats{};
    bool readable{false};
    std::string error;

    [[nodiscard]] bool likely_program_stream() const {
        return stats.likely_program_stream != 0u;
    }
};

class SegmentPreflight final {
public:
    struct Options {
        std::size_t sample_bytes{2u * 1024u * 1024u};
        std::size_t max_workers{0};
    };

    SegmentPreflight();
    explicit SegmentPreflight(Options options);

    [[nodiscard]] std::vector<SegmentProbeReport> scan(const std::vector<fs::path>& parts) const;
    void assert_usable(const std::vector<SegmentProbeReport>& report) const;

private:
    [[nodiscard]] SegmentProbeReport scan_one(const fs::path& part) const;

    Options options_;
};

}  // namespace dvdextractor::homebrew

#endif  // DVDEXTRACTOR_HOMEBREW_SEGMENT_PREFLIGHT_H_
