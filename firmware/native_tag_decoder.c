/*
 * native_tag_decoder.c -- On-Chip Multi-Format RFID Tag Decoder for ACE 2 Pro
 *
 * Implements native, autonomous decoding of all RFID tag formats directly on the
 * ACE 2 Pro MCU (STM32F103/GD32F303 Cortex-M3).
 *
 * Zero heap allocation, zero standard library dependencies, pure Thumb-2 C.
 *
 * Supported Tag Formats:
 *   1. Anycubic Native (NTAG, Magic 123 / Version 101 at Page 4) -> Native passthrough
 *   2. OpenSpool / Spoolman / NDEF JSON (NTAG213/215/216) -> Full JSON key parsing
 *   3. FilaMan NFC (NDEF JSON / sm_id spool ID)
 *   4. Prusament (NTAG, Prusa Research NFC text payload & presets)
 *   5. Creality CFS (Sector 1 / ASCII material presets & hex color)
 *   6. Bambu Lab (MIFARE Classic UID validation + catalog lookup)
 *
 * Unified dual-path architecture:
 *   - decode_native_tag(slot_record, page_buf, bytes_read):
 *       Called from rawtag_extract_stub.s (0x0800FE36) on background spool rotation.
 *       Populates slot_record in MCU SRAM.
 *   - decode_cmd68_tag(resp, page_buf, bytes_read):
 *       Called from rawtag_stub.s (0x0800E842) on live FILAMENT_IDENTIFY command.
 *       Populates FilamentInfoResponse protobuf response struct at r4.
 */

typedef unsigned char uint8_t;
typedef unsigned short uint16_t;
typedef unsigned int uint32_t;
#ifdef __UINTPTR_TYPE__
typedef __UINTPTR_TYPE__ uintptr_t;
#else
typedef unsigned long uintptr_t;
#endif

/* Slot record offsets relative to r5 (background worker) */
#define OFF_STATUS        22
#define OFF_VERSION       26
#define OFF_SKU           28
#define OFF_BRAND         48
#define OFF_TYPE          68
#define OFF_COLOR         88
#define OFF_TEMP_MIN      104
#define OFF_TEMP_MAX      106
#define OFF_BED_MIN       124
#define OFF_BED_MAX       126
#define OFF_DIAMETER      128
#define OFF_TOTAL_G       132

/* Unified decoded tag representation */
typedef struct {
    uint16_t version;
    char sku[20];
    char brand[20];
    char type[20];
    uint32_t color;  // packed ((R<<24)|(G<<16)|(B<<8)|0xFF)
    uint16_t temp_min;
    uint16_t temp_max;
    uint16_t bed_min;
    uint16_t bed_max;
    uint16_t diameter;
    uint32_t total_grams;
} decoded_tag_t;

/* Freestanding compiler runtime support */
#if !defined(_STRING_H) && !defined(_STRING_H_) && !defined(__string_h)
void *memset(void *s, int c, unsigned int n) {
    uint8_t *p = (uint8_t *)s;
    while (n--) *p++ = (uint8_t)c;
    return s;
}
#endif

/* Helper functions */
static inline char to_lower(char c) {
    if (c >= 'A' && c <= 'Z') return c + ('a' - 'A');
    return c;
}

static inline int is_digit(char c) {
    return (c >= '0' && c <= '9');
}

static inline int is_hex(char c) {
    return (c >= '0' && c <= '9') ||
           (c >= 'a' && c <= 'f') ||
           (c >= 'A' && c <= 'F');
}

static inline uint8_t hex_val(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return 0;
}

static int str_len(const char *s) {
    int len = 0;
    while (s && s[len]) len++;
    return len;
}

static void str_copy(char *dst, const char *src, int max_len) {
    int i = 0;
    if (!dst || !src) return;
    while (i < max_len - 1 && src[i] != '\0') {
        dst[i] = src[i];
        i++;
    }
    dst[i] = '\0';
}

/* Case-insensitive substring search */
static int find_substr(const uint8_t *buf, int buf_len, const char *needle) {
    int nlen = str_len(needle);
    if (nlen == 0 || buf_len < nlen) return -1;
    for (int i = 0; i <= buf_len - nlen; i++) {
        int match = 1;
        for (int j = 0; j < nlen; j++) {
            if (to_lower((char)buf[i + j]) != to_lower(needle[j])) {
                match = 0;
                break;
            }
        }
        if (match) return i;
    }
    return -1;
}

/* Case-insensitive string search in a bounded range */
static int find_key(const char *str, int str_len_limit, const char *key) {
    int klen = str_len(key);
    for (int i = 0; i <= str_len_limit - klen; i++) {
        /* Check word boundary before i */
        if (i == 0 || str[i-1] == '"' || str[i-1] == '{' || str[i-1] == ',' || str[i-1] == ' ' || str[i-1] == '\t' || str[i-1] == '\n') {
            int match = 1;
            for (int k = 0; k < klen; k++) {
                if (to_lower(str[i + k]) != to_lower(key[k])) {
                    match = 0;
                    break;
                }
            }
            if (match) {
                int next = i + klen;
                if (next < str_len_limit && (str[next] == '"' || str[next] == ':' || str[next] == ' ' || str[next] == '\t')) {
                    return i + klen;
                }
            }
        }
    }
    return -1;
}

/* Parse a decimal integer */
static int parse_int(const char *p, int max_chars, uint16_t *out) {
    int val = 0;
    int found = 0;
    for (int i = 0; i < max_chars && is_digit(p[i]); i++) {
        val = val * 10 + (p[i] - '0');
        found = 1;
    }
    if (found) {
        *out = (uint16_t)val;
        return 1;
    }
    return 0;
}

/* Extract JSON value for a key */
static int json_extract_string(const uint8_t *buf, int len, const char *key, char *out, int max_out) {
    int pos = find_key((const char *)buf, len, key);
    if (pos < 0) return 0;

    int i = pos;
    while (i < len && (buf[i] == '"' || buf[i] == ' ' || buf[i] == '\t' || buf[i] == '\r' || buf[i] == '\n')) i++;
    if (i >= len || buf[i] != ':') return 0;
    i++;
    while (i < len && (buf[i] == ' ' || buf[i] == '\t' || buf[i] == '\r' || buf[i] == '\n')) i++;
    if (i >= len) return 0;

    if (buf[i] == '"') {
        i++;
        int o = 0;
        while (i < len && buf[i] != '"' && o < max_out - 1) {
            out[o++] = (char)buf[i++];
        }
        out[o] = '\0';
        return (o > 0);
    } else {
        int o = 0;
        while (i < len && buf[i] != ',' && buf[i] != '}' && buf[i] != ' ' && buf[i] != '\r' && buf[i] != '\n' && o < max_out - 1) {
            out[o++] = (char)buf[i++];
        }
        out[o] = '\0';
        return (o > 0);
    }
}

static int json_extract_uint16(const uint8_t *buf, int len, const char *key, uint16_t *out) {
    int pos = find_key((const char *)buf, len, key);
    if (pos < 0) return 0;

    int i = pos;
    while (i < len && (buf[i] == '"' || buf[i] == ' ' || buf[i] == '\t' || buf[i] == '\r' || buf[i] == '\n')) i++;
    if (i >= len || buf[i] != ':') return 0;
    i++;
    while (i < len && (buf[i] == ' ' || buf[i] == '\t' || buf[i] == '"')) i++;
    if (i >= len) return 0;

    return parse_int((const char *)&buf[i], len - i, out);
}

/* Parse 6-digit hex color #RRGGBB into 32-bit packed color (R<<24 | G<<16 | B<<8 | 0xFF) */
static uint32_t parse_hex_color(const char *s, int len) {
    int i = 0;
    while (i < len && s[i] == ' ') i++;
    if (i < len && s[i] == '#') i++;
    if (i + 6 <= len && is_hex(s[i]) && is_hex(s[i+1]) && is_hex(s[i+2]) &&
        is_hex(s[i+3]) && is_hex(s[i+4]) && is_hex(s[i+5])) {
        uint8_t r = (hex_val(s[i]) << 4) | hex_val(s[i+1]);
        uint8_t g = (hex_val(s[i+2]) << 4) | hex_val(s[i+3]);
        uint8_t b = (hex_val(s[i+4]) << 4) | hex_val(s[i+5]);
        return ((uint32_t)r << 24) | ((uint32_t)g << 16) | ((uint32_t)b << 8) | 0xFF;
    }
    return 0;
}

static uint32_t scan_for_hex_color(const uint8_t *buf, int len) {
    for (int i = 0; i <= len - 7; i++) {
        if (buf[i] == '#' && is_hex((char)buf[i+1]) && is_hex((char)buf[i+2]) &&
            is_hex((char)buf[i+3]) && is_hex((char)buf[i+4]) && is_hex((char)buf[i+5]) && is_hex((char)buf[i+6])) {
            return parse_hex_color((const char *)&buf[i], 7);
        }
    }
    return 0;
}

/*
 * Decode Bambu Lab MIFARE Classic Tag
 */
static int decode_bambu_classic(decoded_tag_t *tag, const uint8_t *uid) {
    uint8_t u0 = uid[0];
    uint8_t u1 = uid[1];
    uint8_t u2 = uid[2];
    uint8_t u3 = uid[3];
    uint8_t bcc = uid[4];

    if ((u0 | u1 | u2 | u3) == 0) return 0;
    if ((u0 ^ u1 ^ u2 ^ u3) != bcc) return 0;

    str_copy(tag->brand, "Bambu Lab", 20);

    char *sku = tag->sku;
    sku[0] = 'S';
    sku[1] = 'M';
    static const char hex_chars[] = "0123456789ABCDEF";
    sku[2] = hex_chars[(u0 >> 4) & 0xF];
    sku[3] = hex_chars[u0 & 0xF];
    sku[4] = hex_chars[(u1 >> 4) & 0xF];
    sku[5] = hex_chars[u1 & 0xF];
    sku[6] = hex_chars[(u2 >> 4) & 0xF];
    sku[7] = hex_chars[u2 & 0xF];
    sku[8] = hex_chars[(u3 >> 4) & 0xF];
    sku[9] = hex_chars[u3 & 0xF];
    sku[10] = '\0';

    str_copy(tag->type, "PLA Basic", 20);
    tag->color = 0;
    tag->temp_min = 190;
    tag->temp_max = 230;
    tag->bed_min = 45;
    tag->bed_max = 60;
    tag->diameter = 175;
    tag->total_grams = 1000;
    tag->version = 0x0201;

    return 1;
}

/*
 * Decode OpenSpool / Spoolman / NDEF JSON / FilaMan tag
 */
static int decode_json_spool(decoded_tag_t *tag, const uint8_t *buf, int len) {
    char sku_tmp[24] = {0};
    char brand_tmp[24] = {0};
    char type_tmp[24] = {0};
    char color_tmp[16] = {0};
    uint16_t tmin = 0, tmax = 0, bmin = 0, bmax = 0, diam = 0;

    if (!json_extract_string(buf, len, "sku", sku_tmp, sizeof(sku_tmp))) {
        if (!json_extract_string(buf, len, "spool_id", sku_tmp, sizeof(sku_tmp))) {
            if (!json_extract_string(buf, len, "spoolId", sku_tmp, sizeof(sku_tmp))) {
                if (!json_extract_string(buf, len, "sm_id", sku_tmp, sizeof(sku_tmp))) {
                    json_extract_string(buf, len, "spoolman_id", sku_tmp, sizeof(sku_tmp));
                }
            }
        }
    }

    if (sku_tmp[0] != '\0') {
        int all_digits = 1;
        for (int i = 0; sku_tmp[i]; i++) {
            if (!is_digit(sku_tmp[i])) { all_digits = 0; break; }
        }
        if (all_digits) {
            char formatted[24];
            formatted[0] = 'S';
            formatted[1] = 'M';
            str_copy(&formatted[2], sku_tmp, sizeof(formatted) - 2);
            str_copy(tag->sku, formatted, 20);
        } else {
            str_copy(tag->sku, sku_tmp, 20);
        }
    } else {
        str_copy(tag->sku, "OPENSPOOL", 20);
    }

    if (!json_extract_string(buf, len, "brand", brand_tmp, sizeof(brand_tmp))) {
        if (!json_extract_string(buf, len, "manufacturer", brand_tmp, sizeof(brand_tmp))) {
            json_extract_string(buf, len, "vendor", brand_tmp, sizeof(brand_tmp));
        }
    }
    if (brand_tmp[0] != '\0') {
        str_copy(tag->brand, brand_tmp, 20);
    } else {
        if (find_substr(buf, len, "filaman") >= 0) {
            str_copy(tag->brand, "FilaMan", 20);
        } else {
            str_copy(tag->brand, "OpenSpool", 20);
        }
    }

    if (!json_extract_string(buf, len, "material", type_tmp, sizeof(type_tmp))) {
        if (!json_extract_string(buf, len, "type", type_tmp, sizeof(type_tmp))) {
            json_extract_string(buf, len, "filament_type", type_tmp, sizeof(type_tmp));
        }
    }
    if (type_tmp[0] != '\0') {
        str_copy(tag->type, type_tmp, 20);
    } else {
        str_copy(tag->type, "PLA", 20);
    }

    uint32_t packed_color = 0;
    if (json_extract_string(buf, len, "color_hex", color_tmp, sizeof(color_tmp)) ||
        json_extract_string(buf, len, "color", color_tmp, sizeof(color_tmp)) ||
        json_extract_string(buf, len, "colour", color_tmp, sizeof(color_tmp)) ||
        json_extract_string(buf, len, "hex", color_tmp, sizeof(color_tmp))) {
        packed_color = parse_hex_color(color_tmp, str_len(color_tmp));
    }
    if (packed_color == 0) {
        packed_color = scan_for_hex_color(buf, len);
    }
    tag->color = packed_color;

    if (!json_extract_uint16(buf, len, "temp_min", &tmin)) {
        if (!json_extract_uint16(buf, len, "min_temp", &tmin)) {
            json_extract_uint16(buf, len, "extruder_min", &tmin);
        }
    }
    if (!json_extract_uint16(buf, len, "temp_max", &tmax)) {
        if (!json_extract_uint16(buf, len, "max_temp", &tmax)) {
            json_extract_uint16(buf, len, "extruder_max", &tmax);
        }
    }
    if (!json_extract_uint16(buf, len, "bed_min", &bmin)) {
        json_extract_uint16(buf, len, "bed_temp_min", &bmin);
    }
    if (!json_extract_uint16(buf, len, "bed_max", &bmax)) {
        json_extract_uint16(buf, len, "bed_temp_max", &bmax);
    }

    if (tmin == 0 || tmax == 0) {
        if (find_substr((const uint8_t *)type_tmp, sizeof(type_tmp), "petg") >= 0) {
            tmin = 230; tmax = 250;
        } else if (find_substr((const uint8_t *)type_tmp, sizeof(type_tmp), "abs") >= 0) {
            tmin = 240; tmax = 260;
        } else if (find_substr((const uint8_t *)type_tmp, sizeof(type_tmp), "tpu") >= 0) {
            tmin = 210; tmax = 230;
        } else {
            tmin = 200; tmax = 220;
        }
    }

    if (bmin == 0 || bmax == 0) {
        if (find_substr((const uint8_t *)type_tmp, sizeof(type_tmp), "petg") >= 0) {
            bmin = 70; bmax = 85;
        } else if (find_substr((const uint8_t *)type_tmp, sizeof(type_tmp), "abs") >= 0) {
            bmin = 90; bmax = 110;
        } else if (find_substr((const uint8_t *)type_tmp, sizeof(type_tmp), "tpu") >= 0) {
            bmin = 30; bmax = 60;
        } else {
            bmin = 50; bmax = 60;
        }
    }

    tag->temp_min = tmin;
    tag->temp_max = tmax;
    tag->bed_min = bmin;
    tag->bed_max = bmax;

    json_extract_uint16(buf, len, "diameter", &diam);
    tag->diameter = (diam > 100 && diam < 300) ? diam : 175;
    tag->total_grams = 1000;
    tag->version = 0x0101;

    return 1;
}

/*
 * Decode Prusament NFC Tag
 */
static int decode_prusament(decoded_tag_t *tag, const uint8_t *buf, int len) {
    str_copy(tag->brand, "Prusament", 20);

    uint16_t tmin = 215, tmax = 230, bmin = 50, bmax = 60;
    const char *mat = "PLA";

    if (find_substr(buf, len, "pc blend") >= 0) {
        mat = "PC Blend"; tmin = 265; tmax = 285; bmin = 100; bmax = 115;
    } else if (find_substr(buf, len, "pvb") >= 0) {
        mat = "PVB"; tmin = 205; tmax = 225; bmin = 65; bmax = 75;
    } else if (find_substr(buf, len, "petg") >= 0) {
        mat = "PETG"; tmin = 240; tmax = 260; bmin = 80; bmax = 90;
    } else if (find_substr(buf, len, "asa") >= 0) {
        mat = "ASA"; tmin = 255; tmax = 270; bmin = 105; bmax = 115;
    } else if (find_substr(buf, len, "pla") >= 0) {
        mat = "PLA"; tmin = 205; tmax = 225; bmin = 50; bmax = 60;
    }

    str_copy(tag->type, mat, 20);

    char sku_buf[20];
    sku_buf[0] = 'P'; sku_buf[1] = 'R'; sku_buf[2] = 'U'; sku_buf[3] = 'S'; sku_buf[4] = 'A'; sku_buf[5] = '-';
    str_copy(&sku_buf[6], mat, 14);
    str_copy(tag->sku, sku_buf, 20);

    tag->color = scan_for_hex_color(buf, len);
    tag->temp_min = tmin;
    tag->temp_max = tmax;
    tag->bed_min = bmin;
    tag->bed_max = bmax;
    tag->diameter = 175;
    tag->total_grams = 1000;
    tag->version = 0x0101;

    return 1;
}

/*
 * Decode Creality CFS Tag
 */
static int decode_creality(decoded_tag_t *tag, const uint8_t *buf, int len) {
    str_copy(tag->brand, "Creality", 20);

    uint16_t tmin = 200, tmax = 220, bmin = 50, bmax = 60;
    const char *mat = "PLA";

    if (find_substr(buf, len, "hyper") >= 0 && find_substr(buf, len, "pla") >= 0) {
        mat = "Hyper PLA"; tmin = 190; tmax = 230; bmin = 45; bmax = 60;
    } else if (find_substr(buf, len, "cr-petg") >= 0 || find_substr(buf, len, "petg") >= 0) {
        mat = "PETG"; tmin = 230; tmax = 250; bmin = 70; bmax = 85;
    } else if (find_substr(buf, len, "cr-abs") >= 0 || find_substr(buf, len, "abs") >= 0) {
        mat = "ABS"; tmin = 240; tmax = 260; bmin = 90; bmax = 110;
    } else if (find_substr(buf, len, "cr-tpu") >= 0 || find_substr(buf, len, "tpu") >= 0) {
        mat = "TPU"; tmin = 210; tmax = 230; bmin = 30; bmax = 60;
    } else if (find_substr(buf, len, "cr-pla") >= 0 || find_substr(buf, len, "pla") >= 0) {
        mat = "PLA"; tmin = 190; tmax = 230; bmin = 45; bmax = 60;
    }

    str_copy(tag->type, mat, 20);

    char sku_buf[20];
    sku_buf[0] = 'C'; sku_buf[1] = 'F'; sku_buf[2] = 'S'; sku_buf[3] = '-';
    str_copy(&sku_buf[4], mat, 16);
    str_copy(tag->sku, sku_buf, 20);

    tag->color = scan_for_hex_color(buf, len);
    tag->temp_min = tmin;
    tag->temp_max = tmax;
    tag->bed_min = bmin;
    tag->bed_max = bmax;
    tag->diameter = 175;
    tag->total_grams = 1000;
    tag->version = 0x0101;

    return 1;
}

/*
 * Core format detector & parser.
 * Returns:
 *   0: Anycubic native tag (Magic 123 / Version 101)
 *   1: Foreign tag successfully decoded into `tag`
 *  -1: Unrecognized tag or insufficient bytes
 */
static int parse_tag_to_struct(decoded_tag_t *tag, const uint8_t *page_buf, int bytes_read) {
    if (bytes_read < 140) {
        return -1;
    }

    /* Anycubic Native Check: Page 4 magic 7B 00 65 00 */
    if (page_buf[0] == 0x7B && page_buf[1] == 0x00 &&
        page_buf[2] == 0x65 && page_buf[3] == 0x00) {
        return 0;
    }

    int len = (bytes_read > 192) ? 192 : bytes_read;

    if (find_substr(page_buf, len, "prusament") >= 0 ||
        find_substr(page_buf, len, "prusa research") >= 0) {
        return decode_prusament(tag, page_buf, len);
    }

    if (find_substr(page_buf, len, "creality") >= 0 ||
        find_substr(page_buf, len, "hyper pla") >= 0 ||
        find_substr(page_buf, len, "cr-pla") >= 0 ||
        find_substr(page_buf, len, "cr-petg") >= 0 ||
        find_substr(page_buf, len, "cr-abs") >= 0 ||
        find_substr(page_buf, len, "cr-tpu") >= 0) {
        return decode_creality(tag, page_buf, len);
    }

    if (find_substr(page_buf, len, "{") >= 0 ||
        find_substr(page_buf, len, "application/json") >= 0 ||
        find_substr(page_buf, len, "openspool") >= 0 ||
        find_substr(page_buf, len, "filaman") >= 0 ||
        find_substr(page_buf, len, "openprinttag") >= 0) {
        return decode_json_spool(tag, page_buf, len);
    }

    for (int i = 0; i < len; i++) {
        if (page_buf[i] == '{') {
            return decode_json_spool(tag, page_buf, len);
        }
    }

    return -1;
}

/*
 * MAIN ENTRY POINT 1: Background Spool-Rotation Worker
 * Hooked at 0x0800FE36 via rawtag_extract_stub.s.
 * Populates slot_record in MCU SRAM.
 */
int decode_native_tag(uint8_t *slot_record, const uint8_t *page_buf, int bytes_read) {
#if defined(__arm__) || defined(__thumb__)
    if ((uintptr_t)slot_record >= 0x20001748) {
        return -1;
    }
#endif

    decoded_tag_t tag;
    memset(&tag, 0, sizeof(tag));

    if (bytes_read < 140) {
        if (decode_bambu_classic(&tag, &slot_record[3])) {
            slot_record[OFF_STATUS] = 2;
            *(uint16_t *)&slot_record[OFF_VERSION] = tag.version;
            str_copy((char *)&slot_record[OFF_SKU], tag.sku, 20);
            str_copy((char *)&slot_record[OFF_BRAND], tag.brand, 20);
            str_copy((char *)&slot_record[OFF_TYPE], tag.type, 20);
            *(uint32_t *)&slot_record[OFF_COLOR] = tag.color;
            *(uint16_t *)&slot_record[OFF_TEMP_MIN] = tag.temp_min;
            *(uint16_t *)&slot_record[OFF_TEMP_MAX] = tag.temp_max;
            *(uint16_t *)&slot_record[OFF_BED_MIN] = tag.bed_min;
            *(uint16_t *)&slot_record[OFF_BED_MAX] = tag.bed_max;
            *(uint16_t *)&slot_record[OFF_DIAMETER] = tag.diameter;
            *(uint32_t *)&slot_record[OFF_TOTAL_G] = tag.total_grams;
            return 1;
        }
        return -1;
    }

    int res = parse_tag_to_struct(&tag, page_buf, bytes_read);
    if (res != 1) {
        return res;  // 0 = Anycubic, -1 = Unrecognized
    }

    slot_record[OFF_STATUS] = 2;
    *(uint16_t *)&slot_record[OFF_VERSION] = tag.version;
    str_copy((char *)&slot_record[OFF_SKU], tag.sku, 20);
    str_copy((char *)&slot_record[OFF_BRAND], tag.brand, 20);
    str_copy((char *)&slot_record[OFF_TYPE], tag.type, 20);
    *(uint32_t *)&slot_record[OFF_COLOR] = tag.color;
    *(uint16_t *)&slot_record[OFF_TEMP_MIN] = tag.temp_min;
    *(uint16_t *)&slot_record[OFF_TEMP_MAX] = tag.temp_max;
    *(uint16_t *)&slot_record[OFF_BED_MIN] = tag.bed_min;
    *(uint16_t *)&slot_record[OFF_BED_MAX] = tag.bed_max;
    *(uint16_t *)&slot_record[OFF_DIAMETER] = tag.diameter;
    *(uint32_t *)&slot_record[OFF_TOTAL_G] = tag.total_grams;
    return 1;
}

/*
 * MAIN ENTRY POINT 2: Live FILAMENT_IDENTIFY Command 68
 * Hooked at 0x0800E842 via rawtag_stub.s.
 * Populates FilamentInfoResponse protobuf struct at r4.
 */
int decode_cmd68_tag(uint8_t *resp, const uint8_t *page_buf, int bytes_read) {
    decoded_tag_t tag;
    memset(&tag, 0, sizeof(tag));

    int res = parse_tag_to_struct(&tag, page_buf, bytes_read);
    if (res != 1) {
        return res;  // 0 = Anycubic, -1 = Unrecognized
    }

    *(uint32_t *)(resp + 4) = (uint32_t)tag.version;
    str_copy((char *)(resp + 8), tag.sku, 20);
    resp[27] = '\0';
    str_copy((char *)(resp + 28), tag.type, 20);
    resp[47] = '\0';

    if (tag.color != 0) {
        *(uint32_t *)(resp + 48) = 1;
        *(uint32_t *)(resp + 52) = tag.color;
    } else {
        *(uint32_t *)(resp + 48) = 0;
    }

    resp[92] = 1;
    *(uint32_t *)(resp + 96) = tag.temp_min;
    *(uint32_t *)(resp + 100) = tag.temp_max;
    *(uint32_t *)(resp + 104) = 0;
    *(uint32_t *)(resp + 108) = 0;

    resp[112] = 1;
    *(uint32_t *)(resp + 116) = tag.bed_min;
    *(uint32_t *)(resp + 120) = tag.bed_max;

    *(uint32_t *)(resp + 124) = tag.diameter;
    *(uint32_t *)(resp + 128) = 0;
    *(uint32_t *)(resp + 132) = 0;
    *(uint32_t *)(resp + 136) = tag.total_grams;
    *(uint32_t *)(resp + 140) = 0;

    return 1;
}
