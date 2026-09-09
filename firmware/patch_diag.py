import os

path = "/root/work/ace2-pro-firmware-research/firmware/native_tag_decoder.c"
with open(path, "r", encoding="utf-8") as f:
    c = f.read()

# Replace rc522_mf_authent to preserve registers on exit (don't write CommandReg=Idle before checking)
old_auth_cleanup = """    if (!success) {
        rc522_write_reg(readerObj, 0x01, 0x00);
        rc522_write_reg(readerObj, 0x0A, 0x80);
    }
    return success;"""

new_auth_cleanup = """    return success;"""
assert old_auth_cleanup in c, "old_auth_cleanup not found"
c = c.replace(old_auth_cleanup, new_auth_cleanup)

# Update decode_bambu_classic signature to accept sel_res
old_bambu_sig = "static int decode_bambu_classic(decoded_tag_t *tag, const uint8_t *uid, void *readerObj) {"
new_bambu_sig = "static int decode_bambu_classic(decoded_tag_t *tag, const uint8_t *uid, void *readerObj, int sel_res) {"
assert old_bambu_sig in c, "old_bambu_sig not found"
c = c.replace(old_bambu_sig, new_bambu_sig)

# Update diagnostic formatting in decode_bambu_classic
old_diag = """#if defined(__arm__) || defined(__thumb__)
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
#endif"""

new_diag = """#if defined(__arm__) || defined(__thumb__)
    if (tag->color == 0 && readerObj) {
        uint8_t irq = rc522_read_reg(readerObj, 0x04);
        uint8_t s2 = rc522_read_reg(readerObj, 0x08);
        uint8_t err = rc522_read_reg(readerObj, 0x06);
        uint8_t fifo = rc522_read_reg(readerObj, 0x0A);
        char diag[20];
        diag[0] = 'D';
        diag[1] = (sel_res == 0) ? '0' : ((sel_res > 0 && sel_res < 10) ? ('0' + sel_res) : 'X');
        diag[2] = auth_ok ? 'A' : 'F';
        diag[3] = '_';
        diag[4] = hex_chars[(irq >> 4) & 0xF];
        diag[5] = hex_chars[irq & 0xF];
        diag[6] = hex_chars[(s2 >> 4) & 0xF];
        diag[7] = hex_chars[s2 & 0xF];
        diag[8] = '_';
        diag[9] = hex_chars[(err >> 4) & 0xF];
        diag[10] = hex_chars[err & 0xF];
        diag[11] = hex_chars[(fifo >> 4) & 0xF];
        diag[12] = hex_chars[fifo & 0xF];
        diag[13] = '\\0';
        str_copy(tag->sku, diag, 20);
    }
#endif"""
assert old_diag in c, "old_diag not found"
c = c.replace(old_diag, new_diag)

# Update caller in decode_native_tag
old_native_call = """        if (decode_bambu_classic(&tag, &slot_record[3], readerObj)) {"""
new_native_call = """        if (decode_bambu_classic(&tag, &slot_record[3], readerObj, 0)) {"""
assert old_native_call in c, "old_native_call not found"
c = c.replace(old_native_call, new_native_call)

# Update caller in decode_cmd68_uid_tag
old_cmd68_call = """    if (ctx && readerObj) {
        ensure_reader_power(readerObj);
        __attribute__((aligned(4))) uint8_t select_buf[64];
        rfid_select(ctx, select_buf, 1);
    }
#else
    void *readerObj = (void *)0;
    (void)slot;
#endif

    if (decode_bambu_classic(&tag, uid, readerObj)) {"""

new_cmd68_call = """    int sel_res = -1;
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

    if (decode_bambu_classic(&tag, uid, readerObj, sel_res)) {"""
assert old_cmd68_call in c, "old_cmd68_call not found"
c = c.replace(old_cmd68_call, new_cmd68_call)

with open(path, "w", encoding="utf-8") as f:
    f.write(c)

print("Applied diagnostic patch to native_tag_decoder.c")
