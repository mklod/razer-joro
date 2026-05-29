"""
Find EXACTLY where the DFU device re-enumerates in fw_update_u1.pcap and
what USB device-address / interface (wIndex) the bootloader uses, so the
flasher targets the right device after 00:04.

USBPcap packet header (USBPCAP_BUFFER_PACKET_HEADER):
  off 0  u16 headerLen
  off 2  u64 irpId
  off 10 u32 status
  off 14 u16 function
  off 16 u8  info
  off 17 u16 bus
  off 19 u16 device       <- USB device address
  off 21 u8  endpoint
  off 22 u8  transfer
  off 23 u32 dataLength
  then control-stage byte, then 8-byte SETUP (bmReqType,bReq,wVal,wIdx,wLen)
"""
import struct
from scapy.all import rdpcap

pk=rdpcap(r'L:\PROJECTS\razer-joro\captures\fw_update_u1.pcap')
seq=[]   # (devaddr, wIndex, class, cmd)
for p in pk:
    raw=bytes(p)
    if len(raw)<29: continue
    hlen=struct.unpack('<H',raw[0:2])[0]
    if hlen<27 or hlen+8>len(raw): continue
    dev=struct.unpack('<H',raw[19:21])[0]
    # find setup 21 09 within a small window after the header
    so=None
    for o in range(hlen-2, min(hlen+6,len(raw)-8)):
        if raw[o]==0x21 and raw[o+1]==0x09:
            so=o; break
    if so is None:
        # fall back to broad scan like prior scripts
        for o in range(20,min(40,len(raw)-8)):
            if raw[o]==0x21 and raw[o+1]==0x09: so=o;break
    if so is None: continue
    wVal=struct.unpack('<H',raw[so+2:so+4])[0]
    wIdx=struct.unpack('<H',raw[so+4:so+6])[0]
    wLen=struct.unpack('<H',raw[so+6:so+8])[0]
    if wLen!=90: continue
    rz=raw[so+8:so+8+90]
    seq.append((dev,wIdx,rz[6],rz[7]))

print(f"{len(seq)} Protocol30 SET_REPORT frames")
# run-length collapse on (devaddr,wIndex) to see the transition
runs=[]
prev=None
for i,(d,wi,c,cm) in enumerate(seq):
    key=(d,wi)
    if key!=prev:
        runs.append([key,i,1,(c,cm)])
        prev=key
    else:
        runs[-1][2]+=1
print("\n(devAddr, wIndex) runs  [startFrame, count, firstClass:cmd]:")
for (key,start,cnt,(c,cm)) in runs:
    print(f"  dev={key[0]:3d} wIdx=0x{key[1]:04x}  frames {start}..{start+cnt-1} "
          f"(x{cnt})  first={c:02x}:{cm:02x}")

# specifically: the 00:04 frame and the frame right after the transition
print("\ntransition detail:")
for i,(d,wi,c,cm) in enumerate(seq):
    if i<6 or (c==0x10 and cm==0x01) or (i>0 and (seq[i-1][0]!=d)):
        tags=[]
        if c==0x00 and cm==0x04: tags.append('<ENTER-UPDATE 00:04')
        if c==0x10 and cm==0x01: tags.append('<INIT/ERASE 10:01')
        if i>0 and seq[i-1][0]!=d: tags.append('<<DEV ADDR CHANGED (re-enum)')
        print(f"  f{i:4d} dev={d:3d} wIdx=0x{wi:04x} {c:02x}:{cm:02x} {' '.join(tags)}")
