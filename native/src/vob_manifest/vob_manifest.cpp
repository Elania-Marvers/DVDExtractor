// vob_manifest.cpp
//
// Parseur léger d'un dossier VIDEO_TS.
// Objectif: générer une manifest JSON minimaliste pour la pile Homebrew.
// Version pro: parsing sans regex + helpers inline + sortie triée déterministe.

#include <algorithm>
#include <cctype>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <map>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "common/json_escape.h"

namespace {

struct VobPart {
    int title;
    int part;
    std::filesystem::path path;
};

[[nodiscard]] constexpr bool is_vob_extension(std::string_view name, std::size_t offset) {
    return offset + 4 == name.size()
        && name[offset] == '.'
        && (name[offset + 1] == 'V' || name[offset + 1] == 'v')
        && (name[offset + 2] == 'O' || name[offset + 2] == 'o')
        && (name[offset + 3] == 'B' || name[offset + 3] == 'b');
}

// Parse VTS_<title>_<part>.VOB  (sans regex pour gagner en perf sur grands dossiers).
[[nodiscard]] bool parse_vob_filename(std::string_view name, int& title, int& part) {
    if (name.size() < 10 || name.compare(0, 4, "VTS_") != 0) {
        return false;
    }

    title = 0;
    part = 0;
    std::size_t idx = 4;

    while (idx < name.size() && std::isdigit(static_cast<unsigned char>(name[idx]))) {
        title = (title * 10) + (name[idx] - '0');
        ++idx;
    }
    if (idx == 4 || idx >= name.size() || name[idx] != '_') {
        return false;
    }
    ++idx;

    while (idx < name.size() && std::isdigit(static_cast<unsigned char>(name[idx]))) {
        part = (part * 10) + (name[idx] - '0');
        ++idx;
    }
    if (part <= 0 || idx >= name.size()) {
        return false;
    }

    return is_vob_extension(name, idx);
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::cerr << "usage: dvd_vob_manifest <VIDEO_TS path>\n";
        return 2;
    }

    const std::filesystem::path video_ts = argv[1];
    if (!std::filesystem::exists(video_ts) || !std::filesystem::is_directory(video_ts)) {
        return 3;
    }

    std::vector<VobPart> parts;
    try {
        for (const auto& entry : std::filesystem::directory_iterator(video_ts)) {
            if (!entry.is_regular_file()) {
                continue;
            }

            const std::string filename = entry.path().filename().string();
            int title = 0;
            int part = 0;
            if (!parse_vob_filename(filename, title, part)) {
                continue;
            }

            parts.push_back({title, part, entry.path()});
        }
    } catch (const std::exception&) {
        return 4;
    }

    if (parts.empty()) {
        std::cout << "{ \"titles\": [] }\n";
        return 0;
    }

    std::map<int, std::vector<VobPart>> by_title;
    for (const auto& part : parts) {
        by_title[part.title].push_back(part);
    }

    std::vector<std::pair<int, std::vector<std::string>>> ordered;
    ordered.reserve(by_title.size());

    for (auto& kv : by_title) {
        auto& list = kv.second;
        std::sort(list.begin(), list.end(), [](const VobPart& lhs, const VobPart& rhs) {
            return lhs.part < rhs.part;
        });

        std::vector<std::string> files;
        files.reserve(list.size());
        for (const auto& item : list) {
            files.push_back(item.path.string());
        }

        ordered.push_back({kv.first, std::move(files)});
    }

    std::sort(ordered.begin(), ordered.end(), [](const auto& lhs, const auto& rhs) {
        if (lhs.second.size() != rhs.second.size()) {
            return lhs.second.size() > rhs.second.size();
        }
        return lhs.first < rhs.first;
    });

    std::cout << "{ \"titles\": [";
    bool first_title = true;
    for (const auto& [title, files] : ordered) {
        if (!first_title) {
            std::cout << ",";
        }
        first_title = false;

        std::uintmax_t total_size = 0;
        for (const auto& file : files) {
            total_size += static_cast<std::uintmax_t>(std::filesystem::file_size(file));
        }

        std::cout << "\n  {\"id\":" << title << ",\"size\":" << total_size << ",\"parts\":[";
        bool first_file = true;
        for (const auto& file : files) {
            if (!first_file) {
                std::cout << ",";
            }
            first_file = false;
            std::cout << '"' << dvdextractor::common::json_escape(file) << '"';
        }
        std::cout << "]}";
    }

    std::cout << "\n]}\n";
    return 0;
}
