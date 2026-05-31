#include "homebrew/progress_ticker.h"

#include <iostream>

namespace dvdextractor::homebrew {

ProgressTicker::ProgressTicker(const std::atomic<std::uint64_t>* value, const std::string& command, const fs::path& output)
    : value_(value), command_(command), output_(output) {
    worker_ = std::thread([this]() { this->run(); });
}

ProgressTicker::~ProgressTicker() {
    stop_.store(true, std::memory_order_release);
    if (worker_.joinable()) {
        worker_.join();
    }
}

void ProgressTicker::run() {
    using clock = std::chrono::steady_clock;
    const auto started = clock::now();
    while (!stop_.load(std::memory_order_acquire)) {
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
        if (stop_.load(std::memory_order_acquire)) {
            break;
        }

        const auto now = clock::now();
        const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(now - started).count();
        std::cerr << "HOMEBREW_PROGRESS cmd=" << command_ << " output=" << output_.string()
                  << " bytes=" << (value_ ? value_->load(std::memory_order_relaxed) : 0)
                  << " elapsed_ms=" << elapsed << '\n';
    }
}

}  // namespace dvdextractor::homebrew
