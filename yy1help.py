#!/usr/bin/env python3
"""The manual, built in: what every column, option and colour means.

Everything here cites the NeoDen YY1 user manual by section, so a claim can be
checked against the PDF (NeoDen's YY1 user manual). Two shapes of text:

    SECTIONS   the reference tab -- (title, [(kind, text), ...]) where kind is
               'h2' for a sub-heading, 'p' for a paragraph, 'li' for a bullet,
               'code' for a fixed-width block, and 'note' for a callout.

    TIPS       one-liners keyed by column or control name, shown as tooltips
               and in the board panel when a part is selected.
"""

TIPS = {
    # --- columns the machine reads
    'feeder': 'FeederNo -- which feeder slot this reel is in. Tape 1-52, '
              'flexible/tray 53-80, bulk 81-99 (manual 3.4).',
    'head': 'Head -- 0 lets the machine choose which of its two heads places '
            'this; 1 or 2 pins it.',
    'speed': 'Mount Speed % -- a percentage of the machine\'s global speed, '
             'which defaults to 70%. Lower it for fragile or tall parts '
             '(manual 3.5.1).',
    'pick_h': 'Pick Height mm -- how far below the nozzle height datum the '
              'head drops to pick. 0 uses the datum set on the machine.',
    'place_h': 'Place Height mm -- how far the head drops to place. 0 uses the '
               'machine\'s datum.',
    'mode': 'Mode -- how the part is checked after pick-up: 0 none, 1 camera, '
            '2 vacuum, 3 camera+vacuum, 4 big-IC camera (wider view, shield '
            'from overhead light). Manual 3.2.1 note 4.',
    'skip': 'Skip -- 1 leaves the part out of this run without deleting it. '
            'Use it for a second pass, or for parts you place by hand.',
    'rot_offset': 'Rot + -- degrees added to this type\'s rotation. Set it '
                  'when one reel\'s parts come out turned but the rest of the '
                  'board is right.',
    'nozzle': 'Nozzle -- which tip picks this part. Suggested from the body '
              'size per manual Table 1-2; edit it if you know better.',
    'package': 'Footprint -- what the machine shows for the package. With '
               'Value, it decides which parts share a reel.',
    'comment': 'Value -- what the machine shows for the part. With Footprint, '
               'it decides which parts share a reel.',
    'qty': 'How many placements share this reel.',
    'refs': 'The designators on this reel.',
    'x': 'X in mm from the board\'s lower-left corner (lower-right for the '
         'bottom side).',
    'y': 'Y in mm from the board\'s lower-left corner.',
    'rot': 'Rotation 0-360 as the machine reads it, offsets already applied.',
    'seq': 'Placement order -- the machine works straight down the file.',
    'ref': 'Designator.',

    # --- controls
    'setup': 'Does the whole setup in one go: picks nozzles, trims them to '
             'the four the machine runs without stopping, groups the file, '
             'numbers the feeders and writes the nozzle changes. Shows the '
             'plan before applying it.',
    'board_rotation': 'The whole board turned on the machine, counter-clockwise '
                      'seen from above -- for a panel that only fits the other '
                      'way. Positions, part rotations, board size, fiducial and '
                      'panel all turn with it (Job header tab).',
    'rotation': 'Added to every part. Set it if a test board comes out turned '
                'as a whole -- that constant then applies to every board.',
    'side': 'One file per side. Bottom X is mirrored, measured from the '
            'lower-right corner (manual p.7).',
    'auto_number': 'Numbers each reel into tape slots 1..n in placement '
                   'order. Slots you already set stay put.',
    'merge': 'Make the selected reels one, under one name. For values that '
             'are the same part written two ways -- 10uF and 10uF/16V. Same '
             'footprint only.',
    'lookalikes': 'Finds reels with the same footprint whose values match once '
                  'units and voltage ratings are ignored, and offers to merge '
                  'them.',
    'fiducial': 'Where the fiducial mark sits, in the same mm frame as the '
                'parts. The machine finds it first and corrects the board\'s '
                'position from it.',
    'offset': 'A constant added to every placement, in mm. Leave at 0 -- '
              'fine-tune in the machine\'s Preview instead (manual 3.2.4).',
    'renumber': 'Numbers every reel afresh, including ones you set by hand.',
    'order': 'The machine places straight down the file. Grouping by nozzle '
             'is what lets one change cover a whole run.',
    'station': 'How many positions the nozzle station has. One is kept empty '
               'to drop the outgoing nozzle into (manual 4.5.2 note 3).',
    'omit': 'Ticked: parts set to Skip are left out of the CSV altogether, so '
            'the machine only ever sees what it places; the operator sheet '
            'still lists them to do by hand. Unticked: they stay in the file '
            'with Skip = 1. Nozzle change rows count the rows in the file, so '
            'the plan is redone when this changes.',
    'format': 'yy1 is the machine\'s own 13-column file. legacy is the '
              'generic 11-column layout it converts on load, without feeders '
              'or modes.',
    'import': 'Reads a CSV back off the SD card and keeps the feeder numbers, '
              'modes and heights set on the machine.',
    'colour_ready': 'Green: slot, nozzle and (once planned) a loaded nozzle -- '
                    'nothing left to decide.',
    'colour_unassigned': 'Amber: still on the default feeder slot.',
    'colour_clash': 'Red: two reels on one slot, no nozzle, or a nozzle that is '
                    'not loaded on a head or in the station.',
    'colour_override': 'Blue: this designator has its own settings.',
    'colour_skip': 'Grey: skipped in this run.',
}

# Nozzle table, manual Table 1-2 (p.29): model, what it is for.
NOZZLE_TABLE = [
    ('CN030', '0201'),
    ('CN040', '0402 (best fit)'),
    ('CN065', '0402, 0603'),
    ('CN100', '0805, diodes, 1206, 1210'),
    ('CN140', '1206, 1210, 1812, 2010, SOT-23, 5050'),
    ('CN220', 'SOP-series ICs, SOT-89, SOT-223, SOT-252'),
    ('CN400', 'ICs 5 to 12 mm'),
    ('CN750', 'ICs larger than 12 mm'),
    ('YX01-06', 'Special: soft LEDs, high-power LEDs, BGAs, 4148 round '
                'diodes, ball LEDs'),
]

SECTIONS = [
    ('Workflow', [
        ('note', 'Guide... (top left, Ctrl+G) walks through a new job in five '
                 'steps: what to export from KiCad or PADS and a check of the '
                 'folder; what was read and the fiducial; what is on the heads '
                 'and in the station, then Set up board; the board against the '
                 'feeder list; and a last check before the export. It opens at '
                 'start until you untick "Open at start".'),
        ('note', 'Undo (Ctrl+Z) steps back one change -- a reel, a nozzle, a '
                 'feeder, the order, the job header or the feeder list; Redo '
                 '(Ctrl+Y) brings it back. Every save of the feeder list keeps '
                 'the one before in feeder_list_backups/; "Earlier lists..." on '
                 'the Feeder bank tab brings one back.'),
        ('p', 'The manual\'s quick flow (p.7): export a placement file, copy '
              'it to the SD card, then on the machine: Load components > '
              'Feeder settings > Component editing > Placement.'),
        ('p', 'This program does the computer half and fills in what the '
              'touchscreen would otherwise ask for, one part type at a time.'),
        ('li', '1.  Open the board -- a KiCad project, a PADS export, or a '
               'CSV off the SD card.'),
        ('li', '2.  Press Set up board. It picks nozzles, groups the file, '
               'numbers the reels and plans the nozzle changes, and shows you '
               'the result before applying it.'),
        ('li', '3.  Check the Board tab: every part drawn where it will be '
               'placed, coloured by nozzle. Click one to see or change its '
               'settings.'),
        ('li', '4.  Fix anything amber or red on the Feeders tab, and set '
               'Skip on parts you place by hand.'),
        ('li', '5.  Export. Load the reels into the numbered slots, load the '
               'file on the machine.'),
        ('li', '6.  Step a few parts before running the board (manual p.7 '
               'note 7). Step a polarised part first.'),
        ('note', 'Keep the profile. Everything you set is saved beside the '
                 'board, keyed by part type, so a re-export from the CAD '
                 'tool keeps every decision.'),
    ]),

    ('The columns', [
        ('p', 'The file the machine reads has 13 columns. The first six come '
              'from the CAD export; the rest are yours to set, and this '
              'program fills them from the profile.'),
        ('code', 'Designator  Comment  Footprint  Mid X  Mid Y  Rotation\n'
                 'Head  FeederNo  Mount Speed  Pick Height  Place Height  '
                 'Mode  Skip'),
        ('h2', 'Comment + Footprint = one reel'),
        ('p', 'The machine groups parts into feeder types by these two '
              'together (manual 3.2.1 note 3). Two parts with the same value '
              'and package share a reel; anything else is a separate reel. '
              'That is why a placeholder value such as "C" merges eight '
              'different capacitors onto one feeder and the machine cannot '
              'tell.'),
        ('h2', 'Head'),
        ('p', '0 lets the machine choose between its two heads. 1 or 2 pins '
              'the part to one head -- useful when only one carries the right '
              'nozzle.'),
        ('h2', 'FeederNo'),
        ('p', 'Manual 3.4: slots 1-52 are tape reel feeders, 53-80 the '
              'flexible feeders (vibration, IC tray, strip), 81-99 bulk '
              'feeders where loose parts are found by camera.'),
        ('h2', 'Mount Speed %'),
        ('p', 'A percentage of the machine\'s global speed, default 70% '
              '(manual 3.5.1). Noise and dropped parts at high speed; drop it '
              'for tall or heavy parts.'),
        ('h2', 'Pick and Place Height'),
        ('p', 'Millimetres below the nozzle height datum set on the machine '
              '(manual 3.5.1 note 5). 0 uses the datum. Tall parts may need a '
              'smaller place height so the nozzle does not press them in.'),
        ('h2', 'Mode'),
        ('li', '0  Null -- no check.'),
        ('li', '1  Camera -- the up-looking camera checks the part after '
               'pick-up. The default.'),
        ('li', '2  Vacuum -- checks only that something is on the nozzle. '
               'Fastest.'),
        ('li', '3  Camera + Vacuum -- both.'),
        ('li', '4  Big IC Camera -- a wider field of view for large parts. '
               'Shield the machine from overhead light or recognition fails '
               '(manual 3.2.1 note 4).'),
        ('h2', 'Skip'),
        ('p', '1 leaves the part in the file but out of this run. Use it for '
              'a second pass with different nozzles, or for parts you place '
              'by hand.'),
    ]),

    ('Nozzles', [
        ('p', 'Manual Table 1-2. Pick the nozzle by the body\'s smaller '
              'dimension: the tip must seal on a flat area and be smaller '
              'than the part. A smaller nozzle can pick a bigger part; the '
              'reverse cannot.'),
        ('h2', 'The regular nozzles (manual Table 1-1, p.28)'),
        ('gallery', [('CN030', 'CN030\n0.6 / 0.3 mm\n0201'),
                     ('CN040', 'CN040\n0.8 / 0.4 mm\n0402'),
                     ('CN065', 'CN065\n1.0 / 0.7 mm\n0402, 0603'),
                     ('CN100', 'CN100\n1.8 / 1.0 mm\n0805, 1206'),
                     ('CN140', 'CN140\n2.0 / 1.4 mm\n1210, SOT-23'),
                     ('CN220', 'CN220\n3.6 / 2.2 mm\nSOP, SOT-223'),
                     ('CN400', 'CN400\n5.0 / 4.0 mm\nICs 5-12 mm')]),
        ('p', 'Outer / inner tip diameter under each. CN750 (9.0 / 7.5 mm, '
              'ICs over 12 mm) has no photo in the manual.'),
        ('h2', 'The special nozzles'),
        ('gallery', [('YX01', 'YX01\n3528 soft LED'),
                     ('YX02', 'YX02\nhigh-power LED'),
                     ('YX03', 'YX03\nBGA 11-17 mm'),
                     ('YX04', 'YX04\nBGA over 17 mm'),
                     ('YX05', 'YX05\n4148 round diode'),
                     ('YX06', 'YX06\n3535 ball LED'),
                     ('FPC', 'custom\nFPC/FFC socket')]),
        ('p', 'The tip shape is shown under each. The plain converter never '
              'suggests these; set one by hand on the Feeders tab where a '
              'part needs it.'),
        ('table', NOZZLE_TABLE),
        ('h2', 'Why four'),
        ('p', 'The head carries two nozzles and the nozzle station holds '
              'more, "so it can support four different nozzle types at the '
              'same time in an uninterrupted placement process" '
              '(manual 4.5.2 note 1).'),
        ('h2', 'Nozzle changes'),
        ('img', ('station', 'The nozzle station at the left front of the '
                            'machine: three positions, 1 to 3 (manual p.30).')),
        ('p', 'A change is written as: before which component, which head, '
              'which station position to drop the old nozzle into, which to '
              'pick the new one from. The file holds four of them, and this '
              'is the machine\'s own screen for them:'),
        ('img', ('change_screen', 'Auto Nozzle Change on the touchscreen -- '
                                  'the same four rows the file carries. Its '
                                  'own tip: "ensure that the suction nozzle '
                                  'station to be placed is empty to avoid '
                                  'collision."')),
        ('note', 'The position you drop into must be empty, or the head '
                 'collides with the nozzle already there (manual 4.5.2 note '
                 '3). Set up board keeps one position free for this.'),
        ('p', 'A change happens before one component number, so it only '
              'covers a whole group if that nozzle has one unbroken run in '
              'the file. That is what "grouped by nozzle" order is for.'),
        ('h2', 'The nozzles you have'),
        ('p', 'Tick "Plan only with these" on the Order & nozzles tab and '
              'enter how many of each nozzle are in the drawer. Set up board '
              'then plans only with those: a part whose ideal nozzle you lack '
              'goes to the next smaller one you own (a smaller tip still picks '
              'a bigger body), two of a nozzle lets it sit on both heads, and '
              'anything nothing you own can pick is skipped and named. The '
              'drawer is remembered across boards -- it is a fact about the '
              'machine, not the board.'),
        ('h2', 'When five or more are needed'),
        ('p', 'Set up board moves the least-used nozzle\'s parts onto the '
              'next smaller nozzle, one step at a time, until four remain. '
              'It says which parts moved. If that is not enough, the '
              'leftover parts are set to Skip for a second pass.'),
    ]),

    ('Placement order', [
        ('p', 'The machine works straight down the file. Row order is the '
              'order the board gets built in, and the # column on the Parts '
              'tab shows it.'),
        ('li', 'Designator order -- C1, C10, C11... as the machine-verified '
               'file was written.'),
        ('li', 'Feeder by feeder -- everything off one reel, then the next. '
               'Least feeder travel.'),
        ('li', 'Grouped by nozzle -- one unbroken run per nozzle. Needed for '
               'nozzle changes.'),
        ('li', 'Custom -- Move up / Move down on the Parts tab.'),
    ]),

    ('Feeders', [
        ('p', 'Each row on the Feeders tab is one reel. Auto-number gives '
              'them slots 1..n in placement order, so reels used together '
              'sit together. Slots you set by hand stay put, so adding a '
              'part to a board does not renumber reels already on the '
              'machine.'),
        ('li', 'Green: ready -- a slot of its own, a nozzle, and once a plan '
               'exists that nozzle loaded on a head or in the station.'),
        ('li', 'Amber: still on the default slot -- a to-do.'),
        ('li', 'Red: something the machine would trip over -- two reels on '
               'one slot, no nozzle, or a nozzle nothing is loaded for. Hover '
               'the row on the Board tab or read the status bar for which.'),
        ('li', 'Grey: skipped this run.'),
        ('h2', 'The feeder list'),
        ('p', 'Reels that stay on the machine go in a spreadsheet of slot, '
              'tape width and value (Feeder list… on the Feeders tab; '
              'remembered across boards). Set up board keeps the list\'s slot '
              'for every reel it can match by value, puts new reels into the '
              'list\'s free positions of the right tape width, and sets to Skip '
              'whatever has no free slot -- those get placed by hand. The '
              'operator sheet marks each reel "on machine" or LOAD.'),
        ('p', 'The Feeder bank tab is the list as a picture. Each tape slot '
              'has two bands: the upper one is the list (blue = a reel is on '
              'the machine, grey = free), the lower one is this board (green '
              '= the reel the board needs is the one the list says is there, '
              'orange LOAD = a reel to put into an empty slot, purple ≠ = the '
              'board has a different reel there than the list, red = two '
              'reels of the board on one slot). Reels with no slot at all are '
              'spelled out in red under the picture.'),
        ('p', 'Most used reels... (Feeder bank tab, or Tools menu) scans a '
              'folder of projects -- KiCad, PADS and Altium, any depth -- and '
              'counts on how many boards each reel is needed. Tick the ones to '
              'leave on the machine and "Add ticked" puts them into the feeder '
              'list\'s free tape slots, by tape width; that is the static set '
              'to load once and keep between boards.'),
        ('p', 'Only reels on the list is for a run that places just what is '
              'already on the machine: reels the list has keep their slot, '
              'every other reel is set to Skip and goes on the bench. With '
              '"Skipped parts out of the CSV" (Job header tab) ticked '
              'they are not in the file at all; the operator sheet still '
              'lists them. A Footprint column in the list, when filled, has '
              'to agree with the board\'s package for the reel to count as '
              'the same one -- so 10k on 0805 and 10k on 0603 stay apart.'),
        ('p', 'A match also writes the list as it will be once the loading '
              'is done -- <board>_yy1_feeders_<side>.ods, the feeder list '
              'with this board\'s reels in their slots, in the same columns '
              'as feeders.ods. Open it as the feeder list for the next '
              'board, or copy it over the old one once the reels are in.'),
        ('p', 'Both matches write a loading sheet beside the project '
              '(<board>_yy1_feeders_<side>.html and .txt; Open loading sheet '
              'shows it): the reels to load with their slot, tape width and '
              'what to take out of that slot first, the reels already on the '
              'machine to check, and what goes by hand. Its tick boxes are '
              'remembered by the browser, like the operator sheet.'),
        ('p', 'Selecting: click a slot, or a row in either table. The slot '
              'gets a blue frame and a marker, the row of its reel stays '
              'highlighted, and the blue bar under the picture names both. '
              'Pick a reel in the right-hand table, then click a free slot: '
              'the bar offers "Put <reel> into slot N". With a reel selected '
              'that has no slot yet, the free slots of its tape width get a '
              'dashed blue frame. Drag the divider above the tables to give '
              'the picture or the tables more room.'),
        ('p', 'Match board to list does only the feeder part of Set up board: '
              'reels on the list keep the slot the machine has, new ones take free '
              'slots of the right tape width, the rest are set to Skip. Right-'
              'click a slot to put the selected reel there, take a reel out, '
              'or edit what the list says is in that slot; Update list from '
              'this board makes the list match what you actually loaded.'),
        ('p', 'The machine has 52 tape slots. A board with more part types '
              'than that needs the flexible bank (53-80) for the rest, and '
              'Set up board says which.'),
        ('note', 'Load-side check: each feeder\'s pick position and tape '
                 'direction are set on the machine (manual 3.4.1), not in '
                 'the file. Set the pick-up position close to where the '
                 'part comes out for 0402 and smaller.'),
    ]),

    ('Coordinates and the origin', [
        ('p', 'The machine wants millimetres from the board\'s lower-left '
              'corner, and for the bottom side from the lower-right, so '
              'bottom X is mirrored (manual p.7 note 2). This program does '
              'that rebasing from the board outline, so coordinates are never '
              'negative and always inside the board.'),
        ('p', 'In KiCad set Place > Drill/Place File Origin on the lower-left '
              'corner before exporting; the converter compensates either way, '
              'but re-export if the board has moved.'),
        ('h2', 'Rotation'),
        ('p', 'The file uses 0-360. The KiCad convention agreed with the '
              'machine at offset 0 on the first board tested. If a whole '
              'board comes out turned, the global rotation offset is the one '
              'number to set. If one reel comes out turned, set Rot + on that '
              'type -- usually the tape was loaded the other way round.'),
        ('note', 'Bottom-side rotation is still unverified. Step a polarised '
                 'bottom part before running the side.'),
    ]),

    ('Fiducials and panels', [
        ('p', 'Both are optional (manual p.7 note 6) and live in the Job '
              'header tab.'),
        ('h2', 'Setting the fiducial'),
        ('p', 'Fiducial 1 X/Y is where the mark sits on the board, in the same '
              'millimetres-from-the-corner frame as every part. The Job header '
              'tab lists the fiducial footprints the board carries (FID1 and '
              'the like); pick one and press Use, or click any part on the '
              'Board tab and press Set as fiducial 1. The Board tab draws the '
              'marks as small rings and the one in the header as a crosshair, '
              'so a wrong number shows at once.'),
        ('p', 'What the machine then does: when you press Step it drives to '
              'that position first, finds the round mark with its camera, and '
              'corrects the whole board\'s position by however far off it '
              'really is (manual 3.2.4 note 2). That is the check that it '
              'works -- if it clicks and searches, the mark is not where the '
              'file says or is not round enough (3.2.2).'),
        ('h2', 'Offset X/Y'),
        ('p', 'A constant, in mm, added to every placement on the board. It '
              'is not the fiducial. Leave it at 0: if a whole board comes out '
              'shifted the same way, the manual prefers fine-tuning in the '
              'machine\'s Preview (3.2.4 note 6) to shifting the file, and a '
              'fiducial corrects board position automatically anyway.'),
        ('p', 'A fiducial is a round pad or hole with high contrast, with '
              'nothing round near it (manual 3.2.2). The machine finds it '
              'before each board and corrects the whole board\'s offset. A '
              'badly set fiducial shifts everything the same way.'),
        ('p', 'A panel is one unit\'s width and height plus how many across '
              'and down (manual 3.2.3). A single board is 1 x 1 with size 0.'),
    ]),

    ('What the machine cannot place', [
        ('li', 'Through-hole parts -- SMD only. Dropped automatically.'),
        ('li', 'Bodies over 18 x 18 mm, or taller than 12 mm. The height '
               'cannot be checked from CAD; watch electrolytics, crystals, '
               'relays and connectors.'),
        ('li', 'Anything smaller than 0201.'),
        ('li', 'Domed, recessed or irregular tops the nozzle cannot seal on, '
               'even when the size fits.'),
    ]),

    ('Where the files come from', [
        ('h2', 'KiCad'),
        ('p', 'In the PCB editor run the Fabrication Toolkit plugin and press '
              'Generate. It writes production/ next to the board: '
              'positions.csv and bom.csv (prefixed with the archive name when '
              'one is set). The .kicad_pcb supplies the outline, pad types and '
              'body sizes.'),
        ('p', 'Keep "Apply automatic component translations" as it was for '
              'the board tested on the machine (ticked). It turns '
              'SOT-23 and SOT-223 by 180 and TSSOP by 270 degrees; step one '
              'of each on a new board before running it.'),
        ('p', 'A folder with several boards: the one whose parts sit where '
              'the export says is used, and the log names it. To choose '
              'yourself, File > Open KiCad board… and pick the .kicad_pcb.'),
        ('h2', 'Altium Designer'),
        ('p', 'Nothing to export: Open Altium... reads the .PcbDoc itself -- '
              'positions, rotation, side, footprint and each part\'s Comment, '
              'the board outline, and which parts are through-hole (from '
              'their pads). A BOM saved as .xlsx beside it (Comment and '
              'Designator columns) fills in or overrides values. Altium\'s '
              'pick-and-place CSV (Assembly Outputs) is read too when present.'),
        ('h2', 'PADS Layout'),
        ('p', 'File > Export > .asc with Parts and Lines ticked. Positions, '
              'decals (with alternates), pad stacks and the outline. A PADS '
              'Logic export is the schematic and is refused. The BOM report '
              'or the spreadsheet it was pasted into supplies values.'),
        ('h2', 'Off the SD card'),
        ('p', 'Open YY1 CSV reads the machine\'s own file back. Import '
              'settings from a machine CSV pulls the feeder numbers and modes '
              'set on the touchscreen into the profile.'),
    ]),
]


def tip(key):
    return TIPS.get(key, '')
