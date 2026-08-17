# Technictix / Technic Beat MUS + MRK + VH/VB → WAV Renderer

- Technictix / Technic Beat の楽曲データから、伴奏（MUS）と演奏音（MRK + VAB）を合成してWAVへ復元するスクリプトです。
- 実装にあたって、 https://gist.github.com/Sakimotor/623c1fcd8470c208347300382b32cda3 を参考にしました。
- 基本的にすべての曲はサウンドトラックに収録されているため、よっぽどの事情がない限りはサウンドトラックで十分のはずです。個人で楽しんだりゲームを研究する用途で使ってください。
- 実装は `GPT-5.6 Sol` に手伝ってもらいました。ありがとうございます。

---

## 必要なファイル

基本的に1曲につき以下の4ファイルを使用します。

| ファイル | 用途 |
|---|---|
| `.mus` | 伴奏音声 |
| `.mrk` | 演奏イベント、タイミング、ノート、Velocity、Pan、Key-Off情報 |
| `.vh` | Sony VABヘッダ。Tone、Pitch、Volume、ADSR、VAG割り当てなど |
| `.vb` | Sony VABのPSX ADPCMサンプル |

- `.mrk`はアンダースコア付きのファイル `[a-f][0-9][0-9]_.mrk` を使用してください。譜面作成前の元データとして用意されたと思われるファイルのため、seqなしで演奏できます。
  - テクニクティクスの内部動作では、サウンドプレーヤー及びダンスエディットでアンダースコア付きのファイルが再生されるようになっています。
  - アンダースコア付きのファイルはテクニクティクスか、アーケード版テクニクビートにしか残っていません。ただし、PS2版にはPS2版で追加された楽曲のデータが残っています。
  - ゲーム中に使用されている `.mrk` と `.seq` の組み合わせからの復元は現状できません。私がどうにかするには難しすぎるからです。
- `.seq` はオプションで指定できますが、このスクリプトでは使用していません。指定した場合はファイルの存在とサイズだけを確認します。
- なお、`.mex`はパズルモード用のファイルなので、楽曲復元の際に使うことはないです。

---

## 必要環境

- Python 3
- NumPy

NumPyがない場合:

```bash
pip install numpy
```

---

## 基本的な使い方

```bash
python3 rebuild_technic_beat_v5.py \
    --mus e15.mus \
    --mrk e15_.mrk \
    --vh e15.vh \
    --vb e15.vb \
    --out e15.wav
    --profile {tix, namco, auto}
```

ティクス曲かナムコ曲（＆PS2新規曲）かで動作が変わるため、音がどんどんずれていくときは `--profile`で楽曲の種類を指定してください。  
デフォルト値は`auto`で、自動判定するようになっていますが、Mao's Travel (a08)等、判定に失敗する曲があります。

楽器演奏のみのWAVも同時に出力する場合:

```bash
python rebuild_technic_beat_v5.py \
    --mus e15.mus \
    --mrk e15_.mrk \
    --vh e15.vh \
    --vb e15.vb \
    --out e15.wav \
    --instruments e15_instruments.wav
```

デフォルト設定は、

```text
Profile         : auto
Gate            : adsr
Pan             : linear
Note offset     : -1
Pitch correction: +2.25 cents
Instrument gain : 0.837
Peak            : 0.995
```

が使用されます。

---

# 処理概要

レンダラーは大きく以下の処理を行います。

1. MUSを24 kHz / Stereo PCMとして展開
2. VHからProgram / Tone / VAG情報を取得
3. VB内のPSX ADPCMをデコード
4. MRKから演奏イベントを取得
5. ToneとMRK Noteから再生Pitchを計算
6. VAGサンプルをPitch変換
7. MRKのVelocity・PanとVHのVolumeを適用
8. MRK `+0x10` の時点でKey-Off
9. VAB ADSR2に従ってReleaseを適用
10. 楽器演奏とMUSを合成
11. 必要ならクリッピング防止のため最終Mixのみ正規化
12. WAVとして出力

出力サンプリングレートは **24,000 Hz** です。

---

# MUSのデコード

MUSは24 kHz / 16-bit PCMの左右チャンネルが特殊な順序で格納されています。

スクリプトでは固定値

```text
size       = 0x2EE00
filler     = 0x200
interleave = 0x17700
```

を使用して左右チャンネルを組み直し、Stereo PCMへ変換します。

詳細は https://hcs64.com/mboard/forum.php?showthread=60921&showpage=2 を参照してください。

---

# VAB（VH/VB）の処理

VHはSony VAB形式として解析します。

ファイル先頭の

```text
pBAV
```

を確認した後、以下を取得します。

- Program数
- Tone数
- VAG数
- Tone Volume
- Tone Pan
- Center Note
- Pitch Shift
- Min/Max Note
- ADSR1
- ADSR2
- 使用VAG番号

VBはVH末尾のVAGサイズテーブルに従って各サンプルへ分割します。

---

## PSX ADPCM

各VAGは16-byte単位のPSX ADPCMブロックとしてデコードします。

使用するPredictor係数は以下です。

```python
COEF = [
    (0, 0),
    (60, 0),
    (115, -52),
    (98, -55),
    (122, -60),
]
```

ADPCMはSPUの **44.1 kHz** サンプルとして扱い、Pitch変換後にレンダラーの24 kHzへ変換します。

---

# MRKの解析

MRKは **1レコード32 bytes** として処理します。

ヘッダからBPMとセクション境界を読み、その後の各レコードから演奏イベントを取得します。

主に使用するフィールドは以下です。

| Offset | 内容 |
|---:|---|
| `+0x00` | Event tick |
| `+0x10` | **Key-Offまでのtick数（uint16 LE）** |
| `+0x18` | Tone番号 |
| `+0x19` | Note |
| `+0x1A` | Velocity |
| `+0x1B` | Pan（signed int8） |

解析用として `+0x0C`, `+0x0D`, `+0x0F`, `+0x12`, `+0x1C`, `+0x1D` の値も保持します。

最終レコードが音声イベントではなくタイミング用の終了マーカーの場合は、演奏イベントから除外して曲終了tickとして使用します。

---

# タイミング

現在、2種類の動作プロファイルを持っています。

## `MÄRCHEN MAZE e15`

ナムコ曲で使用されている（と思われる）設定値

```text
MRK tick clock : 60000 / 1001 Hz ≒ 59.94005994 Hz
MRK → MUS lead : 2.9701 sec
Gate           : +0x10 Key-Off + ADSR Release
```

## `Days Dreamer a00`

ティクス曲で使用されている（と思われる）設定値

```text
MRK tick clock : 60 Hz
MRK → MUS lead : 3.000 sec
Gate           : +0x10 Key-Off + ADSR Release
```

演奏開始時刻は、

```text
start_sec = lead_sec + MRK_tick / tick_hz
```

で計算します。

---

# Profile自動判別

デフォルトは

```bash
--profile auto
```

です。

現在の自動判別では、VABが

```text
Programs <= 2
Tones    <= 32
VAGs     <= 32
```

の小規模Bankである場合、`tix` profileを選択します。

それ以外は原則として `namco` profileを使用します。

MRK終了マーカーとMUS長の一致度も参考情報として計算しますが、durationだけでprofileを決定しません。MUS末尾に無音paddingを持つ曲があるためです。

強制指定も可能です。

```bash
--profile namco
```

または

```bash
--profile tix
```

---

# Note / Pitch

デフォルトのNote mappingは、

```text
MRK Note - 1
```

です。

ToneのCenter Noteとの差、およびVHのPitch Shiftを加えて半音差を求めます。

```text
semitones =
    (MRK_note - 1 - center_note)
    + tone_shift
    + fine_pitch_correction
```

VHのPitch Shiftは現在、

```text
signed_value / 128
```

半音として解釈しています。

さらに実機自動演奏との比較から、デフォルトで

```text
+2.25 cents
```

の補正を加えています。

SPU Pitch値は、

```text
pitch = 0x1000 × 2^(semitones / 12)
```

として求め、`1 ～ 0x3FFF` の範囲へ制限します。

---

# Volume

楽器音量は、

```text
Tone Volume / 127
× MRK Velocity / 127
× instrument_gain
```

で計算します。

デフォルトの

```text
instrument_gain = 0.837
```

は、デコードしたMUSに対するゲーム内楽器音の相対音量として、自動演奏録音との比較から調整した値です。

---

# Pan

デフォルトは

```bash
--pan-law linear
```

です。

MRKのPanはsigned値として扱い、

```text
-64 ... 0 ... +63
```

の範囲へ制限します。

ゲームとの比較から、片側を最大音量のまま保持し、反対側だけを線形に減衰させる方式を使用しています。

```text
Pan < 0:
    Left  = 1.0
    Right = (64 + Pan) / 64

Pan > 0:
    Left  = (64 - Pan) / 64
    Right = 1.0

Pan = 0:
    Left = Right = 1.0
```

---

# VoiceOff / Key-Off

ゲーム側の処理を追跡した結果、MRK `+0x10` の値は **VoiceOffまでの時間** として扱う必要があることが分かりました。

レンダラーでは、

```text
keyoff_sec = MRK[+0x10] / tick_hz
```

としてKey-Off位置を求めます。

Key-Off後はVAB ToneのADSR2に従ってRelease phaseへ移行します。

したがって処理の順番は、

```text
Note On
   ↓
サンプル再生
   ↓
MRK +0x10 ticks
   ↓
Key-Off
   ↓
VAB ADSR2 Release
   ↓
Envelope = 0
```

となります。

---

# ADSR2 Release

ToneのADSR2から以下を使用します。

```text
bit 5     : Release Mode
bits 0-4  : Release Shift
```

Release Mode:

```text
0 = Linear decrease
1 = Exponential decrease
```

エンベロープはSPUと同じ **44.1 kHz** のクロックで生成し、その後24 kHzへリサンプリングします。

Key-Off以前の音量は実機録音に対して既にキャリブレーション済みのため、Attack / Decay / Sustainは意図的にシミュレートせず、Key-Off後のADSR2 Releaseのみを再現しています。

```text
Key-Off前 : envelope = 1.0
Key-Off後 : ADSR2 Releaseを適用
```

これにより、既存レンダラーで合わせた音量を壊さず、Voice終了部分だけを実機に近づけています。

---

## Releaseの特殊ケース

Release Shiftが

```text
31 (0x1F)
```

の場合、現在の実装ではRelease envelopeを進めず、自然なサンプル終端まで再生します。

---

# Gateモード

通常は変更する必要はありません。

v5のデフォルト:

```bash
--gate adsr
```

### `adsr`

推奨。

```text
MRK +0x10 → Key-Off → VAB ADSR2 Release
```

### `b18`

MRK `+0x12` の値をGate長として、その位置でサンプルを切ります。

```bash
--gate b18
```

### `next-event`

同じセクションの次イベントまで再生します。

```bash
--gate next-event
```

### `natural`

VoiceOff処理を行わず、VAGの自然な終端まで再生します。

```bash
--gate natural
```

---

# 出力とNormalization

伴奏と楽器を加算して最終Mixを作成します。

```text
final = MUS + instruments
```

ピークがデフォルトの

```text
0.995
```

を超えた場合のみ、最終Mix全体を縮小してクリッピングを防止します。

```bash
--peak 0.995
```

Normalizationを無効化する場合:

```bash
--no-normalize
```

`--instruments` で出力する楽器のみWAVには、この最終Normalizationを適用しません。ゲームとの比較用に、調整済みの相対楽器音量を維持するためです。

---

# オプション

## 必須

```text
--mus FILE
--mrk FILE
--vh FILE
--vb FILE
--out FILE
```

## 任意

```text
--seq FILE
--instruments FILE
--events-csv FILE
--profile {auto,namco,tix}
--gate {adsr,b18,natural,next-event}
--pan-law {linear,equal-power}
--tick-hz FLOAT
--lead-sec FLOAT
--note-offset INT
--pitch-cents FLOAT
--instrument-gain FLOAT
--peak FLOAT
--no-normalize
```

---

# Events CSV

解析用にMRKイベント一覧をCSVへ出力できます。

```bash
python rebuild_technic_beat_v5.py \
    --mus e15.mus \
    --mrk e15_.mrk \
    --vh e15.vh \
    --vb e15.vb \
    --out e15.wav \
    --events-csv e15_events.csv
```

CSVには、tick、section、Tone、Note、Velocity、Pan、Key-Off tickなど、レンダラーがMRKから取得した情報が出力されます。

---

# 調整例

## タイミングを手動指定

```bash
--tick-hz 59.94005994 --lead-sec 2.9701
```

ただし現在の実装ではprofile選択後にprofile既定値が適用されるため、`namco` / `tix` の通常利用ではprofile値が使用されます。

## Pitch補正

```bash
--pitch-cents 2.25
```

## 楽器音量

```bash
--instrument-gain 0.837
```

## Note mapping

```bash
--note-offset -1
```

## リバーブ

このレンダラーは、Technictix の Sound Playerで使用されるリバーブに対応しています。

リバーブはデフォルトで有効です。ゲームの IOP/libsd の解析から取得した HALL エフェクトのパラメータを使用し、Effect Send が設定されているイベントのみをリバーブへ送ります。ただし、 **現在の実装は実機SPU2のリバーブ処理を完全に再現したものではありません。**

リバーブを追加しても、ドライ音声のレンダリング処理には影響しません。

### オプション

#### `--reverb {on,off}`

リバーブ処理の有効・無効を切り替えます。

デフォルト：

```text
--reverb on
```

**リバーブありだと出力までに時間を要します。**

リバーブなしで出力する場合：

```bash
python rebuild_technic_beat.py ... --reverb off
```

リバーブを無効にすると、リバーブ処理前のドライな状態で出力できます。

---

#### `--reverb-trim VALUE`

最終的にミックスされるリバーブ（wet音）の音量を調整します。

デフォルト：

```text
--reverb-trim 1.0
```

例：

```bash
python rebuild_technic_beat.py ... --reverb-trim 0.8
```

`1.0` を指定すると、復元したリバーブモデルが生成したwet音を補正せずに使用します。

値を小さくするとリバーブ音だけが小さくなります。ドライ音の音量には影響しません。

ゲームの実行中にSPU2のEffect Return Volumeを調査したところ、左右とも以下の値が設定されていることを確認しています。

```text
EVOLL = 0x3FFF
EVOLR = 0x3FFF
```

---

#### `--reverb-send FILE.wav`

リバーブのEffect Busへ送られる音声だけを、別のWAVファイルとして出力します。

例：

```bash
python rebuild_technic_beat.py ... \
  --reverb-send reverb_send.wav
```

このファイルには、HALLリバーブが適用される前のEffect Send対象音声が収録されます。

どのイベント・楽器がリバーブへ送られているかを確認するためのデバッグ用途に使用できます。

---

#### `--reverb-wet FILE.wav`

リバーブ処理によって生成されたwet音だけを、別のWAVファイルとして出力します。

例：

```bash
python rebuild_technic_beat.py ... \
  --reverb-wet reverb_wet.wav
```

通常のドライ音は含まれず、HALLリバーブ処理と `--reverb-trim` の適用後に得られたリバーブ音だけが収録されます。

ゲームから録音した音声とリバーブの残響部分を比較する場合などに使用できます。

---

# 現在の既知の制限

- `.seq` の内容はレンダリングに使用していません。
- Key-Off以前の音量は実機録音に対して既にキャリブレーション済みのため、Attack / Decay / Sustainは意図的にシミュレートせず、Key-Off後のADSR2 Releaseのみを再現しています。
- ADSRはKey-Off後のReleaseのみを再現します。
- VAGのloop start情報はADPCM解析時に取得しますが、現在のレンダリングではループ再生を実装していません。
- Profile自動判別は、試作時に使用した `Days Dreamer (a00)` と `MARCHEN MAZE (e15)` の特徴をもとにしたヒューリスティックです。
- `tone_shift_semitones()` の解釈は、検証済みレンダラーから引き継いだ経験則です。
- MUSの格納形式については、現在確認済みのTechnic Beat / Technictixデータ構造を前提としています。
