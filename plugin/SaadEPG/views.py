# -*- coding: utf-8 -*-
# Saad EPG - the big screens: the full guide (grid), sports centre, cinema tonight, my series, series map.
from __future__ import absolute_import

import hashlib
import os
import threading
import time

from enigma import eEPGCache, eServiceReference, eListboxPythonMultiContent, eTimer, RT_HALIGN_RIGHT, \
    RT_HALIGN_LEFT, RT_HALIGN_CENTER, RT_VALIGN_CENTER
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList
from Components.Pixmap import Pixmap
from Components.ScrollLabel import ScrollLabel
from Screens.ChoiceBox import ChoiceBox
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen

from . import engine, features, sources, ui
from .sources import log, u

T = ui.T
S = ui.S
P = ui.P
Col, Table = ui.Col, ui.Table


def make_timer(cb):
    t = eTimer()
    try:
        t.callback.append(cb)
    except AttributeError:
        t.conn = t.timeout.connect(cb)
    return t


class Job(object):
    """fn() in a thread, done(result) back on the main thread"""

    def __init__(self, fn, done):
        self.fn, self.done, self.result, self.finished = fn, done, None, False
        self.timer = make_timer(self._check)
        t = threading.Thread(target=self._work)
        t.daemon = True
        t.start()
        self.timer.start(200, False)

    def _work(self):
        try:
            self.result = self.fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.result = e
        self.finished = True

    def _check(self):
        if self.finished:
            self.timer.stop()
            self.done(self.result)

    def cancel(self):
        self.timer.stop()


class Base(Screen):
    def __init__(self, session, title):
        Screen.__init__(self, session)
        self['title'] = Label(T(title))
        self.setTitle(T(title))

    def set_title(self, t):
        self['title'].setText(T(t))
        self.setTitle(T(t))

    def buttons(self, red=u'', green=u'', yellow=u'', blue=u''):
        for k, v in (('red', red), ('green', green), ('yellow', yellow), ('blue', blue)):
            if 'key_' + k in self:
                self['key_' + k].setText(T(v))
            else:
                self['key_' + k] = Label(T(v))


def hm(t):
    return time.strftime('%H:%M', time.localtime(t))


DAYS = (u'الإثنين', u'الثلاثاء', u'الأربعاء', u'الخميس', u'الجمعة', u'السبت', u'الأحد')


def day_name(t):
    lt = time.localtime(t)
    today = time.localtime()
    if lt.tm_yday == today.tm_yday:
        return u'اليوم'
    if lt.tm_yday == (today.tm_yday % 366) + 1 or (t - time.time() < 2 * 86400 and lt.tm_wday == (today.tm_wday + 1) % 7):
        return u'غدا'
    return DAYS[lt.tm_wday]


def bouquets_ordered(chosen=None):
    bqs = engine.get_bouquets()
    chosen = chosen or []
    return [b for b in bqs if b[0] in chosen] + [b for b in bqs if b[0] not in chosen]


def zap(session, ref):
    try:
        session.nav.playService(eServiceReference(T(ref)))
        return True
    except Exception as e:
        log(u'تشغيل القناة: %s' % e)
        return False


def timer_message(session, res, what):
    txt = {'ok': u'تم: %s' % what, 'exists': u'موجود بالفعل في المؤقتات',
           'conflict': u'تعارض مع مؤقت آخر - لم يُضف'}.get(res, u'تعذر: %s' % res)
    session.open(MessageBox, T(txt), MessageBox.TYPE_INFO, timeout=4)


# --------------------------------------------------------------------- event details
class EventScreen(Base):
    def __init__(self, session, title, when, channel, text):
        # a film with a TMDB key: its poster on the left of the description
        film = bool(features.tmdb_key()) and features.genre(title, u'', text or u'') == 'film'
        tw = 740 if film else 940
        self.skin = ui.frame(1000, 600, (
            ui.box(16, 70, 968, 74, ui.PANEL) +
            ui.label('when', 30, 76, 940, 30, 20, ui.hexc('amber'), ui.PANEL) +
            ui.label('channel', 30, 108, 940, 30, 18, ui.DIM, ui.PANEL) +
            ui.box(16, 152, 968, 434, ui.PANEL) +
            '<widget name="poster" position="%d,%d" size="%d,%d" alphatest="blend" zPosition="2" />' % (
                S(30), S(166), S(185), S(278)) +
            '<widget name="text" position="%d,%d" size="%d,%d" font="Regular;%d" foregroundColor="%s" '
            'backgroundColor="%s" transparent="1" zPosition="1" />' % (
                S(30 + 940 - tw), S(162), S(tw), S(414), S(21), ui.TXT, ui.PANEL)), title)
        Base.__init__(self, session, title)
        self['when'] = Label(T(when))
        self['channel'] = Label(T(channel))
        self['text'] = ScrollLabel(T(text or u'لا يوجد وصف'))
        self['poster'] = Pixmap()
        self['actions'] = ActionMap(['OkCancelActions', 'DirectionActions'], {
            'ok': self.close, 'cancel': self.close, 'up': self['text'].pageUp, 'down': self['text'].pageDown}, -1)
        self.pjob = None
        if film:
            self.onLayoutFinish.append(lambda: self._poster(title))
        self.onClose.append(lambda: self.pjob and self.pjob.cancel())

    def _poster(self, title):
        self['poster'].hide()
        self.pjob = Job(lambda: CinemaScreen.tmdb(title), self.show_poster)

    def show_poster(self, path):
        if isinstance(path, Exception) or not path:
            return
        try:
            from Tools.LoadPixmap import LoadPixmap
            pm = LoadPixmap(path)
            if pm and self['poster'].instance:
                self['poster'].instance.setScale(1)
                self['poster'].instance.setPixmap(pm)
                self['poster'].show()
        except Exception:
            pass


# --------------------------------------------------------------------- the full guide (grid)
GENRE_BG = {'film': 0x3b2d5c, 'sport': 0x1d4a3a, 'news': 0x1e3a5c, 'series': 0x4d3c1c, 'kids': 0x214d57,
            'religion': 0x34481f, None: 0x1b2837}
GENRE_NAME = ((None, u'برامج'), ('film', u'أفلام'), ('series', u'مسلسلات'), ('sport', u'رياضة'), ('news', u'أخبار'),
              ('kids', u'أطفال'))


class GridList(MenuList):
    def __init__(self, row_h):
        MenuList.__init__(self, [], enableWrapAround=True, content=eListboxPythonMultiContent)
        self.row_h = row_h
        self.fonts()

    def fonts(self):
        from enigma import gFont
        self.l.setFont(0, gFont('Regular', S(18)))
        self.l.setFont(1, gFont('Regular', S(16)))
        self.l.setFont(2, gFont(ui.DIGIT, S(15)))
        self.l.setItemHeight(S(self.row_h))

    def applySkin(self, desktop, parent):
        if self.skinAttributes:
            self.skinAttributes = [(k, v) for k, v in self.skinAttributes if k not in ('font', 'itemHeight')]
        res = MenuList.applySkin(self, desktop, parent)
        self.fonts()
        return res


class GridScreen(Base):
    """channels on the right, time flows to the left (Arabic reading order), 2.5 hours on screen."""
    W_MIN = 150
    ROW_H = 46
    ROWS = 10
    GX, GW = 10, 1020          # programme area
    CW = 214                  # channel column

    def __init__(self, session, ref=None, chosen=None):
        sw = self.GX + self.GW + 6 + self.CW + 10
        ruler = ''
        for i in range(self.W_MIN // 30 + 1):
            x = self.xpos(i * 30 * 60, base=True)
            ruler += ui.label('r%d' % i, x - 60, 98, 60, 28, 17, ui.hexc('blue'), ui.BG, 'right', 'center', ui.DIGIT)
            ruler += '<eLabel position="%d,%d" size="%d,%d" backgroundColor="%s" zPosition="0" />' % (
                S(x - 1), S(124), S(2), S(8), ui.hexc('blue'))
        legend = ''
        lx = 20
        for g, name in GENRE_NAME:
            legend += '<eLabel position="%d,%d" size="%d,%d" backgroundColor="#%06x" zPosition="0" />' % (
                S(lx), S(70), S(16), S(16), GENRE_BG[g])
            legend += ui.label('lg_%s' % (g or 'none'), lx + 20, 64, 90, 28, 15, ui.DIM, ui.BG, 'left', 'center')
            lx += 112
        rows_h = self.ROWS * self.ROW_H
        y_info = 136 + rows_h + 6
        self.skin = ui.frame(sw, y_info + 120, (
            legend + ui.label('bouquet', sw - 520, 62, 500, 32, 20, ui.hexc('amber'), ui.BG, 'right', 'center') +
            ruler +
            '<widget name="list" position="%d,%d" size="%d,%d" scrollbarMode="showNever" transparent="0" '
            'backgroundColor="%s" foregroundColor="%s" backgroundColorSelected="%s" foregroundColorSelected="%s" />' % (
                S(self.GX), S(134), S(sw - 2 * self.GX), S(rows_h), ui.BG, ui.TXT, ui.BG, ui.TXT) +
            ui.box(10, y_info, sw - 20, 64, ui.PANEL) +
            ui.label('ev_title', 24, y_info + 4, sw - 48, 30, 21, ui.TXT, ui.PANEL) +
            ui.label('ev_desc', 24, y_info + 34, sw - 48, 28, 17, ui.DIM, ui.PANEL) +
            ui.buttons(y_info + 72, sw)), u'الدليل الكامل')
        Base.__init__(self, session, u'الدليل الكامل')
        self['list'] = GridList(self.ROW_H)
        self['bouquet'] = Label('')
        self['ev_title'] = Label('')
        self['ev_desc'] = Label('')
        for i in range(self.W_MIN // 30 + 1):
            self['r%d' % i] = Label('')
        for g, name in GENRE_NAME:
            self['lg_%s' % (g or 'none')] = Label(T(name))
        self.buttons(u'الآن', u'تذكير', u'تسجيل', u'متابعة المسلسل')
        self['actions'] = ActionMap(['OkCancelActions', 'ColorActions', 'DirectionActions', 'EPGSelectActions',
                                     'ChannelSelectBaseActions'], {
            'ok': self.zap, 'cancel': self.close, 'red': self.to_now, 'green': lambda: self.timer(False),
            'yellow': lambda: self.timer(True), 'blue': self.follow, 'info': self.details,
            'up': self.up, 'down': self.down, 'left': lambda: self.step(1), 'right': lambda: self.step(-1),
            'nextBouquet': lambda: self.bouquet(1), 'prevBouquet': lambda: self.bouquet(-1),
            'nextService': lambda: self.shift(2 * 3600), 'prevService': lambda: self.shift(-2 * 3600),
            'pageUp': lambda: self.page(-1), 'pageDown': lambda: self.page(1)}, -1)
        self.epg = eEPGCache.getInstance()
        self.start_ref = ref
        self.chosen = chosen or []
        self.bqs, self.bq = [], 0
        self.ev = {}
        self.sel_t = time.time()
        self.t0 = self.window_start(time.time())
        self.onLayoutFinish.append(self.load)
        self.clock = make_timer(self.tick)
        self.clock.start(60000, False)
        self.onClose.append(self.clock.stop)

    # ---- geometry: time -> x (later = more to the left)
    def xpos(self, off, base=False):
        right = self.GX + self.GW
        return int(right - float(off) / (self.W_MIN * 60) * self.GW)

    @staticmethod
    def window_start(t):
        return int(t // 1800 * 1800)

    # ---- data
    def load(self):
        try:
            self.bqs = bouquets_ordered(self.chosen)
        except Exception as e:
            log(u'الباقات: %s' % e)
            self.bqs = []
        self.bq = 0
        if self.start_ref:
            for i, (_b, items) in enumerate(self.bqs):
                if self.start_ref in [r for r, _n in items]:
                    self.bq = i
                    break
        self.fill(self.start_ref)

    def services(self):
        return self.bqs[self.bq][1] if self.bqs else engine.get_services()

    def events(self, ref):
        if ref not in self.ev:
            out = []
            try:
                res = self.epg.lookupEvent(['IBDTSE', (engine._enc(ref), 0, self.t0, self.W_MIN + 1)]) or []
                for x in res:
                    if x and x[1] and x[2]:
                        out.append((int(x[1]), int(x[2]), u(x[3] or u''), u(x[4] or u''), u(x[5] or u''),
                                    int(x[0] or 0)))
            except Exception:
                pass
            out.sort()
            self.ev[ref] = out
        return self.ev[ref]

    def fill(self, keep_ref=None):
        self.ev = {}
        items = self.services()
        rows = [self.row(r, n) for r, n in items]
        idx = 0
        if keep_ref:
            for i, (r, _n) in enumerate(items):
                if r == keep_ref:
                    idx = i
                    break
        self['list'].setList(rows)
        if rows:
            self['list'].moveToIndex(idx)
        name = self.bqs[self.bq][0] if self.bqs else u'كل القنوات'
        self['bouquet'].setText(T(u'◀  %s  ▶' % name))
        for i in range(self.W_MIN // 30 + 1):
            t = self.t0 + i * 1800
            self['r%d' % i].setText(T(hm(t) if i < self.W_MIN // 30 else u''))
        self.set_title(u'الدليل الكامل  -  %s %s' % (day_name(self.t0), time.strftime('%d/%m', time.localtime(self.t0))))
        self.show_sel()

    def row(self, ref, name):
        locked = engine.is_locked(ref)
        h = S(self.ROW_H)
        pad = S(2)
        right = S(self.GX + self.GW) - S(self.GX)
        ch_x = right + S(6)
        res = [ref]
        res.append((eListboxPythonMultiContent.TYPE_TEXT, ch_x, pad, S(self.CW), h - 2 * pad, 0, 0, '',
                    None, None, P['panel'], P['sel']))
        if locked:
            res.append((eListboxPythonMultiContent.TYPE_TEXT, ch_x + S(self.CW) - S(4), pad, S(4), h - 2 * pad, 0, 0,
                        '', None, None, P['accent'], P['accent']))
        res.append((eListboxPythonMultiContent.TYPE_TEXT, ch_x + S(8), pad, S(self.CW) - S(18), h - 2 * pad, 0,
                    RT_HALIGN_RIGHT | RT_VALIGN_CENTER, T(name), P['txt'], P['txt']))
        evs = self.events(ref)
        t1 = self.t0 + self.W_MIN * 60
        sel = self.pick(evs)
        if not evs:
            res.append((eListboxPythonMultiContent.TYPE_TEXT, 0, pad, right, h - 2 * pad, 1,
                        RT_HALIGN_CENTER | RT_VALIGN_CENTER, T(u'لا توجد بيانات'), P['dim'], P['dim'],
                        P['cell2'], P['cell2']))
        for e in evs:
            b, d = e[0], e[1]
            a, z = max(b, self.t0), min(b + d, t1)
            if z <= a:
                continue
            xr = S(self.xpos(a - self.t0)) - S(self.GX)
            xl = S(self.xpos(z - self.t0)) - S(self.GX)
            w = xr - xl - S(2)
            if w < S(3):
                continue
            g = features.genre(e[2], e[3])
            bg = GENRE_BG.get(g, GENRE_BG[None])
            is_sel = sel is not None and e[0] == sel[0]
            res.append((eListboxPythonMultiContent.TYPE_TEXT, xl + S(1), pad, w, h - 2 * pad, 0, 0, '',
                        None, None, bg, P['sel'] if is_sel else bg))
            if is_sel:
                res.append((eListboxPythonMultiContent.TYPE_TEXT, xl + S(1), h - pad - S(3), w, S(3), 0, 0, '',
                            None, None, bg, P['frame']))
            if w > S(28):
                al = RT_HALIGN_RIGHT if ui.is_ar(e[2]) else RT_HALIGN_LEFT
                res.append((eListboxPythonMultiContent.TYPE_TEXT, xl + S(6), pad, w - S(10), h - 2 * pad, 0,
                            al | RT_VALIGN_CENTER, T(e[2]), P['txt'], P['txt']))
        now = time.time()
        if self.t0 <= now < t1:
            xn = S(self.xpos(now - self.t0)) - S(self.GX)
            res.append((eListboxPythonMultiContent.TYPE_TEXT, xn - S(1), 0, S(2), h, 0, 0, '', None, None,
                        P['red'], P['red']))
        return res

    def pick(self, evs):
        for e in evs:
            if e[0] <= self.sel_t < e[0] + e[1]:
                return e
        later = [e for e in evs if e[0] >= self.sel_t]
        return later[0] if later else (evs[-1] if evs else None)

    def cur(self):
        c = self['list'].getCurrent()
        if not c:
            return None, None, None
        ref = c[0]
        name = dict(self.services()).get(ref, u'')
        return ref, name, self.pick(self.events(ref))

    def show_sel(self):
        _ref, name, e = self.cur()
        if not e:
            self['ev_title'].setText(T(name or u''))
            self['ev_desc'].setText('')
            return
        self['ev_title'].setText(T(u'%s - %s   %s   (%s)' % (hm(e[0]), hm(e[0] + e[1]), e[2], name)))
        self['ev_desc'].setText(T((e[3] or e[4] or u'').replace(u'\n', u' ')[:220]))

    # ---- keys
    def up(self):
        self['list'].up()
        self.show_sel()

    def down(self):
        self['list'].down()
        self.show_sel()

    def page(self, d):
        if d < 0:
            self['list'].pageUp()
        else:
            self['list'].pageDown()
        self.show_sel()

    def redraw(self):
        idx = self['list'].getSelectedIndex()
        self['list'].setList([self.row(r, n) for r, n in self.services()])
        self['list'].moveToIndex(idx)
        self.show_sel()

    def step(self, d):
        """d=1: next programme (to the left), d=-1: previous"""
        ref, _n, e = self.cur()
        if not ref:
            return
        evs = self.events(ref)
        if e and d > 0:
            nxt = [x for x in evs if x[0] > e[0]]
            if nxt:
                self.sel_t = nxt[0][0] + 1
            else:
                self.sel_t = e[0] + e[1] + 1
                return self.shift(3600)
        elif e and d < 0:
            prv = [x for x in evs if x[0] < e[0]]
            if prv:
                self.sel_t = prv[-1][0] + 1
            else:
                self.sel_t = e[0] - 1
                return self.shift(-3600)
        t1 = self.t0 + self.W_MIN * 60
        if self.sel_t > t1 - 1800:
            return self.shift(3600)
        if self.sel_t < self.t0:
            return self.shift(-3600)
        self.redraw()

    def shift(self, sec):
        self.t0 = max(self.window_start(time.time()) - 6 * 3600, self.t0 + sec)
        if not (self.t0 <= self.sel_t < self.t0 + self.W_MIN * 60):
            self.sel_t = self.t0 + (60 if sec > 0 else self.W_MIN * 60 - 1800)
        ref = self['list'].getCurrent() and self['list'].getCurrent()[0]
        self.fill(ref)

    def to_now(self):
        self.sel_t = time.time()
        self.t0 = self.window_start(time.time())
        ref = self['list'].getCurrent() and self['list'].getCurrent()[0]
        self.fill(ref)

    def bouquet(self, d):
        if self.bqs:
            self.bq = (self.bq + d) % len(self.bqs)
            self.fill()

    def tick(self):
        if self.t0 + self.W_MIN * 60 < time.time():
            return
        self.redraw()

    def zap(self):
        ref, _n, _e = self.cur()
        if ref and zap(self.session, ref):
            self.close()

    def timer(self, record):
        ref, name, e = self.cur()
        if not e:
            return
        res = features.add_timer(self.session, ref, e[0], e[1], e[2], e[3] or e[4], e[5] or None, record)
        timer_message(self.session, res, (u'تسجيل %s' if record else u'تذكير بـ %s') % e[2])

    def follow(self):
        ref, name, e = self.cur()
        if not e:
            return
        title = features.series_title(e[2])

        def done(c):
            if c:
                features.add_series(title, c[1])
                if c[1] != 'list':
                    features.schedule_series(self.session, self.epg, engine.get_services())
                timer_message(self.session, 'ok', u'متابعة "%s" (%s)' % (title, c[0]))
        self.session.openWithCallback(done, ChoiceBox, title=T(u'متابعة "%s" على كل القنوات' % title), list=[
            (T(u'تذكير قبل كل حلقة (أول عرض في اليوم)'), 'remind'),
            (T(u'تسجيل كل حلقة (أول عرض في اليوم)'), 'record'),
            (T(u'عرض المواعيد فقط في "مسلسلاتي"'), 'list')])

    def details(self):
        ref, name, e = self.cur()
        if e:
            self.session.open(EventScreen, e[2], u'%s %s - %s' % (day_name(e[0]), hm(e[0]), hm(e[0] + e[1])), name,
                              u'\n\n'.join(x for x in (e[3], e[4]) if x))


# --------------------------------------------------------------------- sports centre
class MatchesScreen(Base):
    FILTERS = (('today', u'اليوم'), ('tomorrow', u'غدا'), ('all', u'كل المباريات'))

    def __init__(self, session):
        self.table = Table(1216, [Col(u'الوقت', 86, 'center', 1, 'blue'), Col(u'المباراة', 468),
                                  Col(u'البطولة', 250, 'auto', 0, 'dim'), Col(u'القناة عندك', 290),
                                  Col(u'', 92, 'center')], row_h=44, font=19, small=15)
        self.skin = ui.frame(1240, 712, (
            ui.box(10, 64, 1220, 36, ui.HEAD) +
            ui.label('filter', 20, 64, 1200, 36, 20, ui.hexc('amber'), ui.HEAD, 'center', 'center') +
            self.table.header(10, 106) + ui.list_widget('list', 10, 142, 1220, 484) +
            ui.label('info', 16, 630, 1208, 30, 16, ui.DIM, ui.BG, 'right', 'center') +
            ui.buttons(666, 1240)), u'مركز المباريات')
        Base.__init__(self, session, u'مركز المباريات')
        self['list'] = self.table.widget()
        self['filter'] = Label('')
        self['info'] = Label(T(u'جاري التحميل...'))
        self.buttons(u'تسجيل', u'تذكير', u'اليوم / غدا / الكل', u'قنواتي فقط')
        self['actions'] = ActionMap(['OkCancelActions', 'ColorActions', 'DirectionActions'], {
            'ok': self.zap, 'cancel': self.close, 'red': lambda: self.timer(True), 'green': lambda: self.timer(False),
            'yellow': self.next_filter, 'blue': self.toggle_mine,
            'up': lambda: self['list'].up(), 'down': lambda: self['list'].down(),
            'left': lambda: self['list'].pageUp(), 'right': lambda: self['list'].pageDown()}, -1)
        self.flt = 0
        self.mine = True
        self.rows, self.generated = [], None
        self.onLayoutFinish.append(self.load)

    def load(self):
        try:
            services = engine.get_services()
        except Exception:
            services = []
        self.job = Job(lambda: features.matches_for(services), self.loaded)

    def loaded(self, res):
        if isinstance(res, Exception):
            self['info'].setText(T(u'خطأ: %s' % res))
            return
        self.rows, self.generated = res
        self.build()

    def visible(self):
        now = time.time()
        lt = time.localtime(now)
        midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        f = self.FILTERS[self.flt][0]
        out = []
        for m in self.rows:
            if m['stop'] < now - 900:
                continue
            if self.mine and not m['refs']:
                continue
            if f == 'today' and m['start'] >= midnight + 86400 + 4 * 3600:
                continue
            if f == 'tomorrow' and not (midnight + 86400 <= m['start'] < midnight + 2 * 86400):
                continue
            out.append(m)
        return out

    def build(self):
        now = time.time()
        vis = self.visible()
        rows = []
        for m in vis:
            if m['start'] <= now < m['stop']:
                chip = {'t': u'الآن', 'chip': 'red'}
            elif m['start'] - now < 2 * 3600:
                chip = {'t': u'قريبا', 'chip': 'amber'}
            else:
                chip = {'t': day_name(m['start']), 'fg': 'dim'}
            teams = u'%s  \u00d7  %s' % ((m.get('ar_home') or m['home']), (m.get('ar_away') or m['away']))
            comp = m.get('ar_comp') or m.get('comp') or u''
            pct = None
            if m['start'] <= now < m['stop']:
                pct = (now - m['start']) * 100.0 / max(1, m['stop'] - m['start'])
            ch = m['mine'][0] if m['mine'] else (m['channels'][0] if m['channels'] else u'')
            if len(m['mine']) > 1:
                ch += u'  +%d' % (len(m['mine']) - 1)
            rows.append(self.table.row(m, [hm(m['start']), {'t': teams, 'pct': pct, 'bar': 'red'},
                                           comp.split(u' - ')[0][:60],
                                           {'t': ch, 'fg': 'accent' if m['mine'] else 'dim'}, chip], len(rows)))
        self['list'].setList(rows)
        self['filter'].setText(T(u'◀  %s   ·   %s  ▶' % (
            self.FILTERS[self.flt][1], u'على قنواتك فقط' if self.mine else u'كل القنوات العربية')))
        gen = time.strftime('%H:%M', time.localtime(self.generated)) if self.generated else u'-'
        self['info'].setText(T(u'%d مباراة   |   OK: مشاهدة   |   الأخضر: تذكير   الأحمر: تسجيل   |   تحديث السحابة %s' % (
            len(rows), gen) if rows else u'لا توجد مباريات في هذا العرض (السحابة %s) - جرّب "كل المباريات"' % gen))

    def next_filter(self):
        self.flt = (self.flt + 1) % len(self.FILTERS)
        self.build()

    def toggle_mine(self):
        self.mine = not self.mine
        self.build()

    def _choose_ref(self, m, cb):
        refs = m.get('refs') or []
        if not refs:
            self.session.open(MessageBox, T(u'هذه المباراة على قناة ليست في باقاتك: %s' % u'، '.join(m['channels'][:4])),
                              MessageBox.TYPE_INFO, timeout=6)
            return
        if len(refs) == 1:
            return cb(refs[0])
        self.session.openWithCallback(lambda c: c and cb(c[1]), ChoiceBox, title=T(u'اختر القناة'),
                                      list=[(T(n), r) for n, r in zip(m['mine'], refs)])

    def zap(self):
        m = self['list'].payload()
        if m:
            self._choose_ref(m, lambda r: zap(self.session, r) and self.close())

    def timer(self, record):
        m = self['list'].payload()
        if not m:
            return
        title = m.get('ar') or m['title']

        def go(ref):
            res = features.add_timer(self.session, ref, m['start'], m['stop'] - m['start'], title, m.get('comp', u''),
                                     None, record)
            timer_message(self.session, res, (u'تسجيل %s' if record else u'تذكير بـ %s') % title)
        self._choose_ref(m, go)


# --------------------------------------------------------------------- cinema tonight
POSTER_DIR = '/tmp/saadepg_posters'


class CinemaScreen(Base):
    FILTERS = (('tonight', u'الليلة (من 6 مساء)'), ('now', u'يعرض الآن والساعات القادمة'), ('all', u'كل الأفلام'))

    def __init__(self, session):
        self.table = Table(856, [Col(u'الوقت', 80, 'center', 1, 'blue'), Col(u'الفيلم', 430),
                                 Col(u'القناة', 230), Col(u'', 92, 'center')], row_h=44, font=19, small=15)
        self.skin = ui.frame(1240, 712, (
            ui.box(10, 64, 1220, 36, ui.HEAD) +
            ui.label('filter', 20, 64, 1200, 36, 20, ui.hexc('amber'), ui.HEAD, 'center', 'center') +
            '<widget name="list" position="%d,%d" size="%d,%d" scrollbarMode="showOnDemand" transparent="0" '
            'backgroundColor="%s" foregroundColor="%s" backgroundColorSelected="%s" foregroundColorSelected="%s" />' % (
                S(372), S(142), S(858), S(484), ui.BG, ui.TXT, ui.FRAME, ui.TXT) +
            self.table.header(372, 106) +
            ui.box(10, 106, 352, 520, ui.PANEL) +
            ui.box(86, 116, 200, 290, ui.HEAD) +
            ui.label('noposter', 86, 116, 200, 290, 24, ui.DIM, ui.HEAD, 'center', 'center') +
            '<widget name="poster" position="%d,%d" size="%d,%d" alphatest="blend" zPosition="2" />' % (
                S(86), S(116), S(200), S(290)) +
            ui.label('ftitle', 22, 410, 328, 54, 19, ui.TXT, ui.PANEL, 'center', 'center') +
            '<widget name="fdesc" position="%d,%d" size="%d,%d" font="Regular;%d" foregroundColor="%s" '
            'backgroundColor="%s" transparent="1" zPosition="1" halign="right" />' % (
                S(22), S(466), S(328), S(154), S(16), ui.DIM, ui.PANEL) +
            ui.label('info', 16, 630, 1208, 30, 16, ui.DIM, ui.BG, 'right', 'center') +
            ui.buttons(666, 1240)), u'سينما الليلة')
        Base.__init__(self, session, u'سينما الليلة')
        self['list'] = self.table.widget()
        self['filter'] = Label('')
        self['poster'] = Pixmap()
        self['noposter'] = Label(T(u'\u25b6  فيلم'))
        self['ftitle'] = Label('')
        self['fdesc'] = Label('')
        self['info'] = Label(T(u'جاري التحميل...'))
        self.buttons(u'تسجيل', u'تذكير', u'الليلة / الآن / الكل', u'قنواتي للسينما')
        self['actions'] = ActionMap(['OkCancelActions', 'ColorActions', 'DirectionActions'], {
            'ok': self.zap, 'cancel': self.close, 'red': lambda: self.timer(True), 'green': lambda: self.timer(False),
            'yellow': self.next_filter, 'blue': self.channels,
            'up': self.up, 'down': self.down,
            'left': lambda: self.move('pageUp'), 'right': lambda: self.move('pageDown')}, -1)
        self.flt = 0
        self.rows = []
        self.pjob = None
        self.onLayoutFinish.append(self.load)

    def load(self):
        try:
            services = engine.get_services()
        except Exception:
            services = []
        epg = eEPGCache.getInstance()
        self.job = Job(lambda: features.films_for(services, epg), self.loaded)

    def loaded(self, res):
        if isinstance(res, Exception):
            self['info'].setText(T(u'خطأ: %s' % res))
            return
        self.rows = res
        self.build()

    def build(self):
        now = time.time()
        lt = time.localtime(now)
        evening = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 18, 0, 0, 0, 0, -1))
        if lt.tm_hour < 4:
            evening -= 86400
        f = self.FILTERS[self.flt][0]
        rows = []
        for x in self.rows:
            if f == 'tonight' and not (evening - 1800 <= x['start'] < evening + 10 * 3600):
                continue
            if f == 'now' and not (x['start'] < now + 4 * 3600):
                continue
            if x['stop'] <= now:
                continue
            if x['start'] <= now < x['stop']:
                chip = {'t': u'الآن', 'chip': 'red'}
            elif x.get('src') == 'mine':
                chip = {'t': u'قناتك', 'chip': 'violet'}
            else:
                chip = {'t': (x.get('cat') or u'')[:10], 'fg': 'dim'}
            pct = (now - x['start']) * 100.0 / max(1, x['stop'] - x['start']) if x['start'] <= now else None
            rows.append(self.table.row(x, [hm(x['start']), {'t': x['title'], 'pct': pct, 'bar': 'violet'},
                                           x['mine'][0] if x['mine'] else u'', chip], len(rows)))
        self['list'].setList(rows)
        self['filter'].setText(T(u'◀  %s  ▶' % self.FILTERS[self.flt][1]))
        extra = len(features.cinema_channels())
        self['info'].setText(T(u'%d فيلم على قنواتك%s   |   OK: مشاهدة   الأخضر: تذكير   الأحمر: تسجيل' % (
            len(rows), (u'   |   قنوات أضفتها: %d' % extra) if extra else u'   |   الأزرق: أضف قنوات أفلام من باقاتك')))
        self.selected()

    def up(self):
        self['list'].up()
        self.selected()

    def down(self):
        self['list'].down()
        self.selected()

    def move(self, how):
        getattr(self['list'], how)()
        self.selected()

    def selected(self):
        x = self['list'].payload()
        if not x:
            self['ftitle'].setText('')
            self['fdesc'].setText('')
            self['poster'].hide()
            return
        self['ftitle'].setText(T(x['title'] + ((u'\n' + x['en']) if x.get('en') else u'')))
        self['fdesc'].setText(T(u'%s - %s  %s\n%s' % (hm(x['start']), hm(x['stop']), u'، '.join(x['mine'][:2]),
                                                      (x.get('desc') or u'')[:300])))
        self['poster'].hide()
        url = x.get('icon')
        if url:
            path = os.path.join(POSTER_DIR, hashlib.md5(url.encode('utf-8')).hexdigest() + '.jpg')
            if os.path.exists(path):
                self.show_poster(path)
            else:
                self.pjob = Job(lambda: self.download(url, path), lambda p: self.show_poster(p, x))
        elif features.tmdb_key():       # no picture from the guide: TMDB (your own key)
            self.pjob = Job(lambda: self.tmdb(x['title'], x.get('en')), lambda p: self.show_poster(p, x))

    @classmethod
    def tmdb(cls, title, en=None):
        url = features.tmdb_poster(title, en)
        if not url:
            raise IOError('no poster')
        path = os.path.join(POSTER_DIR, hashlib.md5(url.encode('utf-8')).hexdigest() + '.jpg')
        return path if os.path.exists(path) else cls.download(url, path)

    @staticmethod
    def download(url, path):
        if not os.path.isdir(POSTER_DIR):
            os.makedirs(POSTER_DIR)
        data = sources.http_get(url, timeout=15)
        with open(path, 'wb') as f:
            f.write(data)
        return path

    def show_poster(self, path, item=None):
        if isinstance(path, Exception) or (item is not None and self['list'].payload() is not item):
            return
        try:
            from Tools.LoadPixmap import LoadPixmap
            pm = LoadPixmap(path)
            if pm and self['poster'].instance:
                self['poster'].instance.setScale(1)
                self['poster'].instance.setPixmap(pm)
                self['poster'].show()
        except Exception:
            pass

    def next_filter(self):
        self.flt = (self.flt + 1) % len(self.FILTERS)
        self.build()

    def channels(self):
        self.session.openWithCallback(lambda changed: changed and self.load(), ChannelPickScreen)

    def zap(self):
        x = self['list'].payload()
        if x and x['refs'] and zap(self.session, x['refs'][0]):
            self.close()

    def timer(self, record):
        x = self['list'].payload()
        if not x or not x['refs']:
            return
        res = features.add_timer(self.session, x['refs'][0], x['start'], x['stop'] - x['start'], x['title'],
                                 x.get('desc', u''), None, record)
        timer_message(self.session, res, (u'تسجيل %s' if record else u'تذكير بـ %s') % x['title'])


class ChannelPickScreen(Base):
    """your own movie channels for "cinema tonight": any channel of your bouquets."""

    def __init__(self, session):
        self.table = Table(1096, [Col(u'مختارة', 110, 'center'), Col(u'القناة', 560), Col(u'الباقة', 410, 'auto', 0, 'dim')],
                           row_h=42, font=19)
        self.skin = ui.frame(1120, 712, (
            ui.box(10, 64, 1100, 36, ui.HEAD) +
            ui.label('filter', 20, 64, 1080, 36, 20, ui.hexc('amber'), ui.HEAD, 'center', 'center') +
            self.table.header(10, 106) + ui.list_widget('list', 10, 142, 1100, 462) +
            ui.label('info', 16, 610, 1088, 50, 16, ui.DIM, ui.BG, 'right', 'center') +
            ui.buttons(666, 1120)), u'قنواتي للسينما')
        Base.__init__(self, session, u'قنواتي للسينما')
        self['list'] = self.table.widget()
        self['filter'] = Label('')
        self['info'] = Label(T(u'OK: اختيار/إلغاء القناة.  كل برنامج طويل (أكثر من 70 دقيقة) على القنوات المختارة يظهر في '
                               u'"سينما الليلة"، حتى لو لم تكن من قنوات البلوجين.  يمين/يسار: الباقة'))
        self.buttons(u'خروج', u'حفظ', u'بحث بالاسم', u'المختارة فقط')
        self['actions'] = ActionMap(['OkCancelActions', 'ColorActions', 'DirectionActions'], {
            'ok': self.toggle, 'cancel': lambda: self.close(False), 'red': lambda: self.close(False),
            'green': self.save, 'yellow': self.search, 'blue': self.only_chosen,
            'up': lambda: self['list'].up(), 'down': lambda: self['list'].down(),
            'left': lambda: self.step(-1), 'right': lambda: self.step(1)}, -1)
        self.chosen = dict(features.cinema_channels())
        self.bqs, self.bq, self.query, self.only = [], -1, u'', False
        self.onLayoutFinish.append(self.load)

    def load(self):
        try:
            self.bqs = bouquets_ordered()
        except Exception:
            self.bqs = []
        self.build()

    def build(self, keep=0):
        seen, rows = set(), []
        q = self.query.lower()
        groups = self.bqs if self.bq < 0 else [self.bqs[self.bq]]
        for bname, items in groups:
            for r, n in items:
                if r in seen:
                    continue
                seen.add(r)
                if q and q not in n.lower():
                    continue
                if self.only and r not in self.chosen:
                    continue
                on = r in self.chosen
                rows.append(self.table.row((r, n), [{'t': u'✔', 'chip': 'accent'} if on else
                                                    {'t': u'—', 'fg': 'dim'}, n, bname], len(rows)))
        self['list'].setList(rows)
        if rows:
            self['list'].moveToIndex(min(keep, len(rows) - 1))
        where = u'كل الباقات' if self.bq < 0 else self.bqs[self.bq][0]
        self['filter'].setText(T(u'◀  %s%s   ·   مختارة: %d  ▶' % (
            where, (u'   ·   بحث: ' + self.query) if self.query else u'', len(self.chosen))))

    def step(self, d):
        if self.bqs:
            self.bq = (self.bq + 1 + d) % (len(self.bqs) + 1) - 1
            self.build()

    def toggle(self):
        x = self['list'].payload()
        if not x:
            return
        r, n = x
        if r in self.chosen:
            del self.chosen[r]
        else:
            self.chosen[r] = n
        self.build(self['list'].getSelectedIndex())

    def search(self):
        from Screens.VirtualKeyBoard import VirtualKeyBoard
        self.session.openWithCallback(self._searched, VirtualKeyBoard, title=T(u'جزء من اسم القناة'), text=T(self.query))

    def _searched(self, txt):
        if txt is not None:
            self.query = u(txt).strip()
            self.build()

    def only_chosen(self):
        self.only = not self.only
        self.build()

    def save(self):
        features.save_cinema_channels(sorted(self.chosen.items(), key=lambda kv: kv[1].lower()))
        self.close(True)


# --------------------------------------------------------------------- my series
MODE = {'remind': (u'تذكير', 'accent'), 'record': (u'تسجيل', 'red'), 'list': (u'متابعة', 'blue')}


class SeriesScreen(Base):
    def __init__(self, session):
        self.table = Table(1216, [Col(u'المسلسل / البرنامج', 470), Col(u'الطريقة', 120, 'center'),
                                  Col(u'الحلقة القادمة', 230, 'center', 1, 'blue'), Col(u'القناة', 380)],
                           row_h=46, font=19)
        self.skin = ui.frame(1240, 712, (
            self.table.header(10, 66) + ui.list_widget('list', 10, 102, 1220, 460) +
            ui.box(10, 568, 1220, 90, ui.PANEL) +
            ui.label('info', 24, 574, 1192, 80, 17, ui.DIM, ui.PANEL, 'right', 'top') +
            ui.buttons(666, 1240)), u'مسلسلاتي')
        Base.__init__(self, session, u'مسلسلاتي')
        self['list'] = self.table.widget()
        self['info'] = Label(T(u'جاري البحث في الدليل...'))
        self.buttons(u'حذف', u'إضافة بالاسم', u'تذكير / تسجيل / متابعة', u'خريطة المسلسلات')
        self['actions'] = ActionMap(['OkCancelActions', 'ColorActions', 'DirectionActions'], {
            'ok': self.airings, 'cancel': self.close, 'red': self.remove, 'green': self.add,
            'yellow': self.cycle, 'blue': self.open_map,
            'up': lambda: self['list'].up(), 'down': lambda: self['list'].down()}, -1)
        self.epg = eEPGCache.getInstance()
        self.onLayoutFinish.append(self.build)

    def services(self):
        try:
            return engine.get_services()
        except Exception:
            return []

    def build(self, keep=0):
        d = features.load_series()
        services = self.services()
        rows = []
        for it in d['items']:
            al = features.airings(self.epg, it, services, 72)
            nxt = al[0] if al else None
            m = MODE.get(it.get('mode'), MODE['list'])
            rows.append(self.table.row(it, [it['q'], {'t': m[0], 'chip': m[1]},
                                            (u'%s %s' % (day_name(nxt[0]), hm(nxt[0]))) if nxt else
                                            {'t': u'لا توجد مواعيد', 'fg': 'dim'},
                                            nxt[4] if nxt else (it.get('name') or u'')], len(rows)))
        self['list'].setList(rows)
        if rows:
            self['list'].moveToIndex(min(keep, len(rows) - 1))
        n_t = 0
        try:
            n_t = len(features.plugin_timers(self.session))
        except Exception:
            pass
        self['info'].setText(T(
            (u'%d مسلسل/برنامج تتابعه.  البلوجين يضيف تذكيرا أو تسجيلا لأول عرض في كل يوم (الإعادات لا)، '
             u'ويبحث بالاسم على كل قنوات باقاتك.  مؤقتات البلوجين الآن: %d.\n'
             u'OK: كل المواعيد القادمة.  للإضافة السريعة: من "الدليل الكامل" اختر الحلقة واضغط الأزرق.') % (
                len(rows), n_t) if rows else
            u'أضف اسم مسلسل أو برنامج (الأخضر)، أو من "الدليل الكامل" اختر الحلقة واضغط الأزرق.\n'
            u'البلوجين يجده على كل قنوات باقاتك، ويذكّرك أو يسجّل أول عرض في كل يوم تلقائيا.'))

    def add(self):
        from Screens.VirtualKeyBoard import VirtualKeyBoard
        self.session.openWithCallback(self._added, VirtualKeyBoard, title=T(u'اسم المسلسل أو البرنامج'), text='')

    def _added(self, txt):
        if not txt:
            return
        q = u(txt).strip()

        def done(c):
            if c:
                features.add_series(q, c[1])
                features.schedule_series(self.session, self.epg, self.services())
                self.build(len(features.load_series()['items']) - 1)
        self.session.openWithCallback(done, ChoiceBox, title=T(q), list=[
            (T(u'تذكير قبل كل حلقة'), 'remind'), (T(u'تسجيل كل حلقة'), 'record'), (T(u'عرض المواعيد فقط'), 'list')])

    def remove(self):
        it = self['list'].payload()
        if it:
            features.remove_series(it['q'], it.get('ref'))
            self.build(self['list'].getSelectedIndex())

    def cycle(self):
        it = self['list'].payload()
        if not it:
            return
        order = ['remind', 'record', 'list']
        new = order[(order.index(it.get('mode', 'list')) + 1) % 3] if it.get('mode') in order else 'remind'
        features.add_series(it['q'], new, it.get('ref'), it.get('name'))
        if new != 'list':
            features.schedule_series(self.session, self.epg, self.services())
        self.build(self['list'].getSelectedIndex())

    def airings(self):
        it = self['list'].payload()
        if not it:
            return
        al = features.airings(self.epg, it, self.services(), 96)
        if not al:
            self.session.open(MessageBox, T(u'لا توجد مواعيد لـ "%s" في الدليل الآن' % it['q']), MessageBox.TYPE_INFO,
                              timeout=5)
            return
        first = set(a[0] for a in features.first_per_day(al))
        lst = [(T(u'%s %s   %s   %s%s' % (day_name(a[0]), hm(a[0]), a[2], a[4], u'' if a[0] in first else u'  (إعادة)')),
                a) for a in al[:60]]

        def chosen(c):
            if not c:
                return
            a = c[1]
            self.session.openWithCallback(lambda d: d and self._act(a, d[1]), ChoiceBox, title=T(a[2]), list=[
                (T(u'مشاهدة الآن'), 'zap'), (T(u'تذكير'), 'remind'), (T(u'تسجيل'), 'record')])
        self.session.openWithCallback(chosen, ChoiceBox, title=T(u'مواعيد "%s"' % it['q']), list=lst)

    def _act(self, a, what):
        b, dur, title, ref, name, eid = a
        if what == 'zap':
            if zap(self.session, ref):
                self.close()
            return
        res = features.add_timer(self.session, ref, b, dur, title, u'', eid, what == 'record')
        timer_message(self.session, res, (u'تسجيل %s' if what == 'record' else u'تذكير بـ %s') % title)

    def open_map(self):
        self.session.open(SeriesMapScreen)


class SeriesMapScreen(Base):
    def __init__(self, session):
        ram = features.ramadan_now()
        title = u'خريطة مسلسلات رمضان' if ram else u'خريطة المسلسلات اليومية'
        self.table = Table(1216, [Col(u'الموعد', 92, 'center', 1, 'amber'), Col(u'المسلسل', 470),
                                  Col(u'القناة', 320), Col(u'الإعادة', 314, 'center', 2, 'dim')], row_h=44, font=19,
                           small=16)
        self.skin = ui.frame(1240, 712, (
            ui.box(10, 64, 1220, 36, ui.HEAD) +
            ui.label('filter', 20, 64, 1200, 36, 20, ui.hexc('amber'), ui.HEAD, 'center', 'center') +
            self.table.header(10, 106) + ui.list_widget('list', 10, 142, 1220, 484) +
            ui.label('info', 16, 630, 1208, 30, 16, ui.DIM, ui.BG, 'right', 'center') +
            ui.buttons(666, 1240)), title)
        Base.__init__(self, session, title)
        self['list'] = self.table.widget()
        self['filter'] = Label('')
        self['info'] = Label(T(u'جاري قراءة الدليل وتحديد المسلسلات اليومية...'))
        self.buttons(u'', u'متابعة (تذكير)', u'قنوات البلوجين / كل القنوات', u'مشاهدة')
        self['actions'] = ActionMap(['OkCancelActions', 'ColorActions', 'DirectionActions'], {
            'ok': self.details, 'cancel': self.close, 'green': self.follow, 'yellow': self.toggle, 'blue': self.zap,
            'up': lambda: self['list'].up(), 'down': lambda: self['list'].down(),
            'left': lambda: self['list'].pageUp(), 'right': lambda: self['list'].pageDown()}, -1)
        self.all_channels = False
        self.onLayoutFinish.append(self.load)

    def load(self):
        try:
            services = engine.get_services()
        except Exception:
            services = []
        if not self.all_channels:
            filled = engine.load_state().get('filled') or {}
            services = [s for s in services if s[0] in filled]
        epg = eEPGCache.getInstance()
        self['filter'].setText(T(u'◀  %s (%d قناة)  ▶' % (
            u'كل القنوات' if self.all_channels else u'قنوات البلوجين (العربية)', len(services))))
        self.job = Job(lambda: features.series_map(epg, services), self.loaded)

    def loaded(self, res):
        if isinstance(res, Exception):
            self['info'].setText(T(u'خطأ: %s' % res))
            return
        rows = []
        for r in res:
            rows.append(self.table.row(r, [r['time'], r['title'], r['name'], u'  '.join(r['reruns'])], len(rows)))
        self['list'].setList(rows)
        self['info'].setText(T(u'%d مسلسل/برنامج يومي   |   OK: التفاصيل   الأخضر: متابعة وتذكير بكل حلقة   الأزرق: مشاهدة' %
                               len(rows)))

    def toggle(self):
        self.all_channels = not self.all_channels
        self['list'].setList([])
        self['info'].setText(T(u'جاري القراءة...'))
        self.load()

    def follow(self):
        r = self['list'].payload()
        if r:
            features.add_series(r['title'], 'remind', r['ref'], r['name'])
            n = features.schedule_series(self.session, eEPGCache.getInstance(), engine.get_services())
            timer_message(self.session, 'ok', u'متابعة "%s" على %s (%d تذكير)' % (r['title'], r['name'], n))

    def zap(self):
        r = self['list'].payload()
        if r and zap(self.session, r['ref']):
            self.close()

    def details(self):
        r = self['list'].payload()
        if r:
            self.session.open(EventScreen, r['title'], u'يوميا %s   -   الإعادة: %s' % (r['time'], u'، '.join(r['reruns']) or u'-'),
                              r['name'], r.get('desc') or u'')
