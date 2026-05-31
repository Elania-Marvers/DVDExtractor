#ifndef DVDEXTRACTOR_HOMEBREW_MODELS_H_
#define DVDEXTRACTOR_HOMEBREW_MODELS_H_

#include <cstdint>
#include <filesystem>
#include <vector>

namespace fs = std::filesystem;

namespace dvdextractor::homebrew {

struct TitleManifest {
    int title{0};
    std::vector<fs::path> parts;
    std::uint64_t total_bytes{0};
};

}  // namespace dvdextractor::homebrew

#endif  // DVDEXTRACTOR_HOMEBREW_MODELS_H_
