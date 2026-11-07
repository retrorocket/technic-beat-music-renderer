#!/usr/bin/env python3
# Copyright (C) 2026 りゅー
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License in this repository's LICENSE file for details.

"""Technictix / Technic Beat MUS + MRK + VH/VB -> WAV renderer.

Usage:
  python3 technic_beat_music_renderer.py --mus e15.mus --mrk e15.mrk \
      --vh e15.vh --vb e15.vb --profile beat --out e15.wav \
      [--instruments e15_inst.wav]

Run with --help for all options.
"""

from __future__ import annotations

import argparse
import csv
import math
import struct
import wave
from pathlib import Path

import numpy as np

RATE_MUS = 48000
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

        if flags & 1:
            stop = len(out)
            repeat = bool(flags & 2)
            break
    else:
        repeat = False

    if stop is None:
        stop = len(out)

    if not repeat:
        loop_start = None
    elif loop_start is None:
        loop_start = 0

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
                "slot": program * 16 + tone_no,
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


def parse_mrk(mrk: bytes, tick_hz: float, phrase_mode: str = "auto", hit_velocity: bool = False):
    if len(mrk) % 32:
        raise ValueError("MRK size is not aligned to 32-byte records")

    records = [mrk[i:i + 32] for i in range(0, len(mrk), 32)]
    header = records[0]
    bpm = struct.unpack_from("<H", header, 2)[0]
    boundaries = [struct.unpack_from("<I", header, o)[0] for o in (20, 24, 28)]
    boundaries.append(len(records) + 1)

    events = []
    end_marker_tick = None
    section = 0
    next_boundary = boundaries[0]

    for idx, r in enumerate(records[1:], 1):
        while section < 3 and idx >= next_boundary:
            section += 1
            next_boundary = boundaries[section]

        tick = struct.unpack_from("<h", r, 0)[0]

        if tick == -1:
            continue

        flags0c = struct.unpack_from("<I", r, 12)[0]

        if flags0c & 0x80000000:
            if end_marker_tick is None:
                end_marker_tick = tick
            continue

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
            "step_ticks": struct.unpack_from("<H", r, 18)[0],
            "ball": struct.unpack_from("<H", r, 2)[0],
            "group": struct.unpack_from("<H", r, 4)[0],
            "step": r[6],
            "member": r[7],
            "chart_tick": tick,
            "b28": r[28],
            "b29": r[29],
        })

    end_tick = end_marker_tick
    if end_tick is None and len(records) >= 2:
        last = records[-1]
        if last[24:28] == b"\0\0\0\0":
            end_tick = struct.unpack_from("<h", last, 0)[0]

    is_gameplay_chart = any(e["ball"] for e in events)
    if phrase_mode == "on" or (phrase_mode == "auto" and is_gameplay_chart):
        expand_gameplay_phrases(events, tick_hz, hit_velocity=hit_velocity)
    events.sort(key=lambda e: (e["section"], e["tick"], e["record"]))

    return bpm, boundaries, events, end_tick


def expand_gameplay_phrases(events, tick_hz: float, hit_velocity: bool = False):
    groups = {}
    for e in events:
        if e["ball"]:
            groups.setdefault((e["chart_tick"], e["ball"]), []).append(e)

    for members in groups.values():
        steps = [e["step"] for e in members]
        if len(set(steps)) < len(steps) and all(e["group"] for e in members):
            members.sort(key=lambda e: (e["member"], e["record"]))
        else:
            members.sort(key=lambda e: (e["step"], e["record"]))
        t = members[0]["chart_tick"]
        for m in members:
            m["tick"] = t
            m["time_sec"] = t / tick_hz
            t += m["step_ticks"]
        lead = members[0]
        if hit_velocity and not (lead["flags0c"] & 0x10000):
            lead["velocity"] = (lead["velocity"] * 2) & 0xFF


def mus_to_stereo(mus: bytes, channels: int = 2, fmt: str = "s8-48k"):
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

    if fmt == "s8-48k":
        left = np.frombuffer(b"".join(c1), dtype=np.int8).astype(np.float32) / 128.0
        right = np.frombuffer(b"".join(c2), dtype=np.int8).astype(np.float32) / 128.0
        return np.column_stack([left, right])

    left = np.frombuffer(b"".join(c1), dtype="<i2").astype(np.float32) / 32768.0
    right = np.frombuffer(b"".join(c2), dtype="<i2").astype(np.float32) / 32768.0
    x = np.column_stack([left, right])
    return _resample_stereo(x, 24000, RATE_MUS, out_len=len(x) * 2)


def tone_shift_semitones(raw: int):
    v = raw if raw < 128 else raw - 256
    return v / 128.0


def _spu2_gauss_table():
    table = [0.0] * 512
    for n in range(512):
        k = 0.5 + n
        s_ = math.sin(math.pi * k * 2.048 / 1024)
        t_ = (math.cos(math.pi * k * 2.000 / 1023) - 1) * 0.50
        u_ = (math.cos(math.pi * k * 4.000 / 1023) - 1) * 0.08
        table[511 - n] = s_ * (t_ + u_ + 1.0) / k
    scale = 0x7F80 * 128 / sum(table)
    table = [x * scale for x in table]

    def rnd(x):
        return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))

    res = np.zeros((256, 4), dtype=np.int64)
    for ph in range(256):
        ps = table[ph] + table[ph + 256] + table[511 - ph] + table[255 - ph]
        d = (ps - 0x7F80) / 4
        res[255 - ph] = [
            rnd(table[ph] - d), rnd(table[ph + 256] - d),
            rnd(table[511 - ph] - d), rnd(table[255 - ph] - d),
        ]
    return res


SPU2_GAUSS = _spu2_gauss_table()

NOTE_PITCH_TABLE = [32768, 34716, 36780, 38967, 41285, 43740, 46340, 49096, 52015, 55108, 58385, 61857, 32768, 32782, 32797, 32812, 32827, 32842, 32856, 32871, 32886, 32901, 32916, 32931, 32945, 32960, 32975, 32990, 33005, 33020, 33035, 33050, 33065, 33080, 33094, 33109, 33124, 33139, 33154, 33169, 33184, 33199, 33214, 33229, 33244, 33259, 33274, 33289, 33304, 33319, 33334, 33349, 33364, 33379, 33394, 33410, 33425, 33440, 33455, 33470, 33485, 33500, 33515, 33530, 33546, 33561, 33576, 33591, 33606, 33621, 33636, 33652, 33667, 33682, 33697, 33712, 33728, 33743, 33758, 33773, 33789, 33804, 33819, 33834, 33850, 33865, 33880, 33896, 33911, 33926, 33941, 33957, 33972, 33987, 34003, 34018, 34033, 34049, 34064, 34080, 34095, 34110, 34126, 34141, 34157, 34172, 34187, 34203, 34218, 34234, 34249, 34265, 34280, 34296, 34311, 34327, 34342, 34358, 34373, 34389, 34404, 34420, 34435, 34451, 34466, 34482, 34497, 34513, 34528, 34544, 34560, 34575, 34591, 34606, 34622, 34638, 34653, 34669, 34685, 34700]


def sd_note2pitch(center_note: int, center_fine: int, note: int, fine: int = 0) -> int:
    def cdiv(a, b):
        q = abs(a) // abs(b)
        return q if (a >= 0) == (b >= 0) else -q

    _fine = fine + (center_fine & 0xFFFF)
    _fine2 = _fine + 127 if _fine < 0 else _fine
    _fine2 = cdiv(_fine2, 128)
    _note = (note & 0xFFFF) + _fine2 - (center_note & 0xFFFF)
    val3 = cdiv(_note, 6)
    if _note < 0:
        val3 -= 1
    offset2 = _fine - _fine2 * 128
    val2 = -1 if _note < 0 else 0
    if val3 < 0:
        val3 -= 1
    val2 = cdiv(val3, 2) - val2
    val = val2 - 2
    offset1 = _note - (val2 * 12)
    if offset1 < 0 or (offset1 == 0 and offset2 < 0):
        offset1 += 12
        val = val2 - 3
    if offset2 < 0:
        offset1 = (offset1 - 1) + _fine2
        offset2 += (_fine2 + 1) * 128
    ret = (NOTE_PITCH_TABLE[offset1] * NOTE_PITCH_TABLE[offset2 + 12]) // 0x10000
    if val < 0:
        ret = (ret + (1 << (-val - 1))) >> -val
    return ret & 0xFFFF


def driver_pitch(tone, note: int) -> int:
    p = sd_note2pitch(tone["center"] + 1, tone["shift_raw"] + 0x47, note - 2, 0)
    return max(1, min(0x3FFF, p))


def legacy_pitch(tone, note: int, note_offset: int, pitch_cents: float) -> int:
    semitones = (note + note_offset - tone["center"]) + tone_shift_semitones(tone["shift_raw"]) \
        + pitch_cents / 100.0
    p = 0x1000 * (2.0 ** (semitones / 12.0)) * RATE_SPU / RATE_MUS
    return max(1, min(0x3FFF, int(round(p))))


DRIVER_ADSR1 = 0x80FF
DRIVER_ADSR2 = 0x1FC0

_PH_ATTACK, _PH_DECAY, _PH_SUSTAIN, _PH_RELEASE = 1, 2, 3, 4


def spu2_adsr(adsr1: int, adsr2: int, keyoff, n_max: int) -> np.ndarray:
    attack_mode = (adsr1 >> 15) & 1
    attack_shift = (adsr1 >> 10) & 0x1F
    attack_step = (adsr1 >> 8) & 3
    decay_shift = (adsr1 >> 4) & 0xF
    sustain_level = adsr1 & 0xF
    sustain_mode = (adsr2 >> 15) & 1
    sustain_dir = (adsr2 >> 14) & 1
    sustain_shift = (adsr2 >> 8) & 0x1F
    sustain_step = (adsr2 >> 6) & 3
    release_mode = (adsr2 >> 5) & 1
    release_shift = adsr2 & 0x1F

    sus_step = 7 - sustain_step
    if sustain_dir:
        sus_step = ~sus_step
    phases = {
        _PH_ATTACK: (False, bool(attack_mode), attack_shift, 7 - attack_step, 0x7FFF),
        _PH_DECAY: (True, True, decay_shift, -8, (sustain_level + 1) << 11),
        _PH_SUSTAIN: (bool(sustain_dir), bool(sustain_mode), sustain_shift, sus_step, 0),
        _PH_RELEASE: (True, bool(release_mode), release_shift, -8, 0),
    }

    env = np.zeros(max(0, n_max), dtype=np.int32)
    n = 0
    value = 0
    counter = 0
    phase = _PH_ATTACK
    keyoff = None if keyoff is None else max(0, int(keyoff))

    while n < n_max:
        if keyoff is not None and phase != _PH_RELEASE and n >= keyoff:
            phase = _PH_RELEASE
            counter = 0

        decr, exp, shift, step, target = phases[phase]
        inc = 0x8000 >> max(0, shift - 11)
        lvl = step << max(0, 11 - shift)
        if exp:
            if not decr and value > 0x6000:
                inc >>= 2
            if decr:
                lvl = ((lvl * value) >> 15)
                lvl = ((lvl + 0x8000) & 0xFFFF) - 0x8000
        inc = max(1, inc)

        k = -(-(0x8000 - counter) // inc)
        limit = n_max - n
        if keyoff is not None and phase != _PH_RELEASE:
            limit = min(limit, keyoff - n)

        def reached(v):
            return (not decr and v >= target) or (decr and v <= target)

        if phase != _PH_SUSTAIN and reached(value):
            counter += inc
            if counter >= 0x8000:
                counter = 0
                value = min(0x7FFF, max(0, value + lvl))
            env[n] = value
            n += 1
            phase += 1
            if phase > _PH_RELEASE:
                return env[:n - 1]
            continue

        if k > limit:
            env[n:n + limit] = value
            counter += inc * limit
            n += limit
            continue

        env[n:n + k - 1] = value
        n += k - 1
        counter = 0
        value = min(0x7FFF, max(0, value + lvl))
        env[n] = value
        n += 1

        if phase == _PH_SUSTAIN:
            if value == 0:
                return env[:n - 1]
            continue
        if reached(value):
            phase += 1
            if phase > _PH_RELEASE:
                return env[:n - 1]

    return env


def spu2_voice(src: np.ndarray, loop_start, pitch: int, n_max: int) -> np.ndarray:
    smp = np.round(np.asarray(src, dtype=np.float64) * 32768.0).astype(np.int64)
    n_src = len(smp)
    if n_src == 0 or n_max <= 0:
        return np.zeros(0, dtype=np.float32)

    if loop_start is None:
        n_out = min(n_max, int(-(-(n_src << 12) // pitch)))
    else:
        n_out = n_max

    pos = np.arange(n_out, dtype=np.int64) * pitch
    idx = pos >> 12
    phase = (pos >> 4) & 0xFF

    out = np.zeros(n_out, dtype=np.int64)
    for k in range(4):
        i = idx + k
        if loop_start is None:
            v = np.zeros(n_out, dtype=np.int64)
            ok = i < n_src
            v[ok] = smp[i[ok]]
        else:
            span = max(1, n_src - loop_start)
            j = np.where(i < n_src, i, loop_start + (i - loop_start) % span)
            v = smp[j]
        out += (SPU2_GAUSS[phase, k] * v) >> 15

    return (np.clip(out, -32768, 32767) / 32768.0).astype(np.float32)


def render_spu2_note(src, loop_start, pitch, adsr1, adsr2, keyoff, n_max):
    if loop_start is None:
        natural = int(-(-(len(src) << 12) // pitch))
        n_max = min(n_max, natural)
    env = spu2_adsr(adsr1, adsr2, keyoff, n_max)
    if len(env) == 0:
        return np.zeros(0, dtype=np.float32)
    y = spu2_voice(src, loop_start, pitch, len(env))
    n = min(len(y), len(env))
    return (y[:n] * (env[:n].astype(np.float32) / 32768.0)).astype(np.float32)


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


def spu2_hall_reverb(send: np.ndarray, out_rate=RATE_MUS):
    if len(send) == 0 or not np.any(send):
        return np.zeros_like(send)

    p = HALL_REVERB

    down_coef = np.array([
        -1, 0, 2, 0, -10, 0, 35, 0, -103, 0,
        266, 0, -616, 0, 1332, 0, -2960, 0,
        10246, 16384, 10246, 0, -2960, 0, 1332, 0,
        -616, 0, 266, 0, -103, 0, 35, 0, -10, 0, 2, 0, -1
    ], dtype=np.float64) / 32768.0

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

    x48 = _resample_stereo(send, out_rate, SPU2_RATE)

    ds = np.empty_like(x48, dtype=np.float32)
    for ch in range(2):
        conv = np.convolve(x48[:, ch].astype(np.float64), down_coef, mode="full")
        ds[:, ch] = np.concatenate(([0.0], conv[:len(x48)-1])).astype(np.float32)

    def q15(name):
        return _q15(p[name])

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

    wet48 = np.empty_like(sparse, dtype=np.float32)
    for ch in range(2):
        conv = np.convolve(sparse[:, ch].astype(np.float64), up_coef, mode="full")
        wet48[:, ch] = conv[:len(sparse)].astype(np.float32)

    wet48 *= EFFECT_RETURN_VOLUME / 32768.0

    return _resample_stereo(wet48, SPU2_RATE, out_rate, out_len=len(send))


def beat_lead_adjust(chart_tick: int) -> int:
    if chart_tick > 180:
        return 0
    if chart_tick > -60:
        return 1
    return 119 - 178 - chart_tick


def beat_seq_volume_model(end_tick, lead_ticks=180):
    if end_tick is None:
        return lambda tick: 63.0 / 64.0
    E = end_tick + 60

    def gain(tick):
        timer = tick + lead_ticks
        value = 63
        if timer >= E - 128:
            x = E - timer - 1
            value = max(0, int(x / 2))
        return value / 64.0

    return gain


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
    pitch_model="driver",
    adsr_source="driver",
    seq_volume_model=None,
):
    mix = np.zeros((out_len, 2), dtype=np.float32)
    reverb_send_mix = np.zeros((out_len, 2), dtype=np.float32)
    tone_map = {t["slot"]: t for t in tones}
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

            src, loop_start = decoded[t["vag"] - 1]

            if pitch_model == "driver":
                pitch = driver_pitch(t, e["note"])
            else:
                pitch = legacy_pitch(t, e["note"], note_offset, pitch_cents)

            if adsr_source == "driver":
                adsr1, adsr2 = DRIVER_ADSR1, DRIVER_ADSR2
            else:
                adsr1, adsr2 = t["adsr1"], t["adsr2"]

            start_sec = lead_sec + (e["tick"] + e.get("lead_adjust", 0)) / tick_hz
            start = int(round(start_sec * RATE_MUS))
            if start >= out_len:
                continue

            keyoff = None
            if gate_source == "adsr":
                keyoff = int(round(e["keyoff_ticks"] / tick_hz * RATE_MUS))
            elif gate_source == "b18":
                keyoff = int(round(e["b18"] / tick_hz * RATE_MUS))
            elif gate_source == "next-event":
                if event_index + 1 < len(section_events):
                    next_e = section_events[event_index + 1]
                    keyoff = int(round((next_e["tick"] - e["tick"]) / tick_hz * RATE_MUS))
            elif gate_source == "natural":
                pass
            else:
                raise ValueError(f"Unknown gate source: {gate_source}")

            key = (t["vag"], pitch, adsr1, adsr2, keyoff, loop_start is None)
            if key not in cache or len(cache[key]) < 1:
                cache[key] = render_spu2_note(
                    src, loop_start, pitch, adsr1, adsr2, keyoff,
                    n_max=out_len - max(0, start),
                )
            y = cache[key]

            gain = (
                (t["volume"] / 127.0)
                * (e["velocity"] / 127.0)
                * instrument_gain
            )
            if seq_volume_model is not None:
                gain *= seq_volume_model(e["tick"])

            p = max(-64, min(63, e["pan"]))

            if pan_law == "linear":
                if p < 0:
                    gl = 1.0
                    gr = (64.0 + p) / 64.0
                elif p > 0:
                    gl = (64.0 - p) / 64.0
                    gr = 1.0
                else:
                    gl = gr = 1.0
            elif pan_law == "equal-power":
                angle = (p + 64) / 127.0 * (math.pi / 2.0)
                gl = math.cos(angle) * math.sqrt(2.0)
                gr = math.sin(angle) * math.sqrt(2.0)
            else:
                raise ValueError(f"Unknown pan law: {pan_law}")

            end = min(out_len, start + len(y))
            skip = max(0, -start)
            start = max(0, start)

            if end <= start:
                continue

            z = y[skip:skip + end - start] * gain
            zl = z * gl
            zr = z * gr
            mix[start:end, 0] += zl
            mix[start:end, 1] += zr

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


def main():
    ap = argparse.ArgumentParser(
        description="Reconstruct Technic Beat music from MUS + MRK + VH/VB."
    )
    ap.add_argument("--mus", required=True, type=Path)
    ap.add_argument("--mrk", required=True, type=Path)
    ap.add_argument("--vh", required=True, type=Path)
    ap.add_argument("--vb", required=True, type=Path)
    ap.add_argument("--seq", type=Path, help="Optional. Accepted but not used.")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--instruments", type=Path)
    ap.add_argument("--events-csv", type=Path)

    ap.add_argument("--tick-hz", type=float, default=60000.0 / 1001.0)
    ap.add_argument("--lead-sec", type=float, default=2.9701)
    ap.add_argument("--note-offset", type=int, default=-1)
    ap.add_argument("--pitch-cents", type=float, default=2.25)
    ap.add_argument("--instrument-gain", type=float, default=0.837)
    ap.add_argument(
        "--profile",
        choices=("beat", "tix"),
        required=True,
        help="beat: Technic Beat new songs (ste/stf). tix: Technictix songs (incl. Technic Beat sta-std).",
    )
    ap.add_argument(
        "--gate",
        choices=("adsr", "b18", "natural", "next-event"),
        default=None,
        help="Note ending model (default: adsr)."
    )
    ap.add_argument(
        "--reverb",
        choices=("on", "off"),
        default="on",
        help="Reverb on/off (default: on).",
    )
    ap.add_argument(
        "--reverb-trim",
        type=float,
        default=1.0,
        help="Reverb level multiplier (default: 1.0).",
    )
    ap.add_argument(
        "--reverb-wet",
        type=Path,
        help="Optional output path for the reverb return only."
    )
    ap.add_argument(
        "--reverb-send",
        type=Path,
        help="Optional output path for the signal sent to the reverb."
    )

    ap.add_argument(
        "--mus-format",
        choices=("s8-48k", "s16-24k"),
        default="s8-48k",
        help="MUS sample format (default: s8-48k).",
    )
    ap.add_argument(
        "--pitch-model",
        choices=("driver", "legacy"),
        default="driver",
        help="Pitch calculation (default: driver). legacy uses --note-offset/--pitch-cents.",
    )
    ap.add_argument(
        "--adsr",
        choices=("driver", "vab"),
        default="driver",
        help="Envelope source (default: driver).",
    )
    ap.add_argument(
        "--phrases",
        choices=("auto", "on", "off"),
        default="auto",
        help="Phrase expansion for gameplay charts (default: auto).",
    )
    ap.add_argument(
        "--hit-velocity",
        action="store_true",
        help="Gameplay charts only: play struck notes at hit velocity.",
    )
    ap.add_argument(
        "--pan-law",
        choices=("linear", "equal-power"),
        default="linear",
        help="Stereo pan law (default: linear).",
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
    decoded = [decode_psx_adpcm(c) for c in chunks]
    bg = mus_to_stereo(mus, fmt=args.mus_format)

    selected_profile = args.profile

    TIX_LEAD_TICKS = 180
    BEAT_LEAD_TICKS = 180

    if selected_profile == "tix":
        args.tick_hz = 60.0
        args.lead_sec = TIX_LEAD_TICKS / args.tick_hz
        if args.gate is None:
            args.gate = "adsr"
    elif selected_profile == "beat":
        args.tick_hz = 60000.0 / 1001.0
        args.lead_sec = BEAT_LEAD_TICKS / args.tick_hz
        if args.gate is None:
            args.gate = "adsr"
    else:
        raise ValueError(f"Unknown selected profile: {selected_profile}")

    bpm, boundaries, events, end_tick = parse_mrk(
        mrk, args.tick_hz, phrase_mode=args.phrases, hit_velocity=args.hit_velocity
    )
    if selected_profile == "beat":
        for e in events:
            e["lead_adjust"] = beat_lead_adjust(e["chart_tick"])

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
        pitch_model=args.pitch_model,
        adsr_source=args.adsr,
        seq_volume_model=(
            beat_seq_volume_model(end_tick) if selected_profile == "beat" else None
        ),
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
        if tix_end_error is not None:
            print(f"End error (Technictix model)    : {tix_end_error:.6f} s")
            print(f"End error (TechnicBeat model)   : {beat_end_error:.6f} s")
    print(f"Programs / tones / VAGs : {npg} / {nt} / {nv}")
    print(f"MRK BPM                : {bpm}")
    print(f"MRK events             : {len(events)}")
    print(f"MRK section boundaries : {boundaries[:3]}")
    print(f"MRK end tick           : {end_tick if end_tick is not None else 'not detected'}")
    if end_tick is not None:
        print(f"MRK end time           : {end_tick / args.tick_hz:.6f} s (+ lead = {end_tick / args.tick_hz + args.lead_sec:.6f} s)")
    print(f"MUS duration           : {len(bg) / RATE_MUS:.6f} s")
    print(f"Tick clock             : {args.tick_hz:.9f} Hz")
    if selected_profile == "tix":
        lead_ticks = TIX_LEAD_TICKS
    else:
        lead_ticks = BEAT_LEAD_TICKS
    print(
        f"MRK -> MUS lead        : {args.lead_sec:.6f} s "
        f"({lead_ticks} ticks @ {args.tick_hz:.9f} Hz)"
    )
    print(f"Output / SPU2 clock    : {RATE_MUS} Hz")
    print(f"MUS format             : {args.mus_format}")
    print(f"Pitch model            : {args.pitch_model}")
    if args.pitch_model == "legacy":
        print(f"Note offset            : {args.note_offset:+d}")
        print(f"Pitch correction       : {args.pitch_cents:+.3f} cents")
    print(f"ADSR source            : {args.adsr}")
    if selected_profile == "beat":
        print("Seq voice gain         : on")
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
