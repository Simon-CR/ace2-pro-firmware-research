@ ACE 2 Pro V1.1.31 -- RC522 passthrough v3.
@
@ v2 could read/write registers but never got a card reply: 0x0800F32C is only the transceive
@ STEP, while every working firmware read wraps it in sub_800DEB6 (reset + antenna + analog
@ init + REQA/anticollision/SELECT). v3 therefore exposes the firmware's own high-level
@ primitives, which are proven to work because they are what normal identify uses.
@
@ Packed request index:
@   bit31=1 | bit24=reader | op<<16 | arg1<<8 | arg2
@
@   op 0  read register arg1                      -> byte
@   op 1  write arg2 to register arg1             -> 0
@   op 2  store arg2 into staging buffer at arg1  -> 0
@   op 3  transceive: cmd arg2, arg1 TX bytes     -> helper status
@   op 4  read RX buffer byte at arg1             -> byte
@   op 5  read received bit length                -> byte
@   op 6  SELECT a card (sub_800DEB6)             -> status (0 = ok); UID lands at BUF+13..19
@   op 7  bulk page read 4..39 (sub_800E18C)      -> byte count (>=140 on success), data at BUF
@   op 8  clear the cached tag record for slot arg1 -> 0
@
@ Staging buffer 0x20000704 (the firmware's own tag-page buffer; idle while background
@ scanning is disabled): +0 TX/select scratch | +64 RX | +128 rx bit length.
@
@ Cached tag record: 0x20000054 + slot*164; version u16 at +286, sku[19] at +288,
@ type[19] at +328, colours at +348 (served by the cmd 13 handler 0x0800E910).
@
@ r4 = response struct (MUST survive), r7 = request ptr. Everything else is restored by the
@ shared epilogue, so r0-r3/r5/r6/r8/r9/ip and lr are free.

        .syntax unified
        .thumb
        .text
        .global rc522_stub
        .thumb_func

rc522_stub:
        ldr     r5, [r7]                @ packed sub-command
        ubfx    r3, r5, #16, #8         @ op

        cmp     r3, #2
        beq     op_store
        cmp     r3, #4
        beq     op_rxread
        cmp     r3, #5
        beq     op_rxbits
        cmp     r3, #8
        beq     op_clearcache

        @ ops 0/1/3/6/7 need the per-reader context pointer
        movw    r0, #0x1604
        movt    r0, #0x2000
        lsls    r1, r5, #7              @ bit24 -> sign bit
        it      mi
        addmi   r0, r0, #4
        ldr     r0, [r0]                @ context pointer
        cmp     r0, #0
        beq     no_reader

        cmp     r3, #6
        beq     op_select
        cmp     r3, #7
        beq     op_pageread

        ldr     r0, [r0, #4]            @ reader object for the low-level helpers
        cmp     r0, #0
        beq     no_reader
        cmp     r3, #3
        beq     op_transceive

        ubfx    r1, r5, #8, #6
        ubfx    r2, r5, #0, #8
        cbnz    r3, op_write
        bl      rc522_read_reg
        b       done
op_write:
        bl      rc522_write_reg
        movs    r0, #0
        b       done

        @ --- high-level primitives (context pointer already in r0) -----------------
op_select:
        @ Power the reader exactly as the handler does at 0x0800E7F2 before selecting:
        @ a GPIO port/value pair from readerObj+44/+48, then a 20ms settle. Without this the
        @ select always failed (status 1) -- the chip simply was not up.
        mov     r6, r0                  @ keep ctx
        ldr     r0, [r0, #4]            @ reader object
        cmp     r0, #0
        beq     no_reader
        ldrd    r1, r0, [r0, #44]
        str     r0, [r1, #16]
        movs    r0, #20
        bl      delay_ms                @ 0x08013C70
        mov     r0, r6                  @ ctx
        movw    r1, #0x0704
        movt    r1, #0x2000
        movs    r2, #1
        bl      rfid_select             @ sub_800DEB6(ctx, buf, 1)
        b       done

op_pageread:
        movw    r1, #0x0704
        movt    r1, #0x2000
        bl      rfid_pageread           @ sub_800E18C(ctx, dest) -> byte count
        b       done

        @ --- low-level transceive -------------------------------------------------
op_transceive:
        sub     sp, #8
        movw    r2, #0x0704
        movt    r2, #0x2000
        ubfx    r3, r5, #8, #6
        ubfx    r1, r5, #0, #8
        add     r6, r2, #64
        str     r6, [sp, #0]
        add     r6, r2, #128
        str     r6, [sp, #4]
        bl      rc522_transceive
        add     sp, #8
        b       done

        @ --- staging buffer access ------------------------------------------------
op_store:
        movw    r0, #0x0704
        movt    r0, #0x2000
        ubfx    r1, r5, #8, #6
        ubfx    r2, r5, #0, #8
        strb    r2, [r0, r1]
        movs    r0, #0
        b       done

op_rxread:
        movw    r0, #0x0704
        movt    r0, #0x2000
        ubfx    r1, r5, #8, #6
        adds    r0, r0, #64
        ldrb    r0, [r0, r1]
        b       done

op_rxbits:
        movw    r0, #0x0704
        movt    r0, #0x2000
        ldrb    r0, [r0, #128]
        b       done

        @ --- clear the cached tag record for a slot -------------------------------
op_clearcache:
        ubfx    r1, r5, #8, #6          @ slot 0..3
        cmp     r1, #4
        bhs     no_reader
        movw    r0, #0x0054
        movt    r0, #0x2000
        movs    r2, #164
        mla     r0, r1, r2, r0          @ record base
        addw    r0, r0, #286            @ version / sku / type / colours
        movs    r2, #0
        movs    r3, #64
1:
        strb    r2, [r0], #1
        subs    r3, r3, #1
        bne     1b
        movs    r0, #0
        b       done

no_reader:
        movs    r0, #0xFF

done:
        b.w     epilogue
