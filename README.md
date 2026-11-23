# Technictix / Technic Beat MUS + MRK + VH/VB → WAV Renderer

[日本語](./README_ja.md)

- An experimental script that reconstructs music from Technictix / Technic Beat game data into WAV files by mixing the backing track (MUS) with the instrument performance (MRK + VAB).
  - This repository does not contain any files extracted from the games.
- The implementation was developed with reference to [Sakimotor's Gist](https://gist.github.com/Sakimotor/623c1fcd8470c208347300382b32cda3).
- This script was created out of personal technical interest. When using data derived from the games, you are responsible for complying with all applicable copyrights and other rights.
- The implementation was developed with assistance from `GPT-5.6 Sol` and `Opus 5.5`. Thank you.

## License

This project is licensed under the [GNU General Public License v3.0 or later (GPL-3.0-or-later)](LICENSE).

Copyright (C) 2026 りゅー

Copyright for portions derived from third-party works belongs to their respective copyright holders.

## Credits

- [PCSX2](https://github.com/PCSX2/pcsx2) — Reference and implementation source for SPU2 reverb, as well as the source of the 39-tap FIR coefficients in `ReverbResample.cpp`. PCSX2 is licensed under GPL-3.0-or-later, and copyright for the derived portions belongs to the respective PCSX2 contributors.
- [Sakimotor](https://gist.github.com/Sakimotor/623c1fcd8470c208347300382b32cda3) — Implementation/reference source for the Technic Beat MUS data rearrangement process. The original code has been approved for use under the MIT License in [the response to the licensing inquiry on the Gist](https://gist.github.com/Sakimotor/623c1fcd8470c208347300382b32cda3). The GPL license applied to this project as a whole does not alter the license of Sakimotor's original code.
- [almendaz / HCS64 forum](https://hcs64.com/) — Author of the original analysis of the MUS format and data rearrangement algorithm posted on the HCS64 forum.

---

## Usage

### Requirements

- Python 3
- NumPy (`pip3 install numpy`)

### Input Files

Four files are required for each track.

| Option | File |
| --- | --- |
| `--mus` | Backing track (`.mus`) |
| `--mrk` | Note chart (`.mrk`) |
| `--vh` | Instrument header (`.vh`) |
| `--vb` | Instrument data (`.vb`) |

There are two types of `.mrk` files. Either type can be converted to WAV.

- `.mrk` files without `_` are the charts actually performed by the player during gameplay.
- `.mrk` files with `_` are autoplay charts used by the SOUND PLAYER. For some tracks, the SOUND PLAYER performance differs from the in-game performance.

| Game | SOUND PLAYER `.mrk` availability |
| --- | --- |
| Technictix | Available for all tracks |
| Technic Beat (Arcade) | Available for all tracks |
| Technic Beat (PS2) | Available only for the new PS2 tracks, except LOVE MAHJONG |

See [musiclist.md](musiclist.md) for the mapping between filenames and track titles.

### Notes

For PS2 tracks e05–e14 and e19, the included `zmus` files are versions of the arcade `zmus` played 1.001× faster. The note charts are identical between the two versions.

The `ste` timer advances on VBlank (approximately 59.94 Hz), so the sound at tick N is played at N ÷ 59.94 seconds. The arcade `zmus` files are synchronized to this timing, whereas the PS2 versions of these 11 tracks have durations corresponding to N ÷ 60 seconds.

As a result, as the track progresses, the chart audio gradually falls behind the backing track by approximately 0.1%. For example, the offset reaches approximately 50 ms near the end of e05.

### Example

```bash
python3 technic_beat_music_renderer.py \
    --mus ./zmus/ste/e15.mus \
    --mrk ./seq/ste/e15.mrk \
    --vh ./snd/ste/e15.vh \
    --vb ./snd/ste/e15.vb \
    --profile beat \
    --out e15.wav
```

To also output a WAV containing only the instrument performance, add:

```text
--instruments e15_instruments.wav
```

### `--profile` (Required)

| Value | Target |
| --- | --- |
| `tix` | Tracks from Technictix (including sta–std tracks in Technic Beat) |
| `beat` | Additional tracks from Technic Beat (ste / stf) |

### Main Options

| Option | Description |
| --- | --- |
| `--out FILE` | WAV output path (required) |
| `--instruments FILE` | Output an instrument-only WAV (volume is not normalized) |
| `--events-csv FILE` | Export a list of chart events as CSV |
| `--reverb {on,off}` | Apply reverb according to the chart data (default: `on`) |
| `--reverb-trim VALUE` | Reverb level multiplier (default: 1.0) |
| `--reverb-wet FILE` / `--reverb-send FILE` | Output the reverb component / reverb input as separate WAV files |
| `--phrases {auto,on,off}` | Phrase expansion for gameplay charts (default: `auto`) |
| `--hit-velocity` | For gameplay charts, play markers triggered by the round wave at the velocity used when they are triggered |
| `--no-normalize` | Do not reduce the volume of the final mix |
| `--peak VALUE` | Peak level used for normalization (default: 0.995) |

Run `--help` to see the remaining options.
