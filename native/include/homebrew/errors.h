#ifndef DVDEXTRACTOR_HOMEBREW_ERRORS_H_
#define DVDEXTRACTOR_HOMEBREW_ERRORS_H_

#include <stdexcept>
#include <string>

namespace dvdextractor::homebrew {

class HomebrewError final : public std::runtime_error {
public:
    explicit HomebrewError(const std::string& message)
        : std::runtime_error(message) {}
};

}  // namespace dvdextractor::homebrew

#endif  // DVDEXTRACTOR_HOMEBREW_ERRORS_H_
