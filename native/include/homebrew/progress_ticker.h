#ifndef DVDEXTRACTOR_HOMEBREW_PROGRESS_TICKER_H_
#define DVDEXTRACTOR_HOMEBREW_PROGRESS_TICKER_H_

#include <atomic>
#include <chrono>
#include <filesystem>
#include <string>
#include <thread>

namespace fs = std::filesystem;

namespace dvdextractor::homebrew {

class ProgressTicker {
public:
    ProgressTicker(const std::atomic<std::uint64_t>* value, const std::string& command, const fs::path& output);
    ~ProgressTicker();

    ProgressTicker(const ProgressTicker&) = delete;
    ProgressTicker& operator=(const ProgressTicker&) = delete;

private:
    void run();

    const std::atomic<std::uint64_t>* value_{nullptr};
    std::string command_;
    fs::path output_;
    std::thread worker_;
    std::atomic<bool> stop_{false};
};

}  // namespace dvdextractor::homebrew

#endif  // DVDEXTRACTOR_HOMEBREW_PROGRESS_TICKER_H_
