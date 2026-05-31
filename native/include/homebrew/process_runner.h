#ifndef DVDEXTRACTOR_HOMEBREW_PROCESS_RUNNER_H_
#define DVDEXTRACTOR_HOMEBREW_PROCESS_RUNNER_H_

#include <string>
#include <vector>

namespace dvdextractor::homebrew {

// Lance un executable externe sans shell. Les arguments restent separes pour
// eviter les injections et conserver les logs stdout/stderr du parent.
class ProcessRunner final {
public:
    [[nodiscard]] int run_inherited(const std::vector<std::string>& argv) const;
};

}  // namespace dvdextractor::homebrew

#endif  // DVDEXTRACTOR_HOMEBREW_PROCESS_RUNNER_H_
