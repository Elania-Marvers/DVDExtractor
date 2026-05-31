#ifndef DVDEXTRACTOR_HOMEBREW_VOB_SCANNER_H_
#define DVDEXTRACTOR_HOMEBREW_VOB_SCANNER_H_

#include <filesystem>
#include <vector>

#include "homebrew/models.h"

namespace fs = std::filesystem;

namespace dvdextractor::homebrew {

class VobScanner {
public:
    static std::vector<TitleManifest> scan_video_ts(const fs::path& video_ts);
};

}  // namespace dvdextractor::homebrew

#endif  // DVDEXTRACTOR_HOMEBREW_VOB_SCANNER_H_
