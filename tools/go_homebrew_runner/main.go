package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

const (
	defaultWorkDir = "build_go_homebrew"
)

type TitleManifest struct {
	ID    int      `json:"id"`
	Size  uint64   `json:"size"`
	Parts []string `json:"parts"`
}

type ScanResult struct {
	VideoTS string          `json:"video_ts"`
	Titles  []TitleManifest `json:"titles"`
}

type Runner struct {
	Homebrew string
	Ffmpeg   string
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, usage())
		os.Exit(1)
	}

	r := &Runner{
		Homebrew: resolveHomebrewPath(),
		Ffmpeg:   "ffmpeg",
	}

	switch os.Args[1] {
	case "scan":
		runScan(r, os.Args[2:])
	case "extract":
		runExtract(r, os.Args[2:])
	default:
		fmt.Fprintf(os.Stderr, "unknown command: %s\n\n%s\n", os.Args[1], usage())
		os.Exit(1)
	}
}

func usage() string {
	lines := []string{
		"Usage:",
		"  dvd-homebrew-runner scan --video-ts /path/VIDEO_TS",
		"  dvd-homebrew-runner extract --video-ts /path/VIDEO_TS --output /tmp/result.mp4 [--title N]",
		"  Optional flags: --ffmpeg, --homebrew, --timeout, --work-dir",
	}
	return strings.Join(lines, "\n")
}

func resolveHomebrewPath() string {
	if value := os.Getenv("HOMEBREW_TOOL"); value != "" {
		return value
	}

	execPath, err := os.Executable()
	if err == nil {
		execDir := filepath.Dir(execPath)
		candidates := []string{
			filepath.Clean(filepath.Join(execDir, "..", "native", "build", "dvd_homebrew")),
			filepath.Clean(filepath.Join(execDir, "native", "build", "dvd_homebrew")),
		}
		for _, candidate := range candidates {
			if _, statErr := os.Stat(candidate); statErr == nil {
				return candidate
			}
		}
	}

	if candidate, err := filepath.Abs(filepath.Join("native", "build", "dvd_homebrew")); err == nil {
		if _, statErr := os.Stat(candidate); statErr == nil {
			return candidate
		}
	}

	return filepath.Join("native", "build", "dvd_homebrew")
}

func runScan(r *Runner, args []string) {
	fs := flag.NewFlagSet("scan", flag.ContinueOnError)
	videoTS := fs.String("video-ts", "", "Path to VIDEO_TS directory")
	homebrew := fs.String("homebrew", r.Homebrew, "Path to dvd_homebrew binary")
	_ = fs.Parse(args)

	if *videoTS == "" {
		fatal("missing --video-ts")
	}

	r.Homebrew = *homebrew
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()

	output, errCode, err := runCommand(ctx, r.Homebrew, "scan", *videoTS)
	if err != nil {
		fatalf("scan failed (code=%d): %v\nstderr=%s", errCode, err, output.stderr)
	}
	if errCode != 0 {
		fatalf("scan returned non-zero code %d", errCode)
	}

	fmt.Print(output.stdout)
}

func runExtract(r *Runner, args []string) {
	fs := flag.NewFlagSet("extract", flag.ContinueOnError)
	videoTS := fs.String("video-ts", "", "Path to VIDEO_TS directory")
	output := fs.String("output", "", "Output mp4 file")
	titleID := fs.Int("title", 0, "Optional title id")
	ffmpeg := fs.String("ffmpeg", r.Ffmpeg, "ffmpeg binary")
	homebrew := fs.String("homebrew", r.Homebrew, "Path to dvd_homebrew binary")
	timeout := fs.Int("timeout", 1200, "Timeout in seconds for each external command")
	workDir := fs.String("work-dir", defaultWorkDir, "Temporary work directory")
	_ = fs.Parse(args)

	if *videoTS == "" {
		fatal("missing --video-ts")
	}
	if *output == "" {
		fatal("missing --output")
	}

	r.Ffmpeg = *ffmpeg
	r.Homebrew = *homebrew

	absOutput, err := filepath.Abs(*output)
	if err != nil {
		fatalf("invalid output path: %v", err)
	}

	title, parts, err := pickTitle(*videoTS, *titleID, r)
	if err != nil {
		fatal(err.Error())
	}

	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(*timeout)*time.Second)
	defer cancel()

	if err := os.MkdirAll(*workDir, 0o755); err != nil {
		fatalf("cannot create work dir %s: %v", *workDir, err)
	}
	if err := os.MkdirAll(filepath.Dir(absOutput), 0o755); err != nil {
		fatalf("cannot create output directory %s: %v", filepath.Dir(absOutput), err)
	}

	tmpVob := filepath.Join(*workDir, fmt.Sprintf("homebrew_title_%02d_%d.vob", title, time.Now().UnixNano()))
	if err := prepareVob(ctx, r, parts, tmpVob); err != nil {
		fatal(err.Error())
	}
	defer os.Remove(tmpVob)

	fmt.Printf("preprocess=ok title=%d temp=%s\n", title, tmpVob)

	err = transcodeToMp4(ctx, r.Ffmpeg, tmpVob, absOutput, true)
	if err != nil {
		fmt.Fprintf(os.Stderr, "audio transcode failed, fallback sans audio: %v\n", err)
		if fallbackErr := transcodeToMp4(ctx, r.Ffmpeg, tmpVob, absOutput, false); fallbackErr != nil {
			fatalf("ffmpeg fallback failed: %v", fallbackErr)
		}
	}

	fmt.Printf(`{"status":"ok","title":%d,"output":%q}\n`, title, absOutput)
}

func pickTitle(videoTS string, requested int, r *Runner) (int, []string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()

	cmdRes, exitCode, err := runCommand(ctx, r.Homebrew, "scan", videoTS)
	if err != nil {
		return 0, nil, fmt.Errorf("scan command failed (code=%d): %w", exitCode, err)
	}
	if exitCode != 0 {
		return 0, nil, fmt.Errorf("scan failed with code %d", exitCode)
	}

	var payload ScanResult
	if err := json.Unmarshal([]byte(cmdRes.stdout), &payload); err != nil {
		return 0, nil, fmt.Errorf("invalid scan JSON: %w", err)
	}

	if len(payload.Titles) == 0 {
		return 0, nil, errors.New("no title found in VIDEO_TS")
	}

	if requested > 0 {
		for _, title := range payload.Titles {
			if title.ID == requested {
				if len(title.Parts) == 0 {
					return 0, nil, fmt.Errorf("title %d has no VOB parts", requested)
				}
				return title.ID, title.Parts, nil
			}
		}
		return 0, nil, fmt.Errorf("requested title %d not found", requested)
	}

	sort.Slice(payload.Titles, func(i, j int) bool {
		return payload.Titles[i].Size > payload.Titles[j].Size
	})
	best := payload.Titles[0]
	if len(best.Parts) == 0 {
		return 0, nil, errors.New("first title in scan has no VOB parts")
	}
	return best.ID, best.Parts, nil
}

func prepareVob(ctx context.Context, r *Runner, parts []string, output string) error {
	if len(parts) == 0 {
		return errors.New("empty input parts")
	}

	var cmdArgs []string
	if len(parts) == 1 {
		cmdArgs = []string{"copy", "--source", parts[0], "--output", output}
	} else {
		cmdArgs = []string{"concat", "--output", output}
		cmdArgs = append(cmdArgs, parts...)
	}

	cmdRes, exitCode, err := runCommand(ctx, r.Homebrew, cmdArgs...)
	if err != nil {
		return fmt.Errorf("homebrew failed (code=%d): %w", exitCode, err)
	}
	if exitCode != 0 {
		return fmt.Errorf("homebrew returned code %d", exitCode)
	}

	if stderr := strings.TrimSpace(cmdRes.stderr); stderr != "" {
		fmt.Fprintln(os.Stderr, stderr)
	}
	if err := verifyFileNonZero(output); err != nil {
		return err
	}
	return nil
}

func transcodeToMp4(ctx context.Context, ffmpeg string, input, output string, withAudio bool) error {
	attempts := []struct {
		withAudio  bool
		forceInput bool
		label      string
	}{
		{withAudio: withAudio, forceInput: true, label: "strict-mpeg"},
		{withAudio: withAudio, forceInput: false, label: "autodetect"},
	}

	var lastErr error
	for _, attempt := range attempts {
		err := runFfmpegAttempt(
			ctx,
			ffmpeg,
			input,
			output,
			attempt.withAudio,
			attempt.forceInput,
			attempt.label,
		)
		if err == nil {
			return nil
		}
		lastErr = err
	}
	return lastErr
}

func runFfmpegAttempt(
	ctx context.Context,
	ffmpeg string,
	input string,
	output string,
	withAudio bool,
	forceInputFormat bool,
	label string,
) error {
	args := buildFfmpegArgs(input, output, withAudio, forceInputFormat)
	returnCode, stderrText, err := runCommandWithProgress(
		ctx,
		ffmpeg,
		args,
		func(line string) {
			if strings.Contains(line, "time=") || strings.Contains(line, "frame=") || strings.Contains(line, "fps=") || strings.Contains(strings.ToLower(line), "error") {
				fmt.Fprintln(os.Stderr, line)
			}
		},
		label,
	)
	if err != nil {
		return fmt.Errorf("ffmpeg [%s] failed (code=%d): %w\n%s", label, returnCode, err, strings.TrimSpace(stderrText))
	}

	if err := verifyFileNonZero(output); err != nil {
		return fmt.Errorf("ffmpeg [%s] produced invalid output: %w", label, err)
	}
	return nil
}

func buildFfmpegArgs(input, output string, withAudio bool, forceInputFormat bool) []string {
	base := []string{
		"-y",
		"-hide_banner",
		"-loglevel",
		"warning",
		"-nostdin",
		"-analyzeduration",
		"60M",
		"-probesize",
		"60M",
	}

	if forceInputFormat {
		base = append(base, "-f", "mpeg")
	}

	base = append(base, "-i", input)

	base = append(base,
		"-c:v",
		"libx264",
		"-preset",
		"veryfast",
		"-crf",
		"22",
		"-pix_fmt",
		"yuv420p",
		"-movflags",
		"+faststart",
		"-map",
		"0:v:0?",
		"-sn",
		"-dn",
	)
	if withAudio {
		base = append(base,
			"-c:a",
			"aac",
			"-b:a",
			"192k",
			"-ac",
			"2",
			"-map",
			"0:a:0?",
		)
	} else {
		base = append(base, "-an")
	}
	return append(base, output)
}

type commandOutput struct {
	stdout string
	stderr string
}

func runCommand(ctx context.Context, name string, args ...string) (commandOutput, int, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	stdoutPipe, err := cmd.StdoutPipe()
	if err != nil {
		return commandOutput{}, -1, err
	}
	stderrPipe, err := cmd.StderrPipe()
	if err != nil {
		return commandOutput{}, -1, err
	}

	if err := cmd.Start(); err != nil {
		return commandOutput{}, -1, err
	}

	stdout, stderr := new(strings.Builder), new(strings.Builder)
	var stdoutErr, stderrErr error

	waitReader := func(reader io.Reader, buffer *strings.Builder) error {
		sc := bufio.NewScanner(reader)
		for sc.Scan() {
			buffer.WriteString(sc.Text())
			buffer.WriteByte('\n')
		}
		if err := sc.Err(); err != nil {
			return err
		}
		return nil
	}

	done := make(chan struct{}, 2)
	go func() {
		stdoutErr = waitReader(stdoutPipe, stdout)
		done <- struct{}{}
	}()
	go func() {
		stderrErr = waitReader(stderrPipe, stderr)
		done <- struct{}{}
	}()

	for i := 0; i < 2; i++ {
		<-done
	}

	runErr := cmd.Wait()
	if stdoutErr != nil {
		return commandOutput{}, -1, stdoutErr
	}
	if stderrErr != nil {
		return commandOutput{}, -1, stderrErr
	}
	if runErr != nil {
		exitCode := extractExitCode(runErr)
		return commandOutput{stdout: stdout.String(), stderr: stderr.String()}, exitCode, runErr
	}
	return commandOutput{stdout: stdout.String(), stderr: stderr.String()}, 0, nil
}

func runCommandWithProgress(
	ctx context.Context,
	name string,
	args []string,
	onLine func(string),
	label string,
) (int, string, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return -1, "", err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return -1, "", err
	}

	if err := cmd.Start(); err != nil {
		return -1, "", err
	}

	timeRE := regexp.MustCompile(`time=(\d+):(\d+):(\d+(?:\.\d+)?)`)
	var stderrBuffer strings.Builder
	var scannerErr error

	done := make(chan struct{}, 1)
	go func() {
		scanner := bufio.NewScanner(stderr)
		for scanner.Scan() {
			line := scanner.Text()
			stderrBuffer.WriteString(line)
			stderrBuffer.WriteByte('\n')
			if strings.Contains(line, "time=") {
				if m := timeRE.FindStringSubmatch(line); len(m) == 4 {
					h, _ := strconv.Atoi(m[1])
					mn, _ := strconv.Atoi(m[2])
					s, _ := strconv.ParseFloat(m[3], 64)
					_ = h
					_ = mn
					_ = s
				}
			}
			if onLine != nil {
				onLine(line)
			}
		}
		if err := scanner.Err(); err != nil {
			scannerErr = err
		}
		done <- struct{}{}
	}()

	io.Copy(io.Discard, stdout)
	if err := cmd.Wait(); err != nil {
		if scannerErr != nil {
			return -1, strings.TrimSpace(stderrBuffer.String()), scannerErr
		}
		return extractExitCode(err), strings.TrimSpace(stderrBuffer.String()), err
	}
	<-done

	stderrText := strings.TrimSpace(stderrBuffer.String())
	if scannerErr != nil {
		return -1, stderrText, scannerErr
	}
	if strings.Contains(strings.ToLower(stderrText), "permission denied") {
		return 1, stderrText, fmt.Errorf("ffmpeg [%s] reported permission issue", label)
	}
	return 0, stderrText, nil
}

func extractExitCode(err error) int {
	var exitErr *exec.ExitError
	if errors.As(err, &exitErr) {
		return exitErr.ExitCode()
	}
	return -1
}

func verifyFileNonZero(path string) error {
	info, err := os.Stat(path)
	if err != nil {
		return err
	}
	if info.Size() <= 0 {
		return fmt.Errorf("file is empty: %s", path)
	}
	return nil
}

func fatal(msg string) {
	fatalf("%s", msg)
}

func fatalf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
}
