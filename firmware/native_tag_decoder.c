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
 *   6. Bambu Lab (MIFARE Classic On-Chip Crypto-1 MFAuthent & Sector 1 extraction)
 *
 * Unified dual-path architecture:
 *   - decode_native_tag(slot_record, page_buf, bytes_read, ctx):
 *       Called from rawtag_extract_stub.s (0x0800FE36) on background spool rotation.
 *       Populates slot_record in MCU SRAM.
 *   - decode_cmd68_tag(resp, page_buf, bytes_read):
 *       Called from rawtag_stub.s (0x0800E842) on live FILAMENT_IDENTIFY command.
 *       Populates FilamentInfoResponse protobuf response struct at r4.
 *   - decode_cmd68_uid_tag(resp, uid, slot):
 *       Called from uid_stub.s (0x0800E7A8) on MIFARE Classic detection.
 *       Authenticates Sector 1 and populates FilamentInfoResponse response struct at r4.
 */

#if !defined(UINT8_MAX) && !defined(_STDINT_H) && !defined(_STDINT_H_)
typedef unsigned char uint8_t;
typedef unsigned short uint16_t;
typedef unsigned int uint32_t;
typedef unsigned long long uint64_t;
#endif
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

void *memcpy(void *dst, const void *src, unsigned int n) {
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    while (n--) *d++ = *s++;
    return dst;
}
#endif

/* Hardware RC522 Reader Primitives from stock firmware */
#if defined(__arm__) || defined(__thumb__)
extern uint8_t rc522_read_reg(void *readerObj, uint8_t reg);
extern void rc522_write_reg(void *readerObj, uint8_t reg, uint8_t val);
extern int rc522_transceive(void *readerObj, uint8_t cmd, const uint8_t *tx_buf, uint32_t tx_len, uint8_t *rx_buf, uint32_t *rx_bits);
extern void rc522_timer(void *readerObj, uint32_t ms);
extern void rc522_flush(void *readerObj);
extern int rfid_select(void *ctx, uint8_t *buf, int mode);

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

/* --- Zero-Stack Freestanding SHA-256 and HMAC-SHA256 --- */

typedef struct {
    uint32_t state[8];
    uint32_t m[16];
    uint8_t  block[64];
    uint8_t  prk[32];
    uint8_t  out[32];
} kdf_scratch_t;

#if defined(__arm__) || defined(__thumb__)
#define SCRATCH ((kdf_scratch_t *)0x20000704)
#else
static kdf_scratch_t s_scratch_host;
#define SCRATCH (&s_scratch_host)
#endif

#define ROR32(x, n) (((x) >> (n)) | ((x) << (32 - (n))))

static const uint32_t K256[64] = {
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
};

static void sha256_transform_block(const uint8_t *data) {
    uint32_t *m = SCRATCH->m;
    uint32_t *state = SCRATCH->state;

    for (int i = 0, j = 0; i < 16; i++, j += 4) {
        m[i] = ((uint32_t)data[j] << 24) |
               ((uint32_t)data[j + 1] << 16) |
               ((uint32_t)data[j + 2] << 8) |
               ((uint32_t)data[j + 3]);
    }

    uint32_t a = state[0], b = state[1], c = state[2], d = state[3];
    uint32_t e = state[4], f = state[5], g = state[6], h = state[7];

    for (int i = 0; i < 64; i++) {
        if (i >= 16) {
            uint32_t s0 = ROR32(m[(i + 1) & 15], 7) ^ ROR32(m[(i + 1) & 15], 18) ^ (m[(i + 1) & 15] >> 3);
            uint32_t s1 = ROR32(m[(i + 14) & 15], 17) ^ ROR32(m[(i + 14) & 15], 19) ^ (m[(i + 14) & 15] >> 10);
            m[i & 15] = m[i & 15] + s0 + m[(i + 9) & 15] + s1;
        }
        uint32_t s1_val = ROR32(e, 6) ^ ROR32(e, 11) ^ ROR32(e, 25);
        uint32_t ch = (e & f) ^ (~e & g);
        uint32_t t1 = h + s1_val + ch + K256[i] + m[i & 15];
        uint32_t s0_val = ROR32(a, 2) ^ ROR32(a, 13) ^ ROR32(a, 22);
        uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
        uint32_t t2 = s0_val + maj;

        h = g; g = f; f = e; e = d + t1;
        d = c; c = b; b = a; a = t1 + t2;
    }

    state[0] += a; state[1] += b; state[2] += c; state[3] += d;
    state[4] += e; state[5] += f; state[6] += g; state[7] += h;
}

static void hmac_sha256_short(const uint8_t *key, uint32_t key_len,
                              const uint8_t *msg, uint32_t msg_len,
                              uint8_t out[32]) {
    uint8_t *block = SCRATCH->block;
    uint32_t *state = SCRATCH->state;

    // --- Inner Hash ---
    state[0] = 0x6a09e667; state[1] = 0xbb67ae85; state[2] = 0x3c6ef372; state[3] = 0xa54ff53a;
    state[4] = 0x510e527f; state[5] = 0x9b05688c; state[6] = 0x1f83d9ab; state[7] = 0x5be0cd19;

    for (uint32_t i = 0; i < 64; i++) {
        uint8_t k = (i < key_len) ? key[i] : 0;
        block[i] = k ^ 0x36;
    }
    sha256_transform_block(block);

    memset(block, 0, 64);
    for (uint32_t i = 0; i < msg_len; i++) block[i] = msg[i];
    block[msg_len] = 0x80;
    uint32_t total_bits = (64 + msg_len) * 8;
    block[62] = (uint8_t)(total_bits >> 8);
    block[63] = (uint8_t)(total_bits);
    sha256_transform_block(block);

    for (int i = 0; i < 8; i++) {
        SCRATCH->out[i * 4]     = (uint8_t)(state[i] >> 24);
        SCRATCH->out[i * 4 + 1] = (uint8_t)(state[i] >> 16);
        SCRATCH->out[i * 4 + 2] = (uint8_t)(state[i] >> 8);
        SCRATCH->out[i * 4 + 3] = (uint8_t)(state[i]);
    }

    // --- Outer Hash ---
    state[0] = 0x6a09e667; state[1] = 0xbb67ae85; state[2] = 0x3c6ef372; state[3] = 0xa54ff53a;
    state[4] = 0x510e527f; state[5] = 0x9b05688c; state[6] = 0x1f83d9ab; state[7] = 0x5be0cd19;

    for (uint32_t i = 0; i < 64; i++) {
        uint8_t k = (i < key_len) ? key[i] : 0;
        block[i] = k ^ 0x5c;
    }
    sha256_transform_block(block);

    memset(block, 0, 64);
    memcpy(block, SCRATCH->out, 32);
    block[32] = 0x80;
    block[62] = 0x03;
    block[63] = 0x00;
    sha256_transform_block(block);

    for (int i = 0; i < 8; i++) {
        out[i * 4]     = (uint8_t)(state[i] >> 24);
        out[i * 4 + 1] = (uint8_t)(state[i] >> 16);
        out[i * 4 + 2] = (uint8_t)(state[i] >> 8);
        out[i * 4 + 3] = (uint8_t)(state[i]);
    }
}

static const uint8_t BAMBU_SALT[16] = {
    0x9a, 0x75, 0x9c, 0xf2, 0xc4, 0xf7, 0xca, 0xff,
    0x22, 0x2c, 0xb9, 0x76, 0x9b, 0x41, 0xbc, 0x96
};

static void bambu_kdf(const uint8_t *uid, uint8_t sector, uint8_t *key_a, uint8_t *key_b) {
    uint8_t *prk = SCRATCH->prk;
    hmac_sha256_short(BAMBU_SALT, 16, uid, 4, prk);

    if (key_a) {
        uint8_t info_a[8] = {'R', 'F', 'I', 'D', '-', 'A', 0, 0x01};
        uint8_t t1[32];
        hmac_sha256_short(prk, 32, info_a, 8, t1);
        if (sector == 0) {
            memcpy(key_a, &t1[0], 6);
        } else if (sector == 1) {
            memcpy(key_a, &t1[6], 6);
        } else if (sector == 2) {
            memcpy(key_a, &t1[12], 6);
        } else if (sector == 3) {
            memcpy(key_a, &t1[18], 6);
        } else if (sector == 4) {
            memcpy(key_a, &t1[24], 6);
        }
    }
    if (key_b) {
        uint8_t info_b[8] = {'R', 'F', 'I', 'D', '-', 'B', 0, 0x01};
        uint8_t t1[32];
        hmac_sha256_short(prk, 32, info_b, 8, t1);
        if (sector == 0) {
            memcpy(key_b, &t1[0], 6);
        } else if (sector == 1) {
            memcpy(key_b, &t1[6], 6);
        } else if (sector == 2) {
            memcpy(key_b, &t1[12], 6);
        } else if (sector == 3) {
            memcpy(key_b, &t1[18], 6);
        } else if (sector == 4) {
            memcpy(key_b, &t1[24], 6);
        }
    }
}

/* Bambu catalog for fast on-chip material and temp lookup */
typedef struct {
    const char code[6];
    const char material[20];
    uint16_t tmin;
    uint16_t tmax;
    uint16_t bmin;
    uint16_t bmax;
} bambu_catalog_t;

static const bambu_catalog_t BAMBU_CATALOG[] = {
    {"GFA00", "PLA Basic",        190, 230, 45, 60},
    {"GFA01", "PLA Matte",        190, 230, 45, 60},
    {"GFA02", "PLA Metal",        190, 230, 45, 60},
    {"GFA03", "PLA Silk",         200, 240, 45, 60},
    {"GFA05", "PLA Tough",        190, 230, 45, 60},
    {"GFA07", "PLA Galaxy",       190, 230, 45, 60},
    {"GFA08", "PLA Aero",         220, 250, 45, 60},
    {"GFA09", "PLA Marble",       190, 230, 45, 60},
    {"GFA50", "PLA-CF",           210, 240, 45, 60},
    {"GFG00", "PETG Basic",       230, 260, 70, 80},
    {"GFG01", "PETG Translucent", 230, 260, 70, 80},
    {"GFG50", "PETG-CF",          240, 270, 70, 85},
    {"GFB00", "ABS",              240, 270, 90, 100},
    {"GFB01", "ASA",              250, 280, 90, 100},
    {"GFB02", "PC",               260, 290, 90, 110},
    {"GFS00", "TPU 95A",          220, 240, 35, 50},
    {"GFS01", "TPU 95A HF",       220, 240, 35, 50},
    {"GFN03", "PA6-CF",           280, 300, 90, 110},
    {"GFN05", "PAHT-CF",          280, 300, 90, 110},
    {"GFU01", "Support for PLA",  190, 230, 45, 60},
    {"GFU02", "Support for PA",   260, 280, 80, 90},
};
#define BAMBU_CATALOG_SIZE (sizeof(BAMBU_CATALOG) / sizeof(BAMBU_CATALOG[0]))

/* RC522 Reader Context Helpers */
static void *get_reader_obj_for_slot(int slot) {
#if defined(__arm__) || defined(__thumb__)
    uint32_t addr = (slot >= 2) ? 0x20001608 : 0x20001604;
    void *ctx = *(void **)addr;
    if (!ctx) return (void *)0;
    return *(void **)((uint8_t *)ctx + 4);
#else
    (void)slot;
    return (void *)0;
#endif
}

static void *get_reader_obj_from_ctx(void *ctx) {
#if defined(__arm__) || defined(__thumb__)
    if (!ctx) return (void *)0;
    return *(void **)((uint8_t *)ctx + 4);
#else
    (void)ctx;
    return (void *)0;
#endif
}

/* Freestanding Cortex-M3 cycle-based delay (72MHz STM32F103: ~12000 cycles per ms) */
void delay_ms(uint32_t ms) {
    while (ms--) {
        for (volatile uint32_t i = 0; i < 12000; i++) {
            __asm__ volatile ("nop");
        }
    }
}

static void ensure_reader_power(void *readerObj) {
#if defined(__arm__) || defined(__thumb__)
    if (!readerObj) return;
    uint32_t *gpio_port = *(uint32_t **)((uint8_t *)readerObj + 44);
    uint32_t gpio_val = *(uint32_t *)((uint8_t *)readerObj + 48);
    if (gpio_port) {
        gpio_port[4] = gpio_val; // str r0, [r1, #16] (BSRR)
        delay_ms(20);
    }
#else
    (void)readerObj;
#endif
}

static uint32_t g_auth_diag = 0;
static uint32_t g_read_diag = 0;

static int rc522_mf_authent(void *readerObj, uint8_t auth_mode, uint8_t block_addr, const uint8_t *key, const uint8_t *uid) {
#if defined(__arm__) || defined(__thumb__)
    if (!readerObj) return 0;

    // 1. Force Idle and clear IRQ / Crypto
    rc522_write_reg(readerObj, 0x01, 0x00);                // CommandReg = Idle
    rc522_write_reg(readerObj, 0x08, 0x00);                // Status2Reg: MFCrypto1On = 0
    rc522_write_reg(readerObj, 0x02, 0xF7);                // ComIEnReg = 0xF7 (enable IRQs)
    rc522_write_reg(readerObj, 0x04, 0x7F);                // Clear ComIrqReg
    rc522_flush(readerObj);                                // Flush FIFO

    // 2. Clear CRC in TxMode and RxMode (MFAuthent hardware handles parity & CRC internally)
    rc522_write_reg(readerObj, 0x12, 0x00);                // TxModeReg: TxCRCEn=0, 106kbd
    rc522_write_reg(readerObj, 0x13, 0x00);                // RxModeReg: RxCRCEn=0, 106kbd
    rc522_write_reg(readerObj, 0x0D, 0x00);                // BitFramingReg = 0

    // 3. Configure timer (25ms)
    rc522_timer(readerObj, 25);
    rc522_write_reg(readerObj, 0x2A, 0x80);                // TModeReg: TAuto=1

    // 4. Load 12-byte authentication sequence into FIFO
    rc522_write_reg(readerObj, 0x09, auth_mode);
    rc522_write_reg(readerObj, 0x09, block_addr);
    for (int i = 0; i < 6; i++) rc522_write_reg(readerObj, 0x09, key[i]);
    for (int i = 0; i < 4; i++) rc522_write_reg(readerObj, 0x09, uid[i]);

    // 5. Execute PCD_MFAuthent (0x0E)
    rc522_write_reg(readerObj, 0x01, 0x0E);

    int success = 0;
    for (int timeout = 0; timeout < 35; timeout++) {
        uint8_t s2 = rc522_read_reg(readerObj, 0x08);
        if ((s2 & 0x08) != 0) {
            success = 1; // MFCrypto1On is set! Authentication passed!
            break;
        }
        uint8_t irq = rc522_read_reg(readerObj, 0x04);
        if ((irq & 0x11) != 0) {
            break; // IdleIRq or TimerIRq fired
        }
        delay_ms(1);
    }

    uint8_t s2 = rc522_read_reg(readerObj, 0x08);
    uint8_t irq = rc522_read_reg(readerObj, 0x04);
    uint8_t err = rc522_read_reg(readerObj, 0x06);
    uint8_t fifo = rc522_read_reg(readerObj, 0x0A);
    g_auth_diag = ((uint32_t)s2 << 24) | ((uint32_t)irq << 16) | ((uint32_t)err << 8) | (uint32_t)fifo;

    return success;
#else
    (void)readerObj; (void)auth_mode; (void)block_addr; (void)key; (void)uid;
    return 1;
#endif
}

static int rc522_read_block(void *readerObj, uint8_t block_addr, uint8_t *out_16b) {
#if defined(__arm__) || defined(__thumb__)
    if (!readerObj || !out_16b) return 0;
    uint8_t tx_mode = rc522_read_reg(readerObj, 0x12);
    uint8_t rx_mode = rc522_read_reg(readerObj, 0x13);
    rc522_write_reg(readerObj, 0x12, tx_mode | 0x80); // TxCRCEn
    rc522_write_reg(readerObj, 0x13, rx_mode | 0x80); // RxCRCEn
    rc522_timer(readerObj, 10);
    rc522_flush(readerObj);

    uint8_t cmd[2] = {0x30, block_addr};
    uint32_t rx_bits = 0;
    int res = rc522_transceive(readerObj, 0x0C, cmd, 2, out_16b, &rx_bits);
    return ((res == 2 || res == 0) && rx_bits == 128);
#else
    (void)readerObj; (void)block_addr; (void)out_16b;
    return 0;
#endif
}

static void rc522_auth_stop(void *readerObj) {
#if defined(__arm__) || defined(__thumb__)
    if (!readerObj) return;
    rc522_write_reg(readerObj, 0x01, 0x00); // CommandReg = Idle
    uint8_t s2 = rc522_read_reg(readerObj, 0x08);
    rc522_write_reg(readerObj, 0x08, s2 & ~0x08); // MFCrypto1On = 0
    uint8_t tx_mode = rc522_read_reg(readerObj, 0x12);
    uint8_t rx_mode = rc522_read_reg(readerObj, 0x13);
    rc522_write_reg(readerObj, 0x12, tx_mode & ~0x80); // TxCRCEn = 0
    rc522_write_reg(readerObj, 0x13, rx_mode & ~0x80); // RxCRCEn = 0
    rc522_flush(readerObj);
#else
    (void)readerObj;
#endif
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
 * Reads UID/BCC, executes on-chip HMAC-SHA256 KDF for Sector 1 Key A,
 * and if readerObj is active, performs hardware RC522 PCD_MFAuthent to unlock
 * Sector 1 and extract Block 4 (tray/material) and Block 5 (RGBA color, diameter, temps).
 */
static int decode_bambu_classic(decoded_tag_t *tag, const uint8_t *uid, void *readerObj, int sel_res) {
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
    tag->version = 0x0102;

    if (!readerObj) {
        return 1;
    }

    uint8_t key_a[6];
    bambu_kdf(uid, 1, key_a, (uint8_t *)0);

    int auth_ok = rc522_mf_authent(readerObj, 0x60, 4, key_a, uid);
    if (!auth_ok) {
        auth_ok = rc522_mf_authent(readerObj, 0x60, 7, key_a, uid);
    }
    if (auth_ok) {
        uint8_t block4[16];
        uint8_t block5[16];

        if (rc522_read_block(readerObj, 4, block4)) {
            int matched = 0;
            if (block4[0] == 'G' && block4[1] == 'F') {
                for (int c = 0; c < (int)BAMBU_CATALOG_SIZE; c++) {
                    if (block4[2] == BAMBU_CATALOG[c].code[2] &&
                        block4[3] == BAMBU_CATALOG[c].code[3] &&
                        block4[4] == BAMBU_CATALOG[c].code[4]) {
                        str_copy(tag->type, BAMBU_CATALOG[c].material, 20);
                        tag->temp_min = BAMBU_CATALOG[c].tmin;
                        tag->temp_max = BAMBU_CATALOG[c].tmax;
                        tag->bed_min = BAMBU_CATALOG[c].bmin;
                        tag->bed_max = BAMBU_CATALOG[c].bmax;
                        matched = 1;
                        break;
                    }
                }
            }
            if (!matched) {
                int printable = 0;
                for (int i = 0; i < 16; i++) {
                    if ((block4[i] >= 'A' && block4[i] <= 'Z') ||
                        (block4[i] >= 'a' && block4[i] <= 'z') ||
                        (block4[i] >= '0' && block4[i] <= '9') ||
                        block4[i] == '-' || block4[i] == ' ') {
                        printable++;
                    } else if (block4[i] == 0) break;
                }
                if (printable >= 3) {
                    char mat_buf[17];
                    int k = 0;
                    while (k < 16 && block4[k] >= 0x20 && block4[k] <= 0x7E) {
                        mat_buf[k] = (char)block4[k];
                        k++;
                    }
                    mat_buf[k] = '\0';
                    if (k >= 3) str_copy(tag->type, mat_buf, 20);
                }
            }
        }

        if (rc522_read_block(readerObj, 5, block5)) {
            uint8_t r = block5[2];
            uint8_t g = block5[3];
            uint8_t b = block5[4];
            uint8_t a = block5[5];
            if ((r | g | b) != 0 || a != 0) {
                tag->color = ((uint32_t)r << 24) | ((uint32_t)g << 16) | ((uint32_t)b << 8) | 0xFF;
            }

            uint16_t w = ((uint16_t)block5[0] << 8) | block5[1];
            if (w < 200 || w > 5000) w = ((uint16_t)block5[1] << 8) | block5[0];
            if (w >= 200 && w <= 5000) tag->total_grams = w;

            uint16_t diam = ((uint16_t)block5[6] << 8) | block5[7];
            if (diam < 100 || diam > 300) diam = ((uint16_t)block5[7] << 8) | block5[6];
            if (diam >= 100 && diam <= 300) tag->diameter = diam;

            uint16_t tmin = ((uint16_t)block5[8] << 8) | block5[9];
            if (tmin < 150 || tmin > 350) tmin = ((uint16_t)block5[9] << 8) | block5[8];
            if (tmin >= 150 && tmin <= 350) tag->temp_min = tmin;

            uint16_t tmax = ((uint16_t)block5[10] << 8) | block5[11];
            if (tmax < 150 || tmax > 350) tmax = ((uint16_t)block5[11] << 8) | block5[10];
            if (tmax >= 150 && tmax <= 350) tag->temp_max = tmax;

            uint16_t bmin = ((uint16_t)block5[12] << 8) | block5[13];
            if (bmin < 20 || bmin > 150) bmin = ((uint16_t)block5[13] << 8) | block5[12];
            if (bmin >= 20 && bmin <= 150) tag->bed_min = bmin;

            uint16_t bmax = ((uint16_t)block5[14] << 8) | block5[15];
            if (bmax < 20 || bmax > 150) bmax = ((uint16_t)block5[15] << 8) | block5[14];
            if (bmax >= 20 && bmax <= 150) tag->bed_max = bmax;
        }

        rc522_auth_stop(readerObj);
    }

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
int decode_native_tag(uint8_t *slot_record, const uint8_t *page_buf, int bytes_read, void *ctx) {
#if defined(__arm__) || defined(__thumb__)
    if ((uintptr_t)slot_record >= 0x20001748) {
        return -1;
    }
#endif

    decoded_tag_t tag;
    memset(&tag, 0, sizeof(tag));

    void *readerObj = get_reader_obj_from_ctx(ctx);

    if (bytes_read < 140) {
#if defined(__arm__) || defined(__thumb__)
        if (ctx && readerObj) {
            ensure_reader_power(readerObj);
            __attribute__((aligned(4))) uint8_t select_buf[64];
            rfid_select(ctx, select_buf, 1);
        }
#endif
        if (decode_bambu_classic(&tag, &slot_record[3], readerObj, 0)) {
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

/*
 * MAIN ENTRY POINT 3: Live CMD 68 UID fallback decoder
 * Hooked from uid_stub.s at 0x0800E7A8 when page read fails (MIFARE Classic).
 * Populates FilamentInfoResponse struct at r4 if recognized as Bambu Lab.
 */
int decode_cmd68_uid_tag(uint8_t *resp, const uint8_t *uid, int slot) {
    decoded_tag_t tag;
    memset(&tag, 0, sizeof(tag));

#if defined(__arm__) || defined(__thumb__)
    void *ctx = (slot >= 2) ? *(void **)0x20001608 : *(void **)0x20001604;
    void *readerObj = ctx ? *(void **)((uint8_t *)ctx + 4) : (void *)0;

    int sel_res = -1;
    if (ctx && readerObj) {
        ensure_reader_power(readerObj);
        __attribute__((aligned(4))) uint8_t select_buf[64];
        sel_res = rfid_select(ctx, select_buf, 1);
    }
#else
    void *readerObj = (void *)0;
    int sel_res = -1;
    (void)slot;
#endif

    if (decode_bambu_classic(&tag, uid, readerObj, sel_res)) {
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
    return 0;
}
