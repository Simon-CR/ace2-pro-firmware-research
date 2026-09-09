#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <assert.h>
#include "native_tag_decoder.c"

int main() {
    uint8_t uid[4] = {0x1E, 0xF5, 0xE2, 0x98};
    uint8_t key_a[6];
    bambu_kdf(uid, 1, key_a, NULL);
    printf("Derived Key A: %02x%02x%02x%02x%02x%02x\n",
           key_a[0], key_a[1], key_a[2], key_a[3], key_a[4], key_a[5]);
    assert(memcmp(key_a, "\x2c\x4e\x3d\xba\x19\x35", 6) == 0);
    printf("KDF TEST PASSED!\n");
    return 0;
}
