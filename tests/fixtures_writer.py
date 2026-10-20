"""Writes small synthetic Serato libraries and tagged audio files for the tests, with the standard library only.

It follows the public format notes and the real files Serato DJ Lite 5.0 wrote (tests/fixtures/real_serato_5_tags.json), and is kept
separate from the reader on purpose: the reader is checked against a second, independent implementation of the format.
The audio is made-up test audio (clicks and silence), written by hand: no encoder is needed."""
import base64
import math
import pathlib
import struct
import zlib


# ------------------------------------------------------------------ TLV (database V2, crates, smart crates)
def tlv(tag, value):
    t = tag.encode("latin-1")
    if isinstance(value, list):
        raw = b"".join(tlv(k, v) for k, v in value)
    elif isinstance(value, bool):
        raw = b"\x01" if value else b"\x00"
    elif isinstance(value, int):
        raw = struct.pack(">I", value) if tag[0] == "u" else struct.pack(">H", value) if tag[0] == "s" else bytes([value])
    elif isinstance(value, bytes):
        raw = value
    else:
        raw = str(value).encode("utf-16-be")
    return t + struct.pack(">I", len(raw)) + raw


def database(tracks):
    out = tlv("vrsn", "2.0/Serato Scratch LIVE Database")
    for t in tracks:
        out += tlv("otrk", list(t.items()))
    return out


def crate(paths):
    out = tlv("vrsn", "1.0/Serato ScratchLive Crate")
    out += tlv("osrt", [("tvcn", "song"), ("brev", False)])
    out += tlv("ovct", [("tvcn", "song"), ("tvcw", "0")])
    for p in paths:
        out += tlv("otrk", [("ptrk", p)])
    return out


def smart_crate(rules, match_all=True, live=True):
    """rules: list of (field id, condition, value)."""
    out = tlv("vrsn", "1.0/Serato ScratchLive Smart Crate") + tlv("rart", 1 if match_all else 0) + tlv("rlut", 1 if live else 0)
    for f, cond, v in rules:
        r = [("trft", cond), ("urkt", f)]
        r.append(("urpt", int(v)) if isinstance(v, int) else ("trpt", v))
        out += tlv("rurt", r)
    return out


def make_library(root, tracks=(), crates=None, smarts=None, drive_root=None):
    """Writes root/_Serato_ with a database, Subcrates/*.crate and SmartCrates/*.scrate. Returns the _Serato_ folder.
    tracks: list of dicts of database fields; crates: {file stem: [relative paths]}; smarts: {stem: bytes}."""
    folder = pathlib.Path(root) / "_Serato_"
    (folder / "Subcrates").mkdir(parents=True, exist_ok=True)
    (folder / "SmartCrates").mkdir(parents=True, exist_ok=True)
    (folder / "database V2").write_bytes(database(tracks))
    for stem, paths in (crates or {}).items():
        (folder / "Subcrates" / (stem + ".crate")).write_bytes(crate(paths))
    for stem, raw in (smarts or {}).items():
        (folder / "SmartCrates" / (stem + ".scrate")).write_bytes(raw)
    return folder


# ------------------------------------------------------------------ tag payloads
def _b64_lines(raw, width=72):
    s = base64.b64encode(raw).decode().rstrip("=")
    return "\n".join(s[i:i + width] for i in range(0, len(s), width)).encode()


def markers2(cues=(), loops=(), color=0xFFFFFF, bpm_lock=False, flips=0):
    """cues: (index, ms, rgb, name); loops: (index, start, end, rgb, name, locked)."""
    body = b"\x01\x01"

    def entry(name, data):
        return name.encode() + b"\0" + struct.pack(">I", len(data)) + data
    body += entry("COLOR", b"\0" + color.to_bytes(3, "big"))
    for idx, ms, rgb, name in cues:
        body += entry("CUE", b"\0" + bytes([idx]) + struct.pack(">I", ms) + b"\0" + rgb.to_bytes(3, "big") + b"\0\0" + name.encode() + b"\0")
    for idx, s, e, rgb, name, locked in loops:
        body += entry("LOOP", b"\0" + bytes([idx]) + struct.pack(">II", s, e) + b"\xff\xff\xff\xff" + b"\0" + rgb.to_bytes(3, "big")
                      + b"\0" + bytes([1 if locked else 0]) + name.encode() + b"\0")
    for k in range(flips):
        body += entry("FLIP", b"\0" + bytes([k]) + b"\0" + b"F\0" + b"\0" + struct.pack(">I", 0))
    body += entry("BPMLOCK", bytes([1 if bpm_lock else 0]))
    body += b"\0"
    payload = b"\x01\x01" + _b64_lines(body)
    return payload + b"\0" * max(0, 470 - len(payload))


def s32(v):
    a, b, c = (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF
    return bytes([(a >> 5) & 0x7F, ((b >> 6) | (a << 2)) & 0x7F, ((c >> 7) | (b << 1)) & 0x7F, c & 0x7F])


def markers_legacy(cues=(), loops=(), color=0xFFFFFF):
    """ID3 Markers_: 5 cue slots + 9 loop slots of 22 bytes (serato32), then the track colour."""
    out = b"\x02\x05" + struct.pack(">I", 14)
    cmap = {i: (ms, rgb) for i, ms, rgb in cues}
    lmap = {i: (s, e, rgb) for i, s, e, rgb in loops}
    for k in range(14):
        if k < 5 and k in cmap:
            ms, rgb = cmap[k]
            out += b"\0" + s32(ms) + b"\x7f" + b"\x7f\x7f\x7f\x7f" + b"\0\x7f\x7f\x7f\x7f\x7f" + s32(rgb) + b"\x01\x00"
        elif k >= 5 and (k - 5) in lmap:
            s, e, rgb = lmap[k - 5]
            out += b"\0" + s32(s) + b"\0" + s32(e) + b"\0\x7f\x7f\x7f\x7f\x7f" + s32(rgb) + b"\x03\x00"
        else:
            out += b"\x7f" + b"\x7f\x7f\x7f\x7f" + b"\x7f" + b"\x7f\x7f\x7f\x7f" + b"\0\x7f\x7f\x7f\x7f\x7f" + s32(0) + (b"\x01" if k < 5 else b"\x03") + b"\x00"
    return out + s32(color)


def beatgrid(markers):
    """markers: [(pos seconds, beats)] ... then (pos, bpm) as the last."""
    out = b"\x01\x00" + struct.pack(">I", len(markers))
    for k, (pos, v) in enumerate(markers):
        out += struct.pack(">f", pos) + (struct.pack(">f", v) if k == len(markers) - 1 else struct.pack(">I", v))
    return out + b"\x37"


def tempo_map(pos, bpm):
    """Serato DJ 5.0 'Tempo Map' with one marker, laid out as in real files."""
    return (b"\x02\x00" + struct.pack(">H", 1) + struct.pack(">d", pos) + struct.pack(">I", 1) + b"\0" * 8 + struct.pack(">d", bpm)
            + struct.pack(">d", 0.25) + b"\x01" + b"\0" * 8)


def autotags(bpm, autogain=-3.257, gain=0.0):
    return b"\x01\x01" + ("%.2f" % bpm).encode() + b"\0" + ("%.3f" % autogain).encode() + b"\0" + ("%.3f" % gain).encode() + b"\0"


def wrapped(name, payload):
    """The base64 container form (MP4 atoms, FLAC comments): header + payload, base64 without padding."""
    raw = b"application/octet-stream\0\0" + name.encode() + b"\0" + payload
    return _b64_lines(raw)


# ------------------------------------------------------------------ containers
def _ss(n):
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def id3v24(geobs, text=None):
    frames = b""
    for fid, v in (text or {}).items():
        body = b"\x03" + v.encode() + b"\0"
        frames += fid.encode() + _ss(len(body)) + b"\0\0" + body
    for desc, data in geobs.items():
        body = b"\x00" + b"application/octet-stream\0" + b"\0" + desc.encode("latin-1") + b"\0" + data
        frames += b"GEOB" + _ss(len(body)) + b"\0\0" + body
    frames += b"\0" * 64
    return b"ID3\x04\x00\x00" + _ss(len(frames)) + frames


def id3v23(geobs):
    """ID3v2.3: plain big-endian frame sizes."""
    frames = b""
    for desc, data in geobs.items():
        body = b"\x00" + b"application/octet-stream\0" + b"\0" + desc.encode("latin-1") + b"\0" + data
        frames += b"GEOB" + struct.pack(">I", len(body)) + b"\0\0" + body
    frames += b"\0" * 16
    return b"ID3\x03\x00\x00" + _ss(len(frames)) + frames


def silence_pcm(seconds, sr=44100, channels=1):
    return b"\0\0" * int(seconds * sr) * channels


def click_pcm(times_s, seconds, sr=44100):
    n = int(seconds * sr)
    x = [0] * n
    for t in times_s:
        i = int(round(t * sr))
        for k in range(40):
            if i + k < n:
                x[i + k] = int(30000 * math.exp(-k / 8.0))
    return b"".join(struct.pack("<h", v) for v in x)


def make_wav(path, pcm=None, sr=44100, tag=None):
    pcm = silence_pcm(1.0, sr) if pcm is None else pcm
    fmt = struct.pack("<HHIIHH", 1, 1, sr, sr * 2, 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(pcm)) + pcm
    if tag is not None:
        body += b"id3 " + struct.pack("<I", len(tag)) + tag + (b"\0" if len(tag) & 1 else b"")
    pathlib.Path(path).write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def _ext80(v):
    e = int(math.floor(math.log2(v)))
    m = int(v / 2 ** e * (1 << 63))
    return struct.pack(">HQ", e + 16383, m)


def make_aiff(path, frames=44100, tag=None, sr=44100):
    pcm = b"\0\0" * frames
    comm = b"COMM" + struct.pack(">I", 18) + struct.pack(">hIh", 1, frames, 16) + _ext80(sr)
    ssnd = b"SSND" + struct.pack(">I", len(pcm) + 8) + struct.pack(">II", 0, 0) + pcm
    body = b"AIFF" + comm + ssnd
    if tag is not None:
        body += b"ID3 " + struct.pack(">I", len(tag)) + tag + (b"\0" if len(tag) & 1 else b"")
    pathlib.Path(path).write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)


def _crc16(data):
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def mp3_info_frame(kind="lame", delay=576, padding=1105, info_frames=40, good_crc=True):
    """The first frame of an MP3 file: 128 kbps, 44.1 kHz, stereo MPEG-1 Layer III. kind: lame, xing, vbri, none (a plain audio frame)."""
    hdr = bytes([0xFF, 0xFB, 0x90, 0x00])
    body = bytearray(417 - 4)
    if kind in ("lame", "xing"):
        body[32:36] = b"Info" if kind == "lame" else b"Xing"
        struct.pack_into(">I", body, 36, 1 | 2)
        struct.pack_into(">I", body, 40, info_frames)
        struct.pack_into(">I", body, 44, 400000)
        p = 48
        if kind == "lame":
            body[p:p + 9] = b"LAME3.100"
            body[p + 21:p + 24] = bytes([(delay >> 4) & 0xFF, ((delay & 0xF) << 4) | ((padding >> 8) & 0xF), padding & 0xFF])
            frame = hdr + bytes(body)
            crc = _crc16(frame[0:p + 4 + 34])
            body[p + 34:p + 36] = struct.pack(">H", crc if good_crc else (crc ^ 0xFFFF))
        else:
            body[p:p + 9] = b"Lavf58.76"
    elif kind == "vbri":
        body[32:36] = b"VBRI"
    return hdr + bytes(body)


def make_mp3(path, tag=b"", info="lame", frames=20, **kw):
    first = mp3_info_frame(info if info != "none" else "none", **kw)
    audio = b"".join(bytes([0xFF, 0xFB, 0x90, 0x00]) + bytes(413) for _ in range(frames))
    pathlib.Path(path).write_bytes(tag + first + audio)


# ---- MP4
def _atom(typ, body):
    return struct.pack(">I", 8 + len(body)) + typ + body


def _freeform(name, value, mean="com.serato.dj"):
    m = _atom(b"mean", b"\0\0\0\0" + mean.encode())
    nm = _atom(b"name", b"\0\0\0\0" + name.encode())
    dat = _atom(b"data", struct.pack(">II", 1, 0) + value)
    return _atom(b"----", m + nm + dat)


def make_m4a(path, atoms=None, itunsmpb=None, elst_media_time=None, rate=44100, duration_s=1.0, extra_ilst=b""):
    """A tiny M4A: ftyp, moov(mvhd, trak(edts, mdia(mdhd)), udta(meta(ilst))), mdat. atoms: {name: payload} -> ----:com.serato.dj:<name>."""
    names = {"markersv2": "Serato Markers2", "markers": "Serato Markers_", "beatgrid": "Serato BeatGrid", "autgain": "Serato Autotags",
             "tempomap": "Serato Tempo Map"}
    ilst_body = b"".join(_freeform(k, wrapped(names.get(k, k), v)) for k, v in (atoms or {}).items()) + extra_ilst
    if itunsmpb is not None:
        ilst_body += _freeform("iTunSMPB", itunsmpb.encode(), mean="com.apple.iTunes")
    ilst = _atom(b"ilst", ilst_body)
    hdlr = _atom(b"hdlr", b"\0\0\0\0" + b"\0\0\0\0" + b"mdir" + b"appl" + b"\0" * 9)
    meta = _atom(b"meta", b"\0\0\0\0" + hdlr + ilst)
    udta = _atom(b"udta", meta)
    mvhd = _atom(b"mvhd", b"\0" * 12 + struct.pack(">II", rate, int(duration_s * rate)) + b"\0" * 80)
    mdhd = _atom(b"mdhd", b"\0" * 12 + struct.pack(">II", rate, int(duration_s * rate)) + b"\0\0\0\0")
    trak_kids = b""
    if elst_media_time is not None:
        elst = _atom(b"elst", b"\0\0\0\0" + struct.pack(">I", 1) + struct.pack(">IiHH", int(duration_s * 1000), elst_media_time, 1, 0))
        trak_kids += _atom(b"edts", elst)
    trak_kids += _atom(b"mdia", mdhd)
    trak = _atom(b"trak", trak_kids)
    moov = _atom(b"moov", mvhd + trak + udta)
    ftyp = _atom(b"ftyp", b"M4A \0\0\0\0M4A mp42isom")
    pathlib.Path(path).write_bytes(ftyp + moov + _atom(b"mdat", b"\0" * 16))


# ---- FLAC / Ogg
def make_flac(path, comments, rate=44100, total=44100, id3=b""):
    v = (rate << 44) | (0 << 41) | (15 << 36) | total
    si = struct.pack(">HH", 4096, 4096) + b"\0" * 6 + v.to_bytes(8, "big") + b"\0" * 16
    vendor = b"serato-reader-test"
    vc = struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(comments))
    for k, val in comments.items():
        kv = k.encode() + b"=" + val
        vc += struct.pack("<I", len(kv)) + kv
    out = id3 + b"fLaC"
    out += bytes([0]) + len(si).to_bytes(3, "big") + si
    out += bytes([0x80 | 4]) + len(vc).to_bytes(3, "big") + vc
    pathlib.Path(path).write_bytes(out + b"\xff\xf8" + b"\0" * 32)


def _ogg_page(serial, seq, packet, flags=0):
    segs = []
    n = len(packet)
    while n >= 255:
        segs.append(255)
        n -= 255
    segs.append(n)
    return b"OggS" + bytes([0, flags]) + struct.pack("<qIII", 0, serial, seq, 0) + bytes([len(segs)]) + bytes(segs) + packet


def make_ogg(path, comments):
    ident = b"\x01vorbis" + struct.pack("<IBIiiiB", 0, 2, 44100, 0, 192000, 0, 0xB8) + b"\x01"
    vendor = b"serato-reader-test"
    vc = b"\x03vorbis" + struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(comments))
    for k, val in comments.items():
        kv = k.encode() + b"=" + val
        vc += struct.pack("<I", len(kv)) + kv
    vc += b"\x01"
    pathlib.Path(path).write_bytes(_ogg_page(7, 0, ident, 2) + _ogg_page(7, 1, vc))


def mp3_tag(cues=(), loops=(), grid=None, tempo=None, auto=None, color=0xFFFFFF):
    g = {"Serato Markers2": markers2(cues, loops, color), "Serato Markers_": markers_legacy([(i, ms, rgb) for i, ms, rgb, _n in cues],
         [(i, s, e, rgb) for i, s, e, rgb, _n, _l in loops], color)}
    if grid:
        g["Serato BeatGrid"] = beatgrid(grid)
    if tempo:
        g["Serato Tempo Map"] = tempo_map(*tempo)
    if auto:
        g["Serato Autotags"] = autotags(auto)
    return id3v24(g)
