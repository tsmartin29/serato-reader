# Serato file format notes

What this package reads, as plain notes. Sources: Holzhaus' public "serato-tags" notes (documentation CC BY-SA 4.0; no code copied) and
files written by Serato DJ Lite 5.0 on 19 October 2026.

## Library files (`_Serato_` folder)

`database V2`, `Subcrates/*.crate` and `SmartCrates/*.scrate` all use one layout: a 4-letter tag, a big-endian u32 length, the value.
The first letter of the tag gives the type: `o`/`r` nested fields, `t`/`p` UTF-16 big-endian text, `u` u32, `s` u16, `b` one-byte
boolean (`vrsn` is text too). Paths are relative to the drive the `_Serato_` folder is on, with no leading slash.
A crate inside a crate is a file named `Parent%%Child.crate`.

Track fields used: `pfil` path, `ttyp` type, `tsng` title, `tart` artist, `talb` album, `tgen` genre, `tcom` comment, `tgrp` grouping,
`tlbl` label, `tcmp` composer, `trmx` remixer, `ttyr` year, `tbpm` BPM, `tkey` key, `tlen` length, `tsiz` size, `tbit` bitrate,
`tsmp` sample rate, `uadd` Date Added (Unix seconds; `tadd` in older versions), `ulbl` colour, `bbgl` grid locked, `bmis` missing.

Smart crate rules (`rurt`): `urkt` field number, `trft` condition, `trpt` / `urpt` / `trtt` text / number / date value; `rart` match all,
`rlut` live update.

## Tags inside audio files

| Kind | MP3 / AIFF / WAV (ID3 GEOB) | M4A (`----:com.serato.dj:`) | FLAC (Vorbis comment) |
|---|---|---|---|
| Cues, loops, colours, names | Serato Markers2 | markersv2 | SERATO_MARKERS_V2 |
| Older cues and loops | Serato Markers_ | markers | SERATO_MARKERS |
| Beat grid | Serato BeatGrid | beatgrid | SERATO_BEATGRID |
| Tempo map (Serato DJ 5.0) | Serato Tempo Map | tempomap | SERATO_TEMPOMAP (assumed) |
| BPM and gain | Serato Autotags | autgain | SERATO_AUTOGAIN |

M4A and FLAC hold base64 text that decodes to `application/octet-stream`, a null, the tag name and the same payload as the GEOB frame.

**Markers2**: `01 01`, then base64 text of `01 01`, entries (name, null, u32 length, data), `00`. CUE: `[1]` index, `[2:6]` position in ms,
`[7:10]` RGB, `[12:]` name. LOOP: `[1]` index, `[2:6]` start ms, `[6:10]` end ms, `[15:18]` RGB, `[19]` locked, `[20:]` name. COLOR: track colour.
BPMLOCK. FLIP (saved flips, counted only).

**Markers_**: `02 05`, u32 count, entries, track colour. In ID3 each entry is 22 bytes in "serato32" (7 bits per byte); in M4A each entry
is 19 bytes, plain (confirmed with a real Serato 5.0 file). Entries 0-4 are hot cues 1-5, entries 5-13 saved loops.

**BeatGrid**: `01 00`, u32 marker count, markers (f32 seconds + u32 beats to the next marker; the last one f32 seconds + f32 BPM), one byte.

**Tempo Map** (new in Serato DJ 5.0, seen in real files): `02 00`, u16 marker count, then for the one marker seen: f64 position in
seconds, u32 `1`, 8 zero bytes, f64 BPM, f64 `0.25`, `01`, 8 zero bytes (big endian). Only the one-marker form is read.

## Where 0:00 is

See `timeline.py`. Lossless files: no offset. AAC / M4A: Serato counts from after the encoder priming (iTunSMPB, else the edit list,
else 2,112 samples); add that to get the position other players use. Checked against real Serato DJ Lite 5.0 for AIFF and AAC; MP3
is assumed to need none and has not been measured.
