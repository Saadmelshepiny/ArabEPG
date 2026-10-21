#!/usr/bin/env python3
"""Translate the foreign (non-Arabic) guides to English, offline, on the GitHub runner.

Runs as one job of the grab matrix (site "translate", part = languages, e.g. "pl" or "it+fr").
For every language: titles first (most frequent first), then categories, then the descriptions of
the next 36 hours, within a time budget. What was translated before is kept, so the dictionaries
grow run after run. Output: work/out_tren_<lang>.xml  (gzip JSON {original: english}).

Models: Argos Translate packages, run directly with CTranslate2 + SentencePiece (fast batches).

  python3 cloud/translate_en.py --langs pl+it --work work [--budget 3000]
"""
import argparse
import calendar
import collections
import glob
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from tr_common import route, foreign_files, TITLE_MAX, DESC_MAX  # noqa: E402

UA = 'Mozilla/5.0 (X11; Linux x86_64) SaadEPG-cloud/1.2'
INDEX = 'https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json'
MAX_TITLES, MAX_DESC = 200000, 60000
DESC_HOURS = 36


def get(url, tries=3, timeout=300):
    err = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            err = e
            time.sleep(5 * (i + 1))
    raise err


def xtime(s):
    m = re.match(r'(\d{14})\s*([+-]\d{4})?', s or '')
    if not m:
        return None
    ts = calendar.timegm(time.strptime(m.group(1), '%Y%m%d%H%M%S'))
    if m.group(2):
        z = m.group(2)
        ts -= (1 if z[0] == '+' else -1) * (int(z[1:3]) * 3600 + int(z[3:5]) * 60)
    return ts


def collect(cfg, langs):
    """-> {lang: {'t': Counter(title/category), 'd': Counter(description)}}"""
    days = cfg.get('days', 3)
    now = time.time()
    lo, hi, dhi = now - 6 * 3600, now + days * 86400, now + DESC_HOURS * 3600
    out = dict((l, {'t': collections.Counter(), 'd': collections.Counter()}) for l in langs)
    for name, url, flang in foreign_files(cfg):
        try:
            data = get(url)
        except Exception as e:
            print('%-12s download failed: %s' % (name, e))
            continue
        try:
            if data[:2] == b'\x1f\x8b':
                data = gzip.decompress(data)
        except Exception as e:
            print('%-12s bad gzip: %s' % (name, e))
            continue
        if len(data.strip()) < 50:
            print('%-12s empty file, skipped' % name)
            continue
        n = 0
        try:
            n = _scan(data, flang, out, lo, hi, dhi)
        except ET.ParseError as e:
            print('%-12s broken XML (%s): kept what was read' % (name, e))
            n = -1
        print('%-12s %6d titles for %s' % (name, max(n, 0), '+'.join(langs)))
    return out


def _scan(data, flang, out, lo, hi, dhi):
    n = 0
    for _ev, el in ET.iterparse(io.BytesIO(data), events=('end',)):
        if el.tag != 'programme':
            if el.tag == 'channel':
                el.clear()
            continue
        st = xtime(el.get('start'))
        if st and lo < st < hi:
            t = el.find('title')
            if t is not None and t.text:
                txt = t.text.strip()[:TITLE_MAX]
                l = route(flang, t.get('lang'), txt)
                if l in out:
                    out[l]['t'][txt] += 1
                    n += 1
            c = el.find('category')
            if c is not None and c.text:
                txt = c.text.strip()[:60]
                l = route(flang, c.get('lang'), txt)
                if l in out:
                    out[l]['t'][txt] += 1
            if st < dhi:
                d = el.find('desc')
                if d is not None and d.text:
                    txt = d.text.strip()[:DESC_MAX]
                    l = route(flang, d.get('lang'), txt)
                    if l in out:
                        out[l]['d'][txt] += 1
        el.clear()
    return n


# --------------------------------------------------------------------- models
_INDEX = None


def model_for(lang, root='/tmp/argos'):
    """download and unpack the Argos package <lang> -> en. -> (ctranslate2 model dir, sentencepiece file)"""
    global _INDEX
    if _INDEX is None:
        _INDEX = json.loads(get(INDEX).decode('utf-8'))
    pk = [p for p in _INDEX if p.get('from_code') == lang and p.get('to_code') == 'en']
    if not pk:
        raise RuntimeError('no model %s->en' % lang)
    url = [x for x in pk[0].get('links') or [] if x.startswith('http')][0]
    dest = os.path.join(root, lang)
    if not glob.glob(os.path.join(dest, '**', 'model.bin'), recursive=True):
        os.makedirs(dest, exist_ok=True)
        zipfile.ZipFile(io.BytesIO(get(url, timeout=900))).extractall(dest)
    mb = glob.glob(os.path.join(dest, '**', 'model.bin'), recursive=True)
    spm = [p for p in glob.glob(os.path.join(dest, '**', '*.model'), recursive=True) if 'stanza' not in p]
    if not mb or not spm:
        raise RuntimeError('unexpected package layout for %s' % lang)
    return os.path.dirname(mb[0]), spm[0]


class Model(object):
    def __init__(self, lang):
        import ctranslate2
        import sentencepiece
        mdir, spm = model_for(lang)
        threads = os.cpu_count() or 2
        self.tr = ctranslate2.Translator(mdir, device='cpu', inter_threads=1, intra_threads=threads)
        self.sp = sentencepiece.SentencePieceProcessor(model_file=spm)

    def run(self, texts, beam=2):
        toks = [self.sp.encode(t, out_type=str)[:200] for t in texts]
        res = self.tr.translate_batch(toks, beam_size=beam, max_batch_size=64, max_decoding_length=256)
        return [self.sp.decode(r.hypotheses[0]) for r in res]


SENT = re.compile(r'(?<=[.!?…])\s+')


def good(src, res):
    if not res or len(res) > 3 * len(src) + 30:
        return False
    words = res.lower().split()
    for i in range(len(words) - 4):            # "the the the the" = a broken result
        if len(set(words[i:i + 5])) == 1:
            return False
    return True


def tidy(src, res):
    res = res.replace(' ,', ',').strip()
    if src.isupper() and len(src) > 3:
        res = res.upper()
    elif src[:1].isupper() and res[:1].islower():
        res = res[:1].upper() + res[1:]
    return res


def translate_lang(lang, counts, prev, deadline):
    out = dict(prev)
    titles = [t for t, _n in counts['t'].most_common() if t not in out]
    descs = [t for t, _n in counts['d'].most_common() if t not in out]
    print('[%s] titles/categories: %d known, %d new | descriptions (%dh): %d new' % (
        lang, len(counts['t']) - len(titles), len(titles), DESC_HOURS, len(descs)))
    if not titles and not descs:
        return out, 0
    try:
        m = Model(lang)
    except Exception as e:
        print('[%s] model not available: %s' % (lang, e))
        return out, 0
    done = 0
    for i in range(0, len(titles), 256):
        if time.time() > deadline:
            break
        chunk = titles[i:i + 256]
        for src, res in zip(chunk, m.run(chunk, beam=2)):
            res = tidy(src, res)
            if good(src, res):
                out[src] = res
                done += 1
    for i in range(0, len(descs), 64):
        if time.time() > deadline:
            break
        chunk = descs[i:i + 64]
        parts = [SENT.split(d) for d in chunk]
        flat = [p for ps in parts for p in ps if p.strip()]
        res = iter(m.run(flat, beam=1))
        for src, ps in zip(chunk, parts):
            en = ' '.join(next(res) for p in ps if p.strip())
            if good(src, en):
                out[src] = en
                done += 1
    return out, done


def prune(d, counts):
    """keep the dictionary small: texts still in the guides first, then the older ones"""
    if len(d) <= MAX_TITLES + MAX_DESC:
        return d
    live = set(counts['t']) | set(counts['d'])
    keep = [k for k in d if k in live] + [k for k in d if k not in live]
    return dict((k, d[k]) for k in keep[:MAX_TITLES + MAX_DESC])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--langs', required=True, help='pl or it+fr')
    ap.add_argument('--work', default='work')
    ap.add_argument('--budget', type=int, default=0, help='seconds (default: sources.json translate.budget)')
    a = ap.parse_args()
    t0 = time.time()
    cfg = json.load(open(os.path.join(HERE, 'sources.json'), encoding='utf8'))
    tcfg = cfg.get('translate') or {}
    budget = a.budget or int(tcfg.get('budget', 3000))
    langs = [l for l in a.langs.split('+') if l]
    os.makedirs(a.work, exist_ok=True)
    repo = os.environ.get('GITHUB_REPOSITORY', 'Saadmelshepiny/ArabEPG')
    print('languages: %s, budget %ds, python %s' % ('+'.join(langs), budget, sys.version.split()[0]))
    counts = collect(cfg, langs)
    # titles of every language before the descriptions of any: split the time fairly
    for n, lang in enumerate(langs):
        try:
            prev = json.loads(gzip.decompress(get(
                'https://raw.githubusercontent.com/%s/epg/tr/en_%s.json.gz' % (repo, lang), tries=2)).decode('utf-8'))
        except Exception:
            prev = {}
        left = budget - (time.time() - t0)
        deadline = time.time() + max(60, left / (len(langs) - n))
        try:
            d, done = translate_lang(lang, counts[lang], prev, deadline)
        except Exception:
            import traceback
            traceback.print_exc()
            d, done = prev, 0
        d = prune(d, counts[lang])
        with gzip.open(os.path.join(a.work, 'out_tren_%s.xml' % lang), 'wb', compresslevel=9) as f:
            f.write(json.dumps(d, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
        tot_t = sum(counts[lang]['t'].values()) or 1
        cov = sum(n for t, n in counts[lang]['t'].items() if t in d) * 100 // tot_t
        print('[%s] +%d translated, dictionary %d, programmes covered %d%%, %ds' % (
            lang, done, len(d), cov, time.time() - t0))


if __name__ == '__main__':
    main()
