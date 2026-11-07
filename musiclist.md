# PS2 テクニクビートの楽曲の管理情報

テクニクティクス～AC版でも同様の値が使用されている。AC版はおそらく `0x78`がエンディングに該当する。

- PS2の日本版テクニクビートであれば `0x00285680` アドレスの値を呼び出したい曲の値に変更することで、フリーモード一番先頭の曲から任意の曲を呼び出せる。
  - デフォルト値は Days Dreamer `0x00`
- AC版テクニクビートであれば、ノーマルモード 1st Priodの一番先頭の曲 `0x0036B2F0` を呼び出したい曲の値に変更することで、同様に任意の曲を呼び出せる。
  - デフォルト値は Days Dreamer `0x00`

Hard Ver.は `.mus` と `.vh`, `.vb` を通常曲と共有している。`.seq` , `.mrk` にHard Ver.用のファイルが用意してある。

US版は複数の曲で日本版から一部音が変更されているが、f03のみ修正前の`.mrk`が含まれている。

| メモリ上の曲管理用の値 (`0x`) | ファイルのパス | 曲名                        |
| ----------------------------- | -------------- | --------------------------- |
| 00                            | `sta/a00`      | Days Dreamer                |
| 01                            | `sta/a01`      | Phone Dead Room             |
| 02                            | `sta/a02`      | 62-1                        |
| 03                            | `sta/a03`      | Sweet Patch                 |
| 04                            | `sta/a04`      | Earth Will                  |
| 05                            | `sta/a05`      | Clip Click                  |
| 06                            | `sta/a06`      | Visited Alone               |
| 07                            | `sta/a07`      | E.H.M.Y. Hard Ver.          |
| 08                            | `sta/a08`      | Mao's Travel                |
| 09                            | `sta/a09`      | TGM in the Bottle           |
| 0A                            | `sta/a10`      | Clip Click Hard Ver.        |
| 0B                            | `sta/a11`      | Days Dreamer Hard Ver.      |
| 0C                            | `sta/a12`      | E.H.M.Y.                    |
| 0D                            | `sta/a13`      | TGM in the Bottle Hard Ver. |
| 14                            | `stb/b00`      | Rambler                     |
| 15                            | `stb/b01`      | Inside One                  |
| 16                            | `stb/b02`      | Bossa Antique               |
| 17                            | `stb/b03`      | Hypnotherapy                |
| 18                            | `stb/b04`      | Diamond Troll               |
| 19                            | `stb/b05`      | FAKE WORLD MANIAX           |
| 1A                            | `stb/b06`      | 1970 Modern                 |
| 1B                            | `stb/b07`      | Marine snow Dance           |
| 1C                            | `stb/b08`      | Broken Shackles             |
| 1D                            | `stb/b09`      | Spiral Life                 |
| 1E                            | `stb/b10`      | Happy Happy                 |
| 1F                            | `stb/b11`      | Rambler Hard Ver.           |
| 28                            | `stc/c00`      | Dizzy Dog                   |
| 29                            | `stc/c01`      | Hard Head                   |
| 2A                            | `stc/c02`      | Hectic                      |
| 2B                            | `stc/c03`      | Roteen da Moon              |
| 2C                            | `stc/c04`      | Segment4                    |
| 2D                            | `stc/c05`      | Night Life                  |
| 2E                            | `stc/c06`      | Suicide Robot               |
| 2F                            | `stc/c07`      | Sasia                       |
| 30                            | `stc/c08`      | Dynamite Egoist             |
| 31                            | `stc/c09`      | TE-20                       |
| 32                            | `stc/c10`      | Arabesque                   |
| 33                            | `stc/c11`      | Roteen da Moon Hard Ver.    |
| 34                            | `stc/c12`      | Night Life Hard Ver.        |
| 55                            | `ste/e00`      | Xevious                     |
| 56                            | `ste/e01`      | Dig Dug                     |
| 57                            | `ste/e02`      | Mappy                       |
| 58                            | `ste/e03`      | New Rally-X                 |
| 59                            | `ste/e04`      | Libble Rabble               |
| 5A                            | `ste/e05`      | Assault                     |
| 5B                            | `ste/e06`      | Burning Force               |
| 5C                            | `ste/e07`      | Metrocross                  |
| 5D                            | `ste/e08`      | Youkai Douchuki             |
| 5E                            | `ste/e09`      | Motos                       |
| 5F                            | `ste/e10`      | Thunder Ceptor              |
| 60                            | `ste/e11`      | Ordyne                      |
| 61                            | `ste/e12`      | Rolling Thunder             |
| 62                            | `ste/e13`      | PacMania                    |
| 63                            | `ste/e14`      | Berabo Man                  |
| 64                            | `ste/e15`      | MÄRCHEN MAZE                |
| 65                            | `ste/e16`      | Genpei Toumaden             |
| 66                            | `ste/e17`      | Dragon Spirit               |
| 67                            | `ste/e18`      | F/A                         |
| 68                            | `ste/e19`      | The Return of Ishtar        |
| 69                            | `ste/e20`      | Splatter House              |
| 6A                            | `ste/e21`      | TOY POP                     |
| 6B                            | `ste/e22`      | X-Day                       |
| 6C                            | `ste/e23`      | Sky Kid                     |
| 6D                            | `ste/e24`      | Ridge Racer                 |
| 6E                            | `ste/e25`      | Galaxian3                   |
| 6F                            | `ste/e26`      | Dragon Buster               |
| 70                            | `ste/e27`      | Winning Run                 |
| 71                            | `ste/e28`      | Wonder Momo                 |
| 72                            | `ste/e29`      | Gun Bullet                  |
| 73                            | `ste/e30`      | Soul Edge                   |
| 74                            | `ste/e31`      | The Tower of Druaga         |
| 75                            | `ste/e32`      | Cosmo Gang the Video        |
| 76                            | `ste/e33`      | Sky Kid Hard Ver.           |
| 77                            | `ste/e34`      | PacMania Hard Ver.          |
| 78                            | `ste/e35`      | Burning Force Hard Ver.     |
| 79                            | `ste/e36`      | Thunder Ceptor Hard Ver.    |
| 7D                            | -              | ENDING (Arcade mode)        |
| 7E                            | `stf/f01`      | brisa do mar                |
| 7F                            | `stf/f02`      | Spider Control 16th         |
| 80                            | `stf/f03`      | The Absolute                |
| 81                            | `stf/f04`      | Stronger                    |
| 82                            | `stf/f05`      | Crowded Town                |
| 83                            | `stf/f06`      | Prismatics                  |
| 84                            | `stf/f07`      | PURACHINA                   |
| 85                            | `stf/f08`      | Sakura Mankai               |
| 86                            | `stf/f09`      | Ancient Zone                |
| 87                            | `stf/f10`      | L7-A2                       |
| 88                            | `stf/f11`      | Stronger Hard Ver.          |
| 89                            | `stf/f12`      | LOVE MAHJONG                |
| 8A                            | -              | Sudden Death Ending         |

## それぞれのファイルの格納場所

- `zmus/***/***.mus`
- `snd/***/***.vh, .vb`
- `seq/***/***.mrk`

`tr[a-f]`内のファイルはパズル用の曲データと譜面。パズル用の楽曲は（そこまで頑張る気がなかったため、今のところは）ツールで復元できない。

## 出力したWAV結果と実機の演奏結果を比較したいとき

アーケード版のサービスモードからSOUND TESTを選択後、メモリアドレス `0x005CE3C8` の値を `0x01` に変更することで楽曲再生用の隠しメニューを呼び出せる。

もしくは、アーケード版ノーマルモードの楽曲選択画面で、下記の `メモリアドレス` を -> `値` に書き換える。
R5900はリトルエンディアンで動作する点に注意すること。

```text
// アンダースコア付き_mrk（譜面作成前の元データとして用意されたと思われるファイル）, _seq（空のシーケンス）を読み込む
0x001CA7F8 -> 0x1000000A
// マーカーを自動演奏する 
0x001CACA0 -> 0x10000005
```
