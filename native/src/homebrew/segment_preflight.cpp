#include "homebrew/segment_preflight.h"

#include <algorithm>
#include <cassert>
#include <deque>
#include <fstream>
#include <future>
#include <thread>
#include <utility>

#include "homebrew/errors.h"

namespace dvdextractor::homebrew {

namespace {

std::size_t default_workers(std::size_t requested) {
    if (requested > 0u) {
        return requested;
    }

    const auto hw = std::thread::hardware_concurrency();
    if (hw == 0u) {
        return 2u;
    }
    return std::max<std::size_t>(2u, std::min<std::size_t>(static_cast<std::size_t>(hw), 8u));
}

}  // namespace

SegmentPreflight::SegmentPreflight()
    : SegmentPreflight(Options{}) {}

SegmentPreflight::SegmentPreflight(Options options)
    : options_(options) {
    if (options_.sample_bytes == 0u) {
        options_.sample_bytes = 2u * 1024u * 1024u;
    }
    options_.max_workers = default_workers(options_.max_workers);
}

std::vector<SegmentProbeReport> SegmentPreflight::scan(const std::vector<fs::path>& parts) const {
    if (parts.empty()) {
        return {};
    }

    assert(options_.max_workers > 0u);
    std::vector<SegmentProbeReport> reports;
    reports.reserve(parts.size());

    std::deque<std::future<SegmentProbeReport>> futures;
    const auto collect_one = [&reports, &futures]() {
        reports.push_back(futures.front().get());
        futures.pop_front();
    };

    for (const auto& part : parts) {
        futures.emplace_back(std::async(std::launch::async, [this, part]() {
            return scan_one(part);
        }));

        if (futures.size() >= options_.max_workers) {
            collect_one();
        }
    }

    while (!futures.empty()) {
        collect_one();
    }

    std::sort(reports.begin(), reports.end(), [](const SegmentProbeReport& lhs, const SegmentProbeReport& rhs) {
        return lhs.path.filename().string() < rhs.path.filename().string();
    });
    return reports;
}

void SegmentPreflight::assert_usable(const std::vector<SegmentProbeReport>& report) const {
    if (report.empty()) {
        throw HomebrewError("preflight failed: no VOB segment to inspect");
    }

    std::uint64_t total_size = 0;
    bool any_program_stream = false;

    for (const auto& item : report) {
        if (!item.readable) {
            throw HomebrewError("preflight failed for " + item.path.string() + ": " + item.error);
        }

        total_size += item.file_size;
        any_program_stream = any_program_stream || item.likely_program_stream();
    }

    if (total_size == 0u) {
        throw HomebrewError("preflight failed: all VOB segments are empty");
    }

    if (!any_program_stream) {
        throw HomebrewError("preflight failed: no MPEG program-stream signature found in VOB samples");
    }
}

SegmentProbeReport SegmentPreflight::scan_one(const fs::path& part) const {
    SegmentProbeReport report;
    report.path = part;

    std::error_code ec;
    if (!fs::exists(part, ec) || !fs::is_regular_file(part, ec)) {
        report.error = "source is not a regular file";
        return report;
    }

    const auto size = fs::file_size(part, ec);
    if (ec) {
        report.error = "cannot stat source";
        return report;
    }
    report.file_size = static_cast<std::uint64_t>(size);
    if (size == 0u) {
        report.error = "source is empty";
        return report;
    }

    const auto bytes_to_read = std::min<std::uint64_t>(
        static_cast<std::uint64_t>(options_.sample_bytes),
        static_cast<std::uint64_t>(size));

    std::vector<std::uint8_t> sample(static_cast<std::size_t>(bytes_to_read));
    std::ifstream input(part, std::ios::binary);
    if (!input) {
        report.error = "cannot open source";
        return report;
    }

    input.read(reinterpret_cast<char*>(sample.data()), static_cast<std::streamsize>(sample.size()));
    const auto read_count = input.gcount();
    if (read_count <= 0) {
        report.error = "cannot read sample";
        return report;
    }

    dvd_mpeg_probe_buffer(
        sample.data(),
        static_cast<std::size_t>(read_count),
        &report.stats);

    report.readable = true;
    return report;
}

}  // namespace dvdextractor::homebrew
