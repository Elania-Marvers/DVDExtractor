#include "homebrew/process_runner.h"

#include <cerrno>
#include <cstring>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>

#include "homebrew/errors.h"

extern char** environ;

namespace dvdextractor::homebrew {

int ProcessRunner::run_inherited(const std::vector<std::string>& argv) const {
    if (argv.empty() || argv.front().empty()) {
        throw HomebrewError("empty process command");
    }

    std::vector<char*> raw;
    raw.reserve(argv.size() + 1u);
    for (const auto& item : argv) {
        raw.push_back(const_cast<char*>(item.c_str()));
    }
    raw.push_back(nullptr);

    pid_t pid = 0;
    const int spawn_status = posix_spawnp(&pid, raw.front(), nullptr, nullptr, raw.data(), environ);
    if (spawn_status != 0) {
        throw HomebrewError("cannot spawn process " + argv.front() + ": " + std::strerror(spawn_status));
    }

    int status = 0;
    while (waitpid(pid, &status, 0) < 0) {
        if (errno == EINTR) {
            continue;
        }
        throw HomebrewError("waitpid failed for " + argv.front() + ": " + std::strerror(errno));
    }

    if (WIFEXITED(status)) {
        return WEXITSTATUS(status);
    }
    if (WIFSIGNALED(status)) {
        return 128 + WTERMSIG(status);
    }
    return 1;
}

}  // namespace dvdextractor::homebrew
