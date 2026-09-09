import os

path = "/root/work/ace2-pro-firmware-research/firmware/native_tag_decoder.c"
with open(path, "r", encoding="utf-8") as f:
    c = f.read()

# In rc522_mf_authent, add StartSend assertion on BitFramingReg (0x0D)
old_authent_body = """    rc522_write_reg(readerObj, 0x01, 0x0E); // PCD_MFAuthent

    int success = 0;
    for (int timeout = 0; timeout < 40; timeout++) {
        uint8_t status2 = rc522_read_reg(readerObj, 0x08);
        if ((status2 & 0x08) != 0) {
            success = 1; // MFCrypto1On is set! Authentication passed!
            break;
        }
        delay_ms(1);
    }
    return success;"""

new_authent_body = """    rc522_write_reg(readerObj, 0x01, 0x0E); // PCD_MFAuthent

    // Assert StartSend in BitFramingReg (0x0D) to trigger RF transmission
    uint8_t bf = rc522_read_reg(readerObj, 0x0D);
    rc522_write_reg(readerObj, 0x0D, bf | 0x80);

    int success = 0;
    for (int timeout = 0; timeout < 40; timeout++) {
        uint8_t status2 = rc522_read_reg(readerObj, 0x08);
        if ((status2 & 0x08) != 0) {
            success = 1; // MFCrypto1On is set! Authentication passed!
            break;
        }
        delay_ms(1);
    }

    // Clear StartSend
    bf = rc522_read_reg(readerObj, 0x0D);
    rc522_write_reg(readerObj, 0x0D, bf & ~0x80);

    return success;"""

assert old_authent_body in c, "old_authent_body not found"
c = c.replace(old_authent_body, new_authent_body)

# In decode_bambu_classic, try block 4, and if that fails, try trailer block 7
old_call = "int auth_ok = rc522_mf_authent(readerObj, 0x60, 4, key_a, uid);"
new_call = """int auth_ok = rc522_mf_authent(readerObj, 0x60, 4, key_a, uid);
    if (!auth_ok) {
        auth_ok = rc522_mf_authent(readerObj, 0x60, 7, key_a, uid);
    }"""

assert old_call in c, "old_call not found"
c = c.replace(old_call, new_call)

with open(path, "w", encoding="utf-8") as f:
    f.write(c)

print("Successfully patched StartSend and block 4/7 fallback into native_tag_decoder.c")
