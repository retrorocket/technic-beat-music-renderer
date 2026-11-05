# Technictix / Technic Beat MUS + MRK + VH/VB → WAV Renderer

- Technictix / Technic Beat の楽曲データから、伴奏（MUS）と演奏音（MRK + VAB）を合成して楽曲をWAVへ復元する実験的なスクリプトです。
  - **このリポジトリにゲーム由来のファイルは含まれていません。**
- 実装にあたって、 <https://gist.github.com/Sakimotor/623c1fcd8470c208347300382b32cda3> を参考にしました。
- **このスクリプトは個人の技術的な興味から作成されたものです。** ゲーム由来のデータの利用については、各自で適用される著作権その他の権利を遵守してください。
- 実装は `GPT-5.6 Sol` と `Opus 5.5` に手伝ってもらいました。ありがとうございます。

## License

このプロジェクトは [GNU General Public License v3.0 or later (GPL-3.0-or-later)](./LICENSE) で提供されます。

Copyright (C) 2026 りゅー。第三者由来の部分の著作権は、それぞれの権利者に帰属します。

## Credits

- [PCSX2](https://github.com/PCSX2/pcsx2) — SPU2 reverb の実装・参照元、および `ReverbResample.cpp` の39-tap FIR係数の出典です。PCSX2 は GPL-3.0-or-later で提供されており、由来する部分の著作権は各 PCSX2 contributor に帰属します。
- [Sakimotor](https://gist.github.com/Sakimotor/623c1fcd8470c208347300382b32cda3) — Technic Beat MUSデータの並べ替え処理の実装・参照元です。元のコードについては、[Gist上のライセンス問い合わせへの回答](https://gist.github.com/Sakimotor/623c1fcd8470c208347300382b32cda3#gistcomment-6316149)でMITとして扱うことを了承されています。このプロジェクト全体のGPL表記は、Sakimotor氏の元のコードのライセンスを変更するものではありません。
- [almendaz / HCS64 forum](https://hcs64.com/mboard/forum.php?showthread=60921) — MUS形式とデータ並べ替えアルゴリズムの元になった解析をHCS64 forumに投稿された方です。

---

## 使い方

### 必要環境

- Python 3
- NumPy（`pip3 install numpy`）

### 入力ファイル

1曲につき次の4ファイルを指定します。

| オプション | ファイル |
| --- | --- |
| `--mus` | 伴奏（`.mus`） |
| `--mrk` | 譜面（`.mrk`） |
| `--vh` | 音色ヘッダ（`.vh`） |
| `--vb` | 音色データ（`.vb`） |

`.mrk` は2種類存在します。

- `_` なしの `.mrk` はゲームで実際にプレイヤーが演奏する譜面です。
- `_` 付きの `.mrk` はSOUND PLAYERで使用される自動演奏用の譜面です。

|対象|SOUND PLAYER用 `.mrk` の有無|
|:----|:----|
|テクニクティクス|全曲分有り|
|AC版テクニクビート|全曲分有り|
|PS2版テクニクビート|LOVE MAHJONGを除くPS2新規曲のみ|

ファイル名と曲名の対応表は [muliclist.md](./musiclist.md) を参照してください。

### 注意点

PS2版の e05〜e14 と e19 の zmus は、アーケード版の zmus を1.001倍速くしたものが収録されています。
譜面は両版で同じです。
`ste` のタイマーは VBlank（約59.94 Hz）で進むため、tick N の音は N ÷ 59.94 秒で鳴ります。アーケード版の zmus はこの時刻に合っていますが、PS2版の11曲は N ÷ 60 秒に合う長さになっているため、曲が進むにつれて譜面の音が伴奏より約0.1%ずつ遅れていきます（e05 では終盤で約50msずれます）。

### 実行例

```bash
python3 technic_beat_music_renderer.py \
    --mus e15.mus \
    --mrk e15.mrk \
    --vh e15.vh \
    --vb e15.vb \
    --profile beat \
    --out e15.wav
```

楽器演奏のみのWAVも同時に出力する場合は、`--instruments e15_instruments.wav` を追加します。

### `--profile`（必須）

| 値 | 対象 |
| --- | --- |
| `tix` | テクニクティクスの収録曲（テクニクビートの sta〜std を含む） |
| `beat` | テクニクビートの新曲（ste / stf） |

### 主なオプション

| オプション | 内容 |
| --- | --- |
| `--out FILE` | 出力WAV（必須） |
| `--instruments FILE` | 楽器演奏のみのWAVを出力（音量の正規化はしません） |
| `--events-csv FILE` | 譜面のイベント一覧をCSVで出力 |
| `--reverb {on,off}` | リバーブ（既定: `on`） |
| `--reverb-trim VALUE` | リバーブ量の倍率（既定: 1.0） |
| `--reverb-wet FILE` / `--reverb-send FILE` | リバーブ成分 / リバーブへの入力を別WAVで出力 |
| `--phrases {auto,on,off}` | ゲームプレイ用譜面のフレーズ展開（既定: `auto`） |
| `--hit-velocity` | ゲームプレイ用譜面で、ラウンドウェーブで反応させたマーカーを反応時の音量で鳴らす |
| `--no-normalize` | 最終ミックスの音量を下げない |
| `--peak VALUE` | 正規化するときのピーク値（既定: 0.995） |

その他のオプションは `--help` で確認できます。
