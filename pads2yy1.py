#!/usr/bin/env python3
"""PADS Layout -> NeoDen YY1, for the boards that were never in KiCad.

PADS keeps its board in a proprietary binary `.pcb`, so unlike KiCad there is
nothing to read straight off disk. Two exports carry what the machine needs,
and this reads either -- or both, and checks them against each other:

    PADS ASCII (.asc)   File > Export from PADS *Layout*. Carries the part
                        positions, the part-type -> decal mapping, the pad
                        stacks (so through-hole parts can be dropped) and the
                        board outline, which is what coordinates get rebased
                        onto.
    Component Placement report (.xlsx)
                        Layout's own placement report: refdes, decal, value,
                        layer, orientation, X, Y, and an SMD flag. No board
                        outline, so it needs the .asc or --origin for that.

Careful: a PADS *Logic* export (`!PADS-LOGIC-...!`) is the schematic, and its
X/Y are positions on the drawing sheet. Those are refused here -- placing from
them would put every part where its symbol sits on the paper.

    ./pads2yy1.py path/to/pads_project            # finds both exports and the BOM
    ./pads2yy1.py path/to/pads_project --bom-only  # what the BOM alone knows

Everything downstream -- feeders, nozzles, placement order, the profile -- is
the same as for KiCad; only the front end differs.

## Units

The ASCII header ends in the units the file is stored in. `BASIC` is PADS's
internal resolution, and the multiplier for it was not guessed: it was pinned
against one 250-part board, where three independent things agree.

    The .asc and the Component Placement report list the same 250 parts, and
    a straight-line fit across all of them gives asc = 1500 x report exactly.

    The report's own numbers are micrometres -- the only scale on which the
    parts fit inside the board at all.

    So one basic unit is 1/1500 um, which puts the .asc board outline at
    exactly 120.000 x 74.800 mm; and the fabrication Gerber and NC drill
    (145.000 x 99.800 mm outline, 138.1 x 94.4 mm of holes) are that same
    board plus 12.5 mm panel rails on every side.

That is one PADS version, so the board size that comes out is always printed,
and a result outside anything a PCB could be is refused rather than written.
`--board-mm W H` derives the scale from a size you measure instead.
"""

import argparse
import io
import os
import re
import sys
from glob import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yy1lib as yy1


def die(msg):
    sys.exit('error: ' + msg)


# --- the BOM ---------------------------------------------------------------

# "D1 D3-4 D6-7 D19" is six parts: a range is written on the number only, and
# the prefix carries over. "IO1-3" is IO1, IO2, IO3.
RANGE = re.compile(r'^([A-Za-z_$][A-Za-z_$]*)(\d+)(?:-(\d+))?$')


def expand_refs(text):
    """Expand a PADS reference field into individual designators.

    The Qty column is a checksum on this: PADS counts the parts itself, so a
    disagreement means the expansion is wrong rather than merely unexpected.
    """
    refs = []
    prefix = ''
    for token in text.replace(',', ' ').split():
        match = RANGE.match(token)
        if not match:
            # A bare "3-4" continues the previous prefix in some exports.
            tail = re.match(r'^(\d+)-(\d+)$', token)
            if tail and prefix:
                for n in range(int(tail.group(1)), int(tail.group(2)) + 1):
                    refs.append('%s%d' % (prefix, n))
                continue
            if token:
                refs.append(token)
            continue
        prefix, first, last = match.group(1), int(match.group(2)), match.group(3)
        if last is None:
            refs.append('%s%d' % (prefix, first))
        else:
            for n in range(first, int(last) + 1):
                refs.append('%s%d' % (prefix, n))
    return refs


def pads_value(part_name):
    """PADS part names are "<type>,<value>": CC,100n 0603 -> 100n 0603.

    The type prefix is the library part class and means nothing on the
    machine's screen; the value is what tells two reels apart. Names without a
    comma are already the value.
    """
    text = str(part_name or '')
    if ',' in text:
        text = text.split(',', 1)[1]
    return yy1.clean(text)


# A part that is in the BOM but deliberately not fitted. PADS has no column
# for it, so it gets written into the value by hand -- in Czech on these
# boards. Placing one would put a component where the design says none.
DO_NOT_FIT = re.compile(r'\b(NEOSAZUJ|NEOSAZOVAT|NEOSAZENO|DNF|DNP|DNI|'
                        r'DO ?NOT ?(FIT|PLACE|POPULATE|ASSEMBL\w*))\b', re.I)


def rows_from_report(path):
    """Cells of a `|`-delimited PADS BOM report."""
    text = io.open(path, encoding='utf-8', errors='replace').read()
    rows = []
    for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if not line.strip().startswith('|'):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if cells and not all(set(c) <= set('-+= ') for c in cells):
            rows.append(cells)
    return rows


def rows_from_ods(path):
    """Cells of an OpenDocument sheet, using the standard library only.

    An .ods is a zip with the sheet in content.xml. Repeated cells are stored
    as one element with a count, which has to be expanded or the columns after
    a run of blanks all land in the wrong place.
    """
    import zipfile
    import xml.etree.ElementTree as ET

    TABLE = 'urn:oasis:names:tc:opendocument:xmlns:table:1.0'
    TEXT = 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read('content.xml'))

    rows = []
    for table in root.iter('{%s}table' % TABLE):
        for row in table.iter('{%s}table-row' % TABLE):
            cells = []
            for cell in row.iter('{%s}table-cell' % TABLE):
                repeat = int(cell.get('{%s}number-columns-repeated' % TABLE, 1))
                text = ''.join(p.text or '' for p in cell.iter('{%s}p' % TEXT))
                cells += [text.strip()] * min(repeat, 64)
            while cells and not cells[-1]:
                cells.pop()
            if cells:
                rows.append(cells)
        if rows:
            break               # the first sheet with anything on it
    return rows


def rows_from_workbook(path):
    """Cells of an .xlsx or .xls BOM."""
    if path.lower().endswith('.xlsx'):
        try:
            import openpyxl
        except ImportError:
            die('reading an .xlsx BOM needs openpyxl:\n'
                '       python -m pip install openpyxl')
        sheet = openpyxl.load_workbook(path, data_only=True).worksheets[0]
        source = sheet.iter_rows(values_only=True)
    else:
        try:
            import xlrd
        except ImportError:
            die('reading an .xls BOM needs xlrd:\n'
                '       python -m pip install xlrd')
        sheet = xlrd.open_workbook(path).sheet_by_index(0)
        source = ([c.value for c in sheet.row(r)] for r in range(sheet.nrows))

    rows = []
    for row in source:
        cells = ['' if c is None else
                 (str(int(c)) if isinstance(c, float) and c == int(c) else str(c))
                 for c in row]
        while cells and not cells[-1].strip():
            cells.pop()
        if cells:
            rows.append([c.strip() for c in cells])
    return rows


def read_bom(path):
    """Parse a PADS Bill Of Materials. Returns (parts, problems).

    parts is {designator: (value, decal, do-not-fit)}. The exports vary a lot:
    some carry a PCB DECAL column, some stop at Part Name, some put an Item
    column first, some have had their padding mangled on the way through a
    spreadsheet, and some were pasted into one. So the source is reduced to
    rows of cells first, and the columns are then taken positionally from
    whichever row looks like the header.
    """
    lower = path.lower()
    if lower.endswith('.ods'):
        table = rows_from_ods(path)
    elif lower.endswith(('.xlsx', '.xls')):
        table = rows_from_workbook(path)
    else:
        table = rows_from_report(path)

    columns = None
    parts, problems = {}, []
    record = None

    def flush(rec):
        if not rec:
            return
        refs = expand_refs(rec['refs'])
        if not refs:
            return
        try:
            qty = int(rec['qty'])
        except (ValueError, TypeError):
            qty = None
        if qty is not None and qty != len(refs):
            problems.append('%r expands to %d designator(s) but PADS counted %d'
                            % (rec['refs'], len(refs), qty))
        raw = rec['name']
        dnf = bool(DO_NOT_FIT.search(raw))
        if dnf:
            raw = DO_NOT_FIT.sub('', raw).strip().strip(',').strip()
        name, decal = pads_value(raw), yy1.clean(rec['decal'])
        for ref in refs:
            if ref in parts:
                problems.append('%s listed twice' % ref)
            parts[ref] = (name, decal, dnf)

    for cells in table:
        if columns is None:
            lowered = [c.lower() for c in cells]
            if 'qty' in lowered and any(c.startswith('reference') for c in lowered):
                columns = {}
                for i, name in enumerate(lowered):
                    if name.startswith('qty'):
                        columns['qty'] = i
                    elif name.startswith('reference'):
                        columns['refs'] = i
                    elif name.startswith('part name'):
                        columns['name'] = i
                    elif 'decal' in name:
                        columns['decal'] = i
            continue

        def cell(key):
            index = columns.get(key)
            return cells[index].strip() if index is not None and index < len(cells) else ''

        refs_text = cell('refs')
        if not refs_text:
            continue
        # A BOM pasted into a spreadsheet grows costing lines at the bottom --
        # "test", "led diody", "konektory", "fraza" -- with a quantity and a
        # price but no part. A real BOM line always names its part, so that is
        # what separates them. (Continuation rows have no quantity either and
        # are caught below.)
        if cell('qty') and not cell('name'):
            continue
        # The Reference column wraps: a long list runs onto following rows
        # that leave Qty blank. Those continue the record above rather than
        # starting a new one, and missing that loses most of the designators
        # on any board with more than a handful of decoupling caps.
        if not cell('qty') and record:
            record['refs'] += ' ' + refs_text
            continue
        flush(record)
        record = {'refs': refs_text, 'qty': cell('qty'),
                  'name': cell('name'), 'decal': cell('decal')}
    flush(record)
    if columns is None:
        problems.append('no "Qty | Reference | Part Name" header row found -- '
                        'is this a PADS Bill Of Materials report?')
    return parts, problems


# --- the PADS ASCII export -------------------------------------------------

HEADER = re.compile(r'^[!*]PADS-([A-Z]+)-V?([\d.]+)-([A-Z0-9]+)[!*]', re.I)
SECTION = re.compile(r'^\*([A-Z][A-Z0-9_]*)\*')

# mm per stored unit. BASIC is derived and cross-checked -- see the module
# docstring; the others are what the header names them.
BASIC_MM = 1.0 / 1500 / 1000
UNIT_MM = {'BASIC': BASIC_MM, 'METRIC': 0.001, 'MILS': 0.0254, 'INCHES': 25.4}

# The YY1 takes 315 x 350 mm at most and no board is under 5 mm, so an outline
# outside that says the scale is wrong, not that the board is unusual.
PLAUSIBLE_MM = (5.0, 400.0)


class Ascii(object):
    """What a PADS Layout ASCII export holds, in its own stored units."""

    def __init__(self):
        self.units = 'BASIC'
        self.version = ''
        self.parts = {}         # ref -> (x, y, rot, mirrored, parttype, alt)
        self.parttypes = {}     # part type -> [decal, alternate, ...]
        self.thru_hole = set()  # decal names carrying a drilled pad
        self.outline = None     # (min_x, min_y, max_x, max_y), absolute
        # From the ATTRIBUTE VALUES block in *MISC*: attributes per part and
        # per part type. Layout never carries a Value there -- that lives in
        # Logic -- but a stock number or a part number still names a reel.
        self.part_attrs = {}    # ref -> {attribute: text}
        self.type_attrs = {}    # part type -> {attribute: text}

    def attr(self, ref, *names):
        """First of `names` found for the part, then for its part type."""
        entry = self.parts.get(ref)
        sources = [self.part_attrs.get(ref, {})]
        if entry:
            sources.append(self.type_attrs.get(entry[4], {}))
        for source in sources:
            lowered = dict((k.lower(), v) for k, v in source.items())
            for name in names:
                if lowered.get(name.lower()):
                    return lowered[name.lower()]
        return ''

    def decal_of(self, ref):
        """The decal this part actually uses.

        A part type lists its decals as "LTST:LED3MM", and the part's own ALT
        field indexes into that list. Most of a board can be on an alternate --
        159 of 250 parts on that board -- so taking the first one would name the
        wrong package for the majority of them, and would then get their
        through-hole detection wrong too.
        """
        entry = self.parts.get(ref)
        if not entry:
            return ''
        decals = self.parttypes.get(entry[4]) or [entry[4]]
        alt = entry[5]
        return decals[alt] if 0 <= alt < len(decals) else decals[0]


def _ints(fields):
    """Leading integers of a record, stopping at the first thing that is not."""
    out = []
    for field in fields:
        try:
            out.append(int(field))
        except ValueError:
            break
    return out


def _all_ints(fields):
    for field in fields:
        try:
            int(field)
        except ValueError:
            return False
    return bool(fields)


# Names in PADS can start with a digit -- 1N4148, 3XPIN -- so a record is
# recognised by its shape rather than by what its first character looks like.
# Requiring a letter there silently dropped every part type and decal named
# after a part number, which is most of the through-hole ones.

def _is_parttype(fields):
    """NAME DECALNM TYPE GATES SIGPINS UNUSEDPINNMS FLAGS ECO."""
    return (len(fields) >= 8 and _all_ints(fields[3:7])
            and fields[7].upper() in ('Y', 'N'))


def _is_partdecal(fields):
    """NAME UNITS ORIX ORIY PIECES TERMINALS STACKS TEXT LABELS."""
    return (len(fields) == 9 and len(fields[1]) == 1 and fields[1].isalpha()
            and _all_ints(fields[2:9]))


def read_pads_ascii(path):
    """Parse a PADS Layout ASCII export. Returns (Ascii, problems).

    Record layouts are not assumed -- the file states them itself in *REMARK*
    lines, and these are the ones used:

        *PART*      REFNM PTYPENM X Y ORI GLUE MIRROR ALT CLSTID CLSTATTR
                    BROTHERID LABELS, then LABELS blocks of three lines
        *PARTTYPE*  NAME DECALNM TYPE GATES SIGPINS UNUSEDPINNMS FLAGS ECO
        *LINES*     NAME TYPE XLOC YLOC PIECES TEXT SIGSTR, then
                    PIECETYPE CORNERS ... and CORNERS coordinate lines, each
                    relative to the item's own XLOC/YLOC
        *PARTDECAL* NAME UNITS ORIX ORIY PIECES TERMINALS STACKS TEXT LABELS,
                    and later PAD PIN STACKLINES followed by
                    LEVEL SIZE SHAPE IDIA [CORNERRADIUS] [DRILL [PLATED]]
    """
    raw = io.open(path, encoding='cp1250', errors='replace').read()
    lines = raw.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    out, problems = Ascii(), []

    head = HEADER.match(lines[0] if lines else '')
    if not head:
        problems.append('no !PADS-...! header on the first line')
    else:
        kind = head.group(1).upper()
        out.version, out.units = head.group(2), head.group(3).upper()
        if 'LOGIC' in kind:
            die('%s is a PADS Logic (schematic) export. Its X/Y are positions '
                'on the drawing sheet, not on the board -- placing from them '
                'would put every part where its symbol sits on the paper.\n'
                '       Export from PADS Layout instead: File > Export, with '
                'Parts and Lines ticked.' % os.path.basename(path))

    decal = ''          # the *PARTDECAL* record the PAD lines belong to
    section = None
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if line.startswith('*REMARK*') or not line.strip():
            i += 1          # a *REMARK* documents the section, it is not one
            continue
        mark = SECTION.match(line)
        if mark:
            section = mark.group(1)
            i += 1
            continue
        fields = line.split()

        if section == 'PART' and len(fields) >= 12:
            position = _ints(fields[2:4])
            if len(position) == 2:
                try:
                    rotation = float(fields[4])
                except ValueError:
                    rotation = 0.0
                try:
                    labels = int(fields[11])
                except ValueError:
                    labels = 0
                try:
                    alt = int(fields[7])
                except ValueError:
                    alt = 0
                out.parts[fields[0]] = (position[0], position[1], rotation,
                                        fields[6].upper().startswith('M'),
                                        fields[1], alt)
                i += 1 + labels * 3          # each label is three lines
                continue

        elif section == 'PARTTYPE' and _is_parttype(fields):
            # DECALNM lists the decals as "A:B:C"; the part's ALT picks one.
            out.parttypes[fields[0]] = fields[1].split(':')

        elif section == 'LINES' and len(fields) >= 5 and fields[1] == 'BOARD':
            origin = _ints(fields[2:4])
            try:
                pieces = int(fields[4])
            except ValueError:
                pieces = 0
            if len(origin) == 2:
                xs, ys, j = [], [], i + 1
                for _ in range(pieces):
                    if j >= n:
                        break
                    try:
                        corners = int(lines[j].split()[1])
                    except (IndexError, ValueError):
                        break
                    j += 1
                    for _ in range(corners):
                        if j >= n:
                            break
                        point = _ints(lines[j].split()[:2])
                        if len(point) == 2:
                            xs.append(origin[0] + point[0])
                            ys.append(origin[1] + point[1])
                        j += 1
                if xs:
                    out.outline = (min(xs), min(ys), max(xs), max(ys))
                i = j
                continue

        elif section == 'MISC' and line.strip() == 'ATTRIBUTE VALUES':
            # A brace block: KIND NAME { "Attribute" text ... } ... for NET,
            # PART, PARTTYPE, DECAL and PCB entries. Only PART and PARTTYPE
            # are of use here.
            depth, owner, j = 0, None, i + 1
            while j < n:
                stripped = lines[j].strip()
                if stripped == '{':
                    depth += 1
                elif stripped == '}':
                    depth -= 1
                    if depth <= 0:
                        break
                    if depth == 1:
                        owner = None
                elif depth == 1 and stripped and not stripped.startswith('"'):
                    kind, _, name = stripped.partition(' ')
                    owner = (kind, name.strip()) if kind in ('PART', 'PARTTYPE') else None
                elif depth == 2 and owner:
                    # PADS quotes only names with spaces: "PART DESC" text,
                    # but Value 330R bare. The bare form is the one that
                    # carries the value of every passive on the board.
                    attr = LOGIC_ATTR.match(stripped)
                    if attr:
                        name, text = attr.group(1).strip(), attr.group(2)
                    else:
                        name, _, text = stripped.partition(' ')
                    if name and text.strip() and not name.startswith('Associated'):
                        target = out.part_attrs if owner[0] == 'PART' else out.type_attrs
                        target.setdefault(owner[1], {})[name] = yy1.clean(text)
                j += 1
            i = j + 1
            continue

        elif section == 'PARTDECAL':
            if fields[0] == 'PAD' and len(fields) >= 3:
                try:
                    stacklines = int(fields[2])
                except ValueError:
                    stacklines = 0
                for k in range(1, stacklines + 1):
                    if i + k >= n:
                        break
                    # LEVEL SIZE SHAPE IDIA [CORNERRADIUS] [DRILL [PLATED]] --
                    # SHAPE is a letter, so the numbers after it have to be
                    # read past it rather than from the front of the line. A
                    # positive one is a hole, which makes the pad through-hole.
                    stack = lines[i + k].split()
                    after = []
                    for field in stack[3:]:
                        try:
                            after.append(int(field))
                        except ValueError:
                            break
                    if decal and any(v > 0 for v in after):
                        out.thru_hole.add(decal)
                        break
                i += 1 + stacklines
                continue
            if _is_partdecal(fields):
                decal = fields[0]

        i += 1

    if not out.parts:
        problems.append('no *PART* records -- was "Parts" ticked in the export?')
    if not out.outline:
        problems.append('no BOARD outline in *LINES* -- tick "Lines" in the '
                        'export, or pass --origin')
    return out, problems


# --- the Component Placement report ---------------------------------------

def decal_name(cell):
    """Excel stores a decal called "0603" as the number 603 -- put the 0 back.

    Only for three-digit numbers that become a real chip code; anything else
    is left as it came.
    """
    if isinstance(cell, float) and cell == int(cell):
        cell = int(cell)
    text = yy1.clean(cell)
    if re.match(r'^\d{3}$', text) and ('0' + text) in yy1.IMPERIAL:
        return '0' + text
    return text


def read_comp_placing(path):
    """Read Layout's Component Placement report (.xlsx).

    Columns: Part Type, Name, PCB Decal, Value, Pins, Layer Name, Orientation,
    Position X, Position Y, SMD, Glued, and sometimes a Do Not Assembly flag.
    Returns (parts, problems) with parts as {designator: dict}.
    """
    try:
        import openpyxl
    except ImportError:
        die('reading the .xlsx placement report needs openpyxl:\n'
            '       python -m pip install openpyxl\n'
            '       (the .asc path needs nothing extra)')

    book = openpyxl.load_workbook(path, data_only=True)
    sheet = book[book.sheetnames[0]]
    rows = [r for r in sheet.iter_rows(values_only=True)]
    header = None
    for row in rows:
        if row and any(str(c).strip().lower() == 'position x'
                       for c in row if c is not None):
            header = row
            break
    if header is None:
        die('%s has no "Position X" column -- is it a Component Placement '
            'report?' % os.path.basename(path))
    index = dict((str(name).strip().lower(), i)
                 for i, name in enumerate(header) if name is not None)

    def get(row, key, default=None):
        i = index.get(key)
        if i is None or i >= len(row) or row[i] is None:
            return default
        return row[i]

    parts, problems = {}, []
    for row in rows[rows.index(header) + 1:]:
        ref = get(row, 'name')
        if ref is None or not str(ref).strip():
            continue
        try:
            x, y = float(get(row, 'position x', 0)), float(get(row, 'position y', 0))
            rotation = float(get(row, 'orientation', 0))
        except (TypeError, ValueError):
            problems.append('%s: unreadable position or orientation' % ref)
            continue
        side = str(get(row, 'layer name', 'Top')).strip().lower()
        parts[str(ref).strip()] = {
            'x': x, 'y': y, 'rot': rotation,
            'mirrored': side.startswith('bot'),
            'decal': decal_name(get(row, 'pcb decal', '')),
            'value': yy1.clean(get(row, 'value', '')),
            'smd': str(get(row, 'smd', '')).strip().lower().startswith('y'),
            'skip': str(get(row, 'do not assembly', '')).strip().lower()
                    .startswith(('y', 'x', 'true', '1')),
        }
    return parts, problems


# --- the PADS Logic export, as a source of values ---------------------------

LOGIC_HEADER = re.compile(r'^\*PADS-LOGIC-V?([\d.]+)-?(\w*)\*', re.I)
LOGIC_ATTR = re.compile(r'^"([^"]+)"\s*(.*)$')

# Part types that are a class, not a part: CC, RR, C+, LL, DD. Naming a reel
# after one of these would merge every capacitor on the board.
GENERIC_TYPE = re.compile(r'^[A-Z]{1,3}\+?$')


def is_logic_export(path):
    try:
        with io.open(path, encoding='cp1250', errors='replace') as handle:
            return bool(LOGIC_HEADER.match(handle.readline()))
    except (IOError, OSError):
        return False


def read_logic_values(path):
    """Per-part attributes from a PADS Logic ASCII export (File > Export).

    The Layout export carries part types and decals but no values -- "CC" is
    all it says about a capacitor. The schematic export carries, per part,
    lines such as

        "VALUE" 100n
        "PART NUMBER" PCA82C251-smd
        "PART DESC" kondenzator keramicky
        "PCB DECAL" SMD0805

    Its X/Y are sheet positions and are ignored here; only the attributes are
    read. Returns ({ref: {attribute: text}}, problems).
    """
    text = io.open(path, encoding='cp1250', errors='replace').read()
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    problems = []
    if not LOGIC_HEADER.match(lines[0] if lines else ''):
        problems.append('%s does not start with a *PADS-LOGIC* header'
                        % os.path.basename(path))
        return {}, problems

    values, section, current = {}, None, None
    for line in lines:
        mark = SECTION.match(line)
        if mark:
            section = mark.group(1)
            current = None
            continue
        if section != 'PART' or not line.strip():
            current = None if not line.strip() else current
            continue
        fields = line.split()
        # REFNM PTYPENM X Y ... starts a record: a name, a type, two integers.
        # Label geometry lines ("150 -240 0 2 100 10 ...") have the same
        # shape but start with a number, and a designator never does.
        if (len(fields) >= 6 and not line.startswith('"')
                and not re.match(r'^-?\d', fields[0])
                and not re.match(r'^-?\d', fields[1])
                and _all_ints(fields[2:4])
                and fields[0] not in ('REF-DES', 'PART-TYPE', 'VALUE')):
            current = values.setdefault(fields[0], {'parttype': fields[1]})
            continue
        attr = LOGIC_ATTR.match(line)
        if attr and current is not None:
            current[attr.group(1).strip().upper()] = yy1.clean(attr.group(2))
    if not values:
        problems.append('no *PART* records with attributes in %s'
                        % os.path.basename(path))
    return values, problems


def logic_value(entry):
    """The value a reel should be named by, from a Logic part record.

    VALUE first; a part with no value but a part number (an IC) uses that; a
    part with neither uses its type unless the type is a generic class.
    """
    if not entry:
        return ''
    for key in ('VALUE', 'PART NUMBER'):
        if entry.get(key):
            return entry[key]
    parttype = entry.get('parttype', '')
    return '' if GENERIC_TYPE.match(parttype) else parttype


def find(project, patterns):
    """Files matching any pattern anywhere under the project, newest first."""
    hits = []
    for pattern in patterns:
        hits += glob(os.path.join(project, '**', pattern), recursive=True)
    return sorted(set(hits), key=os.path.getmtime, reverse=True)


def cross_check(asc, placing):
    """Fit the two exports against each other. Returns lines to print.

    They are produced by different parts of PADS, so agreement is real
    evidence that both were read correctly -- and a disagreement means one of
    them is from a different revision of the board.
    """
    lines = []
    common = sorted(set(asc.parts) & set(placing))
    if not common:
        return ['WARNING: the .asc and the placement report share no '
                'designators -- are they from the same board?']
    pairs = [(placing[r]['x'], float(asc.parts[r][0])) for r in common]
    low = min(pairs)
    high = max(pairs, key=lambda p: abs(p[0] - low[0]))
    if high[0] != low[0]:
        ratio = (high[1] - low[1]) / (high[0] - low[0])
        offset = low[1] - ratio * low[0]
        worst = max(abs(ratio * x + offset - y) for x, y in pairs)
        lines.append('check   %d part(s) in both exports; .asc = %.4f x report, '
                     'worst disagreement %.0f unit(s)'
                     % (len(common), ratio, worst))
    turned = [r for r in common
              if yy1.norm_rot(asc.parts[r][2]) != yy1.norm_rot(placing[r]['rot'])]
    flipped = [r for r in common if asc.parts[r][3] != placing[r]['mirrored']]
    lines.append('        rotations agree on %d/%d, side on %d/%d'
                 % (len(common) - len(turned), len(common),
                    len(common) - len(flipped), len(common)))
    for name, refs in (('only in the .asc', sorted(set(asc.parts) - set(placing))),
                       ('only in the report', sorted(set(placing) - set(asc.parts)))):
        if refs:
            lines.append('WARNING: %d part(s) %s: %s'
                         % (len(refs), name, ', '.join(refs[:8])))
    return lines


class Conversion(object):
    """Everything one PADS project turned into, before anything is written."""

    def __init__(self, **kw):
        self.project = kw.get('project', '')
        self.prefix = kw.get('prefix', '')
        self.bom = kw.get('bom') or {}
        self.bom_path = kw.get('bom_path')
        self.asc_path = kw.get('asc_path')
        self.xls_path = kw.get('xls_path')
        self.placing = kw.get('placing') or {}
        self.sides = kw.get('sides') or {'T': [], 'B': []}
        self.dropped = kw.get('dropped') or []
        self.no_bom = kw.get('no_bom') or []
        self.fiducials = kw.get('fiducials') or []   # (ref, x_mm, y_mm, side)
        self.width = kw.get('width', 0.0)
        self.height = kw.get('height', 0.0)
        self.outline = kw.get('outline', False)   # True if from the board outline
        self.profile = kw.get('profile') or yy1.Profile()

    @property
    def placed(self):
        return sum(len(v) for v in self.sides.values())


def arguments(argv=None):
    """The command line; also what the window fills in by hand."""
    parser = argparse.ArgumentParser(
        description='Convert PADS Layout output to NeoDen YY1 placement files.')
    parser.add_argument('project', help='PADS project directory')
    parser.add_argument('--asc', help='PADS ASCII export (default: found)')
    parser.add_argument('--placing', help='Component Placement .xlsx (default: found)')
    parser.add_argument('--logic', help='PADS Logic ASCII export (.txt), read for '
                                        'part values only (default: found)')
    parser.add_argument('--bom', help='BOM report (default: found)')
    parser.add_argument('--bom-only', action='store_true',
                        help='read and check the BOM, write nothing')
    parser.add_argument('--outdir', help='where to write (default: <project>)')
    parser.add_argument('--prefix', help='output filename prefix')
    parser.add_argument('--profile', help='settings JSON')
    parser.add_argument('--units', choices=sorted(UNIT_MM),
                        help='override the units the ASCII file is stored in')
    parser.add_argument('--board-mm', nargs=2, type=float, metavar=('W', 'H'),
                        help='true board size in mm; the unit scale is derived '
                             'from it instead of from the header')
    parser.add_argument('--origin', nargs=2, type=float, metavar=('X', 'Y'),
                        help="board's lower-left corner in the export's own "
                             'units, when there is no outline in the file')
    parser.add_argument('--include-tht', action='store_true',
                        help='keep through-hole parts instead of dropping them')
    parser.add_argument('--exclude', default='',
                        help='comma-separated designators to leave out')
    parser.add_argument('--bottom-rot', default='as-is',
                        choices=['as-is', 'negate', 'mirror180'],
                        help='bottom-side rotation convention (default: as-is)')
    return parser.parse_args(argv)


def defaults(project):
    """A Namespace as if only the project had been given."""
    return arguments([project])


def analyse(args, say=print):
    """Read the exports and the BOM; return a Conversion, writing nothing.

    `say` receives every line of the report -- print on the command line,
    a log in the window.
    """
    project = os.path.abspath(args.project)
    if not os.path.isdir(project):
        die('%s is not a directory' % project)

    # -- BOM
    # A BOM turns up as PADS's own report, or as the spreadsheet somebody
    # pasted it into for the production run.
    bom_path = args.bom or (find(project, [
        'BillOfMaterials*.txt', 'BillOfMaterials*.ods', 'BillOfMaterials*.xlsx',
        'BillOfMaterials*.xls', '*[Pp]art[_ ]?[Ll]ist*.ods',
        '*[Pp]art[_ ]?[Ll]ist*.xls', '*[Pp]art[_ ]?[Ll]ist*.xlsx']) or [None])[0]
    bom = {}
    if bom_path:
        bom, problems = read_bom(bom_path)
        say('bom     %s' % os.path.relpath(bom_path, project))
        say('        %d designator(s), %d part type(s)'
              % (len(bom), len(set(bom.values()))))
        for line in problems:
            say('WARNING: %s' % line)
    else:
        say('note:   no BillOfMaterials report found; values and footprints '
              'come from the placement export alone')
    if args.bom_only:
        return Conversion(project=project, bom=bom, bom_path=bom_path)

    # -- placement
    asc_path = args.asc or (find(project, ['*.asc']) or [None])[0]
    xls_path = args.placing or (find(project, ['*COMPPLACING*.xlsx',
                                               '*[Pp]lacement*.xlsx']) or [None])[0]
    if not asc_path and not xls_path:
        say('')
        say('No placement export found under %s.' % project)
        say('PADS keeps positions in the binary .pcb, so they have to come')
        say('out of PADS Layout:')
        say('  File > Export > *.asc with Parts and Lines ticked, or')
        say('  a Component Placement report (.xlsx).')
        return Conversion(project=project, bom=bom, bom_path=bom_path)

    for label, given in (('--asc', args.asc), ('--placing', args.placing),
                         ('--bom', args.bom)):
        if given and not os.path.isfile(given):
            die('%s %s does not exist' % (label, given))

    asc = None
    if asc_path:
        asc, problems = read_pads_ascii(asc_path)
        say('asc     %s' % os.path.relpath(asc_path, project))
        say('        PADS V%s, %s units, %d part(s), %d decal(s) with a '
              'drilled pad' % (asc.version, asc.units, len(asc.parts),
                               len(asc.thru_hole)))
        alternates = sum(1 for e in asc.parts.values() if e[5])
        if alternates:
            say('        %d part(s) on an alternate decal' % alternates)
        for line in problems:
            say('WARNING: %s' % line)

    placing = {}
    if xls_path:
        placing, problems = read_comp_placing(xls_path)
        say('placing %s' % os.path.relpath(xls_path, project))
        say('        %d placement(s), %d marked SMD'
              % (len(placing), sum(1 for p in placing.values() if p['smd'])))
        for line in problems:
            say('WARNING: %s' % line)

    # Values: the Layout export has none, so the schematic's export supplies
    # them when there is no BOM -- or fills in whatever the BOM lacks.
    logic = {}
    logic_path = args.logic
    if not logic_path:
        candidates = [p for p in find(project, ['*.txt']) if is_logic_export(p)]
        logic_path = candidates[0] if candidates else None
    if logic_path:
        logic, problems = read_logic_values(logic_path)
        say('logic   %s' % os.path.relpath(logic_path, project))
        say('        %d part(s) with attributes, %d with a value'
            % (len(logic), sum(1 for e in logic.values() if logic_value(e))))
        for line in problems:
            say('WARNING: %s' % line)

    if asc and asc.parts and placing:
        for line in cross_check(asc, placing):
            say(line)

    # -- scale
    use_asc = bool(asc and asc.parts)
    source = asc.parts if use_asc else placing
    outline = asc.outline if asc else None

    if use_asc:
        units = args.units or asc.units
        if units not in UNIT_MM:
            die('units %r are not one of %s -- pass --units or --board-mm'
                % (units, ', '.join(sorted(UNIT_MM))))
        scale = UNIT_MM[units]
    else:
        # The placement report is in micrometres of its own; the .asc header's
        # units describe the .asc and say nothing about the report.
        units = args.units or 'REPORT-um'
        scale = UNIT_MM.get(args.units, 0.001) if args.units else 0.001
        if outline:
            # Bring the .asc outline into the report's frame.
            ratio = (UNIT_MM.get(asc.units, BASIC_MM) / scale)
            outline = tuple(v * ratio for v in outline)

    def position(entry):
        return (entry[0], entry[1]) if use_asc else (entry['x'], entry['y'])

    if outline:
        min_x, min_y, max_x, max_y = outline
    else:
        xs = [position(e)[0] for e in source.values()]
        ys = [position(e)[1] for e in source.values()]
        min_x, min_y = args.origin if args.origin else (min(xs), min(ys))
        max_x, max_y = max(xs), max(ys)

    if args.board_mm:
        want_w, want_h = args.board_mm
        scale = ((want_w / (max_x - min_x)) + (want_h / (max_y - min_y))) / 2.0
        say('scale   derived from --board-mm: 1 unit = %.10g mm' % scale)

    width = (max_x - min_x) * scale
    height = (max_y - min_y) * scale
    say('board   %.3f x %.3f mm%s'
          % (width, height,
             '' if outline else '   (from the PARTS, not the board outline)'))
    low, high = PLAUSIBLE_MM
    if not low <= max(width, height) <= high:
        die('that is not a board size, so the unit scale is wrong. The header '
            'said %s; try --units or --board-mm W H.' % units)
    if not outline:
        say('WARNING: no board outline, so coordinates are measured from the')
        say('         extent of the parts. The YY1 wants the board\'s')
        say("         lower-left corner (manual p.7) -- re-export the .asc")
        say('         with Lines ticked, or pass --origin X Y.')

    # -- assemble
    # The profile beside the project is the board's memory: renames, merges,
    # feeders, nozzles. It is read whether or not --profile names it, so a
    # plain re-run keeps every decision instead of starting from nothing.
    profile_path = args.profile or os.path.join(
        project, '%s_yy1_profile.json' % (args.prefix or os.path.basename(project)))
    profile = yy1.Profile.load(profile_path)
    excluded = set(r.strip() for r in args.exclude.split(',') if r.strip())
    sides, dropped, no_bom = {'T': [], 'B': []}, [], []

    for ref in sorted(source):
        if ref in excluded:
            dropped.append((ref, 'excluded on request'))
            continue
        if use_asc:
            x, y, rotation, mirrored = source[ref][:4]
            decal, value = asc.decal_of(ref), ''
            tht, marked = decal in asc.thru_hole, False
        else:
            row = source[ref]
            x, y, rotation = row['x'], row['y'], row['rot']
            mirrored, decal, value = row['mirrored'], row['decal'], row['value']
            tht, marked = not row['smd'], row['skip']
        # The report is the one that knows what is surface mount and what has
        # been marked off, so it wins on both wherever it has an opinion.
        if ref in placing:
            tht = not placing[ref]['smd']
            marked = marked or placing[ref]['skip']
            value = value or placing[ref]['value']
            # Per part, so it resolves alternate decals that the part type's
            # default does not.
            decal = placing[ref]['decal'] or decal
        bom_value, bom_decal, bom_dnf = bom.get(ref, ('', '', False))
        if marked or bom_dnf:
            dropped.append((ref, 'marked do-not-fit'))
            continue
        if tht and not args.include_tht:
            dropped.append((ref, 'through-hole -- the YY1 places SMD only'))
            continue
        # Name the reel by the best value on offer: BOM, then the schematic
        # export, then the placement report, then a non-generic part type.
        value_text = bom_value or logic_value(logic.get(ref)) or value
        if not value_text and asc:
            # Layout's own attributes: a part number names an IC, a stock
            # number identifies the exact reel even if it does not read as
            # a value. Both come before falling back to the type name.
            value_text = asc.attr(ref, 'Value', 'Part Number', 'PART NUMBER')
            stock = asc.attr(ref, 'SKLAD CISLO')
            # A stock number has digits in it. "dle hodnoty" ("by value") is
            # the placeholder the library puts on every capacitor, and naming
            # a reel by it would merge them all under one label.
            if not value_text and stock and re.search(r'\d', stock):
                value_text = stock
        if not value_text and use_asc:
            parttype = source[ref][4]
            if not GENERIC_TYPE.match(parttype):
                value_text = parttype
        # The do-not-fit marker can arrive through any of those sources, not
        # just the BOM: a schematic with "NEOSAZUJ" as the value means the
        # same thing, and must not become a reel called NEOSAZUJ.
        if value_text and DO_NOT_FIT.search(value_text):
            dropped.append((ref, 'marked do-not-fit'))
            continue
        # Only worth reporting for parts that survive to the file -- a
        # through-hole connector missing from the BOM changes nothing.
        if not value_text:
            no_bom.append(ref)

        package = yy1.normalise_package(bom_decal or decal, profile.packages)
        comment = yy1.normalise_value(value_text, profile.values)

        side = 'B' if mirrored else 'T'
        mm_x, mm_y = (x - min_x) * scale, (y - min_y) * scale
        if side == 'B':
            mm_x = width - mm_x          # bottom is measured from the right

        base = yy1.norm_rot(rotation)
        if side == 'B':
            if args.bottom_rot == 'negate':
                base = yy1.norm_rot(-base)
            elif args.bottom_rot == 'mirror180':
                base = yy1.norm_rot(180.0 - base)

        package, comment = profile.alias(package, comment)
        column = profile.settings_for(package, comment, ref)
        sides[side].append({
            'ref': ref, 'comment': comment, 'package': package,
            'x': mm_x, 'y': mm_y, 'base_rot': base,
            'rot': yy1.norm_rot(base + profile.rot_offset
                                + column.get('rot_offset', 0.0)),
            'head': column['head'], 'feeder': column['feeder'],
            'speed': column['speed'], 'pick_h': column['pick_h'],
            'place_h': column['place_h'], 'mode': column['mode'],
            'skip': column['skip'],
            # PADS stores no body outline, so the package name is what the
            # nozzle has to come from (manual p.29 suction-surface table).
            'nozzle': yy1.nozzle_for_package(package),
            'raw_package': bom_decal or decal, 'raw_value': value_text,
        })


    # Fiducials: anything called FID* or on a fiducial decal, in the parts'
    # frame, whether or not it survived as a placement.
    fiducials = []
    for ref in sorted(source):
        x, y = position(source[ref])
        mirrored = source[ref][3] if use_asc else source[ref]['mirrored']
        decal = asc.decal_of(ref) if use_asc else source[ref]['decal']
        parttype = source[ref][4] if use_asc else ''
        # A fiducial footprint, or a plain drilled hole -- the manual accepts
        # a round hole with contrast as a fiducial (3.2.2 note 1).
        if re.search(r'fid', ref, re.I) or re.search(r'fid|hole', decal, re.I) \
                or re.search(r'fid', parttype, re.I):
            fx, fy = (x - min_x) * scale, (y - min_y) * scale
            if mirrored:
                fx = width - fx
            fiducials.append((ref, round(fx, 3), round(fy, 3),
                              'B' if mirrored else 'T'))

    prefix = args.prefix or os.path.basename(project)
    return Conversion(project=project, prefix=prefix, bom=bom, bom_path=bom_path,
                      asc_path=asc_path, xls_path=xls_path, placing=placing,
                      sides=sides, dropped=dropped, no_bom=no_bom,
                      fiducials=fiducials,
                      width=width, height=height, outline=bool(outline),
                      profile=profile)


def write_files(conv, outdir=None, profile_path=None, say=print):
    """Write one file per side plus the profile beside them."""
    project, prefix, sides, profile = conv.project, conv.prefix, conv.sides, conv.profile
    width, height = conv.width, conv.height
    # the board turned on the machine, when the profile says so
    angle = getattr(profile, 'board_rotation', 0) or 0
    if angle:
        for side in sides:
            yy1.orient(sides[side], width, height, angle)
            for part in sides[side]:
                part['rot'] = yy1.norm_rot(part.get('base_rot', part['rot']) + angle
                                           + profile.rot_offset +
                                           profile.settings_for(part['package'], part['comment'],
                                                                part['ref']).get('rot_offset', 0.0))
        width, height = yy1.turn_size(width, height, angle)
    class _A(object):
        pass
    args = _A()
    args.outdir, args.prefix, args.profile = outdir, prefix, profile_path
    outdir = args.outdir or project
    prefix = args.prefix or os.path.basename(project)
    if not os.path.isdir(outdir):
        os.makedirs(outdir)
    for side, label in (('T', 'top'), ('B', 'bottom')):
        if not sides[side]:
            continue
        # In the profile's order, the same as the window exports.
        nozzle_of = dict((p['ref'], profile.nozzle_for(
            yy1.type_key(p['package'], p['comment']), p.get('nozzle', '')))
            for p in sides[side])
        parts = yy1.order_parts(sides[side], profile, nozzle_of, side)
        rows = yy1.file_parts(parts, profile)
        path = os.path.join(outdir, '%s_yy1_%s.csv' % (prefix, label))
        yy1.write(path, rows, profile.job_for(side))
        say('wrote   %s  (%d parts%s)' % (path, len(rows),
                                           '' if len(rows) == len(parts) else
                                           ', %d skipped left out' % (len(parts) - len(rows))))
        for sheet in yy1.write_setup_sheet(path, parts, profile, side,
                                           (width, height), prefix):
            say('sheet   %s' % sheet)
    # The board size goes into the profile beside the file, so the window can
    # draw the board when the CSV is opened on its own -- the CAD size; the
    # rotation is in the profile too.
    profile.board = profile.board or prefix
    profile.board_w, profile.board_h = conv.width, conv.height
    # Beside the project, not beside the output: the profile belongs to the
    # board, and the window looks for it there. Writing it next to the CSV
    # when --outdir differs made two copies that drifted apart.
    profile_path = args.profile or os.path.join(project, '%s_yy1_profile.json' % prefix)
    profile.save(profile_path)
    say('profile %s' % profile_path)
    # A copy beside the CSV as well: the file carries no nozzles or reel
    # settings, so a card opened on its own needs the profile next to it.
    beside = os.path.join(outdir, os.path.basename(profile_path))
    if os.path.abspath(beside) != os.path.abspath(profile_path):
        profile.save(beside)

    return profile_path


def summarise(conv, say=print):
    """What was skipped and what still needs a human."""
    sides, dropped, no_bom = conv.sides, conv.dropped, conv.no_bom
    placing, bom, width, height = conv.placing, conv.bom, conv.width, conv.height
    reasons = {}
    for ref, why in dropped:
        reasons.setdefault(why, []).append(ref)
    for why in sorted(reasons):
        refs = sorted(reasons[why])
        say('skipped %3d  %s  (%s%s)'
              % (len(refs), why, ', '.join(refs[:8]),
                 ' ...' if len(refs) > 8 else ''))
    if no_bom and placing:
        say('note:   %d part(s) are not in a BOM report, so the placement '
              'export\'s own value and decal were used' % len(no_bom))
    elif no_bom and bom:
        say('')
        say('WARNING: %d part(s) are on the board but not in the BOM, so they'
              % len(no_bom))
        say('         have no value: %s'
              % ', '.join(sorted(no_bom)[:14]))
        say('         The machine groups feeders by value AND footprint, so')
        say('         these will merge with anything sharing their footprint.')
        say('         Add them to the BOM, or set the values on the Feeders')
        say('         tab.')
        say('')
    elif no_bom:
        say('')
        say('WARNING: %d part(s) have no value. The .asc carries part types,'
              % len(no_bom))
        say('         not values, and no Bill Of Materials report was found.')
        say('         The machine groups feeders by value AND footprint, so')
        say('         without values everything sharing a footprint lands on')
        say('         one reel. In PADS Logic: File > Export > .txt into this')
        say('         folder (it carries every part\'s VALUE), or export a BOM')
        say('         report, or fill the values in on the Feeders tab.')
        say('')
    off = [p['ref'] for side in sides.values() for p in side
           if not (0 <= p['x'] <= width and 0 <= p['y'] <= height)]
    for ref in off:
        say('warning: %s falls outside the board outline' % ref)


def main():
    args = arguments()
    conv = analyse(args)
    if args.bom_only or not conv.placed:
        return 0 if args.bom_only else 1
    write_files(conv, args.outdir, args.profile)
    summarise(conv)
    return 0


if __name__ == '__main__':
    sys.exit(main())
