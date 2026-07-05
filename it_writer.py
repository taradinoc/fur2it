"""Impulse Tracker (.it) file writer.

Writes the internal model to a binary .it file following the IT format spec.
Reference: https://modland.com/pub/documents/format_documentation/Impulse%20Tracker%20v2.04%20(.it).html
"""

import struct
from model import CHANNELS, NoteValue

IT_EFFECT_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ#\\"


def write_it_file(furnace, filename):
    """Write a FurnaceFile to an .it file.

    Args:
        furnace: Parsed FurnaceFile with decoded PCM samples.
        filename: Output .it file path.
    """
    song = furnace.first_song()
    if song is None:
        raise ValueError("No song data found")

    orders_length = song.orders_length

    # --- Flatten per-channel orders into combined order list ---
    # Furnace: each channel has independent orders.
    # IT: single shared order list, each entry references one pattern containing all channels.
    # Strategy: each unique tuple of (ch0_pat, ch1_pat, ..., ch7_pat) = one IT pattern.

    # Build the sequence of combined tuples
    combined_order_tuples = []
    for oi in range(orders_length):
        tup = tuple(song.orders[ch][oi] for ch in range(CHANNELS))
        combined_order_tuples.append(tup)

    # Assign IT pattern indices to each unique tuple
    unique_tuples = list(dict.fromkeys(combined_order_tuples))  # preserve order
    combined_pattern_index = {tup: i for i, tup in enumerate(unique_tuples)}

    # IT order list
    it_orders = [combined_pattern_index[tup] for tup in combined_order_tuples]
    pattern_count = len(unique_tuples)

    # --- Collect instruments and samples ---
    instrument_count = len(furnace.instruments)
    sample_count = len(furnace.samples)

    # --- Build packed pattern data for combined patterns ---
    pattern_data_blocks = []
    pattern_row_counts = []

    for tup in unique_tuples:
        # Row count is the song's pattern length (consistent across Furnace)
        row_count = song.pattern_length
        pattern_row_counts.append(row_count)
        packed = _pack_combined_pattern(furnace, song, tup, row_count)
        pattern_data_blocks.append(packed)

    # --- Compute initial speed and tempo ---
    it_tempo = int(song.ticks_per_second * 2.5)
    it_speed = song.speed_pattern[0] if song.speed_pattern else song.speed1

    # --- Start building file ---
    out = bytearray()
    _w = out.extend

    # Reserve space for header (we'll fill it at the end after computing offsets)
    header_offset = len(out)
    _w(b'\x00' * 192)  # 43 + 64 + 64 + 21 padding

    # --- Orders (exactly order_count bytes; 255 means end-of-song) ---
    order_offset = len(out)
    for oi in range(orders_length):
        _w(struct.pack('<B', it_orders[oi]))

    # Pad to even alignment
    if len(out) % 2:
        _w(b'\x00')

    # --- Instrument offsets (placeholder) ---
    instr_offset_pos = len(out)
    for _ in range(instrument_count):
        _w(b'\x00\x00\x00\x00')

    # --- Sample offsets (placeholder) ---
    sample_offset_pos = len(out)
    for _ in range(sample_count):
        _w(b'\x00\x00\x00\x00')

    # --- Pattern offsets (placeholder) ---
    pattern_offset_pos = len(out)
    for _ in range(pattern_count):
        _w(b'\x00\x00\x00\x00')

    # Pad to even alignment
    while len(out) % 2:
        _w(b'\x00')

    # --- Instrument data ---
    instrument_offsets = []
    for inst in furnace.instruments:
        inst_off = len(out)
        instrument_offsets.append(inst_off)
        _write_instrument(out, inst, furnace, song)

    # Pad to even alignment
    while len(out) % 2:
        _w(b'\x00')

    # --- Sample data ---
    sample_offsets = []
    for smp in furnace.samples:
        smp_off = len(out)
        sample_offsets.append(smp_off)
        _write_sample_header(out, smp, furnace)

    # Pad to even alignment
    while len(out) % 2:
        _w(b'\x00')

    # --- Pattern data ---
    pattern_offsets = []
    for i in range(pattern_count):
        pat_off = len(out)
        pattern_offsets.append(pat_off)
        _w(pattern_data_blocks[i])

    # Pad to even alignment
    while len(out) % 2:
        _w(b'\x00')

    # --- Raw PCM sample data ---
    sample_data_offsets = []
    for smp in furnace.samples:
        data_off = len(out)
        sample_data_offsets.append(data_off)
        _w(smp.pcm_data)

    # --- Now fill in the header ---
    _fill_header(out, header_offset, furnace, song, it_speed, it_tempo,
                 instrument_count, sample_count, pattern_count,
                 orders_length, order_offset,
                 instrument_offsets, sample_offsets,
                 pattern_offsets)

    # --- Fill instrument offsets ---
    for i, off in enumerate(instrument_offsets):
        struct.pack_into('<I', out, instr_offset_pos + i * 4, off)

    # --- Fill sample offsets and sample data pointers ---
    for i, off in enumerate(sample_offsets):
        struct.pack_into('<I', out, sample_offset_pos + i * 4, off)
    # The sample data pointer (relative to file start) is at offset +72 in each sample header.
    for i in range(sample_count):
        struct.pack_into('<I', out, sample_offsets[i] + 72, sample_data_offsets[i])

    # --- Fill pattern offsets ---
    for i, off in enumerate(pattern_offsets):
        struct.pack_into('<I', out, pattern_offset_pos + i * 4, off)

    # --- Write file ---
    with open(filename, 'wb') as f:
        f.write(out)


def _fill_header(out, pos, furnace, song, speed, tempo,
                 inst_count, smp_count, pat_count,
                 order_count, order_off,
                 instr_offsets, smp_offsets,
                 pat_offsets):
    """Fill the 192-byte IT header area."""
    # Bytes 0-3: IMPM magic
    out[pos + 0:pos + 4] = b'IMPM'

    # Bytes 4-29: song name (26 bytes, null-padded)
    name_bytes = _padded_str(furnace.name or "Converted", 26)
    out[pos + 4:pos + 30] = name_bytes

    # Byte 30: pattern row highlight (unused, set to 4)
    out[pos + 30] = 4

    # Byte 31: pattern row highlight (unused, set to 16)
    out[pos + 31] = 16

    # Bytes 32-33: order count
    struct.pack_into('<H', out, pos + 32, order_count)

    # Bytes 34-35: instrument count
    struct.pack_into('<H', out, pos + 34, inst_count)

    # Bytes 36-37: sample count
    struct.pack_into('<H', out, pos + 36, smp_count)

    # Bytes 38-39: pattern count
    struct.pack_into('<H', out, pos + 38, pat_count)

    # Bytes 40-41: created with version (0x0214 = 2.14)
    struct.pack_into('<H', out, pos + 40, 0x0214)

    # Bytes 42-43: compatible with version (0x0200)
    struct.pack_into('<H', out, pos + 42, 0x0200)

    # Bytes 44-45: flags (bit 0=stereo, bit 2=use instruments, bit 4=linear, bit 5=old FX)
    flags = 0x01 | 0x04 | 0x10 | 0x20  # stereo + instruments + linear + old FX
    struct.pack_into('<H', out, pos + 44, flags)

    # Bytes 46-47: special (0)
    struct.pack_into('<H', out, pos + 46, 0)

    # Byte 48: global volume (128)
    out[pos + 48] = 128

    # Byte 49: mix volume (128)
    out[pos + 49] = 128

    # Byte 50: initial speed
    out[pos + 50] = max(1, min(255, speed))

    # Byte 51: initial tempo
    out[pos + 51] = max(32, min(255, tempo))

    # Byte 52: pan separation (128 = surround)
    out[pos + 52] = 128

    # Byte 53: pitch wheel depth (0)
    out[pos + 53] = 0

    # Bytes 54-55: message length (0 = no message)
    struct.pack_into('<H', out, pos + 54, 0)

    # Bytes 56-59: message offset (0)
    struct.pack_into('<I', out, pos + 56, 0)

    # Bytes 60-63: reserved
    struct.pack_into('<I', out, pos + 60, 0)

    # Bytes 64-127: channel pan (64 bytes)
    # IT pan: 0=left, 32=center, 64=right, 100=surround, 128=muted, 255=disabled
    for ch in range(64):
        if ch < CHANNELS:
            out[pos + 64 + ch] = 32  # center
        else:
            out[pos + 64 + ch] = 255  # disabled

    # Bytes 128-191: channel volume (64 bytes, 64 for used, 0 for unused)
    for ch in range(64):
        if ch < CHANNELS:
            out[pos + 128 + ch] = 64
        else:
            out[pos + 128 + ch] = 0


def _write_instrument(out, inst, furnace, song):
    """Write an IT instrument header (278 bytes)."""
    _w = out.extend

    # IMPI magic
    _w(b'IMPI')

    # DOS filename (12 bytes)
    _w(_padded_str("", 12))

    # Reserved (1 byte)
    _w(b'\x00')

    # New note action: 0 = cut previous note
    _w(b'\x00')

    # Duplicate check type: 0 = off
    _w(b'\x00')

    # Duplicate check action: 0 = cut
    _w(b'\x00')

    # Fade out value (signed 16-bit, 0=no fade)
    _w(struct.pack('<h', 128))

    # Pitch-pan separation (0 = off)
    _w(b'\x00')

    # Pitch-pan center (C-5 = 60)
    _w(struct.pack('<B', 60))

    # Global volume (128 = max)
    _w(b'\x80')

    # Default pan (32 = center, or 128 = don't use)
    _w(b'\x20')

    # Random volume variation (0)
    _w(b'\x00')

    # Random pan variation (0)
    _w(b'\x00')

    # Tracker version (0x0214)
    _w(struct.pack('<H', 0x0214))

    # Number of samples in instrument (1, since we map to first sample)
    _w(b'\x01')

    # Reserved
    _w(b'\x00')

    # Instrument name (26 bytes)
    _w(_padded_str(inst.name, 26))

    # Reserved (6 bytes)
    _w(b'\x00\x00\x00\x00\x00\x00')

    # Note-sample-keyboard table (120 notes × 2 bytes each)
    for note_idx in range(120):
        furnace_note = note_idx + 12 * 5
        if inst.use_sample_map and furnace_note in inst.sample_for_note:
            smp_idx = inst.sample_for_note[furnace_note]
        else:
            smp_idx = inst.initial_sample

        # Note to play (same note, 0-based per IT convention)
        note_byte = note_idx
        sample_byte = smp_idx + 1  # 1-based in IT
        _w(struct.pack('<BB', note_byte, sample_byte))

    # --- Envelope data (256 bytes: vol + pan + pitch + padding) ---
    # Volume envelope (81 bytes): enabled, sustain at max, release to 0
    # Flag: 0x01 = enabled, 0x05 = enabled + sustain loop
    _w(b'\x05')   # flags: enabled + sustain loop
    _w(b'\x02')   # 2 nodes
    _w(b'\x00')   # loop begin (node 0)
    _w(b'\x00')   # loop end (node 0)
    _w(b'\x00')   # sustain loop begin (node 0)
    _w(b'\x00')   # sustain loop end (node 0)
    # Node 0: volume 64 (max), tick 0
    _w(b'\x40\x00\x00')
    # Node 1: volume 0, tick 8 (fast release)
    _w(b'\x00\x08\x00')
    # Remaining 23 node slots (zeroed)
    _w(b'\x00' * (23 * 3))

    # Pan envelope (81 bytes): disabled
    _w(b'\x00' * 81)

    # Pitch envelope (81 bytes): disabled
    _w(b'\x00' * 81)

    # Padding to reach 256 total envelope bytes (81*3=243, pad 13)
    _w(b'\x00' * 13)


def _write_sample_header(out, smp, furnace):
    """Write an IT sample header (80 bytes)."""
    _w = out.extend

    # IMPS magic
    _w(b'IMPS')

    # DOS filename (12 bytes)
    _w(_padded_str("", 12))

    # Reserved (1 byte)
    _w(b'\x00')

    # Global volume (64)
    _w(b'\x40')

    # Flags: bit 0=sample present, bit 1=16-bit, bit 4=loop on
    flags = 0x01 | 0x02  # sample present + 16-bit
    if smp.flags_looped:
        flags |= 0x10  # loop on
    _w(struct.pack('<B', flags))

    # Default volume (64)
    _w(b'\x40')

    # Sample name (26 bytes)
    _w(_padded_str(smp.name, 26))

    # Convert flags (bit 1=signed samples)
    _w(b'\x01')  # signed

    # Default pan (32 = center)
    _w(b'\x20')

    # Sample length (in samples)
    _w(struct.pack('<I', smp.pcm_length))

    # Loop beginning (in samples)
    _w(struct.pack('<I', smp.pcm_loop_start))

    # Loop end (in samples)
    _w(struct.pack('<I', smp.pcm_loop_end))

    # C5 speed
    # IT C5 speed = sample rate in Hz at C-5.
    # Furnace gives us c4_rate at C-4. C-5 is one octave above C-4.
    # c5_rate = c4_rate * 2
    # IT stores this as a 32-bit integer.
    c5_rate = int(smp.c4_rate * 2)
    _w(struct.pack('<I', c5_rate))

    # Sustain loop beginning
    _w(struct.pack('<I', smp.pcm_loop_start))

    # Sustain loop end
    _w(struct.pack('<I', smp.pcm_loop_end))

    # Sample data pointer (relative to file start) — will be filled later
    _w(struct.pack('<I', 0))

    # Vibrato speed
    _w(b'\x00')

    # Vibrato depth
    _w(b'\x00')

    # Vibrato sweep
    _w(b'\x00')

    # Vibrato waveform
    _w(b'\x00')


_EFFECT_MAP = {
    0x09: 'A',  # set speed
    0x0F: 'A',  # set speed 2
    0xF0: 'T',  # set tempo/BPM
    0x0B: 'B',  # jump to order
    0x0D: 'C',  # pattern break
    0xFF: 'C',  # stop song (as pattern break, row 0)
}

# Which effects should continue if not repeated (IT auto-cancels these)
_EFFECTS_NEED_CONTINUE = {'A', 'B', 'C', 'T'}


def _pack_combined_pattern(furnace, song, combined_tuple, row_count):
    """Pack a combined pattern (all channels from a per-channel tuple) into IT binary format.

    Args:
        furnace: The FurnaceFile.
        song: The Song.
        combined_tuple: Tuple of (ch0_pat_idx, ch1_pat_idx, ..., ch7_pat_idx).
        row_count: Number of rows in the pattern.

    Returns bytes of the packed pattern.
    """
    out = bytearray()
    _w = out.extend

    # Pattern header: packed length (placeholder), row count, 4 reserved bytes
    packed_len_pos = len(out)
    _w(b'\x00\x00')  # placeholder for packed length
    _w(struct.pack('<H', row_count))
    _w(b'\x00\x00\x00\x00')  # reserved

    # Per-channel state (tracked across the pattern for mask optimization)
    last_mask = [0] * 64
    last_note = [0] * 64
    last_instr = [0] * 64
    last_vol = [0] * 64
    last_effect = [0] * 64

    for row in range(row_count):
        for ch in range(CHANNELS):
            pat_idx = combined_tuple[ch]
            pat = furnace.patterns[ch].get(pat_idx)
            if pat is None or row >= len(pat.rows):
                continue

            note_obj = pat.rows[row]
            if note_obj.is_empty():
                continue

            # Determine what we need to write
            need_note = note_obj.note is not None
            need_instr = note_obj.instrument is not None
            need_vol = note_obj.volume is not None
            need_effect = len(note_obj.effects) > 0

            if not (need_note or need_instr or need_vol or need_effect):
                continue

            # Build mask: low nibble = read, high nibble = use
            mask = 0
            if need_note:
                mask |= 0x01 | 0x10
            if need_instr:
                mask |= 0x02 | 0x20
            if need_vol:
                mask |= 0x04 | 0x40
            if need_effect:
                mask |= 0x08 | 0x80

            # Write channel variable
            ch_var = (ch + 1)
            if mask != last_mask[ch]:
                ch_var |= 0x80
            _w(struct.pack('<B', ch_var))

            # Write new mask if changed
            if mask != last_mask[ch]:
                _w(struct.pack('<B', mask))
                last_mask[ch] = mask

            # Write note
            if need_note:
                it_note = _furnace_note_to_it(note_obj.note)
                _w(struct.pack('<B', it_note))
                last_note[ch] = it_note

            # Write instrument
            if need_instr:
                it_instr = note_obj.instrument + 1  # IT is 1-based
                _w(struct.pack('<B', it_instr))
                last_instr[ch] = it_instr

            # Write volume
            if need_vol:
                it_vol = _furnace_vol_to_it(note_obj.volume)
                _w(struct.pack('<B', it_vol))
                last_vol[ch] = it_vol

            # Write effect
            if need_effect:
                eff_id, eff_val = _map_effect(note_obj.effects)
                it_effect16 = eff_id | (eff_val << 8)
                _w(struct.pack('<H', it_effect16))
                last_effect[ch] = it_effect16

        # End of row marker (0)
        _w(b'\x00')

    # Fill in packed length
    packed_len = len(out) - (packed_len_pos + 2)
    struct.pack_into('<H', out, packed_len_pos, packed_len)

    return bytes(out)


def _furnace_note_to_it(furnace_note):
    """Convert Furnace note value to IT note value.

    Furnace: 0 = C-0? Actually in Furnace, 0 = C-5? Wait...
    Looking at fur2tad.py: note_name_from_index uses offset to produce o4c etc.
    The Furnace format stores 0 = C-5 (middle C in the tracker, which shows as octave 5).
    Actually in Furnace's internal representation, notes 0-179 map to C-0 through B-14
    with C-5 at index 60 (12*5).

    IT: 0 = C-0, 1 = C#0, ..., 11 = B-0, 12 = C-1, ..., 119 = B-9.
    IT: 254 = note cut, 255 = note off.

    So: IT note = furnace_note - 60, clamped to 0-119.
    """
    if furnace_note is None:
        return 255  # note off
    if furnace_note == NoteValue.OFF:
        return 255
    if furnace_note == NoteValue.RELEASE:
        return 254  # note cut
    if furnace_note < 60:
        return 0  # clamp to C-0
    it_note = furnace_note - 60
    if it_note > 119:
        return 119  # clamp to B-9
    return it_note


def _furnace_vol_to_it(furnace_vol):
    """Convert Furnace volume (0-255) to IT volume (0-64)."""
    if furnace_vol is None:
        return 64
    return min(64, max(0, furnace_vol // 4))


def _map_effect(effects):
    """Map the first core Furnace effect to IT effect ID and value.

    Returns (effect_id, effect_value) where effect_id is 1-28 mapping to IT chars A-\.
    Returns (0, 0) if no effect is mappable.
    """
    for eff_type, eff_val in effects:
        if eff_type in _EFFECT_MAP:
            it_char = _EFFECT_MAP[eff_type]
            # Find the effect index (1-based)
            eff_id = IT_EFFECT_CHARS.index(it_char) + 1

            # Special: 0xFF (stop) → pattern break to row 0 of next pattern
            if eff_type == 0xFF:
                eff_val = 0

            return (eff_id, eff_val)

    return (0, 0)


def _padded_str(s, length):
    """Pad or truncate a string to exactly length bytes (null-padded)."""
    b = s.encode('utf-8', errors='replace')[:length]
    if len(b) < length:
        b += b'\x00' * (length - len(b))
    return b
