// vob_manifest.cpp
//
// Analyse le dossier VIDEO_TS et retourne une manifest JSON légère.
// Le format de sortie:
// {"titles":[{"id":1,"size":123456,"parts":["/abs/path/VTS_01_1.VOB","/abs/path/VTS_01_2.VOB"]}, ...]}

#include <algorithm>
#include <array>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <regex>
#include <string>
#include <utility>
#include <vector>

namespace fs = std::filesystem;

struct VobPart {
    int title;
    int part;
    fs::path path;
};

static std::string json_escape(const std::string& input) {
    std::string out;
    out.reserve(input.size() + 16);
    for (const unsigned char ch : input) {
        if (ch == '"' || ch == '\\') {
            out.push_back('\\');
            out.push_back(static_cast<char>(ch));
        } else if (ch == '\n') {
            out.append("\\n");
        } else if (ch == '\r') {
            out.append("\\r");
        } else if (ch == '\t') {
            out.append("\\t");
        } else {
            out.push_back(static_cast<char>(ch));
        }
    }
    return out;
}

int main(int argc, char** argv) {
    if (argc < 2) {
        std::cerr << "usage: dvd_vob_manifest <VIDEO_TS path>\n";
        return 2;
    }

    fs::path video_ts = argv[1];
    if (!fs::exists(video_ts) || !fs::is_directory(video_ts)) {
        return 3;
    }

    const std::regex pattern(R"(VTS_(\d{1,2})_(\d{1,2})\.VOB)", std::regex::icase);
    std::vector<VobPart> parts;

    try {
        for (const auto& entry : fs::directory_iterator(video_ts)) {
            if (!entry.is_regular_file()) {
                continue;
            }

            const std::string filename = entry.path().filename().string();
            std::smatch match;
            if (!std::regex_match(filename, match, pattern)) {
                continue;
            }

            const int title = std::stoi(match[1].str());
            const int part_no = std::stoi(match[2].str());
            if (part_no == 0) {
                continue;
            }
            parts.push_back({title, part_no, entry.path()});
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
        std::sort(list.begin(), list.end(), [](const VobPart& a, const VobPart& b) {
            return a.part < b.part;
        });

        std::vector<std::string> files;
        files.reserve(list.size());
        for (const auto& item : list) {
            files.push_back(item.path.string());
        }
        ordered.push_back({kv.first, files});
    }

    std::sort(ordered.begin(), ordered.end(), [](const auto& a, const auto& b) {
        if (a.second.size() != b.second.size()) {
            return a.second.size() > b.second.size();
        }
        return a.first < b.first;
    });

    std::cout << "{ \"titles\": [";
    bool first_title = true;
    for (const auto& [title, files] : ordered) {
        if (!first_title) {
            std::cout << ",";
        }
        first_title = false;

        std::uintmax_t size_total = 0;
        try {
            for (const auto& file : files) {
                size_total += fs::file_size(file);
            }
        } catch (...) {
            // no-op: keep zero if files disappear between scan and size check
        }

        std::cout << "\n  {\"id\":" << title << ",\"size\":" << size_total << ",\"parts\":[";
        bool first_file = true;
        for (const auto& file : files) {
            if (!first_file) {
                std::cout << ",";
            }
            first_file = false;
            std::cout << '"' << json_escape(file) << '"';
        }
        std::cout << "]}";
    }
    std::cout << "\n]}\n";
    return 0;
}
