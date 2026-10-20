"""Length of an audio file in seconds, read from the file header with no extra libraries (WAV, AIFF, FLAC, MP3, M4A).

Used only to know where a beat grid ends. It is exact for WAV, AIFF, FLAC and M4A, and good to a frame for MP3."""
import os
import struct

from . import tags as _tags


def _read_at(f, off, n):
    f.seek(off)
    return f.read(n)


def _extended(b):
    """80-bit IEEE extended float (AIFF sample rate)."""
    if len(b) < 10:
        return 0.0
    exp = struct.unpack(">H", b[:2])[0]
    mant = struct.unpack(">Q", b[2:10])[0]
    sign = -1.0 if exp & 0x8000 else 1.0
    exp &= 0x7FFF
    if exp == 0 and mant == 0:
        return 0.0
    return sign * mant * 2.0 ** (exp - 16383 - 63)


def _wav(f, size):
    rate = block = data = None
    for cid, off, n in _tags._iff_chunks(f, size, False, 12):
        if cid == b"fmt " and n >= 16:
            _fmt, _ch, rate, _bps, block = struct.unpack("<HHIIH", _read_at(f, off, 14))
        elif cid == b"data":
            data = n if off + n <= size else size - off
            break
    return data / float(rate * block) if rate and block and data is not None else None


def _aiff(f, size):
    for cid, off, n in _tags._iff_chunks(f, size, True, 12):
        if cid == b"COMM" and n >= 18:
            b = _read_at(f, off, 18)
            frames = struct.unpack(">I", b[2:6])[0]
            rate = _extended(b[8:18])
            return frames / rate if rate else None
    return None


def _flac(f, size):
    head = _read_at(f, 0, 4)
    off = 4
    if head != b"fLaC":
        id3 = _tags._id3_at_start(f)
        if not id3 or _read_at(f, len(id3), 4) != b"fLaC":
            return None
        off = len(id3) + 4
    while True:
        h = _read_at(f, off, 4)
        if len(h) < 4:
            return None
        last, typ, n = h[0] & 0x80, h[0] & 0x7F, int.from_bytes(h[1:4], "big")
        if typ == 0 and n >= 34:
            b = _read_at(f, off + 4, 34)
            v = int.from_bytes(b[10:18], "big")
            rate = v >> 44
            total = v & ((1 << 36) - 1)
            return total / float(rate) if rate and total else None
        if last:
            return None
        off += 4 + n


def _mp4(f, size):
    for typ, a, b in _tags._mp4_atoms(f, 0, size):
        if typ == b"moov":
            for t2, a2, b2 in _tags._mp4_atoms(f, a, b):
                if t2 == b"mvhd":
                    raw = _read_at(f, a2, 32)
                    if raw[0] == 1:
                        scale, dur = struct.unpack(">IQ", raw[20:32])
                    else:
                        scale, dur = struct.unpack(">II", raw[12:20])
                    return dur / float(scale) if scale else None
    return None


def _mp3(f, size):
    head = _read_at(f, 0, 10)
    off = 0
    if head[:3] == b"ID3":
        off = 10 + ((head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9])
    buf = _read_at(f, off, 64 * 1024)
    for i in range(len(buf) - 4):
        if buf[i] == 0xFF and (buf[i + 1] & 0xE0) == 0xE0:
            h = struct.unpack_from(">I", buf, i)[0]
            ver, layer, br, sr = (h >> 19) & 3, (h >> 17) & 3, (h >> 12) & 15, (h >> 10) & 3
            if ver == 1 or layer != 1 or br in (0, 15) or sr == 3:
                continue
            mpeg1 = ver == 3
            rate = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}[ver][sr]
            spf = 1152 if mpeg1 else 576
            mono = ((h >> 6) & 3) == 3
            x = i + 4 + ((17 if mono else 32) if mpeg1 else (9 if mono else 17))
            if buf[x:x + 4] in (b"Xing", b"Info"):
                flags = struct.unpack_from(">I", buf, x + 4)[0]
                if flags & 1:
                    frames = struct.unpack_from(">I", buf, x + 8)[0]
                    return frames * spf / float(rate)
            kbps = _tags_bitrate(mpeg1, br)
            return max(size - off - i, 0) * 8 / (kbps * 1000.0) if kbps else None
    return None


def _tags_bitrate(mpeg1, idx):
    from . import timeline
    return timeline._BITRATES[mpeg1][idx]


def duration_s(path):
    """Seconds, or None when the file can't be read or the format is not one of the five above."""
    ext = os.path.splitext(str(path))[1].lower()
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if ext in (".wav", ".wave"):
                return _wav(f, size)
            if ext in (".aif", ".aiff", ".aifc"):
                return _aiff(f, size)
            if ext == ".flac":
                return _flac(f, size)
            if ext in (".m4a", ".mp4", ".m4b", ".aac"):
                return _mp4(f, size)
            if ext == ".mp3":
                return _mp3(f, size)
    except (OSError, struct.error, ValueError, IndexError):
        pass
    return None
