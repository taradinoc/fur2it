"""Data model classes for fur2it — shared between parser and writer."""

from enum import IntEnum

CHANNELS = 8

class NoteValue(IntEnum):
    """Furnace note values."""
    FIRST = 0
    LAST = 179
    OFF = 180
    RELEASE = 181
    MACRO_RELEASE = 182

class Sample:
    """Represents a Furnace/SNES sample."""
    def __init__(self):
        self.name = ""
        self.length = 0           # length in BRR bytes
        self.c4_rate = 0          # Hz at C-4
        self.depth = 0            # 9 = BRR
        self.loop_start = -1      # sample position, -1 if no loop
        self.loop_end = -1
        self.flags_looped = False
        self.raw_data = b''       # raw BRR bytes
        self.pcm_data = b''       # decoded 16-bit signed PCM (filled after BRR decode)
        self.pcm_length = 0       # decoded sample count
        self.pcm_loop_start = 0   # loop start in PCM samples
        self.pcm_loop_end = 0     # loop end in PCM samples

class Instrument:
    """Represents a Furnace SNES instrument."""
    def __init__(self):
        self.name = ""
        self.initial_sample = 0
        self.use_sample_map = False
        self.note_remap = {}       # furnace note → furnace note (from sample map)
        self.sample_for_note = {}  # furnace note → sample index (from sample map)
        self.volume_scale = 1.0
        self.semitone_offset = 0
        # Envelope
        self.envelope_on = False
        self.gain_mode = 0
        self.gain = 127
        self.attack = 0
        self.decay = 0
        self.sustain = 0
        self.release = 0

class Note:
    """A single note/row in a pattern."""
    def __init__(self):
        self.note = None          # int: NoteValue or None for empty
        self.instrument = None    # int: instrument index or None
        self.volume = None        # int: 0–127 or None
        self.effects = []         # list of (type: int, value: int)

    def is_empty(self):
        return (self.note is None and self.instrument is None and
                self.volume is None and len(self.effects) == 0)

    def __repr__(self):
        return "Note(note=%r, instr=%r, vol=%r, fx=%r)" % (
            self.note, self.instrument, self.volume, self.effects)

class Pattern:
    """A pattern for a single channel."""
    def __init__(self):
        self.rows = []  # list of Note

class Song:
    """A Furnace subsong."""
    def __init__(self):
        self.name = ""
        self.author = ""
        self.time_base = 0
        self.speed1 = 6           # ticks per row
        self.speed2 = 0
        self.ticks_per_second = 60.0
        self.pattern_length = 64  # rows per pattern
        self.orders_length = 0
        self.orders = []          # list of lists: orders[channel][order_index] = pattern_index
        self.speed_pattern_length = 0
        self.speed_pattern = [6]
        self.instruments_used = set()

class FurnaceFile:
    """Top-level container for a parsed .fur file."""
    def __init__(self):
        self.format_version = 0
        self.a4_tuning = 440.0
        self.instrument_count = 0
        self.sample_count = 0
        self.global_pattern_count = 0
        self.songs = []
        self.instruments = []
        self.samples = []
        self.groove_patterns = []
        # patterns[channel][pattern_index] = Pattern
        self.patterns = [{} for _ in range(CHANNELS)]
        # Song-level metadata (populated after parsing)
        self.name = ""
        self.author = ""

    def first_song(self):
        if self.songs:
            return self.songs[0]
        return None
