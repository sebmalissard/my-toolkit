#!/usr/bin/env python3
r"""
Rename movie files (.mkv / .avi) and subtitles (.srt) to the format: Title (Year)
Example: Ana.et.les.Enfants.2026.1080p.WEB-DL.x264.mkv -> Ana et les Enfants (2026).mkv

A video and its subtitle get the same new name, so they stay paired.
A subtitle without a matching video in the same folder is reported as [NO VIDEO].

Options:
    --path      Folder to process. Either a local path or an SMB URL:
                smb://[user[:password]@]host[:port]/share/folder
                If the user or the password is missing from the URL, the
                SMB_USER / SMB_PASSWORD environment variables are used,
                otherwise they are prompted (the password is never echoed).
    --apply     Actually rename the files (without it: dry run).
    -r          Also process subfolders.

Exception: a file that is already named "Title (Year)" followed by a language tag, written
with a space or a dot (e.g. "Zulu (2013) eng.avi", "Film (2000).eng.mkv", "Film (2000).eng.srt"),
keeps its language tag. A video and a subtitle may differ only by that tag and stay paired.
The accepted tags are listed in LANGUAGES below.

Examples:
    ./rename_movies.py --path "D:\media"
    ./rename_movies.py --path smb://192.168.1.4/sambashare/media/Films
    ./rename_movies.py --path smb://seb@192.168.1.4/sambashare/media/Films -r --apply

SMB mode requires: pip install smbprotocol
A UNC path (\\host\share\folder) given to --path also enables SMB mode.
"""

import argparse
import getpass
import os
import re
import sys
from urllib.parse import unquote, urlparse

VIDEO_EXTENSIONS = {".mkv", ".avi", ".mp4"}
SUBTITLE_EXTENSIONS = {".srt"}
EXTENSIONS = VIDEO_EXTENSIONS | SUBTITLE_EXTENSIONS

YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
# Multi-part files such as "Title (2010).part1.avi" or "Title (2010) cd2.avi"
PART_RE = re.compile(r"(?<![A-Za-z0-9])(part|cd|disc|disk)[ ._-]?(\d{1,2})(?![A-Za-z0-9])", re.IGNORECASE)
# Initialisms such as "A.C.O.D." must keep their dots
INITIALISM_RE = re.compile(r"(?<![A-Za-z])(?:[A-Za-z]\.){2,}")

# Language tags allowed after the year on files that are already renamed,
# e.g. "Zulu (2013) eng.avi": these names are kept as they are
# (keep the trailing comma: a one-item tuple needs it)
LANGUAGES = ("eng", "fr", "en")
LANGUAGE_PATTERN = "|".join(map(re.escape, LANGUAGES))
# Both "Zulu (2013) eng" and "Film (2000).eng" are accepted and written with a dot
ALREADY_RENAMED_RE = re.compile(
    r"^(?P<base>.*\S \((?:19|20)\d{2}\))(?P<tags>(?:[ .](?:" + LANGUAGE_PATTERN + r"))+)$",
    re.IGNORECASE,
)
# Trailing language tags, ignored when pairing a video with its subtitle
LANGUAGE_TAGS_RE = re.compile(r"(?:[ .](?:" + LANGUAGE_PATTERN + r"))+$", re.IGNORECASE)


def dot_tags(tags: str) -> str:
    """Write language tags with a dot separator: ' eng fr' -> '.eng.fr'."""
    return tags.replace(" ", ".")


def clean_title(raw: str) -> str:
    """Clean the part of the file name located before the year."""
    raw = raw.strip(" -([{")
    if " " in raw:
        # Already human-readable ("L.A. Confidential", "Jimmy P."): keep its punctuation
        title = raw.replace("_", " ")
    else:
        # Release style ("The.Butterfly.Effect"): dots and underscores are separators,
        # except inside initialisms such as "A.C.O.D."
        parts = []
        last = 0
        for m in INITIALISM_RE.finditer(raw):
            parts.append(re.sub(r"[._]+", " ", raw[last:m.start()]))
            parts.append(" " + m.group(0) + " ")
            last = m.end()
        parts.append(re.sub(r"[._]+", " ", raw[last:]))
        title = "".join(parts)
    return re.sub(r"\s+", " ", title).strip(" -([{")


def build_name(stem: str):
    """Return 'Title (Year)' or None if no year is found."""
    # Exception: already renamed file followed by a language tag ("Zulu (2013) eng"):
    # keep the tag, written with a dot ("Zulu (2013).eng")
    already = ALREADY_RENAMED_RE.match(stem.strip())
    if already:
        base = re.sub(r"\s+", " ", already.group("base"))
        return base + dot_tags(already.group("tags"))
    matches = [m for m in YEAR_RE.finditer(stem) if m.start() > 0]
    if not matches:
        return None
    m = matches[-1]  # last year found (handles "2001 A Space Odyssey 1968")

    title = clean_title(stem[: m.start()])
    if not title:
        return None

    # Keep the part marker exactly as written (".part1", " cd2", ...) so that
    # "part1" and "part2" do not get the same name
    suffix = ""
    tail = stem[m.end():]
    part = PART_RE.search(tail)
    if part:
        separators = re.search(r"[ ._-]*$", tail[: part.start()]).group(0)
        suffix = separators + part.group(0)
    # A language tag at the very end of the name is kept, written with a dot (".eng")
    language = LANGUAGE_TAGS_RE.search(tail)
    if language:
        suffix += dot_tags(language.group(0))
    return f"{title} ({m.group(1)}){suffix}"


# --------------------------------------------------------------------------
# Path parsing
# --------------------------------------------------------------------------

def parse_smb_url(url: str):
    """Parse smb://[user[:password]@]host[:port]/share/folder.

    Return (server, unc_path, user, password, port).
    """
    parsed = urlparse(url)
    if not parsed.hostname:
        sys.exit(f"Invalid SMB URL: {url}")
    server = parsed.hostname
    user = unquote(parsed.username) if parsed.username else None
    password = unquote(parsed.password) if parsed.password else None
    folder = unquote(parsed.path).strip("/").replace("/", "\\")
    unc_path = "\\\\" + server + ("\\" + folder if folder else "")
    return server, unc_path, user, password, parsed.port or 445


def parse_unc(path: str):
    """Parse \\\\host\\share\\folder. Return (server, unc_path)."""
    unc_path = path.replace("/", "\\").rstrip("\\")
    server = unc_path.lstrip("\\").split("\\")[0]
    return server, unc_path


# --------------------------------------------------------------------------
# File access: local or SMB
# --------------------------------------------------------------------------

def connect_smb(server: str, user, password, port=445):
    """Open an SMB session to the given server."""
    try:
        import smbclient
    except ImportError:
        sys.exit("Missing module: pip install smbprotocol")

    user = user or os.environ.get("SMB_USER") or input("SMB user: ")
    password = password or os.environ.get("SMB_PASSWORD") or getpass.getpass(f"Password for {user}@{server}: ")
    try:
        smbclient.register_session(server, username=user, password=password, port=port)
    except Exception as e:
        sys.exit(f"SMB connection failed ({server}): {e}")
    return smbclient


class Local:
    sep = os.sep

    def isdir(self, p): return os.path.isdir(p)
    def exists(self, p): return os.path.exists(p)
    def rename(self, a, b): os.rename(a, b)
    def scandir(self, p): return os.scandir(p)


class Smb:
    sep = "\\"

    def __init__(self, smbclient):
        self.c = smbclient

    def isdir(self, p): return self.c.path.isdir(p)
    def exists(self, p): return self.c.path.exists(p)
    def rename(self, a, b): self.c.rename(a, b)
    def scandir(self, p): return self.c.scandir(p)


def scan(fs, base, recursive):
    """Return a sorted list of (directory, file_name) tuples."""
    found = []
    for e in fs.scandir(base):
        if e.is_file():
            found.append((base, e.name))
        elif e.is_dir() and recursive:
            found += scan(fs, base.rstrip(fs.sep) + fs.sep + e.name, recursive)
    return sorted(found)


def main():
    parser = argparse.ArgumentParser(description="Rename movies and subtitles to 'Title (Year)' (local or SMB).")
    parser.add_argument("--path", help="Folder to process: local path or smb://[user[:password]@]host/share/folder")
    parser.add_argument("--apply", action="store_true", help="Actually rename the files")
    parser.add_argument("--recursive", "-r", action="store_true", help="Also process subfolders")

    # No option given: show the help and exit
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    if not args.path:
        parser.error("the --path option is required")

    if args.path.lower().startswith("smb://"):
        server, base, user, password, port = parse_smb_url(args.path)
        fs = Smb(connect_smb(server, user, password, port))
    elif args.path.startswith("\\\\"):
        server, base = parse_unc(args.path)
        fs = Smb(connect_smb(server, None, None))
    else:
        base = args.path
        fs = Local()

    try:
        if not fs.isdir(base):
            sys.exit(f"Folder not found: {base}")
        files = scan(fs, base, args.recursive)
    except Exception as e:
        sys.exit(f"Cannot read folder: {e}")

    # First pass: compute the new name of every supported file
    entries = []
    for directory, name in files:
        stem, ext = os.path.splitext(name)
        if ext.lower() in EXTENSIONS:
            entries.append((directory, name, stem, ext, build_name(stem)))

    # Final name (without extension and language tag) of every video,
    # used to detect subtitles without video
    video_stems = {
        (directory.lower(), LANGUAGE_TAGS_RE.sub("", new_stem or stem).lower())
        for directory, name, stem, ext, new_stem in entries
        if ext.lower() in VIDEO_EXTENSIONS
    }

    renamed = skipped = unchanged = no_video = 0
    planned = set()

    # Second pass: rename
    for directory, name, stem, ext, new_stem in entries:
        if ext.lower() in SUBTITLE_EXTENSIONS and (directory.lower(), LANGUAGE_TAGS_RE.sub("", new_stem or stem).lower()) not in video_stems:
            print(f"[NO VIDEO]  {name}  (subtitle without a matching video)")
            no_video += 1

        if new_stem is None:
            print(f"[SKIPPED]   {name}  (year not found)")
            skipped += 1
            continue

        new_name = new_stem + ext.lower()
        if new_name == name:
            unchanged += 1
            continue

        src = directory.rstrip(fs.sep) + fs.sep + name
        dst = directory.rstrip(fs.sep) + fs.sep + new_name
        # Case-only changes are not conflicts on case-insensitive file systems
        if new_name.lower() != name.lower() and fs.exists(dst):
            print(f"[CONFLICT]  {name} -> {new_name}  (already exists)")
            skipped += 1
            continue
        # Two files of the same folder must not get the same new name
        key = (directory.lower(), new_name.lower())
        if key in planned:
            print(f"[CONFLICT]  {name} -> {new_name}  (another file gets the same name)")
            skipped += 1
            continue
        planned.add(key)

        print(f"[RENAMED]   {name}  ->  {new_name}")
        if args.apply:
            try:
                fs.rename(src, dst)
            except Exception as e:
                print(f"[ERROR]     {name}: {e}")
                skipped += 1
                continue
        renamed += 1

    mode = "" if args.apply else " (dry run, use --apply to rename)"
    orphans = f", {no_video} subtitle(s) without video" if no_video else ""
    print(f"\n{renamed} renamed, {unchanged} already correct, {skipped} skipped{orphans}{mode}")


if __name__ == "__main__":
    main()
