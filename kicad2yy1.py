#!/usr/bin/env python3
"""Convert KiCad Fabrication Toolkit output into NeoDen YY1 placement files.

Writes the machine's own format -- the one the YY1 saves back to the SD card,
verified against a file it ran on 2026-09-02:

    Designator,Comment,Footprint,Mid X(mm),Mid Y(mm) ,Rotation,Head ,FeederNo,
    Mount Speed(%),Pick Height(mm),Place Height(mm),Mode,Skip

behind a five-line header carrying panelisation, fiducial and nozzle-change
settings. See yy1lib.py for the format itself.

Writing the native format instead of the generic 11-column layout means the
feeder numbers, recognition modes and pick/place heights arrive in the file
rather than being typed into the touchscreen once per part type. Those live in
a JSON profile beside the board, so a re-export from KiCad keeps them.

Usage:
    ./kicad2yy1.py path/to/kicad_project
    ./kicad2yy1.py path/to/kicad_project --assign-feeders    # number the reels 1..n
    ./kicad2yy1.py path/to/kicad_project --rot-offset 90     # if the board comes out turned
    ./kicad2yy1.py kicad/card-reader --outdir /media/sdcard

Inputs are auto-discovered under the project directory: the .kicad_pcb (for the
board outline and through-hole detection) and production/*_positions.csv plus
production/*_bom.csv (from the Fabrication Toolkit).
"""

import argparse
import csv
import io
import math
import os
import re
import sys
from glob import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yy1lib as yy1


def die(msg):
    sys.exit('error: ' + msg)


def aux_origin(pcb_path):
    """Return the drill/place origin the Fabrication Toolkit exported against.

    KiCad writes position files relative to this origin, with Y negated:
        export_x = kicad_x - ox
        export_y = -(kicad_y - oy)
    When it is unset the export is in absolute page coordinates (origin 0,0).
    """
    text = io.open(pcb_path, encoding='utf-8').read()
    match = re.search(r'\(aux_axis_origin\s+(-?[\d.]+)\s+(-?[\d.]+)\)', text)
    if not match:
        return 0.0, 0.0
    return float(match.group(1)), float(match.group(2))


POINT = re.compile(r'\((?:start|end|mid|center|xy)\s+(-?[\d.]+)\s+(-?[\d.]+)\)')


def blocks(text, tag):
    """Yield each balanced "(tag ...)" s-expression in text.

    Matching parentheses rather than indentation, because indentation and line
    endings vary between KiCad versions and geometry can be nested at any depth
    (a board outline drawn inside a footprint, for one).
    """
    for match in re.finditer(r'\(' + tag + r'[\s(]', text):
        start = match.start()
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '(':
                depth += 1
            elif text[i] == ')':
                depth -= 1
                if depth == 0:
                    yield text[start:i + 1]
                    break


# A fiducial footprint, or a mounting hole -- the manual accepts a round
# hole with contrast as a fiducial (3.2.2 note 1).
FIDUCIAL_NAME = re.compile(r'fid|fiducial|mountinghole', re.I)


def fiducial_marks(pcb_path):
    """Fiducials on the board: [(ref, kicad_x, kicad_y, side), ...].

    A fiducial is a footprint whose reference or footprint name says so
    (FID1, Fiducial_1mm_Mask2mm). They are usually excluded from the position
    file, so they are read straight off the board rather than from the export.
    """
    marks = []
    for ref, block in footprint_blocks(pcb_path):
        name = re.match(r'\(footprint\s+"([^"]*)"', block)
        if not (FIDUCIAL_NAME.search(ref) or
                (name and FIDUCIAL_NAME.search(name.group(1)))):
            continue
        at = re.search(r'\(at\s+(-?[\d.]+)\s+(-?[\d.]+)', block)
        if not at:
            continue
        side = 'B' if re.search(r'\(layer\s+"B\.Cu"\)', block[:400]) else 'T'
        marks.append((ref, float(at.group(1)), float(at.group(2)), side))
    return marks


def footprint_blocks(pcb_path):
    """Yield (reference, block) for every placed footprint."""
    text = io.open(pcb_path, encoding='utf-8').read()
    for block in blocks(text, 'footprint'):
        ref = re.search(r'\(property "Reference" "([^"]+)"', block)
        if ref:
            yield ref.group(1), block


def board_outline(pcb_path):
    """Return (min_x, min_y, max_x, max_y) of Edge.Cuts in KiCad board coords.

    The outline is usually top-level gr_* graphics, but it is also common to
    draw it as fp_* inside a dedicated board-shape footprint, in which case the
    coordinates are local and must be rotated and offset into board space.
    """
    text = io.open(pcb_path, encoding='utf-8').read()
    xs, ys = [], []

    for block in blocks(text, r'gr_\w+'):
        if '(layer "Edge.Cuts")' not in block:
            continue
        for point in POINT.finditer(block):
            xs.append(float(point.group(1)))
            ys.append(float(point.group(2)))

    for block in blocks(text, 'footprint'):
        if '(layer "Edge.Cuts")' not in block:
            continue
        at = re.search(r'\(at\s+(-?[\d.]+)\s+(-?[\d.]+)(?:\s+(-?[\d.]+))?\)', block)
        if not at:
            continue
        ox, oy = float(at.group(1)), float(at.group(2))
        angle = math.radians(-float(at.group(3) or 0.0))
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        for shape in blocks(block, r'fp_\w+'):
            if '(layer "Edge.Cuts")' not in shape:
                continue
            for point in POINT.finditer(shape):
                lx, ly = float(point.group(1)), float(point.group(2))
                xs.append(ox + lx * cos_a - ly * sin_a)
                ys.append(oy + lx * sin_a + ly * cos_a)

    if not xs:
        die('no Edge.Cuts geometry found in %s' % pcb_path)
    return min(xs), min(ys), max(xs), max(ys)


def padless_refs(pcb_path):
    """Footprints with no pads: board outlines, logos, fiducial art, mounting
    holes. They can carry attr "smd" and so reach the position file, but there
    is nothing there to place."""
    return set(ref for ref, block in footprint_blocks(pcb_path)
               if not any(True for _ in blocks(block, 'pad')))


def through_hole_refs(pcb_path):
    """Refs the YY1 cannot place: any footprint with plated thru_hole pads."""
    refs = set()
    for ref, block in footprint_blocks(pcb_path):
        for pad in blocks(block, 'pad'):
            if re.match(r'\(pad\s+"[^"]*"\s+thru_hole\b', pad):
                refs.add(ref)
                break
    return refs


# Machine envelope, from the YY1 specification and manual Table 1-2 (p.29).
MAX_COMPONENT = 18.0    # mm, largest body the heads/nozzles handle (18*18mm)
MIN_COMPONENT = 0.55    # mm, an 0201 is 0.6*0.3 -- nothing smaller is supported
MAX_HEIGHT = 12.0       # mm, not checkable from the PCB (no body height stored)
MAX_PCB_DUAL = (249.0, 350.0)    # both feeder banks installed
MAX_PCB_SINGLE = (315.0, 350.0)  # left-hand feeder bank only

# The nozzle table lives in yy1lib, so the KiCad and PADS front ends suggest
# the same nozzle for the same body.
NOZZLES = yy1.NOZZLES
suggest_nozzle = yy1.suggest_nozzle


def footprint_sizes(pcb_path):
    """Measure each component's body: ref -> (width, height, source).

    F.Fab/B.Fab carries the true body outline, so it is preferred. Courtyard
    includes a clearance margin and pads can be wider or narrower than the
    body, so both are only fallbacks. Coordinates inside a footprint are
    stored unrotated, which is what we want -- size is rotation-invariant.
    """
    sizes = {}

    def extent(body, layer):
        xs, ys = [], []
        for shape in blocks(body, r'fp_\w+'):
            if '(layer "%s")' % layer not in shape:
                continue
            for pos in POINT.finditer(shape):
                xs.append(float(pos.group(1)))
                ys.append(float(pos.group(2)))
        if not xs:
            return None
        return max(xs) - min(xs), max(ys) - min(ys)

    def pad_extent(body):
        xs, ys = [], []
        for pad in blocks(body, 'pad'):
            at = re.search(r'\(at\s+(-?[\d.]+)\s+(-?[\d.]+)', pad)
            size = re.search(r'\(size\s+([\d.]+)\s+([\d.]+)\)', pad)
            if not (at and size):
                continue
            x, y = float(at.group(1)), float(at.group(2))
            w, h = float(size.group(1)), float(size.group(2))
            xs += [x - w / 2, x + w / 2]
            ys += [y - h / 2, y + h / 2]
        if not xs:
            return None
        return max(xs) - min(xs), max(ys) - min(ys)

    for ref, body in footprint_blocks(pcb_path):
        for layer, source in (('F.Fab', 'fab'), ('B.Fab', 'fab'),
                              ('F.CrtYd', 'courtyard'), ('B.CrtYd', 'courtyard')):
            found = extent(body, layer)
            if found:
                break
        else:
            found, source = pad_extent(body), 'pads'
        if found:
            sizes[ref] = (found[0], found[1], source)
    return sizes


def board_size_note(width, height):
    """The YY1 takes 249x350 mm with both feeder banks, 315x350 with one."""
    def fits(limit):
        return ((width <= limit[0] and height <= limit[1]) or
                (width <= limit[1] and height <= limit[0]))
    if fits(MAX_PCB_DUAL):
        return None
    if fits(MAX_PCB_SINGLE):
        return ('note:   board is %.1f x %.1f mm -- exceeds the %gx%g mm '
                'dual-bank limit. Remove the right-hand feeder bank (%gx%g mm).'
                % (width, height, MAX_PCB_DUAL[0], MAX_PCB_DUAL[1],
                   MAX_PCB_SINGLE[0], MAX_PCB_SINGLE[1]))
    return ('WARNING: board is %.1f x %.1f mm, larger than the YY1 can hold '
            '(max %gx%g mm).'
            % (width, height, MAX_PCB_SINGLE[0], MAX_PCB_SINGLE[1]))


def origin_status(ox, oy, min_x, max_y):
    """Enforce the manual's rule: origin on the board's lower-left corner.

    KiCad's Y grows downwards, so the board's lower-left corner is
    (min_x, max_y) in board coordinates. The conversion compensates for any
    origin mathematically, but the manual (p.7) asks for it to be set, and a
    mismatch usually means the export predates a board move -- so say so.

    Returns (ok, message).
    """
    corner_x, corner_y = min_x, max_y
    if abs(ox - corner_x) < 0.05 and abs(oy - corner_y) < 0.05:
        return True, ("origin  OK -- drill/place origin is on the board's "
                      'lower-left corner (%.3f, %.3f)' % (corner_x, corner_y))

    where = 'not set (page origin 0, 0)' if not (ox or oy) else '(%g, %g)' % (ox, oy)
    return False, '\n'.join([
        '',
        'WARNING: the drill/place origin is %s,' % where,
        "         but the manual requires the board's lower-left corner,",
        '         which for this board is (%.3f, %.3f).' % (corner_x, corner_y),
        '',
        '         In KiCad: Place > Drill/Place File Origin, click the',
        '         lower-left corner of the board outline, then re-run the',
        '         Fabrication Toolkit export.',
        '',
        '         This script compensates for the offset either way, so the',
        '         numbers below are correct AS LONG AS the position file was',
        '         exported from the current .kicad_pcb. If the board has been',
        '         moved since, re-export first.',
        ''])


def load_bom(path):
    """Map each designator to its (footprint, value)."""
    footprints, values = {}, {}
    for row in csv.DictReader(io.open(path, encoding='utf-8-sig')):
        for ref in [r.strip() for r in row['Designator'].split(',')]:
            if not ref:
                continue
            footprints[ref] = yy1.clean(row.get('Footprint', ''))
            values[ref] = yy1.clean(row.get('Value', ''))
    return footprints, values


def find_one(pattern, what, variant=None, log=None):
    """Pick one file, preferring --variant, else the most recently written.

    Projects accumulate one export per board revision in production/, so the
    newest is almost always the one meant. The choice is reported, never silent.
    """
    say = log.append if log is not None else (lambda m: None)
    hits = sorted(glob(pattern))
    if variant:
        hits = [h for h in hits if os.path.basename(h).startswith(variant)]
        if not hits:
            die('no %s matches variant %r (pattern %s)' % (what, variant, pattern))
    if not hits:
        die('no %s found matching %s' % (what, pattern))
    if len(hits) > 1:
        hits.sort(key=os.path.getmtime, reverse=True)
        say('note:   %d %ss present, using the newest: %s'
            % (len(hits), what, os.path.basename(hits[0])))
        say('        (others: %s -- select with --variant)'
            % ', '.join(os.path.basename(h) for h in hits[1:]))
    return hits[0]


def board_agreement(pcb_path, positions, bom):
    """How well a board matches an export: parts in the same place, plus
    values that agree. The higher, the likelier this is the board the
    export came from."""
    placed = {}
    for ref, block in footprint_blocks(pcb_path):
        at = re.search(r'\(at\s+(-?[\d.]+)\s+(-?[\d.]+)(?:\s+(-?[\d.]+))?', block)
        value = re.search(r'\(property "Value" "([^"]*)"', block)
        if at:
            placed[ref] = (float(at.group(1)), float(at.group(2)),
                           value.group(1) if value else '')
    score = 0
    try:
        for row in csv.DictReader(io.open(positions, encoding='utf-8-sig')):
            spot = placed.get(row.get('Designator', '').strip())
            if not spot:
                continue
            try:
                x, y = float(row['Mid X']), float(row['Mid Y'])
            except (KeyError, ValueError):
                continue
            # the export writes Y upwards; the board file has it downwards
            if abs(x - spot[0]) < 0.02 and abs(y + spot[1]) < 0.02:
                score += 1
        _, values = load_bom(bom)
        score += sum(1 for ref, v in values.items()
                     if ref in placed and yy1.clean(placed[ref][2]) == v)
    except (IOError, OSError, csv.Error):
        pass
    return score


def pick_board(project, positions, bom, log):
    """The .kicad_pcb a Fabrication Toolkit export came from.

    One board in the folder: that one. Several (a working copy and one
    adjusted for the placement machine, say): the one whose parts sit
    where the export says and whose values agree with its BOM, the
    export's own name and then the newest file breaking a tie. The choice
    is always reported.
    """
    boards = sorted(glob(os.path.join(project, '*.kicad_pcb')))
    if not boards:
        die('no .kicad_pcb found in %s' % project)
    if len(boards) == 1:
        return boards[0]
    stem = os.path.basename(positions)[:-len('positions.csv')].rstrip('_')
    scored = []
    for path in boards:
        name = os.path.splitext(os.path.basename(path))[0]
        scored.append((board_agreement(path, positions, bom), name == stem,
                       os.path.getmtime(path), path))
    scored.sort(reverse=True)
    best = scored[0]
    log.append('board   %d boards in the folder; using %s -- it matches the export best'
               % (len(boards), os.path.basename(best[3])))
    log.append('        (%s)' % ', '.join('%s: %d' % (os.path.basename(s[3]), s[0])
                                          for s in scored))
    if len(scored) > 1 and scored[1][0] == best[0]:
        log.append('        two boards match equally well -- open the .kicad_pcb '
                   'itself to choose (File > Open KiCad board…)')
    return best[3]


def default_profile_path(project, prefix):
    return os.path.join(project, '%s_yy1_profile.json' % prefix)


class Result(object):
    """Everything a run produced, so the GUI and the CLI can share the work."""

    def __init__(self):
        self.pcb = self.positions = self.bom = self.prefix = ''
        self.project = self.outdir = ''
        self.width = self.height = 0.0
        self.origin = (0.0, 0.0)
        self.origin_ok = True
        self.sides = {'T': [], 'B': []}
        self.nozzle_of = {}      # ref -> suggested nozzle
        self.body_of = {}        # ref -> (w, h, source)
        self.dropped = []        # (ref, why)
        self.fiducials = []      # (ref, x_mm, y_mm, side), in the file's frame
        self.stale = []
        self.unknown = []
        self.unmeasured = []
        self.off_board = []
        self.log = []
        self.profile = None
        self.profile_path = ''

    def parts(self, side='T'):
        return self.sides.get(side, [])

    def type_rows(self, side='T'):
        """Group placements the way the machine does: Comment + Footprint."""
        groups = {}
        for part in self.sides.get(side, []):
            key = yy1.type_key(part['package'], part['comment'])
            groups.setdefault(key, []).append(part)
        return groups


def analyse(args, log=None):
    """Read the KiCad export and build YY1 part rows. No files are written."""
    result = Result()
    result.log = log if log is not None else []
    say = result.log.append

    project = os.path.abspath(args.project)
    # A .kicad_pcb given as the project names the board outright.
    if project.lower().endswith('.kicad_pcb') and os.path.isfile(project):
        if not args.pcb:
            args.pcb = project
        project = os.path.dirname(project)
    result.project = project
    # The Fabrication Toolkit names its files <archive name>_positions.csv,
    # or plain positions.csv when no archive name is set.
    positions = args.positions or find_one(
        os.path.join(project, 'production', '*positions.csv'), 'positions CSV',
        args.variant, log=result.log)
    base = os.path.basename(positions)
    stem = base[:-len('positions.csv')].rstrip('_')
    # Pair the BOM to the chosen positions file rather than globbing again,
    # so a multi-revision production/ can't mix parts from different exports.
    bom = args.bom or os.path.join(os.path.dirname(positions),
                                   (stem + '_bom.csv') if stem else 'bom.csv')
    if not os.path.isfile(bom):
        die('no BOM alongside %s (expected %s)'
            % (os.path.basename(positions), os.path.basename(bom)))
    pcb = args.pcb or pick_board(project, positions, bom, result.log)
    # --prefix only renames the output; with no archive name the board's
    # own name stands in.
    prefix = args.prefix or stem or os.path.splitext(os.path.basename(pcb))[0]
    result.pcb, result.positions, result.bom, result.prefix = pcb, positions, bom, prefix
    result.outdir = args.outdir or os.path.join(project, 'production')

    profile_path = args.profile or default_profile_path(project, prefix)
    profile = yy1.Profile.load(profile_path)
    if not profile.board:
        profile.board = prefix
    if getattr(args, 'rot_offset', None) is not None:
        profile.rot_offset = float(args.rot_offset)
    if getattr(args, 'order', None):
        profile.order = args.order
    result.profile, result.profile_path = profile, profile_path

    min_x, min_y, max_x, max_y = board_outline(pcb)
    width, height = max_x - min_x, max_y - min_y
    ox, oy = aux_origin(pcb)
    result.width, result.height, result.origin = width, height, (ox, oy)
    result.origin_ok, origin_msg = origin_status(ox, oy, min_x, max_y)

    # Undo the export's own origin to get back to KiCad board coords, then
    # measure from the board's lower-left corner (which is KiCad's max_y,
    # since KiCad's Y grows downwards):
    #     kicad_x = export_x + ox        kicad_y = oy - export_y
    #     yy1_x   = kicad_x - min_x      yy1_y   = max_y - kicad_y
    # Collapsed into a single shift per axis:
    shift_x = ox - min_x
    shift_y = max_y - oy

    # Fiducials, in the same frame as the parts, so one can be dropped into
    # the job header as-is. Bottom-side ones mirror like bottom-side parts.
    for ref, kx, ky, fside in fiducial_marks(pcb):
        fx, fy = kx - min_x, max_y - ky
        if fside == 'B':
            fx = width - fx
        result.fiducials.append((ref, round(fx, 3), round(fy, 3), fside))
    # The bottom side is measured from the lower-RIGHT corner, so X mirrors.

    footprints, values = load_bom(bom)
    skip_tht = set() if args.include_tht else through_hole_refs(pcb)
    skip_manual = set(r.strip() for r in (args.exclude or '').split(',') if r.strip())
    only = set(r.strip() for r in (args.only or '').split(',') if r.strip())
    sizes = footprint_sizes(pcb)
    padless = padless_refs(pcb)
    known = set(sizes) | padless
    raw_names = getattr(args, 'raw_names', False)

    note = board_size_note(width, height)
    if note:
        say(note)
    say(origin_msg)

    for row in csv.DictReader(io.open(positions, encoding='utf-8-sig')):
        ref = row['Designator'].strip()
        if only and ref not in only:
            continue
        if ref in skip_manual:
            result.dropped.append((ref, 'excluded on request'))
            continue
        if ref in skip_tht:
            result.dropped.append((ref, 'through-hole -- the YY1 places SMD only'))
            continue
        if ref in padless:
            result.dropped.append((ref, 'no pads -- graphical footprint, not a part'))
            continue

        # Size envelope. Parts outside it cannot be picked at all, so they are
        # dropped rather than left for the machine to fail on.
        size = sizes.get(ref)
        if size and not args.no_size_check:
            w, h, source = size
            big = max(w, h)
            if big > args.max_size:
                result.dropped.append(
                    (ref, 'body %.1f x %.1f mm exceeds the %g mm limit'
                     % (w, h, args.max_size)))
                continue
            if big < args.min_size:
                result.dropped.append(
                    (ref, 'body %.2f x %.2f mm is smaller than an 0201' % (w, h)))
                continue
            result.nozzle_of[ref] = suggest_nozzle(w, h)
            result.body_of[ref] = (w, h, source)
            if source != 'fab':
                result.unmeasured.append((ref, source))
        elif not args.no_size_check and ref not in known:
            # In the position file but not on the board at all: the export
            # predates a change. Placing it would put a part at a coordinate
            # that no longer means anything, so drop it and say why.
            result.stale.append(ref)
            continue

        if ref not in footprints:
            result.unknown.append(ref)

        side = 'T' if row['Layer'].strip().lower() == 'top' else 'B'
        x = float(row['Mid X']) + shift_x
        if side == 'B':
            x = width - x
        y = float(row['Mid Y']) + shift_y

        rot = yy1.norm_rot(row['Rotation'])
        if side == 'B':
            if args.bottom_rot == 'negate':
                rot = yy1.norm_rot(-rot)
            elif args.bottom_rot == 'mirror180':
                rot = yy1.norm_rot(180.0 - rot)

        if not (0 <= x <= width and 0 <= y <= height):
            result.off_board.append((ref, x, y))

        raw_fp, raw_val = footprints.get(ref, ''), values.get(ref, '')
        package = raw_fp if raw_names else \
            yy1.normalise_package(raw_fp, profile.packages)
        comment = raw_val if raw_names else \
            yy1.normalise_value(raw_val, profile.values)

        package, comment = profile.alias(package, comment)
        column = profile.settings_for(package, comment, ref)

        result.sides[side].append({
            # Full precision is kept here; the writers round on the way out.
            # base_rot is what the board says; rot adds the profile's offsets
            # and is recomputed whenever those change.
            'ref': ref, 'comment': comment, 'package': package,
            'x': x, 'y': y, 'base_rot': rot,
            'rot': yy1.norm_rot(rot + profile.rot_offset
                                + column.get('rot_offset', 0.0)),
            'head': column['head'], 'feeder': column['feeder'],
            'speed': column['speed'], 'pick_h': column['pick_h'],
            'place_h': column['place_h'], 'mode': column['mode'],
            'skip': column['skip'],
            'raw_package': raw_fp, 'raw_value': raw_val,
            'nozzle': result.nozzle_of.get(ref, ''),
        })

    for side in ('T', 'B'):
        result.sides[side].sort(key=lambda p: p['ref'])
    return result


def ordered(result, side):
    """The parts of one side in placement order -- which is file order."""
    nozzle_of = dict((p['ref'],
                      result.profile.nozzle_for(
                          yy1.type_key(p['package'], p['comment']),
                          p.get('nozzle', '')))
                     for p in result.sides[side])
    return yy1.order_parts(result.sides[side], result.profile, nozzle_of, side)


def refresh_columns(result):
    """Re-apply the profile to already-analysed parts (after a GUI edit)."""
    profile = result.profile
    for side in ('T', 'B'):
        for part in result.sides[side]:
            column = profile.settings_for(part['package'], part['comment'],
                                          part['ref'])
            part.update(head=column['head'], feeder=column['feeder'],
                        speed=column['speed'], pick_h=column['pick_h'],
                        place_h=column['place_h'], mode=column['mode'],
                        skip=column['skip'],
                        rot=yy1.norm_rot(part.get('base_rot', part['rot'])
                                         + profile.rot_offset
                                         + column.get('rot_offset', 0.0)))
    return result


def write_files(result, fmt='yy1', outdir=None):
    """Write one file per populated side. Returns [(path, count), ...]."""
    outdir = outdir or result.outdir
    if not os.path.isdir(outdir):
        os.makedirs(outdir)
    written = []
    for side, label in (('T', 'top'), ('B', 'bottom')):
        if not result.sides[side]:
            continue
        parts = ordered(result, side)
        # With "leave skipped parts out" set, the file carries only what is
        # placed; the sheet still gets everything, to list the rest by hand.
        rows = yy1.file_parts(parts, result.profile)
        path = os.path.join(outdir, '%s_yy1_%s.csv' % (result.prefix, label))
        if fmt == 'legacy':
            yy1.write_legacy(path, rows, layer=side)
        else:
            # Each side gets its own header, because its nozzle plan is its own.
            yy1.write(path, rows, result.profile.job_for(side))
        written.append((path, len(rows)))
        # The operator's sheet: what goes in which slot, and the nozzle plan.
        yy1.write_setup_sheet(path, parts, result.profile, side,
                              (result.width, result.height), result.prefix)
    # The file the machine reads has no column for nozzles, feeder homes or
    # reel settings -- those live in the profile. Put a copy beside the CSV,
    # so a card that comes back from the machine opens with the same state.
    if written and getattr(result, 'profile_path', ''):
        beside = os.path.join(outdir, os.path.basename(result.profile_path))
        if os.path.abspath(beside) != os.path.abspath(result.profile_path):
            result.profile.save(beside)
    return written


def report(result, written):
    out = list(result.log)
    # A PADS or CSV-opened board has no .kicad_pcb; the report must not fail
    # on that, because the profile save in the window comes after it.
    if result.pcb:
        out.append('pcb     %s' % os.path.relpath(result.pcb, result.project))
    out.append('board   %.3f x %.3f mm   export origin %s'
               % (result.width, result.height,
                  '(%g, %g)' % result.origin if any(result.origin) else 'page (0, 0)'))
    if result.profile.rot_offset:
        out.append('rot     global offset %+g deg applied to every part'
                   % result.profile.rot_offset)
    out.append('order   %s -- the order the machine places them in'
               % result.profile.order)
    for path, count in written:
        out.append('wrote   %s  (%d parts)' % (path, count))

    for side, label in (('T', 'top'), ('B', 'bottom')):
        groups = result.type_rows(side)
        if not groups:
            continue
        unassigned = [k for k, v in groups.items() if not v[0]['feeder']]
        out.append('%-7s %d part types across %d placements%s'
                   % (label, len(groups), sum(len(v) for v in groups.values()),
                      '' if not unassigned else
                      '  (%d with no feeder -- run --assign-feeders)'
                      % len(unassigned)))
        if len(groups) > yy1.TAPE_LAST:
            out.append('WARNING: %d types but only %d tape feeder slots (1-%d); '
                       'the rest need the flexible or bulk banks.'
                       % (len(groups), yy1.TAPE_LAST, yy1.TAPE_LAST))

    for ref, why in sorted(result.dropped):
        out.append('skipped %-6s %s' % (ref, why))

    nozzles = {}
    for ref, model in result.nozzle_of.items():
        nozzles.setdefault(model, []).append(ref)
    if nozzles:
        out.append('nozzles suggested (manual Table 1-2, advisory):')
        for model in sorted(nozzles):
            refs = sorted(nozzles[model])
            shown = ', '.join(refs[:8]) + (' ...' if len(refs) > 8 else '')
            out.append('        %-6s %2d %-5s %s'
                       % (model, len(refs),
                          'part' if len(refs) == 1 else 'parts', shown))
    if result.stale:
        out += ['',
                'WARNING: %d part(s) in the position file are not on the board: %s'
                % (len(result.stale), ', '.join(sorted(result.stale))),
                '         This export is older than the .kicad_pcb. They have been',
                '         dropped -- re-run the Fabrication Toolkit export so the',
                '         placement file matches the current board.', '']
    if result.unmeasured:
        out.append('warning: body size not read from F.Fab for %d part(s), used a '
                   'fallback: %s'
                   % (len(result.unmeasured),
                      ', '.join('%s (%s)' % u for u in result.unmeasured[:6])))
    if result.unknown:
        out.append('warning: not in BOM, footprint/comment left blank: %s'
                   % ', '.join(sorted(result.unknown)))
    for ref, x, y in result.off_board:
        out.append('warning: %s at (%.3f, %.3f) falls outside the board outline'
                   % (ref, x, y))
    if result.sides['B']:
        refs = ', '.join(sorted(p['ref'] for p in result.sides['B'])[:6])
        out.append('note:   bottom-side rotation convention is unverified. '
                   'Step %s before running the board.' % refs)
    return out


def plan_nozzles(result):
    """Assign nozzles to the heads and station, and schedule the changes.

    The head holds two nozzles and the station more, so four types run
    uninterrupted (manual 4.5.2). Anything past two has to be fetched, which
    only works if the file is grouped by nozzle -- a change happens before one
    component number, so each nozzle needs one unbroken run.
    """
    profile = result.profile
    for side in ('T', 'B'):
        if not result.sides[side]:
            continue
        counts = {}
        for part in result.sides[side]:
            key = yy1.type_key(part['package'], part['comment'])
            nozzle = profile.nozzle_for(key, part.get('nozzle', ''))
            if nozzle:
                counts[nozzle] = counts.get(nozzle, 0) + 1
        if not counts:
            continue
        homes, unplaced = yy1.auto_homes(sorted(counts.items()),
                                         profile.station_positions)
        profile.set_homes(side, homes)
        profile.order = 'nozzle'

        parts = ordered(result, side)
        nozzle_of = dict((p['ref'],
                          profile.nozzle_for(
                              yy1.type_key(p['package'], p['comment']),
                              p.get('nozzle', '')))
                         for p in parts)
        changes, problems = yy1.plan_nozzle_changes(
            yy1.nozzle_runs(yy1.file_parts(parts, profile), nozzle_of), homes,
            profile.station_positions)
        profile.set_changes(side, changes)

        result.log.append('nozzle  %s side: %s' % (
            'top' if side == 'T' else 'bottom', ', '.join(
            '%s %s' % (n, 'head %d' % h[1] if h[0] == 'head'
                       else 'station %d' % h[1])
            for n, h in sorted(homes.items()))))
        for _, before, head, drop, pick in changes:
            result.log.append('        before #%d (%s) park head %d in station '
                              '%d, fit %s from station %d'
                              % (before, parts[before - 1]['ref'], head, drop,
                                 nozzle_of.get(parts[before - 1]['ref'], '?'),
                                 pick))
        if not changes:
            result.log.append('        no change needed -- everything fits on '
                              'the two heads')
        for line in unplaced:
            result.log.append('WARNING: no room for %s; four nozzle types is '
                              'the limit for one uninterrupted run' % line)
        for line in problems:
            result.log.append('WARNING: %s' % line)


def convert(args):
    result = analyse(args)

    if not result.origin_ok and not args.origin_ok:
        print('\n'.join(result.log))
        if not sys.stdin.isatty():
            die('refusing to run non-interactively with an unset origin; '
                'fix it in KiCad or pass --origin-ok')
        if input('Continue anyway? [y/N] ').strip().lower() not in ('y', 'yes'):
            die('aborted -- set the origin in KiCad and re-export')
        result.log = []

    if args.assign_feeders:
        for side in ('T', 'B'):
            groups = result.type_rows(side)
            if not groups:
                continue
            # Number in placement order so neighbouring reels hold parts that
            # get placed together -- shorter head travel per board.
            keys = sorted(groups, key=lambda k: groups[k][0]['ref'])
            result.profile.assign_feeders(keys, overwrite=args.renumber)
        refresh_columns(result)

    if args.plan_nozzles:
        plan_nozzles(result)

    if args.setup:
        # The whole decision in one go -- what the window's Set up board does.
        for side in ('T', 'B'):
            if result.sides[side]:
                result.log.append('setup   %s side' % ('top' if side == 'T' else 'bottom'))
                result.log += ['        ' + line for line in
                               yy1.setup_side(result.sides[side], result.profile,
                                              side, renumber=args.renumber)]
        refresh_columns(result)

    if args.save_profile or args.assign_feeders or args.plan_nozzles or \
            args.order or args.setup:
        result.profile.save(result.profile_path)
        result.log.append('profile %s' % result.profile_path)

    written = write_files(result, fmt=args.format, outdir=args.outdir)
    print('\n'.join(report(result, written)))
    return 0


def main():
    parser = argparse.ArgumentParser(
        description='Convert KiCad Fabrication Toolkit output to NeoDen YY1 files.')
    parser.add_argument('project', nargs='?', default='.',
                        help='KiCad project directory (default: current)')
    parser.add_argument('--pcb', help='override .kicad_pcb path')
    parser.add_argument('--positions', help='override positions CSV path')
    parser.add_argument('--bom', help='override BOM CSV path')
    parser.add_argument('--outdir', help='output directory (default: <project>/production)')
    parser.add_argument('--prefix', help='output filename prefix')
    parser.add_argument('--variant',
                        help='pick a specific export by filename prefix when '
                             'production/ holds several board revisions')
    parser.add_argument('--exclude', default='',
                        help='comma-separated designators to leave out')
    parser.add_argument('--only', default='', metavar='REFS',
                        help='keep ONLY these designators -- for a trial run '
                             'of one part before committing to a full board')
    parser.add_argument('--origin-ok', action='store_true',
                        help='skip the drill/place origin confirmation prompt')
    parser.add_argument('--include-tht', action='store_true',
                        help='keep through-hole parts instead of dropping them')
    parser.add_argument('--max-size', type=float, default=MAX_COMPONENT,
                        metavar='MM',
                        help='largest placeable body (default %g mm)' % MAX_COMPONENT)
    parser.add_argument('--min-size', type=float, default=MIN_COMPONENT,
                        metavar='MM',
                        help='smallest placeable body (default %g mm, an 0201)'
                             % MIN_COMPONENT)
    parser.add_argument('--no-size-check', action='store_true',
                        help='keep parts regardless of body size')
    parser.add_argument('--bottom-rot', default='as-is',
                        choices=['as-is', 'negate', 'mirror180'],
                        help='bottom-side rotation convention (default: as-is)')

    group = parser.add_argument_group('YY1 file')
    group.add_argument('--format', default='yy1', choices=['yy1', 'legacy'],
                       help="'yy1' (default) writes the machine's own 13-column "
                            "format with feeders and modes; 'legacy' writes the "
                            'generic 11-column layout the YY1 converts on load')
    group.add_argument('--profile', metavar='JSON',
                       help='feeder/rotation/column settings (default: '
                            '<project>/<prefix>_yy1_profile.json)')
    group.add_argument('--rot-offset', type=float, metavar='DEG',
                       help='add this to every rotation -- the constant to set '
                            'if a test board comes out turned')
    group.add_argument('--assign-feeders', action='store_true',
                       help='number each part type into a tape feeder slot '
                            '(1-52) and save it to the profile')
    group.add_argument('--renumber', action='store_true',
                       help='with --assign-feeders, renumber types that already '
                            'have a slot instead of keeping them')
    group.add_argument('--save-profile', action='store_true',
                       help='write the profile back even without other changes')
    group.add_argument('--order', choices=yy1.ORDERS + ['custom'],
                       help='placement order, which is the file order the '
                            'machine works down: ref (default), feeder, '
                            'nozzle, custom')
    group.add_argument('--plan-nozzles', action='store_true',
                       help='assign nozzles to the heads and station, group '
                            'the file by nozzle and write the changes into '
                            'the job header')
    group.add_argument('--setup', action='store_true',
                       help='decide everything at once: nozzles trimmed to the '
                            'four that run uninterrupted, file grouped by '
                            'nozzle, feeders numbered, changes written')
    group.add_argument('--raw-names', action='store_true',
                       help='keep full KiCad footprint and BOM value strings '
                            'instead of shortening them to package names')
    return convert(parser.parse_args())


if __name__ == '__main__':
    sys.exit(main())
