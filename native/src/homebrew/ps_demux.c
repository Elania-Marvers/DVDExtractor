#include "homebrew/ps_demux.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(__x86_64__) && !defined(DVD_DISABLE_ASM_MEMOPS)
extern int dvd_memcmp(const void* left, const void* right, size_t len);
#define DVD_PS_MEMCMP dvd_memcmp
#else
#define DVD_PS_MEMCMP memcmp
#endif

enum {
    DVD_PS_BUFFER_BYTES = 4u * 1024u * 1024u,
    DVD_PS_MIN_PREFIX = 4u,
    DVD_PS_MIN_PES = 6u,
    DVD_PS_START_PREFIX_0 = 0x00,
    DVD_PS_START_PREFIX_1 = 0x00,
    DVD_PS_START_PREFIX_2 = 0x01,
    DVD_PS_PACK_HEADER = 0xBA,
    DVD_PS_SYSTEM_HEADER = 0xBB,
    DVD_PS_PROGRAM_END = 0xB9,
    DVD_PS_PRIVATE_STREAM_1 = 0xBD,
    DVD_PS_PRIVATE_STREAM_2 = 0xBF,
    DVD_PS_PADDING_STREAM = 0xBE
};

typedef struct dvd_ps_output_slot {
    uint8_t stream_id;
    uint8_t substream_id;
    uint8_t has_substream;
    FILE* handle;
} dvd_ps_output_slot;

typedef struct dvd_ps_context {
    const dvd_ps_demux_options* options;
    dvd_ps_demux_report* report;
    dvd_ps_output_slot outputs[DVD_PS_MAX_STREAMS];
    size_t output_count;
} dvd_ps_context;

static void dvd_ps_set_error(dvd_ps_demux_report* report, const char* message) {
    if (report == NULL || report->error[0] != '\0') {
        return;
    }
    if (message == NULL) {
        message = "unknown error";
    }
    snprintf(report->error, sizeof(report->error), "%s", message);
}

static int dvd_ps_has_prefix(const uint8_t* data) {
    static const uint8_t prefix[3] = {
        DVD_PS_START_PREFIX_0,
        DVD_PS_START_PREFIX_1,
        DVD_PS_START_PREFIX_2,
    };
    return DVD_PS_MEMCMP(data, prefix, sizeof(prefix)) == 0;
}

static size_t dvd_ps_find_prefix(const uint8_t* data, size_t start, size_t len) {
    if (data == NULL || len < DVD_PS_MIN_PREFIX || start + DVD_PS_MIN_PREFIX > len) {
        return len;
    }

    for (size_t i = start; i + DVD_PS_MIN_PREFIX <= len; ++i) {
        if (data[i] == 0x00u && data[i + 1u] == 0x00u && data[i + 2u] == 0x01u) {
            return i;
        }
    }
    return len;
}

static int dvd_ps_is_video_stream(uint8_t stream_id) {
    return stream_id >= 0xE0u && stream_id <= 0xEFu;
}

static int dvd_ps_is_mpeg_audio_stream(uint8_t stream_id) {
    return stream_id >= 0xC0u && stream_id <= 0xDFu;
}

static int dvd_ps_is_length_prefixed_stream(uint8_t stream_id) {
    if (stream_id == DVD_PS_PACK_HEADER || stream_id == DVD_PS_PROGRAM_END) {
        return 0;
    }
    return 1;
}

static const char* dvd_ps_kind_for(uint8_t stream_id, uint8_t substream_id, int has_substream) {
    if (dvd_ps_is_video_stream(stream_id)) {
        return "video";
    }
    if (dvd_ps_is_mpeg_audio_stream(stream_id)) {
        return "mpeg-audio";
    }
    if (stream_id == DVD_PS_PRIVATE_STREAM_1 && has_substream) {
        if (substream_id >= 0x80u && substream_id <= 0x87u) {
            return "ac3";
        }
        if (substream_id >= 0x88u && substream_id <= 0x8Fu) {
            return "dts";
        }
        if (substream_id >= 0xA0u && substream_id <= 0xA7u) {
            return "lpcm";
        }
        if (substream_id >= 0x20u && substream_id <= 0x3Fu) {
            return "subpicture";
        }
        return "private-audio";
    }
    if (stream_id == DVD_PS_PRIVATE_STREAM_1) {
        return "private1";
    }
    if (stream_id == DVD_PS_PRIVATE_STREAM_2) {
        return "private2";
    }
    return "data";
}

static const char* dvd_ps_extension_for(const char* kind) {
    if (strcmp(kind, "video") == 0) {
        return "m2v";
    }
    if (strcmp(kind, "mpeg-audio") == 0) {
        return "mpa";
    }
    if (strcmp(kind, "ac3") == 0) {
        return "ac3";
    }
    if (strcmp(kind, "dts") == 0) {
        return "dts";
    }
    if (strcmp(kind, "lpcm") == 0) {
        return "lpcm";
    }
    if (strcmp(kind, "subpicture") == 0) {
        return "sup";
    }
    return "bin";
}

static dvd_ps_stream_stats* dvd_ps_find_or_add_stream(
    dvd_ps_demux_report* report,
    uint8_t stream_id,
    uint8_t substream_id,
    int has_substream) {
    if (report == NULL) {
        return NULL;
    }

    for (size_t i = 0; i < report->stream_count; ++i) {
        dvd_ps_stream_stats* item = &report->streams[i];
        if (item->stream_id == stream_id &&
            item->has_substream == (uint8_t)(has_substream ? 1u : 0u) &&
            (!has_substream || item->substream_id == substream_id)) {
            return item;
        }
    }

    if (report->stream_count >= DVD_PS_MAX_STREAMS) {
        ++report->skipped_packets;
        return NULL;
    }

    dvd_ps_stream_stats* item = &report->streams[report->stream_count++];
    memset(item, 0, sizeof(*item));
    item->stream_id = stream_id;
    item->substream_id = substream_id;
    item->has_substream = (uint8_t)(has_substream ? 1u : 0u);
    snprintf(item->kind, sizeof(item->kind), "%s", dvd_ps_kind_for(stream_id, substream_id, has_substream));
    return item;
}

static int dvd_ps_same_slot(const dvd_ps_output_slot* slot, const dvd_ps_stream_stats* stats) {
    return slot->stream_id == stats->stream_id &&
           slot->has_substream == stats->has_substream &&
           (!slot->has_substream || slot->substream_id == stats->substream_id);
}

static FILE* dvd_ps_output_for(dvd_ps_context* ctx, dvd_ps_stream_stats* stats) {
    if (ctx == NULL || stats == NULL || ctx->options == NULL || ctx->options->output_dir == NULL) {
        return NULL;
    }
    if (ctx->options->extract_payloads == 0u) {
        return NULL;
    }

    for (size_t i = 0; i < ctx->output_count; ++i) {
        if (dvd_ps_same_slot(&ctx->outputs[i], stats)) {
            return ctx->outputs[i].handle;
        }
    }

    if (ctx->output_count >= DVD_PS_MAX_STREAMS) {
        return NULL;
    }

    const char* ext = dvd_ps_extension_for(stats->kind);
    char path[DVD_PS_PATH_BYTES];
    if (stats->has_substream) {
        snprintf(path, sizeof(path), "%s/stream_%02x_%02x.%s",
                 ctx->options->output_dir,
                 (unsigned int)stats->stream_id,
                 (unsigned int)stats->substream_id,
                 ext);
    } else {
        snprintf(path, sizeof(path), "%s/stream_%02x.%s",
                 ctx->options->output_dir,
                 (unsigned int)stats->stream_id,
                 ext);
    }

    FILE* out = fopen(path, "ab");
    if (out == NULL) {
        dvd_ps_set_error(ctx->report, "cannot open demux output");
        return NULL;
    }

    dvd_ps_output_slot* slot = &ctx->outputs[ctx->output_count++];
    memset(slot, 0, sizeof(*slot));
    slot->stream_id = stats->stream_id;
    slot->substream_id = stats->substream_id;
    slot->has_substream = stats->has_substream;
    slot->handle = out;
    snprintf(stats->output_path, sizeof(stats->output_path), "%s", path);
    return out;
}

static void dvd_ps_close_outputs(dvd_ps_context* ctx) {
    if (ctx == NULL) {
        return;
    }
    for (size_t i = 0; i < ctx->output_count; ++i) {
        if (ctx->outputs[i].handle != NULL) {
            fclose(ctx->outputs[i].handle);
            ctx->outputs[i].handle = NULL;
        }
    }
    ctx->output_count = 0;
}

static size_t dvd_ps_pack_header_length(const uint8_t* packet, size_t available, int final_buffer) {
    if (available < 5u) {
        return final_buffer ? available : 0u;
    }

    if ((packet[4u] & 0xC0u) == 0x40u) {
        if (available < 14u) {
            return final_buffer ? available : 0u;
        }
        return 14u + (size_t)(packet[13u] & 0x07u);
    }

    if (available < 12u) {
        return final_buffer ? available : 0u;
    }
    return 12u;
}

static size_t dvd_ps_mpeg1_payload_offset(const uint8_t* payload, size_t payload_len) {
    size_t offset = 0;

    while (offset < payload_len && payload[offset] == 0xFFu) {
        ++offset;
    }

    if (offset + 2u <= payload_len && (payload[offset] & 0xC0u) == 0x40u) {
        offset += 2u;
    }

    if (offset >= payload_len) {
        return payload_len;
    }

    if ((payload[offset] & 0xF0u) == 0x20u) {
        return offset + 5u <= payload_len ? offset + 5u : payload_len;
    }
    if ((payload[offset] & 0xF0u) == 0x30u) {
        return offset + 10u <= payload_len ? offset + 10u : payload_len;
    }
    if (payload[offset] == 0x0Fu) {
        return offset + 1u <= payload_len ? offset + 1u : payload_len;
    }

    return offset;
}

static size_t dvd_ps_pes_payload_offset(const uint8_t* packet, size_t packet_len) {
    if (packet_len < DVD_PS_MIN_PES) {
        return packet_len;
    }

    const uint8_t stream_id = packet[3u];
    if (stream_id == DVD_PS_PADDING_STREAM ||
        stream_id == DVD_PS_PRIVATE_STREAM_2 ||
        stream_id == 0xBCu ||
        stream_id == 0xBEu ||
        stream_id == 0xF0u ||
        stream_id == 0xF1u ||
        stream_id == 0xF2u ||
        stream_id == 0xF8u ||
        stream_id == 0xFFu) {
        return DVD_PS_MIN_PES;
    }

    const uint8_t* payload = packet + DVD_PS_MIN_PES;
    const size_t payload_len = packet_len - DVD_PS_MIN_PES;
    if (payload_len >= 3u && (payload[0] & 0xC0u) == 0x80u) {
        const size_t header_len = (size_t)payload[2u];
        const size_t offset = DVD_PS_MIN_PES + 3u + header_len;
        return offset <= packet_len ? offset : packet_len;
    }

    return DVD_PS_MIN_PES + dvd_ps_mpeg1_payload_offset(payload, payload_len);
}

static size_t dvd_ps_private_payload_offset(uint8_t substream_id, size_t payload_len) {
    if (payload_len == 0u) {
        return 0u;
    }
    if ((substream_id >= 0x80u && substream_id <= 0x8Fu)) {
        return payload_len > 4u ? 4u : payload_len;
    }
    if (substream_id >= 0xA0u && substream_id <= 0xA7u) {
        return payload_len > 7u ? 7u : payload_len;
    }
    if (substream_id >= 0x20u && substream_id <= 0x3Fu) {
        return payload_len > 1u ? 1u : payload_len;
    }
    return 1u;
}

static void dvd_ps_record_payload(
    dvd_ps_context* ctx,
    uint8_t stream_id,
    const uint8_t* payload,
    size_t payload_len) {
    if (ctx == NULL || ctx->report == NULL) {
        return;
    }

    dvd_ps_demux_report* report = ctx->report;
    ++report->pes_packets;

    if (payload == NULL || payload_len == 0u) {
        ++report->skipped_packets;
        return;
    }

    uint8_t substream_id = 0;
    int has_substream = 0;
    const uint8_t* stream_payload = payload;
    size_t stream_len = payload_len;

    if (stream_id == DVD_PS_PRIVATE_STREAM_1) {
        has_substream = 1;
        substream_id = payload[0];
        const size_t private_offset = dvd_ps_private_payload_offset(substream_id, payload_len);
        stream_payload = payload + private_offset;
        stream_len = payload_len - private_offset;
        ++report->private_packets;
        if ((substream_id >= 0x80u && substream_id <= 0x8Fu) ||
            (substream_id >= 0xA0u && substream_id <= 0xA7u)) {
            ++report->audio_packets;
        } else if (substream_id >= 0x20u && substream_id <= 0x3Fu) {
            ++report->skipped_packets;
        }
    } else if (dvd_ps_is_video_stream(stream_id)) {
        ++report->video_packets;
    } else if (dvd_ps_is_mpeg_audio_stream(stream_id)) {
        ++report->audio_packets;
    } else {
        ++report->skipped_packets;
    }

    dvd_ps_stream_stats* stats = dvd_ps_find_or_add_stream(report, stream_id, substream_id, has_substream);
    if (stats == NULL) {
        return;
    }

    ++stats->packets;
    stats->payload_bytes += (uint64_t)stream_len;

    FILE* out = dvd_ps_output_for(ctx, stats);
    if (out != NULL && stream_len > 0u) {
        const size_t written = fwrite(stream_payload, 1u, stream_len, out);
        if (written != stream_len) {
            dvd_ps_set_error(report, "short write during demux");
        }
    }
}

static size_t dvd_ps_parse_buffer(dvd_ps_context* ctx, const uint8_t* data, size_t len, int final_buffer) {
    dvd_ps_demux_report* report = ctx->report;
    size_t pos = 0u;

    while (pos + DVD_PS_MIN_PREFIX <= len) {
        const size_t prefix = dvd_ps_find_prefix(data, pos, len);
        if (prefix == len) {
            return len > 3u ? len - 3u : 0u;
        }

        if (!dvd_ps_has_prefix(data + prefix)) {
            pos = prefix + 1u;
            continue;
        }

        const uint8_t stream_id = data[prefix + 3u];
        if (stream_id == DVD_PS_PACK_HEADER) {
            const size_t available = len - prefix;
            const size_t header_len = dvd_ps_pack_header_length(data + prefix, available, final_buffer);
            if (header_len == 0u || prefix + header_len > len) {
                return prefix;
            }
            ++report->pack_headers;
            pos = prefix + header_len;
            continue;
        }

        if (stream_id == DVD_PS_PROGRAM_END) {
            pos = prefix + DVD_PS_MIN_PREFIX;
            continue;
        }

        if (!dvd_ps_is_length_prefixed_stream(stream_id)) {
            ++report->skipped_packets;
            pos = prefix + DVD_PS_MIN_PREFIX;
            continue;
        }

        if (prefix + DVD_PS_MIN_PES > len) {
            return final_buffer ? len : prefix;
        }

        const size_t pes_length = ((size_t)data[prefix + 4u] << 8u) | (size_t)data[prefix + 5u];
        if (pes_length == 0u && dvd_ps_is_video_stream(stream_id)) {
            const size_t next = dvd_ps_find_prefix(data, prefix + DVD_PS_MIN_PREFIX, len);
            if (next == len && !final_buffer) {
                return prefix;
            }
            const size_t packet_end = next == len ? len : next;
            const size_t payload_offset = dvd_ps_pes_payload_offset(data + prefix, packet_end - prefix);
            if (payload_offset < packet_end - prefix) {
                dvd_ps_record_payload(
                    ctx,
                    stream_id,
                    data + prefix + payload_offset,
                    packet_end - prefix - payload_offset);
            }
            pos = packet_end;
            continue;
        }

        const size_t packet_len = DVD_PS_MIN_PES + pes_length;
        if (prefix + packet_len > len) {
            if (!final_buffer) {
                return prefix;
            }
            ++report->truncated_packets;
            return len;
        }

        if (stream_id == DVD_PS_SYSTEM_HEADER) {
            ++report->system_headers;
            pos = prefix + packet_len;
            continue;
        }

        const size_t payload_offset = dvd_ps_pes_payload_offset(data + prefix, packet_len);
        if (payload_offset < packet_len) {
            dvd_ps_record_payload(ctx, stream_id, data + prefix + payload_offset, packet_len - payload_offset);
        } else {
            ++report->skipped_packets;
        }

        pos = prefix + packet_len;
    }

    return final_buffer ? len : pos;
}

int dvd_ps_demux_file(const dvd_ps_demux_options* options, dvd_ps_demux_report* report) {
    if (report == NULL) {
        return 1;
    }

    memset(report, 0, sizeof(*report));
    if (options == NULL || options->input_path == NULL || options->input_path[0] == '\0') {
        dvd_ps_set_error(report, "missing input path");
        return 1;
    }

    FILE* in = fopen(options->input_path, "rb");
    if (in == NULL) {
        dvd_ps_set_error(report, strerror(errno));
        return 1;
    }

    dvd_ps_context ctx;
    memset(&ctx, 0, sizeof(ctx));
    ctx.options = options;
    ctx.report = report;

    uint8_t* buffer = (uint8_t*)malloc(DVD_PS_BUFFER_BYTES);
    if (buffer == NULL) {
        fclose(in);
        dvd_ps_set_error(report, "cannot allocate demux buffer");
        return 1;
    }

    size_t used = 0u;
    int eof = 0;
    int rc = 0;

    while (!eof) {
        size_t read_cap = DVD_PS_BUFFER_BYTES - used;
        if (read_cap == 0u) {
            ++report->truncated_packets;
            used = 0u;
            read_cap = DVD_PS_BUFFER_BYTES;
        }

        if (options->max_bytes > 0u) {
            const uint64_t remaining = options->max_bytes > report->input_bytes
                ? options->max_bytes - report->input_bytes
                : 0u;
            if (remaining == 0u) {
                eof = 1;
                break;
            }
            if ((uint64_t)read_cap > remaining) {
                read_cap = (size_t)remaining;
            }
        }

        const size_t n = fread(buffer + used, 1u, read_cap, in);
        if (n > 0u) {
            used += n;
            report->input_bytes += (uint64_t)n;
        }

        if (n < read_cap) {
            if (ferror(in)) {
                dvd_ps_set_error(report, "read error during demux");
                rc = 1;
                break;
            }
            eof = 1;
        }

        const size_t consumed = dvd_ps_parse_buffer(&ctx, buffer, used, eof);
        if (consumed > used) {
            dvd_ps_set_error(report, "parser consumed invalid byte count");
            rc = 1;
            break;
        }
        if (consumed > 0u) {
            if (consumed < used) {
                memmove(buffer, buffer + consumed, used - consumed);
            }
            used -= consumed;
            report->consumed_bytes += (uint64_t)consumed;
        }
    }

    if (rc == 0 && used > 0u) {
        const size_t consumed = dvd_ps_parse_buffer(&ctx, buffer, used, 1);
        report->consumed_bytes += (uint64_t)consumed;
    }

    dvd_ps_close_outputs(&ctx);
    free(buffer);
    fclose(in);

    if (report->error[0] != '\0') {
        return 1;
    }
    return rc;
}
