# General MIDI instruments and drum names

Names below are exactly what `midiInstrument = "..."` accepts. They are read
from LilyPond's own table, so spelling and spacing are authoritative -- an
unrecognised name silently falls back to piano, which is a common and confusing
bug. Numbers are the GM program numbers (1-128) for reference only.

## Melodic instruments

**Piano**  
`acoustic grand` (1), `bright acoustic` (2), `electric grand` (3), `honky-tonk` (4), `electric piano 1` (5), `electric piano 2` (6), `harpsichord` (7), `clav` (8)

**Chromatic percussion**  
`celesta` (9), `glockenspiel` (10), `music box` (11), `vibraphone` (12), `marimba` (13), `xylophone` (14), `tubular bells` (15), `dulcimer` (16)

**Organ**  
`drawbar organ` (17), `percussive organ` (18), `rock organ` (19), `church organ` (20), `reed organ` (21), `accordion` (22), `harmonica` (23), `concertina` (24)

**Guitar**  
`acoustic guitar (nylon)` (25), `acoustic guitar (steel)` (26), `electric guitar (jazz)` (27), `electric guitar (clean)` (28), `electric guitar (muted)` (29), `overdriven guitar` (30), `distorted guitar` (31), `guitar harmonics` (32)

**Bass**  
`acoustic bass` (33), `electric bass (finger)` (34), `electric bass (pick)` (35), `fretless bass` (36), `slap bass 1` (37), `slap bass 2` (38), `synth bass 1` (39), `synth bass 2` (40)

**Strings**  
`violin` (41), `viola` (42), `cello` (43), `contrabass` (44), `tremolo strings` (45), `pizzicato strings` (46), `orchestral harp` (47), `timpani` (48)

**Ensemble**  
`string ensemble 1` (49), `string ensemble 2` (50), `synthstrings 1` (51), `synthstrings 2` (52), `choir aahs` (53), `voice oohs` (54), `synth voice` (55), `orchestra hit` (56)

**Brass**  
`trumpet` (57), `trombone` (58), `tuba` (59), `muted trumpet` (60), `french horn` (61), `brass section` (62), `synthbrass 1` (63), `synthbrass 2` (64)

**Reed**  
`soprano sax` (65), `alto sax` (66), `tenor sax` (67), `baritone sax` (68), `oboe` (69), `english horn` (70), `bassoon` (71), `clarinet` (72)

**Pipe**  
`piccolo` (73), `flute` (74), `recorder` (75), `pan flute` (76), `blown bottle` (77), `shakuhachi` (78), `whistle` (79), `ocarina` (80)

**Synth lead**  
`lead 1 (square)` (81), `lead 2 (sawtooth)` (82), `lead 3 (calliope)` (83), `lead 4 (chiff)` (84), `lead 5 (charang)` (85), `lead 6 (voice)` (86), `lead 7 (fifths)` (87), `lead 8 (bass+lead)` (88)

**Synth pad**  
`pad 1 (new age)` (89), `pad 2 (warm)` (90), `pad 3 (polysynth)` (91), `pad 4 (choir)` (92), `pad 5 (bowed)` (93), `pad 6 (metallic)` (94), `pad 7 (halo)` (95), `pad 8 (sweep)` (96)

**Synth effects**  
`fx 1 (rain)` (97), `fx 2 (soundtrack)` (98), `fx 3 (crystal)` (99), `fx 4 (atmosphere)` (100), `fx 5 (brightness)` (101), `fx 6 (goblins)` (102), `fx 7 (echoes)` (103), `fx 8 (sci-fi)` (104)

**Ethnic**  
`sitar` (105), `banjo` (106), `shamisen` (107), `koto` (108), `kalimba` (109), `bagpipe` (110), `fiddle` (111), `shanai` (112)

**Percussive**  
`tinkle bell` (113), `agogo` (114), `steel drums` (115), `woodblock` (116), `taiko drum` (117), `melodic tom` (118), `synth drum` (119), `reverse cymbal` (120)

**Sound effects**  
`guitar fret noise` (121), `breath noise` (122), `seashore` (123), `bird tweet` (124), `telephone ring` (125), `helicopter` (126), `applause` (127), `gunshot` (128)
## Drum names (`\drummode`)

Long name with its short alias in brackets. Used inside `\drummode` on a
`DrumStaff`; LilyPond routes the staff to MIDI channel 10 automatically, so do
not set `midiInstrument` on a drum staff.

`bassdrum` (`bd`), `hisidestick` (`ssh`), `sidestick` (`ss`), `losidestick` (`ssl`), `acousticsnare` (`sna`), `snare` (`sn`), `handclap` (`hc`), `electricsnare` (`sne`), `lowfloortom` (`tomfl`), `closedhihat` (`hhc`), `hihat` (`hh`), `highfloortom` (`tomfh`), `pedalhihat` (`hhp`), `lowtom` (`toml`), `openhihat` (`hho`), `halfopenhihat` (`hhho`), `lowmidtom` (`tomml`), `himidtom` (`tommh`), `crashcymbala` (`cymca`), `crashcymbal` (`cymc`), `hightom` (`tomh`), `ridecymbala` (`cymra`), `ridecymbal` (`cymr`), `chinesecymbal` (`cymch`), `ridebell` (`rb`), `tambourine` (`tamb`), `splashcymbal` (`cyms`), `cowbell` (`cb`), `crashcymbalb` (`cymcb`), `vibraslap` (`vibs`), `ridecymbalb` (`cymrb`), `mutehibongo` (`bohm`), `hibongo` (`boh`), `openhibongo` (`boho`), `mutelobongo` (`bolm`), `lobongo` (`bol`), `openlobongo` (`bolo`), `mutehiconga` (`cghm`), `muteloconga` (`cglm`), `openhiconga` (`cgho`), `hiconga` (`cgh`), `openloconga` (`cglo`), `loconga` (`cgl`), `hitimbale` (`timh`), `lotimbale` (`timl`), `hiagogo` (`agh`), `loagogo` (`agl`), `cabasa` (`cab`), `maracas` (`mar`), `shortwhistle` (`whs`), `longwhistle` (`whl`), `shortguiro` (`guis`), `longguiro` (`guil`), `guiro` (`gui`), `claves` (`cl`), `hiwoodblock` (`wbh`), `lowoodblock` (`wbl`), `mutecuica` (`cuim`), `opencuica` (`cuio`), `mutetriangle` (`trim`), `triangle` (`tri`), `opentriangle` (`trio`), `acousticbassdrum` (`bda`)

### Handy subset for atmospheric writing

| name | sound |
|---|---|
| `tt` | tam-tam / gong |
| `bd` | bass drum (stands in for taiko) |
| `wbh`, `wbl` | high / low wood block |
| `cymch` | chinese cymbal |
| `cymc` | crash cymbal |
| `tamb` | tambourine |
| `tri`, `trim` | triangle, muted triangle |
| `cb` | cowbell |
| `mar`, `cab` | maracas, cabasa |
| `hh`, `hhc`, `hho` | hi-hat: normal, closed, open |
| `sn`, `sna` | snare, acoustic snare |
