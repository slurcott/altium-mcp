"""Decode chip resistor / MLCC part numbers into comparable attributes.

Used by BOM consolidation: two BOM lines are the same *requirement* when type,
package and value match; the attributes decide which part can stand in for
which (upgrade-only). Decoders cover the series that show up in real BOMs;
anything else falls back to parsing the description, and whatever is still
unknown stays None - the consolidator treats None as "must check by hand",
never as a match.

Units: resistance in ohms, capacitance in farads, voltage in volts,
tolerance in percent, power in watts. Package is the EIA imperial code.
"""
import re

# Dielectric ranking, better first. C0G/NP0 > X8R > X7R > X7S > X6S > X5R > Y5V.
DIELECTRIC_RANK = {"C0G": 7, "X8R": 6, "X7R": 5, "X7S": 4, "X6S": 3, "X5R": 2, "Y5V": 1}

_METRIC_TO_EIA = {"0603": "0201", "1005": "0402", "1608": "0603", "2012": "0805",
                  "3216": "1206", "3225": "1210", "4532": "1812", "3264": "1225"}


def si(text):
    """'4k7' / '4.7k' / '100nF' / '22uF' / '33R' / '1M0' -> float, else None."""
    t = text.strip().replace("µ", "u").replace("μ", "u").replace("Ω", "").replace(" ", "")
    t = re.sub(r"(?i)(ohms?|f)$", "", t)
    m = re.fullmatch(r"(\d*)([.,]?\d*)([pnumkKMR]?)(\d*)", t)
    if not m or not (m.group(1) or m.group(2)):
        return None
    whole, frac, mult, tail = m.groups()
    if tail and not frac:                          # 4k7 style
        num = float(f"{whole}.{tail}")
    else:
        num = float((whole or "0") + (frac.replace(",", ".") if frac else ""))
    scale = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3,
             "M": 1e6, "R": 1.0, "": 1.0}[mult]
    return num * scale


def code3(code, unit_pf=True):
    """EIA 3/4-digit code: '104' -> 100000 (pF for caps, ohm for resistors).
    An 'R' marks the decimal point: '4R7' -> 4.7."""
    if "R" in code:
        return float(code.replace("R", "."))
    # EIA multiplier digit: 0-6 = 10^n, 8 = x0.01, 9 = x0.1 (6R8 is also written 689)
    exp = {"8": -2, "9": -1}.get(code[-1], None)
    return float(code[:-1]) * 10 ** (int(code[-1]) if exp is None else exp)


def _cap(pkg, pf, volts, diel, tol, aec=False, series=""):
    return {"type": "C", "package": pkg, "value": pf * 1e-12 if pf is not None else None,
            "voltage": volts, "dielectric": diel, "tolerance": tol, "aec": aec,
            "series": series}


def _res(pkg, ohms, tol, power=None, tech="thick", aec=False, series=""):
    return {"type": "R", "package": pkg, "value": ohms, "tolerance": tol,
            "power": power, "tech": tech, "aec": aec, "series": series}


# B/C/D are absolute (+/-0.1/0.25/0.5 pF) on small C0G parts - not a percentage
_CAP_TOL = {"B": None, "C": None, "D": None, "F": 1, "G": 2, "J": 5, "K": 10, "M": 20, "Z": 80}
_RES_TOL = {"A": 0.05, "B": 0.1, "C": 0.25, "D": 0.5, "F": 1, "G": 2, "J": 5, "W": 0.05}


def _kemet(m):   # C0603C105K4RACTU, C0805X104J5RACTU, C0402H102J5GACT500, ...AUTO
    v = {"9": 6.3, "8": 10, "4": 16, "3": 25, "6": 35, "5": 50, "1": 100, "2": 200, "A": 250}
    d = {"R": "X7R", "G": "C0G", "P": "X5R", "U": "Z5U", "V": "Y5V"}
    return _cap(m[1], code3(m[2]), v.get(m[4]), d.get(m[5]), _CAP_TOL.get(m[3]),
                aec=m[0].endswith("AUTO"), series="KEMET C")


def _avx(m):     # 06035C104KAZ2A, 12066C226KAT2A, 18121C104KAT2A
    v = {"4": 4, "6": 6.3, "Z": 10, "Y": 16, "3": 25, "D": 35, "5": 50, "1": 100, "2": 200}
    d = {"C": "X7R", "A": "C0G", "D": "X5R", "G": "Y5V"}
    return _cap(m[1], code3(m[4]), v.get(m[2]), d.get(m[3]), _CAP_TOL.get(m[5]),
                aec=m[0][-1:] == "4", series="AVX")


def _yageo_cc(m):  # CC1206KKX5R5BB226, CC0603MRX5R7BB105
    v = {"4": 4, "5": 6.3, "6": 10, "7": 16, "8": 25, "9": 50, "0": 100}
    return _cap(m[1], code3(m[5]), v.get(m[4]), m[3].replace("NPO", "C0G"),
                _CAP_TOL.get(m[2]), aec=m[0].startswith("AC"), series="Yageo CC")


_MURATA_SIZE = {"03": "0201", "15": "0402", "18": "0603", "21": "0805", "31": "1206",
                "32": "1210", "43": "1812"}
_MURATA_DIEL = {"R7": "X7R", "Z7": "X7R", "C7": "X7S", "C8": "X6S", "R6": "X5R",
                "5C": "C0G", "L8": "X8L", "R9": "X8R"}
_V2 = {"0J": 6.3, "1A": 10, "1C": 16, "1E": 25, "YA": 35, "1V": 35, "1H": 50,
       "2A": 100, "2E": 250}


def _murata(m):  # GRM21BZ71H475ME15K, GCM32ER71C226ME19K
    return _cap(_MURATA_SIZE.get(m[2]), code3(m[6]), _V2.get(m[5]),
                _MURATA_DIEL.get(m[4]), _CAP_TOL.get(m[7]), aec=m[1] == "GCM",
                series="Murata " + m[1])


def _tdk(m):     # CGA3E2X7R1H104K080AA, CGA3E2NP02A090D080AA, C1608X7R1H104K
    size = {"1": "0201", "2": "0402", "3": "0603", "4": "0805", "5": "1206", "6": "1210",
            "1005": "0402", "1608": "0603", "2012": "0805", "3216": "1206", "3225": "1210"}
    diel = {"NP0": "C0G", "C0G": "C0G"}.get(m[3], m[3])
    return _cap(size.get(m[2]), code3(m[5]), _V2.get(m[4]), diel, _CAP_TOL.get(m[6]),
                aec=m[1] == "CGA", series="TDK " + m[1])


def _taiyo(m):   # UMK316B7105KLHT, UMK212BB7225KG-T, UMR325AC7106KM-P
    v = {"A": 4, "J": 6.3, "L": 10, "E": 16, "T": 25, "G": 35, "U": 50, "H": 100, "Q": 250}
    d = {"B7": "X7R", "C7": "X7S", "BJ": "X5R", "C6": "X6S", "CG": "C0G"}
    return _cap(_METRIC_TO_EIA.get("3216" if m[2] == "316" else
                                   {"105": "1005", "107": "1608", "212": "2012",
                                    "325": "3225", "432": "4532"}.get(m[2], "")),
                code3(m[4]), v.get(m[1]), d.get(m[3]), _CAP_TOL.get(m[5]),
                series="Taiyo Yuden")


_YAGEO_POWER = {"0201": 0.05, "0402": 0.0625, "0603": 0.1, "0805": 0.125, "1206": 0.25}


def _yageo_r(m):  # RC0603FR-131KL, AC0603JR-0720KL, RC0402JR-100RL, AC0603FR-7W10KL
    # After the dash: reel code (07/10/13), or 7W/10W/13W = the double-power
    # version on that reel; the value follows.
    power = _YAGEO_POWER.get(m[2])
    if power and m[4].endswith("W"):
        power *= 2
    return _res(m[2], si(m[5]), _RES_TOL.get(m[3]), power=power, aec=m[1] == "AC",
                series="Yageo " + m[1] + (" double power" if m[4].endswith("W") else ""))


def _stackpole_hcj(m):  # HCJ0805ZT0R00 - high-current jumper
    return _res(m[1], 0.0, None, aec=True, series="Stackpole HCJ jumper")


def _stackpole_rncp(m):  # RNCP0603FTD2K49 - anti-sulfur thin film
    return _res(m[1], si(m[3]), _RES_TOL.get(m[2]), power=_RMCF_POWER.get(m[1]), tech="thin",
                aec=True, series="Stackpole RNCP")


_RMCF_POWER = {"0201": 0.05, "0402": 0.0625, "0603": 0.1, "0805": 0.125, "1206": 0.25}


def _stackpole(m):  # RMCF0603JG33R0, RMCF0603FG2K20, RMCF0402JT4R70
    return _res(m[1], si(m[3]), _RES_TOL.get(m[2]), power=_RMCF_POWER.get(m[1]), aec=True,
                series="Stackpole RMCF")


def rmcf_1pct(package, ohms):
    """Stackpole RMCF 1 % AEC-Q200 thick-film part number for a value, e.g.
    (0603, 4700) -> RMCF0603FT4K70. Three significant digits with the multiplier
    letter as the decimal point: 4R70, 33R0, 120R, 1K00, 24K9, 100K, 1M00.
    None when the package or value is outside the series. Constructed, so it
    must be confirmed at a distributor before it goes on a BOM."""
    if package not in _RMCF_POWER or not ohms or ohms < 1 or ohms > 10e6:
        return None
    for letter, scale in (("M", 1e6), ("K", 1e3), ("R", 1.0)):
        if ohms >= scale:
            v = ohms / scale
            break
    digits = f"{v:.3g}"
    if "e" in digits:
        return None
    whole, _, frac = digits.partition(".")
    code = whole + letter + frac
    code = (code + "000")[:4] if len(whole) < 3 else whole + letter
    return f"RMCF{package}FT{code}"


def _vishay(m):  # CRCW060333K0JNEC
    return _res(m[1], si(m[2]), _RES_TOL.get(m[3]), aec=True, series="Vishay CRCW")


def _te_crg(m):  # CRGCQ0603J1K0, CRGCQ0805J47R, CRGP0805F1M0, CPF0603F9K09C1
    tech = "thin" if m[1] == "CPF" else "thick"
    return _res(m[2], si(m[4]), _RES_TOL.get(m[3]), tech=tech,
                aec=m[1] == "CRGCQ", series="TE " + m[1])


def _rohm_mcr(m):  # MCR03FZPJ472, MCR03FZPFX2492
    pkg = {"01": "0402", "03": "0603", "10": "0805", "18": "1206"}.get(m[1])
    return _res(pkg, code3(m[3]), _RES_TOL.get(m[2]), series="ROHM MCR")


def _koa(m):     # RK73B1JLTD104J, RN732ATTD9092F100
    pkg = {"1E": "0402", "1J": "0603", "2A": "0805", "2B": "1206"}.get(m[2])
    if m[1] == "RK73B":
        return _res(pkg, code3(m[3]), _RES_TOL.get(m[4]), series="KOA RK73")
    return _res(pkg, code3(m[3]), _RES_TOL.get(m[4]), tech="thin", series="KOA RN73")


_DECODERS = [
    (re.compile(r"^C(0201|0402|0603|0805|1206|1210|1812)[CXHS](\d{3})([B-MZ])([0-9A])([RGPUV])"), _kemet),
    (re.compile(r"^(0402|0603|0805|1206|1210|1812)([46ZY3D5125])([CADG])(\d{3})([B-MZ])"), _avx),
    (re.compile(r"^A?CC(0201|0402|0603|0805|1206|1210|1812)([B-MZ])[KR](X7R|X5R|NPO|C0G|X7S|Y5V)(\d)BB(\d{3})"), _yageo_cc),
    (re.compile(r"^(GRM|GCM|GCJ)(\d\d)(\w)(R7|Z7|C7|C8|R6|5C|L8|R9)(\w\w)(\d{3})([B-MZ])"), _murata),
    (re.compile(r"^(CGA|C|CGJ)(\d{4}|\d)\w?\d?(X7R|X5R|X7S|X6S|NP0|C0G)(\d\w)(\d{3})([B-MZ])"), _tdk),
    (re.compile(r"^([AJLETGUHQ])M[KRJ](105|107|212|316|325|432)[A-Z]?(B7|C7|BJ|C6|CG)(\d{3})([B-MZ])"), _taiyo),
    (re.compile(r"^(RC|AC)(0201|0402|0603|0805|1206)([A-JW])R-(07W?|10W?|13W?|7W)(\w+?)L$"), _yageo_r),
    (re.compile(r"^HCJ(0402|0603|0805|1206)ZT0R00$"), _stackpole_hcj),
    (re.compile(r"^RNCP(0402|0603|0805|1206)([BCDF])T[A-Z]?(\w+)$"), _stackpole_rncp),
    (re.compile(r"^RMCF(0201|0402|0603|0805|1206)([FGJ])[GT](\w+)$"), _stackpole),
    (re.compile(r"^CRCW(0402|0603|0805|1206)(\w+?)([FGJ])\w{3}$"), _vishay),
    (re.compile(r"^(CRGCQ|CRGP|CRG|CPF)(0402|0603|0805|1206)([FJ])(\w+?)(C1)?$"), _te_crg),
    (re.compile(r"^MCR(01|03|10|18)\w{2,3}([FJ])X?(\d{3,4})$"), _rohm_mcr),
]

_KOA = re.compile(r"^(RK73B|RN73\d?)(1E|1J|2A|2B)\w*?TD(\d{3,4})([FJ])")
_KOA_RN = re.compile(r"^(RN73)2?(1E|1J|2A|2B)TTD(\d{4})([B-F])")


def decode_mpn(mpn):
    """Attributes from a part number, or None if the series is not known."""
    if not mpn:
        return None
    p = mpn.strip().upper()
    for rx, fn in _DECODERS:
        m = rx.match(p)
        if m:
            return fn([p] + list(m.groups()))   # [0] = full part number (suffixes)
    m = _KOA_RN.match(p.replace("RN732", "RN73"))
    if m:
        return _res({"1E": "0402", "1J": "0603", "2A": "0805", "2B": "1206"}[m[2]],
                    code3(m[3]), _RES_TOL.get(m[4]), tech="thin", series="KOA RN73")
    m = _KOA.match(p)
    if m:
        return _koa([m.group(0), "RK73B", m[2], m[3], m[4]])
    return None


_DESC_TOL = re.compile(r"(?:±|\+/-|\+-)?\s*(\d+(?:\.\d+)?)\s*%")
_DESC_V = re.compile(r"(\d+(?:\.\d+)?)\s*V(?:DC)?\b", re.I)
_DESC_W = re.compile(r"(\d+(?:\.\d+)?)\s*(m?W)\b|1/(\d+)\s*W", re.I)
_DESC_PKG = re.compile(r"\b(0201|0402|0603|0805|1206|1210|1812|1225)\b")
_DESC_CAP = re.compile(r"(\d+(?:\.\d+)?)\s*([pnuµμ])F", re.I)
_DESC_RES = re.compile(r"(\d+(?:\.\d+)?)\s*([kKM]?)\s*(?:Ω|ohms?\b|R\b)|(\d+[kKMR]\d*)\b(?=\s*(?:0\.|\d|5%|1%|±|$))", re.I)


def decode_description(text):
    """Best-effort attributes from a free-text description (never overrides
    a decoded part number - it fills gaps and cross-checks)."""
    if not text:
        return {}
    t = text.replace("Ω", "Ω ")
    out = {}
    m = _DESC_PKG.search(t)
    if m:
        out["package"] = m.group(1)
    for d in DIELECTRIC_RANK:
        if re.search(r"\b" + d + r"\b", t, re.I) or (d == "C0G" and re.search(r"\bNP0\b|\bNPO\b", t, re.I)):
            out["dielectric"] = d
            break
    m = _DESC_TOL.search(t)
    if m:
        out["tolerance"] = float(m.group(1))
    m = _DESC_CAP.search(t)
    if m:
        out["type"] = "C"
        out["value"] = float(m.group(1)) * {"p": 1e-12, "n": 1e-9, "u": 1e-6,
                                            "µ": 1e-6, "μ": 1e-6}[m.group(2).lower() if m.group(2) != "µ" else "µ"]
        mv = _DESC_V.search(t)
        if mv:
            out["voltage"] = float(mv.group(1))
    elif re.search(r"resist|\bRES\b|Ω|ohm", t, re.I):
        out["type"] = "R"
        m = re.search(r"(\d+(?:\.\d+)?)\s*([kKM]?)\s*(?:Ω|ohm)", t, re.I) or \
            re.search(r"\b(\d+(?:\.\d+)?)\s*([kKM])\b", t)
        if m:
            out["value"] = float(m.group(1)) * {"": 1, "k": 1e3, "K": 1e3, "M": 1e6}[m.group(2)]
        mw = _DESC_W.search(t)
        if mw:
            if mw.group(3):
                out["power"] = 1 / float(mw.group(3))
            else:
                out["power"] = float(mw.group(1)) / (1000 if mw.group(2).lower() == "mw" else 1)
    if re.search(r"AEC-?Q200|automotive", t, re.I):
        out["aec"] = True
    return out


def fmt_value(kind, v):
    if v is None:
        return "?"
    if kind == "C":
        for unit, s in (("uF", 1e-6), ("nF", 1e-9), ("pF", 1e-12)):
            if v >= s * 0.999:
                return f"{v / s:g}{unit}"
        return f"{v:g}F"
    for unit, s in (("M", 1e6), ("k", 1e3)):
        if v >= s:
            return f"{v / s:g}{unit}"
    return f"{v:g}R"
