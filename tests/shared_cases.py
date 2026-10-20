"""Edge-case tests that run against any Serato reader that offers the small adapter interface below (adapter.py in this folder is the
adapter for serato_reader).

Each case is (name, function(R, T)): it raises AssertionError when the reader gets something wrong. R is the adapter, T makes temporary
folders. Everything is synthetic: files are written by fixtures_writer.py, or are the real Serato DJ Lite 5.0 tag bytes in
fixtures/real_serato_5_tags.json."""
import hashlib
import json
import os
import pathlib
import random
import struct
import time
import unicodedata

import fixtures_writer as W

HERE = pathlib.Path(__file__).resolve().parent
CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def add(name, fn):
    CASES.append((name, fn))


def eq(a, b, msg=""):
    assert a == b, "%s: %r != %r" % (msg, a, b)


def near(a, b, tol, msg=""):
    assert abs(a - b) <= tol, "%s: %r vs %r (tolerance %r)" % (msg, a, b, tol)


def real():
    return json.loads((HERE / "fixtures" / "real_serato_5_tags.json").read_text())


def tree_hash(root):
    out = {}
    for p in sorted(pathlib.Path(root).rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p)] = (hashlib.sha1(p.read_bytes()).hexdigest(), st.st_mtime_ns)
    return out


# ================================================================== A. the tag-length-value format
def _two_track_db():
    return W.database([dict(pfil="Music/a.mp3", tsng="A", uadd=1700000000, tbpm="124.00"),
                       dict(pfil="Music/b.mp3", tsng="B", uadd=1700000001, tbpm="128.00")])


@case("tlv: fields of every type")
def _(R, T):
    data = W.tlv("vrsn", "2.0/Test") + W.tlv("otrk", [("pfil", "a/b.mp3"), ("uadd", 5), ("bbgl", True), ("sbit", 7)])
    p = R.tlv_parse(data)
    eq(p[0], ("vrsn", "2.0/Test"))
    eq(p[1][0], "otrk")
    d = dict(p[1][1])
    eq((d["pfil"], d["uadd"], d["bbgl"], d["sbit"]), ("a/b.mp3", 5, True, 7))


@case("tlv: empty input")
def _(R, T):
    eq(R.tlv_parse(b""), [])


@case("tlv: header only, length missing")
def _(R, T):
    eq(R.tlv_parse(b"vrsn"), [])


@case("tlv: length longer than the data is cut, not crashed")
def _(R, T):
    p = R.tlv_parse(b"tsng" + struct.pack(">I", 999) + "Hi".encode("utf-16-be"))
    eq(p, [("tsng", "Hi")])


@case("tlv: zero-length text and number fields")
def _(R, T):
    p = R.tlv_parse(b"tsng" + struct.pack(">I", 0) + b"uadd" + struct.pack(">I", 0))
    eq(p, [("tsng", ""), ("uadd", 0)])


@case("tlv: unknown tag type letters stay bytes")
def _(R, T):
    p = R.tlv_parse(b"zzzz" + struct.pack(">I", 3) + b"abc")
    eq(p, [("zzzz", b"abc")])


@case("tlv: emoji (surrogate pair) text")
def _(R, T):
    p = R.tlv_parse(W.tlv("tsng", "Night \U0001F319 Mix"))
    eq(p[0][1], "Night \U0001F319 Mix")


@case("tlv: u32 field of the wrong width is still a number")
def _(R, T):
    p = R.tlv_parse(b"uadd" + struct.pack(">I", 2) + b"\x01\x00")
    eq(p[0][1], 256)


def _cut_case(cut):
    def fn(R, T):
        data = _two_track_db()
        out = R.tlv_parse(data[:cut])
        assert isinstance(out, list)
    return fn


_N = len(_two_track_db())
for _c in range(0, _N, max(1, _N // 36)):
    add("tlv: database cut at byte %d never crashes" % _c, _cut_case(_c))


def _random_case(seed):
    def fn(R, T):
        rnd = random.Random(seed)
        data = bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 300)))
        assert isinstance(R.tlv_parse(data), list)
    return fn


for _s in range(24):
    add("tlv: random bytes #%d never crash" % _s, _random_case(_s))


# ================================================================== B. libraries, crates, smart crates, drives
def _lib(T, tracks=(), crates=None, smarts=None, root="/"):
    d = T.mk()
    f = W.make_library(d, tracks, crates, smarts)
    return R_load(f, root)


R_load = None


def _setup(R):
    global R_load
    R_load = R.load


@case("library: empty database has no tracks")
def _(R, T):
    _setup(R)
    lib = _lib(T)
    eq(len(lib["tracks"]), 0)
    eq(lib["crates"], {})


@case("library: a folder with no files at all")
def _(R, T):
    d = T.mk() / "_Serato_"
    d.mkdir()
    lib = R.load(d, "/")
    eq((len(lib["tracks"]), lib["crates"], lib["smart"]), (0, {}, {}))


@case("library: folder that does not exist does not crash")
def _(R, T):
    lib = R.load(T.mk() / "nothing" / "_Serato_", "/")
    eq(len(lib["tracks"]), 0)


NOON_A = 1699963200          # 2023-11-14 12:00 UTC: the same calendar day in every time zone
NOON_B = 1650024000          # 2022-04-15 12:00 UTC


@case("library: track fields")
def _(R, T):
    _setup(R)
    lib = _lib(T, [dict(pfil="Users/dj/Music/x.mp3", tsng="Title", tart="Artist", talb="Album", tgen="House", tbpm="124.50", tkey="Am",
                        uadd=NOON_A, tlen="05:23.45")])
    t = lib["tracks"][0]
    eq((t["path"], t["title"], t["artist"], t["album"], t["genre"], t["bpm"], t["key"]),
       ("/Users/dj/Music/x.mp3", "Title", "Artist", "Album", "House", 124.5, "Am"))
    eq(t["date_added"], "2023-11-14")


@case("library: Date Added falls back from uadd to tadd")
def _(R, T):
    lib = _lib(T, [dict(pfil="a.mp3", tadd=str(NOON_B)), dict(pfil="b.mp3", uadd=0, tadd=str(NOON_B)), dict(pfil="c.mp3")])
    eq([t["date_added"] for t in lib["tracks"]], ["2022-04-15", "2022-04-15", None])


@case("library: uadd wins over tadd when both are there")
def _(R, T):
    lib = _lib(T, [dict(pfil="a.mp3", uadd=NOON_A, tadd=str(NOON_B))])
    eq(lib["tracks"][0]["date_added"], "2023-11-14")


@case("library: Date Added as number text, junk and negative")
def _(R, T):
    lib = _lib(T, [dict(pfil="a.mp3", tadd="abc"), dict(pfil="b.mp3", tadd="-5"), dict(pfil="c.mp3", tadd="%d.0" % NOON_A)])
    eq([t["date_added"] for t in lib["tracks"]], [None, None, "2023-11-14"])


@case("library: a track with no path is skipped")
def _(R, T):
    lib = _lib(T, [dict(tsng="no path"), dict(pfil="ok.mp3")])
    eq(len(lib["tracks"]), 1)


@case("library: duplicate paths are both kept (the importer decides)")
def _(R, T):
    lib = _lib(T, [dict(pfil="a.mp3"), dict(pfil="a.mp3")])
    eq(len(lib["tracks"]), 2)


@case("library: paths are relative to the drive they are on")
def _(R, T):
    lib = _lib(T, [dict(pfil="Music/a.mp3")], root="/Volumes/PARTY")
    eq(lib["tracks"][0]["path"], "/Volumes/PARTY/Music/a.mp3")


@case("library: an absolute path stays as it is")
def _(R, T):
    lib = _lib(T, [dict(pfil="/abs/a.mp3")], root="/Volumes/PARTY")
    eq(lib["tracks"][0]["path"], "/abs/a.mp3")


_NAMES = {
    "accents": "Café del Mar – Señor",
    "nfd": unicodedata.normalize("NFD", "Café"),
    "cjk": "夜のドライブ 中文",
    "emoji": "Fire \U0001F525\U0001F525 Set",
    "arabic": "موسيقى",
    "cyrillic": "Москва ночью",
    "hash_percent": "100% #1 hit?.mp3",
    "quotes": "\"Quoted\" 'name', & more;.mp3",
    "long": "x" * 240,
    "spaces": "  leading and trailing  ",
    "greek": "αβγ Ω",
    "combining": "é̂ trés",
}


def _name_case(label, name):
    def fn(R, T):
        lib = _lib(T, [dict(pfil="Music/%s.mp3" % name, tsng=name, tart=name)])
        t = lib["tracks"][0]
        eq(t["title"], name)
        eq(t["artist"], name)
        eq(t["path"], "/Music/%s.mp3" % name)
    return fn


for _k, _v in _NAMES.items():
    add("library: unicode and odd characters in track data (%s)" % _k, _name_case(_k, _v))


def _crate_name_case(label, name):
    def fn(R, T):
        lib = _lib(T, [dict(pfil="a.mp3")], crates={name: ["a.mp3"]})
        names = list(lib["crates"].keys())
        eq(len(names), 1)
        eq(unicodedata.normalize("NFC", names[0]), unicodedata.normalize("NFC", name.replace("%%", "%%")))
        eq(lib["crates"][names[0]], ["/a.mp3"])
    return fn


for _k in ("accents", "nfd", "cjk", "emoji", "arabic", "cyrillic", "greek", "combining"):
    add("crates: unicode crate name (%s)" % _k, _crate_name_case(_k, _NAMES[_k]))


@case("crates: names are NFC-normalised so macOS and Windows spellings match")
def _(R, T):
    lib = _lib(T, crates={unicodedata.normalize("NFD", "Café"): []})
    eq(list(lib["crates"].keys()), ["Café"])


@case("crates: an empty crate is kept, with no tracks")
def _(R, T):
    lib = _lib(T, crates={"Empty": []})
    eq(lib["crates"], {"Empty": []})


@case("crates: tracks stay in Serato's order")
def _(R, T):
    paths = ["z.mp3", "a.mp3", "m.mp3", "b.mp3"]
    lib = _lib(T, crates={"Order": paths})
    eq(lib["crates"]["Order"], ["/" + p for p in paths])


@case("crates: a crate may list a file that is not in the database")
def _(R, T):
    lib = _lib(T, [dict(pfil="a.mp3")], crates={"C": ["a.mp3", "ghost.mp3"]})
    eq(lib["crates"]["C"], ["/a.mp3", "/ghost.mp3"])


@case("crates: nested crates use %% in the file name")
def _(R, T):
    lib = _lib(T, crates={"Club%%Peak%%Warm": ["a.mp3"], "Club%%Peak": ["b.mp3"], "Club": []})
    eq(sorted(lib["crates"]), ["Club", "Club%%Peak", "Club%%Peak%%Warm"])


@case("crates: a child crate whose parent has no file of its own")
def _(R, T):
    lib = _lib(T, crates={"Parent%%Child": ["a.mp3"]})
    eq(list(lib["crates"]), ["Parent%%Child"])


@case("crates: five levels deep")
def _(R, T):
    name = "%%".join("L%d" % i for i in range(5))
    lib = _lib(T, crates={name: ["a.mp3"]})
    eq(list(lib["crates"]), [name])


@case("crates: the same track in two crates")
def _(R, T):
    lib = _lib(T, crates={"One": ["a.mp3"], "Two": ["a.mp3"]})
    eq(lib["crates"]["One"], lib["crates"]["Two"])


@case("crates: 5,000 tracks in one crate")
def _(R, T):
    lib = _lib(T, crates={"Big": ["Music/%05d.mp3" % i for i in range(5000)]})
    eq(len(lib["crates"]["Big"]), 5000)
    eq(lib["crates"]["Big"][4999], "/Music/04999.mp3")


@case("crates: a damaged crate does not stop the others")
def _(R, T):
    d = T.mk()
    f = W.make_library(d, crates={"Good": ["a.mp3"], "Bad": ["b.mp3"]})
    p = f / "Subcrates" / "Bad.crate"
    p.write_bytes(p.read_bytes()[:25])
    lib = R.load(f, "/")
    eq(lib["crates"]["Good"], ["/a.mp3"])
    assert "Bad" in lib["crates"]


@case("crates: a zero-byte crate file")
def _(R, T):
    d = T.mk()
    f = W.make_library(d, crates={"Good": ["a.mp3"]})
    (f / "Subcrates" / "Zero.crate").write_bytes(b"")
    lib = R.load(f, "/")
    eq(lib["crates"].get("Zero", []), [])
    eq(lib["crates"]["Good"], ["/a.mp3"])


@case("crates: macOS '._' resource-fork files are ignored")
def _(R, T):
    d = T.mk()
    f = W.make_library(d, crates={"Good": ["a.mp3"]})
    (f / "Subcrates" / "._Good.crate").write_bytes(b"\0\0\0\0junk")
    lib = R.load(f, "/")
    eq(list(lib["crates"]), ["Good"])


@case("crates: other files in Subcrates are ignored")
def _(R, T):
    d = T.mk()
    f = W.make_library(d, crates={"Good": ["a.mp3"]})
    (f / "Subcrates" / "notes.txt").write_text("hi")
    (f / "Subcrates" / "Good.crate.bak").write_bytes(b"x")
    eq(list(R.load(f, "/")["crates"]), ["Good"])


@case("crates: a crate on a drive uses that drive's paths")
def _(R, T):
    d = T.mk()
    f = W.make_library(d, crates={"Set": ["DJ/a.mp3"]})
    eq(R.load(f, "/Volumes/USB")["crates"]["Set"], ["/Volumes/USB/DJ/a.mp3"])


# ---- smart crates
_COND = {
    "cond_con_str": ("contains", "text"), "cond_dnc_str": ("not_contains", "text"), "cond_is_str": ("is", "text"),
    "cond_isn_str": ("is_not", "text"), "cond_greq_uint": (">=", "num"), "cond_lseq_uint": ("<=", "num"), "cond_is_uint": ("=", "num"),
    "cond_isn_uint": ("!=", "num"),
}


def _smart_case(cond, kind):
    def fn(R, T):
        v = 128 if kind == "num" else "house"
        field = 15 if kind == "num" else 9
        lib = _lib(T, smarts={"S": W.smart_crate([(field, cond, v)])})
        s = lib["smart"]["S"]
        eq(len(s["rules"]), 1)
        r = s["rules"][0]
        eq((r["field_id"], r["cond"]), (field, cond))
        eq(r["number"] if kind == "num" else r["text"], v)
    return fn


for _cond, (_op, _kind) in _COND.items():
    add("smart crate: condition %s" % _cond, _smart_case(_cond, _kind))


@case("smart crate: match all and live flags")
def _(R, T):
    lib = _lib(T, smarts={"All": W.smart_crate([(9, "cond_con_str", "x")], True, True), "Any": W.smart_crate([(9, "cond_con_str", "x")], False, False)})
    eq((lib["smart"]["All"]["match_all"], lib["smart"]["All"]["live"]), (True, True))
    eq((lib["smart"]["Any"]["match_all"], lib["smart"]["Any"]["live"]), (False, False))


@case("smart crate: several rules keep their order")
def _(R, T):
    lib = _lib(T, smarts={"S": W.smart_crate([(7, "cond_is_str", "a"), (15, "cond_greq_uint", 120), (9, "cond_con_str", "tech")])})
    eq([r["field_id"] for r in lib["smart"]["S"]["rules"]], [7, 15, 9])


@case("smart crate: no rules")
def _(R, T):
    lib = _lib(T, smarts={"S": W.smart_crate([])})
    eq(lib["smart"]["S"]["rules"], [])


@case("smart crate: unicode rule text")
def _(R, T):
    lib = _lib(T, smarts={"S": W.smart_crate([(7, "cond_con_str", "夜\U0001F319")])})
    eq(lib["smart"]["S"]["rules"][0]["text"], "夜\U0001F319")


@case("smart crate: a damaged one does not stop the rest")
def _(R, T):
    d = T.mk()
    f = W.make_library(d, smarts={"Good": W.smart_crate([(9, "cond_is_str", "x")]), "Bad": W.smart_crate([(9, "cond_is_str", "x")])})
    p = f / "SmartCrates" / "Bad.scrate"
    p.write_bytes(p.read_bytes()[:20])
    lib = R.load(f, "/")
    eq(len(lib["smart"]["Good"]["rules"]), 1)


@case("smart crate: unicode name")
def _(R, T):
    lib = _lib(T, smarts={"夜 \U0001F319": W.smart_crate([(9, "cond_is_str", "x")])})
    eq(list(lib["smart"]), ["夜 \U0001F319"])


# ---- drives
@case("drives: the Mac's own library plus every drive with a _Serato_ folder")
def _(R, T):
    root = T.mk()
    home = W.make_library(root / "home" if (root / "home").mkdir() is None else root, [dict(pfil="a.mp3")])
    vols = root / "Volumes"
    vols.mkdir()
    for n in ("USB1", "USB2", "USB3"):
        (vols / n).mkdir()
        W.make_library(vols / n, [dict(pfil="Music/%s.mp3" % n)])
    found = R.find(home, vols)
    eq(len(found), 4)
    eq(found[0][1], "/")
    eq(sorted(r for _f, r in found[1:]), sorted(str(vols / n) for n in ("USB1", "USB2", "USB3")))


@case("drives: a drive with only crates (no database) counts")
def _(R, T):
    root = T.mk()
    vols = root / "V"
    (vols / "D").mkdir(parents=True)
    f = W.make_library(vols / "D", crates={"C": ["x.mp3"]})
    (f / "database V2").unlink()
    found = R.find(root / "none", vols)
    eq(len(found), 1)


@case("drives: a drive without a _Serato_ folder is skipped")
def _(R, T):
    root = T.mk()
    vols = root / "V"
    (vols / "Plain").mkdir(parents=True)
    eq(R.find(root / "none", vols), [])


@case("drives: a stray file in the volumes folder is skipped")
def _(R, T):
    root = T.mk()
    vols = root / "V"
    vols.mkdir()
    (vols / "file.txt").write_text("x")
    eq(R.find(root / "none", vols), [])


@case("drives: no volumes folder at all")
def _(R, T):
    root = T.mk()
    f = W.make_library(root, [dict(pfil="a.mp3")])
    eq(len(R.find(f, root / "does-not-exist")), 1)


@case("drives: the same crate name on two drives stays separate")
def _(R, T):
    root = T.mk()
    vols = root / "V"
    for n in ("A", "B"):
        (vols / n).mkdir(parents=True)
        W.make_library(vols / n, crates={"Set": ["Music/%s.mp3" % n]})
    libs = [R.load(f, r) for f, r in R.find(root / "none", vols)]
    eq(sorted(l["crates"]["Set"][0] for l in libs), [str(vols / "A") + "/Music/A.mp3", str(vols / "B") + "/Music/B.mp3"])


@case("drives: a drive name with spaces and unicode")
def _(R, T):
    root = T.mk()
    vols = root / "V"
    name = "DJ été Drive \U0001F3A7"
    (vols / name).mkdir(parents=True)
    W.make_library(vols / name, [dict(pfil="Music/a.mp3")])
    (f, r), = R.find(root / "none", vols)
    eq(R.load(f, r)["tracks"][0]["path"], str(vols / name) + "/Music/a.mp3")


@case("drives: the Mac's folder is not listed twice when it is also under the volumes folder")
def _(R, T):
    root = T.mk()
    vols = root / "V"
    (vols / "Mac").mkdir(parents=True)
    f = W.make_library(vols / "Mac", [dict(pfil="a.mp3")])
    eq(len(R.find(f, vols)), 1)


# ---- a big database
@case("library: 20,000 tracks parse in a few seconds")
def _(R, T):
    tracks = [dict(pfil="Users/dj/Music/%05d.mp3" % i, tsng="T%d" % i, tart="A", tbpm="124.00", tkey="Am", uadd=1700000000 + i, tlen="03:30.00",
                   tsiz="8.1MB") for i in range(20000)]
    d = T.mk()
    f = W.make_library(d, tracks, crates={"All": ["Users/dj/Music/%05d.mp3" % i for i in range(20000)]})
    t0 = time.time()
    lib = R.load(f, "/")
    dt = time.time() - t0
    eq(len(lib["tracks"]), 20000)
    eq(len(lib["crates"]["All"]), 20000)
    assert dt < 20, "took %.1f s" % dt


# ================================================================== C. tags in every container
CUES = [(0, 17343, 0xCC0000, "Intro"), (1, 26900, 0xCC8800, "Drop ★"), (2, 7786, 0x0000CC, ""), (3, 33929, 0xCCCC00, "夜")]
LOOPS = [(0, 8000, 12000, 0x27AAE1, "Loop A", False), (1, 20000, 21000, 0x27AAE1, "", True)]


def _tag_file(T, kind, geobs):
    d = T.mk()
    p = d / ("t." + {"mp3": "mp3", "mp3v23": "mp3", "wav": "wav", "aiff": "aiff", "m4a": "m4a", "flac": "flac", "ogg": "ogg"}[kind])
    if kind == "mp3":
        W.make_mp3(p, W.id3v24(geobs))
    elif kind == "mp3v23":
        W.make_mp3(p, W.id3v23(geobs))
    elif kind == "wav":
        W.make_wav(p, tag=W.id3v24(geobs))
    elif kind == "aiff":
        W.make_aiff(p, tag=W.id3v24(geobs))
    else:
        names = {"Serato Markers2": "markersv2", "Serato Markers_": "markers", "Serato BeatGrid": "beatgrid", "Serato Autotags": "autgain",
                 "Serato Tempo Map": "tempomap"}
        if kind == "m4a":
            W.make_m4a(p, {names[k]: v for k, v in geobs.items()})
        elif kind == "flac":
            fl = {"Serato Markers2": "SERATO_MARKERS_V2", "Serato Markers_": "SERATO_MARKERS", "Serato BeatGrid": "SERATO_BEATGRID",
                  "Serato Autotags": "SERATO_AUTOGAIN", "Serato Tempo Map": "SERATO_TEMPOMAP"}
            W.make_flac(p, {fl[k]: W.wrapped(k, v) for k, v in geobs.items()})
        else:
            ol = {"Serato Markers2": "serato_markers2", "Serato BeatGrid": "serato_beatgrid", "Serato Autotags": "serato_autotags"}
            W.make_ogg(p, {ol[k]: W.wrapped(k, v) for k, v in geobs.items() if k in ol})
    return p


_KINDS = ["mp3", "mp3v23", "wav", "aiff", "m4a", "flac", "ogg"]


def _container_cases(kind):
    def cues(R, T):
        p = _tag_file(T, kind, {"Serato Markers2": W.markers2(CUES, LOOPS, 0x112233)})
        t = R.read_tags(p)
        eq([(c["index"], c["ms"], c["rgb"], c["name"]) for c in t["cues"]], CUES)

    def loops(R, T):
        p = _tag_file(T, kind, {"Serato Markers2": W.markers2(CUES, LOOPS, 0x112233)})
        t = R.read_tags(p)
        eq([(l["index"], l["start"], l["end"], l["name"], l["locked"]) for l in t["loops"]], [(0, 8000, 12000, "Loop A", False), (1, 20000, 21000, "", True)])

    def color(R, T):
        p = _tag_file(T, kind, {"Serato Markers2": W.markers2(CUES, LOOPS, 0x112233, bpm_lock=True)})
        t = R.read_tags(p)
        eq((t["color"], t["bpm_lock"]), (0x112233, True))

    def grid(R, T):
        p = _tag_file(T, kind, {"Serato BeatGrid": W.beatgrid([(0.5, 8), (4.5, 120.0)]), "Serato Autotags": W.autotags(124.0)})
        t = R.read_tags(p)
        eq(len(t["grid"]), 2)
        near(t["grid"][0]["pos"], 0.5, 1e-6)
        eq(t["grid"][0]["beats"], 8)
        near(t["grid"][1]["bpm"], 120.0, 1e-3)
        near(t["autotags"]["bpm"], 124.0, 1e-6)

    def tempo(R, T):
        p = _tag_file(T, kind, {"Serato Tempo Map": W.tempo_map(1.9669, 124.0017)})
        t = R.read_tags(p)
        eq(len(t["grid"]), 1)
        near(t["grid"][0]["pos"], 1.9669, 1e-9)
        near(t["grid"][0]["bpm"], 124.0017, 1e-9)

    def nothing(R, T):
        p = _tag_file(T, kind, {})
        t = R.read_tags(p)
        eq((t["cues"], t["loops"], t["grid"]), ([], [], []))

    return [("cues", cues), ("loops and their names", loops), ("track colour and BPM lock", color), ("BeatGrid and Autotags", grid),
            ("Tempo Map", tempo), ("a file with Serato tags absent", nothing)]


for _k in _KINDS:
    for _n, _f in _container_cases(_k):
        if _k == "ogg" and _n in ("Tempo Map",):
            continue
        add("tags %s: %s" % (_k, _n), _f)


@case("tags: Markers_ alone gives the cues and loops")
def _(R, T):
    p = _tag_file(T, "mp3", {"Serato Markers_": W.markers_legacy([(0, 1000, 0xCC0000), (2, 5000, 0x00CC00)], [(0, 2000, 3000, 0x27AAE1)], 0xFFFFFF)})
    t = R.read_tags(p)
    eq([(c["index"], c["ms"], c["rgb"]) for c in t["cues"]], [(0, 1000, 0xCC0000), (2, 5000, 0x00CC00)])
    eq([(l["start"], l["end"]) for l in t["loops"]], [(2000, 3000)])


@case("tags: Markers_ wins over Markers2 where they disagree, and the disagreement is counted")
def _(R, T):
    p = _tag_file(T, "mp3", {"Serato Markers2": W.markers2([(0, 1000, 0xCC0000, "Name")]), "Serato Markers_": W.markers_legacy([(0, 1500, 0xCC0000)])})
    t = R.read_tags(p)
    eq((t["cues"][0]["ms"], t["cues"][0]["name"], t["conflicts"]), (1500, "Name", 1))


@case("tags: Markers_ and Markers2 that agree give no conflict")
def _(R, T):
    p = _tag_file(T, "mp3", {"Serato Markers2": W.markers2([(0, 1000, 0xCC0000, "N")]), "Serato Markers_": W.markers_legacy([(0, 1000, 0xCC0000)])})
    eq(R.read_tags(p)["conflicts"], 0)


@case("tags: twenty cues and a cue at the 32-bit limit")
def _(R, T):
    cues = [(i, 1000 * i + 7, 0xCC0000, "c%d" % i) for i in range(20)] + [(20, 2 ** 32 - 1, 0x00CC00, "end")]
    p = _tag_file(T, "mp3", {"Serato Markers2": W.markers2(cues)})
    eq([(c["index"], c["ms"]) for c in R.read_tags(p)["cues"]], [(c[0], c[1]) for c in cues])


@case("tags: a cue name of 200 bytes")
def _(R, T):
    p = _tag_file(T, "mp3", {"Serato Markers2": W.markers2([(0, 5, 0xCC0000, "n" * 200)])})
    eq(R.read_tags(p)["cues"][0]["name"], "n" * 200)


@case("tags: saved flips are counted")
def _(R, T):
    p = _tag_file(T, "mp3", {"Serato Markers2": W.markers2(flips=3)})
    eq(R.read_tags(p)["flips"], 3)


@case("tags: a loop that ends before it starts is returned as stored")
def _(R, T):
    p = _tag_file(T, "mp3", {"Serato Markers2": W.markers2(loops=[(0, 9000, 8000, 0x27AAE1, "", False)])})
    t = R.read_tags(p)
    assert len(t["loops"]) == 1 and t["loops"][0]["start"] == 9000


def _color_case(rgb):
    def fn(R, T):
        p = _tag_file(T, "mp3", {"Serato Markers_": W.markers_legacy([(0, 1234, rgb)], [], rgb)})
        t = R.read_tags(p)
        eq((t["cues"][0]["rgb"], t["color"]), (rgb, rgb))
    return fn


for _rgb in (0x000000, 0xFFFFFF, 0xCC0000, 0xCC4400, 0xCC8800, 0xCCCC00, 0x88CC00, 0x44CC00, 0x00CC00, 0x00CC44, 0x00CC88, 0x00CCCC, 0x0088CC,
             0x0044CC, 0x0000CC, 0x4400CC, 0x8800CC, 0xCC00CC, 0xCC0088, 0xCC0044, 0x27AAE1, 0x7F7F7F, 0x010101, 0xFEFEFE):
    add("tags: colour %06X survives the 7-bit Markers_ encoding" % _rgb, _color_case(_rgb))


# ---- damaged tags
_GOOD = W.markers2(CUES, LOOPS, 0x112233)


def _cut_markers2(cut):
    def fn(R, T):
        t = R.parse_markers2(_GOOD[:cut])
        assert isinstance(t["cues"], list)
    return fn


for _c in range(0, 470, 20):
    add("damaged: Markers2 cut at %d bytes" % _c, _cut_markers2(_c))


def _cut_mp3(cut):
    def fn(R, T):
        d = T.mk()
        tag = W.id3v24({"Serato Markers2": _GOOD, "Serato BeatGrid": W.beatgrid([(0.5, 120.0)])})
        W.make_mp3(d / "t.mp3", tag[:cut])
        R.read_tags(d / "t.mp3")
    return fn


for _c in range(0, 900, 45):
    add("damaged: ID3 tag cut at %d bytes" % _c, _cut_mp3(_c))


def _cut_m4a(cut):
    def fn(R, T):
        d = T.mk()
        W.make_m4a(d / "t.m4a", {"markersv2": _GOOD, "beatgrid": W.beatgrid([(0.5, 120.0)])})
        data = (d / "t.m4a").read_bytes()
        (d / "t.m4a").write_bytes(data[:max(cut, 0)])
        R.read_tags(d / "t.m4a")
    return fn


for _c in (0, 4, 8, 16, 40, 80, 120, 200, 300, 500, 700):
    add("damaged: M4A cut at %d bytes" % _c, _cut_m4a(_c))


def _cut_flac(cut):
    def fn(R, T):
        d = T.mk()
        W.make_flac(d / "t.flac", {"SERATO_MARKERS_V2": W.wrapped("Serato Markers2", _GOOD)})
        data = (d / "t.flac").read_bytes()
        (d / "t.flac").write_bytes(data[:cut])
        R.read_tags(d / "t.flac")
    return fn


for _c in (0, 3, 4, 8, 20, 60, 100, 200, 400):
    add("damaged: FLAC cut at %d bytes" % _c, _cut_flac(_c))


@case("damaged: a zero-byte MP3")
def _(R, T):
    p = T.mk() / "z.mp3"
    p.write_bytes(b"")
    eq(R.read_tags(p)["cues"], [])


@case("damaged: a zero-byte M4A, FLAC, WAV, AIFF and OGG")
def _(R, T):
    d = T.mk()
    for e in ("m4a", "flac", "wav", "aiff", "ogg"):
        (d / ("z." + e)).write_bytes(b"")
        eq(R.read_tags(d / ("z." + e))["cues"], [])


@case("damaged: a text file named .mp3")
def _(R, T):
    p = T.mk() / "x.mp3"
    p.write_text("this is not audio" * 100)
    eq(R.read_tags(p)["cues"], [])


@case("damaged: an unsupported extension gives an empty result")
def _(R, T):
    p = T.mk() / "x.xyz"
    p.write_bytes(b"ID3" + b"\0" * 50)
    assert isinstance(R.read_tags(p)["cues"], list)


@case("damaged: a file that is not there raises a plain OSError")
def _(R, T):
    try:
        R.read_tags(T.mk() / "missing.mp3")
    except OSError:
        return
    raise AssertionError("no error")


@case("damaged: ID3 tag claiming more bytes than the file has")
def _(R, T):
    p = T.mk() / "x.mp3"
    p.write_bytes(b"ID3\x04\x00\x00" + bytes([0x7F, 0x7F, 0x7F, 0x7F]) + b"GEOB" + b"\0" * 20)
    eq(R.read_tags(p)["cues"], [])


@case("damaged: a GEOB frame with a wrong text-encoding byte")
def _(R, T):
    body = b"\x09" + b"application/octet-stream\0" + b"\0" + b"Serato Markers2\0" + _GOOD
    frame = b"GEOB" + bytes([0, 0, (len(body) >> 7) & 0x7F, len(body) & 0x7F]) + b"\0\0" + body
    p = T.mk() / "x.mp3"
    W.make_mp3(p, b"ID3\x04\x00\x00" + bytes([0, 0, (len(frame) >> 7) & 0x7F, len(frame) & 0x7F]) + frame)
    assert isinstance(R.read_tags(p)["cues"], list)


@case("damaged: bad base64 in an M4A tag")
def _(R, T):
    p = T.mk() / "x.m4a"
    W.make_m4a(p, extra_ilst=W._freeform("markersv2", b"!!!not base64!!!"))
    eq(R.read_tags(p)["cues"], [])


@case("damaged: bad base64 in a FLAC comment")
def _(R, T):
    p = T.mk() / "x.flac"
    W.make_flac(p, {"SERATO_MARKERS_V2": b"@@@@"})
    eq(R.read_tags(p)["cues"], [])


@case("damaged: Markers2 payload that is not base64 text")
def _(R, T):
    t = R.parse_markers2(b"\x01\x01" + bytes(range(1, 200)))
    eq(t["cues"], [])


@case("damaged: a CUE entry shorter than it should be")
def _(R, T):
    body = b"\x01\x01" + b"CUE\0" + struct.pack(">I", 3) + b"\0\0\0" + b"\0"
    payload = b"\x01\x01" + W._b64_lines(body)
    eq(R.parse_markers2(payload)["cues"], [])


@case("damaged: Markers_ with an impossible entry count")
def _(R, T):
    eq(R.parse_markers_legacy(b"\x02\x05" + struct.pack(">I", 9999) + b"\0" * 40)["cues"], [])


@case("damaged: Markers_ shorter than its header")
def _(R, T):
    eq(R.parse_markers_legacy(b"\x02\x05\0")["cues"], [])


# ---- the real Serato DJ Lite 5.0 files
@case("real Serato 5: the four hot cues in the AIFF, to the millisecond")
def _(R, T):
    r = real()["aiff"]
    t = R.parse_markers2(bytes.fromhex(r["Serato Markers2"]))
    eq([(c["index"], c["ms"]) for c in t["cues"]], [(0, 17343), (1, 26900), (2, 7786), (3, 33929)])


@case("real Serato 5: the cue colours")
def _(R, T):
    t = R.parse_markers2(bytes.fromhex(real()["aiff"]["Serato Markers2"]))
    eq([c["rgb"] for c in t["cues"]], [0xCC0000, 0xCC8800, 0x0000CC, 0xCCCC00])


@case("real Serato 5: Markers_ agrees with Markers2")
def _(R, T):
    r = real()["aiff"]
    legacy = R.parse_markers_legacy(bytes.fromhex(r["Serato Markers_"]))
    eq([(c["index"], c["ms"], c["rgb"]) for c in legacy["cues"]], [(0, 17343, 0xCC0000), (1, 26900, 0xCC8800), (2, 7786, 0x0000CC), (3, 33929, 0xCCCC00)])


@case("real Serato 5: the track colour and BPM lock")
def _(R, T):
    t = R.parse_markers2(bytes.fromhex(real()["aiff"]["Serato Markers2"]))
    eq((t["color"], t["bpm_lock"]), (0xFFFFFF, False))


@case("real Serato 5: Autotags")
def _(R, T):
    a = R.parse_autotags(bytes.fromhex(real()["aiff"]["Serato Autotags"]))
    near(a["bpm"], 124.0, 1e-9)
    near(a["autogain"], 19.846, 1e-9)


@case("real Serato 5: the Tempo Map (one marker)")
def _(R, T):
    g = R.parse_tempo_map(bytes.fromhex(real()["aiff"]["Serato Tempo Map"]))
    eq(len(g), 1)
    near(g[0]["pos"], 1.9669027366724656, 1e-12)
    near(g[0]["bpm"], 124.00172505757646, 1e-12)


@case("real Serato 5: the AAC copy has the same Tempo Map as the AIFF copy, bit for bit")
def _(R, T):
    r = real()
    eq(r["m4a"]["tempomap"], r["aiff"]["Serato Tempo Map"])


@case("real Serato 5: the M4A's Markers_ uses 19-byte entries and holds its one cue")
def _(R, T):
    m = real()["m4a"]
    legacy = R.parse_markers_legacy(bytes.fromhex(m["markers"]))
    t = R.parse_markers2(bytes.fromhex(m["markersv2"]))
    eq([(c["index"], c["ms"]) for c in t["cues"]], [(0, 0)])
    eq([(c["index"], c["ms"]) for c in legacy["cues"]], [(0, 0)])


@case("real Serato 5: the grid from the Tempo Map gives beats about 1 ms before each click")
def _(R, T):
    g = R.parse_tempo_map(bytes.fromhex(real()["aiff"]["Serato Tempo Map"]))
    beats, bpms, nums = R.grid_beats(g, 40.0)
    clicks = [1.0 + k * 60.0 / 124.0 for k in range(80)]
    off = [min(abs(b - c) for c in clicks) * 1000 for b in beats[2:60]]
    assert max(off) < 2.0, max(off)


@case("real Serato 5: the whole tag set written into a new MP3 is read back")
def _(R, T):
    r = real()["aiff"]
    p = _tag_file(T, "mp3", {k: bytes.fromhex(r[k]) for k in ("Serato Markers2", "Serato Markers_", "Serato Autotags", "Serato Tempo Map")})
    t = R.read_tags(p)
    eq([c["ms"] for c in t["cues"]], [17343, 26900, 7786, 33929])
    eq(len(t["grid"]), 1)
    eq(t["conflicts"], 0)


@case("real Serato 5: the database and crate Serato wrote")
def _(R, T):
    lib = R.load(HERE / "fixtures" / "real_serato_5", "/")
    eq(len(lib["tracks"]), 3)
    eq(len(lib["crates"]["Test Crate"]), 24)
    t = {x["title"]: x for x in lib["tracks"]}
    eq(t["Click 124 (AIFF)"]["bpm"], 124.0)
    eq(t["Click 124 (AAC)"]["key"], "Abm")
    assert t["Click 124 (AIFF)"]["date_added"] in ("2026-10-19", "2026-10-20")


# ================================================================== D. beat grids
def _grid(R, markers, dur=40.0):
    mk = []
    for k, m in enumerate(markers):
        mk.append(dict(pos=m[0], beats=m[1]) if k < len(markers) - 1 else dict(pos=m[0], bpm=m[1]))
    return R.grid_beats(mk, dur)


def _steady_case(bpm):
    def fn(R, T):
        beats, bpms, nums = _grid(R, [(0.25, bpm)])
        iv = 60.0 / bpm
        for a, b in zip(beats, beats[1:]):
            near(b - a, iv, 1e-9)
        near(beats[0] % iv, 0.25 % iv, 1e-6)
        assert beats[-1] < 40.0 + 1e-9 and beats[-1] + iv >= 40.0 - 1e-9
        eq(set(round(x, 6) for x in bpms), {round(bpm, 6)})
    return fn


for _b in (60.0, 87.5, 100.0, 120.0, 124.0, 128.0, 140.0, 150.5, 174.0, 200.0):
    add("grid: steady %s BPM" % _b, _steady_case(_b))


@case("grid: first beat is back-filled to the start of the track")
def _(R, T):
    beats, _b, _n = _grid(R, [(10.0, 120.0)])
    near(beats[0], 0.0, 1e-9)
    eq(len(beats), 80)


@case("grid: a grid with its first marker at 0")
def _(R, T):
    beats, _b, _n = _grid(R, [(0.0, 120.0)])
    near(beats[0], 0.0, 1e-9)


@case("grid: 120 to 128 BPM, beats and tempo per beat")
def _(R, T):
    markers = [(1.0, 32), (17.0, 128.0)]
    beats, bpms, nums = _grid(R, markers, 40.0)
    seg1 = [b for b in beats if 1.0 - 1e-9 <= b < 17.0 - 1e-9]
    eq(len(seg1), 32)
    near(seg1[1] - seg1[0], 0.5, 1e-9)            # 32 beats in 16 s: 120 BPM
    after = [b for b in beats if b >= 17.0 - 1e-9]
    near(after[1] - after[0], 60.0 / 128.0, 1e-9)
    eq(round(bpms[beats.index(seg1[3])], 6), 120.0)
    eq(round(bpms[beats.index(after[3])], 6), 128.0)


@case("grid: the beat on the marker is exact")
def _(R, T):
    beats, _b, _n = _grid(R, [(1.0, 32), (17.0, 128.0)])
    assert any(abs(b - 17.0) < 1e-9 for b in beats)


@case("grid: beat-in-bar restarts at every marker")
def _(R, T):
    beats, bpms, nums = _grid(R, [(1.0, 6), (4.0, 120.0)], 10.0)
    i = min(range(len(beats)), key=lambda k: abs(beats[k] - 4.0))
    eq(nums[i], 1)
    eq(nums[i + 1], 2)


@case("grid: 50 markers with a tempo change at each")
def _(R, T):
    pos, m = 1.0, []
    bpm_list = [100 + k for k in range(50)]
    for bpm in bpm_list[:-1]:
        beats = 8
        m.append((pos, beats))
        pos += beats * 60.0 / bpm
    m.append((pos, float(bpm_list[-1])))
    beats, bpms, nums = _grid(R, m, pos + 20)
    assert all(b > a for a, b in zip(beats, beats[1:]))
    eq(round(bpms[beats.index(min(beats, key=lambda x: abs(x - 1.0)))], 3), 100.0)
    assert len(set(round(x, 3) for x in bpms)) >= 49


@case("grid: a segment with zero beats is skipped")
def _(R, T):
    beats, _b, _n = _grid(R, [(1.0, 0), (2.0, 4), (4.0, 120.0)])
    assert len(beats) > 4


@case("grid: a marker that goes backwards in time is skipped")
def _(R, T):
    beats, _b, _n = _grid(R, [(5.0, 4), (3.0, 4), (9.0, 120.0)])
    assert all(b >= 0 for b in beats)


@case("grid: a zero BPM on the last marker gives no grid")
def _(R, T):
    eq(_grid(R, [(1.0, 0.0)])[0], [])


@case("grid: no markers gives no grid")
def _(R, T):
    eq(R.grid_beats([], 10.0), ([], [], []))


@case("grid: the grid stops at the end of the track")
def _(R, T):
    beats, _b, _n = _grid(R, [(0.0, 120.0)], 10.0)
    eq(len(beats), 20)
    assert beats[-1] < 10.0


@case("grid: with no length known it runs 10 minutes")
def _(R, T):
    beats, _b, _n = R.grid_beats([dict(pos=0.0, bpm=120.0)], None)
    eq(len(beats), 1200)


@case("grid: BeatGrid payload round trip (positions are 32-bit floats)")
def _(R, T):
    g = R.parse_beatgrid(W.beatgrid([(0.123456, 16), (8.5, 128.25)]))
    near(g[0]["pos"], 0.123456, 1e-6)
    eq(g[0]["beats"], 16)
    near(g[1]["bpm"], 128.25, 1e-6)


@case("grid: a BeatGrid claiming 10 million markers is refused")
def _(R, T):
    eq(R.parse_beatgrid(b"\x01\x00" + struct.pack(">I", 10 ** 7) + b"\0" * 16), [])


@case("grid: a BeatGrid shorter than its marker count")
def _(R, T):
    eq(R.parse_beatgrid(W.beatgrid([(0.5, 8), (4.5, 120.0)])[:12]), [])


@case("grid: an empty BeatGrid payload")
def _(R, T):
    eq(R.parse_beatgrid(b""), [])


@case("grid: Tempo Map with several markers is reported as not readable, never guessed")
def _(R, T):
    payload = bytearray(W.tempo_map(1.0, 124.0))
    payload[2:4] = struct.pack(">H", 3)
    eq(R.parse_tempo_map(bytes(payload)), [])


@case("grid: Tempo Map with an impossible BPM")
def _(R, T):
    for bpm in (0.0, 5.0, 1000.0, float("nan")):
        eq(R.parse_tempo_map(W.tempo_map(1.0, bpm)), [])


@case("grid: Tempo Map with a negative position")
def _(R, T):
    eq(R.parse_tempo_map(W.tempo_map(-1.0, 124.0)), [])


@case("grid: Tempo Map cut short")
def _(R, T):
    eq(R.parse_tempo_map(W.tempo_map(1.0, 124.0)[:30]), [])


@case("grid: BeatGrid is preferred over a Tempo Map when a file has both")
def _(R, T):
    p = _tag_file(T, "mp3", {"Serato BeatGrid": W.beatgrid([(0.5, 120.0)]), "Serato Tempo Map": W.tempo_map(1.9669, 124.0)})
    near(R.read_tags(p)["grid"][0]["bpm"], 120.0, 1e-3)


# ================================================================== E. the timeline (where Serato's 0:00 is)
def _case_of(kind, **kw):
    def fn(R, T):
        p = T.mk() / "t.mp3"
        W.make_mp3(p, kw.pop("tag", b""), info=kind, **kw)
        eq(R.timeline_case(p), {"lame": "mp3_lame", "xing": "mp3_xing", "vbri": "mp3_vbri", "none": "mp3_plain"}[kind])
    return fn


for _k in ("lame", "xing", "vbri", "none"):
    add("timeline: MP3 with %s frame is its own case" % _k, _case_of(_k))


@case("timeline: a LAME tag with a wrong CRC is its own case")
def _(R, T):
    p = T.mk() / "t.mp3"
    W.make_mp3(p, info="lame", good_crc=False)
    eq(R.timeline_case(p), "mp3_lame_badcrc")


@case("timeline: an MP3 that starts with an ID3 tag still finds its info frame")
def _(R, T):
    p = T.mk() / "t.mp3"
    W.make_mp3(p, W.id3v24({"Serato Markers2": _GOOD}), info="lame")
    eq(R.timeline_case(p), "mp3_lame")


@case("timeline: WAV, AIFF and FLAC are lossless")
def _(R, T):
    d = T.mk()
    W.make_wav(d / "a.wav")
    W.make_aiff(d / "a.aiff")
    W.make_flac(d / "a.flac", {})
    eq([R.timeline_case(d / x) for x in ("a.wav", "a.aiff", "a.flac")], ["lossless"] * 3)


@case("timeline: an M4A with AAC is the AAC case")
def _(R, T):
    p = T.mk() / "a.m4a"
    W.make_m4a(p, itunsmpb=" 00000000 00000840 00000120 000000000000AC44 00000000")
    eq(R.timeline_case(p), "aac")


@case("timeline: an unknown extension")
def _(R, T):
    p = T.mk() / "a.xyz"
    p.write_bytes(b"x")
    eq(R.timeline_case(p), "other")


@case("timeline: a missing MP3 is treated as having no info frame")
def _(R, T):
    eq(R.timeline_case(T.mk() / "missing.mp3"), "mp3_plain")


# ================================================================== F. reading never writes
@case("safety: loading a library changes no byte and no modification time")
def _(R, T):
    d = T.mk()
    f = W.make_library(d, [dict(pfil="a.mp3")], crates={"C": ["a.mp3"]}, smarts={"S": W.smart_crate([(9, "cond_is_str", "x")])})
    before = tree_hash(d)
    R.load(f, "/")
    eq(tree_hash(d), before)


@case("safety: reading tags from every container changes no byte")
def _(R, T):
    d = T.mk()
    paths = [_tag_file(T, k, {"Serato Markers2": _GOOD}) for k in ("mp3", "wav", "aiff", "m4a", "flac")]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
    for p in paths:
        R.read_tags(p)
    for p in paths:
        eq((p.read_bytes(), p.stat().st_mtime_ns), before[p])


@case("safety: a read-only library folder can be read")
def _(R, T):
    d = T.mk()
    f = W.make_library(d, [dict(pfil="a.mp3")], crates={"C": ["a.mp3"]})
    for p in f.rglob("*"):
        os.chmod(p, 0o555 if p.is_dir() else 0o444)
    try:
        eq(len(R.load(f, "/")["tracks"]), 1)
    finally:
        for p in sorted(f.rglob("*"), reverse=True):
            os.chmod(p, 0o755 if p.is_dir() else 0o644)
        os.chmod(f, 0o755)
