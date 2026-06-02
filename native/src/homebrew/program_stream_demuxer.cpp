#include "homebrew/program_stream_demuxer.h"

#include <sstream>
#include <utility>

#include "homebrew/errors.h"

namespace dvdextractor::homebrew {

namespace {

DemuxSummary map_report(
    const fs::path& input,
    const fs::path& output_dir,
    const dvd_ps_demux_report& report) {
    DemuxSummary summary;
    summary.input = input;
    summary.output_dir = output_dir;
    summary.input_bytes = report.input_bytes;
    summary.consumed_bytes = report.consumed_bytes;
    summary.pack_headers = report.pack_headers;
    summary.system_headers = report.system_headers;
    summary.pes_packets = report.pes_packets;
    summary.video_packets = report.video_packets;
    summary.audio_packets = report.audio_packets;
    summary.private_packets = report.private_packets;
    summary.skipped_packets = report.skipped_packets;
    summary.truncated_packets = report.truncated_packets;

    summary.streams.reserve(report.stream_count);
    for (std::size_t i = 0; i < report.stream_count; ++i) {
        const auto& item = report.streams[i];
        DemuxStreamSummary stream;
        stream.stream_id = item.stream_id;
        stream.substream_id = item.substream_id;
        stream.has_substream = item.has_substream != 0u;
        stream.kind = item.kind;
        stream.packets = item.packets;
        stream.payload_bytes = item.payload_bytes;
        if (item.output_path[0] != '\0') {
            stream.output_path = item.output_path;
        }
        summary.streams.push_back(std::move(stream));
    }

    return summary;
}

}  // namespace

ProgramStreamDemuxer::ProgramStreamDemuxer(Options options)
    : options_(std::move(options)) {}

DemuxSummary ProgramStreamDemuxer::run() const {
    return execute(options_.extract_payloads);
}

DemuxSummary ProgramStreamDemuxer::inspect() const {
    return execute(false);
}

DemuxSummary ProgramStreamDemuxer::execute(bool extract_payloads) const {
    if (options_.input.empty()) {
        throw HomebrewError("missing MPEG-PS input path");
    }
    if (!fs::is_regular_file(options_.input)) {
        throw HomebrewError("invalid MPEG-PS input path: " + options_.input.string());
    }

    fs::path output_dir = options_.output_dir;
    if (extract_payloads) {
        if (output_dir.empty()) {
            throw HomebrewError("missing demux output directory");
        }
        fs::create_directories(output_dir);
        if (!fs::is_directory(output_dir)) {
            throw HomebrewError("invalid demux output directory: " + output_dir.string());
        }
    }

    dvd_ps_demux_options c_options{};
    const std::string input_text = options_.input.string();
    const std::string output_text = output_dir.string();
    c_options.input_path = input_text.c_str();
    c_options.output_dir = extract_payloads ? output_text.c_str() : nullptr;
    c_options.max_bytes = options_.max_bytes;
    c_options.extract_payloads = extract_payloads ? 1u : 0u;

    dvd_ps_demux_report report{};
    const int rc = dvd_ps_demux_file(&c_options, &report);
    if (rc != 0) {
        std::ostringstream message;
        message << "program stream demux failed";
        if (report.error[0] != '\0') {
            message << ": " << report.error;
        }
        throw HomebrewError(message.str());
    }

    return map_report(options_.input, output_dir, report);
}

}  // namespace dvdextractor::homebrew
