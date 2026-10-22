# -*- coding: utf-8 -*-
# Saad EPG on your phone: a small web page served by the receiver itself (http://<receiver ip>:8899).
# Now / next per bouquet, the sports centre, cinema tonight and your series, with "watch" and "remind me".
from __future__ import absolute_import

import json
import time

from . import engine, features
from .sources import log, u

STATE = {'port': None, 'session': None}


def _services():
    try:
        return engine.get_services()
    except Exception:
        return []


def _json(request, obj):
    request.setHeader(b'Content-Type', b'application/json; charset=utf-8')
    request.setHeader(b'Cache-Control', b'no-store')
    return json.dumps(obj, ensure_ascii=False).encode('utf-8')


def _arg(request, name, default=u''):
    v = request.args.get(name.encode('utf-8')) or request.args.get(name)
    if not v:
        return default
    v = v[0]
    return v.decode('utf-8') if isinstance(v, bytes) else u(v)


def api(request, path):
    from enigma import eEPGCache
    session = STATE['session']
    epg = eEPGCache.getInstance()
    if path == 'bouquets':
        return [{'i': i, 'name': b, 'n': len(items)} for i, (b, items) in enumerate(engine.get_bouquets())]
    if path == 'nownext':
        bqs = engine.get_bouquets()
        i = int(_arg(request, 'b', u'0') or 0)
        items = bqs[i][1] if 0 <= i < len(bqs) else []
        out = []
        for ref, name in items[:400]:
            nw, nx = engine.now_next(epg, ref)
            if not nw:
                continue
            out.append({'ref': ref, 'name': name, 'ours': engine.is_locked(ref),
                        'now': {'t': nw[0], 'b': nw[1], 'd': nw[2]},
                        'next': {'t': nx[0], 'b': nx[1], 'd': nx[2]} if nx else None})
        return out
    if path == 'series':
        d = features.load_series()
        services = _services()
        out = []
        for it in d['items']:
            al = features.airings(epg, it, services, 72)
            out.append({'q': it['q'], 'mode': it.get('mode'),
                        'next': [{'b': a[0], 'd': a[1], 't': a[2], 'ref': a[3], 'ch': a[4]} for a in al[:6]]})
        return out
    if path == 'series_add':
        ok = features.add_series(_arg(request, 'q'), _arg(request, 'mode', u'remind') or u'remind')
        if ok and session:
            features.schedule_series(session, epg, _services())
        return {'ok': ok}
    if path == 'series_del':
        features.remove_series(_arg(request, 'q'))
        return {'ok': True}
    if path == 'zap':
        from enigma import eServiceReference
        ref = _arg(request, 'ref')
        if session and ref:
            session.nav.playService(eServiceReference(engine._enc(ref)))
            return {'ok': True}
        return {'ok': False}
    if path == 'timer':
        if not session:
            return {'res': 'no session'}
        res = features.add_timer(session, _arg(request, 'ref'), int(_arg(request, 'b', u'0')),
                                 int(_arg(request, 'd', u'0')), _arg(request, 't'), u'', None,
                                 _arg(request, 'rec') == u'1')
        return {'res': res}
    if path == 'timers':
        out = []
        for t in features.plugin_timers(session) if session else []:
            try:
                out.append({'name': u(t.name), 'b': t.begin, 'zap': bool(t.justplay),
                            'ch': u(t.service_ref.getServiceName())})
            except Exception:
                pass
        return out
    return None


def threaded(path):
    """the parts that read the cloud files: in a thread"""
    services = _services()
    if path == 'matches':
        rows, gen = features.matches_for(services)
        now = time.time()
        return {'generated': gen, 'matches': [dict((k, m.get(k)) for k in (
            'start', 'stop', 'title', 'ar', 'home', 'away', 'ar_home', 'ar_away', 'comp', 'ar_comp', 'refs', 'mine', 'channels'))
            for m in rows if m['stop'] > now - 900][:300]}
    if path == 'films':
        from enigma import eEPGCache
        rows = features.films_for(services, eEPGCache.getInstance())
        return [dict((k, f.get(k)) for k in ('start', 'stop', 'title', 'en', 'desc', 'icon', 'refs', 'mine', 'src'))
                for f in rows[:400]]
    return None


PAGE = u'''<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Saad EPG</title>
<style>
:root{--bg:#0d1520;--panel:#15202d;--cell:#1b2837;--head:#233a52;--txt:#eef3f8;--dim:#9fb1c4;--acc:#46c08f;
--blue:#5aaee6;--amber:#e6b85c;--red:#e27d6e;--violet:#a58be0}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:16px/1.45 system-ui,Tahoma,sans-serif}
header{position:sticky;top:0;background:var(--panel);border-bottom:2px solid var(--acc);padding:10px 14px;z-index:2}
h1{margin:0 0 8px;font-size:19px}h1 span{color:var(--blue);font-family:monospace;float:left}
nav{display:flex;gap:6px;overflow-x:auto}nav button{flex:0 0 auto;background:var(--cell);color:var(--txt);border:0;
border-radius:8px;padding:8px 12px;font-size:15px}nav button.on{background:var(--acc);color:#0d1520;font-weight:700}
main{padding:10px 12px 40px}select,input{width:100%;padding:9px;border-radius:8px;border:0;background:var(--cell);
color:var(--txt);font-size:15px;margin-bottom:10px}
.card{background:var(--cell);border-radius:10px;padding:10px 12px;margin:8px 0}
.row{display:flex;justify-content:space-between;gap:8px;align-items:center}
.t{font-weight:700}.d{color:var(--dim);font-size:14px}.time{font-family:monospace;color:var(--blue)}
.bar{height:4px;background:#2a3a4d;border-radius:2px;margin-top:6px}.bar i{display:block;height:4px;border-radius:2px;background:var(--acc)}
.chip{font-size:12px;padding:2px 8px;border-radius:10px;background:var(--head);color:var(--txt);white-space:nowrap}
.now{background:var(--red);color:#0d1520}.ours{border-right:3px solid var(--acc)}
.btns{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}.btns button{background:var(--head);color:var(--txt);border:0;
border-radius:7px;padding:6px 10px;font-size:14px}.btns button.g{background:var(--acc);color:#0d1520}
img.p{width:70px;border-radius:6px;float:left;margin-right:10px}.empty{color:var(--dim);text-align:center;padding:30px}
#toast{position:fixed;bottom:14px;left:50%;transform:translateX(-50%);background:var(--acc);color:#0d1520;padding:8px 16px;
border-radius:20px;display:none}
</style></head><body>
<header><h1>Saad EPG <span id="clock"></span></h1><nav id="tabs">
<button data-t="now" class="on">الآن وبعده</button><button data-t="matches">المباريات</button>
<button data-t="films">سينما الليلة</button><button data-t="series">مسلسلاتي</button><button data-t="timers">التذكيرات</button>
</nav></header><main id="m"><div class="empty">...</div></main><div id="toast"></div>
<script>
const $=s=>document.querySelector(s),m=$('#m');let tab='now',bq=+(localStorage.getItem('bq')||0);
const hm=t=>{const d=new Date(t*1000);return d.toTimeString().slice(0,5)};
const day=t=>{const d=new Date(t*1000),n=new Date();if(d.toDateString()==n.toDateString())return'اليوم';
n.setDate(n.getDate()+1);return d.toDateString()==n.toDateString()?'غدا':d.toLocaleDateString('ar-EG',{weekday:'long'})};
const esc=s=>(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const get=p=>fetch('api/'+p).then(r=>r.json());
function toast(t){const e=$('#toast');e.textContent=t;e.style.display='block';setTimeout(()=>e.style.display='none',2500)}
function zap(ref){get('zap?ref='+encodeURIComponent(ref)).then(()=>toast('تم تشغيل القناة'))}
function timer(ref,b,d,t,rec){get('timer?ref='+encodeURIComponent(ref)+'&b='+b+'&d='+d+'&t='+encodeURIComponent(t)+'&rec='+(rec?1:0))
.then(r=>toast({ok:rec?'تم إضافة التسجيل':'تم إضافة التذكير',exists:'موجود بالفعل',conflict:'تعارض مع مؤقت آخر'}[r.res]||r.res))}
function pct(b,d){const n=Date.now()/1000;return Math.max(0,Math.min(100,(n-b)*100/d))}
async function showNow(){const bs=await get('bouquets');let h='<select id="bq">'+bs.map(b=>`<option value="${b.i}" ${b.i==bq?'selected':''}>${esc(b.name)} (${b.n})</option>`).join('')+'</select>';
const l=await get('nownext?b='+bq);h+=l.map(c=>`<div class="card ${c.ours?'ours':''}"><div class="row"><span class="t">${esc(c.name)}</span>
<span class="time">${hm(c.now.b)}</span></div><div>${esc(c.now.t)}</div><div class="bar"><i style="width:${pct(c.now.b,c.now.d)}%"></i></div>
${c.next?`<div class="d">بعده ${hm(c.next.b)}: ${esc(c.next.t)}</div>`:''}<div class="btns"><button class="g" onclick="zap('${c.ref}')">مشاهدة</button>
${c.next?`<button onclick="timer('${c.ref}',${c.next.b},${c.next.d},'${esc(c.next.t).replace(/'/g,'')}',0)">ذكّرني بالتالي</button>`:''}</div></div>`).join('');
m.innerHTML=h||'<div class="empty">لا توجد بيانات</div>';$('#bq').onchange=e=>{bq=+e.target.value;localStorage.setItem('bq',bq);showNow()}}
async function showMatches(){const r=await get('matches'),n=Date.now()/1000;m.innerHTML=r.matches.filter(x=>x.refs.length).map(x=>{const live=x.start<=n&&n<x.stop,
t=(x.ar_home||x.home)+' × '+(x.ar_away||x.away),ref=x.refs[0];return`<div class="card"><div class="row"><span class="t">${esc(t)}</span>
<span class="chip ${live?'now':''}">${live?'الآن':day(x.start)+' '+hm(x.start)}</span></div><div class="d">${esc((x.ar_comp||x.comp||'').split(' - ')[0])}</div>
<div class="d">على: ${esc(x.mine.join('، '))}</div><div class="btns"><button class="g" onclick="zap('${ref}')">مشاهدة</button>
<button onclick="timer('${ref}',${x.start},${x.stop-x.start},'${esc(t).replace(/'/g,'')}',0)">تذكير</button>
<button onclick="timer('${ref}',${x.start},${x.stop-x.start},'${esc(t).replace(/'/g,'')}',1)">تسجيل</button></div></div>`}).join('')||'<div class="empty">لا توجد مباريات على قنواتك الآن</div>'}
async function showFilms(){const r=await get('films'),n=Date.now()/1000;m.innerHTML=r.map(x=>{const live=x.start<=n,ref=x.refs[0];
return`<div class="card">${x.icon?`<img class="p" loading="lazy" src="${esc(x.icon)}">`:''}<div class="row"><span class="t">${esc(x.title)}</span>
<span class="chip ${live?'now':''}">${live?'الآن':day(x.start)+' '+hm(x.start)}</span></div>${x.en?`<div class="d">${esc(x.en)}</div>`:''}
<div class="d">${esc(x.mine.join('، '))}</div><div class="d">${esc((x.desc||'').slice(0,160))}</div><div class="btns">
<button class="g" onclick="zap('${ref}')">مشاهدة</button><button onclick="timer('${ref}',${x.start},${x.stop-x.start},'${esc(x.title).replace(/'/g,'')}',0)">تذكير</button>
<button onclick="timer('${ref}',${x.start},${x.stop-x.start},'${esc(x.title).replace(/'/g,'')}',1)">تسجيل</button></div><div style="clear:both"></div></div>`}).join('')||'<div class="empty">لا توجد أفلام</div>'}
async function showSeries(){const r=await get('series');let h=`<input id="q" placeholder="اسم مسلسل أو برنامج"><div class="btns" style="margin-top:0">
<button class="g" onclick="addS('remind')">متابعة مع تذكير</button><button onclick="addS('record')">متابعة مع تسجيل</button></div>`;
h+=r.map(s=>`<div class="card"><div class="row"><span class="t">${esc(s.q)}</span><span class="chip">${{remind:'تذكير',record:'تسجيل',list:'متابعة'}[s.mode]||''}</span></div>
${s.next.map(a=>`<div class="d"><span class="time">${day(a.b)} ${hm(a.b)}</span> ${esc(a.ch)}</div>`).join('')||'<div class="d">لا توجد مواعيد في الدليل الآن</div>'}
<div class="btns"><button onclick="delS('${esc(s.q).replace(/'/g,'')}')">حذف</button></div></div>`).join('');m.innerHTML=h}
function addS(mode){const q=$('#q').value.trim();if(q)get('series_add?q='+encodeURIComponent(q)+'&mode='+mode).then(()=>{toast('تمت الإضافة');showSeries()})}
function delS(q){get('series_del?q='+encodeURIComponent(q)).then(showSeries)}
async function showTimers(){const r=await get('timers');m.innerHTML=r.map(t=>`<div class="card"><div class="row"><span class="t">${esc(t.name)}</span>
<span class="chip">${t.zap?'تذكير':'تسجيل'}</span></div><div class="d"><span class="time">${day(t.b)} ${hm(t.b)}</span> ${esc(t.ch)}</div></div>`).join('')||'<div class="empty">لا توجد تذكيرات من Saad EPG</div>'}
const show={now:showNow,matches:showMatches,films:showFilms,series:showSeries,timers:showTimers};
document.querySelectorAll('#tabs button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#tabs button').forEach(x=>x.classList.remove('on'));
b.classList.add('on');tab=b.dataset.t;m.innerHTML='<div class="empty">...</div>';show[tab]().catch(e=>m.innerHTML='<div class="empty">خطأ: '+e+'</div>')});
setInterval(()=>$('#clock').textContent=new Date().toTimeString().slice(0,5),1000);showNow();
</script></body></html>'''


def start(session, port=8899):
    """start the page once (twisted runs inside enigma2's main loop)"""
    STATE['session'] = session
    if STATE['port']:
        return True
    try:
        from twisted.internet import reactor, threads
        from twisted.web import resource, server

        class Root(resource.Resource):
            isLeaf = True

            def render_GET(self, request):
                path = u(request.path.decode('utf-8') if isinstance(request.path, bytes) else request.path)
                if not path.startswith(u'/api/'):
                    request.setHeader(b'Content-Type', b'text/html; charset=utf-8')
                    return PAGE.encode('utf-8')
                name = path[5:]
                if name in ('matches', 'films'):
                    d = threads.deferToThread(threaded, name)

                    def ok(res):
                        request.write(_json(request, res))
                        request.finish()

                    def bad(f):
                        request.setResponseCode(500)
                        request.write(_json(request, {'error': str(f.value)}))
                        request.finish()
                    d.addCallbacks(ok, bad)
                    return server.NOT_DONE_YET
                try:
                    res = api(request, name)
                except Exception as e:
                    request.setResponseCode(500)
                    return _json(request, {'error': str(e)})
                if res is None:
                    request.setResponseCode(404)
                    return _json(request, {'error': 'not found'})
                return _json(request, res)

        STATE['port'] = reactor.listenTCP(port, server.Site(Root()))
        log(u'صفحة الموبايل: http://<IP الريسيفر>:%d' % port)
        return True
    except Exception as e:
        log(u'صفحة الموبايل: تعذر التشغيل على المنفذ %d (%s)' % (port, e))
        return False


def stop():
    p = STATE.get('port')
    STATE['port'] = None
    if p:
        try:
            p.stopListening()
        except Exception:
            pass


def address(port):
    """the receiver's own address on the home network"""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = u'IP-الريسيفر'
    return u'http://%s:%d' % (ip, port)
