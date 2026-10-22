#!/usr/bin/env python3
"""Arabic for the English guides of Arabic channels, made on the GitHub runner. Three dictionaries:

  tr_ar_real.json.gz  English title/description -> the REAL Arabic one, learned from sites that publish both
                      (Shahid, OSN, beIN, Rotana: same channel, same time). Not a translation: always preferred.
  tr_ar.json.gz       machine translation of titles (Argos), only for titles without a real Arabic one
  tr_ar_desc.json.gz  machine translation of descriptions of the next 36 hours (sentences translate well;
                      the plugin shows them while the title stays as the channel wrote it)

Every run only translates what is new, most frequent first, within a time budget.

  python3 cloud/translate_cloud.py --guides public/guides --tr public/tr --out-dir public [--budget 1800]
"""
import argparse
import calendar
import collections
import glob
import gzip
import html
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

AR_FILES = ('ar_', 'ae', 'sa', 'eg', 'bein', 'oe_egypt', 'oe_uae', 'oe_qatar', 'oe_saudi', 'oe_arabia')
AR = re.compile(u'[ء-ي]')
LATIN = re.compile(r'[A-Za-z]{3}')
MAX_TITLES, MAX_DESC, MAX_REAL = 250000, 80000, 200000
DESC_HOURS = 36
SENT = re.compile(r'(?<=[.!?])\s+')


def usable(src, res):
    """reject half-translated or truncated results"""
    ar = len(AR.findall(res))
    lat = len(re.findall(r'[A-Za-z]', res))
    src_letters = len(re.findall(r'[A-Za-z]', src)) or 1
    return ar >= 2 and lat <= 0.35 * (ar + lat) and ar >= 0.3 * src_letters


def needs(t):
    return bool(LATIN.search(t)) and not AR.search(t)


def xtime(s):
    m = re.match(r'(\d{14})\s*([+-]\d{4})?', s or '')
    if not m:
        return None
    ts = calendar.timegm(time.strptime(m.group(1), '%Y%m%d%H%M%S'))
    if m.group(2):
        z = m.group(2)
        ts -= (1 if z[0] == '+' else -1) * (int(z[1:3]) * 3600 + int(z[3:5]) * 60)
    return ts


def collect(guides_dir):
    """English titles (all days) and descriptions (next 36 hours) of the Arabic-region guides"""
    titles, descs = collections.Counter(), collections.Counter()
    hi = time.time() + DESC_HOURS * 3600
    for path in sorted(glob.glob(os.path.join(guides_dir, '*.xml.gz'))):
        name = os.path.basename(path)[:-7]
        if not any(name == p or name.startswith(p) for p in AR_FILES):
            continue
        with gzip.open(path, 'rb') as f:
            for _ev, el in ET.iterparse(f, events=('end',)):
                if el.tag != 'programme':
                    continue
                t = (el.findtext('title') or '').strip()
                if t and len(t) <= 200 and needs(t):
                    titles[t] += 1
                st = xtime(el.get('start'))
                d = (el.findtext('desc') or '').strip()
                if d and st and st < hi and needs(d):
                    descs[d[:500]] += 1
                el.clear()
    return titles, descs


def load(path):
    try:
        with gzip.open(path, 'rb') as f:
            d = json.loads(f.read().decode('utf-8'))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save(path, d):
    with gzip.open(path, 'wb', compresslevel=9) as f:
        f.write(json.dumps(d, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def prev(repo, name):
    try:
        req = urllib.request.Request('https://raw.githubusercontent.com/%s/epg/%s' % (repo, name),
                                     headers={'User-Agent': 'SaadEPG-cloud'})
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.loads(gzip.decompress(r.read()).decode('utf-8'))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


class Engine(object):
    """English -> Arabic: CTranslate2 batches (fast); Argos one by one when that is not available."""

    def __init__(self):
        self.fast = self.slow = None
        try:
            from translate_en import Model
            self.fast = Model('en', to='ar')
        except Exception as e:
            print('fast translator not available (%s), using Argos' % e)
            import argostranslate.package as P
            import argostranslate.translate as T
            langs = dict((l.code, l) for l in T.get_installed_languages())
            if 'en' not in langs or 'ar' not in langs or not langs['en'].get_translation(langs['ar']):
                P.update_package_index()
                pkg = [p for p in P.get_available_packages() if p.from_code == 'en' and p.to_code == 'ar'][0]
                P.install_from_path(pkg.download())
                langs = dict((l.code, l) for l in T.get_installed_languages())
            self.slow = langs['en'].get_translation(langs['ar'])

    def run(self, texts, beam=2):
        if self.fast:
            return self.fast.run(texts, beam=beam)
        return [self.slow.translate(t) for t in texts]


def clean(t):
    return re.sub(r'\s+', ' ', html.unescape(t or '').replace('▁', ' ').replace('"', '')).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--guides', required=True)
    ap.add_argument('--tr', default='public/tr', help='folder with pairs_ar.json.gz made by build.py')
    ap.add_argument('--out-dir', default='public')
    ap.add_argument('--budget', type=int, default=1800, help='seconds of translation per run')
    a = ap.parse_args()
    t0 = time.time()
    repo = os.environ.get('GITHUB_REPOSITORY', 'Saadmelshepiny/ArabEPG')

    # 1. real Arabic titles/descriptions (the newest pairs win)
    real = prev(repo, 'tr_ar_real.json.gz')
    pairs = load(os.path.join(a.tr, 'pairs_ar.json.gz'))
    real.update(pairs)
    titles, descs = collect(a.guides)
    if len(real) > MAX_REAL:
        live = set(titles) | set(descs)
        keep = [k for k in real if k in live] + [k for k in real if k not in live]
        real = dict((k, real[k]) for k in keep[:MAX_REAL])
    save(os.path.join(a.out_dir, 'tr_ar_real.json.gz'), real)
    t_real = sum(n for t, n in titles.items() if t in real) * 100 // max(1, sum(titles.values()))
    print('real Arabic: %d (+%d this run); titles of English guides that have one: %d%%' % (
        len(real), len(pairs), t_real))

    # 2. machine titles, then 3. machine descriptions
    mt = dict((k, clean(v)) for k, v in prev(repo, 'tr_ar.json.gz').items() if v)
    mt = dict((k, v) for k, v in mt.items() if usable(k, v))
    md = dict((k, clean(v)) for k, v in prev(repo, 'tr_ar_desc.json.gz').items() if v)
    todo_t = [t for t, _n in titles.most_common() if t not in mt and t not in real]
    todo_d = [t for t, _n in descs.most_common() if t not in md and t not in real]
    print('titles: %d unique English, %d new | descriptions (%dh): %d unique, %d new' % (
        len(titles), len(todo_t), DESC_HOURS, len(descs), len(todo_d)))
    done_t = done_d = 0
    if todo_t or todo_d:
        try:
            eng = Engine()
        except Exception as e:
            eng = None
            print('no translator: %s' % e)
        if eng:
            for i in range(0, len(todo_t), 128):
                if time.time() - t0 > a.budget * 0.4:
                    break
                chunk = todo_t[i:i + 128]
                for src, res in zip(chunk, eng.run(chunk, beam=2)):
                    res = clean(res)
                    if res and res != src and usable(src, res):
                        mt[src] = res
                        done_t += 1
            for i in range(0, len(todo_d), 32):
                if time.time() - t0 > a.budget:
                    break
                chunk = todo_d[i:i + 32]
                parts = [[p for p in SENT.split(d) if p.strip()] for d in chunk]
                res = iter(eng.run([p for ps in parts for p in ps], beam=1))
                for src, ps in zip(chunk, parts):
                    out = clean(' '.join(next(res) for _p in ps))
                    if out and usable(src, out) and len(out) < 3 * len(src) + 40:
                        md[src] = out
                        done_d += 1
    if len(mt) > MAX_TITLES:
        keep = [t for t in mt if t in titles] + [t for t in mt if t not in titles]
        mt = dict((t, mt[t]) for t in keep[:MAX_TITLES])
    md = dict((k, v) for k, v in md.items() if k in descs) if len(md) > MAX_DESC else md
    save(os.path.join(a.out_dir, 'tr_ar.json.gz'), mt)
    save(os.path.join(a.out_dir, 'tr_ar_desc.json.gz'), md)
    cov_d = sum(n for t, n in descs.items() if t in md or t in real) * 100 // max(1, sum(descs.values()))
    print('machine: +%d titles (%d), +%d descriptions (%d, %d%% of the next %dh) in %ds' % (
        done_t, len(mt), done_d, len(md), cov_d, DESC_HOURS, time.time() - t0))


if __name__ == '__main__':
    main()
