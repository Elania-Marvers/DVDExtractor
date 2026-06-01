#include "homebrew/mpeg_probe.h"

#include <string.h>

#if defined(__x86_64__) && !defined(DVD_DISABLE_ASM_MEMOPS)
extern int dvd_memcmp(const void* left, const void* right, size_t len);
extern size_t dvd_max_zero_run(const uint8_t* data, size_t len);
#define DVD_PROBE_MEMCMP dvd_memcmp
#define DVD_PROBE_MAX_ZERO_RUN dvd_max_zero_run
#else
static int dvd_probe_memcmp_fallback(const void* left, const void* right, size_t len) {
    return memcmp(left, right, len);
}

static size_t dvd_probe_max_zero_run_fallback(const uint8_t* data, size_t len) {
    size_t best = 0;
    size_t current = 0;
    for (size_t i = 0; i < len; ++i) {
        if (data[i] == 0u) {
            ++current;
            best = current > best ? current : best;
        } else {
            current = 0;
        }
    }
    return best;
}
#define DVD_PROBE_MEMCMP dvd_probe_memcmp_fallback
#define DVD_PROBE_MAX_ZERO_RUN dvd_probe_max_zero_run_fallback
#endif

enum {
    DVD_MPEG_CODE_PACK = 0xBA,
    DVD_MPEG_CODE_SYSTEM_HEADER = 0xBB,
    DVD_MPEG_CODE_SEQUENCE_HEADER = 0xB3,
    DVD_MPEG_CODE_PRIVATE_STREAM_2 = 0xBF
};

void dvd_mpeg_probe_buffer(const uint8_t* data, size_t len, dvd_mpeg_probe_stats* out) {
    static const uint8_t kStartCodePrefix[3] = {0x00u, 0x00u, 0x01u};

    if (out == NULL) {
        return;
    }

    memset(out, 0, sizeof(*out));
    if (data == NULL || len == 0u) {
        return;
    }

    out->bytes = (uint64_t)len;
    out->max_zero_run = (uint64_t)DVD_PROBE_MAX_ZERO_RUN(data, len);

    if (len < 4u) {
        return;
    }

    for (size_t i = 0; i + 4u <= len; ++i) {
        if (DVD_PROBE_MEMCMP(data + i, kStartCodePrefix, sizeof(kStartCodePrefix)) != 0) {
            continue;
        }

        const uint8_t code = data[i + 3u];
        if (code == DVD_MPEG_CODE_PACK) {
            ++out->pack_sync_count;
        } else if (code == DVD_MPEG_CODE_SYSTEM_HEADER) {
            ++out->system_header_count;
        } else if (code == DVD_MPEG_CODE_SEQUENCE_HEADER) {
            ++out->sequence_header_count;
        } else if (code == DVD_MPEG_CODE_PRIVATE_STREAM_2) {
            ++out->nav_pack_count;
        }
    }

    out->likely_program_stream = (
        out->pack_sync_count > 0u ||
        out->sequence_header_count > 0u ||
        out->system_header_count > 0u ||
        out->nav_pack_count > 0u
    ) ? 1u : 0u;
}
