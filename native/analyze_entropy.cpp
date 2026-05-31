#include <array>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <fcntl.h>
#include <string>
#include <unistd.h>
#include <vector>

extern "C" size_t fast_byte_sum(const uint8_t* data, size_t len);

static double entropy_ratio(const std::vector<uint8_t>& data) {
    if (data.empty()) {
        return 0.0;
    }
    std::array<size_t, 256> histogram{};
    for (auto b : data) {
        histogram[b]++;
    }
    const double total = static_cast<double>(data.size());
    double entropy = 0.0;
    for (auto n : histogram) {
        if (n == 0) {
            continue;
        }
        const double p = n / total;
        entropy -= p * std::log2(p);
    }
    return entropy;
}

int main(int argc, char** argv) {
    if (argc < 2) {
        std::cerr << "usage: dvd_entropy <device_or_file> [sample_bytes]\n";
        return 2;
    }

    const std::string path = argv[1];
    size_t limit = 4 * 1024 * 1024;
    if (argc >= 3) {
        try {
            limit = static_cast<size_t>(std::stoull(argv[2]));
        } catch (...) {
            limit = 4 * 1024 * 1024;
        }
    }

    int fd = ::open(path.c_str(), O_RDONLY);
    if (fd < 0) {
        std::cerr << "Cannot open input\n";
        return 3;
    }

    std::vector<uint8_t> buffer;
    std::vector<uint8_t> chunk(64 * 1024);
    buffer.reserve(limit);

    while (buffer.size() < limit) {
        size_t to_read = std::min(chunk.size(), limit - buffer.size());
        auto n = ::read(fd, chunk.data(), to_read);
        if (n <= 0) {
            break;
        }
        buffer.insert(buffer.end(), chunk.begin(), chunk.begin() + n);
    }
    ::close(fd);

    if (buffer.empty()) {
        std::cerr << "No data\n";
        return 4;
    }

    const double ent = entropy_ratio(buffer);
    const size_t byte_sum = fast_byte_sum(buffer.data(), buffer.size());

    std::cout << '{';
    std::cout << "\"path\":" << '"' << path << "\",";
    std::cout << "\"bytes\":" << buffer.size() << ',';
    std::cout << "\"entropy\":" << std::fixed << std::setprecision(6) << ent << ',';
    std::cout << "\"byte_sum\":" << byte_sum;
    std::cout << "}\n";
    return 0;
}
