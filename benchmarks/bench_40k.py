"""Speed check: read a made-up 40,000-track Serato library (database V2 + 60 crates, one of them holding every track) and time it.

  python benchmarks/bench_40k.py

Uses a temporary folder and removes nothing outside it. Prints seconds and peak memory."""
import pathlib
import resource
import shutil
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE.parent / "tests"))
import fixtures_writer as W
import serato_reader as sr

N = 40000


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="serato_reader_bench_"))
    try:
        tracks = [dict(pfil="Users/dj/Music/Artist %d/Album %d/%05d Song with a long name.mp3" % (i % 900, i % 300, i), tsng="Song %d" % i,
                       tart="Artist %d" % (i % 900), talb="Album %d" % (i % 300), tgen="House", tbpm="%.2f" % (118 + i % 30), tkey="Am",
                       tlen="05:%02d.00" % (i % 60), tsiz="11.2MB", tbit="320.0kbps", tsmp="44.1k", uadd=1700000000 + i, utme=1700000000 + i,
                       ufsb=11000000 + i, bbgl=False, bmis=False) for i in range(N)]
        paths = [t["pfil"] for t in tracks]
        crates = {"Everything": paths}
        for k in range(59):
            crates["Folder %d%%%%Crate %d" % (k % 6, k)] = paths[k * 500:k * 500 + 400]
        folder = W.make_library(tmp, tracks, crates, {"Big room": W.smart_crate([(15, "cond_greq_uint", 128)])})
        size = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
        del tracks, paths, crates
        child = (
            "import resource, sys, time\n"
            "sys.path.insert(0, %r)\n"
            "import serato_reader as sr\n"
            "t0 = time.time()\n"
            "lib = sr.load_library(%r, '/')\n"
            "dt = time.time() - t0\n"
            "t1 = time.time()\n"
            "match = lib.smart_crate_tracks('Big room')\n"
            "dt2 = time.time() - t1\n"
            "scale = 1024.0 * 1024.0 if sys.platform == 'darwin' else 1024.0\n"
            "print('tracks %%d, crates %%d' %% (len(lib.tracks), len(lib.crates)))\n"
            "print('read: %%.2f s   smart crate over every track: %%.2f s (%%d matches)   peak memory: %%.0f MB' %% (dt, dt2, len(match), "
            "resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / scale))\n"
        ) % (str(HERE.parent / "src"), str(folder))
        import subprocess
        print("library files: %.1f MB" % (size / 1e6))
        print(subprocess.run([sys.executable, "-c", child], capture_output=True, text=True).stdout.strip())
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


if __name__ == "__main__":
    main()
