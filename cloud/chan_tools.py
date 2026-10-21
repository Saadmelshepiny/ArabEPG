#!/usr/bin/env python3
"""Helpers for the grab jobs.

  matrix                         -> prints the job matrix (JSON) built from sources.json
  opts  <site>                   -> prints "days delay connections" for a site
  split <channels.xml> <part> <parts> <out.xml>
  missing <channels.xml> <guide.xml> <out.xml>   -> channels of the list that got no programme (exit 1 if none)
"""
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, 'sources.json'), encoding='utf8'))
CH_RE = re.compile(r'\s*<channel [^>]*>[^<]*</channel>')


def site_cfg(site):
    for d in CFG['grab'].values():
        if d['site'] == site:
            return d
    return {}


def read_channels(path):
    return [m.group(0).strip() for m in CH_RE.finditer(open(path, encoding='utf8').read())]


def write_channels(path, lines):
    with open(path, 'w', encoding='utf8') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<channels>\n')
        for line in lines:
            f.write('  %s\n' % line)
        f.write('</channels>\n')


def main():
    cmd = sys.argv[1]
    if cmd == 'matrix':
        inc = []
        for d in CFG['grab'].values():
            if d.get('python'):
                continue          # grabbed by cloud/official.py with the plugin's own readers
            parts = int(d.get('parts', 1))
            for p in range(1, parts + 1):
                inc.append({'site': d['site'], 'part': p, 'parts': parts})
        # offline translation of the foreign guides to English runs as extra jobs of the same matrix
        for g in (CFG.get('translate') or {}).get('groups', []):
            inc.append({'site': 'translate', 'part': g, 'parts': 1})
        print(json.dumps({'include': inc}, separators=(',', ':')))
    elif cmd == 'opts':
        d = site_cfg(sys.argv[2])
        print(d.get('days', CFG.get('days', 3)), d.get('delay', 0), d.get('conns', 5))
    elif cmd == 'split':
        lines = read_channels(sys.argv[2])
        part, parts = int(sys.argv[3]), int(sys.argv[4])
        write_channels(sys.argv[5], [l for i, l in enumerate(lines) if i % parts == part - 1])
    elif cmd == 'missing':
        lines = read_channels(sys.argv[2])
        got = set()
        if os.path.exists(sys.argv[3]):
            for m in re.finditer(r'<programme [^>]*channel="([^"]*)"', open(sys.argv[3], encoding='utf8').read()):
                got.add(html.unescape(m.group(1)))
        miss = []
        for l in lines:
            m = re.search(r'xmltv_id="([^"]*)"', l)
            if m and html.unescape(m.group(1)) not in got:
                miss.append(l)
        write_channels(sys.argv[4], miss)
        print('%d/%d channels without programmes' % (len(miss), len(lines)))
        sys.exit(0 if miss else 1)


if __name__ == '__main__':
    main()
