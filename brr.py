"""BRR (Bit Rate Reduction) ADPCM decoder for SNES audio samples.

BRR is a 4-bit ADPCM format used by the SNES S-DSP. Each block of 9 bytes
produces 16 samples. The first byte is a header, and the remaining 8 bytes
each contain two 4-bit nibbles.

Reference: https://wiki.superfamicom.org/spc700-reference#dsp-brr-decoding
"""

def decode_brr(raw_data, loop_start_sample=-1):
    """Decode BRR-encoded bytes to 16-bit signed PCM samples.

    Args:
        raw_data: Raw BRR bytes.
        loop_start_sample: Sample position where looping begins (-1 if no loop).

    Returns:
        (pcm_bytes, pcm_sample_count, loop_start_pcm, loop_end_pcm)
        where pcm_bytes is little-endian 16-bit signed PCM,
        pcm_sample_count is total samples,
        loop_start_pcm/loop_end_pcm are in PCM sample units.
    """
    BLOCK_SIZE = 9       # bytes per BRR block
    SAMPLES_PER_BLOCK = 16

    if not raw_data:
        return (b'', 0, 0, 0)

    num_blocks = len(raw_data) // BLOCK_SIZE
    total_samples = num_blocks * SAMPLES_PER_BLOCK

    pcm = [0] * total_samples
    pcm_loop_start = 0
    pcm_loop_end = total_samples
    has_loop = loop_start_sample >= 0

    # How many samples per BRR nibble byte (always 2 for standard BRR)
    # We process each block
    for block_idx in range(num_blocks):
        block_offset = block_idx * BLOCK_SIZE
        header = raw_data[block_offset]

        # Header byte layout:
        # bits 7-4: range (0–12 valid)
        # bits 3-2: filter (0–3)
        # bit  1:   loop flag (start of loop)
        # bit  0:   end flag (end of sample, stop after this block)

        _range = (header >> 4) & 0xF
        _filter = (header >> 2) & 3
        _loop_flag = (header >> 1) & 1
        _end_flag = header & 1

        # Determine shift amount from range
        if _range <= 12:
            if _range == 0:
                shift = 0
                # For range 0, we effectively divide by 2 by using shift=-1?
                # Actually, range 0 means shift right by 1 (treated as range 0 → shift -1 → >>1)
                # But in standard BRR decoding, range 0 → right shift 1
                # Let's use the standard interpretation:
                shift = -1  # right shift by 1 = divide by 2
            else:
                shift = _range - 1  # left shift by (range-1)
        else:
            shift = 9  # invalid, clamp to reasonable

        # Extract nibbles from bytes 1-8
        nibbles = []
        for i in range(1, BLOCK_SIZE):
            b = raw_data[block_offset + i]
            nibbles.append(b >> 4)       # high nibble
            nibbles.append(b & 0x0F)     # low nibble

        # First two nibbles are the "nybble0" and "nybble1" (direct, no filter)
        # We need to interpret the 4-bit signed values
        def sign_extend_4(v):
            if v >= 8:
                return v - 16
            return v

        # Calculate sample index for this block
        sample_base = block_idx * SAMPLES_PER_BLOCK

        # Previous two samples for filter (cross block boundaries)
        if block_idx > 0:
            prev1 = pcm[sample_base - 1]
            prev2 = pcm[sample_base - 2] if sample_base >= 2 else 0
        else:
            prev1 = 0
            prev2 = 0

        for ni in range(SAMPLES_PER_BLOCK):
            raw_nibble = nibbles[ni]
            signed_input = sign_extend_4(raw_nibble)

            # Apply range shift to the input FIRST (before filter)
            # This matches the S-DSP decoding order
            if shift >= 0:
                scaled_input = signed_input << shift
            else:
                scaled_input = signed_input >> (-shift)

            if _filter == 0:
                sample = scaled_input
            elif _filter == 1:
                # Filter 1: out = scaled_input + prev1 * 15/16
                sample = scaled_input + prev1 - (prev1 >> 4)
            elif _filter == 2:
                # Filter 2: out = scaled_input + prev1 * 61/32 - prev2 * 15/16
                sample = scaled_input + (prev1 << 1) - ((prev1 * 3) >> 5) - prev2 + (prev2 >> 4)
            elif _filter == 3:
                # Filter 3: out = scaled_input + prev1 * 115/64 - prev2 * 13/16
                sample = scaled_input + (prev1 << 1) - ((prev1 * 13) >> 6) - prev2 + ((prev2 * 3) >> 4)
            else:
                sample = scaled_input

            # Clamp to 16-bit signed
            if sample > 32767:
                sample = 32767
            elif sample < -32768:
                sample = -32768

            pcm[sample_base + ni] = sample

            # Shift history
            prev2 = prev1
            prev1 = sample

        # Check loop flag
        if _loop_flag and has_loop:
            # Mark loop start at the beginning of this block
            pcm_loop_start = sample_base

        # Check end flag
        if _end_flag:
            total_samples = sample_base + SAMPLES_PER_BLOCK
            pcm_loop_end = total_samples
            break

    # If a specific loop start was given in samples, use that
    if loop_start_sample >= 0 and loop_start_sample < total_samples:
        pcm_loop_start = loop_start_sample
    else:
        has_loop = False

    if not has_loop:
        pcm_loop_start = 0
        pcm_loop_end = total_samples

    # Convert to little-endian 16-bit signed bytes
    pcm_bytes = bytearray()
    for i in range(total_samples):
        val = pcm[i]
        # Clamp again just in case
        if val > 32767:
            val = 32767
        elif val < -32768:
            val = -32768
        # Little-endian 16-bit signed
        pcm_bytes.append(val & 0xFF)
        pcm_bytes.append((val >> 8) & 0xFF)

    return (bytes(pcm_bytes), total_samples, pcm_loop_start, pcm_loop_end)
