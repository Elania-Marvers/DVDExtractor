#include "homebrew/commands.h"

#include <memory>
#include <iostream>
#include <ostream>

#include <filesystem>
#include <string>
#include <string_view>
#include <vector>

#include "common/perf.h"
#include "homebrew/errors.h"

void usage(const char* exe) {
    std::cerr << "usage:\n"
              << "  " << exe << " scan <VIDEO_TS_DIR>\n"
              << "  " << exe << " copy --source <input> --output <output>\n"
              << "  " << exe << " concat --output <output> <part1> <part2> ...\n"
              << "  " << exe << " extract --video-ts <VIDEO_TS_DIR> --output <movie.mp4> [--title N] [--ffmpeg ffmpeg] [--work-dir dir] [--keep-temp]\n";
}

namespace {

constexpr std::string_view kCommandScan = "scan";
constexpr std::string_view kCommandCopy = "copy";
constexpr std::string_view kCommandConcat = "concat";
constexpr std::string_view kCommandExtract = "extract";

}  // namespace

int main(int argc, char* argv[]) {
    try {
        if (argc < 2) {
            usage(argv[0]);
            return 2;
        }

        std::unique_ptr<dvdextractor::homebrew::HomebrewCommand> command;
        const std::string command_name = argv[1];

        if (command_name == kCommandScan) {
            if (argc < 3) {
                throw dvdextractor::homebrew::HomebrewError("scan needs <VIDEO_TS_DIR>");
            }
            command = std::make_unique<dvdextractor::homebrew::ScanCommand>(argv[2]);
        } else if (command_name == kCommandCopy) {
            std::filesystem::path source;
            std::filesystem::path output;

            for (int i = 2; i < argc; ++i) {
                const std::string arg = argv[i];
                if (arg == "--source" && i + 1 < argc) {
                    source = argv[++i];
                } else if (arg == "--output" && i + 1 < argc) {
                    output = argv[++i];
                }
            }

            if (source.empty() || output.empty()) {
                usage(argv[0]);
                return 2;
            }

            command = std::make_unique<dvdextractor::homebrew::CopyCommand>(source, output);
        } else if (command_name == kCommandConcat) {
            std::filesystem::path output;
            std::vector<std::filesystem::path> parts;

            for (int i = 2; i < argc; ++i) {
                const std::string arg = argv[i];
                if (arg == "--output" && i + 1 < argc) {
                    output = argv[++i];
                } else if (arg.rfind("-", 0) != 0) {
                    parts.push_back(argv[i]);
                }
            }

            if (output.empty() || parts.empty()) {
                usage(argv[0]);
                return 2;
            }

            command = std::make_unique<dvdextractor::homebrew::ConcatCommand>(output, parts);
        } else if (command_name == kCommandExtract) {
            std::filesystem::path video_ts;
            std::filesystem::path output;
            std::filesystem::path work_dir;
            std::string ffmpeg = "ffmpeg";
            int title = 0;
            bool keep_temp = false;

            for (int i = 2; i < argc; ++i) {
                const std::string arg = argv[i];
                if (arg == "--video-ts" && i + 1 < argc) {
                    video_ts = argv[++i];
                } else if (arg == "--output" && i + 1 < argc) {
                    output = argv[++i];
                } else if (arg == "--title" && i + 1 < argc) {
                    title = std::stoi(argv[++i]);
                } else if (arg == "--ffmpeg" && i + 1 < argc) {
                    ffmpeg = argv[++i];
                } else if (arg == "--work-dir" && i + 1 < argc) {
                    work_dir = argv[++i];
                } else if (arg == "--keep-temp") {
                    keep_temp = true;
                }
            }

            if (video_ts.empty() || output.empty()) {
                usage(argv[0]);
                return 2;
            }

            command = std::make_unique<dvdextractor::homebrew::ExtractCommand>(
                video_ts,
                output,
                title,
                ffmpeg,
                work_dir,
                keep_temp);
        }

        if (!command) {
            usage(argv[0]);
            return 3;
        }

        return command->execute(std::cout, std::cerr);
    } catch (const dvdextractor::homebrew::HomebrewError& exc) {
        std::cerr << "HOMEBREW_ERROR: " << exc.what() << '\n';
        return 1;
    } catch (const std::exception& exc) {
        std::cerr << "HOMEBREW_ERROR: " << exc.what() << '\n';
        return 1;
    }
}
