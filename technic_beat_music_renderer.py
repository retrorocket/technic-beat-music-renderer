#!/usr/bin/env python3
"""
Technic Beat / Technictix MUS + MRK + VH/VB -> WAV renderer

See: https://gist.github.com/Sakimotor/623c1fcd8470c208347300382b32cda3

Default profile selection is automatic.
Profile names are based on the data/timing family rather than a single song:
  namco = former 'e15 MARCHEN MAZE' profile
  tix   = former 'a00 Days Dreamer' profile

The known profiles are:
  namco profile:  # formerly called 'e15'
    MRK tick clock : 60000/1001 Hz
    MRK -> MUS lead: 2.9701 s
    Gate           : MRK +0x10 Key-Off + VAB ADSR2 Release

  tix profile:    # formerly called 'a00'
    MRK tick clock : 60 Hz
    MRK -> MUS lead: 3.000 s
    Gate           : MRK +0x10 Key-Off + VAB ADSR2 Release

  Shared:
    Note mapping   : MRK note - 1
    Fine correction: +2.25 cents
    Instrument gain: 0.837 relative to decoded MUS
    Pan law        : linear (signed MRK pan, one channel held at full level)

  v5 change:
    MRK byte +0x10 is treated as the Key-Off delay in ticks. At Key-Off the
    sample is NOT hard-cut; the VAB tone ADSR2 Release Mode/Release Shift is
    simulated at the SPU 44.1 kHz envelope clock.

  v6.2 change (experimental reverb):
    MRK +0x0C bit 3 selects the SPU2 effect send for each event.  Events with
    that bit set are also sent to an emulation of the HALL preset recovered
    from the game's IOP/libsd state.  The dry renderer is unchanged from v5.
    v6.1 fixed the effect-return gain: EVOLL/EVOLR=0x3FFF is Q15 (~0.5).
    v6.2 additionally models the SPU2 48 kHz clock, L/R-alternating reverb
    processing, and the 39-tap reverb down/up-sampling filters.

Example:
  python rebuild_technic_beat.py \
      --mus e15.mus --mrk e15_.mrk --vh e15.vh --vb e15.vb \
      --out e15.wav --instruments e15_instruments.wav

The defaults can be overridden for songs/versions that differ.
"""

from __future__ import annotations

import argparse
import csv
import math
import struct
import wave
from pathlib import Path

import numpy as np

RATE_MUS = 24000
RATE_SPU = 44100
COEF = [(0, 0), (60, 0), (115, -52), (98, -55), (122, -60)]


def decode_psx_adpcm(data: bytes):
    out = []
    s1 = s2 = 0
    loop_start = None
    stop = None

    for bi in range(len(data) // 16):
        blk = data[bi * 16:(bi + 1) * 16]
        hdr, flags = blk[0], blk[1]
        shift = hdr & 0x0F
        filt = (hdr >> 4) & 0x0F
        if filt >= len(COEF):
            filt = 0
        c0, c1 = COEF[filt]

        if flags & 4 and loop_start is None:
            loop_start = len(out)

        for byte in blk[2:]:
            for nib in (byte & 0x0F, byte >> 4):
                if nib >= 8:
                    nib -= 16
                x = (nib << 12) >> shift
                x += (s1 * c0 + s2 * c1 + 32) >> 6
                x = max(-32768, min(32767, x))
                out.append(x)
                s2, s1 = s1, x

        # These VABs use an end flag before the final terminator.
        if flags & 1:
            stop = len(out)
            break

    if stop is None:
        stop = len(out)

    return np.asarray(out[:stop], dtype=np.float32) / 32768.0, loop_start


def parse_vh(vh: bytes):
    if vh[:4] != b"pBAV":
        raise ValueError("Not a Sony VAB header (pBAV missing)")

    num_programs = struct.unpack_from("<H", vh, 0x12)[0]
    num_tones = struct.unpack_from("<H", vh, 0x14)[0]
    num_vags = struct.unpack_from("<H", vh, 0x16)[0]

    tones = []
    tone_base = 0x20 + 128 * 16

    for program in range(num_programs):
        for tone_no in range(16):
            off = tone_base + (program * 16 + tone_no) * 32
            r = vh[off:off + 32]
            if len(r) < 32:
                continue

            vag = struct.unpack_from("<H", r, 22)[0]
            if vag == 0:
                continue

            tones.append({
                "global_tone": len(tones),
                "program": program,
                "tone": tone_no,
                "volume": r[2],
                "pan": r[3],
                "center": r[4],
                "shift_raw": r[5],
                "min_note": r[6],
                "max_note": r[7],
                "adsr1": struct.unpack_from("<H", r, 16)[0],
                "adsr2": struct.unpack_from("<H", r, 18)[0],
                "vag": vag,
            })

            if len(tones) >= num_tones:
                break
        if len(tones) >= num_tones:
            break

    sample_table_off = tone_base + num_programs * 16 * 32
    sizes = [
        struct.unpack_from("<H", vh, sample_table_off + i * 2)[0] * 8
        for i in range(num_vags + 1)
    ]

    return num_programs, num_tones, num_vags, tones, sizes


def split_vb(vb: bytes, sizes):
    samples = []
    off = 0
    for vag in range(1, len(sizes)):
        n = sizes[vag]
        samples.append(vb[off:off + n])
        off += n

    if off != len(vb):
        raise ValueError(f"VB size mismatch: size table={off}, file={len(vb)}")
    return samples


def parse_mrk(mrk: bytes, tick_hz: float):
    if len(mrk) % 32:
        raise ValueError("MRK size is not aligned to 32-byte records")

    records = [mrk[i:i + 32] for i in range(0, len(mrk), 32)]
    header = records[0]
    bpm = struct.unpack_from("<H", header, 2)[0]
    boundaries = [struct.unpack_from("<I", header, o)[0] for o in (20, 24, 28)]
    boundaries.append(len(records) + 1)

    events = []
    section = 0
    next_boundary = boundaries[0]

    for idx, r in enumerate(records[1:], 1):
        while section < 3 and idx >= next_boundary:
            section += 1
            next_boundary = boundaries[section]

        tick = struct.unpack_from("<I", r, 0)[0]

        if tick == 0xFFFF:
            continue

        # Last record can be a timing-only end marker rather than a sound event.
        if idx == len(records) - 1 and r[24:28] == b"\0\0\0\0":
            continue

        events.append({
            "record": idx,
            "section": section,
            "tick": tick,
            "time_sec": tick / tick_hz,
            "tone": r[24],
            "note": r[25],
            "velocity": r[26],
            "pan": struct.unpack_from("<b", r, 27)[0],
            "flags0c": struct.unpack_from("<I", r, 12)[0],
            "reverb_send": bool(struct.unpack_from("<I", r, 12)[0] & 0x08),
            "b12": r[12],
            "b13": r[13],
            "b15": r[15],
            "b16": r[16],
            "b18": r[18],
            "keyoff_ticks": struct.unpack_from("<H", r, 16)[0],
            "b28": r[28],
            "b29": r[29],
        })

    end_tick = None
    if len(records) >= 2:
        last = records[-1]
        if last[24:28] == b"\0\0\0\0":
            end_tick = struct.unpack_from("<I", last, 0)[0]

    return bpm, boundaries, events, end_tick


def mus_to_stereo(mus: bytes, channels: int = 2):
    size = 0x2EE00
    filler = 0x200
    interleave = 0x17700
    offset = (size + filler) * (channels // 2)

    chunks = len(mus) // offset
    lim_l = chunks - 2 if chunks % 2 == 0 else chunks - 1
    lim_r = chunks - 1 if chunks % 2 == 0 else chunks - 2

    c1, c2 = [], []
    pos = 0
    i = 0

    while i <= lim_l:
        c1.append(mus[pos:pos + interleave])
        pos += interleave
        c2.append(mus[pos:pos + interleave])
        pos += interleave
        pos += filler
        i += 2
        if i <= lim_l:
            pos += offset

    i = lim_r
    while i >= 1:
        c1.append(mus[pos:pos + interleave])
        pos += interleave
        c2.append(mus[pos:pos + interleave])
        pos += interleave
        pos += filler
        i -= 2
        if i >= 1:
            pos -= offset * 3

    left = np.frombuffer(b"".join(c1), dtype="<i2").astype(np.float32) / 32768.0
    right = np.frombuffer(b"".join(c2), dtype="<i2").astype(np.float32) / 32768.0
    return np.column_stack([left, right])


def tone_shift_semitones(raw: int):
    # Empirical interpretation inherited from the validated renderer.
    v = raw if raw < 128 else raw - 256
    return v / 128.0


def pitch_sample(src: np.ndarray, semitones: float, out_rate=RATE_MUS):
    ideal_pitch = 0x1000 * (2.0 ** (semitones / 12.0))
    pitch = max(1, min(0x3FFF, int(round(ideal_pitch))))
    step = pitch / 4096.0

    out_len = max(1, int(math.ceil(len(src) / step)))
    pos = np.minimum(
        np.arange(out_len, dtype=np.float64) * step,
        len(src) - 1
    )
    y = np.interp(
        pos,
        np.arange(len(src), dtype=np.float64),
        src
    ).astype(np.float32)

    n = max(1, int(round(len(y) * out_rate / RATE_SPU)))
    return np.interp(
        np.linspace(0, len(y) - 1, n),
        np.arange(len(y)),
        y
    ).astype(np.float32)



def spu_release_envelope(adsr2: int, n_out: int, out_rate=RATE_MUS):
    """Return the SPU release envelope resampled to out_rate.

    ADSR2 bits used by PS1/PS2 SPU:
      bit 5    release mode (0 linear, 1 exponential)
      bits 0-4 release shift (0 fast .. 31 slow)

    The envelope generator itself advances at 44.1 kHz.  This follows the
    documented SPU shift/counter algorithm for a decreasing phase with the
    fixed release step (-8).  The envelope starts at full scale because v1-v4
    did not model Attack/Decay/Sustain; doing so preserves the already
    calibrated pre-Key-Off level while fixing the tail.
    """
    if n_out <= 0:
        return np.zeros(0, dtype=np.float32)

    shift = adsr2 & 0x1F
    exponential = bool(adsr2 & 0x20)

    # Shift=31 with the fixed release step is the all-ones special case: the
    # envelope never advances.  Natural sample end can still stop the voice.
    if shift == 0x1F:
        return np.ones(n_out, dtype=np.float32)

    n_spu = max(1, int(math.ceil(n_out * RATE_SPU / out_rate)) + 2)
    env = np.empty(n_spu, dtype=np.float32)
    level = 0x7FFF
    counter = 0

    base_step = -8 << max(0, 11 - shift)
    counter_inc = 0x8000 >> max(0, shift - 11)
    counter_inc = max(counter_inc, 1)

    zero_at = None
    for i in range(n_spu):
        env[i] = level / 32767.0
        if level <= 0:
            zero_at = i
            env[i:] = 0.0
            break

        counter += counter_inc
        if counter & 0x8000:
            counter &= 0x7FFF
            step = base_step
            if exponential:
                # C-style truncation toward zero is intentional.
                step = int((step * level) / 32768.0)
                # Avoid a mathematical stall caused solely by integer rounding.
                if step == 0 and level > 0:
                    step = -1
            level = max(0, level + step)

    # Envelope values are generated on the 44.1-kHz SPU clock, then sampled at
    # the renderer's 24-kHz output clock.
    pos = np.arange(n_out, dtype=np.float64) * (RATE_SPU / out_rate)
    pos = np.minimum(pos, len(env) - 1)
    return np.interp(pos, np.arange(len(env), dtype=np.float64), env).astype(np.float32)


def apply_keyoff_release(y: np.ndarray, keyoff_samples: int, adsr2: int):
    """Keep audio unchanged before Key-Off and apply SPU ADSR Release after it."""
    if len(y) == 0:
        return y
    keyoff_samples = max(0, min(int(keyoff_samples), len(y)))
    if keyoff_samples >= len(y):
        return y
    out = y.copy()
    env = spu_release_envelope(adsr2, len(y) - keyoff_samples, RATE_MUS)
    out[keyoff_samples:] *= env
    return out



# ---------------------------------------------------------------------------
# Experimental SPU2 HALL reverb (v6)
# ---------------------------------------------------------------------------
#
# Reverse-engineered from the game's IOP/libsd state:
#
#   sceSdSetEffectAttr(..., mode=0x0105)  -> HALL | CLEAR
#   Effect return volume L/R              -> 0x3FFF / 0x3FFF
#   MRK +0x0C bit 3                       -> voice effect-send enable
#
# The 32 HALL parameters below were recovered from the expanded libsd effect
# structure at IOP 0x001F3908.  Their ordering matches the SPU/SPU2 reverb
# register ordering.
#
# This implementation follows the same core equations used by PCSX2's SPU2
# reverb path.  It intentionally does not try to emulate every timing/MMIO
# detail of the hardware.  Reverb is processed at 22050 Hz (the effective
# per-channel SPU reverb rate) and resampled back to the 24 kHz renderer rate.
#
# Keep this feature separable from the validated v5 dry path: use
# --reverb off to reproduce v5 behaviour.

HALL_REVERB = {
    "dAPF1":  0x01A5,
    "dAPF2":  0x0139,
    "vIIR":   0x6000,
    "vCOMB1": 0x5000,
    "vCOMB2": 0x4C00,
    "vCOMB3": 0xB800,
    "vCOMB4": 0xBC00,
    "vWALL":  0xC000,
    "vAPF1":  0x6000,
    "vAPF2":  0x5C00,

    "mLSAME":  0x15BA,
    "mRSAME":  0x11BB,
    "mLCOMB1": 0x14C2,
    "mRCOMB1": 0x10BD,
    "mLCOMB2": 0x11BC,
    "mRCOMB2": 0x0DC1,
    "dLSAME":  0x11C0,
    "dRSAME":  0x0DC3,
    "mLDIFF":  0x0DC0,
    "mRDIFF":  0x09C1,
    "mLCOMB3": 0x0BC4,
    "mRCOMB3": 0x07C1,
    "mLCOMB4": 0x0A00,
    "mRCOMB4": 0x06CD,
    "dLDIFF":  0x09C2,
    "dRDIFF":  0x05C1,
    "mLAPF1":  0x05C0,
    "mRAPF1":  0x041A,
    "mLAPF2":  0x0274,
    "mRAPF2":  0x013A,

    "vLIN":  0x8000,
    "vRIN":  0x8000,
}

SPU2_RATE = 48000
EFFECT_RETURN_VOLUME = 0x3FFF


def _s16(x: int) -> int:
    x &= 0xFFFF
    return x - 0x10000 if x & 0x8000 else x


def _q15(x: int) -> float:
    return _s16(x) / 32768.0


def _resample_stereo(x: np.ndarray, src_rate: int, dst_rate: int, out_len=None):
    if len(x) == 0:
        n = 0 if out_len is None else int(out_len)
        return np.zeros((n, 2), dtype=np.float32)
    if src_rate == dst_rate and (out_len is None or out_len == len(x)):
        return x.astype(np.float32, copy=True)

    if out_len is None:
        out_len = max(1, int(round(len(x) * dst_rate / src_rate)))

    src_pos = np.arange(len(x), dtype=np.float64)
    dst_pos = np.linspace(0.0, len(x) - 1.0, int(out_len))
    y = np.empty((int(out_len), 2), dtype=np.float32)
    y[:, 0] = np.interp(dst_pos, src_pos, x[:, 0])
    y[:, 1] = np.interp(dst_pos, src_pos, x[:, 1])
    return y


def spu2_hall_reverb(send_24k: np.ndarray, out_rate=RATE_MUS):
    """Approximate the game's SPU2 HALL effect using PCSX2's timing model.

    This v6.2 path improves on v6/v6.1 in three important ways:

      * SPU2 mixer/reverb clock is treated as 48 kHz.
      * Only one reverb channel is processed per SPU2 cycle (L/R alternating).
      * The 39-tap SPU2 reverb down/up FIR filters are applied.

    The function returns only the wet effect return.  Dry audio remains on the
    validated v5 path.
    """
    if len(send_24k) == 0 or not np.any(send_24k):
        return np.zeros_like(send_24k)

    p = HALL_REVERB

    # PCSX2 ReverbResample.cpp filter coefficients.
    down_coef = np.array([
        -1, 0, 2, 0, -10, 0, 35, 0, -103, 0,
        266, 0, -616, 0, 1332, 0, -2960, 0,
        10246, 16384, 10246, 0, -2960, 0, 1332, 0,
        -616, 0, 266, 0, -103, 0, 35, 0, -10, 0, 2, 0, -1
    ], dtype=np.float64) / 32768.0

    # Upsampling coefficients are down coefficients * 2, clipped to s16.
    up_i = np.clip(
        np.array([
            -1, 0, 2, 0, -10, 0, 35, 0, -103, 0,
            266, 0, -616, 0, 1332, 0, -2960, 0,
            10246, 16384, 10246, 0, -2960, 0, 1332, 0,
            -616, 0, 266, 0, -103, 0, 35, 0, -10, 0, 2, 0, -1
        ], dtype=np.int64) * 2,
        -32768, 32767
    ).astype(np.float64)
    up_coef = up_i / 32768.0

    # Reconstruct a 48 kHz pre-reverb stream from the 24 kHz renderer bus.
    # This is still an approximation because the validated renderer itself is
    # 24 kHz, but it preserves the SPU2 reverb clock and alternation.
    x48 = _resample_stereo(send_24k, out_rate, SPU2_RATE)

    # PCSX2's FIR uses the previous 39 input samples.  np.convolve gives us the
    # same low-pass response; the one-sample phase difference is irrelevant to
    # the audible wet level but keeps the spectral balance much closer to SPU2.
    ds = np.empty_like(x48, dtype=np.float32)
    for ch in range(2):
        conv = np.convolve(x48[:, ch].astype(np.float64), down_coef, mode="full")
        # Delay by one sample to mimic the current-sample exclusion in the
        # circular input buffer.
        ds[:, ch] = np.concatenate(([0.0], conv[:len(x48)-1])).astype(np.float32)

    def q15(name):
        return _q15(p[name])

    # libsd writes the address/tap parameters shifted left by two before they
    # reach SPU2 address registers.
    def off(name):
        return int(p[name]) * 4

    apf1_size = int(p["dAPF1"]) * 4
    apf2_size = int(p["dAPF2"]) * 4

    offsets = [
        off("mLSAME"), off("mRSAME"),
        off("mLCOMB1"), off("mRCOMB1"),
        off("mLCOMB2"), off("mRCOMB2"),
        off("dLSAME"), off("dRSAME"),
        off("mLDIFF"), off("mRDIFF"),
        off("mLCOMB3"), off("mRCOMB3"),
        off("mLCOMB4"), off("mRCOMB4"),
        off("dLDIFF"), off("dRDIFF"),
        off("mLAPF1"), off("mRAPF1"),
        off("mLAPF2"), off("mRAPF2"),
    ]

    # Effects RAM advances once per L/R pair (Cycles >> 1 in PCSX2), i.e.
    # effectively 24 kHz.
    ring_len = max(offsets) + max(apf1_size, apf2_size) + 64
    ring = np.zeros(ring_len, dtype=np.float32)

    iir   = q15("vIIR")
    comb1 = q15("vCOMB1")
    comb2 = q15("vCOMB2")
    comb3 = q15("vCOMB3")
    comb4 = q15("vCOMB4")
    wall  = q15("vWALL")
    apf1v = q15("vAPF1")
    apf2v = q15("vAPF2")
    in_l  = q15("vLIN")
    in_r  = q15("vRIN")

    # PCSX2 writes one channel's reverb result per 48 kHz cycle and explicitly
    # zeroes the opposite channel before the 39-tap interpolation filter.
    sparse = np.zeros_like(x48, dtype=np.float32)

    def clamp(v):
        return max(-1.0, min(1.0, float(v)))

    for cyc in range(len(x48)):
        right = cyc & 1
        pos = cyc >> 1
        base = pos % ring_len

        def idx(delta):
            return (base + delta) % ring_len

        if right:
            same_src = idx(off("dRSAME"))
            same_dst = idx(off("mRSAME"))
            same_prv = idx(off("mRSAME") - 1)
            diff_src = idx(off("dLDIFF"))
            diff_dst = idx(off("mRDIFF"))
            diff_prv = idx(off("mRDIFF") - 1)
            comb1_src = idx(off("mRCOMB1"))
            comb2_src = idx(off("mRCOMB2"))
            comb3_src = idx(off("mRCOMB3"))
            comb4_src = idx(off("mRCOMB4"))
            apf1_dst = idx(off("mRAPF1"))
            apf1_src = idx(off("mRAPF1") - apf1_size)
            apf2_dst = idx(off("mRAPF2"))
            apf2_src = idx(off("mRAPF2") - apf2_size)
            inp = ds[cyc, 1] * in_r
            out_ch = 1
        else:
            same_src = idx(off("dLSAME"))
            same_dst = idx(off("mLSAME"))
            same_prv = idx(off("mLSAME") - 1)
            diff_src = idx(off("dRDIFF"))
            diff_dst = idx(off("mLDIFF"))
            diff_prv = idx(off("mLDIFF") - 1)
            comb1_src = idx(off("mLCOMB1"))
            comb2_src = idx(off("mLCOMB2"))
            comb3_src = idx(off("mLCOMB3"))
            comb4_src = idx(off("mLCOMB4"))
            apf1_dst = idx(off("mLAPF1"))
            apf1_src = idx(off("mLAPF1") - apf1_size)
            apf2_dst = idx(off("mLAPF2"))
            apf2_src = idx(off("mLAPF2") - apf2_size)
            inp = ds[cyc, 0] * in_l
            out_ch = 0

        same = iir * (inp + wall * ring[same_src] - ring[same_prv]) + ring[same_prv]
        diff = iir * (inp + wall * ring[diff_src] - ring[diff_prv]) + ring[diff_prv]

        out = (
            comb1 * ring[comb1_src]
            + comb2 * ring[comb2_src]
            + comb3 * ring[comb3_src]
            + comb4 * ring[comb4_src]
        )

        apf1 = out - apf1v * ring[apf1_src]
        out = ring[apf1_src] + apf1v * apf1

        apf2 = out - apf2v * ring[apf2_src]
        out = ring[apf2_src] + apf2v * apf2

        ring[same_dst] = clamp(same)
        ring[diff_dst] = clamp(diff)
        ring[apf1_dst] = clamp(apf1)
        ring[apf2_dst] = clamp(apf2)
        sparse[cyc, out_ch] = clamp(out)

    # PCSX2's upsampler convolves the sparse alternating-channel stream using
    # the doubled 39-tap interpolation filter.
    wet48 = np.empty_like(sparse, dtype=np.float32)
    for ch in range(2):
        conv = np.convolve(sparse[:, ch].astype(np.float64), up_coef, mode="full")
        wet48[:, ch] = conv[:len(sparse)].astype(np.float32)

    # Effect return registers EVOLL/EVOLR are signed Q15.  The game writes
    # 0x3FFF to both, which is ~0.5.
    wet48 *= EFFECT_RETURN_VOLUME / 32768.0

    # Renderer output remains 24 kHz.
    wet24 = _resample_stereo(wet48, SPU2_RATE, out_rate, out_len=len(send_24k))
    return wet24


def render_instruments(
    events,
    tones,
    decoded,
    out_len,
    *,
    tick_hz,
    lead_sec,
    note_offset,
    pitch_cents,
    instrument_gain,
    gate_source,
    pan_law,
):
    mix = np.zeros((out_len, 2), dtype=np.float32)
    reverb_send_mix = np.zeros((out_len, 2), dtype=np.float32)
    tone_map = {t["global_tone"]: t for t in tones}
    cache = {}

    skipped = 0

    by_section = {}
    for e in events:
        by_section.setdefault(e["section"], []).append(e)
    for sec in by_section:
        by_section[sec].sort(key=lambda z: (z["tick"], z["record"]))

    for sec, section_events in by_section.items():
        for event_index, e in enumerate(section_events):
            t = tone_map.get(e["tone"])
            if t is None:
                skipped += 1
                continue

            src = decoded[t["vag"] - 1]
            fine = tone_shift_semitones(t["shift_raw"])
            semitones = (
                (e["note"] + note_offset - t["center"])
                + fine
                + pitch_cents / 100.0
            )

            key = (t["vag"], round(semitones, 8))
            if key not in cache:
                cache[key] = pitch_sample(src, semitones)

            y = cache[key]

            if gate_source == "adsr":
                # Reverse-engineered game behaviour: MRK +0x10 is the delay to
                # CLSound::VoiceOff.  VoiceOff becomes SPU2 KOFF; KOFF starts
                # the VAB ADSR2 Release phase rather than muting immediately.
                gate_sec = e["keyoff_ticks"] / tick_hz
                keyoff_samples = max(0, int(round(gate_sec * RATE_MUS)))
                y = apply_keyoff_release(y, keyoff_samples, t["adsr2"])
            elif gate_source == "b18":
                gate_sec = e["b18"] / tick_hz
                gate_samples = max(1, int(round(gate_sec * RATE_MUS)))
                y = y[:min(len(y), gate_samples)]
            elif gate_source == "next-event":
                if event_index + 1 < len(section_events):
                    next_e = section_events[event_index + 1]
                    gate_sec = (next_e["tick"] - e["tick"]) / tick_hz
                    gate_samples = max(1, int(round(gate_sec * RATE_MUS)))
                    y = y[:min(len(y), gate_samples)]
            elif gate_source == "natural":
                pass
            else:
                raise ValueError(f"Unknown gate source: {gate_source}")

            gain = (
                (t["volume"] / 127.0)
                * (e["velocity"] / 127.0)
                * instrument_gain
            )

            p = max(-64, min(63, e["pan"]))

            if pan_law == "linear":
                # Empirically confirmed against automatic-play recordings.
                # Signed MRK pan is centered at 0.  One side remains at full
                # level while the opposite side attenuates linearly.
                if p < 0:
                    gl = 1.0
                    gr = (64.0 + p) / 64.0
                elif p > 0:
                    gl = (64.0 - p) / 64.0
                    gr = 1.0
                else:
                    gl = gr = 1.0
            elif pan_law == "equal-power":
                # Legacy v1-v3 behaviour retained for comparison.
                angle = (p + 64) / 127.0 * (math.pi / 2.0)
                gl = math.cos(angle) * math.sqrt(2.0)
                gr = math.sin(angle) * math.sqrt(2.0)
            else:
                raise ValueError(f"Unknown pan law: {pan_law}")

            start_sec = lead_sec + e["tick"] / tick_hz
            start = int(round(start_sec * RATE_MUS))
            end = min(out_len, start + len(y))

            if end <= start:
                continue

            z = y[:end - start] * gain
            zl = z * gl
            zr = z * gr
            mix[start:end, 0] += zl
            mix[start:end, 1] += zr

            # Reverse-engineered from the IOP voice update path:
            # MRK +0x0C bit 3 controls VMIXEL/VMIXER effect send.
            if e.get("reverb_send", False):
                reverb_send_mix[start:end, 0] += zl
                reverb_send_mix[start:end, 1] += zr

    return mix, reverb_send_mix, skipped


def write_wav(path: Path, arr: np.ndarray, rate=RATE_MUS):
    y = np.clip(arr, -1.0, 1.0)
    pcm = (y * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(arr.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())



def choose_profile_auto(
    *,
    num_programs,
    num_tones,
    num_vags,
    bpm,
    events,
    end_tick,
    mus_duration,
):
    """
    Infer the renderer profile from structural properties.

    The two confirmed timing profiles are:

    tix (formerly a00):
      - small VAB bank (a00: 2 programs / 26 tones / 26 VAGs)
      - MRK timing behaves as exactly 60 Hz
      - +3.000 s lead
      - v5 uses MRK +0x10 Key-Off + VAB ADSR2 Release

    namco (formerly e15):
      - larger/more general song banks
      - MRK timing follows NTSC 60000/1001 Hz
      - +2.9701 s lead
      - v5 uses MRK +0x10 Key-Off + VAB ADSR2 Release

    The detector intentionally favors the namco profile unless there is strong
    evidence for the small-bank tix behaviour.

    Naming note:
      - namco = former 'e15' timing profile
      - tix   = former 'a00' timing profile
    """
    reasons = []

    # Strongest structural discriminator observed so far.
    small_bank = (
        num_programs <= 2
        and num_tones <= 32
        and num_vags <= 32
    )

    if small_bank:
        reasons.append(
            f"small VAB bank ({num_programs} programs / "
            f"{num_tones} tones / {num_vags} VAGs)"
        )

    # Secondary consistency check: does the explicit MRK end marker land near
    # the MUS end under the Days Dreamer timing model?
    a00_end_error = None
    e15_end_error = None
    if end_tick is not None and mus_duration > 0:
        a00_end = end_tick / 60.0 + 3.0
        e15_end = end_tick / (60000.0 / 1001.0) + 2.9701
        a00_end_error = abs(mus_duration - a00_end)
        e15_end_error = abs(mus_duration - e15_end)

        # This is only supporting evidence: some MUS files contain padded
        # silence after the musical ending (e15 is such a case).
        if a00_end_error <= 1.5:
            reasons.append(
                f"MRK end matches MUS end under tix timing (60 Hz/+3 s) "
                f"(error {a00_end_error:.3f} s)"
            )

    # Small-bank evidence is intentionally required. This avoids classifying
    # padded MUS files incorrectly from duration alone.
    if small_bank:
        return "tix", reasons, a00_end_error, e15_end_error

    reasons.append("no strong tix small-bank signature")
    return "namco", reasons, a00_end_error, e15_end_error


def main():
    ap = argparse.ArgumentParser(
        description="Reconstruct Technic Beat music from MUS + MRK + VH/VB."
    )
    ap.add_argument("--mus", required=True, type=Path)
    ap.add_argument("--mrk", required=True, type=Path)
    ap.add_argument("--vh", required=True, type=Path)
    ap.add_argument("--vb", required=True, type=Path)
    ap.add_argument("--seq", type=Path, help="Optional; currently checked but not needed by the renderer.")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--instruments", type=Path)
    ap.add_argument("--events-csv", type=Path)

    # Arcade automatic-play calibrated defaults.
    ap.add_argument("--tick-hz", type=float, default=60000.0 / 1001.0)
    ap.add_argument("--lead-sec", type=float, default=2.9701)
    ap.add_argument("--note-offset", type=int, default=-1)
    ap.add_argument("--pitch-cents", type=float, default=2.25)
    ap.add_argument("--instrument-gain", type=float, default=0.837)
    ap.add_argument(
        "--profile",
        choices=("auto", "namco", "tix"),
        default="auto",
        help=(
            "Timing/voice preset. auto: infer from VAB/MRK structure. "
            "namco (formerly e15): arcade-calibrated 59.94 Hz timing. "
            "tix (formerly a00): 60 Hz + 3.000 s timing. v6 keeps the v5 ADSR Key-Off path for both."
        ),
    )
    ap.add_argument(
        "--gate",
        choices=("adsr", "b18", "natural", "next-event"),
        default=None,
        help="Voice ending model. v6 keeps the v5 default: adsr = MRK +0x10 Key-Off followed by VAB ADSR2 Release."
    )
    ap.add_argument(
        "--reverb",
        choices=("on", "off"),
        default="on",
        help=(
            "Experimental v6 HALL reverb. 'on' uses MRK +0x0C bit3 as the "
            "per-event effect send; 'off' reproduces the validated v5 dry path."
        ),
    )
    ap.add_argument(
        "--reverb-trim",
        type=float,
        default=1.0,
        help=(
            "Additional multiplier applied after the recovered SPU2 effect-return "
            "volume. 1.0 = hardware-derived 0x3FFF Q15 level. Useful only for "
            "A/B testing while the remaining SPU2 resampling details are refined."
        ),
    )
    ap.add_argument(
        "--reverb-wet",
        type=Path,
        help="Optional output path for the wet reverb return only."
    )
    ap.add_argument(
        "--reverb-send",
        type=Path,
        help="Optional output path for the dry signal sent into the effect bus."
    )

    ap.add_argument(
        "--pan-law",
        choices=("linear", "equal-power"),
        default="linear",
        help=(
            "Stereo pan law. 'linear' is calibrated from automatic-play "
            "recordings and is the recommended default. 'equal-power' "
            "reproduces the legacy v1-v3 renderer."
        ),
    )
    ap.add_argument(
        "--no-normalize",
        action="store_true",
        help="Do not reduce the final mix if it exceeds full scale."
    )
    ap.add_argument(
        "--peak",
        type=float,
        default=0.995,
        help="Peak target when normalization is needed (default: 0.995)."
    )

    args = ap.parse_args()

    for p in (args.mus, args.mrk, args.vh, args.vb):
        if not p.is_file():
            ap.error(f"File not found: {p}")

    if args.seq is not None:
        if not args.seq.is_file():
            ap.error(f"SEQ file not found: {args.seq}")
        seq_bytes = args.seq.read_bytes()
        print(f"SEQ: {args.seq.name} ({len(seq_bytes)} bytes; renderer currently does not consume it)")

    vh = args.vh.read_bytes()
    vb = args.vb.read_bytes()
    mrk = args.mrk.read_bytes()
    mus = args.mus.read_bytes()

    npg, nt, nv, tones, sizes = parse_vh(vh)
    chunks = split_vb(vb, sizes)
    decoded = [decode_psx_adpcm(c)[0] for c in chunks]
    bg = mus_to_stereo(mus)

    # Parse once with a neutral 60 Hz clock to inspect structure/end marker.
    bpm_probe, boundaries_probe, events_probe, end_tick_probe = parse_mrk(mrk, 60.0)

    selected_profile = args.profile
    profile_reasons = []
    a00_end_error = e15_end_error = None

    if selected_profile == "auto":
        selected_profile, profile_reasons, a00_end_error, e15_end_error = choose_profile_auto(
            num_programs=npg,
            num_tones=nt,
            num_vags=nv,
            bpm=bpm_probe,
            events=events_probe,
            end_tick=end_tick_probe,
            mus_duration=len(bg) / RATE_MUS,
        )

    # Apply profile defaults. The profile deliberately controls the timing
    # constants; --gate remains an explicit override for experiments.
    if selected_profile == "tix":
        args.tick_hz = 60.0
        args.lead_sec = 3.0
        if args.gate is None:
            args.gate = "adsr"
    elif selected_profile == "namco":
        args.tick_hz = 60000.0 / 1001.0
        args.lead_sec = 2.9701
        if args.gate is None:
            args.gate = "adsr"
    else:
        raise ValueError(f"Unknown selected profile: {selected_profile}")

    # Reparse using the selected clock so reported event times are correct.
    bpm, boundaries, events, end_tick = parse_mrk(mrk, args.tick_hz)

    inst_dry, reverb_send_mix, skipped = render_instruments(
        events,
        tones,
        decoded,
        len(bg),
        tick_hz=args.tick_hz,
        lead_sec=args.lead_sec,
        note_offset=args.note_offset,
        pitch_cents=args.pitch_cents,
        instrument_gain=args.instrument_gain,
        gate_source=args.gate,
        pan_law=args.pan_law,
    )

    if args.reverb == "on":
        wet = spu2_hall_reverb(reverb_send_mix, RATE_MUS) * args.reverb_trim
    else:
        wet = np.zeros_like(inst_dry)

    inst = inst_dry + wet
    mix = bg + inst

    peak_before = float(np.max(np.abs(mix))) if len(mix) else 0.0
    scale = 1.0
    if not args.no_normalize and peak_before > args.peak:
        scale = args.peak / peak_before
        mix *= scale
        # Deliberately do not scale the optional instrument stem:
        # it remains at the calibrated game-relative instrument level.

    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_wav(args.out, mix)

    if args.instruments:
        args.instruments.parent.mkdir(parents=True, exist_ok=True)
        write_wav(args.instruments, inst)

    if args.reverb_wet:
        args.reverb_wet.parent.mkdir(parents=True, exist_ok=True)
        write_wav(args.reverb_wet, wet)

    if args.reverb_send:
        args.reverb_send.parent.mkdir(parents=True, exist_ok=True)
        write_wav(args.reverb_send, reverb_send_mix)

    if args.events_csv:
        args.events_csv.parent.mkdir(parents=True, exist_ok=True)
        with args.events_csv.open("w", newline="", encoding="utf-8-sig") as f:
            cols = list(events[0].keys()) if events else []
            w = csv.DictWriter(f, fieldnames=cols)
            if cols:
                w.writeheader()
                w.writerows(events)

    print()
    print("=== Technic Beat reconstruction ===")
    print(f"Requested profile       : {args.profile}")
    print(f"Selected profile        : {selected_profile}")
    if args.profile == "auto":
        for reason in profile_reasons:
            print(f"Auto-profile reason     : {reason}")
        if a00_end_error is not None:
            print(f"End error (a00 model)    : {a00_end_error:.6f} s")
            print(f"End error (e15 model)   : {e15_end_error:.6f} s")
    print(f"Programs / tones / VAGs : {npg} / {nt} / {nv}")
    print(f"MRK BPM                : {bpm}")
    print(f"MRK events             : {len(events)}")
    print(f"MRK section boundaries : {boundaries[:3]}")
    print(f"MRK end tick           : {end_tick if end_tick is not None else 'not detected'}")
    if end_tick is not None:
        print(f"MRK end time           : {end_tick / args.tick_hz:.6f} s (+ lead = {end_tick / args.tick_hz + args.lead_sec:.6f} s)")
    print(f"MUS duration           : {len(bg) / RATE_MUS:.6f} s")
    print(f"Tick clock             : {args.tick_hz:.9f} Hz")
    print(f"MRK -> MUS lead        : {args.lead_sec:.6f} s")
    print(f"Note offset            : {args.note_offset:+d}")
    print(f"Pitch correction       : {args.pitch_cents:+.3f} cents")
    print(f"Instrument gain        : {args.instrument_gain:.6f}")
    print(f"Gate                   : {args.gate}")
    print(f"Pan law                : {args.pan_law}")
    print(f"Reverb                 : {args.reverb}")
    print(f"Reverb trim            : {args.reverb_trim:.6f}")
    print(f"Reverb-send events     : {sum(bool(e.get('reverb_send', False)) for e in events)} / {len(events)}")
    print(f"Skipped unknown tones  : {skipped}")
    print(f"Final peak before norm : {peak_before:.6f}")
    print(f"Final normalization    : x{scale:.6f}")
    print(f"Wrote                  : {args.out}")
    if args.instruments:
        print(f"Instrument stem        : {args.instruments}")
    if args.reverb_wet:
        print(f"Reverb wet stem        : {args.reverb_wet}")
    if args.reverb_send:
        print(f"Reverb send stem       : {args.reverb_send}")
    if args.events_csv:
        print(f"Events CSV             : {args.events_csv}")


if __name__ == "__main__":
    main()
