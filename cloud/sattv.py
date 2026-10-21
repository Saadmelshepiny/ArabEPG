#!/usr/bin/env python3
"""sat.tv (Eutelsat's own guide) for the cloud.

  grab <iptv-org sites/sat.tv> <work dir> [days]
      one request per line-up and day (each answer holds every channel of the line-up), only the
      fewest line-ups that cover all channels -> work/out_sat.tv.1.xml   (run by official.py)
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
import time
import xml.etree.ElementTree as ET

SAT_NILESAT, SAT_HOTBIRD = '1', '2'
GROUPS = (('ar', SAT_NILESAT, 'ar1'), ('en', SAT_NILESAT, 'en1'), ('en', SAT_HOTBIRD, 'en2'))
API = 'https://www.sat.tv/wp-content/themes/twentytwenty-child/ajax_chaines.php'
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'


def cover(src, lang, sat):
    """the fewest line-ups of this satellite that hold every channel (from iptv-org's channel lists)"""
    by = collections.defaultdict(set)
    for sid, _l, name in read_list(os.path.join(src, 'sat.tv_%s.channels.xml' % lang)):
        p = sid.split('#')
        if p[0] == sat:
            by[p[1]].add(name)
    left, out = set().union(*by.values()) if by else set(), []
    while left and by:
        best = max(by, key=lambda k: len(by[k] & left))
        gain = by[best] & left
        if len(gain) < 3:
            break
        out.append(best)
        left -= gain
    return out


def _txt(s):
    return html.unescape(re.sub(r'<[^>]+>', '', s or '')).strip()


def parse(page, day):
    """-> {channel name: [(start, duration, title, desc)]}; times are UTC (userTimezone=UTC)"""
    import calendar
    out = {}
    base = calendar.timegm(time.strptime(day, '%Y-%m-%d'))
    for block in re.split(r"<div class='container-channel-events ?'?", page)[1:]:
        t = re.search(r"class='channel-title'>(.*?)</span>", block, re.S)
        if not t:
            continue
        name = _txt(t.group(1))
        evs, last, extra = [], -1, 0
        for ev in re.split(r"<div class='container-event ", block)[1:]:
            tm = re.search(r"event-data-date'>[^<]*?([0-9]{2}):([0-9]{2})\s*<", ev)
            ti = re.search(r"event-data-title'>(.*?)</span>", ev, re.S)
            if not tm or not ti:
                continue
            mins = int(tm.group(1)) * 60 + int(tm.group(2))
            if mins < last:
                extra += 1440          # after midnight
            last = mins
            du = re.search(r"([0-9]{2})h([0-9]{2})", re.search(r"event-data-info'>(.*?)</span>", ev, re.S).group(1)
                           if re.search(r"event-data-info'>(.*?)</span>", ev, re.S) else '')
            dur = (int(du.group(1)) * 60 + int(du.group(2))) * 60 if du else 0
            de = re.search(r"event-data-desc'[^>]*>(.*?)</span>", ev, re.S)
            evs.append((base + (mins + extra) * 60, dur, _txt(ti.group(1)), _txt(de.group(1)) if de else ''))
        if name and evs:
            out.setdefault(name, []).extend(evs)
    return out


def fetch(lang, sat, lineup, day):
    import urllib.parse
    import urllib.request
    data = urllib.parse.urlencode({'dateFiltre': day, 'hoursFiltre': '0', 'satLineup': lineup, 'satSatellite': sat,
                                   'userDateTime': int(time.time() * 1000), 'userTimezone': 'UTC'}).encode()
    req = urllib.request.Request(API, data=data, headers={
        'User-Agent': UA, 'Cookie': 'pll_language=%s' % lang, 'X-Requested-With': 'XMLHttpRequest',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'Referer': 'https://www.sat.tv/'})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode('utf-8', 'replace')


def grab(src, work, days=2):
    guides = collections.OrderedDict()
    for lang, sat, prefix in GROUPS:
        lineups = cover(src, lang, sat)
        got = {}
        for d in range(int(days)):
            day = time.strftime('%Y-%m-%d', time.gmtime(time.time() + d * 86400))
            for lu in lineups:
                try:
                    page = fetch(lang, sat, lu, day)
                except Exception as e:
                    print('sat.tv %s %s#%s %s: %s' % (lang, sat, lu, day, e))
                    continue
                for name, evs in parse(page, day).items():
                    if d == 0 and name in got and lu != lineups[0]:
                        continue         # the first line-up that has the channel wins
                    got.setdefault(name, {})
                    for e in evs:
                        got[name][e[0]] = e
                time.sleep(1)
        print('sat.tv %s (satellite %s): %d line-ups, %d channels with programmes' % (lang, sat, len(lineups), len(got)))
        for name, evs in got.items():
            guides['%s.%s' % (prefix, name)] = (name, sorted(evs.values()))
    out = os.path.join(work, 'out_sat.tv.1.xml')
    fmt = lambda t: time.strftime('%Y%m%d%H%M%S +0000', time.gmtime(t))
    n = 0
    with open(out, 'w', encoding='utf8') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<tv>\n')
        for cid, (name, _e) in guides.items():
            f.write('<channel id="%s"><display-name>%s</display-name></channel>\n' % (html.escape(cid), html.escape(name)))
        for cid, (_n, evs) in guides.items():
            for i, e in enumerate(evs):
                dur = e[1] or ((evs[i + 1][0] - e[0]) if i + 1 < len(evs) else 1800)
                f.write('<programme start="%s" stop="%s" channel="%s"><title>%s</title>%s</programme>\n' % (
                    fmt(e[0]), fmt(e[0] + dur), html.escape(cid), html.escape(e[2]),
                    ('<desc>%s</desc>' % html.escape(e[3])) if e[3] else ''))
                n += 1
        f.write('</tv>\n')
    print('sat.tv: %d channels, %d programmes -> %s' % (len(guides), n, out))


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


_AR_DIGITS = dict(zip('٠١٢٣٤٥٦٧٨٩', '0123456789'))


def _digits(names):
    return set(re.findall(r'[0-9]+', ''.join(_AR_DIGITS.get(ch, ch) for ch in ' '.join(names))))


def _digits_agree(a, b):
    """'Dubai Sports 1' is not 'Dubai Sports 3'; a name without a number says nothing"""
    da, db = _digits(a), _digits(b)
    return not da or not db or bool(da & db)


def _alike(a, b):
    from difflib import SequenceMatcher
    k = lambda x: re.sub(r'\b(hd|sd|fhd|tv|uhd|4k)\b|[^a-z0-9]', '', x.lower())
    return SequenceMatcher(None, k(a), k(b)).ratio() >= 0.75


def starts(plist):
    return set(re.search(r'start="([^"]+)"', x).group(1) for x in plist)


def merge(work, out_ar, out_en):
    names, progs = read_guides(work)
    ids = [c for c in progs if progs[c]]
    en_ids = [c for c in ids if c.startswith('en1.')]
    en_starts = dict((c, starts(progs[c])) for c in en_ids)
    by_start = collections.defaultdict(set)
    for c, st in en_starts.items():
        for t in st:
            by_start[t].add(c)
    # every English channel goes to its best Arabic channel only; a number in both names must agree
    cand = []
    for c in ids:
        if not c.startswith('ar1.'):
            continue
        mine = starts(progs[c])
        common = collections.Counter(e for t in mine for e in by_start.get(t, ()))
        for e, n in common.items():
            sc = n / float(min(len(mine), len(en_starts[e])))
            if n >= 6 and sc >= 0.9 and _digits_agree(names.get(c) or [c], names.get(e) or [e]):
                cand.append((sc, n, c, e))
    best_for_en, best_for_ar = {}, collections.defaultdict(list)
    for sc, n, c, e in sorted(cand, reverse=True):
        if e not in best_for_en:
            best_for_en[e] = (sc, c)
    for e, (sc, c) in best_for_en.items():
        best_for_ar[c].append((sc, e))
    ar, used, paired = collections.OrderedDict(), set(), 0
    for c in ids:
        if not c.startswith('ar1.'):
            continue
        nm = list(names.get(c) or [c])
        tw = sorted(best_for_ar.get(c) or [], reverse=True)
        if tw:   # copies (HD/SD) are named alike; anything else with the same times is not this channel
            first = (names.get(tw[0][1]) or [tw[0][1]])[0]
            tw = [tw[0][1]] + [e for sc, e in tw[1:3] if sc >= tw[0][0] - 0.02 and
                               _alike(first, (names.get(e) or [e])[0])]
        for t in tw:
            nm += [x for x in names.get(t) or [] if x not in nm]
            used.add(t)
        paired += 1 if tw else 0
        ar[c] = nm
    # channels that only exist in the English guide of 7/8 West (Al Jazeera English...)
    for c in en_ids:
        if c not in used:
            ar[c] = names.get(c) or [c]
    en = collections.OrderedDict((c, names.get(c) or [c]) for c in ids if c.startswith('en2.'))
    n_ar = write(out_ar, ar, progs)
    n_en = write(out_en, en, progs)
    print('sat.tv merged: 7/8 West %d channels (%d Arabic named in English too), %d programmes | '
          'Hot Bird %d channels, %d programmes' % (len(ar), paired, n_ar, len(en), n_en))


if __name__ == '__main__':
    if sys.argv[1] == 'grab':
        grab(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else 2)
    elif sys.argv[1] == 'channels':
        channels(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == 'merge':
        merge(sys.argv[2], sys.argv[3], sys.argv[4])
