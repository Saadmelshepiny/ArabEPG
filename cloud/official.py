#!/usr/bin/env python3
"""Grab the official sites that have no iptv-org grabber, with the plugin's own readers
(sources.json "grab" entries marked "python": true). Writes work/out_<site>.1.xml for build.py."""
import argparse
import html
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'plugin'))
sys.path.insert(0, HERE)
from SaadEPG import sources  # noqa: E402

GRABBERS = {'lbcgroup.tv': sources.grab_lbci, 'tunisiatv.tn': sources.grab_tunisia, 'trtarabi.com': sources.grab_trt}


def fmt(ts):
    return time.strftime('%Y%m%d%H%M%S +0000', time.gmtime(ts))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', default='work')
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(HERE, 'sources.json'), encoding='utf8'))
    os.makedirs(a.work, exist_ok=True)
    for name, d in cfg['grab'].items():
        if d.get('python') and d['site'] == 'sat.tv':     # whole line-ups per request: its own grabber
            try:
                import sattv
                sattv.grab(os.path.join(HERE, '..', 'iptv-epg', 'sites', 'sat.tv'), a.work, d.get('days', 2))
            except Exception as e:
                import traceback
                traceback.print_exc()
                print('%s: %s' % (name, e))
            continue
        if not d.get('python') or d['site'] not in GRABBERS:
            continue
        path = os.path.join(HERE, 'channels', d['site'] + '.channels.xml')
        chans = []
        for m in re.finditer(r'<channel ([^>]*)>([^<]*)</channel>', open(path, encoding='utf8').read()):
            at = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
            chans.append((html.unescape(at['site_id']), html.unescape(at['xmltv_id']), html.unescape(m.group(2))))
        try:
            got = GRABBERS[d['site']]([c[0] for c in chans], d.get('days', cfg.get('days', 3)))
        except Exception as e:
            print('%s: %s' % (name, e))
            continue
        out = os.path.join(a.work, 'out_%s.1.xml' % d['site'])
        n = 0
        with open(out, 'w', encoding='utf8') as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n<tv>\n')
            for sid, xid, cname in chans:
                f.write('<channel id="%s"><display-name>%s</display-name></channel>\n' % (html.escape(xid), html.escape(cname)))
            for sid, xid, _c in chans:
                for e in got.get(sid) or []:
                    f.write('<programme start="%s" stop="%s" channel="%s"><title>%s</title>%s</programme>\n' % (
                        fmt(e[0]), fmt(e[0] + e[1]), html.escape(xid), html.escape(e[2]),
                        ('<desc>%s</desc>' % html.escape(e[4])) if e[4] else ''))
                    n += 1
            f.write('</tv>\n')
        print('%-12s %d programmes' % (name, n))


if __name__ == '__main__':
    main()
