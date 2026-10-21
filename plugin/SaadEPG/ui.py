# -*- coding: utf-8 -*-
# Saad EPG - look of the screens: one palette, a digital font for times and numbers,
# and a column table (boxes per cell) used by every list screen.
from __future__ import absolute_import

import os
import re

from enigma import getDesktop, eListboxPythonMultiContent, gFont, RT_HALIGN_LEFT, RT_HALIGN_RIGHT, \
    RT_HALIGN_CENTER, RT_VALIGN_CENTER
from Components.MenuList import MenuList

from .sources import u, PY3

HERE = os.path.dirname(os.path.abspath(__file__))
_F = getDesktop(0).size().width() / 1280.0


def S(v):
    return int(v * _F)


def T(s):
    s = u(s)
    return s if PY3 else s.encode('utf-8')


# --------------------------------------------------------------------- palette (soft, calm, readable on TV)
P = {
    'bg': 0x0d1520,        # screen
    'panel': 0x15202d,     # header / side panels
    'cell': 0x1b2837,      # table cells
    'cell2': 0x182433,     # every other row
    'head': 0x233a52,      # column titles
    'sel': 0x2b5a85,       # cells of the selected row
    'frame': 0x56b6e0,     # thin frame around the selected row
    'accent': 0x46c08f,    # green
    'blue': 0x5aaee6,
    'amber': 0xe6b85c,
    'red': 0xe27d6e,
    'violet': 0xa58be0,
    'track': 0x2a3a4d,     # empty part of a progress bar
    'txt': 0xeef3f8,
    'dim': 0x9fb1c4,
}


def hexc(name):
    return '#%06x' % P[name]


BG, PANEL, CELL, HEAD, ACCENT, TXT, DIM = (hexc(x) for x in ('bg', 'panel', 'cell', 'head', 'accent', 'txt', 'dim'))
SEL, FRAME, BLUE, AMBER, RED = hexc('sel'), hexc('frame'), hexc('blue'), hexc('amber'), hexc('red')

# --------------------------------------------------------------------- fonts
DIGIT = 'Regular'
try:
    from enigma import addFont
    _ttf = os.path.join(HERE, 'fonts', 'ShareTechMono-Regular.ttf')
    if os.path.exists(_ttf):
        addFont(_ttf, 'SaadDigit', 100, False)
        DIGIT = 'SaadDigit'
except Exception as _e:
    print('[SaadEPG] digital font not loaded', _e)

_AR = re.compile(u'[؀-ۿ]')


def is_ar(text):
    return bool(_AR.search(u(text or u'')))


ALIGN = {'left': RT_HALIGN_LEFT, 'right': RT_HALIGN_RIGHT, 'center': RT_HALIGN_CENTER}


# --------------------------------------------------------------------- skin pieces
def frame(w, h, body, title='Saad EPG'):
    """the window: header bar (title on the right, digital clock on the left), then the body."""
    hh = 56
    head = (
        '<eLabel position="0,0" size="%d,%d" backgroundColor="%s" zPosition="-3" />'
        '<eLabel position="0,0" size="%d,%d" backgroundColor="%s" zPosition="-2" />'
        '<eLabel position="0,%d" size="%d,%d" backgroundColor="%s" zPosition="-2" />'
        '<eLabel position="0,%d" size="%d,%d" backgroundColor="%s" zPosition="-2" />'
        '<widget source="global.CurrentTime" render="Label" position="%d,%d" size="%d,%d" font="%s;%d" '
        'foregroundColor="%s" backgroundColor="%s" transparent="1" halign="left" valign="center">'
        '<convert type="ClockToText">Format:%%H:%%M</convert></widget>'
        '<widget source="global.CurrentTime" render="Label" position="%d,%d" size="%d,%d" font="%s;%d" '
        'foregroundColor="%s" backgroundColor="%s" transparent="1" halign="left" valign="center">'
        '<convert type="ClockToText">Format:%%d.%%m.%%Y</convert></widget>'
        '<widget name="title" position="%d,%d" size="%d,%d" font="Regular;%d" foregroundColor="%s" '
        'backgroundColor="%s" transparent="1" halign="right" valign="center" />'
    ) % (S(w), S(h), BG,
         S(w), S(hh), PANEL,
         S(hh), S(w), S(2), ACCENT,
         S(h - 3), S(w), S(3), PANEL,
         S(18), S(6), S(110), S(44), DIGIT, S(34), hexc('blue'), PANEL,
         S(128), S(10), S(140), S(40), DIGIT, S(19), DIM, PANEL,
         S(280), S(6), S(w - 300), S(44), S(27), TXT, PANEL)
    return T(u'<screen position="center,center" size="%d,%d" title="%s" backgroundColor="%s" flags="wfNoBorder">'
             u'%s%s</screen>' % (S(w), S(h), title, BG, u(head), u(body)))


def box(x, y, w, h, color=None):
    return '<eLabel position="%d,%d" size="%d,%d" backgroundColor="%s" zPosition="-1" />' % (
        S(x), S(y), S(w), S(h), color or PANEL)


def label(name, x, y, w, h, size=18, color=None, bg=None, align='right', valign='top', font='Regular', z=1):
    return ('<widget name="%s" position="%d,%d" size="%d,%d" font="%s;%d" foregroundColor="%s" '
            'backgroundColor="%s" transparent="1" halign="%s" valign="%s" zPosition="%d" />') % (
        name, S(x), S(y), S(w), S(h), font, S(size), color or TXT, bg or BG, align, valign, z)


def list_widget(name, x, y, w, h, bg=None):
    return ('<widget name="%s" position="%d,%d" size="%d,%d" scrollbarMode="showOnDemand" transparent="0" '
            'backgroundColor="%s" foregroundColor="%s" backgroundColorSelected="%s" foregroundColorSelected="%s" />') % (
        name, S(x), S(y), S(w), S(h), bg or BG, TXT, FRAME, TXT)


BTN = (('red', 0xe0604f), ('green', 0x3fb37e), ('yellow', 0xe2b33c), ('blue', 0x4d8fd6))


def buttons(y, w):
    """four colour keys: a box with a coloured strip on its right side."""
    out = ''
    bw = (w - 50) // 4
    for i, (n, col) in enumerate(BTN):
        x = w - 10 - (i + 1) * bw - i * 10          # red on the right (Arabic reading order)
        out += ('<eLabel position="%d,%d" size="%d,%d" backgroundColor="%s" zPosition="-1" />'
                '<eLabel position="%d,%d" size="%d,%d" backgroundColor="#%06x" zPosition="0" />'
                '<widget name="key_%s" zPosition="1" position="%d,%d" size="%d,%d" font="Regular;%d" halign="center" '
                'valign="center" foregroundColor="%s" backgroundColor="%s" transparent="1" />') % (
            S(x), S(y), S(bw), S(38), PANEL,
            S(x + bw - 8), S(y), S(8), S(38), col,
            n, S(x), S(y), S(bw - 8), S(38), S(19), TXT, PANEL)
    return out


def stat_cards(prefix, n, x, y, w, h=70):
    """n boxes side by side, each with a big digital number and a small caption (widgets <prefix>N / <prefix>Nt)."""
    out = ''
    gap = 10
    cw = (w - gap * (n - 1)) // n
    for i in range(n):
        cx = x + w - (i + 1) * cw - i * gap
        out += box(cx, y, cw, h, PANEL)
        out += '<eLabel position="%d,%d" size="%d,%d" backgroundColor="%s" zPosition="0" />' % (
            S(cx), S(y), S(cw), S(3), ACCENT if i == 0 else hexc('blue'))
        out += label('%s%d' % (prefix, i), cx + 8, y + 6, cw - 16, 38, 30, TXT, PANEL, 'center', 'center', DIGIT)
        out += label('%s%dt' % (prefix, i), cx + 8, y + 42, cw - 16, 24, 16, DIM, PANEL, 'center', 'center')
    return out


# --------------------------------------------------------------------- table
class Col(object):
    def __init__(self, title, width, align='auto', font=0, color='txt'):
        self.title, self.width, self.align, self.font, self.color = title, width, align, font, color
        self.x = 0


class Table(object):
    """columns laid out from right to left (Arabic order); every cell is its own box.

    cell value: a text, or a dict: {'t': text, 'fg': colour name, 'bg': colour name, 'pct': 0..100,
                                    'chip': colour name (a small coloured box with the text inside)}"""
    GAP = 4

    def __init__(self, width, cols, row_h=40, font=19, small=15):
        self.width, self.cols, self.row_h = width, cols, row_h
        x = width - 4
        for c in cols:
            c.x = x - c.width
            x = c.x - self.GAP
        self.fonts = (gFont('Regular', S(font)), gFont(DIGIT, S(font + 2)), gFont('Regular', S(small)))

    def header(self, x0, y0, h=34):
        out = ''
        for c in self.cols:
            out += '<eLabel position="%d,%d" size="%d,%d" backgroundColor="%s" zPosition="-1" />' % (
                S(x0 + c.x), S(y0), S(c.width), S(h), HEAD)
            out += ('<eLabel position="%d,%d" size="%d,%d" text="%s" font="Regular;%d" foregroundColor="%s" '
                    'backgroundColor="%s" halign="center" valign="center" transparent="1" zPosition="0" />') % (
                S(x0 + c.x), S(y0), S(c.width), S(h), c.title, S(16), DIM, HEAD)
        return out

    def widget(self):
        return TableList(self)

    def row(self, payload, values, n=0):
        res = [payload]
        h = S(self.row_h)
        pad = S(3)
        bg = P['cell'] if n % 2 == 0 else P['cell2']
        for c, v in zip(self.cols, values):
            if not isinstance(v, dict):
                v = {'t': v}
            text = u(v.get('t', u''))
            x, w = S(c.x), S(c.width)
            cbg = P[v['bg']] if v.get('bg') else bg
            res.append((eListboxPythonMultiContent.TYPE_TEXT, x, pad, w, h - 2 * pad, 0, 0, '',
                        None, None, cbg, P['sel']))
            if v.get('chip'):
                cw = min(w - S(8), S(14) * max(2, len(text)) + S(12))
                res.append((eListboxPythonMultiContent.TYPE_TEXT, x + (w - cw) // 2, pad + S(6), cw, h - 2 * pad - S(12),
                            2, RT_HALIGN_CENTER | RT_VALIGN_CENTER, T(text), P['bg'], P['bg'], P[v['chip']],
                            P[v['chip']]))
                continue
            al = c.align
            if al == 'auto':
                al = 'right' if is_ar(text) else 'left'
            th = h - 2 * pad
            if v.get('pct') is not None:
                th -= S(8)
                p = max(0, min(100, int(v['pct'])))
                bw = w - S(16)
                res.append((eListboxPythonMultiContent.TYPE_TEXT, x + S(8), h - pad - S(9), bw, S(4), 0, 0, '',
                            None, None, P['track'], P['track']))
                if p:
                    fw = max(S(2), bw * p // 100)
                    fx = x + S(8) + (bw - fw if al == 'right' else 0)
                    res.append((eListboxPythonMultiContent.TYPE_TEXT, fx, h - pad - S(9), fw, S(4), 0, 0, '',
                                None, None, P[v.get('bar', 'accent')], P[v.get('bar', 'accent')]))
            fg = P[v.get('fg', c.color)]
            res.append((eListboxPythonMultiContent.TYPE_TEXT, x + S(8), pad, w - S(16), th,
                        v.get('font', c.font), ALIGN[al] | RT_VALIGN_CENTER, T(text), fg, P['txt']))
        return res


class TableList(MenuList):
    def __init__(self, table):
        MenuList.__init__(self, [], enableWrapAround=True, content=eListboxPythonMultiContent)
        self.table = table
        for i, f in enumerate(table.fonts):
            self.l.setFont(i, f)
        self.l.setItemHeight(S(table.row_h))

    def applySkin(self, desktop, parent):
        # the skin must not replace the fonts / row height set above
        if self.skinAttributes:
            self.skinAttributes = [(k, v) for k, v in self.skinAttributes if k not in ('font', 'itemHeight')]
        res = MenuList.applySkin(self, desktop, parent)
        for i, f in enumerate(self.table.fonts):
            self.l.setFont(i, f)
        self.l.setItemHeight(S(self.table.row_h))
        return res

    def setRows(self, rows, keep=None):
        self.setList(rows)

    def payload(self):
        c = self.getCurrent()
        return c[0] if c else None
