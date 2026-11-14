#!/usr/bin/env python3
"""
Extract chrome/test/chromedriver from Chromium with full history.

Designed for partial clones (--filter=blob:none --sparse) where only
chrome/test/chromedriver blobs have been backfilled.

Usage (run from within the chromium repo):
  python3 extract_chromedriver.py | git -C <output-dir> fast-import --done
"""

import subprocess
import sys

SUBDIR = "chrome/test/chromedriver"
PREFIX = SUBDIR + "/"
OUTPUT_REF = "refs/heads/main"


def git(*args):
    return subprocess.run(["git"] + list(args), capture_output=True, check=True).stdout


def quote_path(path):
    """C-style quote a path for fast-import if it starts with '"' or contains LF."""
    if path.startswith(b'"') or b"\n" in path:
        path = path.replace(b"\\", b"\\\\").replace(b'"', b'\\"').replace(b"\n", b"\\n")
        return b'"' + path + b'"'
    return path


# Get all commits touching SUBDIR, oldest first. --parents prints rewritten
# parents (nearest ancestors also touching SUBDIR), not the original ones.
commits_raw = git(
    "rev-list", "--topo-order", "--reverse", "--parents", "HEAD", "--", SUBDIR
).decode()
commits = [(s[0], s[1:]) for s in map(str.split, commits_raw.splitlines())]
print(f"Processing {len(commits)} commits...", file=sys.stderr)

out = sys.stdout.buffer
counter, blob_marks, commit_marks = 0, {}, {}

# Persistent cat-file process for efficient blob reading
cat = subprocess.Popen(
    ["git", "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
)


def next_mark():
    global counter
    counter += 1
    return counter


def write_blob(sha):
    if sha in blob_marks:
        return blob_marks[sha]
    m = next_mark()
    blob_marks[sha] = m
    cat.stdin.write(sha.encode() + b"\n")
    cat.stdin.flush()
    size = int(cat.stdout.readline().split()[2])
    data = cat.stdout.read(size)
    cat.stdout.read(1)  # trailing newline
    out.write(f"blob\nmark :{m}\ndata {size}\n".encode() + data + b"\n")
    return m


for i, (sha, parents) in enumerate(commits):
    if i % 1000 == 0:
        print(f"  {i}/{len(commits)}", file=sys.stderr)

    # Parse commit object for author/committer/message
    raw = git("cat-file", "commit", sha)
    sep = raw.index(b"\n\n")
    msg = raw[sep + 2 :]
    author = committer = b""
    for line in raw[:sep].split(b"\n"):
        if line.startswith(b"author "):
            author = line[7:]
        elif line.startswith(b"committer "):
            committer = line[10:]

    # Get files in SUBDIR and ensure their blobs are written
    files = {}
    for entry in git("ls-tree", "-r", "-z", sha, "--", PREFIX).split(b"\0"):
        if entry:
            meta, path = entry.split(b"\t", 1)
            mode, _, bsha = meta.decode().split()
            files[path[len(PREFIX) :]] = (mode, write_blob(bsha))

    m = next_mark()
    commit_marks[sha] = m

    if not parents:
        out.write(f"reset {OUTPUT_REF}\n".encode())  # start a new root explicitly
    out.write(f"commit {OUTPUT_REF}\nmark :{m}\n".encode())
    out.write(b"author " + author + b"\ncommitter " + committer + b"\n")
    out.write(
        f"data {len(msg)}\n".encode() + msg + (b"" if msg.endswith(b"\n") else b"\n")
    )
    for n, p in enumerate(parents):
        out.write(f"{'merge' if n else 'from'} :{commit_marks[p]}\n".encode())
    out.write(b"deleteall\n")
    for path, (mode, bm) in files.items():
        out.write(f"M {mode} :{bm} ".encode() + quote_path(path) + b"\n")
    out.write(b"\n")

cat.stdin.close()
cat.wait()
out.write(b"done\n")
print(f"Done! {len(commits)} commits, {len(blob_marks)} unique blobs.", file=sys.stderr)
