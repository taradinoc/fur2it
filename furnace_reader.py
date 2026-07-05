"""Furnace Tracker (.fur) file parser.

Reads Furnace module files and produces a FurnaceFile object.
Based on the Furnace file format specification and reference parser in fur2tad.py.

Furnace format: https://github.com/tildearrow/furnace/blob/master/papers/format.md
"""

import io
import struct
import zlib

from model import CHANNELS, FurnaceFile, Song, Instrument, Sample, Pattern, Note

def bytes_to_int(b, order='little', signed=False):
    return int.from_bytes(b, byteorder=order, signed=signed)

def bytes_to_float(b):
    return struct.unpack('f', b)[0]

def read_string(stream):
    """Read a null-terminated UTF-8 string."""
    out = b''
    while True:
        c = stream.read(1)
        if c == b'\0' or len(c) == 0:
            return out.decode('utf-8', errors='replace')
        out += c


def parse_furnace_file(filename):
    """Parse a .fur file and return a FurnaceFile.

    Args:
        filename: Path to the .fur file.

    Returns:
        FurnaceFile with all parsed data.

    Raises:
        ValueError: If the file is not a valid Furnace module or not an SNES module.
    """
    with open(filename, 'rb') as f:
        raw = f.read()

    # Handle zlib compression
    if raw and raw[0] == 0x78:
        raw = zlib.decompress(raw)

    stream = io.BytesIO(raw)
    furnace = FurnaceFile()

    # --- Magic header ---
    header = stream.read(32)
    if header[0:16] != b'-Furnace module-':
        raise ValueError("Not a Furnace module (bad magic)")

    furnace.format_version = bytes_to_int(header[16:18])
    song_info_pointer = bytes_to_int(header[20:24])

    # --- Read blocks ---
    stream.seek(song_info_pointer)
    block_handlers = _make_block_handlers(furnace)

    while True:
        block_name_bytes = stream.read(4)
        if len(block_name_bytes) < 4:
            break
        block_name = block_name_bytes.decode('ascii', errors='replace')
        block_size = bytes_to_int(stream.read(4))
        block_data = stream.read(block_size)

        if block_name in block_handlers:
            block_handlers[block_name](io.BytesIO(block_data))

    # Copy first song's metadata to top level
    first = furnace.first_song()
    if first:
        furnace.name = first.name
        furnace.author = first.author

    return furnace


def _make_block_handlers(furnace):
    """Create block handler dispatch table."""
    handlers = {}

    def handler(name):
        def decorator(fn):
            handlers[name] = fn
            return fn
        return decorator

    @handler("INFO")
    def info_block(s):
        """INFO block — song metadata (old format, versions < 240)."""
        song = Song()
        furnace.songs.append(song)

        # --- First sub-song data (embedded in INFO block) ---
        song.time_base = bytes_to_int(s.read(1))
        song.speed1 = bytes_to_int(s.read(1))
        song.speed2 = bytes_to_int(s.read(1))
        s.read(1)  # initial_arpeggio_time
        song.ticks_per_second = bytes_to_float(s.read(4))
        song.pattern_length = bytes_to_int(s.read(2))
        song.orders_length = bytes_to_int(s.read(2))
        s.read(1)  # highlight_A
        s.read(1)  # highlight_B

        # --- Counts ---
        furnace.instrument_count = bytes_to_int(s.read(2))
        furnace.wavetable_count = bytes_to_int(s.read(2))
        furnace.sample_count = bytes_to_int(s.read(2))
        furnace.global_pattern_count = bytes_to_int(s.read(4))

        # --- Chip list (32 bytes) ---
        chips = s.read(32)
        chip_ids = []
        for b in chips:
            if b == 0:
                break
            chip_ids.append(b)
        if 0x87 not in chip_ids:
            import sys
            print("Warning: No SNES chip (0x87) found in chip list. Chips: %s" %
                  [hex(c) for c in chip_ids], file=sys.stderr)

        # --- Chip volumes, panning, flag pointers (for compat) ---
        s.read(32)   # chip volumes (<135) or reserved
        s.read(32)   # chip panning (<135) or reserved
        s.read(128)  # chip flag pointers (>=119) or flags (<119)

        # --- Song name and author ---
        song.name = read_string(s).replace("/", "-").replace("\\", "-")
        song.author = read_string(s)
        furnace.a4_tuning = bytes_to_float(s.read(4))

        # --- Various flags ---
        s.read(1)  # limit_slides
        s.read(1)  # linear_pitch
        s.read(1)  # loop_modality
        s.read(1)  # proper_noise_layout
        s.read(1)  # wave_duty_is_volume
        s.read(1)  # reset_macro_on_porta
        s.read(1)  # legacy_volume_slides
        s.read(1)  # compatible_arpeggio
        s.read(1)  # note_off_resets_slides
        s.read(1)  # target_resets_slides
        s.read(1)  # arpeggio_inhibits_portamento
        s.read(1)  # wack_algorithm_macro
        s.read(1)  # broken_shortcut_slides
        s.read(1)  # ignore_duplicate_slides
        s.read(1)  # stop_portamento_on_note_off
        s.read(1)  # continuous_vibrato
        s.read(1)  # broken_DAC_mode
        s.read(1)  # one_tick_cut
        s.read(1)  # instrument_change_allowed_during_porta
        s.read(1)  # reset_note_base_on_arpeggio_effect_stop

        # --- Pointers to assets ---
        s.read(4 * furnace.instrument_count)     # instrument pointers
        s.read(4 * furnace.wavetable_count)       # wavetable pointers
        s.read(4 * furnace.sample_count)          # sample pointers
        s.read(4 * furnace.global_pattern_count)  # pattern pointers

        # --- Orders and channel metadata ---
        _read_song_orders(song, s)

        # --- Song comment ---
        song.comment = read_string(s)
        furnace.master_volume = bytes_to_float(s.read(4))

        # --- Extended compatibility flags ---
        s.read(1)  # broken_speed_selection
        s.read(1)  # no_slides_on_first_tick
        s.read(1)  # next_row_reset_arp_pos
        s.read(1)  # ignore_jump_at_end
        s.read(1)  # buggy_portamento_after_slide
        s.read(1)  # new_ins_affects_envelope
        s.read(1)  # ExtCh_channel_state_is_shared
        s.read(1)  # ignore_DAC_mode_change_outside_of_intended_channel
        s.read(1)  # E1xy_and_E2xy_also_take_priority_over_lide00
        s.read(1)  # new_Sega_PCM
        s.read(1)  # weird_f_num_block_based_chip_pitch_slides
        s.read(1)  # SN_duty_macro_always_resets_phase
        s.read(1)  # pitch_macro_is_linear
        s.read(1)  # pitch_slide_speed_in_full_linear_pitch_mode
        s.read(1)  # old_octave_boundary_behavior
        s.read(1)  # disable_OPN2_DAC_volume_control
        s.read(1)  # new_volume_scaling_strategy
        s.read(1)  # volume_macro_still_applies_after_end
        s.read(1)  # broken_outVol
        s.read(1)  # E1xy_and_E2xy_stop_on_same_note
        s.read(1)  # broken_initial_position_of_porta_after_arp
        s.read(1)  # SN_periods_under_8_are_treated_as_1
        s.read(1)  # cut_delay_effect_policy
        s.read(1)  # _0B_0D_effect_treatment
        s.read(1)  # automatic_system_name_detection
        s.read(1)  # disable_sample_macro
        s.read(1)  # broken_outVol_episode_2
        s.read(1)  # old_arpeggio_strategy

        # --- Virtual tempo data ---
        song.virtual_tempo_numerator = bytes_to_int(s.read(2))
        song.virtual_tempo_denominator = bytes_to_int(s.read(2))

        # --- Additional subsongs (>=95) ---
        read_string(s)  # first subsong name
        read_string(s)  # first subsong comment
        num_subsongs = bytes_to_int(s.read(1))
        s.read(3)  # reserved
        s.read(4 * num_subsongs)  # subsong pointers

        # --- Additional metadata (>=103) ---
        read_string(s)  # system name
        read_string(s)  # album/category/game name
        read_string(s)  # song name (Japanese)
        read_string(s)  # song author (Japanese)
        read_string(s)  # system name (Japanese)
        read_string(s)  # album/category/game name (Japanese)

        # --- Extra chip output settings (>=135) ---
        # Need to know chip count. Use the first non-zero chip count.
        chip_count = len(chip_ids)
        s.read(4 * 3 * chip_count)  # volume, panning, front/rear balance × chipCount

        # --- Patchbay (>=135) ---
        patchbay_count = bytes_to_int(s.read(4))
        s.read(4 * patchbay_count)
        s.read(1)  # automatic patchbay

        # --- A couple more compat flags (>=138) ---
        s.read(1)  # broken_portamento_during_legato
        s.read(1)  # broken_macro_during_note_off_in_some_FM_chips
        s.read(1)  # pre_note_does_not_compensate_for_portamento_or_legato
        s.read(1)  # disable_new_NES_DPCM_features
        s.read(1)  # reset_arp_effect_phase_on_new_note
        s.read(1)  # linear_volume_scaling_rounds_up
        s.read(1)  # legacy_always_set_volume_behavior
        s.read(1)  # legacy_sample_offset_effect

        # --- Speed pattern of first song (>=139) ---
        song.speed_pattern_length = bytes_to_int(s.read(1))
        song.speed_pattern = list(s.read(16))[:song.speed_pattern_length]
        if not song.speed_pattern:
            song.speed_pattern = [song.speed1]

        # --- Groove list (>=139) ---
        furnace.groove_patterns = []
        num_grooves = bytes_to_int(s.read(1))
        for _ in range(num_grooves):
            groove_size = bytes_to_int(s.read(1))
            groove_pattern = list(s.read(16))[:groove_size]
            furnace.groove_patterns.append(groove_pattern)

        # --- Pointers to asset directories (>=156) ---
        s.read(4)  # instrument dirs pointer
        s.read(4)  # wavetable dirs pointer
        s.read(4)  # sample dirs pointer

    @handler("SONG")
    def song_block(s):
        """SONG block — subsong data (old format, versions < 240)."""
        song = Song()
        furnace.songs.append(song)

        song.time_base = bytes_to_int(s.read(1))
        song.speed1 = bytes_to_int(s.read(1))
        song.speed2 = bytes_to_int(s.read(1))
        s.read(1)  # initial_arpeggio_time
        song.ticks_per_second = bytes_to_float(s.read(4))
        song.pattern_length = bytes_to_int(s.read(2))
        song.orders_length = bytes_to_int(s.read(2))
        s.read(1)  # highlight_A
        s.read(1)  # highlight_B

        song.virtual_tempo_numerator = bytes_to_int(s.read(2))
        song.virtual_tempo_denominator = bytes_to_int(s.read(2))
        song.name = read_string(s).replace("/", "-").replace("\\", "-")
        song.comment = read_string(s)

        _read_song_orders(song, s)

        song.speed_pattern_length = bytes_to_int(s.read(1))
        song.speed_pattern = list(s.read(16))[:song.speed_pattern_length]
        if not song.speed_pattern:
            song.speed_pattern = [song.speed1]

    @handler("SMP2")
    def sample_block(s):
        """SMP2 block — sample data."""
        sample = Sample()
        furnace.samples.append(sample)

        sample.name = read_string(s).strip()
        sample.length = bytes_to_int(s.read(4))
        sample.compat_rate = bytes_to_int(s.read(4))
        sample.c4_rate = bytes_to_int(s.read(4))
        sample.depth = bytes_to_int(s.read(1))
        sample.loop_direction = bytes_to_int(s.read(1))
        sample.flags = bytes_to_int(s.read(1))
        sample.flags2 = bytes_to_int(s.read(1))
        sample.loop_start = bytes_to_int(s.read(4), signed=True)
        sample.loop_end = bytes_to_int(s.read(4), signed=True)

        s.read(16)  # sample presence bitfields

        sample.raw_data = s.read(sample.length)

        sample.flags_looped = bool(sample.flags & 1)
        if sample.loop_start < 0:
            sample.flags_looped = False
            sample.loop_start = -1
            sample.loop_end = -1

        if sample.depth != 9:
            raise ValueError(
                "Sample '%s' has depth %d, not BRR (9). "
                "Only SNES BRR samples are supported." % (sample.name, sample.depth)
            )

    @handler("INS2")
    def instrument_block(s):
        """INS2 block — instrument definition."""
        instrument = Instrument()

        format_version = bytes_to_int(s.read(2))
        instrument_type = bytes_to_int(s.read(2))

        if instrument_type != 29:
            raise ValueError(
                "Instrument type %d found, but only SNES (29) is supported. "
                "This module may use non-sample instruments." % instrument_type
            )

        # Read features until EN marker
        while True:
            feature = s.read(2)
            if len(feature) < 2 or feature == b'EN':
                break
            feature_size = bytes_to_int(s.read(2))
            feature_data = s.read(feature_size)
            fs = io.BytesIO(feature_data)

            if feature == b'NA':
                instrument.name = read_string(fs).strip()
            elif feature == b'SM':
                instrument.initial_sample = bytes_to_int(fs.read(2))
                b = bytes_to_int(fs.read(1))  # flags
                instrument.use_sample_map = bool(b & 1)
                fs.read(1)  # waveform_length

                if instrument.use_sample_map:
                    for i in range(120):
                        note_to_play = bytes_to_int(fs.read(2)) + 12 * 5
                        sample_to_play = bytes_to_int(fs.read(2))
                        if sample_to_play == 65535:
                            continue
                        instrument.note_remap[i + 12 * 5] = note_to_play
                        instrument.sample_for_note[i + 12 * 5] = sample_to_play
            elif feature == b'SN':
                b = bytes_to_int(fs.read(1))  # attack/decay
                instrument.decay = (b >> 4) & 7
                instrument.attack = b & 15

                b = bytes_to_int(fs.read(1))  # sustain/release
                instrument.sustain = (b >> 5) & 7
                instrument.release = b & 31

                b = bytes_to_int(fs.read(1))  # flags
                instrument.gain_mode = b & 7
                instrument.envelope_on = bool(b & 16)

                instrument.gain = bytes_to_int(fs.read(1))
            elif feature == b'MA':
                # Macros — parse just enough to skip them
                fs.read(2)  # macro data size
                while True:
                    macro_code = fs.read(1)
                    if len(macro_code) == 0 or macro_code[0] == 255:
                        break
                    macro_length = bytes_to_int(fs.read(1))
                    fs.read(1)  # loop
                    fs.read(1)  # release
                    fs.read(1)  # mode
                    word_byte = bytes_to_int(fs.read(1))
                    fs.read(1)  # delay
                    fs.read(1)  # speed

                    signed = True
                    word_size = 1
                    if word_byte & 0xC0 == 0x00:
                        signed = False
                    elif word_byte & 0xC0 == 0x80:
                        word_size = 2
                    elif word_byte & 0xC0 == 0xC0:
                        word_size = 4

                    for _ in range(macro_length):
                        fs.read(word_size)

        furnace.instruments.append(instrument)

    @handler("PATN")
    def pattern_block(s):
        """PATN block — pattern data for one channel."""
        subsong_index = bytes_to_int(s.read(1))
        channel = bytes_to_int(s.read(1))
        pattern_index = bytes_to_int(s.read(2))
        read_string(s)  # pattern name

        song = furnace.songs[subsong_index]
        pattern = Pattern()
        pattern.rows = [Note() for _ in range(song.pattern_length)]

        index = 0
        while index < song.pattern_length:
            b = bytes_to_int(s.read(1))
            if b == 0xFF:
                break
            if b & 128:
                # Skip rows
                index += 2 + (b & 127)
            else:
                note = pattern.rows[index]

                effect1 = None
                effect2 = None
                if b & 32:
                    effect1 = bytes_to_int(s.read(1))
                if b & 64:
                    effect2 = bytes_to_int(s.read(1))

                if b & 1:
                    note.note = bytes_to_int(s.read(1))
                if b & 2:
                    note.instrument = bytes_to_int(s.read(1))
                    song.instruments_used.add(note.instrument)
                if b & 4:
                    vol = bytes_to_int(s.read(1))
                    note.volume = vol * 2 + (vol & 1)  # 0-127 → 0-255

                _read_furnace_effect(s, b & 8, b & 16, note)

                if effect1 is not None:
                    _read_furnace_effect(s, effect1 & 4, effect1 & 8, note)
                    _read_furnace_effect(s, effect1 & 16, effect1 & 32, note)
                    _read_furnace_effect(s, effect1 & 64, effect1 & 128, note)
                if effect2 is not None:
                    _read_furnace_effect(s, effect2 & 1, effect2 & 2, note)
                    _read_furnace_effect(s, effect2 & 4, effect2 & 8, note)
                    _read_furnace_effect(s, effect2 & 64, effect2 & 128, note)

                index += 1

        furnace.patterns[channel][pattern_index] = pattern

    @handler("ADIR")
    def adir_block(s):
        pass  # asset directory — ignore

    return handlers


def _read_song_orders(song, s):
    """Read orders and channel metadata from the stream for a song.
    
    Does NOT read time_base, speed1, speed2, etc. — those must be
    read by the caller before calling this function.
    """
    # Orders: size = channels * orders_length
    song.orders = []
    for _ in range(CHANNELS):
        column = []
        for _ in range(song.orders_length):
            column.append(bytes_to_int(s.read(1)))
        song.orders.append(column)

    # Effect columns
    s.read(CHANNELS)

    # Channel hide status
    s.read(CHANNELS)

    # Channel collapse status
    s.read(CHANNELS)

    # Channel names
    for _ in range(CHANNELS):
        read_string(s)

    # Channel short names
    for _ in range(CHANNELS):
        read_string(s)


def _read_furnace_effect(s, have_type, have_value, note):
    """Read a single effect from the stream and append to note."""
    t = bytes_to_int(s.read(1)) if have_type else None
    v = bytes_to_int(s.read(1)) if have_value else None
    if t is not None:
        if v is None:
            v = 0
        note.effects.append((t, v))
