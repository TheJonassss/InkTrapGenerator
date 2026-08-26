# encoding: utf-8
# ─────────────────────────────────────────────────────────────────────────────
# Ink Trap Generator — Glyphs 3 Filter Plugin
# Inserts ink traps into the concave joints of any typeface
#
# Author:       Jona Saucedo
# Organization: Non Foundry
# Website:      nonfoundry.com
# Version:      1.0.0
# Copyright:    © 2026 Jona Saucedo / Non Foundry. All rights reserved.
# License:      MIT License
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import division, print_function, unicode_literals
import objc
import math
from GlyphsApp import Glyphs, GSNode, LINE, CURVE, OFFCURVE
from GlyphsApp.plugins import FilterWithDialog
from Foundation import NSPoint, NSLog
from vanilla import Window, Group, TextBox, Slider, RadioGroup, Button

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────
DOMAIN = "com.jonasaucedo.InkTrapGenerator"


def dkey(name):
    return "%s.%s" % (DOMAIN, name)


SLIDER_SPECS = [
    ("minAngle",   {"en": "Min angle (°)", "es": "Ángulo mín (°)"}, 0.0, 180.0, 0.0),
    ("maxAngle",   {"en": "Max angle (°)", "es": "Ángulo máx (°)"}, 0.0, 180.0, 100.0),
    ("length",     {"en": "Trap depth",    "es": "Profundidad"},    0.0, 200.0, 45.0),
    ("width",      {"en": "Trap width",    "es": "Ancho (rel.)"},   0.0, 0.45,  0.15),
    ("minSegment", {"en": "Min segment",   "es": "Segmento mín"},   0.0, 200.0, 0.0),
    ("smoothing",  {"en": "Smoothing",     "es": "Suavizado"},      0.0, 100.0, 0.0),
]

TRAP_TYPES = [
    {"en": "Conic",   "es": "Cónica"},
    {"en": "Flat",    "es": "Plana"},
    {"en": "Rounded", "es": "Redonda"},
]

DEPTH_MODES = [
    {"en": "Absolute (units)", "es": "Absoluta (u)"},
    {"en": "Relative (% stem)", "es": "Relativa (% asta)"},
]


def fmt(name, value):
    if name == "width":
        return "%.2f" % value
    return "%d" % int(round(value))


# ─────────────────────────────────────────────────────────────────────────────
# Geometría (Python puro, tuplas (x, y))
# ─────────────────────────────────────────────────────────────────────────────

def _lerp(p, q, t):
    return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)


def _bez(P0, P1, P2, P3, t):
    a = _lerp(P0, P1, t); b = _lerp(P1, P2, t); c = _lerp(P2, P3, t)
    d = _lerp(a, b, t); e = _lerp(b, c, t)
    return _lerp(d, e, t)


def _split(P0, P1, P2, P3, t):
    a = _lerp(P0, P1, t); b = _lerp(P1, P2, t); c = _lerp(P2, P3, t)
    d = _lerp(a, b, t); e = _lerp(b, c, t); f = _lerp(d, e, t)
    return (P0, a, d, f), (f, e, c, P3)


def _subcurve(P0, P1, P2, P3, t0, t1):
    if t0 > 0.0:
        _, right = _split(P0, P1, P2, P3, t0)
        P0, P1, P2, P3 = right
        t1 = (t1 - t0) / (1.0 - t0) if t0 < 1.0 else 0.0
    if t1 < 1.0:
        left, _ = _split(P0, P1, P2, P3, t1)
        return left
    return (P0, P1, P2, P3)


def _curve_arclen(P0, P1, P2, P3, N=32):
    prev = P0; acc = 0.0
    for s in range(1, N + 1):
        pt = _bez(P0, P1, P2, P3, s / N)
        acc += math.hypot(pt[0] - prev[0], pt[1] - prev[1]); prev = pt
    return acc


def _curve_param(P0, P1, P2, P3, dist, N=48):
    prev = P0; acc = 0.0; lastt = 0.0
    for s in range(1, N + 1):
        t = s / N; pt = _bez(P0, P1, P2, P3, t)
        seg = math.hypot(pt[0] - prev[0], pt[1] - prev[1])
        if acc + seg >= dist:
            frac = (dist - acc) / seg if seg > 1e-9 else 0.0
            return lastt + (t - lastt) * frac
        acc += seg; prev = pt; lastt = t
    return 1.0


def _seg_len(seg, P0, P3):
    if seg[0] == "line":
        return math.hypot(P3[0] - P0[0], P3[1] - P0[1])
    return _curve_arclen(P0, seg[1], seg[2], P3)


def _seg_param_from_start(seg, P0, P3, dist):
    if seg[0] == "line":
        L = math.hypot(P3[0] - P0[0], P3[1] - P0[1])
        return min(1.0, dist / L) if L > 1e-9 else 0.0
    return _curve_param(P0, seg[1], seg[2], P3, dist)


def _seg_point(seg, P0, P3, t):
    if seg[0] == "line":
        return _lerp(P0, P3, t)
    return _bez(P0, seg[1], seg[2], P3, t)


def _seg_sub(seg, P0, P3, t0, t1):
    """Sub-segmento entre t0 y t1: devuelve (segDesc, startPt, endPt)."""
    if seg[0] == "line":
        return ("line",), _lerp(P0, P3, t0), _lerp(P0, P3, t1)
    sub = _subcurve(P0, seg[1], seg[2], P3, t0, t1)
    return ("curve", sub[1], sub[2]), sub[0], sub[3]


def _unit(v):
    l = math.hypot(v[0], v[1])
    if l < 1e-9:
        return None
    return (v[0] / l, v[1] / l)


def _arc_offcurves(P0, P1, C):
    """Dos puntos de control cúbicos que aproximan el arco circular de centro C
    entre P0 y P1 (arco menor). Aproximación estándar k = (4/3)·tan(da/4)·R."""
    a0 = math.atan2(P0[1] - C[1], P0[0] - C[0])
    a1 = math.atan2(P1[1] - C[1], P1[0] - C[0])
    da = a1 - a0
    while da <= -math.pi:
        da += 2 * math.pi
    while da > math.pi:
        da -= 2 * math.pi
    R = math.hypot(P0[0] - C[0], P0[1] - C[1])
    k = (4.0 / 3.0) * math.tan(da / 4.0) * R
    t0 = (-math.sin(a0), math.cos(a0))
    t1 = (-math.sin(a1), math.cos(a1))
    off0 = (P0[0] + t0[0] * k, P0[1] + t0[1] * k)
    off1 = (P1[0] - t1[0] * k, P1[1] - t1[1] * k)
    return off0, off1


def _ray_seg_t(A, d, p0, p1):
    """Distancia t>=0 desde A en dirección d hasta el segmento p0->p1, o None.
    Intersección rayo-segmento estándar (cross products)."""
    ex = p1[0] - p0[0]
    ey = p1[1] - p0[1]
    den = d[0] * ey - d[1] * ex
    if abs(den) < 1e-12:
        return None
    wx = p0[0] - A[0]
    wy = p0[1] - A[1]
    t = (wx * ey - wy * ex) / den
    s = (wx * d[1] - wy * d[0]) / den
    if t >= 0.0 and 0.0 <= s <= 1.0:
        return t
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Plugin
# ─────────────────────────────────────────────────────────────────────────────
class InkTrapGenerator(FilterWithDialog):

    # ─────────────────────────────────────────────────────────────────────
    # Setup & UI
    # ─────────────────────────────────────────────────────────────────────
    @objc.python_method
    def settings(self):
        self.menuName = Glyphs.localize({"en": "Ink Trap", "es": "Trampa de tinta"})
        self.actionButtonLabel = Glyphs.localize({"en": "Apply", "es": "Aplicar"})

        width = 340
        height = 302
        self.paletteView = Window((width, height))
        self.paletteView.group = Group((0, 0, width, height))
        g = self.paletteView.group

        g.trapType = RadioGroup(
            (12, 10, -12, 20),
            [Glyphs.localize(t) for t in TRAP_TYPES],
            isVertical=False, callback=self.uiCallback,
        )
        g.depthMode = RadioGroup(
            (12, 38, -12, 20),
            [Glyphs.localize(d) for d in DEPTH_MODES],
            isVertical=False, callback=self.uiCallback,
        )

        y = 74
        for name, label, lo, hi, dflt in SLIDER_SPECS:
            setattr(g, "lbl_" + name, TextBox((12, y + 2, 116, 17), Glyphs.localize(label)))
            setattr(g, "sld_" + name, Slider((132, y, -52, 23), minValue=lo, maxValue=hi, value=dflt, callback=self.uiCallback))
            setattr(g, "val_" + name, TextBox((-46, y + 2, -12, 17), fmt(name, dflt), alignment="right"))
            y += 30

        g.resetButton = Button((12, y + 4, -12, 20),
                               Glyphs.localize({"en": "Reset", "es": "Restablecer"}),
                               callback=self.resetCallback)

        self.dialog = self.paletteView.group.getNSView()

    @objc.python_method
    def start(self):
        if Glyphs.defaults[dkey("trapType")] is None:
            Glyphs.defaults[dkey("trapType")] = 0
        if Glyphs.defaults[dkey("depthMode")] is None:
            Glyphs.defaults[dkey("depthMode")] = 1  # Relativo por defecto
        # Migración única: asegurar que Relativo quede marcado por defecto al abrir,
        # incluso si una versión previa había guardado Absoluto. Solo se aplica una vez;
        # después se respeta la elección del usuario.
        if not Glyphs.defaults[dkey("relativeDefaultMigrated")]:
            Glyphs.defaults[dkey("depthMode")] = 1
            Glyphs.defaults[dkey("relativeDefaultMigrated")] = True
        for name, _, _, _, dflt in SLIDER_SPECS:
            if Glyphs.defaults[dkey(name)] is None:
                Glyphs.defaults[dkey(name)] = dflt
        g = self.paletteView.group
        g.trapType.set(int(Glyphs.defaults[dkey("trapType")]))
        g.depthMode.set(int(Glyphs.defaults[dkey("depthMode")]))
        for name, _, _, _, _ in SLIDER_SPECS:
            v = float(Glyphs.defaults[dkey(name)])
            getattr(g, "sld_" + name).set(v)
            getattr(g, "val_" + name).set(self._valLabel(name, v))
        self.update()

    @objc.python_method
    def _valLabel(self, name, v):
        # La profundidad muestra "%" en modo relativo; el suavizado siempre en %
        if name == "smoothing":
            return "%d%%" % int(round(v))
        if name == "length" and int(Glyphs.defaults[dkey("depthMode")] or 0) == 1:
            return "%d%%" % int(round(v))
        return fmt(name, v)

    @objc.python_method
    def uiCallback(self, sender):
        g = self.paletteView.group
        Glyphs.defaults[dkey("trapType")] = g.trapType.get()
        Glyphs.defaults[dkey("depthMode")] = g.depthMode.get()
        for name, _, _, _, _ in SLIDER_SPECS:
            v = getattr(g, "sld_" + name).get()
            Glyphs.defaults[dkey(name)] = v
            getattr(g, "val_" + name).set(self._valLabel(name, v))
        self.update()

    @objc.python_method
    def resetCallback(self, sender):
        # Restablece valores por defecto (conserva tipo de trampa y modo de profundidad)
        sane = {"minAngle": 0.0, "maxAngle": 100.0, "length": 45.0, "width": 0.15,
                "minSegment": 0.0, "smoothing": 0.0}
        g = self.paletteView.group
        for name, v in sane.items():
            Glyphs.defaults[dkey(name)] = v
            getattr(g, "sld_" + name).set(v)
            getattr(g, "val_" + name).set(self._valLabel(name, v))
        self.update()

    @objc.python_method
    def _num(self, key, fallback):
        try:
            return float(Glyphs.defaults[dkey(key)])
        except (TypeError, ValueError, KeyError):
            return fallback

    # ─────────────────────────────────────────────────────────────────────
    # Filter & procesado
    # ─────────────────────────────────────────────────────────────────────
    @objc.python_method
    def filter(self, layer, inEditView, customParameters):
        # Solo sobre el master. Si se invocara como Custom Parameter en export, no se ejecuta.
        if customParameters:
            return
        try:
            p = {
                "trapType": int(self._num("trapType", 0)),
                "depthMode": int(self._num("depthMode", 1)),
                "minAngle": self._num("minAngle", 0.0),
                "maxAngle": self._num("maxAngle", 100.0),
                "length": max(0.0, self._num("length", 45.0)),
                "width": self._num("width", 0.15),
                "minSegment": self._num("minSegment", 0.0),
                "smoothing": self._num("smoothing", 0.0),
            }
            # Si hay nodos on-curve seleccionados en Edit View, limitar a esas junturas.
            selPositions = self._selectedOncurvePositions(layer) if inEditView else None
            self.processLayer(layer, p, selPositions)
        except Exception:
            import traceback
            NSLog("InkTrapGenerator: %@", traceback.format_exc())

    @objc.python_method
    def _stemRef(self, layer):
        """Grosor de asta de referencia del master (fallback 50 si no hay stems)."""
        try:
            stems = layer.master.stems
            vals = [abs(float(s)) for s in stems if s is not None]
            vals = [v for v in vals if v > 1.0]
            if vals:
                return min(vals)
        except Exception:
            pass
        return 50.0

    @objc.python_method
    def _flattenLayer(self, layer):
        """Aplana todos los contornos (paths) a polígonos de puntos. Geometría pura,
        sin objetos Cocoa: seguro en hilos de fondo (export)."""
        polys = []
        for path in list(layer.paths):
            data = self._readContour(path)
            if not data:
                pts = [(nd.position.x, nd.position.y) for nd in path.nodes if nd.type != OFFCURVE]
                if len(pts) >= 3:
                    polys.append(pts)
                continue
            anchors, segs = data
            m = len(anchors)
            pts = []
            for i in range(m):
                A = anchors[i]["pos"]
                B = anchors[(i + 1) % m]["pos"]
                seg = segs[i]
                pts.append(A)
                if seg[0] == "curve":
                    for s in range(1, 8):
                        pts.append(_bez(A, seg[1], seg[2], B, s / 8.0))
            if len(pts) >= 3:
                polys.append(pts)
        return polys

    @objc.python_method
    def _inBlack(self, pt, polys):
        """Punto dentro del relleno por winding number (regla non-zero). Sustituye a
        bezierPath.containsPoint_, sin Cocoa."""
        x, y = pt
        wn = 0
        for poly in polys:
            n = len(poly)
            for i in range(n):
                x0, y0 = poly[i]
                x1, y1 = poly[(i + 1) % n]
                if y0 <= y:
                    if y1 > y and ((x1 - x0) * (y - y0) - (x - x0) * (y1 - y0)) > 0:
                        wn += 1
                else:
                    if y1 <= y and ((x1 - x0) * (y - y0) - (x - x0) * (y1 - y0)) < 0:
                        wn -= 1
        return wn != 0

    @objc.python_method
    def _blackDepthAlong(self, A, d, polys, eps=1.0):
        """Distancia desde A (en dirección d, unitaria) hasta que el rayo sale del negro:
        la primera arista cruzada con t>eps. Mide cuánto trazo hay disponible hacia dentro."""
        best = None
        for poly in polys:
            n = len(poly)
            for i in range(n):
                t = _ray_seg_t(A, d, poly[i], poly[(i + 1) % n])
                if t is not None and t > eps:
                    if best is None or t < best:
                        best = t
        return best

    @objc.python_method
    def _selectedOncurvePositions(self, layer):
        """Posiciones (redondeadas) de los nodos on-curve seleccionados, o None si no hay
        selección de nodos. Se lee defensivamente (la selección puede ser nil)."""
        try:
            sel = layer.selection
            if not sel:
                return None
            pts = set()
            for el in sel:
                if isinstance(el, GSNode) and el.type != OFFCURVE:
                    pts.add((round(el.position.x, 3), round(el.position.y, 3)))
            return pts if pts else None
        except Exception:
            return None

    @objc.python_method
    def processLayer(self, layer, p, selPositions=None):
        try:
            polys = self._flattenLayer(layer)
            if not polys:
                return
            # Modo relativo: la profundidad (en % del grosor de asta) pasa a unidades.
            if p.get("depthMode", 0) == 1:
                p = dict(p)
                p["length"] = (p["length"] / 100.0) * self._stemRef(layer)
            # Primero calcula la nueva lista de nodos de cada path (sin mutar todavía),
            # para no tocar la geometría si algo falla. Solo se itera sobre PATHS:
            # los componentes nunca se tocan.
            jobs = []
            for path in list(layer.paths):
                try:
                    nodeList = self._rebuildPath(path, p, polys, selPositions)
                except Exception:
                    import traceback
                    NSLog("InkTrapGenerator: %@", traceback.format_exc())
                    nodeList = None
                if nodeList:
                    jobs.append((path, nodeList))
            # Aplica la reescritura en sitio (sin reasignar layer.shapes)
            for path, nodeList in jobs:
                self._applyNodeList(path, nodeList)
        except Exception:
            import traceback
            NSLog("InkTrapGenerator: %@", traceback.format_exc())

    @objc.python_method
    def _readContour(self, path):
        """Devuelve (anchors, segs) con tuplas, o None si la estructura no es estándar."""
        nodes = list(path.nodes)
        n = len(nodes)
        if n < 3:
            return None
        onc = [k for k in range(n) if nodes[k].type != OFFCURVE]
        m = len(onc)
        if m < 3:
            return None
        anchors = []
        segs = []
        for i in range(m):
            k = onc[i]
            nk = nodes[k]
            anchors.append({"pos": (nk.position.x, nk.position.y), "smooth": bool(nk.smooth)})
            knext = onc[(i + 1) % m]
            between = []
            kk = (k + 1) % n
            while kk != knext:
                between.append(nodes[kk]); kk = (kk + 1) % n
            if len(between) == 0:
                segs.append(("line",))
            elif len(between) == 2 and between[0].type == OFFCURVE and between[1].type == OFFCURVE:
                segs.append(("curve", (between[0].position.x, between[0].position.y),
                             (between[1].position.x, between[1].position.y)))
            else:
                return None  # qcurve u offcurves inesperados: no tocar este contorno
        return anchors, segs

    @objc.python_method
    def _rebuildPath(self, path, p, polys, selPositions=None):
        if not path.closed:
            return None
        data = self._readContour(path)
        if data is None:
            return None
        anchors, segs = data
        m = len(anchors)

        trapType = p["trapType"]
        minAngle, maxAngle = p["minAngle"], p["maxAngle"]
        length, widthF, minSeg = p["length"], p["width"], p["minSegment"]
        smooth = max(0.0, min(1.0, p.get("smoothing", 0.0) / 100.0))

        isCorner = [False] * m
        tA = [0.0] * m   # param sobre segs[(i-1)%m] (labio de entrada)
        tB = [0.0] * m   # param sobre segs[i] (labio de salida)
        trap = [None] * m

        for i in range(m):
            A = anchors[i]["pos"]
            if anchors[i]["smooth"]:
                continue
            # Si hay selección de nodos, solo trampear las junturas seleccionadas.
            if selPositions is not None and (round(A[0], 3), round(A[1], 3)) not in selPositions:
                continue
            inSeg = segs[(i - 1) % m]
            outSeg = segs[i]
            PA = anchors[(i - 1) % m]["pos"]
            NA = anchors[(i + 1) % m]["pos"]

            # refs de tangente
            inRef = inSeg[2] if inSeg[0] == "curve" else PA
            outRef = outSeg[1] if outSeg[0] == "curve" else NA
            u1 = _unit((inRef[0] - A[0], inRef[1] - A[1]))
            u2 = _unit((outRef[0] - A[0], outRef[1] - A[1]))
            if u1 is None or u2 is None:
                continue

            inLen = _seg_len(inSeg, PA, A)
            outLen = _seg_len(outSeg, A, NA)
            if inLen < minSeg or outLen < minSeg or inLen < 1e-6 or outLen < 1e-6:
                continue

            cosA = max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))
            angle = math.degrees(math.acos(cosA))
            if angle < minAngle or angle > maxAngle:
                continue

            bis = _unit((u1[0] + u2[0], u1[1] + u2[1]))
            if bis is None:
                continue
            # solo junturas cóncavas: +bis cae en blanco
            if self._inBlack((A[0] + bis[0], A[1] + bis[1]), polys):
                continue

            # Guardarraíl: no dejar que la trampa sea más honda que el trazo disponible
            # hacia dentro (en dirección -bis), para que nunca traspase al otro lado.
            cornerLength = length
            avail = self._blackDepthAlong(A, (-bis[0], -bis[1]), polys)
            if avail is not None:
                cornerLength = min(cornerLength, 0.8 * avail)

            lipDist = widthF * min(inLen, outLen)
            lipDist = max(1.0, min(lipDist, 0.45 * inLen, 0.45 * outLen))

            ti = _seg_param_from_start(inSeg, PA, A, max(0.0, inLen - lipDist))
            to = _seg_param_from_start(outSeg, A, NA, lipDist)
            lipA = _seg_point(inSeg, PA, A, ti)
            lipB = _seg_point(outSeg, A, NA, to)

            trapAnchors = self._trapAnchors(trapType, A, bis, lipA, lipB, cornerLength, polys)
            if trapAnchors is None:
                continue
            if smooth > 0.0:
                trapAnchors = self._smoothLips(trapAnchors, u1, u2, smooth)

            isCorner[i] = True
            tA[i] = ti
            tB[i] = to
            trap[i] = trapAnchors

        if not any(isCorner):
            return None

        # Construir la lista de "emit anchors" con su segmento de salida
        emit = []
        for i in range(m):
            if isCorner[i]:
                for a in trap[i]:
                    emit.append(dict(a))  # copia
            else:
                emit.append({"pos": anchors[i]["pos"], "smooth": anchors[i]["smooth"], "out": None})
            # segmento de salida del anchor terminal (lipB_i o A_i) = segs[i] recortado
            t0 = tB[i] if isCorner[i] else 0.0
            nxt = (i + 1) % m
            t1 = tA[nxt] if isCorner[nxt] else 1.0
            newSeg, _sp, _ep = _seg_sub(segs[i], anchors[i]["pos"], anchors[nxt]["pos"], t0, t1)
            emit[-1]["out"] = newSeg

        # Serializar a nodos
        E = len(emit)
        nodeList = []
        for k in range(E):
            a = emit[k]
            inSeg = emit[(k - 1) % E]["out"]
            onType = CURVE if inSeg[0] == "curve" else LINE
            nodeList.append((a["pos"], onType, a.get("smooth", False)))
            if a["out"][0] == "curve":
                nodeList.append((a["out"][1], OFFCURVE, False))
                nodeList.append((a["out"][2], OFFCURVE, False))

        newPath = nodeList
        return newPath

    @objc.python_method
    def _applyNodeList(self, path, nodeList):
        """Reescribe los nodos de un path EXISTENTE en sitio (sin reasignar layer.shapes
        ni crear paths nuevos). Devuelve True si tuvo éxito."""
        try:
            while len(path.nodes) > 0:
                del path.nodes[len(path.nodes) - 1]
            for pos, ntype, smooth in nodeList:
                nd = GSNode(NSPoint(pos[0], pos[1]), ntype)
                if smooth:
                    nd.smooth = True
                path.addNode_(nd)
            path.closed = True
            return True
        except Exception:
            import traceback
            NSLog("InkTrapGenerator: %@", traceback.format_exc())
            return False

    # ─────────────────────────────────────────────────────────────────────
    # Construcción de trampas
    # ─────────────────────────────────────────────────────────────────────
    @objc.python_method
    def _trapAnchors(self, trapType, A, bis, lipA, lipB, length, polys):
        """Anchors que sustituyen la esquina (lipA .. lipB). 'out' de lipB se fija fuera.
        Devuelve None si la trampa no cae dentro del negro."""
        # Chaflán: profundidad ~0 -> corte recto lipA->lipB
        if length < 1.0:
            return [
                {"pos": lipA, "smooth": False, "out": ("line",)},
                {"pos": lipB, "smooth": False, "out": None},
            ]

        if trapType == 1:  # Plana
            floorA = (lipA[0] - bis[0] * length, lipA[1] - bis[1] * length)
            floorB = (lipB[0] - bis[0] * length, lipB[1] - bis[1] * length)
            mid = ((floorA[0] + floorB[0]) / 2.0, (floorA[1] + floorB[1]) / 2.0)
            if not self._inBlack((mid[0], mid[1]), polys):
                return None
            return [
                {"pos": lipA, "smooth": False, "out": ("line",)},
                {"pos": floorA, "smooth": False, "out": ("line",)},
                {"pos": floorB, "smooth": False, "out": ("line",)},
                {"pos": lipB, "smooth": False, "out": None},
            ]

        if trapType == 2:  # Redonda (arco circular real a través de lipA, fondo, lipB)
            mm = ((lipA[0] + lipB[0]) / 2.0, (lipA[1] + lipB[1]) / 2.0)
            depth = length
            bottom = (mm[0] - bis[0] * depth, mm[1] - bis[1] * depth)
            if not self._inBlack((bottom[0], bottom[1]), polys):
                return None
            ml = math.hypot(lipB[0] - lipA[0], lipB[1] - lipA[1])
            halfMouth = ml / 2.0
            if depth < 1e-6 or halfMouth < 1e-6:
                return None
            # Centro sobre el eje (dirección -bis), a distancia cdist del punto medio
            cdist = (depth * depth - halfMouth * halfMouth) / (2.0 * depth)
            center = (mm[0] - bis[0] * cdist, mm[1] - bis[1] * cdist)
            o1, o2 = _arc_offcurves(lipA, bottom, center)   # arco lipA -> fondo
            o3, o4 = _arc_offcurves(bottom, lipB, center)   # arco fondo -> lipB
            return [
                {"pos": lipA, "smooth": False, "out": ("curve", o1, o2)},
                {"pos": bottom, "smooth": True, "out": ("curve", o3, o4)},
                {"pos": lipB, "smooth": False, "out": None},
            ]

        # Cónica (V)
        apex = (A[0] - bis[0] * length, A[1] - bis[1] * length)
        if not self._inBlack((apex[0], apex[1]), polys):
            return None
        return [
            {"pos": lipA, "smooth": False, "out": ("line",)},
            {"pos": apex, "smooth": False, "out": ("line",)},
            {"pos": lipB, "smooth": False, "out": None},
        ]

    @objc.python_method
    def _smoothLips(self, anchors, u1, u2, s):
        """Suaviza la unión de la trampa con el contorno (labios lipA/lipB).
        Convierte los segmentos extremos en curvas tangentes al contorno y marca
        los labios como nodos suaves. u1/u2 = tangentes del contorno en la esquina."""
        if s <= 0.0 or len(anchors) < 2:
            return anchors

        def hlen(p, q):
            return s * 0.5 * math.hypot(q[0] - p[0], q[1] - p[1])

        if len(anchors) == 2:
            # chaflán: una sola curva que entra y sale tangente al contorno
            a = anchors[0]["pos"]; b = anchors[1]["pos"]
            L = hlen(a, b)
            h1 = (a[0] - u1[0] * L, a[1] - u1[1] * L)
            h2 = (b[0] - u2[0] * L, b[1] - u2[1] * L)
            anchors[0]["out"] = ("curve", h1, h2)
            anchors[0]["smooth"] = True
            anchors[1]["smooth"] = True
            return anchors

        # lipA (primer anchor): curva tangente al contorno entrante
        a = anchors[0]["pos"]; nxt = anchors[1]["pos"]
        L = hlen(a, nxt)
        toN = _unit((nxt[0] - a[0], nxt[1] - a[1])) or (0.0, 0.0)
        anchors[0]["out"] = ("curve",
                             (a[0] - u1[0] * L, a[1] - u1[1] * L),
                             (nxt[0] - toN[0] * L, nxt[1] - toN[1] * L))
        anchors[0]["smooth"] = True

        # lipB (último anchor): curva tangente al contorno saliente
        prev = anchors[-2]["pos"]; b = anchors[-1]["pos"]
        L = hlen(prev, b)
        toB = _unit((b[0] - prev[0], b[1] - prev[1])) or (0.0, 0.0)
        anchors[-2]["out"] = ("curve",
                              (prev[0] + toB[0] * L, prev[1] + toB[1] * L),
                              (b[0] - u2[0] * L, b[1] - u2[1] * L))
        anchors[-1]["smooth"] = True
        return anchors

    @objc.python_method
    def __file__(self):
        return __file__
