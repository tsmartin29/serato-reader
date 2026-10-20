# serato-reader

A read-only reader for Serato DJ libraries, in plain Python with no dependencies.

It reads what Serato stores on your computer and gives it back as simple Python objects (or JSON). It never writes to your Serato
folder or to your music files. It is not made by Serato and is not connected to them. "Serato" is their trademark.

## What it reads

- The track database ("database V2"): title, artist, album, genre, BPM, key, length, year, comment, grouping, label, colour, bitrate,
  sample rate, grid-locked flag, and Serato's own **Date Added**.
- **Crates**, in the order Serato shows them, including crates inside crates, empty crates, and crates with Unicode names.
- **Smart crates**: the rules, whether it matches all or any, and whether it updates live. It can also work out which tracks a smart
  crate matches.
- **Every drive**: your computer's own library and the `_Serato_` folder on each mounted drive, with paths turned into real file paths.
- **Cues, loops, colours and names** from inside the audio files (MP3, AIFF, WAV, M4A, FLAC, Ogg): both Serato's "Markers2" and the
  older "Markers_" tags, track colour, BPM lock.
- **Beat grids** as a list of every beat with the tempo at that beat, so tempo changes are kept. Reads the classic "BeatGrid" tag and
  the "Tempo Map" tag that Serato DJ 5.0 writes instead.
- **Autotags** (BPM and gain Serato measured).
- **Where Serato's 0:00 is.** For AAC / M4A files Serato counts from after the encoder's priming; players like rekordbox and CDJs
  count from before it. `shift_ms(path)` gives the difference, so positions copied to another player land on the right sample.

## Install and use

```
pip install .            # from this folder (not on PyPI yet)
```

```python
import serato_reader as sr

for lib in sr.load_all():                      # your Music library plus one per mounted drive
    for t in lib.tracks:
        print(t.artist, t.title, t.bpm, t.key, t.date_added_iso)
    print(lib.crates)                          # {"House%%Peak": ["/Volumes/Drive/a.mp3", ...]}
    print(lib.crate_tree())                    # nested view
    for name, smart in lib.smart_crates.items():
        print(name, smart.rules, [t.title for t in lib.smart_crate_tracks(name)])

tags = sr.read_tags("/path/song.mp3")          # cues, loops, color, grid, autotags
grid = sr.read_beatgrid("/path/song.m4a", timeline="audio")   # beats on the rekordbox / CDJ timeline
```

Command line:

```
serato-reader libraries
serato-reader tracks
serato-reader crates
serato-reader tags song.mp3
serato-reader grid song.m4a --audio
```

A nested crate is named `Parent%%Child`, as Serato names its crate files.

## How accurate is it? Checked against real Serato

Tested with **Serato DJ Lite 5.0 (build 5.0.0.1236) on macOS**, on made-up test audio (a click track at 124 BPM, a click on every
beat from exactly 1.000 s), on 19 October 2026.

| Check | Result |
|---|---|
| 4 hot cues set by hand in Serato, AIFF file: positions and colours | read back exactly: 17,343 / 26,900 / 7,786 / 33,929 ms, all four colours right |
| 1 hot cue, M4A (AAC) file | read back exactly |
| Serato's two cue tags ("Markers2" and "Markers_") for the same cues | agree, for both the AIFF layout and the M4A layout |
| Serato's grid against the real clicks, AIFF file | within 1.23 ms of a click on every beat (median 1.03 ms early: that is how Serato places its grid) |
| Same click track as AAC, Serato's grid after the encoder shift (+47.891 ms) | identical to the AIFF result, to 0.01 ms; without the shift it would be 47.9 ms off |
| Track database, crate, Date Added, key, BPM written by Serato | read back correctly |

A cue's stored position is a whole number of milliseconds, so what you get back is exactly what Serato stored (0 error). How close
that is to the audio depends on Serato itself, and the grid check above shows it is about a millisecond (about 45 samples at 44.1 kHz).

What this check did **not** cover, and why: MP3, FLAC, WAV and ALAC files, saved loops, smart crates made by Serato, and crates on a
second drive were not checked against real Serato. Serato DJ Lite wrote tags for only two of the test files in the session
(the rest needs hands-on clicking in the app, which was not possible in that run). For those, the reader follows the published format
notes and is tested against files written by this package's own independent test writer; MP3 timing in particular is **not yet
confirmed** (`timeline.CHECKED` lists what has been measured). The Tempo Map tag is read only when it holds one marker (the only
kind seen so far); a longer one is reported as unreadable rather than guessed. Serato DJ Pro was not tested.

## Speed

A made-up library of **40,000 tracks and 60 crates** (one crate holds all 40,000), 26 MB of Serato files, on an Apple-silicon Mac,
Python 3.9: **1.5 seconds** to read everything, 225 MB peak memory. Working out which tracks a smart crate matches takes 0.05 seconds.
Reproduce it with `python benchmarks/bench_40k.py`.

## Tests

**394 tests** (`pip install -e .` then `python -m unittest discover -s tests`, about 2 seconds). They include: files cut off at many points, broken base64 and
sizes, empty and nested crates, several drives, Unicode names and paths, grids with 50 tempo changes, a 20,000-track library, checks
that reading changes no byte of any file, and the real tag bytes Serato DJ Lite 5.0 wrote (`tests/fixtures/`). All test audio is
made up; no real music is included.

## Compared with serato-connect

[serato-connect](https://github.com/chrisle/serato-connect) by Chris Le is another open-source project that works with Serato. This
table is from reading its source code on 19 October 2026; it may have changed since. The projects aim at different things: it is built
around a live connection to a running Serato, this one is built around reading libraries accurately.

| | serato-reader | serato-connect |
|---|---|---|
| Language | Python, no dependencies | TypeScript / JavaScript |
| Licence | MIT | MIT |
| Live connection to a running Serato (network "Remote" mode) | No | Yes |
| Track database, crates, nested crates | Yes | Yes |
| Smart crates | Rules read; can match tracks | Mentioned in a comment, not read |
| Crates on external drives' `_Serato_` folders | Every mounted drive | Only `~/Music/_Serato_` |
| AAC / M4A encoder delay for cue and grid positions | Handled (checked against real Serato) | Not handled |
| Beat grid | Every beat with its tempo; BeatGrid and Serato 5 Tempo Map | Read as stored |
| Accuracy numbers published | Yes (above) | None found |
| Speed numbers published | Yes (above) | None found |
| Automated tests | 394 | 196 |

## Limits

- Read only, on purpose. There is no code that writes Serato files.
- No live connection to Serato.
- Smart-crate field numbers come from public format notes and have not been checked against smart crates made by Serato itself; a rule on a field it does not know never matches.
- Serato's own idea of "now" (play counts, history) is not read.
- Tested on macOS with Serato DJ Lite 5.0. Windows paths and Serato DJ Pro are untested.

## Licence

MIT. See `LICENSE`.
