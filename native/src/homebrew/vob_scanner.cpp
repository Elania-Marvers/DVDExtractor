#include "homebrew/vob_scanner.h"

#include <algorithm>
#include <filesystem>
#include <map>
#include <regex>

#include "homebrew/errors.h"

namespace dvdextractor::homebrew {

std::vector<TitleManifest> VobScanner::scan_video_ts(const fs::path& video_ts) {
    if (!fs::exists(video_ts) || !fs::is_directory(video_ts)) {
        throw HomebrewError("invalid VIDEO_TS path: " + video_ts.string());
    }

    const std::regex pattern{R"(VTS_(\d{1,2})_(\d{1,2})\.VOB)", std::regex::icase};
    std::map<int, std::vector<fs::path>> grouped;

    for (const auto& entry : fs::directory_iterator(video_ts)) {
        if (!entry.is_regular_file()) {
            continue;
        }

        const auto name = entry.path().filename().string();
        std::smatch match;
        if (!std::regex_match(name, match, pattern)) {
            continue;
        }

        const int title = std::stoi(match[1].str());
        const int part = std::stoi(match[2].str());
        if (part <= 0) {
            continue;
        }

        grouped[title].push_back(entry.path());
    }

    std::vector<TitleManifest> titles;
    titles.reserve(grouped.size());
    for (auto& item : grouped) {
        auto& parts = item.second;
        std::sort(parts.begin(), parts.end(), [](const fs::path& a, const fs::path& b) {
            return a.filename().string() < b.filename().string();
        });

        std::uint64_t total = 0;
        for (const auto& path : parts) {
            total += static_cast<std::uint64_t>(fs::file_size(path));
        }

        titles.push_back(TitleManifest{item.first, parts, total});
    }

    std::sort(titles.begin(), titles.end(), [](const TitleManifest& a, const TitleManifest& b) {
        if (a.total_bytes != b.total_bytes) {
            return a.total_bytes > b.total_bytes;
        }
        return a.title < b.title;
    });

    return titles;
}

}  // namespace dvdextractor::homebrew
