# ACE2 V1.1.31 nanopb 0.4 descriptor decoder. Run from L:/ace2-fw-analysis.
import struct
D=open('ex/ACE2_V1.1.31_20260306.bin','rb').read()
BASE=0x08008000
END=BASE+len(D)
def ok(a): return BASE<=a<END
def u32(a): return struct.unpack_from('<I',D,a-BASE)[0]
def u16(a): return struct.unpack_from('<H',D,a-BASE)[0]
def u8(a): return D[a-BASE]

LTYPE={0:'BOOL',1:'VARINT',2:'UVARINT',3:'SVARINT',4:'FIXED32',5:'FIXED64',6:'BYTES',7:'STRING',
       8:'SUBMESSAGE',9:'SUBMSG_W_CB',10:'EXTENSION',11:'FIXED_LENGTH_BYTES'}
HTYPE={0x00:'REQUIRED',0x10:'SINGULAR/OPTIONAL',0x20:'REPEATED/FIXARRAY',0x30:'ONEOF'}
ATYPE={0x00:'STATIC',0x40:'CALLBACK',0x80:'POINTER'}

def decode_fields(fi):
    """decode nanopb 0.4 field_info array at addr fi -> list of dicts"""
    out=[]; a=fi
    while True:
        w=u32(a)
        if w==0: break
        fmt=w&3
        if fmt==0:
            tag=(w>>2)&0x3F; typ=(w>>8)&0xFF; doff=(w>>16)&0xFF
            soff=(w>>24)&0xF; dsz=(w>>28)&0xF; asz=1; nw=1
        elif fmt==1:
            w1=u32(a+4)
            tag=(w>>2)&0x3F; typ=(w>>8)&0xFF; asz=(w>>16)&0xFFF; soff=(w>>28)&0xF
            doff=w1&0xFFFF; dsz=(w1>>16)&0xFFF; nw=2
        elif fmt==2:
            w1,w2,w3=u32(a+4),u32(a+8),u32(a+12)
            tag=(w>>2)&0x3FFFFF; typ=(w>>24)&0xFF
            asz=w1&0xFFFF; soff=(w1>>16)&0xFFFF
            doff=w2&0xFFFF; dsz=(w2>>16)&0xFFFF
            nw=4
        else:
            ws=[u32(a+4*i) for i in range(8)]
            tag=w>>8; typ=ws[1]&0xFF; asz=ws[2]; soff=ws[3]; doff=ws[4]; dsz=ws[5]; nw=8
        out.append(dict(addr=a,raw=[u32(a+4*i) for i in range(nw)],fmt=fmt,tag=tag,typ=typ,
                        ltype=typ&0x0F,htype=typ&0x30,atype=typ&0xC0,
                        data_offset=doff,data_size=dsz,size_offset=soff,array_size=asz))
        a+=4*nw
    return out,a  # a = terminator addr

def msgdesc(addr):
    fi=u32(addr); sm=u32(addr+4); dv=u32(addr+8); cb=u32(addr+12)
    fc=u32(addr+16); rq=u32(addr+20); lt=u32(addr+24)
    subs=[]
    if sm:
        b=sm
        while True:
            v=u32(b)
            if v==0: break
            subs.append(v); b+=4
    flds,_=decode_fields(fi)
    return dict(addr=addr,field_info=fi,submsg_info=sm,default=dv,callback=cb,
                field_count=fc,required=rq,largest_tag=lt,fields=flds,subs=subs)

def fdesc(f):
    lt=LTYPE.get(f['ltype'],'?%d'%f['ltype'])
    ht={0x00:'req',0x10:'sing',0x20:'rep',0x30:'oneof'}[f['htype']]
    at={0x00:'',0x40:'CB:',0x80:'PTR:'}[f['atype']]
    s="tag=%-3d %s%-18s %-5s off=0x%02x size=%-3d" % (f['tag'],at,lt,ht,f['data_offset'],f['data_size'])
    if f['htype']==0x20: s+=" arr=%d"%f['array_size']
    if f['size_offset']: s+=" soff=%d"%f['size_offset']
    return s
