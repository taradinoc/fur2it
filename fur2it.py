#!/usr/bin/env python3
"""fur2it — Convert Furnace Tracker (.fur) SNES modules to Impulse Tracker (.it) format.

Usage:
    python fur2it.py input.fur [-o output.it] [-v]

Only SNES modules (chip 0x87) that use samples for all instruments are supported.
BRR samples are decoded to 16-bit signed PCM.
"""

import argparse
import os
import sys

from model import FurnaceFile
from furnace_reader import parse_furnace_file
from brr import decode_brr
from it_writer import write_it_file


def main():
    parser = argparse.ArgumentParser(
        description="Convert Furnace Tracker SNES modules to Impulse Tracker format."
    )
    parser.add_argument(
        "input",
        help="Input .fur file (Furnace Tracker module, SNES only)"
    )
    parser.add_argument(
        "-o", "--output",
        help="Output .it file path (default: input stem + .it)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print detailed conversion information"
    )
    args = parser.parse_args()

    input_path = args.input
    if not os.path.exists(input_path):
        print("Error: Input file not found: %s" % input_path, file=sys.stderr)
        sys.exit(1)

    output_path = args.output
    if output_path is None:
        base = os.path.splitext(input_path)[0]
        output_path = base + ".it"

    # --- Parse ---
    if args.verbose:
        print("Parsing: %s" % input_path)

    furnace = parse_furnace_file(input_path)

    if args.verbose:
        song = furnace.first_song()
        print("  Name: %s" % (furnace.name or "(unnamed)"))
        print("  Author: %s" % (furnace.author or "(unknown)"))
        print("  Instruments: %d" % len(furnace.instruments))
        print("  Samples: %d" % len(furnace.samples))
        if song:
            print("  Orders: %d rows" % song.orders_length)
            print("  Pattern length: %d rows" % song.pattern_length)
            print("  Tempo: %.1f ticks/sec (IT tempo ~%d)" %
                  (song.ticks_per_second, int(song.ticks_per_second * 2.5)))
            print("  Speed: %s" % (song.speed_pattern or song.speed1))

        # Show instrument sample assignments
        print("  Instrument → Sample mapping:")
        for i, inst in enumerate(furnace.instruments):
            if inst.use_sample_map:
                notes = sorted(inst.sample_for_note.keys())
                sample_ids = sorted(set(inst.sample_for_note.values()))
                print("    [%d] %s (sample map: %d notes, samples %s)" %
                      (i, inst.name or "unnamed", len(notes), sample_ids))
            else:
                print("    [%d] %s → sample %d" %
                      (i, inst.name or "unnamed", inst.initial_sample))

    # --- Validate ---
    if not furnace.samples:
        print("Error: No samples found. This module may not use SNES samples.", file=sys.stderr)
        sys.exit(1)

    if not furnace.instruments:
        print("Error: No instruments found.", file=sys.stderr)
        sys.exit(1)

    # --- Decode BRR samples ---
    if args.verbose:
        print("\nDecoding BRR samples to 16-bit PCM...")

    for i, sample in enumerate(furnace.samples):
        if sample.depth != 9:
            print("Warning: Sample %d (%s) has depth %d, not BRR. Skipping." %
                  (i, sample.name, sample.depth), file=sys.stderr)
            continue

        loop_start = -1
        if sample.flags_looped and sample.loop_start >= 0:
            # loop_start is in sample units for BRR
            loop_start = sample.loop_start

        try:
            pcm_bytes, pcm_count, pcm_loop_start, pcm_loop_end = decode_brr(
                sample.raw_data, loop_start
            )
        except Exception as e:
            print("Error decoding sample %d (%s): %s" % (i, sample.name, e), file=sys.stderr)
            sys.exit(1)

        sample.pcm_data = pcm_bytes
        sample.pcm_length = pcm_count
        sample.pcm_loop_start = pcm_loop_start
        sample.pcm_loop_end = pcm_loop_end

        if args.verbose:
            loop_info = ""
            if sample.flags_looped:
                loop_info = " (looped %d-%d)" % (pcm_loop_start, pcm_loop_end)
            print("  Sample %d: %s — %d samples%s, C4=%.0f Hz" %
                  (i, sample.name, pcm_count, loop_info, sample.c4_rate))

    # --- Write IT ---
    if args.verbose:
        print("\nWriting: %s" % output_path)

    write_it_file(furnace, output_path)

    if args.verbose:
        file_size = os.path.getsize(output_path)
        print("  Done: %d bytes" % file_size)

    print("Converted: %s → %s" % (input_path, output_path))


if __name__ == "__main__":
    main()
