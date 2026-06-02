#ifndef DVDEXTRACTOR_HOMEBREW_PS_DEMUX_H_
#define DVDEXTRACTOR_HOMEBREW_PS_DEMUX_H_

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum {
    DVD_PS_MAX_STREAMS = 64,
    DVD_PS_KIND_BYTES = 16,
    DVD_PS_PATH_BYTES = 512,
    DVD_PS_ERROR_BYTES = 256
};

typedef struct dvd_ps_stream_stats {
    uint8_t stream_id;
    uint8_t substream_id;
    uint8_t has_substream;
    char kind[DVD_PS_KIND_BYTES];
    uint64_t packets;
    uint64_t payload_bytes;
    char output_path[DVD_PS_PATH_BYTES];
} dvd_ps_stream_stats;

typedef struct dvd_ps_demux_options {
    const char* input_path;
    const char* output_dir;
    uint64_t max_bytes;
    uint8_t extract_payloads;
} dvd_ps_demux_options;

typedef struct dvd_ps_demux_report {
    uint64_t input_bytes;
    uint64_t consumed_bytes;
    uint64_t pack_headers;
    uint64_t system_headers;
    uint64_t pes_packets;
    uint64_t video_packets;
    uint64_t audio_packets;
    uint64_t private_packets;
    uint64_t skipped_packets;
    uint64_t truncated_packets;
    size_t stream_count;
    dvd_ps_stream_stats streams[DVD_PS_MAX_STREAMS];
    char error[DVD_PS_ERROR_BYTES];
} dvd_ps_demux_report;

int dvd_ps_demux_file(const dvd_ps_demux_options* options, dvd_ps_demux_report* report);

#ifdef __cplusplus
}
#endif

#endif  // DVDEXTRACTOR_HOMEBREW_PS_DEMUX_H_
