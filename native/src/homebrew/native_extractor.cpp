#include "homebrew/native_extractor.h"

#include <chrono>
#include <iostream>
#include <utility>
#include <unistd.h>

#include "homebrew/errors.h"
#include "homebrew/process_runner.h"
#include "homebrew/transfer_engines.h"
#include "homebrew/vob_scanner.h"

namespace dvdextractor::homebrew {

namespace {

std::uint64_t file_size_or_zero(const fs::path& path) {
    std::error_code ec;
    const auto size = fs::file_size(path, ec);
    return ec ? 0u : static_cast<std::uint64_t>(size);
}

}  // namespace

NativeDvdExtractor::NativeDvdExtractor(Options options)
    : options_(std::move(options)) {
    if (options_.ffmpeg.empty()) {
        options_.ffmpeg = "ffmpeg";
    }
}

NativeDvdExtractor::Result NativeDvdExtractor::extract() const {
    if (!fs::is_directory(options_.video_ts)) {
        throw HomebrewError("invalid VIDEO_TS path: " + options_.video_ts.string());
    }
    if (options_.output.empty()) {
        throw HomebrewError("missing output mp4 path");
    }

    if (!options_.output.parent_path().empty()) {
        fs::create_directories(options_.output.parent_path());
    }

    const auto titles = VobScanner::scan_video_ts(options_.video_ts);
    const auto title = pick_title(titles);
    const auto temp_vob = build_temp_path(title.title);

    Result result;
    result.title = title.title;
    result.temp_vob = temp_vob;
    result.output = options_.output;

    try {
        result.bytes_prepared = prepare_program_stream(title, temp_vob);
        if (result.bytes_prepared == 0u) {
            throw HomebrewError("native preparation produced empty VOB");
        }

        if (!transcode_to_mp4(temp_vob)) {
            throw HomebrewError("ffmpeg backend failed after native preparation");
        }
        if (!valid_output()) {
            throw HomebrewError("mp4 output is missing or empty: " + options_.output.string());
        }

        if (!options_.keep_temp) {
            fs::remove(temp_vob);
        }
        return result;
    } catch (...) {
        if (!options_.keep_temp) {
            std::error_code ec;
            fs::remove(temp_vob, ec);
        }
        throw;
    }
}

TitleManifest NativeDvdExtractor::pick_title(const std::vector<TitleManifest>& titles) const {
    if (titles.empty()) {
        throw HomebrewError("no VOB title found in VIDEO_TS");
    }

    if (options_.title > 0) {
        for (const auto& title : titles) {
            if (title.title == options_.title) {
                if (title.parts.empty()) {
                    throw HomebrewError("requested title has no VOB parts");
                }
                return title;
            }
        }
        throw HomebrewError("requested title not found: " + std::to_string(options_.title));
    }

    return titles.front();
}

fs::path NativeDvdExtractor::build_temp_path(int title) const {
    fs::path dir = options_.work_dir.empty() ? fs::temp_directory_path() : options_.work_dir;
    fs::create_directories(dir);

    const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
    const auto name = ".dvd_native_title_" + std::to_string(title) + "_" + std::to_string(getpid()) + "_" + std::to_string(stamp) + ".vob";
    return dir / name;
}

std::uint64_t NativeDvdExtractor::prepare_program_stream(const TitleManifest& title, const fs::path& temp_vob) const {
    if (title.parts.empty()) {
        throw HomebrewError("selected title has no source parts");
    }

    if (title.parts.size() == 1u) {
        CopyEngine engine;
        return engine.copy(temp_vob, title.parts.front());
    }

    ConcatEngine engine;
    return engine.concat(temp_vob, title.parts);
}

bool NativeDvdExtractor::transcode_to_mp4(const fs::path& input_vob) const {
    const ProcessRunner runner;
    const bool audio_modes[] = {true, false};
    const bool input_modes[] = {true, false};

    for (const bool with_audio : audio_modes) {
        for (const bool force_mpeg : input_modes) {
            std::error_code remove_ec;
            fs::remove(options_.output, remove_ec);

            const auto argv = build_ffmpeg_args(input_vob, with_audio, force_mpeg);
            std::cerr << "HOMEBREW_EXTRACT ffmpeg audio=" << (with_audio ? "on" : "off")
                      << " force_mpeg=" << (force_mpeg ? "on" : "off") << '\n';

            const int code = runner.run_inherited(argv);
            if (code == 0 && valid_output()) {
                return true;
            }

            std::cerr << "HOMEBREW_EXTRACT ffmpeg_failed code=" << code << '\n';
        }
    }
    return false;
}

std::vector<std::string> NativeDvdExtractor::build_ffmpeg_args(
    const fs::path& input_vob,
    bool with_audio,
    bool force_mpeg_input) const {
    std::vector<std::string> args = {
        options_.ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-analyzeduration",
        "60M",
        "-probesize",
        "60M",
        "-fflags",
        "+genpts",
        "-err_detect",
        "ignore_err",
        "-ignore_unknown",
    };

    if (force_mpeg_input) {
        args.push_back("-f");
        args.push_back("mpeg");
    }

    args.push_back("-i");
    args.push_back(input_vob.string());
    args.insert(args.end(), {
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-map",
        "0:v:0?",
        "-sn",
        "-dn",
    });

    if (with_audio) {
        args.insert(args.end(), {
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ac",
            "2",
            "-map",
            "0:a:0?",
        });
    } else {
        args.push_back("-an");
    }

    args.push_back(options_.output.string());
    return args;
}

bool NativeDvdExtractor::valid_output() const {
    return fs::exists(options_.output) && file_size_or_zero(options_.output) > 0u;
}

}  // namespace dvdextractor::homebrew
