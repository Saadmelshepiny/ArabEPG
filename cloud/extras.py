#!/usr/bin/env python3
"""Made from the Arabic guides after the golden guide (gold.py):

  matches.json.gz  the sports centre: today's and tomorrow's matches on every Arabic sports channel, the live
                   airing marked (replays left out), team names in Arabic when a source has them
  films.json.gz    cinema tonight: the films of the next 30 hours on the Arabic channels (poster when the
                   site gives one)

Channels are given by the plugin's own keys (and the golden channel that contains them), so the receiver
finds them in your bouquets through the links it already has.
"""
import gzip
import json
import os
import re
import time
import urllib.request

AR = re.compile('[ء-ي]')
VS = re.compile(r'^\s*(?P<a>[^|]{2,60}?)\s+(?:vs\.?|v\.?|x|×|ضد|@)\s+(?P<b>[^|]{2,60}?)\s*(?:[-|–:]\s*(?P<comp>.+))?$',
                re.I)
NOT_MATCH = re.compile(r'(?i)\b(highlights|magazine|preview|review|show|studio|countdown|best of|goals|classics|'
                       r'replay|rewind|stories|documentary|news|weekly|program|talk|analysis|draw|ملخص|أهداف|'
                       r'استوديو|برنامج|تحليل|موجز|إعادة|اعادة)\b')
LIVE = re.compile(r'(?i)(\blive\b|مباشر|\(L\)|\[L\])')
SPORT_CH = re.compile(r'(?i)(bein|ssc|ad sport|abu ?dhabi sport|dubai sport|sharjah sport|alkass|al kass|ksa sport|'
                      r'on ?time|on sport|nile sport|kuwait sport|oman sport|bahrain sport|jordan sport|qatar sport|'
                      r'arryadia|الرياضية|سبورت|رياضي|الكاس|الكأس)')
FILM_CAT = re.compile(r'(?i)(فيلم|أفلام|افلام|سينما|\bmovies?\b|\bfilms?\b|cinema|feature)')
FILM_CH = re.compile(r'(?i)(cinema|aflam|movies?|film|sinema|سينما|أفلام|افلام|mbc ?2|mbc ?max|mbc ?action|'
                     r'mbc ?bollywood|zee ?aflam|osn.*(movie|aflam)|rotana ?(cinema|classic)|nile ?cinema|art ?(aflam|cinema)|'
                     r'cima|كايرو ?سينما|سينما)')
NO_FILM = re.compile(r'(?i)(news|أخبار|اخبار|live|مباشر|match|مباراة|vs|ضد|series|مسلسل|episode|حلقة|season|موسم|'
                     r'show|برنامج|concert|حفل|cartoon|كرتون)')


def _get_json(repo, name):
    try:
        req = urllib.request.Request('https://raw.githubusercontent.com/%s/epg/%s' % (repo, name),
                                     headers={'User-Agent': 'SaadEPG-cloud'})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        if data[:2] == b'\x1f\x8b':
            data = gzip.decompress(data)
        d = json.loads(data.decode('utf-8'))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _key(fname, kind, cid):
    if kind == 'site' and '.' in cid:
        src, _, i = cid.partition('.')
        return '%s:%s' % (src, i)
    return 'xml:%s/%s' % (fname, cid)


def _teams_key(a, b):
    def n(x):
        return re.sub(r'[^0-9a-zء-ي]', '', x.lower())
    return '|'.join(sorted([n(a), n(b)]))


def _airings(data, index, members, lo, hi, accept):
    """one airing per channel and time: the same channel in several files (= one golden channel) counts once,
    with its Arabic title when one of the files has it. accept(e, names) -> extra dict or None"""
    air = {}
    for fname, chans in data.items():
        kind = (index['files'].get(fname) or {}).get('kind')
        for cid, ch in chans.items():
            names = ch.get('names') or [cid]
            key = _key(fname, kind, cid)
            ident = members.get(key) or key
            for e in ch['ev']:
                if not (lo <= e['start'] < hi):
                    continue
                x = accept(e, names)
                if x is None:
                    continue
                k = (ident, e['start'] // 900)
                cur = air.get(k)
                if cur is None:
                    cur = air[k] = dict(x, start=e['start'], stop=e['stop'], keys=[], names=[], ident=ident)
                    if members.get(key):
                        cur['keys'].append('xml:gold/' + members[key])
                elif AR.search(e['title']) and not AR.search(cur['title']):
                    cur['en'] = cur['title']
                    cur.update(dict((f, v) for f, v in x.items() if v))
                elif not AR.search(e['title']) and AR.search(cur['title']) and not cur.get('en'):
                    cur['en'] = e['title']
                if key not in cur['keys']:
                    cur['keys'].append(key)
                if names[0] not in cur['names']:
                    cur['names'].append(names[0])
                for f in ('icon', 'desc', 'cat'):
                    if not cur.get(f) and e.get(f):
                        cur[f] = e[f]
    return list(air.values())


def matches(data, index, members, real, machine, now):
    def accept(e, names):
        t = e['title']
        dur = e['stop'] - e['start']
        m = VS.match(t)
        if not m or NOT_MATCH.search(t) or dur < 75 * 60 or dur > 5 * 3600:
            return None
        sport = SPORT_CH.search(' '.join(names[:3]))
        if not sport and not re.search(r'(?i)(league|cup|دوري|كأس|كاس|championship|liga|serie a|bundesliga|ligue|'
                                       r'premier|match|مباراة)', t + ' ' + e.get('cat', '')):
            return None
        a, b, comp = m.group('a').strip(), m.group('b').strip(), (m.group('comp') or '').strip()
        if ':' in a:                     # "الدوري الإيطالي:بولونيا × إنتر ميلان"
            comp, a = (comp or a.split(':', 1)[0].strip()), a.split(':', 1)[1].strip()
        if len(a) > 40 or len(b) > 40 or not a or not b:
            return None
        return {'title': t, 'home': a, 'away': b, 'comp': comp, 'cat': e.get('cat', ''), 'live': bool(LIVE.search(t))}
    air = _airings(data, index, members, now - 6 * 3600, now + 48 * 3600, accept)
    # the same match on several channels (same teams, same time) -> one entry
    groups = {}
    for x in air:
        tk = _teams_key(x['home'], x['away'])
        groups.setdefault(tk, []).append(x)
    out = []
    for tk, xs in groups.items():
        xs.sort(key=lambda x: x['start'])
        first = xs[0]['start']
        for x in xs:
            if x['start'] > first + 3600 and not x['live']:
                continue                     # a replay
            if x['stop'] < now - 1800:
                continue
            same = [o for o in out if o['tk'] == tk and abs(o['start'] - x['start']) <= 1800]
            if same:
                o = same[0]
                for k in x['keys']:
                    if k not in o['keys']:
                        o['keys'].append(k)
                for n in x['names']:
                    if n not in o['channels']:
                        o['channels'].append(n)
                continue
            en = x.get('en') or x['title']
            ar = x['title'] if AR.search(x['title']) else (real.get(en) or machine.get(en))
            ah, aa, ac = x['home'], x['away'], x['comp']
            if ar and AR.search(ar) and not AR.search(ah):
                ma = VS.match(ar)
                if ma:
                    ah, aa = ma.group('a').strip(), ma.group('b').strip()
                    ac = (ma.group('comp') or '').strip() or ac
                    if ':' in ah:
                        ac, ah = ah.split(':', 1)[0].strip(), ah.split(':', 1)[1].strip()
            out.append({'tk': tk, 'start': x['start'], 'stop': x['stop'], 'title': en,
                        'ar': ar if ar and AR.search(ar) else None, 'home': x['home'], 'away': x['away'],
                        'ar_home': ah if AR.search(ah) else None, 'ar_away': aa if AR.search(aa) else None,
                        'comp': x['comp'], 'ar_comp': ac if AR.search(ac or '') else None, 'live': x['live'], 'first': x['start'] <= first + 3600,
                        'keys': x['keys'], 'channels': list(x['names'])})
    for o in out:
        o.pop('tk', None)
        o['keys'] = o['keys'][:30]
        o['channels'] = o['channels'][:12]
    out.sort(key=lambda m: (m['start'], m['title']))
    return out


def films(data, index, members, now):
    def accept(e, names):
        dur = e['stop'] - e['start']
        cat = e.get('cat', '')
        if dur < 70 * 60 or dur > 4 * 3600 or re.search('(?i)مسلسل|series', cat):
            return None
        if not (FILM_CAT.search(cat) or (FILM_CH.search(' '.join(names[:3])) and
                                          not NO_FILM.search(e['title'] + ' ' + cat))):
            return None
        return {'title': e['title'], 'desc': e.get('desc', '')[:400], 'cat': cat, 'icon': e.get('icon')}
    air = _airings(data, index, members, now - 2 * 3600, now + 30 * 3600, accept)
    out = []
    for x in air:
        if x['stop'] <= now:
            continue
        out.append({'start': x['start'], 'stop': x['stop'], 'title': x['title'], 'en': x.get('en'),
                    'desc': (x.get('desc') or '')[:400], 'cat': x.get('cat', ''), 'icon': x.get('icon'),
                    'keys': x['keys'][:20], 'channels': x['names'][:8]})
    out.sort(key=lambda f: (f['start'], f['title']))
    return out


def build(data, index, out_dir, days=3, log=print):
    now = time.time()
    repo = os.environ.get('GITHUB_REPOSITORY', 'Saadmelshepiny/ArabEPG')
    real = _get_json(repo, 'tr_ar_real.json.gz')
    machine = _get_json(repo, 'tr_ar.json.gz')
    members = (index.get('gold') or {}).get('members') or {}
    ms = matches(data, index, members, real, machine, now)
    fl = films(data, index, members, now)
    for name, obj in (('matches.json.gz', {'generated': int(now), 'matches': ms}),
                      ('films.json.gz', {'generated': int(now), 'films': fl})):
        with gzip.open(os.path.join(out_dir, name), 'wt', encoding='utf-8', compresslevel=9) as f:
            json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))
    index['extras'] = {'matches': 'matches.json.gz', 'films': 'films.json.gz'}
    live = [m for m in ms if m['first']]
    log('sports centre: %d matches (%d first airings, %d with Arabic names) | cinema: %d films (%d with a poster)' % (
        len(ms), len(live), len([m for m in ms if m['ar']]), len(fl), len([f for f in fl if f.get('icon')])))
    for m in live[:8]:
        log('   %s  %s  [%s]' % (time.strftime('%d/%m %H:%M', time.gmtime(m['start'])), m['ar'] or m['title'],
                                 ', '.join(m['channels'][:3])))
