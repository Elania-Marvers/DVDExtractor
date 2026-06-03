// dvd_reader_dump.cpp
//
// Outil natif de dump VOB via libdvdread.
// - listing des titres: --list-titles
// - dump binaire d'un titre: --title N --output OUT

#include <dvdread/dvd_reader.h>

#include <algorithm>
#include <array>
#include <cassert>
#include <chrono>
#include <charconv>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

#include "common/json_escape.h"
#include "common/perf.h"

namespace {

struct TitleInfo {
    int title;
    std::uint64_t blocks;
    std::uint64_t size_bytes;
};

constexpr int kMaxTitleCount = 99;
constexpr int kDefaultTitle = 1;
constexpr std::size_t kProgressIntervalMs = 600;

bool parse_positive_int(std::string_view text, int& value) {
    if (text.empty()) {
        return false;
    }

    int parsed = 0;
    const char* begin = text.data();
    const char* end = begin + text.size();
    const auto [ptr, ec] = std::from_chars(begin, end, parsed);
    if (ec != std::errc{} || ptr != end || parsed <= 0 || parsed > kMaxTitleCount) {
        return false;
    }

    value = parsed;
    return true;
}

std::vector<TitleInfo> scan_titles(dvd_reader_t* dvd) {
    std::vector<TitleInfo> result;
    if (!dvd) {
        return result;
    }

    result.reserve(kMaxTitleCount);
    for (int title = 1; title <= kMaxTitleCount; ++title) {
        dvd_stat_t stats{};
        if (DVDFileStat(dvd, title, DVD_READ_TITLE_VOBS, &stats) != 0) {
            continue;
        }
        if (stats.nr_parts <= 0 || stats.size <= 0) {
            continue;
        }

        result.push_back({
            title,
            static_cast<std::uint64_t>(stats.size / DVD_VIDEO_LB_LEN),
            static_cast<std::uint64_t>(stats.size)
        });
    }

    std::sort(result.begin(), result.end(), [](const TitleInfo& lhs, const TitleInfo& rhs) {
        if (lhs.size_bytes != rhs.size_bytes) {
            return lhs.size_bytes > rhs.size_bytes;
        }
        return lhs.blocks > rhs.blocks;
    });

    return result;
}

int cmd_list_titles(const std::string& source) {
    dvd_reader_t* dvd = DVDOpen(source.c_str());
    if (!dvd) {
        std::cerr << "Cannot open source\n";
        return 11;
    }

    const auto titles = scan_titles(dvd);
    DVDClose(dvd);

    std::cout << '{' << "\"source\":" << '"' << dvdextractor::common::json_escape(source) << "\","
              << "\"titles\":[";
    for (std::size_t i = 0; i < titles.size(); ++i) {
        const auto& item = titles[i];
        if (i > 0) {
            std::cout << ',';
        }
        std::cout << '{'
                  << "\"id\":" << item.title << ','
                  << "\"blocks\":" << item.blocks << ','
                  << "\"size\":" << item.size_bytes
                  << '}';
    }
    std::cout << "]}\n";
    return 0;
}

int cmd_dump_title(const std::string& source, int title, const std::filesystem::path& output) {
    dvd_reader_t* dvd = DVDOpen(source.c_str());
    if (!dvd) {
        std::cerr << "Cannot open source\n";
        return 12;
    }

    dvd_stat_t stats{};
    if (DVDFileStat(dvd, title, DVD_READ_TITLE_VOBS, &stats) != 0 || stats.size <= 0) {
        DVDClose(dvd);
        std::cerr << "Cannot stat title " << title << " on source\n";
        return 18;
    }
    const auto expected_blocks = static_cast<std::uint64_t>(stats.size / DVD_VIDEO_LB_LEN);
    if (expected_blocks == 0u) {
        DVDClose(dvd);
        std::cerr << "Title " << title << " has no readable blocks\n";
        return 19;
    }

    dvd_file_t* file = DVDOpenFile(dvd, title, DVD_READ_TITLE_VOBS);
    if (!file) {
        DVDClose(dvd);
        std::cerr << "Cannot open title " << title << " on source\n";
        return 13;
    }

    if (output.has_parent_path()) {
        std::error_code ec;
        std::filesystem::create_directories(output.parent_path(), ec);
    }

    std::ofstream out(output, std::ios::binary);
    if (!out) {
        DVDCloseFile(file);
        DVDClose(dvd);
        std::cerr << "Cannot open output file\n";
        return 14;
    }

    constexpr std::size_t kChunkBytes = dvdextractor::common::kTransferChunkBytes;
    constexpr std::size_t kBlockChunk = kChunkBytes / static_cast<std::size_t>(DVD_VIDEO_LB_LEN);
    static_assert(kChunkBytes % static_cast<std::size_t>(DVD_VIDEO_LB_LEN) == 0, "chunk must align DVD blocks");
    const int max_blocks_per_read = static_cast<int>(kBlockChunk);

    std::vector<std::uint8_t> buffer(kChunkBytes);
    const auto start = std::chrono::steady_clock::now();
    auto last_report = start;

    std::uint64_t offset_blocks = 0;
    std::uint64_t total_blocks = 0;
    std::uint64_t total_bytes = 0;
    while (offset_blocks < expected_blocks) {
        const auto remaining_blocks = expected_blocks - offset_blocks;
        const int request_blocks = static_cast<int>(
            std::min<std::uint64_t>(remaining_blocks, static_cast<std::uint64_t>(max_blocks_per_read)));
        assert(request_blocks > 0);

        const int read_blocks = DVDReadBlocks(file, static_cast<int>(offset_blocks), request_blocks, buffer.data());
        if (read_blocks < 0) {
            std::cerr << "Read failed at block " << offset_blocks << '\n';
            DVDCloseFile(file);
            DVDClose(dvd);
            return 15;
        }
        if (read_blocks == 0) {
            std::cerr << "Short read at block " << offset_blocks << " of " << expected_blocks << '\n';
            DVDCloseFile(file);
            DVDClose(dvd);
            return 15;
        }
        assert(read_blocks <= request_blocks);

        const auto payload = static_cast<std::size_t>(read_blocks) * DVD_VIDEO_LB_LEN;
        out.write(reinterpret_cast<const char*>(buffer.data()), static_cast<std::streamsize>(payload));
        if (!out) {
            std::cerr << "Write failed\n";
            DVDCloseFile(file);
            DVDClose(dvd);
            return 16;
        }

        offset_blocks += static_cast<std::uint64_t>(read_blocks);
        total_blocks += static_cast<std::uint64_t>(read_blocks);
        total_bytes += static_cast<std::uint64_t>(payload);

        const auto now = std::chrono::steady_clock::now();
        if (std::chrono::duration_cast<std::chrono::milliseconds>(now - last_report).count() >= kProgressIntervalMs) {
            std::cerr << "DUMP_PROGRESS blocks=" << total_blocks << " bytes=" << total_bytes << " title=" << title << "\n";
            last_report = now;
        }
    }

    out.close();
    DVDCloseFile(file);
    DVDClose(dvd);

    if (total_bytes == 0) {
        std::cerr << "Empty title output\n";
        return 17;
    }

    const auto end = std::chrono::steady_clock::now();
    const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(end - start).count();

    std::cout << '{'
              << "\"source\":" << '"' << dvdextractor::common::json_escape(source) << "\","
              << "\"title\":" << title << ','
              << "\"blocks\":" << total_blocks << ','
              << "\"expected_blocks\":" << expected_blocks << ','
              << "\"bytes\":" << total_bytes << ','
              << "\"output\":" << '"' << dvdextractor::common::json_escape(output.string()) << "\","
              << "\"elapsed_ms\":" << elapsed_ms
              << "}\n";

    return 0;
}

void usage(const char* binary) {
    std::cout << "usage: " << (binary ? binary : "dvd_reader_dump")
              << " [--list-titles] [--title N] --output OUT SOURCE\n";
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        usage(argv[0]);
        return 2;
    }

    std::string source;
    std::string output;
    bool list_titles = false;
    int title = kDefaultTitle;

    int index = 1;
    while (index < argc) {
        const std::string_view arg = argv[index];
        if (arg == "--list-titles") {
            list_titles = true;
            ++index;
            continue;
        }
        if (arg == "--title") {
            if (index + 1 >= argc || !parse_positive_int(argv[index + 1], title)) {
                std::cerr << "invalid --title value\n";
                return 4;
            }
            index += 2;
            continue;
        }
        if (arg == "--output") {
            if (index + 1 >= argc) {
                std::cerr << "missing --output value\n";
                return 6;
            }
            output = argv[index + 1];
            index += 2;
            continue;
        }

        source = argv[index];
        ++index;
    }

    if (source.empty()) {
        usage(argv[0]);
        return 5;
    }

    if (list_titles) {
        return cmd_list_titles(source);
    }

    if (output.empty()) {
        std::cerr << "missing --output\n";
        return 6;
    }

    return cmd_dump_title(source, title, output);
}
