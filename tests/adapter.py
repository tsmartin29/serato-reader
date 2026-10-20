"""Adapter: lets shared_cases.py talk to serato_reader through plain dicts."""
import serato_reader as sr
from serato_reader import library, tags, timeline, tlv


def _track(t):
    return dict(path=t.path, title=t.title, artist=t.artist, album=t.album, genre=t.genre, bpm=t.bpm, key=t.key, date_added=(t.date_added_iso or "")[:10] or None)


def _smart(s):
    return dict(match_all=s.match_all, live=s.live,
                rules=[dict(field_id=r.field_id, cond=r.condition, text=r.text, number=r.number, date=r.date) for r in s.rules])


class R(object):
    tlv_parse = staticmethod(tlv.parse)
    read_tags = staticmethod(tags.read)
    parse_markers2 = staticmethod(tags.parse_markers2)
    parse_markers_legacy = staticmethod(tags.parse_markers_legacy)
    parse_beatgrid = staticmethod(tags.parse_beatgrid)
    parse_tempo_map = staticmethod(tags.parse_tempo_map)
    parse_autotags = staticmethod(tags.parse_autotags)
    grid_beats = staticmethod(tags.grid_beats)
    timeline_case = staticmethod(timeline.case_of)

    @staticmethod
    def load(folder, root="/"):
        lib = library.load_library(folder, root)
        return dict(tracks=[_track(t) for t in lib.tracks], crates=lib.crates, smart={n: _smart(s) for n, s in lib.smart_crates.items()},
                    errors=lib.errors)

    @staticmethod
    def find(home, volumes):
        return [(str(f), r) for f, r in library.find_libraries(home, volumes)]
