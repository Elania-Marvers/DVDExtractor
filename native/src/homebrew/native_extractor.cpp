#include "homebrew/native_extractor.h"

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <sstream>
#include <utility>
#include <unistd.h>

#include "homebrew/errors.h"
#include "homebrew/ifo_audio.h"
#include "homebrew/program_stream_demuxer.h"
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

bool is_audio_stream_kind(const std::string& kind) {
    return kind == "ac3" || kind == "dts" || kind == "mpeg-audio" || kind == "lpcm";
}

bool native_es_transcode_enabled() {
    const char* value = std::getenv("DVD_EXTRACT_NATIVE_ES_TRANSCODE");
    return value != nullptr && std::string(value) == "1";
}

std::string mp4_language_code(const std::string& code) {
    if (code == "fr") {
        return "fra";
    }
    if (code == "en") {
        return "eng";
    }
    if (code == "de") {
        return "deu";
    }
    if (code == "es") {
        return "spa";
    }
    if (code == "it") {
        return "ita";
    }
    return code.empty() ? "und" : code;
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
    const auto demux_dir = build_demux_dir(title.title);

    Result result;
    result.title = title.title;
    result.temp_vob = temp_vob;
    result.output = options_.output;

    try {
        result.bytes_prepared = prepare_program_stream(title, temp_vob);
        if (result.bytes_prepared == 0u) {
            throw HomebrewError("native preparation produced empty VOB");
        }
        inspect_program_stream(temp_vob);

        if (!demux_then_transcode_to_mp4(temp_vob, demux_dir, title.title) && !transcode_to_mp4(temp_vob, title.title)) {
            throw HomebrewError("all mp4 backends failed after native preparation");
        }
        if (!valid_output()) {
            throw HomebrewError("mp4 output is missing or empty: " + options_.output.string());
        }

        if (!options_.keep_temp) {
            fs::remove(temp_vob);
            std::error_code ec;
            fs::remove_all(demux_dir, ec);
        }
        return result;
    } catch (...) {
        if (!options_.keep_temp) {
            std::error_code ec;
            fs::remove(temp_vob, ec);
            fs::remove_all(demux_dir, ec);
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

fs::path NativeDvdExtractor::build_demux_dir(int title) const {
    fs::path dir = options_.work_dir.empty() ? fs::temp_directory_path() : options_.work_dir;
    fs::create_directories(dir);

    const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
    const auto name = ".dvd_native_demux_" + std::to_string(title) + "_" + std::to_string(getpid()) + "_" + std::to_string(stamp);
    return dir / name;
}

std::vector<SegmentProbeReport> NativeDvdExtractor::preflight_title(const TitleManifest& title) const {
    SegmentPreflight preflight;
    auto report = preflight.scan(title.parts);
    preflight.assert_usable(report);

    for (const auto& item : report) {
        std::cerr << "HOMEBREW_PREFLIGHT"
                  << " part=" << item.path.filename().string()
                  << " size=" << item.file_size
                  << " sample=" << item.stats.bytes
                  << " pack_sync=" << item.stats.pack_sync_count
                  << " sequence=" << item.stats.sequence_header_count
                  << " nav=" << item.stats.nav_pack_count
                  << " zero_run=" << item.stats.max_zero_run
                  << " likely_ps=" << (item.likely_program_stream() ? "yes" : "no")
                  << '\n';
    }

    return report;
}

std::uint64_t NativeDvdExtractor::prepare_program_stream(const TitleManifest& title, const fs::path& temp_vob) const {
    if (title.parts.empty()) {
        throw HomebrewError("selected title has no source parts");
    }

    (void)preflight_title(title);

    if (title.parts.size() == 1u) {
        CopyEngine engine;
        return engine.copy(temp_vob, title.parts.front());
    }

    ConcatEngine engine;
    return engine.concat(temp_vob, title.parts);
}

void NativeDvdExtractor::inspect_program_stream(const fs::path& input_vob) const {
    ProgramStreamDemuxer demuxer(ProgramStreamDemuxer::Options{
        input_vob,
        {},
        false,
        64u * 1024u * 1024u,
    });

    const auto summary = demuxer.inspect();
    std::cerr << "HOMEBREW_DEMUX"
              << " input=" << input_vob.filename().string()
              << " bytes=" << summary.input_bytes
              << " pes=" << summary.pes_packets
              << " video=" << summary.video_packets
              << " audio=" << summary.audio_packets
              << " private=" << summary.private_packets
              << " streams=" << summary.streams.size()
              << '\n';

    for (const auto& stream : summary.streams) {
        std::cerr << "HOMEBREW_STREAM"
                  << " kind=" << stream.kind
                  << " id=0x" << std::hex << static_cast<int>(stream.stream_id) << std::dec;
        if (stream.has_substream) {
            std::cerr << " sub=0x" << std::hex << static_cast<int>(stream.substream_id) << std::dec;
        }
        std::cerr << " packets=" << stream.packets
                  << " bytes=" << stream.payload_bytes
                  << '\n';
    }
}

bool NativeDvdExtractor::demux_then_transcode_to_mp4(const fs::path& input_vob, const fs::path& demux_dir, int title) const {
    try {
        ProgramStreamDemuxer demuxer(ProgramStreamDemuxer::Options{
            input_vob,
            demux_dir,
            true,
            0,
        });

        const auto summary = demuxer.run();
        std::cerr << "HOMEBREW_DEMUX_FULL"
                  << " dir=" << demux_dir.string()
                  << " streams=" << summary.streams.size()
                  << " pes=" << summary.pes_packets
                  << " video=" << summary.video_packets
                  << " audio=" << summary.audio_packets
                  << '\n';

        std::error_code remove_ec;
        fs::remove(options_.output, remove_ec);

        if (!native_es_transcode_enabled()) {
            std::cerr << "HOMEBREW_DEMUX_FULL elementary_transcode=skipped"
                      << " reason=requires_DVD_EXTRACT_NATIVE_ES_TRANSCODE_1"
                      << '\n';
            return false;
        }

        const ProcessRunner runner;
        const auto argv = build_ffmpeg_demux_args(summary, title);
        if (argv.empty()) {
            std::cerr << "HOMEBREW_DEMUX_FULL no usable elementary streams" << '\n';
            return false;
        }

        const int code = runner.run_inherited(argv);
        if (code == 0 && valid_output()) {
            return true;
        }

        std::cerr << "HOMEBREW_DEMUX_FULL ffmpeg_failed code=" << code << '\n';
        return false;
    } catch (const std::exception& exc) {
        std::cerr << "HOMEBREW_DEMUX_FULL failed=" << exc.what() << '\n';
        return false;
    }
}

bool NativeDvdExtractor::transcode_to_mp4(const fs::path& input_vob, int title) const {
    const ProcessRunner runner;
    const bool audio_modes[] = {true, false};
    const bool input_modes[] = {true, false};

    for (const bool with_audio : audio_modes) {
        for (const bool force_mpeg : input_modes) {
            std::error_code remove_ec;
            fs::remove(options_.output, remove_ec);

            const auto argv = build_ffmpeg_args(input_vob, title, with_audio, force_mpeg);
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

std::vector<std::string> NativeDvdExtractor::build_ffmpeg_demux_args(const DemuxSummary& summary, int title) const {
    fs::path video;
    struct AudioInput {
        fs::path path;
        std::uint8_t substream_id{0};
        std::string language;
    };
    std::vector<AudioInput> audio;

    std::vector<IfoAudioStream> ifo_audio;
    try {
        ifo_audio = IfoAudioReader::read_title_audio(options_.video_ts, title);
    } catch (const std::exception& exc) {
        std::cerr << "HOMEBREW_IFO audio_probe_failed=" << exc.what() << '\n';
    }

    std::vector<std::uint8_t> preferred_substreams;
    for (const auto& item : ifo_audio) {
        if (item.language == options_.preferred_audio_language) {
            preferred_substreams.push_back(item.substream_id);
            std::cerr << "HOMEBREW_IFO preferred_audio"
                      << " lang=" << item.language
                      << " sub=0x" << std::hex << static_cast<int>(item.substream_id) << std::dec
                      << " format=" << item.format
                      << " channels=" << item.channels
                      << '\n';
        }
    }

    const auto language_for_substream = [&ifo_audio](std::uint8_t substream_id) -> std::string {
        for (const auto& item : ifo_audio) {
            if (item.substream_id == substream_id) {
                return item.language;
            }
        }
        return "";
    };

    const auto is_preferred = [&preferred_substreams](std::uint8_t substream_id) {
        if (preferred_substreams.empty()) {
            return true;
        }
        for (const auto item : preferred_substreams) {
            if (item == substream_id) {
                return true;
            }
        }
        return false;
    };

    for (const auto& stream : summary.streams) {
        if (stream.output_path.empty() || !fs::exists(stream.output_path)) {
            continue;
        }
        if (stream.kind == "video" && video.empty()) {
            video = stream.output_path;
        } else if (is_audio_stream_kind(stream.kind) && is_preferred(stream.substream_id)) {
            std::cerr << "HOMEBREW_AUDIO_SELECT"
                      << " backend=elementary"
                      << " kind=" << stream.kind
                      << " sub=0x" << std::hex << static_cast<int>(stream.substream_id) << std::dec
                      << " lang=" << language_for_substream(stream.substream_id)
                      << '\n';
            audio.push_back(AudioInput{
                stream.output_path,
                stream.substream_id,
                language_for_substream(stream.substream_id),
            });
        }
    }

    if (video.empty()) {
        return {};
    }

    std::vector<std::string> args = {
        options_.ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-fflags",
        "+genpts",
        "-f",
        "mpegvideo",
        "-i",
        video.string(),
    };

    for (const auto& item : audio) {
        args.push_back("-i");
        args.push_back(item.path.string());
    }

    args.insert(args.end(), {
        "-map",
        "0:v:0",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-pix_fmt",
        "yuv420p",
    });

    if (audio.empty()) {
        args.push_back("-an");
    } else {
        for (std::size_t index = 0; index < audio.size(); ++index) {
            args.push_back("-map");
            args.push_back(std::to_string(index + 1u) + ":a:0?");
        }
        args.insert(args.end(), {
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ac",
            "2",
            "-shortest",
        });
        for (std::size_t index = 0; index < audio.size(); ++index) {
            args.push_back("-metadata:s:a:" + std::to_string(index));
            args.push_back("language=" + mp4_language_code(audio[index].language));
        }
        args.insert(args.end(), {
            "-disposition:a:0",
            "default",
        });
    }

    args.insert(args.end(), {
        "-movflags",
        "+faststart",
        "-sn",
        "-dn",
        options_.output.string(),
    });
    return args;
}

std::vector<std::string> NativeDvdExtractor::build_ffmpeg_args(
    const fs::path& input_vob,
    int title,
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
        std::vector<IfoAudioStream> ifo_audio;
        try {
            ifo_audio = IfoAudioReader::read_title_audio(options_.video_ts, title);
        } catch (const std::exception& exc) {
            std::cerr << "HOMEBREW_IFO fallback_audio_probe_failed=" << exc.what() << '\n';
        }
        std::vector<unsigned int> preferred_audio_indexes;
        for (const auto& item : ifo_audio) {
            if (item.language == options_.preferred_audio_language && item.substream_id >= 0x80u) {
                preferred_audio_indexes.push_back(static_cast<unsigned int>(item.substream_id - 0x80u));
            }
        }

        args.insert(args.end(), {
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ac",
            "2",
        });
        if (preferred_audio_indexes.empty()) {
            args.insert(args.end(), {
                "-map",
                "0:a?",
            });
        } else {
            for (const auto index : preferred_audio_indexes) {
                std::cerr << "HOMEBREW_AUDIO_SELECT"
                          << " backend=vob"
                          << " index=" << index
                          << " lang=" << options_.preferred_audio_language
                          << '\n';
                args.push_back("-map");
                args.push_back("0:a:" + std::to_string(index) + "?");
            }
            for (std::size_t index = 0; index < preferred_audio_indexes.size(); ++index) {
                args.push_back("-metadata:s:a:" + std::to_string(index));
                args.push_back("language=" + mp4_language_code(options_.preferred_audio_language));
            }
        }
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
