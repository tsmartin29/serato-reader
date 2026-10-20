"""Serato's library files, read only: "database V2", crates (Subcrates/*.crate) and smart crates (SmartCrates/*.scrate).

A Serato library lives in a folder named "_Serato_": one in the user's Music folder for the computer's own drive, and one at the
top of every external drive that holds music. Paths inside a library are relative to the drive the _Serato_ folder is on (no
leading slash), so a library needs to know its drive root to give you real file paths.

Nothing here writes to Serato's files. Each file is read in one go and parsed from memory.
"""
import datetime
import os
import pathlib
import unicodedata

from . import tlv

CRATE_SEP = "%%"

# Smart-crate rule fields (the "urkt" number) and conditions (the "trft" text). The field numbers come from public notes and from
# smart crates written by Serato DJ; ids not listed come through as field_id with field=None.
SMART_FIELDS = {4: "filename", 6: "title", 7: "artist", 8: "album", 9: "genre", 15: "bpm", 17: "comment", 19: "grouping",
                20: "remixer", 21: "label", 22: "composer", 23: "year", 25: "added", 51: "key", 79: "plays"}
TEXT_CONDITIONS = {"cond_con_str": "contains", "cond_dnc_str": "does not contain", "cond_is_str": "is", "cond_isn_str": "is not"}
NUMBER_CONDITIONS = {"cond_greq_uint": ">=", "cond_lseq_uint": "<=", "cond_is_uint": "=", "cond_isn_uint": "!="}
DATE_CONDITIONS = {"cond_bef_time": "before", "cond_aft_time": "after", "cond_bef_str": "before", "cond_aft_str": "after",
                   "cond_bef_date": "before", "cond_aft_date": "after"}


# ------------------------------------------------------------------ text fields
def num(v):
    """'128.00' / '320.0kbps' / '44.1k' / '9.8MB' -> float (None if there is no number)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", ".")
    digits = ""
    for ch in s:
        if ch.isdigit() or ch in ".-" and not (ch == "-" and digits):
            digits += ch
        elif digits:
            break
    try:
        x = float(digits)
    except ValueError:
        return None
    low = s.lower()
    if low.endswith("k") or low.endswith("khz"):
        x *= 1000
    elif low.endswith("mb"):
        x *= 1024 * 1024
    elif low.endswith("kb") and not low.endswith("kbps"):
        x *= 1024
    elif low.endswith("gb"):
        x *= 1024 ** 3
    return x


def length_s(v):
    """'05:23.45' / '5:23' / '1:02:03' / '323.4' -> seconds (None when it is not a time)."""
    if v is None:
        return None
    try:
        parts = [float(p) for p in str(v).strip().split(":")]
    except ValueError:
        return None
    t = 0.0
    for p in parts:
        t = t * 60 + p
    return t


def nfc(text):
    return unicodedata.normalize("NFC", text or "")


# ------------------------------------------------------------------ tracks
class Track(object):
    """One track of the database. `raw` holds every field Serato stored; the attributes are the ones people use."""

    def __init__(self, raw, library):
        self.raw = raw
        self.library = library
        self.relative_path = raw.get("pfil", "")
        self.path = library.abspath(self.relative_path)
        self.title = raw.get("tsng") or None
        self.artist = raw.get("tart") or None
        self.album = raw.get("talb") or None
        self.genre = raw.get("tgen") or None
        self.comment = raw.get("tcom") or None
        self.grouping = raw.get("tgrp") or None
        self.label = raw.get("tlbl") or None
        self.composer = raw.get("tcmp") or None
        self.remixer = raw.get("trmx") or None
        self.key = raw.get("tkey") or None
        self.bpm = num(raw.get("tbpm"))
        self.length_s = length_s(raw.get("tlen"))
        self.file_size = raw.get("ufsb") if isinstance(raw.get("ufsb"), int) else (int(num(raw.get("tsiz")) or 0) or None)
        self.sample_rate = num(raw.get("tsmp"))
        self.bitrate_kbps = num(raw.get("tbit"))
        self.file_type = raw.get("ttyp") or None
        self.color = raw.get("ulbl") if isinstance(raw.get("ulbl"), int) else None
        self.grid_locked = bool(raw.get("bbgl"))
        self.missing = bool(raw.get("bmis"))
        self.corrupt = bool(raw.get("bcrt"))
        self.played = bool(raw.get("bply"))
        year = raw.get("ttyr") if raw.get("ttyr") not in (None, "") else raw.get("tyr")
        self.year = int(num(year)) if num(year) else None
        self.track_number = int(num(raw.get("utkn"))) if num(raw.get("utkn")) else None
        # Serato's own Date Added: Unix seconds ("uadd", older versions "tadd")
        self.date_added = None
        for k in ("uadd", "tadd"):
            try:
                ts = int(float(raw.get(k)))
            except (TypeError, ValueError):
                continue
            if ts > 0:
                self.date_added = ts
                break

    @property
    def date_added_iso(self):
        """Date Added as a UTC ISO 8601 time ('2026-10-19T17:02:46Z'), or None."""
        if self.date_added is None:
            return None
        return datetime.datetime.fromtimestamp(self.date_added, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def exists(self):
        return resolve_existing(self.path) is not None

    def __repr__(self):
        return "Track(%r)" % (self.path,)


def resolve_existing(path):
    """The path as it is on disk: tries it as stored, then Unicode-normalised both ways (macOS keeps names decomposed, Serato may
    store either). Returns None when the file is not there."""
    for p in (path, unicodedata.normalize("NFC", path), unicodedata.normalize("NFD", path)):
        try:
            if os.path.isfile(p):
                return p
        except (OSError, ValueError):
            pass
    return None


# ------------------------------------------------------------------ smart crates
class Rule(object):
    def __init__(self, field_id, condition, text, number, date):
        self.field_id = field_id
        self.field = SMART_FIELDS.get(field_id)
        self.condition = condition
        self.operator = TEXT_CONDITIONS.get(condition) or NUMBER_CONDITIONS.get(condition) or DATE_CONDITIONS.get(condition)
        self.text, self.number, self.date = text, number, date

    @property
    def value(self):
        for v in (self.text, self.date, self.number):
            if v not in (None, ""):
                return v
        return None

    def as_dict(self):
        return dict(field=self.field, field_id=self.field_id, condition=self.condition, operator=self.operator, value=self.value)

    def __repr__(self):
        return "Rule(%r %r %r)" % (self.field or self.field_id, self.operator or self.condition, self.value)


class SmartCrate(object):
    def __init__(self, name, match_all, live, rules):
        self.name, self.match_all, self.live, self.rules = name, match_all, live, rules

    def matches(self, track):
        """Does this track meet the rules? A rule on a field the reader does not know never matches."""
        results = [_rule_matches(r, track) for r in self.rules]
        if not results:
            return False
        return all(results) if self.match_all else any(results)

    def __repr__(self):
        return "SmartCrate(%r, %s, %d rules)" % (self.name, "all" if self.match_all else "any", len(self.rules))


def _flag(v):
    if isinstance(v, list):
        for d in v:
            for x in d.values():
                if isinstance(x, (bool, int)):
                    return bool(x)
        return False
    if isinstance(v, (bytes, bytearray)):
        return any(v)
    return bool(v)


def parse_smart_crate(name, data):
    fields = tlv.parse(data)
    d = tlv.as_dict(fields)
    rules = [Rule(r.get("urkt"), r.get("trft", ""), r.get("trpt"), r.get("urpt"), r.get("trtt")) for r in d.get("rurt", [])]
    return SmartCrate(name, _flag(d.get("rart", False)), _flag(d.get("rlut", False)), rules)


def _rule_matches(rule, track):
    f = rule.field
    if f is None or f == "plays":
        return False
    if f == "filename":
        x = os.path.basename(track.path)
    elif f == "year":
        x = track.year
    elif f == "added":
        x = track.date_added
    else:
        x = {"title": track.title, "artist": track.artist, "album": track.album, "genre": track.genre, "bpm": track.bpm,
             "comment": track.comment, "grouping": track.grouping, "remixer": track.remixer, "label": track.label,
             "composer": track.composer, "key": track.key}.get(f)
    cond, v = rule.condition, rule.value
    if cond in TEXT_CONDITIONS:
        a, b = str(x if x is not None else "").lower(), str(v if v is not None else "").lower()
        return {"contains": b in a, "does not contain": b not in a, "is": a == b, "is not": a != b}[TEXT_CONDITIONS[cond]]
    if cond in NUMBER_CONDITIONS:
        try:
            a, b = float(num(x) or 0), float(v)
        except (TypeError, ValueError):
            return False
        return {">=": a >= b, "<=": a <= b, "=": a == b, "!=": a != b}[NUMBER_CONDITIONS[cond]]
    if cond in DATE_CONDITIONS:
        a, b = _as_unix(x), _as_unix(v)
        if a is None or b is None:
            return False
        return a < b if DATE_CONDITIONS[cond] == "before" else a > b
    return False


def _as_unix(v):
    if v in (None, ""):
        return None
    try:
        n = int(float(v))
        if n > 100000:
            return n
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return int(datetime.datetime.strptime(s, fmt).replace(tzinfo=datetime.timezone.utc).timestamp())
        except ValueError:
            pass
    return None


# ------------------------------------------------------------------ libraries
class Library(object):
    """One _Serato_ folder: its database tracks, crates and smart crates, with absolute paths.

    tracks        list of Track
    crates        {full crate name: [absolute track paths in Serato's order]}; a crate inside another is "Parent%%Child"
    smart_crates  {name: SmartCrate}
    errors        files that could not be read, as plain text (a damaged crate never stops the rest)
    """

    def __init__(self, folder, root="/"):
        self.folder = pathlib.Path(folder)
        self.root = str(root)
        self.tracks = []
        self.crates = {}
        self.smart_crates = {}
        self.version = ""
        self.errors = []

    def abspath(self, rel):
        rel = str(rel or "")
        if not rel:
            return ""
        if rel.startswith("/"):
            return rel
        if self.root == "/":
            return "/" + rel
        return os.path.join(self.root, rel)

    def crate_tree(self):
        """Crates as a nested dict: {name: {"tracks": [paths], "children": {...}}}. A parent that has no crate file of its own (only
        children) shows up with an empty track list."""
        tree = {}
        for full, paths in sorted(self.crates.items(), key=lambda kv: kv[0].lower()):
            node, parts = None, full.split(CRATE_SEP)
            level = tree
            for i, part in enumerate(parts):
                node = level.setdefault(part, {"tracks": [], "children": {}})
                if i < len(parts) - 1:
                    level = node["children"]
            node["tracks"] = list(paths)
        return tree

    def track_by_path(self, path):
        for t in self.tracks:
            if t.path == path:
                return t
        return None

    def smart_crate_tracks(self, name):
        sc = self.smart_crates[name]
        return [t for t in self.tracks if sc.matches(t)]


def find_libraries(home=None, volumes="/Volumes"):
    """[Library folder, drive root] pairs for every Serato library found: the one in the user's Music folder (paths from "/") and the
    _Serato_ folder at the top of each mounted drive (paths from that drive). Returns a list of (folder, root)."""
    out = []
    home_dir = pathlib.Path(home).expanduser() if home else pathlib.Path("~/Music/_Serato_").expanduser()
    if (home_dir / "database V2").is_file() or (home_dir / "Subcrates").is_dir():
        out.append((home_dir, "/"))
    if volumes:
        try:
            vols = sorted(pathlib.Path(volumes).iterdir())
        except OSError:
            vols = []
        for v in vols:
            f = v / "_Serato_"
            try:
                if (f / "database V2").is_file() or (f / "Subcrates").is_dir() or (f / "SmartCrates").is_dir():
                    try:
                        same = f.resolve() == home_dir.resolve()
                    except OSError:
                        same = False
                    if not same:
                        out.append((f, str(v)))
            except OSError:
                pass
    return out


def crate_name(stem):
    """Subcrates file name without '.crate' -> crate names outermost first ('A%%B' -> ['A', 'B'])."""
    return [p for p in unicodedata.normalize("NFC", stem).split(CRATE_SEP) if p != ""] or [stem]


def _read(path):
    with open(str(path), "rb") as f:
        return f.read()


def load_library(folder, root="/"):
    """Read one _Serato_ folder. Never writes anything there. `root` is the drive the folder's paths start from ("/" for the
    computer's own library, '/Volumes/<name>' for an external drive's)."""
    lib = Library(folder, root)
    if not lib.folder.is_dir():
        lib.errors.append("folder not found: %s" % lib.folder)
        return lib
    dbf = lib.folder / "database V2"
    if dbf.is_file():
        try:
            data = _read(dbf)
        except OSError as e:
            lib.errors.append("database V2: %s" % e)
            data = b""
        for tag, v in tlv.parse(data):
            if tag == "vrsn":
                lib.version = v
            elif tag == "otrk" and isinstance(v, list):
                raw = tlv.as_dict(v)
                if raw.get("pfil"):
                    lib.tracks.append(Track(raw, lib))
    sub = lib.folder / "Subcrates"
    if sub.is_dir():
        for f in sorted(sub.glob("*.crate")):
            if f.name.startswith("._"):
                continue
            try:
                items = tlv.parse(_read(f))
            except OSError as e:
                lib.errors.append("%s: %s" % (f.name, e))
                continue
            paths = []
            for tag, v in items:
                if tag == "otrk" and isinstance(v, list):
                    p = tlv.as_dict(v).get("ptrk")
                    if p:
                        paths.append(lib.abspath(p))
            lib.crates[CRATE_SEP.join(crate_name(f.stem))] = paths
    sc = lib.folder / "SmartCrates"
    if sc.is_dir():
        for f in sorted(sc.glob("*.scrate")):
            if f.name.startswith("._"):
                continue
            name = CRATE_SEP.join(crate_name(f.stem))
            try:
                lib.smart_crates[name] = parse_smart_crate(name, _read(f))
            except OSError as e:
                lib.errors.append("%s: %s" % (f.name, e))
    return lib


def load_all(home=None, volumes="/Volumes"):
    """Every library find_libraries() finds, loaded."""
    return [load_library(f, r) for f, r in find_libraries(home, volumes)]
