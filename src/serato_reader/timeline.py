"""Where Serato's 0:00 is, file by file, against the timeline players like rekordbox and the CDJ use.

Serato stores every cue, loop and grid position in milliseconds counted from ITS idea of the start of the audio. Other players can
count from somewhere else, so a cue copied over unchanged lands a few milliseconds off. shift_ms(path) is the number of
milliseconds to ADD to a Serato position to get the position on the "player timeline": the decoded stream including the encoder's
priming, without the MP3 LAME / Xing info frame.

What is known, case by case:
  WAV, AIFF, FLAC, ALAC  no priming, every decoder agrees: 0.
                         (AIFF checked against real Serato DJ Lite 5.0.)
  AAC / M4A              Serato counts from the first sample AFTER the encoder priming (Apple's decoder removes it); the player
                         timeline keeps it, so the file's priming is added. Checked against real Serato DJ Lite 5.0: on the same click
                         track, Serato's grid for the AAC copy and the AIFF copy is in the same place to 0.01 ms once the 47.891 ms
                         (2,112 samples) are added to the AAC one.
  MP3                    0 in every case (LAME, Xing, VBRI, no info frame). From other players' public notes (Mixxx moves Serato
                         positions by one MP3 frame for decoders that play the info frame as silence). NOT yet checked against real
                         Serato DJ Lite 5.0: it did not write tags for the MP3 test files before the test stopped.

CHECKED holds the cases that have been measured against real Serato; the rest follow the notes above.
"""
import os
import pathlib
import struct

from . import tags as _tags

CHECKED = {"lossless_aiff", "aac"}
SHIFT = {                 # ms to add, per case; "priming" = the file's own encoder priming
    "lossless": 0.0,
    "mp3_lame": 0.0,      # LAME info frame with a valid CRC
    "mp3_lame_badcrc": 0.0,
    "mp3_xing": 0.0,      # Xing / Info frame without a LAME tag (or a Lavf / Lavc one)
    "mp3_vbri": 0.0,
    "mp3_plain": 0.0,     # no info frame
    "aac": "priming",
    "other": 0.0,
}
AAC_DEFAULT_PRIMING = 2112          # samples: what Apple's AAC encoder writes, used when the file does not say

_BITRATES = {  # (mpeg1?, layer3) kbps
    True: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0],
    False: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
}
_RATES = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def _crc16(data):
    """CRC-16/ARC, as LAME computes its tag CRC over the info frame up to the CRC itself."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def mp3_info(path):
    """dict(case, sample_rate, enc_delay, enc_padding, encoder) from the first MP3 frame (and its info frame, if any)."""
    with open(path, "rb") as f:
        head = f.read(10)
        off = 0
        if head[:3] == b"ID3" and len(head) >= 10:
            off = 10 + ((head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]) + (10 if head[5] & 0x10 else 0)
        f.seek(off)
        buf = f.read(64 * 1024)
    i = 0
    while i + 4 <= len(buf):
        if buf[i] == 0xFF and (buf[i + 1] & 0xE0) == 0xE0:
            h = struct.unpack_from(">I", buf, i)[0]
            ver = (h >> 19) & 3
            layer = (h >> 17) & 3
            br = (h >> 12) & 15
            sr = (h >> 10) & 3
            if ver != 1 and layer == 1 and br not in (0, 15) and sr != 3:
                break
        i += 1
    else:
        return dict(case="mp3_plain", sample_rate=None, enc_delay=None, enc_padding=None, encoder="")
    mpeg1 = ver == 3
    mono = ((h >> 6) & 3) == 3
    rate = _RATES[ver][sr]
    side = (17 if mono else 32) if mpeg1 else (9 if mono else 17)
    x = i + 4 + side
    out = dict(case="mp3_plain", sample_rate=rate, enc_delay=None, enc_padding=None, encoder="")
    tag = buf[x:x + 4]
    if tag in (b"Xing", b"Info"):
        flags = struct.unpack_from(">I", buf, x + 4)[0] if x + 8 <= len(buf) else 0
        p = x + 8 + (4 if flags & 1 else 0) + (4 if flags & 2 else 0) + (100 if flags & 4 else 0) + (4 if flags & 8 else 0)
        enc = buf[p:p + 9]
        out["encoder"] = enc.decode("latin-1", "ignore").strip("\0 ")
        if enc[:4] == b"LAME" or (enc[:2] == b"L3" and enc[2:3] == b"."):
            d = buf[p + 21:p + 24]
            if len(d) == 3:
                out["enc_delay"] = (d[0] << 4) | (d[1] >> 4)
                out["enc_padding"] = ((d[1] & 0x0F) << 8) | d[2]
            crc = struct.unpack_from(">H", buf, p + 34)[0] if p + 36 <= len(buf) else None
            ok = crc is not None and _crc16(buf[i:p + 34]) == crc
            out["case"] = "mp3_lame" if ok else "mp3_lame_badcrc"
        else:
            out["case"] = "mp3_xing"
    elif buf[i + 36:i + 40] == b"VBRI":
        out["case"] = "mp3_vbri"
    return out


def aac_priming(path):
    """Encoder priming of an M4A in samples: the iTunSMPB note if the file has one, else the edit list's start offset, else
    AAC_DEFAULT_PRIMING. Returns (samples, sample_rate or None, where it came from)."""
    rate = None
    try:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            media_time, rate = _elst_and_rate(f, size)
            for (mean, name), datas in _tags._mp4_freeform(f).items():
                if name == "iTunSMPB" and datas:
                    parts = datas[0].decode("latin-1", "ignore").split()
                    if len(parts) >= 3:
                        try:
                            return int(parts[1], 16), rate, "iTunSMPB"
                        except ValueError:
                            pass
            if media_time:
                return media_time, rate, "edit list"
    except (OSError, struct.error, IndexError):
        pass
    return AAC_DEFAULT_PRIMING, rate, "default"


def _elst_and_rate(f, size):
    """(media_time of the first non-empty edit, sample rate) from moov/trak/edts/elst and mdhd."""
    media_time, rate = 0, None

    def walk(start, end):
        nonlocal media_time, rate
        for typ, a, b in _tags._mp4_atoms(f, start, end):
            if typ in (b"moov", b"trak", b"mdia", b"edts"):
                walk(a, b)
            elif typ == b"mdhd" and rate is None:
                raw = _tags._read_at(f, a, 24)
                if raw[:1] == b"\x01" and len(raw) >= 24:
                    rate = struct.unpack_from(">I", raw, 20)[0]
                elif len(raw) >= 16:
                    rate = struct.unpack_from(">I", raw, 12)[0]
            elif typ == b"elst" and not media_time:
                raw = _tags._read_at(f, a, min(b - a, 64))
                version, count = raw[0], struct.unpack_from(">I", raw, 4)[0]
                step = 20 if version == 1 else 12
                for k in range(min(count, 4)):
                    o = 8 + k * step
                    if version == 1:
                        mt = struct.unpack_from(">q", raw, o + 8)[0]
                    else:
                        mt = struct.unpack_from(">i", raw, o + 4)[0]
                    if mt > 0:
                        media_time = mt
                        break

    try:
        walk(0, size)
    except (struct.error, IndexError):
        pass
    return media_time, rate


def case_of(path):
    ext = pathlib.Path(str(path)).suffix.lower()
    if ext in (".wav", ".wave", ".aif", ".aiff", ".aifc", ".flac"):
        return "lossless"
    if ext == ".mp3":
        try:
            return mp3_info(path)["case"]
        except OSError:
            return "mp3_plain"
    if ext in (".m4a", ".mp4", ".aac", ".m4b"):
        return "aac" if not _is_alac(path) else "lossless"
    return "other"


def _is_alac(path):
    try:
        with open(path, "rb") as f:
            head = f.read(min(os.path.getsize(path), 1 << 20))
        return b"alac" in head
    except OSError:
        return False


def shift_for(case, path):
    v = SHIFT.get(case, 0.0)
    if v == "priming":
        samples, rate, _ = aac_priming(path)
        return round(samples * 1000.0 / float(rate or 44100), 3)
    return float(v)


def shift_ms(path):
    """Milliseconds to ADD to a Serato position in this file to get the player-timeline position (0 when the file can't be read)."""
    return shift_for(case_of(path), path)
