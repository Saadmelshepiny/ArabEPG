#!/usr/bin/env python3
"""Translate the English programme titles of the Arabic-region guides to Arabic, offline.

Uses Argos Translate (open source, runs on the GitHub runner, no account, no daily limit).
The result is a dictionary {english title: arabic title} published as tr_ar.json.gz; the plugin
uses it before any online translation service. Every run only translates titles that are new,
most frequent first, within a time budget, so the dictionary grows run after run.

  python3 cloud/translate_cloud.py --guides public/guides --prev prev_tr.json.gz --out public/tr_ar.json.gz
"""
import argparse
import collections
import glob
import gzip
import json
import os
import re
import time
import xml.etree.ElementTree as ET

AR_FILES = ('ar_', 'ae', 'sa', 'eg', 'bein', 'oe_egypt', 'oe_uae', 'oe_qatar', 'oe_saudi', 'oe_arabia')
AR = re.compile(u'[ء-ي]')
LATIN = re.compile(r'[A-Za-z]{3}')
MAX_ENTRIES = 250000


def needs(t):
    return bool(LATIN.search(t)) and not AR.search(t)


def titles(guides_dir):
    count = collections.Counter()
    for path in sorted(glob.glob(os.path.join(guides_dir, '*.xml.gz'))):
        name = os.path.basename(path)[:-7]
        if not any(name == p or name.startswith(p) for p in AR_FILES):
            continue
        with gzip.open(path, 'rb') as f:
            for _ev, el in ET.iterparse(f, events=('end',)):
                if el.tag == 'programme':
                    t = (el.findtext('title') or '').strip()
                    if t and len(t) <= 200 and needs(t):
                        count[t] += 1
                    el.clear()
    return count


def load(path):
    try:
        with gzip.open(path, 'rb') as f:
            d = json.loads(f.read().decode('utf-8'))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def translator():
    import argostranslate.package as P
    import argostranslate.translate as T
    langs = dict((l.code, l) for l in T.get_installed_languages())
    if 'en' not in langs or 'ar' not in langs or not langs['en'].get_translation(langs['ar']):
        P.update_package_index()
        pkg = [p for p in P.get_available_packages() if p.from_code == 'en' and p.to_code == 'ar'][0]
        P.install_from_path(pkg.download())
        langs = dict((l.code, l) for l in T.get_installed_languages())
    return langs['en'].get_translation(langs['ar'])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--guides', required=True)
    ap.add_argument('--prev', default='')
    ap.add_argument('--out', required=True)
    ap.add_argument('--budget', type=int, default=1800, help='seconds of translation per run')
    a = ap.parse_args()
    t0 = time.time()
    prev = dict((k, v) for k, v in load(a.prev).items() if v and AR.search(v))
    count = titles(a.guides)
    todo = [t for t, _n in count.most_common() if t not in prev]
    print('titles: %d unique English, %d already translated, %d new' % (len(count), len(count) - len(todo), len(todo)))
    out = dict(prev)
    done = 0
    if todo:
        tr = translator()
        for t in todo:
            if time.time() - t0 > a.budget:
                break
            try:
                r = tr.translate(t).strip()
            except Exception as e:
                print('error:', t, e)
                continue
            if r and AR.search(r) and r != t:
                out[t] = r
                done += 1
    # keep the dictionary small: titles still in the guides first, then the newest older ones
    if len(out) > MAX_ENTRIES:
        keep = [t for t in out if t in count] + [t for t in out if t not in count]
        out = dict((t, out[t]) for t in keep[:MAX_ENTRIES])
    with gzip.open(a.out, 'wb', compresslevel=9) as f:
        f.write(json.dumps(out, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    covered = sum(n for t, n in count.items() if t in out)
    total = sum(count.values()) or 1
    print('translated %d new titles in %d s; dictionary %d; programmes covered %d%%' % (
        done, time.time() - t0, len(out), covered * 100 // total))


if __name__ == '__main__':
    main()
