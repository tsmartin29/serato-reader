"""Tests for things only serato_reader has: its objects, smart-crate matching, the timeline numbers, the duration reader, the CLI."""
import io
import json
import os
import pathlib
import contextlib
import shutil
import sys
import tempfile
import unicodedata
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fixtures_writer as W
import serato_reader as sr
from serato_reader import cli, duration, library, tags, timeline

HERE = pathlib.Path(__file__).resolve().parent


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="serato_reader_pkg_"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)


class TrackObjects(Base):
    def lib(self, tracks, **kw):
        return sr.load_library(W.make_library(self.tmp, tracks, **kw), "/")

    def test_attributes(self):
        l = self.lib([dict(pfil="Users/dj/a.mp3", tsng="T", tart="A", tbpm="123.45", tlen="01:02.50", tsmp="44.1k", tbit="320.0kbps",
                           ttyr="2024", utkn=3, ulbl=0xFF0000, bbgl=True, uadd=1700000000, ufsb=1234)])
        t = l.tracks[0]
        self.assertEqual((t.year, t.track_number, t.color, t.grid_locked, t.file_size), (2024, 3, 0xFF0000, True, 1234))
        self.assertAlmostEqual(t.length_s, 62.5)
        self.assertEqual((t.sample_rate, t.bitrate_kbps), (44100.0, 320.0))

    def test_date_added_iso_is_utc(self):
        l = self.lib([dict(pfil="a.mp3", uadd=1700000000)])
        self.assertEqual(l.tracks[0].date_added_iso, "2023-11-14T22:13:20Z")

    def test_date_added_iso_none(self):
        self.assertIsNone(self.lib([dict(pfil="a.mp3")]).tracks[0].date_added_iso)

    def test_raw_keeps_every_field(self):
        l = self.lib([dict(pfil="a.mp3", tzzz="kept")])
        self.assertEqual(l.tracks[0].raw["tzzz"], "kept")

    def test_missing_file_is_flagged_by_exists(self):
        l = self.lib([dict(pfil="definitely/not/here.mp3")])
        self.assertFalse(l.tracks[0].exists())

    def test_existing_file(self):
        f = self.tmp / "song.mp3"
        f.write_bytes(b"x")
        l = sr.load_library(W.make_library(self.tmp / "lib", [dict(pfil=str(f).lstrip("/"))]), "/")
        self.assertTrue(l.tracks[0].exists())

    def test_nfd_name_on_disk_found_from_nfc_path(self):
        name = "Café.mp3"
        (self.tmp / unicodedata.normalize("NFD", name)).write_bytes(b"x")
        found = library.resolve_existing(str(self.tmp / unicodedata.normalize("NFC", name)))
        # on a file system that keeps names as written, the NFD file is only found when the two spell the same
        if (self.tmp / unicodedata.normalize("NFC", name)).exists():
            self.assertIsNotNone(found)
        else:
            self.assertIsNone(found)

    def test_track_by_path(self):
        l = self.lib([dict(pfil="a.mp3", tsng="A"), dict(pfil="b.mp3", tsng="B")])
        self.assertEqual(l.track_by_path("/b.mp3").title, "B")
        self.assertIsNone(l.track_by_path("/c.mp3"))

    def test_year_field(self):
        l = self.lib([dict(pfil="a.mp3", ttyr="2001"), dict(pfil="b.mp3", ttyr="1999-05-01"), dict(pfil="c.mp3")])
        self.assertEqual([t.year for t in l.tracks], [2001, 1999, None])

    def test_num_helper(self):
        self.assertEqual(library.num("320.0kbps"), 320.0)
        self.assertEqual(library.num("44.1k"), 44100.0)
        self.assertEqual(library.num("9.8MB"), 9.8 * 1048576)
        self.assertIsNone(library.num("abc"))
        self.assertIsNone(library.num(None))

    def test_length_helper(self):
        self.assertEqual(library.length_s("1:02:03"), 3723.0)
        self.assertEqual(library.length_s("05:23.45"), 323.45)
        self.assertIsNone(library.length_s("x"))


class Crates(Base):
    def test_crate_tree_nests(self):
        f = W.make_library(self.tmp, crates={"A": ["1.mp3"], "A%%B": ["2.mp3"], "A%%B%%C": ["3.mp3"], "Z%%Y": []})
        tree = sr.load_library(f, "/").crate_tree()
        self.assertEqual(tree["A"]["tracks"], ["/1.mp3"])
        self.assertEqual(tree["A"]["children"]["B"]["children"]["C"]["tracks"], ["/3.mp3"])
        self.assertEqual(tree["Z"]["tracks"], [])
        self.assertIn("Y", tree["Z"]["children"])

    def test_missing_folder_reports_an_error(self):
        l = sr.load_library(self.tmp / "nope")
        self.assertEqual(len(l.errors), 1)

    def test_load_all(self):
        W.make_library(self.tmp / "h", [dict(pfil="a.mp3")])
        libs = sr.load_all(self.tmp / "h" / "_Serato_", self.tmp / "none")
        self.assertEqual(len(libs), 1)


class SmartCrates(Base):
    def crate(self, rules, match_all=True):
        f = W.make_library(self.tmp, [
            dict(pfil="a.mp3", tsng="Deep House Mix", tart="Alpha", tgen="House", tbpm="124.00", ttyr="2020", tkey="Am", uadd=1700000000),
            dict(pfil="b.mp3", tsng="Techno Beast", tart="Beta", tgen="Techno", tbpm="140.00", ttyr="2023", tkey="C", uadd=1600000000),
            dict(pfil="Sub/c.mp3", tsng="Other", tart="Alpha", tgen="Pop", tbpm="100.00", ttyr="2010", tkey="Dm", uadd=1500000000)],
            smarts={"S": W.smart_crate(rules, match_all)})
        l = sr.load_library(f, "/")
        return [t.title for t in l.smart_crate_tracks("S")]

    def test_contains(self):
        self.assertEqual(self.crate([(6, "cond_con_str", "house")]), ["Deep House Mix"])

    def test_does_not_contain(self):
        self.assertEqual(self.crate([(6, "cond_dnc_str", "house")]), ["Techno Beast", "Other"])

    def test_is_and_is_not(self):
        self.assertEqual(self.crate([(7, "cond_is_str", "alpha")]), ["Deep House Mix", "Other"])
        self.assertEqual(self.crate([(7, "cond_isn_str", "alpha")]), ["Techno Beast"])

    def test_bpm_range_all(self):
        self.assertEqual(self.crate([(15, "cond_greq_uint", 120), (15, "cond_lseq_uint", 130)]), ["Deep House Mix"])

    def test_bpm_any(self):
        self.assertEqual(self.crate([(15, "cond_lseq_uint", 100), (15, "cond_greq_uint", 140)], match_all=False), ["Techno Beast", "Other"])

    def test_year(self):
        self.assertEqual(self.crate([(23, "cond_greq_uint", 2020)]), ["Deep House Mix", "Techno Beast"])

    def test_filename(self):
        self.assertEqual(self.crate([(4, "cond_con_str", "c.mp3")]), ["Other"])

    def test_added_after_date_number(self):
        self.assertEqual(self.crate([(25, "cond_aft_time", 1650000000)]), ["Deep House Mix"])

    def test_unknown_field_never_matches(self):
        self.assertEqual(self.crate([(999, "cond_is_str", "x")]), [])

    def test_plays_is_not_known_to_the_reader(self):
        self.assertEqual(self.crate([(79, "cond_greq_uint", 0)]), [])

    def test_empty_rules_match_nothing(self):
        self.assertEqual(self.crate([]), [])

    def test_rule_as_dict_names_the_field(self):
        f = W.make_library(self.tmp, smarts={"S": W.smart_crate([(15, "cond_greq_uint", 120)])})
        r = sr.load_library(f, "/").smart_crates["S"].rules[0].as_dict()
        self.assertEqual((r["field"], r["operator"], r["value"]), ("bpm", ">=", 120))


class Timeline(Base):
    def m4a(self, **kw):
        p = self.tmp / "a.m4a"
        W.make_m4a(p, **kw)
        return p

    def test_aac_priming_from_itunsmpb(self):
        p = self.m4a(itunsmpb=" 00000000 00000840 00000120 000000000000AC44 00000000")
        self.assertEqual(timeline.aac_priming(p)[0::2], (2112, "iTunSMPB"))
        self.assertAlmostEqual(timeline.shift_ms(p), 47.891, places=3)

    def test_aac_priming_other_value(self):
        p = self.m4a(itunsmpb=" 00000000 00000400 00000100 0000000000001000 00000000")
        self.assertAlmostEqual(timeline.shift_ms(p), 1024 / 44.1, places=3)

    def test_aac_priming_at_48k(self):
        p = self.m4a(itunsmpb=" 00000000 00000840 00000120 0000000000001000 00000000", rate=48000)
        self.assertAlmostEqual(timeline.shift_ms(p), 44.0, places=3)

    def test_aac_priming_from_edit_list(self):
        p = self.m4a(elst_media_time=1024)
        self.assertEqual(timeline.aac_priming(p)[0::2], (1024, "edit list"))

    def test_aac_priming_default(self):
        p = self.m4a()
        self.assertEqual(timeline.aac_priming(p)[0::2], (2112, "default"))
        self.assertAlmostEqual(timeline.shift_ms(p), 47.891, places=3)

    def test_itunsmpb_wins_over_edit_list(self):
        p = self.m4a(itunsmpb=" 00000000 00000840 00000120 0000000000001000 00000000", elst_media_time=999)
        self.assertEqual(timeline.aac_priming(p)[0], 2112)

    def test_damaged_itunsmpb_falls_back(self):
        p = self.m4a(itunsmpb="garbage")
        self.assertEqual(timeline.aac_priming(p)[2], "default")

    def test_lossless_and_mp3_shift_zero(self):
        W.make_wav(self.tmp / "a.wav")
        W.make_mp3(self.tmp / "a.mp3", info="lame")
        W.make_mp3(self.tmp / "b.mp3", info="none")
        self.assertEqual([timeline.shift_ms(self.tmp / n) for n in ("a.wav", "a.mp3", "b.mp3")], [0.0, 0.0, 0.0])

    def test_missing_file_shift_zero(self):
        self.assertEqual(timeline.shift_ms(self.tmp / "missing.wav"), 0.0)

    def test_lame_delay_and_padding_read(self):
        W.make_mp3(self.tmp / "a.mp3", info="lame", delay=576, padding=1105)
        i = timeline.mp3_info(self.tmp / "a.mp3")
        self.assertEqual((i["enc_delay"], i["enc_padding"], i["sample_rate"], i["case"]), (576, 1105, 44100, "mp3_lame"))

    def test_checked_cases_are_the_measured_ones(self):
        self.assertEqual(timeline.CHECKED, {"lossless_aiff", "aac"})

    def test_beatgrid_on_the_player_timeline_adds_the_shift(self):
        p = self.tmp / "a.m4a"
        W.make_m4a(p, {"tempomap": W.tempo_map(1.9669, 124.0017)}, itunsmpb=" 00000000 00000840 00000120 0000000000001000 00000000")
        a = tags.read_beatgrid(p, 40.0, "serato")
        b = tags.read_beatgrid(p, 40.0, "audio")
        self.assertAlmostEqual(b["beats"][5] - a["beats"][5], 0.047891, places=6)
        self.assertEqual(b["shift_ms"], 47.891)

    def test_real_aac_and_aiff_grids_agree_once_shifted(self):
        """The real-Serato result: same click track, AAC and AIFF copies: Serato wrote the same grid for both, and the AAC file's own
        note says its priming is 2,112 samples, which is what the shift adds."""
        r = json.loads((HERE / "fixtures" / "real_serato_5_tags.json").read_text())
        g_aiff = tags.parse_tempo_map(bytes.fromhex(r["aiff"]["Serato Tempo Map"]))
        g_aac = tags.parse_tempo_map(bytes.fromhex(r["m4a"]["tempomap"]))
        self.assertEqual(g_aiff, g_aac)
        priming = int(r["m4a"]["iTunSMPB"].split()[1], 16)
        self.assertEqual(priming, 2112)


class Duration(Base):
    def test_wav(self):
        W.make_wav(self.tmp / "a.wav", W.silence_pcm(2.5))
        self.assertAlmostEqual(duration.duration_s(self.tmp / "a.wav"), 2.5, places=6)

    def test_wav_48k(self):
        W.make_wav(self.tmp / "a.wav", W.silence_pcm(1.5, 48000), sr=48000)
        self.assertAlmostEqual(duration.duration_s(self.tmp / "a.wav"), 1.5, places=6)

    def test_aiff(self):
        W.make_aiff(self.tmp / "a.aiff", frames=88200)
        self.assertAlmostEqual(duration.duration_s(self.tmp / "a.aiff"), 2.0, places=6)

    def test_flac(self):
        W.make_flac(self.tmp / "a.flac", {}, total=441000)
        self.assertAlmostEqual(duration.duration_s(self.tmp / "a.flac"), 10.0, places=6)

    def test_m4a(self):
        W.make_m4a(self.tmp / "a.m4a", duration_s=3.0)
        self.assertAlmostEqual(duration.duration_s(self.tmp / "a.m4a"), 3.0, places=3)

    def test_mp3_with_frame_count(self):
        W.make_mp3(self.tmp / "a.mp3", info="lame", info_frames=100)
        self.assertAlmostEqual(duration.duration_s(self.tmp / "a.mp3"), 100 * 1152 / 44100.0, places=6)

    def test_mp3_without_info_frame_from_file_size(self):
        W.make_mp3(self.tmp / "a.mp3", info="none", frames=200)
        d = duration.duration_s(self.tmp / "a.mp3")
        self.assertTrue(abs(d - 201 * 1152 / 44100.0) < 0.1, d)

    def test_unknown_and_damaged(self):
        (self.tmp / "a.xyz").write_bytes(b"x")
        (self.tmp / "b.wav").write_bytes(b"RIFF")
        self.assertIsNone(duration.duration_s(self.tmp / "a.xyz"))
        self.assertIsNone(duration.duration_s(self.tmp / "b.wav"))
        self.assertIsNone(duration.duration_s(self.tmp / "missing.mp3"))

    def test_grid_ends_at_the_file_length(self):
        p = self.tmp / "a.wav"
        tag = W.id3v24({"Serato BeatGrid": W.beatgrid([(0.0, 120.0)])})
        W.make_wav(p, W.silence_pcm(10.0), tag=tag)
        g = tags.read_beatgrid(p)
        self.assertEqual(len(g["beats"]), 20)


class Cli(Base):
    def run_cli(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(list(args))
        self.assertEqual(code, 0)
        return json.loads(buf.getvalue())

    def test_tags(self):
        p = self.tmp / "a.mp3"
        W.make_mp3(p, W.mp3_tag(cues=[(0, 1500, 0xCC0000, "A")]), info="lame")
        out = self.run_cli("tags", str(p))
        self.assertEqual(out["cues"][0]["ms"], 1500)

    def test_tracks_and_crates(self):
        f = W.make_library(self.tmp, [dict(pfil="a.mp3", tsng="A")], crates={"C": ["a.mp3"]}, smarts={"S": W.smart_crate([(9, "cond_is_str", "x")])})
        out = self.run_cli("tracks", str(f))
        self.assertEqual(out[0]["tracks"][0]["title"], "A")
        out = self.run_cli("crates", str(f))
        self.assertEqual(out[0]["crates"], {"C": ["/a.mp3"]})
        self.assertEqual(out[0]["smart_crates"]["S"]["rules"][0]["value"], "x")

    def test_grid(self):
        p = self.tmp / "a.mp3"
        W.make_mp3(p, W.mp3_tag(grid=[(0.5, 120.0)]), info="lame")
        out = self.run_cli("grid", str(p))
        self.assertAlmostEqual(out["bpm"], 120.0, places=3)


if __name__ == "__main__":
    unittest.main()
