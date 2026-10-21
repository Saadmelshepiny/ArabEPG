"""Shared by translate_en.py and build.py: which translation model a text of a foreign guide goes to."""
import re

# languages with an offline model to English (Argos Translate)
SUPPORTED = set('pl it el fr ro uk ru sl sq bg hu he de nl es pt cs sk tr da nb sv fi lt lv et ca gl'.split())
ALIAS = {'gr': 'el', 'sr': 'sl', 'hr': 'sl', 'bs': 'sl', 'me': 'sl', 'mk': 'bg', 'no': 'nb', 'nn': 'nb',
         'iw': 'he', 'ua': 'uk', 'cz': 'cs', 'dk': 'da', 'se': 'sv', 'ee': 'et', 'al': 'sq'}
CYR = re.compile('[Ѐ-ӿ]')
LETTER = re.compile(r'[^\W\d_]', re.U)

TITLE_MAX, DESC_MAX = 200, 500


def norm_lang(code):
    code = (code or '').strip().lower()[:2]
    return ALIAS.get(code, code)


def route(file_lang, attr, text):
    """-> model language for this text, or None (English already / no model / nothing to translate)"""
    if not text or not LETTER.search(text):
        return None
    lang = norm_lang(attr)
    if lang == 'en':
        return None
    if lang not in SUPPORTED:
        lang = norm_lang(file_lang)
    if lang == 'sl' and CYR.search(text):
        lang = 'ru'                    # Serbian / Macedonian in Cyrillic
    return lang if lang in SUPPORTED else None


def foreign_files(cfg):
    """[(name, url, lang)] of the ready XMLTV files that are not Arabic and not English"""
    out = []
    for name, d in (cfg.get('xmltv') or {}).items():
        lang = norm_lang(d.get('lang'))
        if d.get('url') and lang in SUPPORTED:
            out.append((name, d['url'], lang))
    return out
