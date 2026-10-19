#!/usr/bin/env python3
"""Build the service-reference database used for exact channel linking.

Source: the channel assignment files of EPGGrabber by ZAKARIYA KHA / iet5
(https://github.com/ziko-ZR1/Epg-plugin, GPL-3.0), which map EPG channel ids to real
Nilesat / Arabsat / Eutelsat 8W service references.

Output json: {"<orbital hex>:<SID>:<TSID>:<ONID>": ["epg id", ...]}
Usage: python3 tools/make_refs.py --src ziko/configs --out plugin/ArabEPG/refs.json
"""
import argparse
import glob
import html
import json
import os
import re


def key_of(ref):
    p = ref.strip().rstrip(':').split(':')
    if len(p) < 7 or p[0] != '1':
        return None
    try:
        ns = int(p[6], 16) >> 16
        return '%X:%X:%X:%X' % (ns, int(p[3], 16), int(p[4], 16), int(p[5], 16))
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    refs = {}
    for f in sorted(glob.glob(os.path.join(a.src, '*.channels.xml'))):
        text = open(f, encoding='utf8', errors='replace').read()
        for cid, ref in re.findall(r'<channel id="([^"]+)">([^<]+)</channel>', text):
            k = key_of(ref)
            cid = html.unescape(cid).strip()
            if k and cid:
                lst = refs.setdefault(k, [])
                if cid not in lst:
                    lst.append(cid)
    with open(a.out, 'w', encoding='utf8') as fh:
        json.dump(refs, fh, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    print('refs: %d services, %d ids' % (len(refs), sum(len(v) for v in refs.values())))


if __name__ == '__main__':
    main()
