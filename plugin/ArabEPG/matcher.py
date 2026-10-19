# -*- coding: utf-8 -*-
# Smart (fuzzy) matcher: links receiver channel names to online EPG channels.
#
# How it decides:
#   1. normalises names (HD/FHD/TV/قناة..., Arabic letter forms, "Al"/"El" prefixes)
#   2. Arabic is transliterated to Latin so "الحياة" can meet "Al Hayat"
#   3. a consonant "skeleton" absorbs spelling variants (Hayah/Hayat, Kahera/Qahera, Masr/Misr)
#   4. numbers must agree (MBC 2 never matches MBC 4, Sada El Balad 2 never matches Sada El Balad)
#   5. candidates come from a trigram index, so thousands of channels match in a second or two
from __future__ import absolute_import

import re
import unicodedata
from difflib import SequenceMatcher

try:
    text_type = unicode  # noqa: F821
except NameError:
    text_type = str


def _u(s):
    if isinstance(s, bytes):
        return s.decode('utf-8', 'replace')
    return s if isinstance(s, text_type) else text_type(s)


# ------------------------------------------------------------------ normalisation
_AR_WORDS = [
    (u'ام بي سي', u'mbc'), (u'امبي سي', u'mbc'), (u'ام.بي.سي', u'mbc'), (u'او اس ان', u'osn'),
    (u'سي بي سي', u'cbc'), (u'دي ام سي', u'dmc'), (u'بي ان', u'bein'), (u'ال بي سي', u'lbc'),
    (u'ام تي في', u'mtv'), (u'اس بي سي', u'sbc'), (u'ان بي ان', u'nbn'), (u'تي في', u' '),
]
_STRIP = re.compile(u'\\b(fhd|uhd|hd|sd|4k|8k|hevc|h265|h264|backup|bkp|test|tv|t\\.v|channel|chanel|ch|'
                    u'ott|iptv|live|plus1|\\+1|hq|lq|sat|satellite|feed)\\b')
_AR_STRIP = re.compile(u'(^|\\s)(قناه|تلفزيون|فضائيه|الفضائيه|اتش دي|اتش|بث|مباشر|الجديده)(?=\\s|$)')


_NUMS = {u'one': u'1', u'two': u'2', u'three': u'3', u'four': u'4', u'five': u'5', u'six': u'6',
         u'seven': u'7', u'eight': u'8', u'nine': u'9', u'ten': u'10'}


def base_norm(name):
    s = unicodedata.normalize('NFKC', _u(name)).lower()
    s = re.sub(u'[ً-ْـ‎‏‪-‮\x86\x87]', u'', s)
    s = s.replace(u'أ', u'ا').replace(u'إ', u'ا').replace(u'آ', u'ا').replace(u'ة', u'ه') \
         .replace(u'ى', u'ي').replace(u'ؤ', u'و').replace(u'ئ', u'ي')
    for a, b in _AR_WORDS:
        s = s.replace(a, b)
    s = s.replace(u'&amp;', u'&').replace(u'osntv', u'osn').replace(u'+', u' plus ')
    s = re.sub(u'[\\(\\[].*?[\\)\\]]', u' ', s)
    s = re.sub(u'[_\\-\\.:|/]', u' ', s)
    s = _STRIP.sub(u' ', s)
    s = _AR_STRIP.sub(u' ', s)
    s = re.sub(u'\\b(one|two|three|four|five|six|seven|eight|nine|ten)\\b',
               lambda m: _NUMS[m.group(1)], s)
    return re.sub(u'\\s+', u' ', s).strip()


def norm(name):
    """compact key used for exact matching (kept compatible with v1 mappings)."""
    s = re.sub(u'[^0-9a-zء-ي]', u'', base_norm(name))
    s = re.sub(u'^(al|el)(?=[a-z]{3})', u'', s)
    s = re.sub(u'^ال(?=..)', u'', s) if re.match(u'^ال...', s) else s
    return s


# Arabic -> Latin (Egyptian/Gulf friendly)
_TR = {
    u'ا': u'a', u'ب': u'b', u'ت': u't', u'ث': u's', u'ج': u'g', u'ح': u'h', u'خ': u'kh', u'د': u'd',
    u'ذ': u'z', u'ر': u'r', u'ز': u'z', u'س': u's', u'ش': u'sh', u'ص': u's', u'ض': u'd', u'ط': u't',
    u'ظ': u'z', u'ع': u'a', u'غ': u'gh', u'ف': u'f', u'ق': u'k', u'ك': u'k', u'ل': u'l', u'م': u'm',
    u'ن': u'n', u'ه': u'h', u'و': u'o', u'ي': u'i', u'ء': u'', u'ى': u'a',
}


def latin(name):
    s = norm(name)
    s = u''.join(_TR.get(c, c) for c in s)
    s = re.sub(u'^(al|el)(?=[a-z]{3})', u'', s)
    return s


def skeleton(lat):
    """consonant skeleton: tolerant to vowels and common spelling swaps."""
    s = lat.replace(u'ph', u'f').replace(u'q', u'k').replace(u'c', u'k').replace(u'j', u'g') \
        .replace(u'kh', u'k').replace(u'sh', u's').replace(u'th', u's').replace(u'dh', u'z') \
        .replace(u'x', u'ks').replace(u'v', u'f').replace(u'w', u'o').replace(u'y', u'i') \
        .replace(u'ee', u'i').replace(u'ou', u'o')
    s = re.sub(u'h$', u't', s)  # Hayah == Hayat (taa marbuta)
    if not s:
        return s
    head, tail = s[0], s[1:]
    tail = re.sub(u'[aeiou]', u'', tail)
    return re.sub(u'(.)\\1+', u'\\1', head + tail)


_GENERIC = re.compile(u'(drama|derama|cinema|sinima|movies|movie|aflam|series|sport|sports|news|kids|plus|masr|misr|'
                      u'comedy|classic|zaman|life|tv|one|max|action|music|mosalsalat|hd)')


def distinct(lat):
    """the part of a name that is not a generic word (\"fan drama\" -> \"fan\")."""
    return re.sub(u'\\d+', u'', _GENERIC.sub(u'', lat))


def digits(s):
    return u''.join(re.findall(u'\\d+', s))


def trigrams(s):
    s = u'#' + s + u'#'
    return set(s[i:i + 3] for i in range(len(s) - 2))


# ------------------------------------------------------------------ matcher
def source_of(key):
    """'shahid:387238' -> 'shahid' ; 'xml:it/Rai1.it' -> 'xml:it'"""
    src, _, rest = key.partition(':')
    if src == 'xml':
        return 'xml:' + rest.split('/', 1)[0]
    return src


def _allow_fn(allow):
    if allow is None:
        return lambda key: True
    if callable(allow):
        return allow
    allow = set(allow)
    return lambda key: source_of(key) in allow or key.split(':', 1)[0] in allow


class Matcher(object):
    def __init__(self, catalog, priority):
        """catalog: [{'src','id','names'}]; priority: tuple of source names (best first)."""
        self.prio = dict((s, i) for i, s in enumerate(priority))
        self.exact = {}
        self.items = []     # (latin, skeleton, digits, key)
        self.tri = {}
        for c in catalog:
            key = u'%s:%s' % (c['src'], c['id'])
            for n in c['names']:
                k = norm(n)
                if not k:
                    continue
                self._put_exact(k, key)
                lat = latin(n)
                if not lat:
                    continue
                self._put_exact(lat, key)
                idx = len(self.items)
                self.items.append((lat, skeleton(lat), digits(lat), key))
                for t in trigrams(lat):
                    self.tri.setdefault(t, []).append(idx)

    def _rank(self, key):
        return self.prio.get(key.split(':', 1)[0], 99)

    def _put_exact(self, k, key):
        lst = self.exact.setdefault(k, [])
        if key not in lst:
            lst.append(key)

    def match_all(self, name, allow=None, min_score=0.84, limit=4):
        """all good candidates, best first, at most one per source -> [(key, score)]"""
        ok = _allow_fn(allow)
        k = norm(name)
        if not k:
            return []
        lat = latin(name)
        found = {}
        for cand in (k, lat):
            for key in self.exact.get(cand, ()):
                if ok(key):
                    found[key] = 1.0
        if len(lat) >= 2:
            sk, dg = skeleton(lat), digits(lat)
            counts = {}
            for t in trigrams(lat):
                for i in self.tri.get(t, ()):
                    counts[i] = counts.get(i, 0) + 1
            need = max(1, len(lat) // 4)
            dl = distinct(lat)
            for i, n in counts.items():
                if n < need:
                    continue
                clat, csk, cdg, key = self.items[i]
                if key in found and found[key] >= 1.0:
                    continue
                if cdg != dg or not ok(key):
                    continue
                dc = distinct(clat)
                if dl != dc:
                    if not dl or not dc or min(len(dl), len(dc)) <= 3 or \
                            SequenceMatcher(None, skeleton(dl), skeleton(dc)).ratio() < 0.75:
                        continue  # only the generic words agree (Fan Drama != ON Drama, ON Sport != Oman Sport)
                if len(sk) >= 3 and csk == sk:
                    score = 0.95
                else:
                    r1 = SequenceMatcher(None, lat, clat).ratio()
                    r2 = SequenceMatcher(None, sk, csk).ratio() * 0.96 if len(sk) >= 3 else 0
                    score = max(r1, r2)
                if score >= min_score and score > found.get(key, 0):
                    found[key] = round(score, 2)
        ranked = sorted(found.items(), key=lambda kv: (-kv[1], self._rank(kv[0])))
        out, seen = [], set()
        for key, sc in ranked:
            src = source_of(key)
            if src in seen:
                continue
            seen.add(src)
            out.append((key, sc))
        # among equal scores prefer the official/preferred source order
        out.sort(key=lambda kv: (-round(kv[1], 1), self._rank(kv[0])))
        return out[:limit]

    def match(self, name, allow=None, min_score=0.84):
        """-> (key, score) or (None, 0)"""
        r = self.match_all(name, allow, min_score, 1)
        return r[0] if r else (None, 0)
