#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <assert.h>

/* Include decoder source directly for unit testing on host */
#include "native_tag_decoder.c"

int main() {
    printf("--- Running Native RFID Decoder Unit Tests on Devbox ---\n");

    uint8_t slot[164];
    uint8_t page_buf[256];

    /* Test 1: Anycubic Native Tag */
    memset(slot, 0, sizeof(slot));
    memset(page_buf, 0, sizeof(page_buf));
    page_buf[0] = 0x7B; page_buf[1] = 0x00; page_buf[2] = 0x65; page_buf[3] = 0x00;
    int res1 = decode_native_tag(slot, page_buf, 144);
    assert(res1 == 0); /* Must return 0 so native Anycubic unpack runs */
    printf("[PASS] Test 1: Anycubic native passthrough verified (return 0)\n");

    /* Test 2: OpenSpool JSON */
    memset(slot, 0, sizeof(slot));
    memset(page_buf, 0, sizeof(page_buf));
    const char *openspool_payload = 
        "\x03\x75\xd1\x02\x71""application/json{"
        "\"sku\":\"SM99\","
        "\"brand\":\"Polymaker\","
        "\"material\":\"PolyLite PLA\","
        "\"color_hex\":\"#38BDF8\","
        "\"temp_min\":205,"
        "\"temp_max\":225,"
        "\"bed_min\":50,"
        "\"bed_max\":60"
        "}";
    memcpy(page_buf, openspool_payload, strlen(openspool_payload));
    int res2 = decode_native_tag(slot, page_buf, strlen(openspool_payload));
    assert(res2 == 1);
    assert(slot[OFF_STATUS] == 2);
    assert(strcmp((char *)&slot[OFF_SKU], "SM99") == 0);
    assert(strcmp((char *)&slot[OFF_BRAND], "Polymaker") == 0);
    assert(strcmp((char *)&slot[OFF_TYPE], "PolyLite PLA") == 0);
    assert(*(uint16_t *)&slot[OFF_TEMP_MIN] == 205);
    assert(*(uint16_t *)&slot[OFF_TEMP_MAX] == 225);
    assert(*(uint16_t *)&slot[OFF_BED_MIN] == 50);
    assert(*(uint16_t *)&slot[OFF_BED_MAX] == 60);
    uint32_t color2 = *(uint32_t *)&slot[OFF_COLOR];
    assert(((color2 >> 24) & 0xFF) == 0x38);
    assert(((color2 >> 16) & 0xFF) == 0xBD);
    assert(((color2 >> 8) & 0xFF) == 0xF8);
    printf("[PASS] Test 2: OpenSpool NDEF JSON verified (SKU=%s, Brand=%s, Material=%s, Nozzle=%d-%dC, Bed=%d-%dC)\n",
           &slot[OFF_SKU], &slot[OFF_BRAND], &slot[OFF_TYPE],
           *(uint16_t *)&slot[OFF_TEMP_MIN], *(uint16_t *)&slot[OFF_TEMP_MAX],
           *(uint16_t *)&slot[OFF_BED_MIN], *(uint16_t *)&slot[OFF_BED_MAX]);

    /* Test 3: FilaMan NFC payload with numeric sm_id */
    memset(slot, 0, sizeof(slot));
    memset(page_buf, 0, sizeof(page_buf));
    const char *filaman_payload = 
        "{\"filaman\":1,\"sm_id\":24,\"brand\":\"eSUN\",\"type\":\"PETG\",\"color\":\"#FF0055\"}";
    memcpy(page_buf, filaman_payload, strlen(filaman_payload));
    int res3 = decode_native_tag(slot, page_buf, 144);
    assert(res3 == 1);
    assert(slot[OFF_STATUS] == 2);
    assert(strcmp((char *)&slot[OFF_SKU], "SM24") == 0);
    assert(strcmp((char *)&slot[OFF_BRAND], "eSUN") == 0);
    assert(strcmp((char *)&slot[OFF_TYPE], "PETG") == 0);
    assert(*(uint16_t *)&slot[OFF_TEMP_MIN] == 230);
    assert(*(uint16_t *)&slot[OFF_TEMP_MAX] == 250);
    printf("[PASS] Test 3: FilaMan numeric sm_id verified (SKU=%s, Brand=%s, Material=%s, Nozzle=%d-%dC)\n",
           &slot[OFF_SKU], &slot[OFF_BRAND], &slot[OFF_TYPE],
           *(uint16_t *)&slot[OFF_TEMP_MIN], *(uint16_t *)&slot[OFF_TEMP_MAX]);

    /* Test 4: Prusament NFC */
    memset(slot, 0, sizeof(slot));
    memset(page_buf, 0, sizeof(page_buf));
    const char *prusa_payload = "Prusament PC Blend Jet Black #101010";
    memcpy(page_buf, prusa_payload, strlen(prusa_payload));
    int res4 = decode_native_tag(slot, page_buf, 144);
    assert(res4 == 1);
    assert(slot[OFF_STATUS] == 2);
    assert(strcmp((char *)&slot[OFF_BRAND], "Prusament") == 0);
    assert(strcmp((char *)&slot[OFF_TYPE], "PC Blend") == 0);
    assert(*(uint16_t *)&slot[OFF_TEMP_MIN] == 265);
    assert(*(uint16_t *)&slot[OFF_TEMP_MAX] == 285);
    assert(*(uint16_t *)&slot[OFF_BED_MIN] == 100);
    assert(*(uint16_t *)&slot[OFF_BED_MAX] == 115);
    printf("[PASS] Test 4: Prusament verified (Brand=%s, Material=%s, Nozzle=%d-%dC, Bed=%d-%dC)\n",
           &slot[OFF_BRAND], &slot[OFF_TYPE],
           *(uint16_t *)&slot[OFF_TEMP_MIN], *(uint16_t *)&slot[OFF_TEMP_MAX],
           *(uint16_t *)&slot[OFF_BED_MIN], *(uint16_t *)&slot[OFF_BED_MAX]);

    /* Test 5: Creality CFS */
    memset(slot, 0, sizeof(slot));
    memset(page_buf, 0, sizeof(page_buf));
    const char *creality_payload = "Creality Hyper PLA White #FFFFFF";
    memcpy(page_buf, creality_payload, strlen(creality_payload));
    int res5 = decode_native_tag(slot, page_buf, 144);
    assert(res5 == 1);
    assert(slot[OFF_STATUS] == 2);
    assert(strcmp((char *)&slot[OFF_BRAND], "Creality") == 0);
    assert(strcmp((char *)&slot[OFF_TYPE], "Hyper PLA") == 0);
    assert(*(uint16_t *)&slot[OFF_TEMP_MIN] == 190);
    assert(*(uint16_t *)&slot[OFF_TEMP_MAX] == 230);
    assert(*(uint16_t *)&slot[OFF_BED_MIN] == 45);
    assert(*(uint16_t *)&slot[OFF_BED_MAX] == 60);
    printf("[PASS] Test 5: Creality CFS verified (Brand=%s, Material=%s, Nozzle=%d-%dC, Bed=%d-%dC)\n",
           &slot[OFF_BRAND], &slot[OFF_TYPE],
           *(uint16_t *)&slot[OFF_TEMP_MIN], *(uint16_t *)&slot[OFF_TEMP_MAX],
           *(uint16_t *)&slot[OFF_BED_MIN], *(uint16_t *)&slot[OFF_BED_MAX]);

    /* Test 6: Bambu Lab MIFARE Classic UID */
    memset(slot, 0, sizeof(slot));
    memset(page_buf, 0, sizeof(page_buf));
    /* Real Bambu UID: 89 93 34 FC -> BCC = 0x89 ^ 0x93 ^ 0x34 ^ 0xFC = 0xD2 */
    slot[3] = 0x89;
    slot[4] = 0x93;
    slot[5] = 0x34;
    slot[6] = 0xFC;
    slot[7] = 0xD2;
    int res6 = decode_native_tag(slot, page_buf, 0); /* page read failed (0 bytes) */
    assert(res6 == 1);
    assert(slot[OFF_STATUS] == 2);
    assert(*(uint16_t *)&slot[OFF_VERSION] == 0x0102);
    assert(strcmp((char *)&slot[OFF_BRAND], "Bambu Lab") == 0);
    assert(strcmp((char *)&slot[OFF_SKU], "SM899334FC") == 0);
    assert(strcmp((char *)&slot[OFF_TYPE], "PLA Basic") == 0);
    printf("[PASS] Test 6: Bambu Lab MIFARE Classic verified (SKU=%s, Brand=%s, Material=%s)\n",
           &slot[OFF_SKU], &slot[OFF_BRAND], &slot[OFF_TYPE]);

    /* Test 7: CMD 68 UID fallback decoder */
    uint8_t resp[144];
    memset(resp, 0, sizeof(resp));
    uint8_t uid[7] = {0x1E, 0xF5, 0xE2, 0x98, 0x91, 0x00, 0x00};
    int res7 = decode_cmd68_uid_tag(resp, uid);
    assert(res7 == 1);
    assert(*(uint32_t *)(resp + 4) == 0x0102);
    assert(strcmp((char *)(resp + 8), "SM1EF5E298") == 0);
    assert(strcmp((char *)(resp + 28), "PLA Basic") == 0);
    assert(*(uint32_t *)(resp + 96) == 190);
    assert(*(uint32_t *)(resp + 100) == 230);
    assert(*(uint32_t *)(resp + 116) == 45);
    assert(*(uint32_t *)(resp + 120) == 60);
    assert(*(uint32_t *)(resp + 124) == 175);
    assert(*(uint32_t *)(resp + 136) == 1000);
    assert(*(uint32_t *)(resp + 140) == 0);
    printf("[PASS] Test 7: CMD 68 Bambu UID decoder verified (SKU=%s, Type=%s, Version=0x%04X)\n",
           (char *)(resp + 8), (char *)(resp + 28), *(uint32_t *)(resp + 4));

    printf("\n>>> ALL 7 TAG DECODER SUITES PASSED VERIFICATION <<<\n");
    return 0;
}
