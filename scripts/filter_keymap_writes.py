"""Parse the frida keymap trace and extract class=0x02 packets with timestamps and full hex."""
import re, sys

path = sys.argv[1] if len(sys.argv) > 1 else "L:/PROJECTS/razer-joro/captures/frida_keymap_trace.log"

with open(path, encoding='latin-1') as f:
    lines = f.readlines()

last_meta = None
for line in lines:
    line = line.rstrip()
    m = re.match(r'\[\s*(\S+)\]\s+\[(\d+)\]\s+\*\*\* (PROTOCOL30|HidD_SetFeature) \*\*\* (.*)', line)
    if m:
        last_meta = {'ts': m.group(1), 'pid': m.group(2), 'kind': m.group(3), 'rest': m.group(4)}
        continue
    if 'hex:' in line and last_meta:
        # parse 'hex:  00 [trans] 00 00 00 [dsize] [class] [cmd] [args...]'
        bytes_ = re.findall(r'[0-9a-f]{2}', line.split('hex:')[1])
        if len(bytes_) < 12:
            continue
        # Layout: byte[0] = report_id (always 0)
        #         byte[1] = status, byte[2] = trans_id, ..., byte[7] = class, byte[8] = cmd
        report_id = bytes_[0]
        status = bytes_[1]
        trans_id = bytes_[2]
        dsize = bytes_[6]
        class_ = bytes_[7]
        cmd = bytes_[8]
        if class_ == "02":
            args = ' '.join(bytes_[9:9+max(int(dsize, 16), 18) if dsize and len(bytes_) > 9 else 18])
            print(f"ts={last_meta['ts']} pid={last_meta['pid']} kind={last_meta['kind']} "
                  f"status={status} trans={trans_id} dsize={int(dsize,16):02d} class=0x{class_} cmd=0x{cmd}")
            print(f"  meta: {last_meta['rest']}")
            print(f"  hex:  {' '.join(bytes_[:32])}")
            print(f"        {' '.join(bytes_[32:64])}")
            print(f"        {' '.join(bytes_[64:91])}")
            print()
        last_meta = None
