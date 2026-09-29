#!/usr/bin/env python3
"""Altium Designer boards -> NeoDen YY1 placement files.

The .PcbDoc is read directly -- no export needed. It is an OLE compound
file (the container Word 97 used) with one stream per record type, and
the ones that matter are simple:

    Components6/Data   text records: |X=3992.36mil|Y=..|ROTATION=..|LAYER=TOP
                       |PATTERN=RES 0805_2012|SOURCEDESIGNATOR=R21|...
    Texts6/Data        binary; each text knows its component and whether
                       it is the designator or the Comment (the value)
    Pads6/Data         binary; each pad knows its component and hole size,
                       which is how through-hole parts are told apart, and
                       the pads' extent is the body size for the nozzle
    Board6/Data        text; VX0/VY0.. are the board outline

Units are mil throughout; Y grows upwards like the machine's.

Alternatively Altium's own pick-and-place export (File > Assembly Outputs
> Generates pick and place files, CSV) is read, with the outline still
taken from the .PcbDoc when one is beside it.

Values come from the Comment on the board (what Altium shows on the BOM),
and a BOM spreadsheet with Comment and Designator columns fills in or
overrides them.

    ./altium2yy1.py path/to/project        # or the .PcbDoc itself
"""

import argparse
import csv
import io
import os
import re
import struct
import sys
from glob import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yy1lib as yy1
import pads2yy1 as p2y

MIL = 0.0254
FIDUCIAL = re.compile(r'fid|fiducial', re.I)


def die(msg):
    sys.stderr.write('error: %s\n' % msg)
    raise SystemExit(msg)


# --- the container -----------------------------------------------------------

class Compound(object):
    """Just enough of the OLE compound file format to read named streams.

    Sectors, FAT, DIFAT, the directory tree and the mini-stream for small
    streams. Nothing is written.
    """

    MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
    END = 0xFFFFFFFE
    FREE = 0xFFFFFFFF

    def __init__(self, path):
        with open(path, 'rb') as handle:
            self.data = handle.read()
        d = self.data
        if d[:8] != self.MAGIC:
            raise ValueError('%s is not an Altium (OLE) file' % path)
        shift, mini_shift = struct.unpack_from('<HH', d, 0x1e)
        self.sector, self.mini_sector = 1 << shift, 1 << mini_shift
        dir_first = struct.unpack_from('<I', d, 0x30)[0]
        self.mini_cutoff = struct.unpack_from('<I', d, 0x38)[0]
        minifat_first, minifat_count = struct.unpack_from('<II', d, 0x3c)
        difat_first, difat_count = struct.unpack_from('<II', d, 0x44)
        per = self.sector // 4
        difat = list(struct.unpack_from('<109I', d, 0x4c))
        s = difat_first
        for _ in range(difat_count):
            block = struct.unpack_from('<%dI' % per, d, (s + 1) * self.sector)
            difat += block[:-1]
            s = block[-1]
        self.fat = []
        for s in difat:
            if s >= 0xFFFFFFFA:
                continue
            self.fat += struct.unpack_from('<%dI' % per, d, (s + 1) * self.sector)
        raw = self._chain(dir_first)
        self.entries = []
        for i in range(len(raw) // 128):
            e = raw[i * 128:(i + 1) * 128]
            nlen = struct.unpack_from('<H', e, 0x40)[0]
            name = e[:max(0, nlen - 2)].decode('utf-16-le', 'replace')
            left, right, child = struct.unpack_from('<III', e, 0x44)
            start = struct.unpack_from('<I', e, 0x74)[0]
            size = struct.unpack_from('<I', e, 0x78)[0]
            self.entries.append((name, e[0x42], left, right, child, start, size))
        root = self.entries[0]
        self.mini = self._chain(root[5]) if root[6] else b''
        self.minifat = []
        s = minifat_first
        for _ in range(minifat_count):
            self.minifat += struct.unpack_from('<%dI' % per, d, (s + 1) * self.sector)
            s = self.fat[s]
        self.streams = {}
        self._walk(root[4], '')

    def _chain(self, start):
        out, s, seen = [], start, set()
        while s < 0xFFFFFFFA and s not in seen and s < len(self.fat):
            seen.add(s)
            off = (s + 1) * self.sector
            out.append(self.data[off:off + self.sector])
            s = self.fat[s]
        return b''.join(out)

    def _mini_chain(self, start):
        out, s, seen = [], start, set()
        while s < 0xFFFFFFFA and s not in seen and s < len(self.minifat):
            seen.add(s)
            off = s * self.mini_sector
            out.append(self.mini[off:off + self.mini_sector])
            s = self.minifat[s]
        return b''.join(out)

    def _walk(self, index, prefix):
        if index >= len(self.entries) or index == self.FREE:
            return
        name, kind, left, right, child, start, size = self.entries[index]
        self._walk(left, prefix)
        path = prefix + name
        if kind == 1:
            self._walk(child, path + '/')
        elif kind == 2:
            self.streams[path] = index
        self._walk(right, prefix)

    def read(self, path):
        if path not in self.streams:
            return None
        entry = self.entries[self.streams[path]]
        start, size = entry[5], entry[6]
        if size < self.mini_cutoff:
            return self._mini_chain(start)[:size]
        return self._chain(start)[:size]


# --- the records --------------------------------------------------------------

def mil(text):
    """'3992.3622mil' -> mm; also plain numbers (mil) and 'mm' suffixes."""
    t = (text or '').strip().lower()
    if not t:
        return 0.0
    if t.endswith('mm'):
        return float(t[:-2])
    if t.endswith('mil'):
        t = t[:-3]
    return float(t) * MIL


def text_records(blob):
    """Records of a |KEY=VALUE| stream: [{key: value}, ...]."""
    out, i = [], 0
    while blob and i + 4 <= len(blob):
        n = struct.unpack_from('<I', blob, i)[0]
        i += 4
        raw = blob[i:i + n].rstrip(b'\x00').decode('latin-1')
        i += n
        fields = {}
        for kv in raw.split('|'):
            if '=' in kv:
                k, v = kv.split('=', 1)
                fields[k.strip().upper()] = v
        if fields:
            out.append(fields)
    return out


def components(blob):
    """The board's components in file order (Texts6 and Pads6 refer to
    them by that index)."""
    out = []
    for f in text_records(blob):
        out.append({
            'ref': yy1.clean(f.get('SOURCEDESIGNATOR', '')),
            'pattern': f.get('PATTERN', '').strip(),
            'x': mil(f.get('X')), 'y': mil(f.get('Y')),
            'rot': float(f.get('ROTATION', '0') or 0),
            'layer': f.get('LAYER', 'TOP').strip().upper(),
            'description': f.get('SOURCEDESCRIPTION', '').strip(),
            'libref': f.get('SOURCELIBREFERENCE', '').strip(),
        })
    return out


def texts(blob):
    """Comment and designator strings per component: {index: {'comment':
    text, 'designator': text}}. A text record is one byte of type, a
    length-prefixed binary block, then a length-prefixed Pascal string.
    In the block: the component index at 7 (int16, -1 for free text) and
    two flags at 40/41 -- comment, designator."""
    out, i = {}, 0
    while blob and i + 5 <= len(blob):
        n = struct.unpack_from('<I', blob, i + 1)[0]
        data = blob[i + 5:i + 5 + n]
        i += 5 + n
        if i + 4 > len(blob):
            break
        m = struct.unpack_from('<I', blob, i)[0]
        raw = blob[i + 4:i + 4 + m]
        i += 4 + m
        if len(data) < 42 or not raw:
            continue
        index = struct.unpack_from('<h', data, 7)[0]
        if index < 0:
            continue
        text = raw[1:1 + raw[0]].decode('latin-1') if raw[0] < len(raw) else raw[1:].decode('latin-1')
        slot = out.setdefault(index, {})
        if data[40] == 1:
            slot['comment'] = text
        elif data[41] == 1:
            slot['designator'] = text
    return out


def pads(blob):
    """Per component: (through-hole?, [(x, y, w, h), ...] in mm) from the
    pad records -- six length-prefixed blocks after a type byte; the fifth
    holds the geometry: component index at 7, x/y at 13, top size at 21,
    hole size at 45, all in 1/10000 mil."""
    out, i = {}, 0
    while blob and i < len(blob):
        i += 1                                  # record type
        blocks = []
        for _ in range(6):
            if i + 4 > len(blob):
                break
            n = struct.unpack_from('<I', blob, i)[0]
            i += 4
            blocks.append(blob[i:i + n])
            i += n
        if len(blocks) < 5 or len(blocks[4]) < 49:
            continue
        main = blocks[4]
        index = struct.unpack_from('<h', main, 7)[0]
        if index < 0:
            continue
        x, y = struct.unpack_from('<ii', main, 13)
        w, h = struct.unpack_from('<ii', main, 21)
        hole = struct.unpack_from('<i', main, 45)[0]
        tht, shapes = out.get(index, (False, []))
        shapes.append((x * MIL / 10000, y * MIL / 10000, w * MIL / 10000, h * MIL / 10000))
        out[index] = (tht or hole > 0, shapes)
    return out


def outline(blob):
    """Board outline vertices in mm, and its bounding box."""
    fields = text_records(blob)
    fields = fields[0] if fields else {}
    points = []
    for k in range(0, 10000):
        vx, vy = fields.get('VX%d' % k), fields.get('VY%d' % k)
        if vx is None or vy is None:
            break
        points.append((mil(vx), mil(vy)))
        # an arc segment bows out to its radius; take that in too
        if fields.get('KIND%d' % k, '0').strip() == '1':
            try:
                cx, cy, r = mil(fields.get('CX%d' % k)), mil(fields.get('CY%d' % k)), mil(fields.get('R%d' % k))
                points += [(cx - r, cy - r), (cx + r, cy + r)]
            except (TypeError, ValueError):
                pass
    return points


def read_pcbdoc(path):
    """Everything the converter needs off one .PcbDoc."""
    doc = Compound(path)
    comps = components(doc.read('Components6/Data') or b'')
    named = texts(doc.read('Texts6/Data') or b'')
    padded = pads(doc.read('Pads6/Data') or b'')
    for i, c in enumerate(comps):
        t = named.get(i, {})
        c['comment'] = yy1.clean(t.get('comment', ''))
        if not c['ref']:
            c['ref'] = yy1.clean(t.get('designator', ''))
        tht, shapes = padded.get(i, (False, []))
        c['tht'] = tht
        c['pads'] = len(shapes)
        if shapes:
            xs = [x - w / 2 for x, y, w, h in shapes] + [x + w / 2 for x, y, w, h in shapes]
            ys = [y - h / 2 for x, y, w, h in shapes] + [y + h / 2 for x, y, w, h in shapes]
            c['body'] = (max(xs) - min(xs), max(ys) - min(ys))
        else:
            c['body'] = None
    points = outline(doc.read('Board6/Data') or b'')
    return comps, points


# --- Altium's own pick-and-place export ---------------------------------------

def read_pick_place(path):
    """Altium's pick-and-place CSV: a few banner lines, then a table with
    Designator, Comment, Layer, Footprint, Center-X(mm), Center-Y(mm),
    Rotation (older exports: Mid X / Mid Y, in mm or mil). Returns
    components like read_pcbdoc's, with no pad information."""
    with io.open(path, encoding='utf-8-sig', errors='replace') as handle:
        lines = handle.read().splitlines()
    start = next((i for i, l in enumerate(lines) if 'designator' in l.lower()
                  and ('center-x' in l.lower() or 'mid x' in l.lower())), None)
    if start is None:
        return []
    delim = '\t' if lines[start].count('\t') > lines[start].count(',') else ','
    rows = list(csv.reader(lines[start:], delimiter=delim))
    head = [h.strip().lower() for h in rows[0]]

    def col(*names):
        for n in names:
            for i, h in enumerate(head):
                if h.startswith(n):
                    return i
        return None
    ix, iy = col('center-x', 'mid x'), col('center-y', 'mid y')
    unit = 'mm' if 'mm' in head[ix] else ('mil' if 'mil' in head[ix] else 'mm')
    iref, icom, ilay = col('designator'), col('comment'), col('layer')
    ifp, irot = col('footprint'), col('rotation')
    out = []
    for row in rows[1:]:
        if len(row) <= max(i for i in (ix, iy, iref) if i is not None):
            continue
        try:
            x, y = float(row[ix]), float(row[iy])
        except ValueError:
            continue
        if unit == 'mil':
            x, y = x * MIL, y * MIL
        layer = (row[ilay] if ilay is not None else 'TOP').strip().upper()
        out.append({'ref': yy1.clean(row[iref]),
                    'comment': yy1.clean(row[icom]) if icom is not None else '',
                    'pattern': row[ifp].strip() if ifp is not None else '',
                    'x': x, 'y': y,
                    'rot': float(row[irot] or 0) if irot is not None else 0.0,
                    'layer': 'BOTTOM' if layer.startswith('B') else 'TOP',
                    'description': '', 'libref': '', 'tht': False, 'pads': 1,
                    'body': None})
    return out


# --- the BOM -----------------------------------------------------------------

def read_bom(path):
    """An Altium BOM (xlsx/xls/ods/csv) with Comment and Designator
    columns: {designator: (comment, footprint)}."""
    lower = path.lower()
    if lower.endswith('.ods'):
        table = p2y.rows_from_ods(path)
    elif lower.endswith(('.xlsx', '.xls')):
        table = p2y.rows_from_workbook(path)
    else:
        with io.open(path, encoding='utf-8-sig', errors='replace') as handle:
            table = list(csv.reader(handle))
    out = {}
    head = None
    for row in table:
        cells = [str(c).strip() for c in row]
        lows = [c.lower() for c in cells]
        if head is None:
            if 'designator' in lows and 'comment' in lows:
                head = lows
            continue
        get = lambda name: cells[head.index(name)] if name in head and head.index(name) < len(cells) else ''
        refs = [r.strip() for r in re.split(r'[,;\s]+', get('designator')) if r.strip()]
        for ref in refs:
            out[ref] = (yy1.clean(get('comment')), get('footprint'))
    return out


# --- discovery ----------------------------------------------------------------

def find(folder, patterns):
    hits = []
    for pattern in patterns:
        hits += glob(os.path.join(folder, '**', pattern), recursive=True)
    return sorted(set(hits), key=os.path.getmtime, reverse=True)


def boards_in(folder):
    seen, out = set(), []
    for path in glob(os.path.join(folder, '*.PcbDoc')) + glob(os.path.join(folder, '*.pcbdoc')):
        key = os.path.normcase(path)
        if key not in seen:
            seen.add(key)
            out.append(path)
    return sorted(out, key=os.path.getmtime, reverse=True)


def ref_key(ref):
    """C2 before C10: letters, then the number."""
    m = re.match(r'^([A-Za-z_]*)(\d*)(.*)$', ref)
    return (m.group(1).upper(), int(m.group(2) or 0), m.group(3)) if m else (ref, 0, '')


def pick_places_in(folder):
    out = []
    for path in find(folder, ['*.csv', '*.txt']):
        try:
            with io.open(path, encoding='utf-8-sig', errors='replace') as handle:
                head = handle.read(4000).lower()
        except (IOError, OSError):
            continue
        if 'pick and place' in head or ('designator' in head and 'center-x' in head):
            out.append(path)
    return out


def boms_in(folder):
    out = []
    places = set(os.path.normcase(p) for p in pick_places_in(folder))
    for path in find(folder, ['*.xlsx', '*.xls', '*.ods', '*.csv']):
        name = os.path.basename(path).lower()
        if name.startswith('~$') or 'stackup' in name or os.path.normcase(path) in places:
            continue
        out.append(path)
    return out


# --- the conversion -----------------------------------------------------------

def arguments(argv=None):
    parser = argparse.ArgumentParser(
        description='Convert an Altium Designer board to NeoDen YY1 placement files.')
    parser.add_argument('project', help='project directory, or the .PcbDoc itself')
    parser.add_argument('--board', help='the .PcbDoc (default: newest in the folder)')
    parser.add_argument('--pick-place', help='Altium pick-and-place CSV instead of '
                                             'reading the .PcbDoc')
    parser.add_argument('--bom', help='BOM with Comment and Designator columns '
                                      '(default: found)')
    parser.add_argument('--outdir', help='where to write (default: <project>)')
    parser.add_argument('--prefix', help='output filename prefix')
    parser.add_argument('--profile', help='settings JSON')
    parser.add_argument('--include-tht', action='store_true',
                        help='keep through-hole parts instead of dropping them')
    parser.add_argument('--exclude', default='',
                        help='comma-separated designators to leave out')
    parser.add_argument('--bottom-rot', default='as-is',
                        choices=['as-is', 'negate', 'mirror180'],
                        help='bottom-side rotation convention (default: as-is)')
    return parser.parse_args(argv)


def defaults(project):
    return arguments([project])


def analyse(args, say=print):
    """Read the board (and BOM); return a pads2yy1.Conversion, writing nothing."""
    project = os.path.abspath(args.project)
    board = args.board
    if project.lower().endswith('.pcbdoc') and os.path.isfile(project):
        board, project = project, os.path.dirname(project)
    if not os.path.isdir(project):
        die('%s is not a directory' % project)
    if not board and not args.pick_place:
        found = boards_in(project)
        if not found:
            places = pick_places_in(project)
            if places:
                args.pick_place = places[0]
            else:
                die('no .PcbDoc in %s (and no pick-and-place export either)' % project)
        else:
            board = found[0]
            if len(found) > 1:
                say('note:   %d boards here, using the newest: %s' % (
                    len(found), os.path.basename(board)))
                say('        (others: %s -- pick one with --board or File > '
                    'Open Altium board...)' % ', '.join(os.path.basename(b) for b in found[1:]))

    points = []
    if board:
        comps, points = read_pcbdoc(board)
        say('board   %s' % os.path.relpath(board, project))
        say('        %d component(s), %d outline vertices' % (len(comps), len(points)))
        if args.pick_place:
            comps = read_pick_place(args.pick_place)
            say('placing %s (%d rows) -- positions from the export, outline from '
                'the board' % (os.path.relpath(args.pick_place, project), len(comps)))
    else:
        comps = read_pick_place(args.pick_place)
        say('placing %s (%d rows)' % (os.path.relpath(args.pick_place, project), len(comps)))
        say('WARNING: no .PcbDoc beside it -- no outline (board size estimated) '
            'and no pad data (through-hole parts are not told apart).')
    if not comps:
        die('no components found')

    # the BOM, when there is one, for values the board lacks or disagrees on
    bom, bom_path = {}, args.bom
    if not bom_path:
        for candidate in boms_in(project):
            try:
                found = read_bom(candidate)
            except Exception:
                continue
            if found:
                bom_path, bom = candidate, found
                break
    elif os.path.isfile(bom_path):
        bom = read_bom(bom_path)
    if bom:
        say('bom     %s: %d designator(s)' % (os.path.relpath(bom_path, project), len(bom)))

    prefix = args.prefix or os.path.splitext(os.path.basename(board or args.pick_place))[0]
    profile_path = args.profile or os.path.join(project, '%s_yy1_profile.json' % prefix)
    profile = yy1.Profile.load(profile_path)
    if not profile.board:
        profile.board = prefix

    # the frame: the outline's lower-left corner, or the parts' extent
    xs = [c['x'] for c in comps]
    ys = [c['y'] for c in comps]
    if points:
        min_x, min_y = min(p[0] for p in points), min(p[1] for p in points)
        width = max(p[0] for p in points) - min_x
        height = max(p[1] for p in points) - min_y
    else:
        min_x, min_y = min(xs) - 2.0, min(ys) - 2.0
        width, height = max(xs) - min_x + 2.0, max(ys) - min_y + 2.0
    say('board   %.3f x %.3f mm%s' % (width, height, '' if points else ' (estimated from the parts)'))

    exclude = set(r.strip() for r in (args.exclude or '').split(',') if r.strip())
    sides = {'T': [], 'B': []}
    dropped, no_value, fiducials = [], [], []
    for c in comps:
        ref = c['ref']
        if not ref:
            continue
        side = 'B' if c['layer'].startswith('B') else 'T'
        mm_x, mm_y = c['x'] - min_x, c['y'] - min_y
        if side == 'B':
            mm_x = width - mm_x
        if FIDUCIAL.search(ref) or FIDUCIAL.search(c['pattern']):
            fiducials.append((ref, round(mm_x, 3), round(mm_y, 3), side))
            dropped.append((ref, 'fiducial mark'))
            continue
        if ref in exclude:
            dropped.append((ref, 'excluded on request'))
            continue
        if c['tht'] and not args.include_tht:
            dropped.append((ref, 'through-hole -- the YY1 places SMD only'))
            continue
        if board and not args.pick_place and not c['pads']:
            dropped.append((ref, 'no pads -- graphical footprint, not a part'))
            continue
        value = c['comment']
        if ref in bom and bom[ref][0]:
            value = bom[ref][0]
        if not value:
            value = c['libref'] or ''
            if value:
                no_value.append(ref)
        package = yy1.normalise_package(c['pattern'], profile.packages)
        comment = yy1.normalise_value(value, profile.values)
        base = yy1.norm_rot(c['rot'])
        if side == 'B':
            if args.bottom_rot == 'negate':
                base = yy1.norm_rot(-base)
            elif args.bottom_rot == 'mirror180':
                base = yy1.norm_rot(180.0 - base)
        package, comment = profile.alias(package, comment)
        column = profile.settings_for(package, comment, ref)
        # A known package names its nozzle (manual p.29); otherwise the pads'
        # extent stands in for the body -- an under-estimate for a two-pad
        # chip, which is why the table comes first.
        body = c.get('body')
        nozzle = yy1.nozzle_for_package(package) or (
            yy1.suggest_nozzle(*body) if body and min(body) > 0.2 else '')
        sides[side].append({
            'ref': ref, 'comment': comment, 'package': package,
            'x': round(mm_x, 4), 'y': round(mm_y, 4), 'base_rot': base,
            'rot': yy1.norm_rot(base + profile.rot_offset + column.get('rot_offset', 0.0)),
            'head': column['head'], 'feeder': column['feeder'],
            'speed': column['speed'], 'pick_h': column['pick_h'],
            'place_h': column['place_h'], 'mode': column['mode'],
            'skip': column['skip'], 'nozzle': nozzle,
            'raw_package': c['pattern'], 'raw_value': value,
        })
    for side in sides:
        sides[side].sort(key=lambda p: ref_key(p['ref']))
    say('parts   %d on top, %d on the bottom, %d left out' % (
        len(sides['T']), len(sides['B']), len(dropped)))
    if no_value:
        say('note:   %d part(s) with no Comment -- the library reference stands in: %s'
            % (len(no_value), ', '.join(no_value[:8])))
    blank = sorted(set('%s' % p['package'] for s in sides.values() for p in s if not p['comment']))
    if blank:
        say('WARNING: parts with no value at all on %s -- every one of a footprint '
            'would share a reel; add a BOM with Comment and Designator columns'
            % ', '.join(blank[:6]))

    conv = p2y.Conversion(project=project, prefix=prefix, bom=bom, bom_path=bom_path,
                          asc_path=board, xls_path=args.pick_place, sides=sides,
                          dropped=dropped, fiducials=fiducials, width=width,
                          height=height, outline=bool(points), profile=profile)
    conv.board = board
    return conv


def main():
    args = arguments()
    log = []
    conv = analyse(args, say=lambda m: (log.append(m), print(m)))
    if not conv.placed:
        die('nothing to place')
    return 0 if p2y.write_files(conv, outdir=args.outdir, profile_path=args.profile) else 1


if __name__ == '__main__':
    sys.exit(main())
