"""Serato's per-file tags, read only. Written from the public format notes in Holzhaus' "serato-tags" (docs: CC BY-SA 4.0, scripts:
MIT; no code copied) and checked against files written by Serato DJ Lite 5.0. Files are opened read-only and never written.

Where Serato keeps its data inside an audio file:
  MP3 / AIFF / WAV   ID3v2 GEOB frames "Serato Markers2", "Serato Markers_", "Serato BeatGrid", "Serato Autotags" (AIFF and WAV keep the
                     ID3 tag in an "ID3 " / "id3 " chunk)
  M4A / MP4          iTunes freeform atoms ----:com.serato.dj:markersv2 / markers / beatgrid / autgain, base64 text
  FLAC               Vorbis comments SERATO_MARKERS_V2 / SERATO_BEATGRID / SERATO_AUTOGAIN, base64 text
  Ogg Vorbis         Vorbis comments serato_markers2 / serato_beatgrid (best effort: the public notes say this format differs)
The base64 forms decode to "application/octet-stream\\0" "\\0" "<GEOB description>\\0" + the same payload as the GEOB frame.

Payloads:
  Markers2   01 01, then base64 text (newline every 72 characters, null padded) of: 01 01, entries (type\\0, u32 BE length, data), 00
             CUE   [0]=0, [1]=index, [2:6]=position ms, [6]=0, [7:10]=RGB, [10:12]=0, [12:]=name UTF-8 \\0
             LOOP  [1]=index, [2:6]=start ms, [6:10]=end ms, [10:14]=ff.., [14:18]=0RGB, [18]=?, [19]=locked, [20:]=name UTF-8 \\0
             COLOR [1:4]=track colour RGB      BPMLOCK [0]=grid locked      FLIP: saved flips (counted only)
  Markers_   02 05, u32 count, entries, track colour. ID3: 22-byte entries in "serato32" (7 bits a byte); MP4: 19-byte plain entries
             (MP4 layout unconfirmed). Entries 0-4 are hot cues 1-5, 5-13 saved loops 1-9. Serato prefers this over Markers2 for the
             positions and colours it holds; names only exist in Markers2.
  BeatGrid   01 00, u32 marker count, markers (f32 position in seconds + u32 beats to the next marker; the last one: f32 position + f32
             BPM), 1 footer byte
  Autotags   01 01, then three ASCII strings \\0: BPM, auto gain, gain dB
  Tempo Map  Serato DJ 5.0 writes this INSTEAD of BeatGrid (seen in real files, 19 Oct 2026): 02 00, u16 marker count, then for one
             marker: f64 position in seconds (BE), u32 1, 8 zero bytes, f64 BPM, f64 0.25, 01, 8 zero bytes. Only a one-marker map has
             been seen, so only that is read (position + BPM); a longer one is reported as unreadable, never guessed.

read_beatgrid(path) is usable on its own: file -> markers, beat times and tempo per beat.
"""
import base64
import os
import pathlib
import struct

GEOB_NAMES = {"Serato Markers2": "markers2", "Serato Markers_": "markers", "Serato BeatGrid": "beatgrid", "Serato Autotags": "autotags",
              "Serato Tempo Map": "tempomap"}
MP4_NAMES = {"markersv2": "markers2", "markers": "markers", "beatgrid": "beatgrid", "autgain": "autotags", "tempomap": "tempomap"}
VORBIS_NAMES = {"serato_markers_v2": "markers2", "serato_markers2": "markers2", "serato_markers": "markers",
                "serato_beatgrid": "beatgrid", "serato_autogain": "autotags", "serato_autotags": "autotags", "serato_tempomap": "tempomap"}
MP3_EXT = {".mp3"}
MP4_EXT = {".m4a", ".mp4", ".aac", ".m4b", ".alac"}
ID3_CHUNK_EXT = {".aif", ".aiff", ".aifc", ".wav", ".wave"}
FLAC_EXT = {".flac"}
OGG_EXT = {".ogg", ".oga"}


# ------------------------------------------------------------------ small helpers
def b64(text):
    """Serato's base64: no padding, newlines, sometimes one character too many (a lone last character is dropped)."""
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).split(b"\0", 1)[0].decode("ascii", "ignore")
    s = "".join(text.split())
    if len(s) % 4 == 1:
        s = s[:-1]
    return base64.b64decode(s + "=" * (-len(s) % 4))


def unwrap(data):
    """Strip the 'application/octet-stream\\0\\0<name>\\0' header the base64 containers (MP4, FLAC, Ogg) put before the payload."""
    if data.startswith(b"application/octet-stream\0"):
        rest = data[len(b"application/octet-stream\0"):]
        rest = rest[1:] if rest[:1] == b"\0" else rest            # the empty file name
        i = rest.find(b"\0")
        return rest[i + 1:] if i >= 0 else b""
    return data


def _synchsafe(b):
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _deunsync(b):
    return b.replace(b"\xff\x00", b"\xff")


def _read_at(f, off, n):
    f.seek(off)
    return f.read(n)


# ------------------------------------------------------------------ ID3v2 (MP3, and the ID3 chunk of AIFF / WAV)
def _cstr(data, i, enc):
    """A null-terminated string in ID3 encoding `enc` starting at i: (text, next index)."""
    if enc in (1, 2):                                     # UTF-16: two-byte terminator on an even boundary
        j = i
        while j + 1 < len(data) and data[j:j + 2] != b"\0\0":
            j += 2
        raw = data[i:j]
        try:
            txt = raw.decode("utf-16" if enc == 1 else "utf-16-be")
        except UnicodeDecodeError:
            txt = raw.decode("utf-16-le", "ignore")
        return txt, j + 2
    j = data.find(b"\0", i)
    j = len(data) if j < 0 else j
    return data[i:j].decode("utf-8" if enc == 3 else "latin-1", "ignore"), j + 1


def parse_geob(body):
    """GEOB frame body -> (description, data)."""
    enc = body[0]
    _mime, i = _cstr(body, 1, 0)
    _fname, i = _cstr(body, i, enc)
    desc, i = _cstr(body, i, enc)
    return desc, body[i:]


def id3_frames(tag):
    """Frames of a whole ID3v2 tag (bytes starting with 'ID3'): list of (frame id, body). v2.2, v2.3 and v2.4."""
    if len(tag) < 10 or tag[:3] != b"ID3":
        return []
    ver, flags = tag[3], tag[5]
    size = _synchsafe(tag[6:10])
    body = tag[10:10 + size]
    if flags & 0x80 and ver < 4:                          # whole-tag unsynchronisation (v2.2 / v2.3)
        body = _deunsync(body)
    i = 0
    if flags & 0x40:                                      # extended header
        if ver == 3 and len(body) >= 4:
            i = 4 + struct.unpack(">I", body[:4])[0]
        elif ver == 4 and len(body) >= 4:
            i = _synchsafe(body[:4])
    out = []
    if ver == 2:
        while i + 6 <= len(body) and body[i:i + 1] not in (b"\0", b""):
            fid = body[i:i + 3].decode("latin-1")
            n = int.from_bytes(body[i + 3:i + 6], "big")
            out.append(("GEOB" if fid == "GEO" else fid, body[i + 6:i + 6 + n]))
            i += 6 + n
        return out
    while i + 10 <= len(body) and body[i:i + 1] not in (b"\0", b""):
        fid = body[i:i + 4].decode("latin-1", "ignore")
        n = _synchsafe(body[i + 4:i + 8]) if ver == 4 else struct.unpack(">I", body[i + 4:i + 8])[0]
        if ver == 4 and n > len(body) - i - 10:           # some writers put a plain size in v2.4
            n = struct.unpack(">I", body[i + 4:i + 8])[0]
        fflags = body[i + 9]
        data = body[i + 10:i + 10 + n]
        if ver == 4:
            if fflags & 0x01 and len(data) >= 4:          # data length indicator
                data = data[4:]
            if fflags & 0x02 or flags & 0x80:             # frame unsynchronisation
                data = _deunsync(data)
        out.append((fid, data))
        i += 10 + n
    return out


def _id3_at_start(f):
    head = _read_at(f, 0, 10)
    if head[:3] != b"ID3":
        return None
    return head + f.read(_synchsafe(head[6:10]))


def _iff_chunks(f, size, big_endian, start):
    """(id, offset of data, size) for the chunks of a RIFF / FORM file."""
    off = start
    while off + 8 <= size:
        h = _read_at(f, off, 8)
        if len(h) < 8:
            break
        cid = h[:4]
        n = struct.unpack(">I" if big_endian else "<I", h[4:])[0]
        yield cid, off + 8, n
        off += 8 + n + (n & 1)


def _id3_in_chunk(f):
    size = os.fstat(f.fileno()).st_size
    head = _read_at(f, 0, 12)
    if head[:4] == b"FORM":
        big = True
    elif head[:4] in (b"RIFF", b"RF64"):
        big = False
    else:
        return None
    for cid, off, n in _iff_chunks(f, size, big, 12):
        if cid in (b"ID3 ", b"id3 ", b"ID32"):
            return _read_at(f, off, n)
    return None


def _tags_from_id3(tag, out):
    for fid, body in id3_frames(tag or b""):
        if fid != "GEOB" or not body:
            continue
        try:
            desc, data = parse_geob(body)
        except (IndexError, UnicodeDecodeError):
            continue
        k = GEOB_NAMES.get(desc)
        if k and k not in out:
            out[k] = data


# ------------------------------------------------------------------ MP4
_MP4_CONTAINERS = {b"moov", b"udta", b"ilst", b"trak", b"mdia", b"minf", b"stbl"}


def _mp4_atoms(f, start, end):
    off = start
    while off + 8 <= end:
        h = _read_at(f, off, 8)
        if len(h) < 8:
            return
        n, typ = struct.unpack(">I4s", h)
        hl = 8
        if n == 1:
            n = struct.unpack(">Q", f.read(8))[0]
            hl = 16
        elif n == 0:
            n = end - off
        if n < hl:
            return
        yield typ, off + hl, off + n
        off += n


def _mp4_freeform(f):
    """{(mean, name): [data payloads]} for every ---- atom under moov/udta/meta/ilst."""
    size = os.fstat(f.fileno()).st_size
    found = {}

    def walk(start, end, depth):
        for typ, a, b in _mp4_atoms(f, start, end):
            if typ == b"moov" or typ == b"udta" or typ == b"ilst":
                walk(a, b, depth + 1)
            elif typ == b"meta":
                walk(a + 4, b, depth + 1)                # full box: version + flags first
            elif typ == b"----":
                mean = name = None
                datas = []
                for t2, a2, b2 in _mp4_atoms(f, a, b):
                    raw = _read_at(f, a2, b2 - a2)
                    if t2 == b"mean":
                        mean = raw[4:].decode("utf-8", "ignore")
                    elif t2 == b"name":
                        name = raw[4:].decode("utf-8", "ignore")
                    elif t2 == b"data":
                        datas.append(raw[8:])            # type (4) + locale (4)
                if mean and name:
                    found.setdefault((mean, name), []).extend(datas)
    walk(0, size, 0)
    return found


# ------------------------------------------------------------------ FLAC / Ogg Vorbis comments
def _vorbis_comments(block):
    """{lowercase key: [values]} from a Vorbis comment structure (little-endian lengths)."""
    out = {}
    try:
        n = struct.unpack_from("<I", block, 0)[0]
        i = 4 + n
        count = struct.unpack_from("<I", block, i)[0]
        i += 4
        for _ in range(count):
            m = struct.unpack_from("<I", block, i)[0]
            i += 4
            kv = block[i:i + m]
            i += m
            k, _, v = kv.partition(b"=")
            out.setdefault(k.decode("ascii", "ignore").lower(), []).append(v)
    except struct.error:
        pass
    return out


def _flac_comments(f):
    if _read_at(f, 0, 4) != b"fLaC":
        id3 = _id3_at_start(f)                           # a FLAC with an ID3 tag in front
        if not id3 or _read_at(f, len(id3), 4) != b"fLaC":
            return {}
        off = len(id3) + 4
    else:
        off = 4
    while True:
        h = _read_at(f, off, 4)
        if len(h) < 4:
            return {}
        last, typ, n = h[0] & 0x80, h[0] & 0x7F, int.from_bytes(h[1:4], "big")
        if typ == 4:
            return _vorbis_comments(_read_at(f, off + 4, n))
        if last:
            return {}
        off += 4 + n


def _ogg_packets(f, want=2, limit=1 << 24):
    """The first `want` packets of the first logical stream of an Ogg file."""
    packets, cur, off = [], b"", 0
    while len(packets) < want and off < limit:
        h = _read_at(f, off, 27)
        if len(h) < 27 or h[:4] != b"OggS":
            break
        nseg = h[26]
        lace = _read_at(f, off + 27, nseg)
        pos = off + 27 + nseg
        for L in lace:
            cur += _read_at(f, pos, L)
            pos += L
            if L < 255:
                packets.append(cur)
                cur = b""
                if len(packets) >= want:
                    break
        off = off + 27 + nseg + sum(lace)
    return packets


def _ogg_comments(f):
    pk = _ogg_packets(f, 2)
    if len(pk) >= 2 and pk[1][:7] == b"\x03vorbis":
        return _vorbis_comments(pk[1][7:])
    if len(pk) >= 2 and pk[1][:8] == b"OpusTags":
        return _vorbis_comments(pk[1][8:])
    return {}


def _tags_from_vorbis(comments, out):
    for k, vals in comments.items():
        name = VORBIS_NAMES.get(k)
        if name and vals and name not in out:
            try:
                out[name] = unwrap(b64(vals[0]))
            except (ValueError, TypeError):
                pass


# ------------------------------------------------------------------ one file -> raw payloads
def raw_tags(path):
    """{'markers2'|'markers'|'beatgrid'|'autotags': payload bytes} found in the file, plus 'container'. Read only."""
    p = pathlib.Path(path)
    ext = p.suffix.lower()
    out = {}
    with open(p, "rb") as f:
        if ext in MP4_EXT:
            out["container"] = "mp4"
            for (mean, name), datas in _mp4_freeform(f).items():
                k = MP4_NAMES.get(name) if mean == "com.serato.dj" else None
                if k and datas and k not in out:
                    try:
                        out[k] = unwrap(b64(datas[0]))
                    except (ValueError, TypeError):
                        pass
        elif ext in FLAC_EXT:
            out["container"] = "flac"
            _tags_from_vorbis(_flac_comments(f), out)
        elif ext in OGG_EXT:
            out["container"] = "ogg"
            _tags_from_vorbis(_ogg_comments(f), out)
        elif ext in ID3_CHUNK_EXT:
            out["container"] = "id3-chunk"
            _tags_from_id3(_id3_in_chunk(f), out)
        else:
            out["container"] = "id3"
            _tags_from_id3(_id3_at_start(f), out)
    return out


# ------------------------------------------------------------------ payload parsers
def parse_markers2(payload):
    """Markers2 -> dict(cues=[{index, ms, rgb, name}], loops=[{index, start, end, rgb, name, locked}], color, bpm_lock, flips)."""
    out = dict(cues=[], loops=[], color=None, bpm_lock=None, flips=0)
    if not payload or len(payload) < 3:
        return out
    body = payload[2:] if payload[:2] == b"\x01\x01" else payload
    try:
        data = b64(body)
    except (ValueError, TypeError):
        return out
    if data[:2] != b"\x01\x01":
        return out
    i = 2
    while i < len(data):
        j = data.find(b"\0", i)
        if j < 0 or j == i:
            break
        typ = data[i:j].decode("ascii", "ignore")
        if j + 5 > len(data):
            break
        n = struct.unpack_from(">I", data, j + 1)[0]
        d = data[j + 5:j + 5 + n]
        i = j + 5 + n
        if typ == "CUE" and len(d) >= 12:
            out["cues"].append(dict(index=d[1], ms=struct.unpack_from(">I", d, 2)[0], rgb=int.from_bytes(d[7:10], "big"),
                                    name=d[12:].split(b"\0", 1)[0].decode("utf-8", "ignore")))
        elif typ == "LOOP" and len(d) >= 20:
            out["loops"].append(dict(index=d[1], start=struct.unpack_from(">I", d, 2)[0], end=struct.unpack_from(">I", d, 6)[0],
                                     rgb=int.from_bytes(d[15:18], "big"), locked=bool(d[19]),
                                     name=d[20:].split(b"\0", 1)[0].decode("utf-8", "ignore")))
        elif typ == "COLOR" and len(d) >= 4:
            out["color"] = int.from_bytes(d[1:4], "big")
        elif typ == "BPMLOCK" and len(d) >= 1:
            out["bpm_lock"] = bool(d[0])
        elif typ == "FLIP":
            out["flips"] += 1
    return out


def serato32_decode(b):
    w, x, y, z = b
    c = (z & 0x7F) | ((y & 0x01) << 7)
    bb = ((y & 0x7F) >> 1) | ((x & 0x03) << 6)
    a = ((x & 0x7F) >> 2) | ((w & 0x07) << 5)
    return (a << 16) | (bb << 8) | c


def serato32_encode(v):
    a, b, c = (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF
    return bytes([(a >> 5) & 0x7F, ((b >> 6) | (a << 2)) & 0x7F, ((c >> 7) | (b << 1)) & 0x7F, c & 0x7F])


def parse_markers_legacy(payload):
    """Markers_ -> dict(cues, loops, color). Unset entries are left out."""
    out = dict(cues=[], loops=[], color=None)
    if not payload or len(payload) < 6 or payload[:2] != b"\x02\x05":
        return out
    n = struct.unpack_from(">I", payload, 2)[0]
    rest = len(payload) - 6
    if n <= 0 or n > 64:
        return out
    size = 22 if rest >= 22 * n else 19
    for k in range(n):
        e = payload[6 + k * size:6 + (k + 1) * size]
        if len(e) < size:
            break
        if size == 22:
            sset, eset = e[0] == 0, e[5] == 0
            start = serato32_decode(e[1:5]) if sset else None
            end = serato32_decode(e[6:10]) if eset else None
            rgb, typ, locked = serato32_decode(e[16:20]), e[20], bool(e[21])
        else:                                            # MP4 variant (unconfirmed): plain u32 positions, 0xffffffff = unset
            s_, e_ = struct.unpack_from(">II", e, 0)
            start = None if s_ == 0xFFFFFFFF else s_
            end = None if e_ == 0xFFFFFFFF else e_
            rgb, typ, locked = int.from_bytes(e[14:17], "big"), e[17], bool(e[18])
        if start is None:
            continue
        if k < 5:                                        # hot cues 1-5
            out["cues"].append(dict(index=k, ms=start, rgb=rgb, name=""))
        elif end is not None and end > start:             # saved loops 1-9
            out["loops"].append(dict(index=k - 5, start=start, end=end, rgb=rgb, name="", locked=locked))
    tail = payload[6 + n * size:6 + n * size + 4]
    if len(tail) == 4:
        out["color"] = serato32_decode(tail) if size == 22 else int.from_bytes(tail[1:], "big")
    return out


def parse_beatgrid(payload):
    """BeatGrid -> list of markers: dict(pos=seconds, beats=int) for every marker but the last, dict(pos, bpm) for the last."""
    if not payload or len(payload) < 6:
        return []
    n = struct.unpack_from(">I", payload, 2)[0]
    if n <= 0 or len(payload) < 6 + 8 * n or n > 100000:
        return []
    out = []
    for k in range(n):
        if k < n - 1:
            pos, beats = struct.unpack_from(">fI", payload, 6 + 8 * k)
            out.append(dict(pos=float(pos), beats=int(beats)))
        else:
            pos, bpm = struct.unpack_from(">ff", payload, 6 + 8 * k)
            out.append(dict(pos=float(pos), bpm=float(bpm)))
    return out


def parse_tempo_map(payload):
    """Serato 5 "Tempo Map" -> the same marker list parse_beatgrid gives for a one-marker grid: [dict(pos=seconds, bpm=BPM)].
    Empty when the map has more than one marker (layout not known yet) or is damaged. Layout seen in real files: see the module notes."""
    if not payload or len(payload) < 41 or payload[:1] != b"\x02":
        return []
    n = struct.unpack_from(">H", payload, 2)[0]
    if n != 1:
        return []
    pos = struct.unpack_from(">d", payload, 4)[0]
    bpm = struct.unpack_from(">d", payload, 24)[0]
    if not (pos == pos and bpm == bpm) or pos < 0 or pos > 36000 or not 20.0 <= bpm <= 400.0:
        return []
    return [dict(pos=float(pos), bpm=float(bpm))]


def parse_autotags(payload):
    """Autotags -> dict(bpm, autogain, gain_db) (None for any value that isn't there)."""
    out = dict(bpm=None, autogain=None, gain_db=None)
    if not payload or len(payload) < 3:
        return out
    parts = payload[2:].split(b"\0")
    for k, v in zip(("bpm", "autogain", "gain_db"), parts):
        try:
            out[k] = float(v.decode("ascii", "ignore").strip())
        except ValueError:
            pass
    return out


def merge_markers(m2, legacy):
    """Markers2 with Markers_ on top for the slots Markers_ holds (Serato prefers Markers_ when both are present); names from
    Markers2. Returns (cues, loops, track colour, conflicts)."""
    cues = {c["index"]: dict(c) for c in m2.get("cues", [])}
    loops = {l["index"]: dict(l) for l in m2.get("loops", [])}
    conflicts = 0
    for c in legacy.get("cues", []):
        old = cues.get(c["index"])
        if old and (old["ms"] != c["ms"]):
            conflicts += 1
        cues[c["index"]] = dict(c, name=(old or {}).get("name", ""))
    for l in legacy.get("loops", []):
        old = loops.get(l["index"])
        if old and (old["start"], old["end"]) != (l["start"], l["end"]):
            conflicts += 1
        loops[l["index"]] = dict(l, name=(old or {}).get("name", ""), locked=(old or l).get("locked", False))
    color = m2.get("color") if m2.get("color") is not None else legacy.get("color")
    return [cues[k] for k in sorted(cues)], [loops[k] for k in sorted(loops)], color, conflicts


def read(path):
    """Everything Serato stores in one file's tags (read only):
    dict(container, cues, loops, color, bpm_lock, flips, grid (markers), autotags, conflicts, has=set of tags found)."""
    raw = raw_tags(path)
    m2 = parse_markers2(raw.get("markers2"))
    legacy = parse_markers_legacy(raw.get("markers"))
    cues, loops, color, conflicts = merge_markers(m2, legacy)
    grid = parse_beatgrid(raw.get("beatgrid"))
    tempo_map = parse_tempo_map(raw.get("tempomap"))
    return dict(container=raw.get("container"), cues=cues, loops=loops, color=color, bpm_lock=m2.get("bpm_lock"), flips=m2.get("flips", 0),
                grid=grid or tempo_map, autotags=parse_autotags(raw.get("autotags")), conflicts=conflicts,
                has=sorted(k for k in raw if k != "container"), grid_source="beatgrid" if grid else "tempomap" if tempo_map else None)


# ------------------------------------------------------------------ beat grid -> beats
def grid_beats(markers, duration_s=None):
    """Serato beat-grid markers -> (beat times in seconds, tempo per beat, beat-in-bar per beat (1-4)).

    Between two markers there are exactly `beats` beats, evenly spaced; from the last marker the grid runs on at its BPM to the end of
    the track (duration_s; 10 minutes when unknown). Before the first marker it runs back to 0:00 at the first stretch's tempo. Each
    marker is a bar start (beat 1) and bars restart at every marker, as Serato draws them (unconfirmed for markers that are not a whole
    number of bars apart)."""
    if not markers or "bpm" not in markers[-1] or markers[-1]["bpm"] <= 0:
        return [], [], []
    end = float(duration_s) if duration_s else 600.0
    t, bpm, num = [], [], []
    segs = []
    for k, m in enumerate(markers[:-1]):
        nxt = markers[k + 1]["pos"]
        if m["beats"] <= 0 or nxt <= m["pos"]:
            continue
        segs.append((m["pos"], (nxt - m["pos"]) / m["beats"], m["beats"]))
    last = markers[-1]
    first_pos, first_iv = (segs[0][0], segs[0][1]) if segs else (last["pos"], 60.0 / last["bpm"])
    # before the first marker
    k = 1
    pre = []
    while first_pos - k * first_iv >= -1e-9:
        pre.append(first_pos - k * first_iv)
        k += 1
    for j, x in enumerate(reversed(pre)):
        t.append(max(x, 0.0)); bpm.append(60.0 / first_iv); num.append(((j - len(pre)) % 4) + 1)
    for pos, iv, n in segs:
        for j in range(n):
            t.append(pos + j * iv); bpm.append(60.0 / iv); num.append((j % 4) + 1)
    iv = 60.0 / last["bpm"]
    j = 0
    while last["pos"] + j * iv < end:
        t.append(last["pos"] + j * iv); bpm.append(last["bpm"]); num.append((j % 4) + 1)
        j += 1
    return t, bpm, num


def duration_of(path):
    from . import duration
    return duration.duration_s(path)


def read_beatgrid(path, duration_s=None, timeline="serato"):
    """Serato's beat grid of one audio file, on its own (no library needed).

    Returns None when the file has no Serato BeatGrid tag, else dict(markers=[{pos (s), beats | bpm}], beats=[seconds], bpms=[per beat],
    beat_in_bar=[1-4], bpm=the last marker's BPM, shift_ms=shift applied, timeline=...). timeline="serato" gives Serato's own positions;
    "audio" moves them onto the timeline players such as rekordbox and the CDJ use (timeline.shift_ms)."""
    raw = raw_tags(path)
    markers = parse_beatgrid(raw.get("beatgrid")) or parse_tempo_map(raw.get("tempomap"))
    if not markers:
        return None
    dur = duration_s or duration_of(path)
    beats, bpms, nums = grid_beats(markers, dur)
    shift = 0.0
    if timeline == "audio":
        from . import timeline as _timeline
        shift = _timeline.shift_ms(path)
        beats = [b + shift / 1000.0 for b in beats]
    return dict(markers=markers, beats=beats, bpms=bpms, beat_in_bar=nums, bpm=markers[-1].get("bpm"), shift_ms=shift, timeline=timeline)
