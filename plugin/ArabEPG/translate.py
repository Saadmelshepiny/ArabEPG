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


class Translator(object):
    def __init__(self, cache_path, provider='mymemory', email='', libre_url='', libre_key=''):
        self.path = cache_path
        self.provider = provider
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
        return self.cache.get(u(text).strip())

    # ---- providers
    def _mymemory(self, text):
        url = 'https://api.mymemory.translated.net/get?q=%s&langpair=en|ar' % quote(text.encode('utf-8'))
        if self.email:
            url += '&de=' + quote(self.email)
        data = json.loads(sources.http_get(url, timeout=20).decode('utf-8'))
        res = u((data.get('responseData') or {}).get('translatedText') or u'')
        if data.get('quotaFinished') or u'MYMEMORY WARNING' in res or data.get('responseStatus') in (429, '429'):
            self.exhausted = True
            return None
        return res

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
            if t and t not in seen and t not in self.cache and self.needs(t):
                seen.add(t)
                todo.append(t)
        todo = todo[:limit]
        if not todo:
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
                    if dst and dst != src:
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
        """replace English titles with their cached Arabic translation (original kept in the description)."""
        out, n = [], 0
        for e in events:
            title, short, desc = e[2], e[3], e[4]
            ar = self.get(title) if self.needs(title) else None
            if ar:
                desc = (u'%s\n(%s)' % (desc, title)) if desc else u'(%s)' % title
                title = ar
                n += 1
            if with_short and short and self.needs(short) and self.get(short):
                short = self.get(short)
            out.append((e[0], e[1], title, short, desc, e[5]))
        return out, n
