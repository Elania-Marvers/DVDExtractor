// dvd_reader_dump.cpp
//
// Outil natif de dump VOB via libdvdread.
// Objectif:
// - explorer les titres disponibles (mode --list-titles)
// - dumper un titre (mode normal) en VOB brut vers un fichier local
//
// Ce module est utilisé par Python en ingénieur mode: dump -> ffmpeg.

#include <dvdread/dvd_reader.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <string>
#include <string_view>
#include <vector>

namespace fs = std::filesystem;

struct TitleInfo {
    int title;
    int64_t size_bytes;
    int64_t blocks;
};

static std::string escape_json(std::string_view value) {
    std::string out;
    out.reserve(value.size() + 16);

    for (unsigned char c : value) {
        switch (c) {
            case '\\': out += "\\\\"; break;
            case '"': out += "\\\""; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default: out += static_cast<char>(c); break;
        }
    }
    return out;
}

static bool parse_positive_int(const char* txt, int& value) {
    if (!txt || !*txt) {
        return false;
    }

    char* end = nullptr;
    long converted = std::strtol(txt, &end, 10);
    if (!end || *end != '\0') {
        return false;
    }
    if (converted <= 0 || converted > 9999) {
        return false;
    }

    value = static_cast<int>(converted);
    return true;
}

static int scan_titles(dvd_reader_t* dvd, std::vector<TitleInfo>& out_titles) {
    if (!dvd) {
        return 1;
    }

    constexpr int kMaxTitle = 99;
    for (int title = 1; title <= kMaxTitle; ++title) {
        dvd_stat_t stats{};
        if (DVDFileStat(dvd, title, DVD_READ_TITLE_VOBS, &stats) != 0) {
            continue;
        }

        if (stats.nr_parts <= 0 || stats.size <= 0) {
            continue;
        }

        out_titles.push_back({title, stats.size, stats.size / DVD_VIDEO_LB_LEN});
    }

    if (out_titles.empty()) {
        return 1;
    }

    std::sort(out_titles.begin(), out_titles.end(), [](const TitleInfo& a, const TitleInfo& b) {
        if (a.size_bytes != b.size_bytes) {
            return a.size_bytes > b.size_bytes;
        }
        return a.blocks > b.blocks;
    });

    return 0;
}

static int cmd_list_titles(const std::string& source) {
    dvd_reader_t* dvd = DVDOpen(source.c_str());
    if (!dvd) {
        std::cerr << "Cannot open source\n";
        return 11;
    }

    std::vector<TitleInfo> titles;
    const int scan = scan_titles(dvd, titles);
    DVDClose(dvd);

    if (scan != 0) {
        std::cout << "{\"source\":\"" << escape_json(source) << "\",\"titles\":[]}\n";
        return 0;
    }

    std::cout << "{\"source\":\"" << escape_json(source) << "\",\"titles\":[";
    for (size_t idx = 0; idx < titles.size(); ++idx) {
        const auto& item = titles[idx];
        if (idx) {
            std::cout << ',';
        }
        std::cout << '{'
                  << "\"id\":" << item.title << ','
                  << "\"blocks\":" << item.blocks << ','
                  << "\"size\":" << item.size_bytes << '}';
    }
    std::cout << "]}\n";
    return 0;
}

static int cmd_dump_title(const std::string& source, int title, const fs::path& output) {
    dvd_reader_t* dvd = DVDOpen(source.c_str());
    if (!dvd) {
        std::cerr << "Cannot open source\n";
        return 12;
    }

    dvd_file_t* file = DVDOpenFile(dvd, title, DVD_READ_TITLE_VOBS);
    if (!file) {
        DVDClose(dvd);
        std::cerr << "Cannot open title " << title << " on source\n";
        return 13;
    }

    if (output.has_parent_path()) {
        std::error_code ec;
        fs::create_directories(output.parent_path(), ec);
    }

    std::ofstream out(output, std::ios::binary);
    if (!out) {
        DVDCloseFile(file);
        DVDClose(dvd);
        std::cerr << "Cannot open output file\n";
        return 14;
    }

    constexpr int kBlockChunk = 64;
    std::vector<uint8_t> buffer(static_cast<size_t>(DVD_VIDEO_LB_LEN) * kBlockChunk);

    const auto begin = std::chrono::steady_clock::now();
    int64_t offset_blocks = 0;
    int64_t total_blocks = 0;
    int64_t total_bytes = 0;
    const int64_t max_report_ms = 600;
    auto last_report = std::chrono::steady_clock::now();

    while (true) {
        int64_t remain = (std::numeric_limits<int64_t>::max() - offset_blocks);
        const auto read_blocks = static_cast<size_t>(std::min<int64_t>(remain, kBlockChunk));
        const ssize_t blocks = DVDReadBlocks(file, static_cast<int>(offset_blocks), read_blocks, buffer.data());

        if (blocks < 0) {
            std::cerr << "Read failed at block " << offset_blocks << '\n';
            DVDCloseFile(file);
            DVDClose(dvd);
            return 15;
        }

        if (blocks == 0) {
            break;
        }

        const std::streamsize payload = blocks * DVD_VIDEO_LB_LEN;
        out.write(reinterpret_cast<const char*>(buffer.data()), payload);
        if (!out) {
            std::cerr << "Write failed\n";
            DVDCloseFile(file);
            DVDClose(dvd);
            return 16;
        }

        offset_blocks += blocks;
        total_blocks += blocks;
        total_bytes += payload;

        auto now = std::chrono::steady_clock::now();
        const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(now - last_report).count();
        if (elapsed_ms >= max_report_ms) {
            std::cerr << "DUMP_PROGRESS blocks=" << total_blocks << " bytes=" << total_bytes << " title=" << title << "\n";
            last_report = now;
        }
    }

    out.close();
    DVDCloseFile(file);
    DVDClose(dvd);

    if (total_bytes <= 0) {
        std::cerr << "Empty title output\n";
        return 17;
    }

    const auto end = std::chrono::steady_clock::now();
    const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(end - begin).count();

    std::cout << '{'
              << "\"source\":\"" << escape_json(source) << "\"," 
              << "\"title\":" << title << ','
              << "\"blocks\":" << total_blocks << ','
              << "\"bytes\":" << total_bytes << ','
              << "\"output\":\"" << escape_json(output.string()) << "\"," 
              << "\"elapsed_ms\":" << elapsed_ms << "}\n";

    return 0;
}

static void usage(const char* binary) {
    std::cout << "usage: " << (binary ? binary : "dvd_reader_dump")
              << " [--list-titles] [--title N] --output OUT SOURCE\n";
}

int main(int argc, char** argv) {
    if (argc < 2) {
        usage(argv[0]);
        return 2;
    }

    std::string source;
    std::string output;
    bool list_titles = false;
    int title = 1;

    int idx = 1;
    while (idx < argc) {
        std::string_view arg = argv[idx];
        if (arg == "--list-titles") {
            list_titles = true;
            ++idx;
            continue;
        }

        if (arg == "--title") {
            if (idx + 1 >= argc) {
                std::cerr << "missing --title value\n";
                return 3;
            }
            if (!parse_positive_int(argv[idx + 1], title)) {
                std::cerr << "invalid --title value\n";
                return 3;
            }
            idx += 2;
            continue;
        }

        if (arg == "--output") {
            if (idx + 1 >= argc) {
                std::cerr << "missing --output value\n";
                return 4;
            }
            output = argv[idx + 1];
            idx += 2;
            continue;
        }

        if (source.empty()) {
            source = argv[idx];
            ++idx;
            continue;
        }

        std::cerr << "unknown argument: " << arg << '\n';
        return 4;
    }

    if (source.empty()) {
        usage(argv[0]);
        return 2;
    }

    if (list_titles) {
        return cmd_list_titles(source);
    }

    if (output.empty()) {
        std::cerr << "--output is required\n";
        return 5;
    }

    return cmd_dump_title(source, title, fs::path(output));
}
