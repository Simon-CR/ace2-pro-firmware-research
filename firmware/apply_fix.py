import os

path = "/root/work/ace2-pro-firmware-research/firmware/native_tag_decoder.c"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Replace rfid_reset_antenna with ensure_reader_power
old_antenna = """static void rfid_reset_antenna(void *readerObj) {
#if defined(__arm__) || defined(__thumb__)
    if (!readerObj) return;
    rc522_write_reg(readerObj, 0x14, 0x00); // disable Tx1/Tx2 RF output
    delay_ms(3);
    rc522_write_reg(readerObj, 0x14, 0x03); // enable Tx1/Tx2 RF output
    delay_ms(3);
#else
    (void)readerObj;
#endif
}"""

new_power = """static void ensure_reader_power(void *readerObj) {
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
}"""

assert old_antenna in content, "old_antenna not found in native_tag_decoder.c"
content = content.replace(old_antenna, new_power)

# 2. Update rc522_mf_authent timer
old_auth = """    rc522_write_reg(readerObj, 0x01, 0x00);                // CommandReg = Idle
    rc522_write_reg(readerObj, 0x02, 0x00);                // ComIEnReg: disable hardware IRQ pin
    rc522_write_reg(readerObj, 0x04, 0x7F);                // Clear ComIrqReg
    rc522_write_reg(readerObj, 0x0A, 0x80);                // FIFOLevelReg = Flush

    rc522_write_reg(readerObj, 0x09, auth_mode);"""

new_auth = """    rc522_write_reg(readerObj, 0x01, 0x00);                // CommandReg = Idle
    rc522_write_reg(readerObj, 0x02, 0x00);                // ComIEnReg: disable hardware IRQ pin
    rc522_write_reg(readerObj, 0x04, 0x7F);                // Clear ComIrqReg
    rc522_write_reg(readerObj, 0x0A, 0x80);                // FIFOLevelReg = Flush

    rc522_timer(readerObj, 25);                            // 25ms timeout

    rc522_write_reg(readerObj, 0x09, auth_mode);"""

assert old_auth in content, "old_auth not found"
content = content.replace(old_auth, new_auth)

# 3. Update timeout loop in rc522_mf_authent
old_loop = "for (int timeout = 0; timeout < 30; timeout++) {"
new_loop = "for (int timeout = 0; timeout < 40; timeout++) {"
assert old_loop in content, "old_loop not found"
content = content.replace(old_loop, new_loop, 1)

# 4. Update rc522_read_block return condition
old_read_block = """    uint8_t cmd[2] = {0x30, block_addr};
    uint32_t rx_bits = 0;
    int res = rc522_transceive(readerObj, 0x0C, cmd, 2, out_16b, &rx_bits);
    return (res == 0 && rx_bits == 128);"""

new_read_block = """    uint8_t cmd[2] = {0x30, block_addr};
    uint32_t rx_bits = 0;
    int res = rc522_transceive(readerObj, 0x0C, cmd, 2, out_16b, &rx_bits);
    return ((res == 2 || res == 0) && rx_bits == 128);"""

assert old_read_block in content, "old_read_block not found"
content = content.replace(old_read_block, new_read_block)

# 5. In decode_bambu_classic, add diagnostic SKU and check auth_ok
old_bambu_auth = """    uint8_t key_a[6];
    bambu_kdf(uid, 1, key_a, (uint8_t *)0);

    if (rc522_mf_authent(readerObj, 0x60, 4, key_a, uid)) {"""

new_bambu_auth = """    uint8_t key_a[6];
    bambu_kdf(uid, 1, key_a, (uint8_t *)0);

    int auth_ok = rc522_mf_authent(readerObj, 0x60, 4, key_a, uid);
    if (auth_ok) {"""

assert old_bambu_auth in content, "old_bambu_auth not found"
content = content.replace(old_bambu_auth, new_bambu_auth)

old_auth_stop = """        rc522_auth_stop(readerObj);
    }

    return 1;"""

new_auth_stop = """        rc522_auth_stop(readerObj);
    }
#if defined(__arm__) || defined(__thumb__)
    if (tag->color == 0 && readerObj) {
        uint8_t s2 = rc522_read_reg(readerObj, 0x08);
        uint8_t err = rc522_read_reg(readerObj, 0x06);
        char diag[20];
        diag[0] = 'D';
        diag[1] = auth_ok ? 'A' : 'F';
        diag[2] = '_';
        diag[3] = hex_chars[(s2 >> 4) & 0xF];
        diag[4] = hex_chars[s2 & 0xF];
        diag[5] = '_';
        diag[6] = hex_chars[(err >> 4) & 0xF];
        diag[7] = hex_chars[err & 0xF];
        diag[8] = '\\0';
        str_copy(tag->sku, diag, 20);
    }
#endif

    return 1;"""

assert old_auth_stop in content, "old_auth_stop not found"
content = content.replace(old_auth_stop, new_auth_stop)

# 6. In decode_native_tag, update select call
old_native_select = """#if defined(__arm__) || defined(__thumb__)
        if (ctx && readerObj) {
            rfid_reset_antenna(readerObj);
            uint8_t select_buf[32];
            rfid_select(ctx, select_buf, 0);
        }
#endif"""

new_native_select = """#if defined(__arm__) || defined(__thumb__)
        if (ctx && readerObj) {
            ensure_reader_power(readerObj);
            __attribute__((aligned(4))) uint8_t select_buf[64];
            rfid_select(ctx, select_buf, 1);
        }
#endif"""

assert old_native_select in content, "old_native_select not found"
content = content.replace(old_native_select, new_native_select)

# 7. In decode_cmd68_uid_tag, update select call
old_cmd68_select = """#if defined(__arm__) || defined(__thumb__)
    void *ctx = (slot >= 2) ? *(void **)0x20001608 : *(void **)0x20001604;
    void *readerObj = ctx ? *(void **)((uint8_t *)ctx + 4) : (void *)0;

    if (ctx && readerObj) {
        rfid_reset_antenna(readerObj);
        uint8_t select_buf[32];
        rfid_select(ctx, select_buf, 0);
    }
#else
    void *readerObj = (void *)0;
#endif"""

new_cmd68_select = """#if defined(__arm__) || defined(__thumb__)
    void *ctx = (slot >= 2) ? *(void **)0x20001608 : *(void **)0x20001604;
    void *readerObj = ctx ? *(void **)((uint8_t *)ctx + 4) : (void *)0;

    if (ctx && readerObj) {
        ensure_reader_power(readerObj);
        __attribute__((aligned(4))) uint8_t select_buf[64];
        rfid_select(ctx, select_buf, 1);
    }
#else
    void *readerObj = (void *)0;
    (void)slot;
#endif"""

assert old_cmd68_select in content, "old_cmd68_select not found"
content = content.replace(old_cmd68_select, new_cmd68_select)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Successfully updated native_tag_decoder.c")

# Also update build_patch.py version to V1.1.52O
bp_path = "/root/work/ace2-pro-firmware-research/firmware/build_patch.py"
with open(bp_path, "r", encoding="utf-8") as f:
    bp = f.read()

bp = bp.replace('VERSION_STRING = b"V1.1.51O\\x00"', 'VERSION_STRING = b"V1.1.52O\\x00"')
bp = bp.replace('default="ACE2-Open-V1.1.51O.bin"', 'default="ACE2-Open-V1.1.52O.bin"')
with open(bp_path, "w", encoding="utf-8") as f:
    f.write(bp)
print("Successfully updated build_patch.py to V1.1.52O")
