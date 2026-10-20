"""Command line: print what is in a Serato library as JSON. Read only.

  serato-reader libraries                      every library found (your Music folder and each mounted drive)
  serato-reader tracks [FOLDER]                tracks of one _Serato_ folder (or all libraries)
  serato-reader crates [FOLDER]                crates (nested ones as Parent%%Child) with their tracks, and smart crates with rules
  serato-reader tags FILE                      cues, loops, colours, grid and autotags stored in one audio file
  serato-reader grid FILE [--audio]            beat times and the tempo of every beat (--audio: on the rekordbox / CDJ timeline)
"""
import argparse
import json
import sys

from . import library, tags, timeline


def _libs(folder):
    if folder:
        return [library.load_library(folder, "/")]
    return library.load_all()


def _track_dict(t):
    return dict(path=t.path, title=t.title, artist=t.artist, album=t.album, genre=t.genre, bpm=t.bpm, key=t.key, length_s=t.length_s,
                year=t.year, color=t.color, date_added=t.date_added_iso, grid_locked=t.grid_locked)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="serato-reader", description="Read-only reader for Serato DJ libraries.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("libraries")
    for name in ("tracks", "crates"):
        p = sub.add_parser(name)
        p.add_argument("folder", nargs="?")
    p = sub.add_parser("tags")
    p.add_argument("file")
    p = sub.add_parser("grid")
    p.add_argument("file")
    p.add_argument("--audio", action="store_true", help="positions on the player timeline (adds the AAC priming)")
    a = ap.parse_args(argv)
    if a.cmd == "libraries":
        out = [dict(folder=str(f), root=r) for f, r in library.find_libraries()]
    elif a.cmd == "tracks":
        out = [dict(folder=str(l.folder), tracks=[_track_dict(t) for t in l.tracks], errors=l.errors) for l in _libs(a.folder)]
    elif a.cmd == "crates":
        out = [dict(folder=str(l.folder), crates=l.crates,
                    smart_crates={n: dict(match_all=s.match_all, live=s.live, rules=[r.as_dict() for r in s.rules])
                                  for n, s in l.smart_crates.items()}, errors=l.errors) for l in _libs(a.folder)]
    elif a.cmd == "tags":
        out = tags.read(a.file)
        out["has"] = list(out["has"])
        out["shift_ms"] = timeline.shift_ms(a.file)
    else:
        out = tags.read_beatgrid(a.file, timeline="audio" if a.audio else "serato")
    json.dump(out, sys.stdout, indent=1, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
