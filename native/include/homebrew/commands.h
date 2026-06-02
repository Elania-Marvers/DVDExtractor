#ifndef DVDEXTRACTOR_HOMEBREW_COMMANDS_H_
#define DVDEXTRACTOR_HOMEBREW_COMMANDS_H_

#include <cstdint>
#include <iosfwd>
#include <memory>
#include <string>
#include <vector>

#include <filesystem>

namespace fs = std::filesystem;

namespace dvdextractor::homebrew {

// Contrat commun des commandes homebrew (scan / copy / concat).
class HomebrewCommand {
public:
    virtual ~HomebrewCommand() = default;
    virtual int execute(std::ostream& out, std::ostream& err) const = 0;
};

class ScanCommand : public HomebrewCommand {
public:
    explicit ScanCommand(fs::path video_ts);

    int execute(std::ostream& out, std::ostream& err) const override;

private:
    fs::path video_ts_;
};

class PreflightCommand : public HomebrewCommand {
public:
    PreflightCommand(fs::path video_ts, int title);

    int execute(std::ostream& out, std::ostream& err) const override;

private:
    fs::path video_ts_;
    int title_{0};
};

class CopyCommand : public HomebrewCommand {
public:
    CopyCommand(fs::path source, fs::path output);

    int execute(std::ostream& out, std::ostream& err) const override;

private:
    fs::path source_;
    fs::path output_;
};

class ConcatCommand : public HomebrewCommand {
public:
    ConcatCommand(fs::path output, std::vector<fs::path> parts);

    int execute(std::ostream& out, std::ostream& err) const override;

private:
    fs::path output_;
    std::vector<fs::path> parts_;
};

class DemuxCommand : public HomebrewCommand {
public:
    DemuxCommand(fs::path input, fs::path output_dir, bool extract_payloads, std::uint64_t max_bytes);

    int execute(std::ostream& out, std::ostream& err) const override;

private:
    fs::path input_;
    fs::path output_dir_;
    bool extract_payloads_{true};
    std::uint64_t max_bytes_{0};
};

class ExtractCommand : public HomebrewCommand {
public:
    ExtractCommand(fs::path video_ts, fs::path output, int title, std::string ffmpeg, fs::path work_dir, bool keep_temp);

    int execute(std::ostream& out, std::ostream& err) const override;

private:
    fs::path video_ts_;
    fs::path output_;
    int title_{0};
    std::string ffmpeg_;
    fs::path work_dir_;
    bool keep_temp_{false};
};

}  // namespace dvdextractor::homebrew

#endif  // DVDEXTRACTOR_HOMEBREW_COMMANDS_H_
