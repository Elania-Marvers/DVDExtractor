#include "homebrew/commands.h"

#include <algorithm>
#include <chrono>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <utility>

#include "common/asm_fmt.h"
#include "common/json_escape.h"
#include "common/perf.h"
#include "homebrew/native_extractor.h"
#include "homebrew/transfer_engines.h"
#include "homebrew/vob_scanner.h"
#include "homebrew/errors.h"

namespace dvdextractor::homebrew {

namespace {

std::string format_elapsed_ms(const std::chrono::steady_clock::time_point& begin, const std::chrono::steady_clock::time_point& end) {
    const auto elapsed_ms = std::chrono::duration<double, std::milli>(end - begin).count();
    std::ostringstream stream;
    stream << std::fixed << std::setprecision(2) << elapsed_ms;
    return stream.str();
}

std::string build_scan_json(const fs::path& video_ts, const std::vector<TitleManifest>& titles) {
    std::ostringstream out;
    out << '{' << "\"video_ts\":" << '"' << common::json_escape(video_ts.string()) << "\",";
    out << "\"titles\":" << "[";

    for (std::size_t i = 0; i < titles.size(); ++i) {
        const auto& title = titles[i];
        if (i > 0) {
            out << ',';
        }

        out << '{';
        out << "\"id\":" << title.title << ',';
        out << "\"size\":" << common::u64_to_decimal(title.total_bytes) << ',';
        out << "\"parts\":" << '[';

        for (std::size_t p = 0; p < title.parts.size(); ++p) {
            if (p > 0) {
                out << ',';
            }
            out << '"' << common::json_escape(title.parts[p].string()) << '"';
        }
        out << "]}";
    }

    out << "]}";
    return out.str();
}

std::string build_copy_result_json(const fs::path& source, const fs::path& output, std::uint64_t bytes, const std::string& elapsed_ms) {
    std::ostringstream out;
    out << '{';
    out << "\"source\":" << '"' << common::json_escape(source.string()) << "\",";
    out << "\"output\":" << '"' << common::json_escape(output.string()) << "\",";
    out << "\"bytes\":" << common::u64_to_decimal(bytes) << ',';
    out << "\"elapsed_ms\":" << elapsed_ms;
    out << "}";
    return out.str();
}

}  // namespace

ScanCommand::ScanCommand(fs::path video_ts)
    : video_ts_(std::move(video_ts)) {}

int ScanCommand::execute(std::ostream& out, std::ostream&) const {
    const auto titles = VobScanner::scan_video_ts(video_ts_);
    out << build_scan_json(video_ts_, titles) << '\n';
    return 0;
}

CopyCommand::CopyCommand(fs::path source, fs::path output)
    : source_(std::move(source)), output_(std::move(output)) {}

int CopyCommand::execute(std::ostream& out, std::ostream& err) const {
    CopyEngine engine;
    const auto start = std::chrono::steady_clock::now();
    const auto bytes = engine.copy(output_, source_);
    const auto stop = std::chrono::steady_clock::now();

    out << build_copy_result_json(source_, output_, bytes, format_elapsed_ms(start, stop)) << '\n';
    err << "HOMEBREW_DONE command=copy output=" << output_.string() << " bytes=" << bytes << '\n';
    return 0;
}

ConcatCommand::ConcatCommand(fs::path output, std::vector<fs::path> parts)
    : output_(std::move(output)), parts_(std::move(parts)) {}

int ConcatCommand::execute(std::ostream& out, std::ostream& err) const {
    ConcatEngine engine;
    const auto start = std::chrono::steady_clock::now();
    const auto bytes = engine.concat(output_, parts_);
    const auto stop = std::chrono::steady_clock::now();
    const auto elapsed_ms = format_elapsed_ms(start, stop);

    const std::size_t parts_count = parts_.size();

    out << '{';
    out << "\"output\":" << '"' << common::json_escape(output_.string()) << "\",";
    out << "\"parts\":" << parts_count << ',';
    out << "\"bytes\":" << common::u64_to_decimal(bytes) << ',';
    out << "\"elapsed_ms\":" << elapsed_ms << "}\n";

    err << "HOMEBREW_DONE command=concat output=" << output_.string() << " bytes=" << bytes << '\n';
    return 0;
}

ExtractCommand::ExtractCommand(fs::path video_ts, fs::path output, int title, std::string ffmpeg, fs::path work_dir, bool keep_temp)
    : video_ts_(std::move(video_ts))
    , output_(std::move(output))
    , title_(title)
    , ffmpeg_(std::move(ffmpeg))
    , work_dir_(std::move(work_dir))
    , keep_temp_(keep_temp) {}

int ExtractCommand::execute(std::ostream& out, std::ostream& err) const {
    NativeDvdExtractor extractor(NativeDvdExtractor::Options{
        video_ts_,
        output_,
        work_dir_,
        ffmpeg_.empty() ? "ffmpeg" : ffmpeg_,
        title_,
        keep_temp_,
    });

    const auto result = extractor.extract();
    out << '{';
    out << "\"status\":\"ok\",";
    out << "\"title\":" << result.title << ',';
    out << "\"prepared_bytes\":" << common::u64_to_decimal(result.bytes_prepared) << ',';
    out << "\"output\":" << '"' << common::json_escape(result.output.string()) << "\"";
    if (keep_temp_) {
        out << ",\"temp_vob\":" << '"' << common::json_escape(result.temp_vob.string()) << "\"";
    }
    out << "}\n";

    err << "HOMEBREW_DONE command=extract output=" << result.output.string()
        << " bytes=" << result.bytes_prepared << '\n';
    return 0;
}

}  // namespace dvdextractor::homebrew
