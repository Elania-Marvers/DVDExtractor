#ifndef DVDEXTRACTOR_HOMEBREW_MPEG_PROBE_H_
#define DVDEXTRACTOR_HOMEBREW_MPEG_PROBE_H_

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct dvd_mpeg_probe_stats {
    uint64_t bytes;
    uint64_t pack_sync_count;
    uint64_t system_header_count;
    uint64_t sequence_header_count;
    uint64_t nav_pack_count;
    uint64_t max_zero_run;
    uint8_t likely_program_stream;
} dvd_mpeg_probe_stats;

void dvd_mpeg_probe_buffer(const uint8_t* data, size_t len, dvd_mpeg_probe_stats* out);

#ifdef __cplusplus
}
#endif

#endif  // DVDEXTRACTOR_HOMEBREW_MPEG_PROBE_H_
