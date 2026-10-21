#!/usr/bin/env python3
"""sat.tv (Eutelsat's own guide) for the cloud.

  channels <iptv-org sites/sat.tv> <out.channels.xml>
      one entry per channel: Eutelsat 7/8 West (Nilesat position) in Arabic and in English,
      Hot Bird in English. The grabber asks once per line-up and day, so this stays fast.
  merge <work dir> <out_ar.xml> <out_en.xml>
      the Arabic guide of 7/8 West, every channel also named in English (paired with the English
      guide by identical programme times: receiver names are in Latin letters), and Hot Bird in English.
"""
import collections
import glob
import html
import os
import re
import sys
import xml.etree.ElementTree as ET

SAT_NILESAT, SAT_HOTBIRD = '1', '2'


def read_list(path):
    out = []
    for m in re.finditer(r'<channel ([^>]*)>([^<]*)</channel>', open(path, encoding='utf8').read()):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        out.append((html.unescape(attrs.get('site_id', '')), attrs.get('lang', ''), html.unescape(m.group(2)).strip()))
    return out


def channels(src, out):
    rows = []
    for lang, sat, prefix in (('ar', SAT_NILESAT, 'ar1'), ('en', SAT_NILESAT, 'en1'), ('en', SAT_HOTBIRD, 'en2')):
        seen = set()
        for sid, lng, name in read_list(os.path.join(src, 'sat.tv_%s.channels.xml' % lang)):
            if sid.split('#')[0] != sat or name in seen:
                continue
            seen.add(name)
            rows.append((sid, lng or lang, '%s.%d' % (prefix, len(seen)), name))
    with open(out, 'w', encoding='utf8') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<channels>\n')
        for sid, lang, xid, name in rows:
            f.write('  <channel site="sat.tv" site_id="%s" lang="%s" xmltv_id="%s">%s</channel>\n' % (
                html.escape(sid), lang, xid, html.escape(name)))
        f.write('</channels>\n')
    print('sat.tv: %d channels (%s)' % (len(rows), ', '.join(
        '%s %d' % (p, len([r for r in rows if r[2].startswith(p + '.')])) for p in ('ar1', 'en1', 'en2'))))


def read_guides(work):
    names, progs = {}, collections.defaultdict(list)
    for p in sorted(glob.glob(os.path.join(work, 'out_sat.tv.*xml'))):
        if os.path.getsize(p) < 200:
            continue
        try:
            for _ev, el in ET.iterparse(p, events=('end',)):
                if el.tag == 'channel':
                    names[el.get('id')] = [d.text.strip() for d in el.findall('display-name') if d.text]
                    el.clear()
                elif el.tag == 'programme':
                    progs[el.get('channel')].append(ET.tostring(el, encoding='unicode').strip())
                    el.clear()
        except ET.ParseError as e:
            print('sat.tv: %s: %s' % (p, e))
    return names, progs


def fingerprint(plist):
    starts = sorted(set(re.search(r'start="([^"]+)"', x).group(1) for x in plist))
    return tuple(starts) if len(starts) >= 4 else None


def write(path, chans, progs):
    n = 0
    with open(path, 'w', encoding='utf8') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="SaadEPG sat.tv">\n')
        for cid, nm in chans.items():
            f.write('<channel id="%s">%s</channel>\n' % (html.escape(cid), ''.join(
                '<display-name>%s</display-name>' % html.escape(x) for x in nm[:6])))
        for cid in chans:
            seen = set()
            for x in progs[cid]:
                if x not in seen:
                    seen.add(x)
                    f.write(x + '\n')
                    n += 1
        f.write('</tv>\n')
    return n


def merge(work, out_ar, out_en):
    names, progs = read_guides(work)
    ids = [c for c in progs if progs[c]]
    en_by_fp = collections.defaultdict(list)
    for c in ids:
        if c.startswith('en1.'):
            fp = fingerprint(progs[c])
            if fp:
                en_by_fp[fp].append(c)
    ar, used, paired = collections.OrderedDict(), set(), 0
    for c in ids:
        if not c.startswith('ar1.'):
            continue
        nm = list(names.get(c) or [c])
        fp = fingerprint(progs[c])
        twins = (en_by_fp.get(fp) or []) if fp else []
        if 0 < len(twins) <= 3:          # the same schedule: the same channel (or its HD/SD copies)
            for t in twins:
                nm += [x for x in names.get(t) or [] if x not in nm]
                used.add(t)
            paired += 1
        ar[c] = nm
    # channels that only exist in the English guide of 7/8 West (Al Jazeera English...)
    for c in ids:
        if c.startswith('en1.') and c not in used:
            ar[c] = names.get(c) or [c]
    en = collections.OrderedDict((c, names.get(c) or [c]) for c in ids if c.startswith('en2.'))
    n_ar = write(out_ar, ar, progs)
    n_en = write(out_en, en, progs)
    print('sat.tv merged: 7/8 West %d channels (%d Arabic named in English too), %d programmes | '
          'Hot Bird %d channels, %d programmes' % (len(ar), paired, n_ar, len(en), n_en))


if __name__ == '__main__':
    if sys.argv[1] == 'channels':
        channels(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == 'merge':
        merge(sys.argv[2], sys.argv[3], sys.argv[4])
