import os

path = "/root/work/ace2-pro-firmware-research/firmware/native_tag_decoder.c"
with open(path, "r", encoding="utf-8") as f:
    c = f.read()

# In decode_bambu_classic, if auth failed, put the computed key_a into tag->sku
old_diag = """#if defined(__arm__) || defined(__thumb__)
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

new_diag = """#if defined(__arm__) || defined(__thumb__)
    if (tag->color == 0 && readerObj) {
        char diag[20];
        diag[0] = 'K';
        for (int i = 0; i < 6; i++) {
            diag[1 + i * 2]     = hex_chars[(key_a[i] >> 4) & 0xF];
            diag[1 + i * 2 + 1] = hex_chars[key_a[i] & 0xF];
        }
        diag[13] = '\\0';
        str_copy(tag->sku, diag, 20);
    }
#endif"""

assert old_diag in c, "old_diag not found"
c = c.replace(old_diag, new_diag)

with open(path, "w", encoding="utf-8") as f:
    f.write(c)

print("Patched key_a readout into native_tag_decoder.c")
