# NeoDen YY1 converter

Turns a KiCad, PADS Layout or Altium Designer board into the file the NeoDen
YY1 pick-and-place machine reads off its SD card, with the feeder numbers,
nozzles, recognition modes, placement order and nozzle changes already filled
in — plus an operator sheet, a loading sheet against the reels that stay on
the machine, and a guide that walks a new job through in five steps.

The output format was reverse-engineered byte for byte from a file that came
back off the machine, and every change is checked against it
(`verify_yy1.py`).

**New here? Read the [user guide](docs/USER-GUIDE.md)** — install, what to
export from KiCad, PADS or Altium, the Guide, Set up board, the feeder list,
export — with screenshots. Try it on the demo board: `python examples/make_demo.py`,
then open `examples/demo_yy1_top.csv`.

**Windows:** download `yy1_converter.exe` from the
[latest release](https://github.com/koupypavel/neoden_yy1_converter/releases)
— one file, nothing to install. **Anywhere with Python 3:**

```bash
python yy1_gui.py                                   # the window (tkinter, no extras needed)
python kicad2yy1.py path/to/kicad_project           # or the command line
python pads2yy1.py path/to/pads_project
python altium2yy1.py path/to/board.PcbDoc
pip install -r requirements-optional.txt            # only for .xlsx/.xls BOMs and the exe build
python build_exe.py                                 # -> dist/yy1_converter.exe
```

| File | What it is |
|---|---|
| `yy1_gui.py` | The window: feeders, placement order, nozzle changes |
| `kicad2yy1.py` | Command line, and the KiCad side of the work |
| `pads2yy1.py` | The same for PADS Layout boards — see below |
| `yy1lib.py` | The file format, the job profile, the nozzle planner |
| `verify_yy1.py` | Checks output against the file the machine ran |
| `build_exe.py` | Packages the window as `dist/yy1_converter.exe` |
| the reference file (a placement file that came back off the machine, kept out of the repository because it is a real board) | The reference file, straight off the machine |

Point it at a KiCad project directory; it finds the `.kicad_pcb` and the
Fabrication Toolkit's `production/*positions.csv` + matching `bom.csv` on its
own. The toolkit names them `<archive name>_positions.csv`, or plain
`positions.csv` when no archive name is set; both are read, and with no archive
name the output takes the board's name. When the folder holds several
`.kicad_pcb` files (a working copy and one adjusted for the machine), the one
whose parts sit where the export says, and whose values agree with its BOM, is
used and named in the log. *File > Open KiCad board…* picks the board outright.

The the reference board that ran on the machine was exported with *Apply automatic
component translations* ticked (JLCPCB's rotation table: SOT-23 and SOT-223
+180°, SOIC −90°, TSSOP +270°). Export the same way, and step one of each such
part on a new board before running it.

## What is where

```
neoden_yy1_converter/
  *.py, README.md, TEST-NOTES.md   the tool itself
  reference/    the machine-verified file (kept out of git: a real board)
  feeders/      an example feeder list (the reels that stay on the machine)
  docs/         screenshots of the window
  dist/         the built yy1_converter.exe (not in git; on the release page)
```

Board projects are not in the repository: the tool is pointed at a project
folder wherever it lives (`kicad/` and `pads/` beside the tool are handy and
ignored by git). The YY1 user manual, cited by page throughout, is NeoDen's
own; the built-in Manual tab carries what the operator needs from it.

Converted files are written next to the board they came from (or wherever
`--outdir` / Export to SD card… points), and **the profile always lives beside the
project** — `<project>/<board>_yy1_profile.json` — never beside the output.
The window and the command line both read and write that one file, and a
plain re-run applies it: renames, merges, feeders, nozzles and the fiducial
all come back. Every export also writes a **copy of the profile beside the
CSV** — the machine's file has no column for nozzles, feeder homes or reel
settings, so a card that comes back from the machine opens with the same
state only if the profile travelled with it. (An earlier version wrote the profile beside the output when
`--outdir` differed, which left two copies that drifted apart.)

## The format

The manual is oddly incomplete here: it says a correct file "will be
automatically converted" and warns you get an error "if the file format wrong",
but nowhere in its 37 pages does it list the columns.

the reference file (a placement file that came back off the machine, kept out of the repository because it is a real board) settles it. That file came back off the machine,
so it is the machine's own format rather than a guess, and it is what the
converter writes:

```
NEODEN,YY1,P&P FILE,,,,,,,,,,,
,,,,,,,,,,,,,
PanelizedPCB,UnitLength,0,UnitWidth,0,Rows,1,Columns,1,
,,,,,,,,,,,,,
Fiducial,1-X,0.00,1-Y,0.00,OverallOffsetX,0,OverallOffsetY,0,
,,,,,,,,,,,,,
NozzleChange,OFF,BeforeComponent,1,Head1,Drop,Station2,PickUp,Station1,
NozzleChange,OFF,BeforeComponent,2,Head2,Drop,Station3,PickUp,Station2,
NozzleChange,OFF,BeforeComponent,1,Head1,Drop,Station1,PickUp,Station1,
NozzleChange,OFF,BeforeComponent,1,Head1,Drop,Station1,PickUp,Station1,
,,,,,,,,,,,,,
Designator,Comment,Footprint,Mid X(mm),Mid Y(mm) ,Rotation,Head ,FeederNo,Mount Speed(%),Pick Height(mm),Place Height(mm),Mode,Skip
C1,100nF,0805,28.85,2.25,180.00,0,1,75,0.0,0.0,1,0
```

Details that matter, all taken from that file:

- **`Comment` comes before `Footprint`** — the opposite of the generic
  11-column layout NeoDen USA's support page describes.
- **Rotation is 0…360**, not −180…180. KiCad's `-90` is written `270.00`.
- Coordinates are millimetres to two decimals, measured from the board's
  lower-left corner (lower-**right** for the bottom side, so bottom X mirrors).
- **There is no `Layer` column.** One file per side; the side is in the name.
- Lines are CRLF, no BOM. The trailing spaces in `Mid Y(mm) ` and `Head ` are
  the machine's own and are reproduced exactly.
- No commas inside any text field — the machine splits on commas and nothing
  else.
- The blank rows between header sections carry 13 commas (14 fields).

The old 11-column layout is still available with `--format legacy`. The machine
converts it on load, but everything past position — feeder, head, speed, mode —
then has to be typed into the touchscreen, once per part type.

`verify_yy1.py` regenerates the reference board and compares it against the reference file:

```bash
python verify_yy1.py
```

It checks the preamble and header verbatim, and every placement to 0.01 mm.
(The reference's last decimal truncates where ours rounds — it was made by
re-reading a 3-decimal file — so coordinates are compared by tolerance, not by
string. The difference is 0.005 mm, four times below the machine's own ±0.02 mm
placement spec.)

## The profile — feeders, nozzles and the rest of the columns

Everything you set by hand lives in `<project>/<board>_yy1_profile.json`, keyed
by **part type** rather than by designator. The machine groups parts into feeder
types by Comment + Footprint (manual §3.2.1 note 3), so the profile uses the
same key — which means a schematic change and a re-export from KiCad keep every
feeder assignment you have already made.

Three layers, each overriding the one before:

```
defaults   ->  every part
types      ->  every part of one Comment+Footprint type
parts      ->  one designator
```

plus two rename tables. `packages` and `values` shorten KiCad's names into what
the machine's screen shows — `CP_EIA-7343-40_Kemet-Y` becomes `7343`,
`QFP50P1200X1200X120-64N` becomes `QFP-64`. Rules handle most of it; the tables
catch what no rule could derive, such as a manufacturer part number standing in
for a value. Edit the cell in the window and the rename is stored against the
raw KiCad name, so it survives the next export.

Feeder numbers, from manual §3.4:

| Range | Bank |
|---|---|
| 1–52 | tape reel |
| 53–80 | flexible: vibration, IC tray, strip |
| 81–99 | bulk (loose parts, found by camera) |

Recognition modes, from manual §3.2.1 note 4: `0` Null, `1` Camera, `2` Vacuum,
`3` Camera+Vacuum, `4` Big IC Camera. Mode 4 widens the field of view but needs
shielding from overhead light.

## The window

**Guide…** (top left, *Ctrl+G*, and at start until *Open at start* is
unticked) takes a new job through five steps, driving the tabs rather than
copying them:

1. **Board files** — what to export from KiCad (Fabrication Toolkit) or PADS
   Layout (ASCII with Parts and Lines, plus a BOM or Logic export for values),
   and a check of the folder before anything is read: the board, the export
   and its date against the board's, the BOM, the automatic-translation
   setting, which of several boards the export belongs to.
2. **Check the board** — size, parts per side, reels, what was left out and
   why, reels with no value, the side, and the fiducial (one click when the
   board has marks).
3. **Heads and nozzles** — either what is on the machine now (head 1, head 2,
   the station positions; the last one stays empty for the drop), which the
   plan then uses and nothing else, or the nozzle drawer; then *Set up board*,
   with the report shown in the guide.
4. **Feeders** — the board against the feeder list: use the reels on the
   machine and load the rest, or only the reels on the machine and the rest by
   hand; what to load where, what to swap, what goes by hand.
5. **Check and export** — red and amber reels, the nozzle changes against the
   four the machine holds, the fiducial, what is by hand; then export next to
   the board or to the SD card, and open the operator sheet.

**The top bar** is the job in order: open the board, pick the side, **Set up
board**, then **Export** (next to the board) or **to SD card…** — the two big
buttons. Rotation offset, the file format and *Skipped parts out of the CSV*
live on the Job header tab. The tabs run the same way: Feeders, Board, Feeder
bank, Order & nozzles, Parts, Job header, Manual.

**Undo / Redo** (*Ctrl+Z* / *Ctrl+Y*, buttons beside Guide…) step through the
last sixty changes to the board's profile and the feeder list. **Feeder list
backups**: every save of a list first keeps the old file in
`feeder_list_backups/` beside it (the newest forty per list); *Earlier lists…*
on the Feeder bank tab lists them with date and reel count and brings one back.

`python yy1_gui.py` — or the built `yy1_converter.exe`, which needs nothing
installed. Standard library only, so it packages to about 11 MB.

**Set up board** is the one button. It decides everything for the side on
screen and shows the result before applying it: a nozzle per reel, trimmed to
the four types the machine runs without stopping, where each nozzle starts,
the file grouped by nozzle, the reels numbered in that order, and the change
rows. When five or more nozzle types are needed it moves the least-used one's
parts onto the next smaller nozzle — a smaller tip seals on a bigger body, the
reverse cannot — one step at a time until four remain, and says which parts
moved; whatever still will not fit is set to Skip for a second pass. Nothing is
saved until you press Save profile or Export. `--setup` does the same on the
command line.

**Board** draws the board with every part where it will be placed, coloured by
nozzle (or feeder, or skipped), with a tick on each showing its rotation.
Click a part to see its reel's settings in the side panel and change them
there; *Skip this part only* pins one designator. *Show placement path* draws
the order the machine will follow, which makes the nozzle grouping visible.
A CSV opened on its own has no board size, so the profile beside it carries
one; failing that, the parts' extent is drawn and labelled as an estimate.

**Manual** is the reference, built in: what every column means, the feeder
banks, the nozzle table, the four-nozzle rule and why the drop position must
be empty, placement order, the origin rule, fiducials and panels — each with
the manual's section number. Hovering any column heading or control gives the
one-line version. The Nozzles page carries the manual's own photos: the seven
regular nozzles in tip-size order with their diameters, the six YX specials
with their tip shapes, the three-position nozzle station, and the machine's
Auto Nozzle Change screen. They are embedded as data in `yy1images.py`, so
the exe carries them without an image library.

**Open PADS project…** does for a PADS directory what Open KiCad project does
for KiCad: finds the `.asc` export, the placement report and the BOM, converts
in place and shows the report. A profile saved by an earlier run is picked up,
so the feeders and nozzles already decided survive a fresh export.

**Feeders** is the main table: one row per reel you will load, with every
machine column editable in place. Double-click a cell, type, press Enter;
Enter steps down, Tab steps right. Select several rows and set a field on all
of them at once, incrementing if it is the feeder number. *Auto-number feeders*
walks the types in placement order and assigns tape slots 1…n, leaving anything
you already pinned where it is — so adding a part to a board does not renumber
reels already sitting on the machine.

Rows are coloured by state. Green is ready: a slot of its own, a nozzle, and
once a plan exists that nozzle loaded on a head or in the station. Amber is
still on the default slot. Red is something the machine would trip over: two
reels on one slot, no nozzle, or a nozzle nothing is loaded for. Grey is
skipped. The status bar counts them, so after *Set up board* the goal is
simply "no red".

**Parts** is the same data one placement per row, in file order — which is the
order the machine builds the board in, numbered in the `#` column. Editing here
pins one designator; *Move up* / *Move down* rearranges the sequence by hand.

**Order & nozzles** covers both of those. See below.

**Job header** is panelisation, the fiducial, and the four nozzle-change rows.
The fiducial is a position in the same mm-from-the-corner frame as the parts:
the tab lists the fiducial footprints and drilled holes the board carries, or
click any part on the Board tab and press *Set as fiducial 1*. The Board tab
draws the candidates as rings and the one in the header as a crosshair. With
it set, Step on the machine drives there first, finds the round mark by camera
and corrects the whole board's position (manual §3.2.4 note 2) — that is the
check that it works. *Offset X/Y* is a constant added to every placement; the
manual prefers fine-tuning in Preview to shifting the file, so leave it at 0.

**Feeder list** — the reels that stay on the machine. *Feeder list…* on the
Feeders tab loads a spreadsheet of slot, tape width and value (a package
column is optional; `docs/feeders.ods` is the one in use), remembered across
boards like the nozzle drawer. Set up board then matches every reel on the
board against it by value — spelling-tolerant, so `4K7` is `4.7K`, `100nF` is
`100n`, `10uF16V` is `10uF/16V`, and `TJA1050` is a probable match for
`TJA1050T` — checks the tape width against the board's package, and keeps the
list's slot for every reel it finds. Reels not on the list go into the list's
free positions of the right width; whatever has no free slot is set to Skip
and named for placing by hand. The report says which is which, and the
operator sheet marks each reel *on machine* or **LOAD**.

**Feeder bank** is the list as a picture: the 52 tape slots in two rows and
the flexible 53–80 below. Each tape slot is a box with the slot number and
tape width on top and two bands under it: the upper band is the list (blue =
a reel is on the machine, grey = free), the lower band is this board (green =
the board's reel matches what the list says is there, orange **LOAD** = a
reel to put into an empty slot, purple ≠ = the board has a different reel in
that slot than the list, red = two of the board's reels on one slot). *Show*
switches between the two bands together, the list alone and the board alone.
Whatever has no slot at all is spelled out in red under the picture, and the
two tables underneath say the same thing as text: the machine's slots with
what this board makes of each, and the board's reels with where each goes.

**Only reels on the list** is for a run that places just what is already on
the machine: reels the list has keep their slot, every other reel is set to
Skip and listed under the picture as "not on the list, place by hand". It
offers to tick **Skipped parts out of the CSV** (Job header tab), which
keeps skipped parts out of the file altogether instead of writing them with
Skip = 1; the operator sheet still lists them, and the nozzle change rows are
renumbered for the file as it will be written. The list's *Footprint* column,
when filled, has to agree with the board's package for a reel to count as the
same one, so 10k on 0805 and 10k on 0603 stay apart.

A match also writes the **completed list**, `<board>_yy1_feeders_<side>.ods`:
the feeder list with this board's reels written into their slots, same
columns as `feeders.ods` (Feeder, Feeder width, Value, Footprint,
highest slot first). Once the reels are in, open it as the feeder list for
the next board, or copy it over the old one. *Save list as…* writes the
current list the same way, as .ods or .csv.

Both matches write a **loading sheet** beside the project,
`<board>_yy1_feeders_<side>.html` and `.txt` (*Open loading sheet* shows it):
the reels to load with slot, tape width and what to take out of that slot
first, the reels already on the machine to check, and what goes by hand, each
with its designators. The tick boxes are remembered by the browser like the
operator sheet's.

**Selecting on the Feeder bank tab**: click a slot, or a row in either table.
The slot gets a blue frame with a marker above it, the reel's row stays
highlighted in the table, and a blue bar under the picture names the reel
(with its designators and slot) and the slot. Pick a reel in the right-hand
table, then click a free slot, and the bar's button offers *Put <reel> into
slot N*. A selected reel with no slot yet shows the free slots of its tape
width with a dashed frame. The divider above the tables can be dragged, and
the flexible bank shrinks to one line when nothing is on it.

**Most used reels…** (Feeder bank tab, or the Tools menu; `yy1survey.py` on
the command line) is how the static set gets chosen in the first place. Point
it at a folder of projects — KiCad with a Fabrication Toolkit export, PADS with
an ASCII export, Altium `.PcbDoc`, any depth, backup and gerber folders skipped
— and it reads every board the way the converters do and counts, for every
reel as the machine groups them, on how many boards it is needed and how many
placements it has. Sorted by boards first: a reel on six boards earns a fixed
slot even if each board uses it twice. Reels already on the feeder list show
green with their slot. Tick the ones to keep (or *Tick the top N not on the
list*) and *Add ticked to feeder list* puts them into the list's free tape
slots by tape width, saved like any other list edit (with a backup of the old
one). *Save survey as CSV…* keeps the table.

```bash
./yy1survey.py path/to/all/projects --list feeders/feeders.ods
./yy1survey.py path/to/altium/projects --list feeders/feeders.ods --top 20 --write static.ods
```

**Match board to list** does the feeder part of Set up board on its own,
without touching nozzles or order: every reel the list already has keeps the
machine's slot, the rest go into free slots of the right tape width (so they
show as LOAD), and whatever has no free slot is set to Skip and listed as
"place by hand". A reel skipped only for want of a slot is picked up again
the next time you match after freeing one. Click a slot to select it (and
its reel on the Feeders tab); right-click to put the selected reel there,
take a reel out (skip it), tell the list that a slot now holds the board's
reel, or set / clear the list's value for that slot. *Update list from this
board* writes every slot the board uses into the list. Edits never touch the
original spreadsheet — they go to a CSV of the tool's own
(`~/yy1_feeder_list.csv`), which becomes the active list.

**Nozzles you have** — tick *Plan only with these* on the Order & nozzles tab
and enter the drawer: two CN065 and one CN220, say. Set up board then plans
inside that: a part whose ideal nozzle you lack goes to the next smaller one
you own (up to three sizes down — a smaller tip still picks a bigger body),
a nozzle you have two of can sit on both heads, the number of types loaded at
once is capped by what you own, and anything nothing in the drawer can pick is
skipped and named. Reels needing a nozzle you lack show red. The drawer is a
fact about the machine, not the board, so it is remembered in
`~/.yy1_converter.json` across boards.

**Nozzle homes** are set straight from a list on the Order & nozzles tab —
one row per nozzle in use: Head 1, Head 2, **Head 1+2**, a station position,
or not loaded. Moving one nozzle onto a head sends whatever was there to a
free station position (a nozzle that had both heads keeps the other one), and
a station position holds one nozzle, so an occupant moves on. *Same nozzle on
both heads* does the common case in one press: that nozzle on both heads, the
others parked while positions last, with one position always kept empty for
the drop — anything beyond that is left unloaded and said so. The plan is
recomputed after every change.

**Right-click a reel** on the Feeders tab for: nozzle for the selected reels
(the full list), merge the selected reels into one, group the values of that
footprint, skip or place again.

**Group values…** (also on the right-click menu) is the by-hand way to put
several values of one footprint on one reel: pick the footprint, tick the
values that are really the same part — `10uF` and `10uF/16V` — name the reel,
merge. The rename is stored against each raw value in the profile, so it
survives the next export.

**Across footprints** works too — `10k 0805` and `10k 0603` placed from one
0805 reel. Select both (or pick *(any footprint)* in Group values…), merge,
and say which part is really on the reel. Only those reels change: the 0603
10k is exported as `0805 10k` while every other 0603 stays 0603. That is
stored as a per-reel alias in the profile and survives re-export. Whether an
0805 body sits on 0603 pads is your call — it usually does; the reverse
usually does not.
*Find look-alikes…* lists reels of one footprint whose values match once units
and voltage ratings are ignored (`4k7`/`4K7`/`4.7k`, `100n`/`100nF`,
`10uF/10V`/`10uF/16V`) and lets you tick which to merge — some of those really
are two reels, so nothing merges without your say.

**Every export also writes an operator sheet** beside the CSV —
`<board>_yy1_top_setup.html` to print, and a `.txt` twin that reads anywhere.
It lists which nozzle sits on each head and in each station position (and
which position to keep empty), the nozzle changes exactly as the machine's
Auto Nozzle Change screen wants them entered, and every reel by slot with
package, value, quantity, nozzle, mode and all its designators — with a tick
box per row for loading. The boxes are real: on screen a tick turns the row
green and is remembered by the browser for that file, so the sheet survives a
reload or a print halfway through loading, and *clear all ticks* at the top
resets it for the next board. Reels past tape slot 52, reels still on the default
slot, and parts skipped for a second pass are called out. Component numbers on
the sheet count down the file, so they match what the machine shows.

*File > Import settings from a machine CSV* reads a file back off the SD card
and pulls its feeder numbers, modes and heights into the profile — so the
settings an operator made on the touchscreen survive the next KiCad export.

## Placement order and nozzle changes

The YY1 works straight down the file, so row order is the order the board gets
built in. Four choices:

| Order | What it gives you |
|---|---|
| `ref` | Designator order — what the machine-verified file used |
| `feeder` | All the parts off one reel, then the next |
| `nozzle` | One unbroken run per nozzle |
| `custom` | Whatever you arrange with Move up / Move down |

Nozzle order matters because of how changes work. The head carries two nozzles
and the station holds more, so **four nozzle types run without stopping**
(manual §4.5.2 note 1). Past that, a nozzle has to be swapped mid-board, and a
`NozzleChange` row happens *before one component number* — so it only covers a
whole group if that nozzle has one contiguous run.

*Plan it for me* does the whole thing: it puts the two busiest nozzles on the
heads, parks the rest in the station, groups the file by nozzle, and writes the
change rows. It keeps one station position empty on purpose, because the manual's
caution in §4.5.2 note 3 is that the position you drop the outgoing nozzle into
must be vacant or the head collides with what is already there. When it must
evict a fitted nozzle it drops whichever one is needed furthest in the future.

For the reference board that comes out as:

```
CN100    90 parts   Head 1
CN140    20 parts   Head 2
CN220     7 parts   Station 1
CN400     1 part    Station 2

before #111 (C14) park head 1 in station 3, fit CN220 from station 1
before #118 (IC1) park head 1 in station 1, fit CN400 from station 2
```

IC1, the 64-pin QFP, is the one part needing a CN400 — so it is placed last,
after a single change. Double-click a nozzle's *Starts on* cell to overrule the
assignment; the plan recomputes. On the command line, `--plan-nozzles`.

If more than four nozzle types are needed the wizard says so rather than
producing a plan that cannot run: put those parts on a nozzle already loaded,
mark them Skip and do a second pass, or free a station position.

## Altium Designer boards

`altium2yy1.py` reads the `.PcbDoc` directly — no export needed. The file is an
OLE compound document with one stream per record type, and four of them carry
everything the machine needs: `Components6` (position, rotation, layer,
footprint pattern, designator, all in mil), `Texts6` (each text knows its
component and whether it is the designator or the **Comment**, which is the
value), `Pads6` (each pad knows its component and hole size — that is how
through-hole parts are told apart, and the pads' extent gives the body size
for the nozzle suggestion) and `Board6` (the outline vertices). *Open
Altium…* takes the project folder (newest `.PcbDoc`) or, from the File menu,
the board file itself.

Values are the parts' Comments. A BOM saved as `.xlsx`/`.ods`/`.csv` beside
the board with *Comment* and *Designator* columns fills in what the board
lacks and overrides what it has. Altium's own pick-and-place CSV (*File →
Assembly Outputs*) is read when present, with the outline and pads still
taken from the board.

```bash
./altium2yy1.py path/to/project            # the newest .PcbDoc in the folder
./altium2yy1.py path/to/project --board a board.PcbDoc
```

Footprint names follow IPC-7351 in Altium libraries and are shortened the
way the machine names reels: `RESC2012X65N` → 0805, `CAPC1608X90N` → 0603,
`SOT95P280X145-5N` → SOT23-5, `SOT65P210X110-3N` → SOT323, `SOIC127P600X175-8N`
→ SOIC-8, `QFP80P900X900X160-32N` → QFP-32; `C0805` and `RES 0805_2012` → 0805.
Bottom-side rotation is taken as-is (`--bottom-rot` negates or mirrors it)
until a bottom-side board has been stepped on the machine.

Read on four boards so far (60 components with 48 placed and 12 through-hole
dropped; 34 / 24; 63 / 54; 4 / 2). None of them has been run on the machine
yet.

## PADS Layout boards

`pads2yy1.py` is the same converter with a different front end. PADS keeps the
board in a proprietary binary `.pcb`, so nothing can be read straight off disk
the way KiCad's files can — the positions have to come out of PADS. Two exports
carry them, and it reads either, or both:

```bash
./pads2yy1.py pads/a PADS board            # finds the exports and the BOM itself
./pads2yy1.py pads/a PADS board --bom-only  # what the BOM alone knows
```

**PADS ASCII** (*File → Export* from PADS **Layout**, with Parts and Lines
ticked) is the complete one: part positions, the part-type → decal mapping,
the pad stacks — so through-hole parts get dropped like they do from KiCad —
and the board outline, which is what coordinates are rebased onto.

**The Component Placement report** (`.xlsx`) has refdes, decal, value, layer,
orientation, X, Y and an SMD flag, but no board outline. Reading it needs
`openpyxl`; the `.asc` path needs nothing.

A PADS **Logic** export (`File → Export → .txt` in PADS Logic) is the
schematic. Its X/Y are positions on the drawing sheet, so it is refused as a
source of positions — placing from it would put every part where its symbol
sits on the paper. But it is the one export that carries every part's
**value** (`"VALUE" 100n`, plus part number and description), which the Layout
export does not: Layout knows a capacitor only as part type `CC`. So drop the
Logic `.txt` into the project folder and it is read for values alone, filling
in whatever the BOM lacks — or standing in for the BOM entirely. Without either,
every 0805 on the board has an empty value and the machine puts them all on one
reel, which is what the warning at the end of the run is about.

The Layout export does carry values after all — not in the `*PART*` records
but in the `ATTRIBUTE VALUES` block of a `*MISC*` section, one entry per part:

```
PART R57
{
"PART DESC" metalizovaný odpor miniaturní
Value 330R
}
```

PADS quotes only attribute names that contain spaces, so `Value 330R` is
written bare; a parser that looks for quoted lines misses every one of them,
which is how a first attempt at `a 110-part PADS board` put 68 0805 parts on one reel. Reel
names come from, in order: the BOM; the Logic export's `VALUE`, then its
`PART NUMBER` (ICs usually have that and no value); the placement report; the
Layout `ATTRIBUTE VALUES` — per-part `Value` or `Part Number`, a `SKLAD CISLO`
stock number if it has digits in it (the library's placeholder *"dle hodnoty"*
does not), or the part type's `Part Number`; and finally the Layout part type
when it is a real name (`TJA1050T`) rather than a class (`CC`, `RR`, `C+`).
So a bare `.asc` is usually enough on its own; a BOM or Logic export only
sharpens it.

Two things about the ASCII format are worth knowing, because both were wrong on
the first attempt and both are silent failures:

- **Alternate decals.** A part type lists its decals as `LTST:LED3MM`, and each
  part's own `ALT` field indexes into that list. On `a PADS board`, 159 of 250 parts
  are on an alternate — so taking the first decal names the wrong package for
  most of the board, and gets their through-hole detection wrong with it.
- **Names can start with a digit** — `1N4148`, `3XPIN`. Records are recognised
  by their shape, not by their first character; requiring a letter there
  silently dropped every part type named after a part number, which is most of
  the through-hole ones.

With both handled, the two exports agree exactly: on `a PADS board` all 250 parts
match, decals 250/250, through-hole 28/28, rotation and side 250/250, and the
positions differ by 0.0008 mm. When both files are present that cross-check is
run and printed, because they come from different parts of PADS and agreement
is real evidence rather than a hope.

### Units

The ASCII header names the units the file is stored in. `BASIC` is PADS's
internal resolution and its multiplier was not guessed — it was pinned against
`a PADS board`, where three independent things agree: the fit between the two
exports is exactly ×1500; the report's own numbers are micrometres, the only
scale on which the parts fit inside the board at all; and the resulting board
outline is exactly 120.000 × 74.800 mm, which the fabrication Gerber and NC
drill confirm as that board plus 12.5 mm panel rails on every side.

That is one PADS version, so the board size is always printed, and a result
outside anything a PCB could be is refused rather than written. `--board-mm W H`
derives the scale from a size you measure instead.

### The BOM

`BillOfMaterials.rep.txt` is already in the projects and needs no export, and
the same BOM pasted into a spreadsheet is read too — `.ods` with the standard
library alone, `.xlsx`/`.xls` if `openpyxl`/`xlrd` are installed. It supplies
Comment and Footprint, and PADS's own Qty column is a checksum on the reference
expansion (`C4-8 C10 C17-21` → individual designators).

That check keeps earning its keep. It found that the Reference column **wraps
onto continuation rows with a blank Qty**, without which most of the decoupling
caps on five of seven reports went missing. On `a PADS board` it then found a
genuine error in the BOM rather than in the parser: the 1k line says Qty 10 and
lists nine designators, and R19, R32 and R33 are on the board but in no BOM
line at all.

A part marked do-not-fit is dropped. PADS has no column for it, so it gets
written into the value by hand — `BC847,NEOSAZUJ` — and the marker is matched
in Czech and English both.

A BOM pasted into a spreadsheet also grows costing lines at the bottom
(`test`, `led diody`, `konektory`, `fráza`) that have a quantity and a price
but no part. A real BOM line always names its part, and that is what separates
them.

PADS part names are `<type>,<value>` — `CC,100n 0603` becomes Comment `100n`.
Decals shorten the same way KiCad footprints do: `SMD0805A` → `0805`,
`SOT23_LMH6642` → `SOT23`. A custom decal such as `MURATA_NFM21CC223R1H3D`
keeps its whole name, because its meaning is in the tail.

Since PADS stores no body outline, the nozzle suggestion comes from the package
name instead, via the suction-surface table in manual p.29. Packages the manual
does not list get no suggestion rather than a guess — a wrong nozzle is worse
than none, because the change plan is built on them.

## The origin rule

The manual (p.7) requires the coordinate origin on the **board's lower-left
corner** — lower-**right** for the bottom side, which is why bottom X is
mirrored. In KiCad that is *Place → Drill/Place File Origin*, clicked on the
lower-left corner of the board outline, before exporting.

The script checks this and **stops to ask** if the origin is not on the corner.
It prints the exact coordinates to click. You can continue anyway with
`--origin-ok`, because the conversion compensates for any origin
mathematically — it reads `aux_axis_origin` from the `.kicad_pcb` and undoes it.
That override is only safe if the position file was exported from the *current*
board; if the board has been moved since, re-export first.

Getting this wrong is the single most likely way to produce a file that looks
fine and places everything in the wrong place, so the prompt is deliberate.

## What the machine can physically place

| Limit | Value | Source |
|---|---|---|
| Smallest component | 0201 (0.6 × 0.3 mm) | spec; manual Table 1-2 (CN030) |
| Largest component | **18 × 18 mm** | YY1 specification |
| Tallest component | 12 mm | YY1 specification |
| Max PCB | 249 × 350 mm (both feeder banks)<br>315 × 350 mm (left bank only) | YY1 specification |
| Rotation | ±180° | manual p.8 |
| Mounting | SMD only — no through-hole | manual §4.5 |

Nozzles run CN030 (0201) through CN750 (ICs over 12 mm), plus the YX special
nozzles for LEDs, BGAs and FPC/FFC sockets — manual Table 1-2, p.29.

The script enforces this envelope automatically. It measures every component's
real body from the footprint's **F.Fab** outline (falling back to courtyard,
then pads) and drops anything the machine cannot pick:

- **larger than 18 mm** on either axis,
- **smaller than an 0201**,
- **through-hole**, detected from the PCB's pad types,
- **not on the board at all** — refs present in the position file but absent
  from the current `.kicad_pcb`, which means the export is stale,
- **footprints with no pads** — board outlines, logos and mounting holes reach
  the position file when tagged `attr smd`, but there is nothing to place.

Each exclusion is printed with its measured size and the reason. Override with
`--max-size`, `--min-size`, `--no-size-check` or `--include-tht`.

The suggested nozzle per part comes from the same measurement, and is what the
change planner works from. It is a starting point, not gospel — edit the Nozzle
cell where you disagree.

**Height is the one limit that cannot be checked.** KiCad stores no body
height, so the 12 mm limit is on you — watch electrolytics, crystals, relays
and tall connectors.

## What the converter does for you

- Reads the board outline from `Edge.Cuts` and rebases coordinates onto the
  correct corner per side.
- Detects and undoes `aux_axis_origin`, so both origin conventions work.
- Drops everything outside the machine's envelope (see above).
- Joins the BOM for `Footprint` and `Comment`, shortens both to package names,
  and strips commas.
- Normalises rotation into 0…360 and applies any offset you have set.
- Mirrors X for the bottom side and emits a separate `_bottom.csv`.
- Fills in feeder, head, speed, heights, mode and skip from the profile.
- Warns if any part lands outside the board outline — this is the check that
  catches a stale export paired with a newer `.kicad_pcb`.

## Options

| Flag | Purpose |
|---|---|
| `--outdir DIR` | Where to write (default `<project>/production/`) |
| `--variant NAME` | Pick an export when `production/` holds several revisions |
| `--exclude R1,R2` | Leave specific designators out |
| `--only R1,R2` | Keep **only** these — for a single-part trial run |
| `--origin-ok` | Skip the origin confirmation prompt |
| `--include-tht` | Keep through-hole parts instead of dropping them |
| `--max-size MM` | Largest placeable body (default 18) |
| `--min-size MM` | Smallest placeable body (default 0.55, an 0201) |
| `--no-size-check` | Keep parts regardless of body size |
| `--bottom-rot` | `as-is` (default), `negate`, `mirror180` |
| `--pcb/--positions/--bom/--prefix` | Override auto-discovery |
| `--format yy1\|legacy` | Native 13-column (default) or generic 11-column |
| `--profile JSON` | Where the settings live |
| `--rot-offset DEG` | Add to every rotation |
| `--assign-feeders` | Number each part type into a tape slot |
| `--renumber` | With `--assign-feeders`, renumber pinned types too |
| `--order` | `ref`, `feeder`, `nozzle` or `custom` |
| `--plan-nozzles` | Assign nozzles and write the change rows |
| `--raw-names` | Keep full KiCad names instead of shortening them |
| `--save-profile` | Write the profile back even with no other change |

When a project has several exports, the newest is used and the others are
listed. The BOM is always paired to the chosen positions file, so a
multi-revision `production/` cannot mix parts from different exports.

## A note on board outlines

`the reference board` draws its Edge.Cuts inside a footprint (`00_custom_boards:CAR2COM_board`)
rather than as top-level graphics, and is a newer KiCad file (v20260206) with
CRLF endings. The parser matches balanced parentheses rather than indentation,
handles both KiCad 8 and 9-era files, and transforms footprint-local outline
geometry by that footprint's position and rotation. If a board's outline is
ever not found, that is the code to look at.

## Before you run a board

**Top-side rotation.** The reference file went to the machine at rotation
offset 0 and its rotations match what the converter produces from KiCad
unchanged. If a board ever comes out turned, `--rot-offset` (or the box at the
top of the window) is the one constant to set, and it applies to every part.

**Bottom-side rotation is still unverified.** Mirrored X is geometry and is
correct, but neither the manual nor NeoDen's support docs state whether the YY1
expects bottom rotation as viewed from the top or from the bottom. The default
is `--bottom-rot=as-is` and the affected parts are named; if they come out
rotated, try `negate` or `mirror180`.

**Step a few parts first.** Which is what the manual recommends anyway (p.7
§7): step a couple of polarised parts, or verify the first board on double-sided
tape, keeping the tape off any fiducials.

**Placeholder values merge feeders.** The machine groups by Comment +
Footprint, so two different parts sharing a value land on one reel and the
machine cannot tell. `the reference board` C27–C34 are all valued `C`, and D11/D19–D22 are
all `D_Zener`. Fix those in KiCad and re-export, or give them distinct values in
the Feeders tab.

**Tall parts.** The 12 mm height limit is the one thing the converter cannot
check, because KiCad stores no body height. Review anything tall by eye.

**Odd shapes.** The size filter is a bounding box; it cannot tell whether a
part has a flat surface the nozzle can seal against. Domed, recessed or
irregular tops may still fail to pick even when they fit the envelope.
