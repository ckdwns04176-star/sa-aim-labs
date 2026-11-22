#!/usr/bin/env python3
"""Extract the NVIDIA Drs DB profile sets that ship with SA AIM LABS.

The profile packs the user supplied are self-extracting Windows executables that
carry a ZIP archive at the end of the file.  Their entries are compressed with
XZ (Zip method 95), which Python's ``zipfile`` cannot decompress, so this script
parses the ZIP local headers itself and inflates the payload with :mod:`lzma`.

Usage (development only -- the extracted files are committed under assets/):

    python tools/extract_drs_profiles.py \
        --rifle  ../../attachments/2026-11-23/kimjihoon-....exe \
        --sniper ../../attachments/2026-11-23/syukman-....exe

It verifies the CRC of every entry and checks that the resulting Drs database
actually contains a ``suddenattack.exe`` application entry.
"""
from __future__ import annotations

import argparse
import hashlib
import lzma
import os
import struct
import sys
import zipfile
import zlib

DRS_FILES = ("nvdrsdb0.bin", "nvdrsdb1.bin", "nvdrssel.bin", "nvAppTimestamps", "nvdrswr.lk")
here = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(here)


def extract_pack(path: str, out_dir: str) -> dict[str, tuple[int, str]]:
    """Extract every entry of the embedded ZIP archive into ``out_dir``."""
    os.makedirs(out_dir, exist_ok=True)
    result: dict[str, tuple[int, str]] = {}
    with zipfile.ZipFile(path) as zf, open(path, "rb") as fh:
        for info in zf.infolist():
            if info.compress_type == 0:  # stored
                data = zf.read(info.filename)
            elif info.compress_type == 95:  # XZ, unsupported by zipfile
                fh.seek(info.header_offset)
                header = fh.read(30)
                sig, _ver, _flag, _method, _t, _d, _crc, _cs, _us, fnl, exl = struct.unpack(
                    "<IHHHHHIIIHH", header
                )
                if sig != 0x04034B50:
                    raise ValueError(f"{info.filename}: bad local file header")
                fh.seek(info.header_offset + 30 + fnl + exl)
                data = lzma.decompress(fh.read(info.compress_size), format=lzma.FORMAT_AUTO)
            else:
                raise ValueError(f"{info.filename}: unsupported compression {info.compress_type}")

            crc = zlib.crc32(data) & 0xFFFFFFFF
            if crc != info.CRC:
                raise ValueError(f"{info.filename}: CRC mismatch")
            with open(os.path.join(out_dir, info.filename), "wb") as out:
                out.write(data)
            result[info.filename] = (len(data), hashlib.sha256(data).hexdigest())
    return result


def check_database(out_dir: str) -> None:
    path = os.path.join(out_dir, "nvdrsdb0.bin")
    blob = open(path, "rb").read()
    if "suddenattack.exe".encode("utf-16-le") not in blob:
        raise SystemExit(f"{path}: no suddenattack.exe profile found -- wrong pack?")
    if blob != open(os.path.join(out_dir, "nvdrsdb1.bin"), "rb").read():
        raise SystemExit(f"{path}: nvdrsdb0.bin and nvdrsdb1.bin differ")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rifle", required=True, help="라플 Drs 프로필 exe")
    ap.add_argument("--sniper", required=True, help="스나 Drs 프로필 exe")
    ap.add_argument("--dest", default=os.path.join(ROOT, "assets", "profiles"))
    args = ap.parse_args(argv)

    for mode, src in (("rifle", args.rifle), ("sniper", args.sniper)):
        out_dir = os.path.join(args.dest, mode)
        info = extract_pack(src, out_dir)
        missing = [n for n in DRS_FILES if n not in info]
        if missing:
            raise SystemExit(f"{mode}: missing entries {missing}")
        check_database(out_dir)
        print(f"[{mode}] -> {out_dir}")
        for name in DRS_FILES:
            size, digest = info[name]
            print(f"   {name:16} {size:>9,} bytes  sha256 {digest[:16]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
