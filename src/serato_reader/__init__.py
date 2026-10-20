"""serato-reader: a read-only reader for Serato DJ libraries.

    import serato_reader as sr
    for lib in sr.load_all():                         # the Mac's own library and one per mounted drive
        for track in lib.tracks: ...
    tags = sr.read_tags("/path/to/song.mp3")          # cues, loops, colours, grid, autotags

Nothing in this package writes to Serato's files or to your audio files.
"""
from .library import (CRATE_SEP, Library, Rule, SmartCrate, Track, find_libraries, load_all, load_library, parse_smart_crate,
                      resolve_existing)
from .tags import grid_beats, read as read_tags, read_beatgrid
from .timeline import shift_ms

__version__ = "0.1.0"
__all__ = ["CRATE_SEP", "Library", "Rule", "SmartCrate", "Track", "find_libraries", "load_all", "load_library", "parse_smart_crate",
           "resolve_existing", "grid_beats", "read_tags", "read_beatgrid", "shift_ms", "__version__"]
