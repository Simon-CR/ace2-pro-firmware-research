@ ACE 2 Pro V1.1.45 -- Native Multi-Format RFID Tag Decoder Integration.
@
@ Hooked at 0x0800FE36 (replaces `mov r6,r0` + `cmp r0,#140`, bytes 06 46 8c 28),
@ immediately after the background page read returns.
@
@ Registers on entry:
@   r0: byte count returned from rfid_pageread (0x0800E18C)
@   r5: active slot record pointer (base+12)
@   r6: reader context pointer
@
@ Buffer locations:
@   0x20000704: extended page read buffer (up to 192 bytes from tag Page 4 onwards)
@   r5+3:       10-byte UID buffer copied from RC522 FIFO
@
@ The C module `decode_native_tag(r5, 0x20000704, r0)` handles:
@   1. Anycubic Native (returns 0 -> stock Anycubic unpack runs untouched)
@   2. OpenSpool / Spoolman / NDEF JSON (returns 1 -> commits fully decoded record, exits success)
@   3. FilaMan NFC (returns 1 -> commits sm_id, brand, type, temps, exits success)
@   4. Prusament (returns 1 -> commits presets and color, exits success)
@   5. Creality CFS (returns 1 -> commits presets and color, exits success)
@   6. Bambu Lab MIFARE Classic (returns 1 -> commits UID, Bambu Lab brand, PLA preset, exits success)
@   7. Unrecognized / failed read (returns -1 -> rejoins stock failure path)

        .syntax unified
        .thumb
        .text
        .global rawtag_extract_stub
        .thumb_func

rawtag_extract_stub:
        push    {r4, r5, r6, lr}
        sub     sp, #8
        str     r0, [sp, #0]            @ save byte count

        @ Call C native_tag_decoder:
        @ int decode_native_tag(uint8_t *slot_record, const uint8_t *page_buf, int bytes_read)
        mov     r2, r0                  @ arg2: bytes_read
        movw    r1, #0x0704             @ arg1: page_buf = 0x20000704
        movt    r1, #0x2000
        mov     r0, r5                  @ arg0: slot_record
        bl      decode_native_tag

        cmp     r0, #1
        beq     .Lsuccess_exit          @ foreign tag successfully decoded

        @ r0 == 0 (Anycubic) or r0 == -1 (unrecognized/failed):
        ldr     r0, [sp, #0]            @ restore byte count
        add     sp, #8
        pop     {r4, r5, r6, lr}
        mov     r6, r0                  @ replicate 0x0800FE36: mov r6, r0
        cmp     r0, #140                @ replicate 0x0800FE38: cmp r0, #140
        b.w     extract_resume          @ rejoin at 0x0800FE3A (native bcc.n 0x800fe94)

.Lsuccess_exit:
        add     sp, #8
        pop     {r4, r5, r6, lr}
        movs    r6, #140                @ r6 >= 140 ensures native scan_exit returns r0 = 1
        b.w     scan_exit               @ branch to 0x0800FE98 (clean exit as success)
