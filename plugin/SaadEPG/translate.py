# -*- coding: utf-8 -*-
# Translate English programme titles to Arabic (for channels that only have an English guide).
#
# Providers (all public, no account needed for the default):
#   mymemory : https://mymemory.translated.net  free, ~5000 characters a day
#              (~50000 when an e-mail address is given in the settings)
#   libre    : any LibreTranslate server (your own, or a public one with an API key)
# Every translation is cached on disk, so a title is translated only once; the guide
# fills up over a few days even with a small daily allowance.
from __future__ import absolute_import

import gzip
import json
import os
import re
import threading
import time

from . import sources
from .sources import log, u

try:
    from urllib.parse import quote
except ImportError:  # Python 2
    from urllib import quote


_SERVICE_MSG = re.compile(u'(INVALID EMAIL|MYMEMORY|QUERY LENGTH LIMIT|PLEASE SELECT|NO QUERY SPECIFIED|'
                          u'TOO MANY REQUESTS|INVALID LANGUAGE PAIR|AUTHENTICATION|USAGE LIMITS)', re.I)


# Arabic names written in Latin letters ("Bedon Sabeq Enthar", "Al A'a'edon", "3esh Sabahak"): a machine
# translation of them is nonsense, so they stay as written unless the real Arabic title is known.
_FRANCO_WORD = re.compile(u"(?i)\\b(al|el|wal|wel|wa|we|bel|fel|lel|ya|abu|abo|om|umm|bent|bint|ibn|ben|ahl|"
                          u"masr|misr|hekayat|hikayat|mosalsal|mosalsalat|aflam|sabeq|bedon|bidun|enta|enty|ana|"
                          u"ahla|habibi|sahra|leila|layali|zaman|baba|mama|ostaz|sheikh|haga|kalam|nas|beit|bait|"
                          u"hob|alb|eid|ramadan|el3|wl)\\b")
_FRANCO_SIGN = re.compile(u"[a-z]'[a-z]|[a-z][2357][a-z]|\\b[2357][a-z]|(aa|ee|oo)[a-z]*(kh|gh|sh|dh|th)", re.I)


def franco(text):
    t = u(text or u'')
    return bool(_FRANCO_SIGN.search(t)) or len(_FRANCO_WORD.findall(t)) >= 1


class Translator(object):
    """real Arabic titles (learned from bilingual sites) first; machine translation of descriptions, and of
    titles only when asked (mode 'all'); mode 'none' leaves the English guide as it is."""

    def __init__(self, cache_path, provider='mymemory', email='', libre_url='', libre_key='', cloud_path=None,
                 real_path=None, desc_path=None, mode='desc'):
        self.path = cache_path
        self.provider = provider
        email = re.sub(u'[\\s\u200b-\u200f\u202a-\u202e]+', u'', u(email or u'')).lower()
        if email and not re.match(u'^[a-z0-9._%+\\-]+@[a-z0-9.\\-]+\\.[a-z]{2,}$', email):
            log(u'الترجمة: الإيميل في الإعدادات غير صالح (%d حرف) - لن يُرسل' % len(email))
            email = u''
        self.email = email
        self.libre_url = libre_url.rstrip('/')
        self.libre_key = libre_key
        self.cache = {}
        self.dirty = False
        self.exhausted = False
        self.lock = threading.Lock()
        try:
            with gzip.open(self.path, 'rb') as f:
                self.cache = json.loads(f.read().decode('utf-8'))
        except Exception:
            self.cache = {}
        self.mode = mode
        self.cloud = self._read(cloud_path)       # machine titles from the cloud (no daily limit)
        self.real = self._read(real_path)         # real Arabic titles/descriptions of bilingual sites
        self.cdesc = self._read(desc_path)        # machine descriptions from the cloud
        # drop anything that is not a real Arabic translation (e.g. "INVALID EMAIL PROVIDED" from 4.0/4.1)
        bad = [k for k, v in self.cache.items() if not self.good(v)]
        for k in bad:
            del self.cache[k]
        if bad:
            self.dirty = True
            log(u'الترجمة: تم حذف %d ترجمة غير صحيحة من الذاكرة' % len(bad))

    @staticmethod
    def _read(path):
        if not path:
            return {}
        try:
            with gzip.open(path, 'rb') as f:
                d = json.loads(f.read().decode('utf-8'))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def good(text):
        """a usable translation: mostly Arabic, no service message, not half English."""
        t = u(text or u'')
        ar = len(re.findall(u'[\u0621-\u064a]', t))
        lat = len(re.findall(u'[A-Za-z]', t))
        return ar >= 2 and lat <= 0.35 * (ar + lat) and u'&quot;' not in t and not _SERVICE_MSG.search(t)

    def save(self):
        if not self.dirty:
            return
        try:
            d = os.path.dirname(self.path)
            if not os.path.isdir(d):
                os.makedirs(d)
            with gzip.open(self.path + '.tmp', 'wb') as f:
                f.write(json.dumps(self.cache, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
            os.rename(self.path + '.tmp', self.path)
            self.dirty = False
        except Exception as e:
            log(u'تعذر حفظ ذاكرة الترجمة: %s' % e)

    @staticmethod
    def needs(text):
        t = u(text)
        return bool(re.search(u'[A-Za-z]{3}', t)) and not re.search(u'[؀-ۿ]', t)

    def get(self, text):
        t = u(text).strip()
        return self.cache.get(t) or self.cloud.get(t)

    # ---- providers
    def _mymemory(self, text):
        url = 'https://api.mymemory.translated.net/get?q=%s&langpair=en|ar' % quote(text.encode('utf-8'))
        if self.email:
            url += '&de=' + quote(self.email)
        data = json.loads(sources.http_get(url, timeout=20).decode('utf-8'))
        res = u((data.get('responseData') or {}).get('translatedText') or u'')
        if u'INVALID EMAIL' in res.upper() and self.email:
            with self.lock:
                if self.email:
                    log(u'الترجمة: MyMemory رفض الإيميل المكتوب في الإعدادات - سيُكمل بدون إيميل (الحد اليومي الأصغر)')
                    self.email = ''
            return self._mymemory(text)
        if data.get('quotaFinished') or u'MYMEMORY WARNING' in res.upper() or \
                data.get('responseStatus') in (429, '429'):
            self.exhausted = True
            return None
        return res if self.good(res) else None

    def _libre(self, texts):
        body = json.dumps({'q': texts, 'source': 'en', 'target': 'ar', 'format': 'text',
                           'api_key': self.libre_key}).encode('utf-8')
        data = json.loads(sources.http_get(self.libre_url + '/translate', {'Content-Type': 'application/json'},
                                           timeout=60, data=body).decode('utf-8'))
        out = data.get('translatedText')
        return out if isinstance(out, list) else [out]

    def translate_many(self, texts, limit=1500, stop=None):
        """translate unknown texts (unique, longest-lived first); returns how many were added."""
        todo = []
        seen = set()
        for t in texts:
            t = u(t).strip()
            if t and t not in seen and t not in self.cache and not self.good(self.cloud.get(t)) and self.needs(t) \
                    and t not in self.real and not franco(t):
                seen.add(t)
                todo.append(t)
        todo = todo[:limit]
        if not todo or self.provider == 'cloud':
            return 0
        added = [0]
        if self.provider == 'libre' and self.libre_url:
            for i in range(0, len(todo), 40):
                if stop and stop():
                    break
                part = todo[i:i + 40]
                try:
                    res = self._libre(part)
                except Exception as e:
                    log(u'الترجمة (LibreTranslate): %s' % e)
                    break
                for src, dst in zip(part, res):
                    if dst and dst != src and self.good(dst):
                        self.cache[src] = u(dst)
                        added[0] += 1
            self.dirty = self.dirty or bool(added[0])
            return added[0]

        queue = list(todo)

        def worker():
            while True:
                with self.lock:
                    if not queue or self.exhausted or (stop and stop()):
                        return
                    t = queue.pop(0)
                try:
                    r = self._mymemory(t)
                except Exception as e:
                    log(u'الترجمة: %s' % e)
                    time.sleep(1)
                    continue
                if r and r != t:
                    with self.lock:
                        self.cache[t] = r
                        added[0] += 1
                time.sleep(0.2)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for th in threads:
            th.daemon = True
            th.start()
        for th in threads:
            th.join()
        if self.exhausted:
            log(u'الترجمة: انتهت الحصة اليومية المجانية - سيكمل البلوجين الترجمة في التحديثات القادمة')
        self.dirty = self.dirty or bool(added[0])
        return added[0]

    def apply(self, events, with_short=True):
        """-> (events with Arabic where it is known, number of programmes changed)
        title: the real Arabic one; else (mode 'all' only) a machine translation, the original kept in the details;
        Arabic names in Latin letters stay as written. description: real Arabic, else machine translation."""
        out, n = [], 0
        for e in events:
            title, short, desc = e[2], e[3], e[4]
            changed = False
            if self.mode == 'none':
                out.append(tuple(e))
                continue
            t = u(title).strip()
            if self.needs(t):
                ar = self.real.get(t)
                if ar:
                    title, changed = ar, True
                elif self.mode == 'all' and not franco(t):
                    ar = self.get(t)
                    if ar and self.good(ar):
                        desc = (u'%s\n(%s)' % (desc, t)) if desc else u'(%s)' % t
                        title, changed = ar, True
            if with_short and short and self.needs(short):
                ar = self.real.get(u(short).strip()) or self.get(short)
                if ar and self.good(ar):
                    short = ar
            d = u(desc or u'').strip()
            if d and self.needs(d):
                key = d[:500]
                ar = self.real.get(key) or self.cdesc.get(key)
                if ar and self.good(ar):
                    desc, changed = ar, True
            n += 1 if changed else 0
            out.append((e[0], e[1], title, short, desc, e[5]))
        return out, n
