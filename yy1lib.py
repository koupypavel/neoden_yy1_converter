#!/usr/bin/env python3
"""The NeoDen YY1's own placement-file format, plus the job profile.

Everything here is pinned to `reference/LIN2LIN_yy1_top_YY1_rot0.csv`, a file the YY1
accepted and ran on 2026-09-02. That file is the specification; the writer
reproduces it byte for byte, trailing spaces in the header included.

    NEODEN,YY1,P&P FILE,,,,,,,,,,,          <- 14 fields
    ,,,,,,,,,,,,,
    PanelizedPCB,UnitLength,0,UnitWidth,0,Rows,1,Columns,1,
    ,,,,,,,,,,,,,
    Fiducial,1-X,0.00,1-Y,0.00,OverallOffsetX,0,OverallOffsetY,0,
    ,,,,,,,,,,,,,
    NozzleChange,OFF,BeforeComponent,1,Head1,Drop,Station2,PickUp,Station1,
    ... four of them ...
    ,,,,,,,,,,,,,
    Designator,Comment,Footprint,Mid X(mm),Mid Y(mm) ,Rotation,Head ,FeederNo,
    Mount Speed(%),Pick Height(mm),Place Height(mm),Mode,Skip

Note the column order: Comment comes BEFORE Footprint, the opposite of the
11-column "generic EDA" layout the machine auto-converts on load. Rotation is
0..360 here, not -180..180. Lines are CRLF. There is no Layer column -- one
file per board side, and the side lives in the filename.

The machine groups parts into feeder types by Comment + Footprint (manual
3.2.1 note 3), so that pair is the key everything in the profile hangs off.
"""

import io
import json
import os
import re

# --- the format ------------------------------------------------------------

# The trailing spaces in "Mid Y(mm) " and "Head " are the machine's, not typos.
COLUMNS = ['Designator', 'Comment', 'Footprint', 'Mid X(mm)', 'Mid Y(mm) ',
           'Rotation', 'Head ', 'FeederNo', 'Mount Speed(%)', 'Pick Height(mm)',
           'Place Height(mm)', 'Mode', 'Skip']

# Internal field names, in column order.
FIELDS = ['ref', 'comment', 'package', 'x', 'y', 'rot', 'head', 'feeder',
          'speed', 'pick_h', 'place_h', 'mode', 'skip']

BLANK = ',' * 13          # the 14-field spacer row between sections
NEWLINE = '\r\n'

# Recognition modes, manual 3.2.1 note 4.
MODES = {0: 'Null', 1: 'Camera', 2: 'Vacuum', 3: 'Camera+Vacuum',
         4: 'Big IC Camera'}

# Feeder number ranges, manual 3.4.1-3.4.3.
FEEDER_BANKS = [(1, 52, 'tape reel'), (53, 80, 'flexible / tray / strip'),
                (81, 99, 'bulk')]
TAPE_FIRST, TAPE_LAST = 1, 52

# Head 0 lets the machine choose; 1 and 2 pin it.
HEADS = {0: 'auto', 1: 'Head 1', 2: 'Head 2'}

# Nozzle changing, manual 4.5.2. The head carries two nozzles and the station
# holds more, "so it can support four different nozzle types at the same time
# in an uninterrupted placement process". The file has room for four changes.
HEAD_SLOTS = 2
STATION_POSITIONS = 3     # the machine's own defaults reference Station3
MAX_NOZZLE_CHANGES = 4

# Nozzle by the body's minor dimension, manual Table 1-2 (p.29).
NOZZLE_TABLE = [
    ('CN030', '0201'), ('CN040', '0402'), ('CN065', '0402, 0603'),
    ('CN100', '0805, 1206, 1210, diodes'),
    ('CN140', '1206, 1210, 1812, 2010, SOT-23, 5050'),
    ('CN220', 'SOP ICs, SOT-89, SOT-223, SOT-252'),
    ('CN400', 'ICs 5 to 12 mm'), ('CN750', 'ICs over 12 mm'),
]


# Body size by package name, from the suction-surface table in manual p.29.
# The KiCad front end measures the real body off F.Fab; PADS stores no body
# outline, so there the package name is all there is to go on.
PACKAGE_SIZE = {
    '0201': (0.6, 0.3), '0402': (1.0, 0.5), '0603': (1.6, 0.8),
    '0805': (2.0, 1.2), '1206': (3.2, 1.6), '1210': (3.2, 2.5),
    '1812': (4.5, 3.2), '2010': (5.0, 2.5), '2512': (6.4, 3.2),
    'SMA': (4.0, 2.5), 'SMB': (4.0, 3.3), 'SMC': (6.6, 5.6),
    'SOT23': (3.0, 1.4), 'SOT-23': (3.0, 1.4), 'SOT89': (4.5, 2.5),
    'SOT-89': (4.5, 2.5), 'SOT223': (6.4, 3.7), 'SOT-223': (6.4, 3.7),
    'TO252': (6.5, 6.3), 'TO-252': (6.5, 6.3),
    # Not in the table, but the same idea -- a nozzle has to seal on the body.
    'SOD-123': (3.7, 1.8), 'SOD123': (3.7, 1.8), 'SMF': (3.7, 1.8),
    'MELF': (3.6, 1.4), 'SOD80C': (3.6, 1.4),
    # Tantalum EIA case codes A-D (3216, 3528, 6032, 7343), and the PADS
    # library's CTS* names for them.
    '3216': (3.2, 1.6), '3528': (3.5, 2.8), '6032': (6.0, 3.2), '7343': (7.3, 4.3),
    'CTSA': (3.2, 1.6), 'CTSB': (3.5, 2.8), 'CTSC': (6.0, 3.2), 'CTSD': (7.3, 4.3),
    'CTSBA': (3.5, 2.8), 'CTSCA': (6.0, 3.2),
}

# Suggested nozzle by the body's minor dimension, after manual Table 1-2.
# Advisory only -- the head holds two nozzles and the station two more.
NOZZLES = [(0.4, 'CN030'), (0.7, 'CN040'), (1.1, 'CN065'), (1.8, 'CN100'),
           (3.5, 'CN140'), (5.0, 'CN220')]


def suggest_nozzle(width, height):
    """Nozzle for a body of this size, per manual Table 1-2."""
    minor, major = min(width, height), max(width, height)
    for limit, model in NOZZLES:
        if minor < limit:
            return model
    return 'CN400' if major < 12.0 else 'CN750'


def nozzle_for_package(name):
    """Nozzle from a package name alone, when no body outline is available.

    Returns '' rather than a guess when the name is not a package the manual
    lists -- a wrong nozzle is worse than none, because the change plan is
    built on these.
    """
    key = clean(name).upper().replace(' ', '')
    size = PACKAGE_SIZE.get(key)
    if not size:
        # SOIC-8, SOIC-16, SO8M1 and friends: pitch and pin count put every
        # small-outline IC in the SOP row of the table.
        if re.match(r'^(SOIC|SO|SOP|TSSOP|MSOP|SSOP)[-_]?\d', key):
            return 'CN220'
        if re.match(r'^(QFP|TQFP|LQFP|QFN|BGA|DFN|SON)[-_]?\d', key):
            return 'CN400'
        return ''
    return suggest_nozzle(*size)


def plan_nozzle_changes(runs, homes, positions=STATION_POSITIONS):
    """Turn a nozzle-grouped placement order into NozzleChange rows.

    `runs` is [(nozzle, first component number), ...] in the order the machine
    will place them, numbered from 1. `homes` says where each nozzle starts:
    ('head', 1 or 2) or ('station', position).

    Returns (changes, problems). A change is the tuple the Job wants:
    (True, before_component, head, drop_position, pickup_position).

    Two rules shape this. The head only carries two nozzles, so a third has to
    be fetched from the station -- and the manual's caution in 4.5.2 note 3 is
    that the position you drop the old nozzle into must be empty, or the head
    collides with the nozzle already sitting there. So the planner tracks what
    is in every position and refuses a swap it cannot make safely.
    """
    heads = {1: None, 2: None}
    parked = {}
    problems = []
    for nozzle, home in homes.items():
        where, slot = home
        if where == 'head':
            # Slot 0 means both heads carry it -- the usual way to run a
            # board that is nearly all one size of part at full speed.
            for h in ((1, 2) if int(slot) == 0 else (int(slot),)):
                heads[h] = nozzle
        else:
            parked[int(slot)] = nozzle

    changes = []
    for index, (nozzle, before) in enumerate(runs):
        if nozzle in heads.values():
            continue
        slot = next((s for s, n in parked.items() if n == nozzle), None)
        if slot is None:
            problems.append('%s is needed at component %d but is neither on a '
                            'head nor in the station' % (nozzle, before))
            continue

        # Evict whichever fitted nozzle is needed furthest in the future --
        # the one whose removal costs the fewest changes later.
        def next_use(fitted):
            for later, (n, _) in enumerate(runs[index + 1:]):
                if n == fitted:
                    return later
            return len(runs) + 1
        head = max(heads, key=lambda h: next_use(heads[h]))

        vacant = [s for s in range(1, positions + 1) if s not in parked]
        if not vacant:
            problems.append('no empty nozzle-station position to drop %s into '
                            'before component %d -- leave one position free '
                            '(manual 4.5.2 note 3)' % (heads[head], before))
            continue
        drop = vacant[0]

        changes.append((True, before, head, drop, slot))
        parked[drop] = heads[head]
        del parked[slot]
        heads[head] = nozzle

    if len(changes) > MAX_NOZZLE_CHANGES:
        problems.append('%d changes needed but the file holds only %d -- run '
                        'the board in two passes, or put the odd parts on one '
                        'nozzle' % (len(changes), MAX_NOZZLE_CHANGES))
    return changes[:MAX_NOZZLE_CHANGES], problems


def remap_to_available(counts, available, max_steps=3):
    """Send every nozzle you do not own to the nearest smaller one you do.

    `available` is {nozzle: how many you have}. A smaller tip still picks a
    bigger body, so CN100 work goes to CN065 when that is what is in the
    drawer; up to `max_steps` sizes down, which is as far as a tip can
    reasonably be asked to reach. Returns (mapping, notes, unpickable) --
    unpickable lists nozzles whose parts nothing you own can pick.
    """
    owned = [n for n in NOZZLE_ORDER if available.get(n, 0) > 0]
    mapping, notes, unpickable = {}, [], []
    for nozzle, count in counts.items():
        if available.get(nozzle, 0) > 0 or nozzle not in NOZZLE_ORDER:
            mapping[nozzle] = nozzle
            continue
        at = NOZZLE_ORDER.index(nozzle)
        smaller = [n for n in owned if at - max_steps <= NOZZLE_ORDER.index(n) < at]
        if smaller:
            target = smaller[-1]          # the closest size down
            mapping[nozzle] = target
            notes.append('%s parts on %s -- you have no %s (%d placement%s)'
                         % (nozzle, target, nozzle, count, '' if count == 1 else 's'))
        else:
            unpickable.append(nozzle)
    return mapping, notes, unpickable


def auto_homes(nozzles, positions=STATION_POSITIONS, available=None):
    """Put the two busiest nozzles on the heads and park the rest.

    `nozzles` is [(name, placement count), ...]. One station position is left
    empty on purpose: without somewhere to drop the outgoing nozzle, no change
    can happen at all.
    """
    ranked = [n for n, _ in sorted(nozzles, key=lambda x: (-x[1], x[0]))]
    homes = {}
    def have(nozzle):
        return available.get(nozzle, 99) if available else 99

    if len(ranked) == 1:
        # One nozzle: both heads carry it -- if you own two of them.
        homes[ranked[0]] = ('head', 0 if have(ranked[0]) >= 2 else 1)
        return homes, []
    if len(ranked) >= 2 and have(ranked[0]) >= 2 and \
            available and sum(1 for n in ranked if have(n) > 0) <= positions:
        # With a known drawer, the busiest nozzle on both heads and the rest
        # parked usually beats one nozzle per head: every part of the big
        # group can go to either head at full speed.
        homes[ranked[0]] = ('head', 0)
        for i, nozzle in enumerate(ranked[1:]):
            if i + 1 > positions - 1:
                break
            homes[nozzle] = ('station', i + 1)
        return homes, [n for n in ranked if n not in homes]
    for i, nozzle in enumerate(ranked[:HEAD_SLOTS]):
        homes[nozzle] = ('head', i + 1)
    for i, nozzle in enumerate(ranked[HEAD_SLOTS:]):
        if i + 1 > positions - 1:      # keep the last position free to drop into
            break
        homes[nozzle] = ('station', i + 1)
    unplaced = [n for n in ranked if n not in homes]
    return homes, unplaced


def order_parts(parts, profile=None, nozzle_of=None, side='T'):
    """Return the parts in the order the machine will place them.

    The YY1 works straight down the file, so this is the placement order. It
    decides how far the head travels between picks, and -- because a nozzle
    change happens "before component N" -- whether one change can cover a whole
    group of parts or has to be repeated.

        ref      designator order, as the machine-verified file was written
        feeder   reel by reel, so the head works one feeder at a time
        nozzle   one contiguous run per nozzle: what a change plan needs
        custom   the order you dragged into place, kept in the profile
    """
    how = getattr(profile, 'order', 'ref') or 'ref'
    nozzle_of = nozzle_of or {}

    if how == 'custom' and getattr(profile, 'sequence', None):
        rank = dict((ref, i) for i, ref in enumerate(profile.sequence))
        # Parts added since the order was saved go to the end, in ref order.
        return sorted(parts, key=lambda p: (rank.get(p['ref'], len(rank)),
                                            p['ref']))
    if how == 'feeder':
        return sorted(parts, key=lambda p: (int(p['feeder']), p['ref']))
    if how == 'nozzle':
        counts = {}
        for part in parts:
            counts[nozzle_of.get(part['ref'], '')] =                 counts.get(nozzle_of.get(part['ref'], ''), 0) + 1
        homes = profile.homes_for(side) if profile else {}

        def group_rank(nozzle):
            # Whatever starts on a head is placed first, so the run only has
            # to break once per nozzle that has to be fetched.
            home = homes.get(nozzle)
            if home:
                where, slot = home[0], int(home[1])
                return (0 if where == 'head' else 1, slot, nozzle)
            return (0 if not homes else 2, -counts.get(nozzle, 0), nozzle)

        return sorted(parts, key=lambda p: (group_rank(nozzle_of.get(p['ref'], '')),
                                            int(p['feeder']), p['ref']))
    return sorted(parts, key=lambda p: p['ref'])


def file_parts(parts, profile):
    """The rows the placement file will carry, in order.

    Everything, unless the profile says to leave skipped parts out -- then
    only what is placed. Component numbers (nozzle changes) count these.
    """
    if not profile or not getattr(profile, 'omit_skipped', False):
        return list(parts)
    return [p for p in parts
            if not profile.settings_for(p['package'], p['comment'], p['ref'])['skip']]


def nozzle_runs(parts, nozzle_of):
    """Contiguous nozzle runs in an ordered part list.

    Returns [(nozzle, first component number), ...], numbered from 1 the way
    the machine counts them for "BeforeComponent".
    """
    runs, previous = [], None
    for index, part in enumerate(parts, start=1):
        nozzle = nozzle_of.get(part['ref'], '')
        if not nozzle:
            continue        # no nozzle set: it neither starts nor breaks a run
        if nozzle != previous:
            runs.append((nozzle, index))
            previous = nozzle
    return runs


# Nozzles from smallest tip to largest, manual Table 1-2.
# Every nozzle the machine ships with: the regular tips in size order, then
# the YX specials (manual Table 1-1). What the window offers in its lists.
NOZZLE_CHOICES = ['CN030', 'CN040', 'CN065', 'CN100', 'CN140', 'CN220',
                  'CN400', 'CN750', 'YX01', 'YX02', 'YX03', 'YX04', 'YX05',
                  'YX06']

NOZZLE_ORDER = ['CN030', 'CN040', 'CN065', 'CN100', 'CN140', 'CN220',
                'CN400', 'CN750']


def consolidate_nozzles(counts, limit, max_steps=1):
    """Trim the set of nozzles in use down to `limit`.

    `counts` is {nozzle: placements}. Returns (mapping, notes): mapping sends
    each nozzle to the one that will actually be used, notes say what moved.

    The physics only allows one direction. A smaller tip seals on a bigger
    body; a bigger tip overhangs a smaller body and picks nothing. So parts
    can be moved onto the next smaller nozzle already in use, never a larger
    one, and only `max_steps` sizes down -- a CN140 doing 1206 work is what
    the manual's own table lists, a CN065 on a QFP is not. The least-used
    nozzle is moved first, so the fewest parts pick with a compromise tip.
    Whatever still does not fit is left for the caller to skip.
    """
    mapping = dict((n, n) for n in counts)
    live = dict((n, c) for n, c in counts.items() if n in NOZZLE_ORDER)
    notes = []
    while len(live) > limit:
        moved = False
        # Fewest placements first: the cheapest compromise.
        for nozzle in sorted(live, key=lambda n: (live[n], -NOZZLE_ORDER.index(n))):
            at = NOZZLE_ORDER.index(nozzle)
            for step in range(1, max_steps + 1):
                if at - step < 0:
                    break
                smaller = NOZZLE_ORDER[at - step]
                if smaller in live:
                    live[smaller] += live.pop(nozzle)
                    for source, target in mapping.items():
                        if target == nozzle:
                            mapping[source] = smaller
                    notes.append('%s parts moved onto %s (%d placement%s)'
                                 % (nozzle, smaller, counts[nozzle],
                                    '' if counts[nozzle] == 1 else 's'))
                    moved = True
                    break
            if moved:
                break
        if not moved:
            break
    return mapping, notes


def side_changes(ordered, profile, side):
    """The nozzle changes for one side as its file will be written.

    `ordered` is the side's parts in placement order. Skipped parts neither
    start nor break a nozzle run; with "skipped parts out of the CSV" they
    are not counted in the component numbers either. Returns (changes,
    problems, file rows, {ref: nozzle}).
    """
    nozzle_of = {}
    for part in ordered:
        settings = profile.settings_for(part['package'], part['comment'], part['ref'])
        nozzle_of[part['ref']] = '' if settings['skip'] else profile.nozzle_for(
            type_key(part['package'], part['comment']), part.get('nozzle', ''))
    rows = file_parts(ordered, profile)
    changes, problems = plan_nozzle_changes(nozzle_runs(rows, nozzle_of),
                                            profile.homes_for(side),
                                            profile.station_positions)
    return changes, problems, rows, nozzle_of


def setup_side(parts, profile, side, renumber=False, available=None,
               library=None):
    """Everything the machine needs decided for one side, in one call.

    Nozzle per reel, trimmed to the four types that run uninterrupted;
    where each nozzle starts; the file grouped by nozzle; feeder slots in
    that order; and the change rows. Returns a report as a list of lines,
    with 'WARNING:' on anything that needs a human.

    Nothing here is irreversible -- it all lands in the profile, which is
    not saved until you say so.
    """
    report = []
    positions = profile.station_positions
    limit = HEAD_SLOTS + positions - 1
    profile.order = 'nozzle'

    # 1. one nozzle per reel
    groups = {}
    for part in parts:
        groups.setdefault(type_key(part['package'], part['comment']), []).append(part)
    counts, nozzle_of_type = {}, {}
    for key, members in groups.items():
        nozzle = profile.nozzle_for(key, members[0].get('nozzle', ''))
        nozzle_of_type[key] = nozzle
        if nozzle:
            counts[nozzle] = counts.get(nozzle, 0) + len(members)
    unknown = [k for k, n in nozzle_of_type.items() if not n]
    if unknown:
        report.append('WARNING: no nozzle for %d reel(s) -- unknown package. '
                      'Set one on the Feeders tab: %s'
                      % (len(unknown), ', '.join(k.replace('|', ' ')
                                                 for k in sorted(unknown)[:6])))

    # 2. no more nozzle types than the machine can hold at once
    # 2a. only nozzles you own. Parts nothing you own can pick are skipped.
    if available:
        owned_map, owned_notes, unpickable = remap_to_available(counts, available)
        report.append('drawer   %s' % ', '.join(
            '%s x%d' % (n, c) for n, c in sorted(available.items(),
                                                 key=lambda x: NOZZLE_ORDER.index(x[0])
                                                 if x[0] in NOZZLE_ORDER else 99)
            if c > 0))
        for line in owned_notes:
            report.append('         ' + line)
        for key, nozzle in nozzle_of_type.items():
            target = owned_map.get(nozzle, nozzle)
            if target != nozzle:
                profile.set_type(key, 'nozzle', target)
                nozzle_of_type[key] = target
        skipped_unpickable = []
        for nozzle in unpickable:
            for key, n in nozzle_of_type.items():
                if n == nozzle:
                    profile.set_type(key, 'skip', 1)
                    skipped_unpickable += [p['ref'] for p in groups[key]]
        if skipped_unpickable:
            report.append('WARNING: nothing you own picks %s parts -- %d set to '
                          'Skip: %s' % (', '.join(unpickable), len(skipped_unpickable),
                                        ', '.join(sorted(skipped_unpickable)[:8])))
        counts = {}
        for key, nozzle in nozzle_of_type.items():
            if nozzle and not profile.types.get(key, {}).get('skip'):
                counts[nozzle] = counts.get(nozzle, 0) + len(groups[key])
        # the drawer also caps how many types can be loaded at once
        limit = min(limit, sum(1 for c in available.values() if c > 0))

    mapping, notes = consolidate_nozzles(counts, limit)
    for key, nozzle in nozzle_of_type.items():
        target = mapping.get(nozzle, nozzle)
        if target != nozzle:
            profile.set_type(key, 'nozzle', target)
            nozzle_of_type[key] = target
    used = {}
    for key, nozzle in nozzle_of_type.items():
        if nozzle:
            used[nozzle] = used.get(nozzle, 0) + len(groups[key])
    report.append('nozzles  %s' % ', '.join(
        '%s x%d' % (n, c) for n, c in sorted(used.items(),
                                             key=lambda x: NOZZLE_ORDER.index(x[0])
                                             if x[0] in NOZZLE_ORDER else 99)))
    for line in notes:
        report.append('         ' + line)

    # 3. where each nozzle starts; whatever still has no home is a second pass.
    # Homes already chosen by hand are kept as long as they cover every
    # nozzle in use -- Set up board must not undo a deliberate "CN065 on both
    # heads".
    existing = profile.homes_for(side)
    if existing and all(n in existing for n in used):
        homes = dict((n, h) for n, h in existing.items() if n in used)
        unplaced = []
        report.append('         nozzle homes kept as set by hand')
    else:
        homes, unplaced = auto_homes(sorted(used.items()), positions, available)
    profile.set_homes(side, homes)
    skipped = []
    for nozzle in unplaced:
        for key, n in nozzle_of_type.items():
            if n == nozzle:
                profile.set_type(key, 'skip', 1)
                skipped += [p['ref'] for p in groups[key]]
    if skipped:
        report.append('WARNING: %s still would not fit -- %d part(s) set to Skip '
                      'for a second pass: %s'
                      % (', '.join(unplaced), len(skipped),
                         ', '.join(sorted(skipped)[:8])))
    for nozzle, home in sorted(homes.items(), key=lambda h: (h[1][0] != 'head', h[1][1])):
        where = ('both heads' if int(home[1]) == 0 else 'head %d' % int(home[1])) \
            if home[0] == 'head' else 'station %d' % int(home[1])
        report.append('         %-6s starts on %s' % (nozzle, where))

    # 4. file grouped by nozzle, reels numbered in that order
    nozzle_of = dict((p['ref'], nozzle_of_type[type_key(p['package'], p['comment'])])
                     for p in parts)
    ordered = order_parts(parts, profile, nozzle_of, side)
    keys, seen = [], set()
    for part in ordered:
        key = type_key(part['package'], part['comment'])
        if key not in seen:
            seen.add(key)
            keys.append(key)
    if library:
        # Reels the machine already has keep their slots; new ones go into
        # the list's free positions of the right tape width.
        apply_library(keys, groups, profile, library, report)
        assigned = [(k, profile.types.get(k, {}).get('feeder', 0)) for k in keys]
    else:
        assigned = profile.assign_feeders(keys, overwrite=renumber)
    beyond = [(k, n) for k, n in assigned if n > TAPE_LAST]
    report.append('feeders  %d reel(s) in slots %d-%d'
                  % (len(assigned), min(n for _, n in assigned),
                     max(n for _, n in assigned)))
    if beyond:
        report.append('WARNING: %d reel(s) past tape slot %d go in the flexible '
                      'bank (53-80): %s'
                      % (len(beyond), TAPE_LAST,
                         ', '.join(k.replace('|', ' ') for k, _ in beyond[:6])))

    # 5. the changes
    ordered = order_parts(parts, profile, nozzle_of, side)
    changes, problems, rows, nozzle_of = side_changes(ordered, profile, side)
    profile.set_changes(side, changes)
    if changes:
        report.append('changes  %d' % len(changes))
        for _, before, head, drop, pick in changes:
            report.append('         before #%d: head %d parks in station %d, '
                          'takes %s from station %d'
                          % (before, head, drop,
                             nozzle_of.get(rows[before - 1]['ref'], '?'), pick))
    else:
        report.append('changes  none needed')
    for line in problems:
        report.append('WARNING: ' + line)
    return report


# --- the feeder library: reels that stay on the machine ------------------

# Tape width the machine's feeders take for a package, in mm. Used only to
# check a match and to pick a free slot of the right width; unknown packages
# get no check rather than a wrong one.
TAPE_WIDTH = {
    8: ['0201', '0402', '0603', '0805', '1206', 'SOT23', 'SOT-23', 'SOT-23-6',
        'CTSA', 'CTSB', 'CTSBA', '3216', '3528',
        'SOT23-6', 'SOT-23-5', 'SOT23-5', 'SOD-123', 'SOD123', 'SMF', 'MELF',
        'SOD80C', 'SOD-323', 'SOD323', 'SC70', 'SOT-363'],
    12: ['1210', '1812', '2010', '2512', 'SMA', 'SMB', 'SOIC-8', 'SO8', 'SO8M1',
         'SO8NB', 'SOIC8', 'SOT-223', 'SOT223', 'SOT89', 'SOT-89',
         'CTSC', 'CTSCA', 'CTSD', '6032',
         '7343', 'MSOP', 'TSSOP-8', 'TSSOP-14', 'TSSOP-16', 'TSSOP-20',
         'SOIC-14', 'SOIC-16', 'SO14', 'SO16', 'SO14WM', 'SOD106A', 'SOD106AB',
         'SMAF'],
    16: ['SMC', 'DPAK', 'TO252', 'TO-252', 'SOT-252', 'QFP-32', 'QFP32',
         'SOIC-16W', 'SOIC-18', 'SOIC-20', 'SOIC-24', 'SOIC-28'],
    24: ['QFP44', 'QFP-44', 'QFP-64', 'QFP64', 'QFP100', 'QFP-100', 'TQFP-64'],
}
_WIDTH_OF = dict((p.upper(), w) for w, ps in TAPE_WIDTH.items() for p in ps)


def tape_width(package):
    """Tape width in mm for a package name, or None when not known."""
    name = clean(package).upper()
    if name in _WIDTH_OF:
        return _WIDTH_OF[name]
    m = re.match(r'^(?:SOIC|SO|SOP|TSSOP|MSOP|SSOP)[-_]?(\d+)', name)
    if m:
        pins = int(m.group(1))
        return 12 if pins <= 16 else (16 if pins <= 28 else 24)
    if re.match(r'^(?:T|L)?QFP[-_]?(\d+)', name):
        return 24
    return None


_SI = {'P': 1e-12, 'N': 1e-9, 'U': 1e-6, 'µ': 1e-6, 'M': 1e-3, 'K': 1e3,
       'R': 1.0, 'F': 1.0, 'H': 1.0}


def canonical_value(text):
    """Turn a value however it is spelt into something two lists can agree on.

    4K7, 4.7K, 4k7 and 4700 are one resistor; 100nF, 100n and 0.1uF one
    capacitor; 10uF16V and 10uF/16V the same reel. Returns
    (kind, number, extra): kind is 'R', 'C', 'L' or 'X' (a part number),
    number the value in base units for R/C/L, extra a voltage rating when
    one was written, or the cleaned name for 'X'.
    """
    t = clean(text).upper().replace(' ', '').replace('Ω', 'R').replace('OHM', 'R')
    t = t.replace('µ', 'U')
    if not t:
        return ('', 0.0, '')
    volts = None
    m = re.search(r'/?(\d+(?:\.\d+)?)V$', t)
    if m:
        volts = float(m.group(1))
        t = t[:m.start()].rstrip('/')
    # 4K7 / 2N2 / 4U7: the letter is the decimal point
    m = re.match(r'^(\d+)([RKMPNU])(\d+)$', t)
    if m:
        number = float('%s.%s' % (m.group(1), m.group(3))) * _SI[m.group(2)]
        letter = m.group(2)
    else:
        m = re.match(r'^(\d+(?:\.\d+)?)([RKMPNU]?)([FH]?)$', t)
        if not m:
            return ('X', 0.0, re.sub(r'[^A-Z0-9]', '', t))
        number = float(m.group(1)) * (_SI[m.group(2)] if m.group(2) else 1.0)
        letter = m.group(2) or (m.group(3) or 'R')
    tail = t[-1]
    if tail == 'F' or letter in 'PNU' and tail != 'H':
        kind = 'C'
    elif tail == 'H':
        kind = 'L'
    else:
        kind = 'R'
    return (kind, number, volts)


def values_match(a, b):
    """0 no match, 1 probable (part-number prefix, or a voltage missing on one
    side), 2 exact."""
    ka, na, xa = canonical_value(a)
    kb, nb, xb = canonical_value(b)
    if not ka or not kb or ka != kb:
        return 0
    if ka == 'X':
        if xa == xb:
            return 2
        if len(xa) >= 5 and len(xb) >= 5 and (xa.startswith(xb) or xb.startswith(xa)):
            return 1
        return 0
    if abs(na - nb) > 1e-9 * max(abs(na), abs(nb), 1e-30) and abs(na - nb) > 1e-15:
        return 0
    if xa is not None and xb is not None and xa != xb:
        return 0
    return 2 if (xa is None) == (xb is None) else 1


BACKUP_DIR = 'feeder_list_backups'
BACKUPS_KEPT = 40


def backup_file(path):
    """Before a feeder list is overwritten, keep the old one:
    <folder>/feeder_list_backups/<name>_<date-time><ext>. The newest forty of
    each list are kept. Returns the copy's path, or None when there was
    nothing to keep."""
    import shutil
    import time
    if not path or not os.path.isfile(path):
        return None
    folder = os.path.join(os.path.dirname(os.path.abspath(path)), BACKUP_DIR)
    base, ext = os.path.splitext(os.path.basename(path))
    try:
        if not os.path.isdir(folder):
            os.makedirs(folder)
        stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(os.path.getmtime(path)))
        target = os.path.join(folder, '%s_%s%s' % (base, stamp, ext))
        if not os.path.exists(target):
            shutil.copy2(path, target)
        mine = sorted(backups_of(path), reverse=True)
        for old in mine[BACKUPS_KEPT:]:
            os.remove(old)
        return target
    except (IOError, OSError):
        return None


def backups_of(path):
    """Earlier copies of a feeder list, oldest first."""
    import glob as _glob
    folder = os.path.join(os.path.dirname(os.path.abspath(path)), BACKUP_DIR)
    base, ext = os.path.splitext(os.path.basename(path))
    return sorted(p for p in _glob.glob(os.path.join(folder, base + '_*' + ext))
                  if re.match(re.escape(base) + r'_\d{8}-\d{6}' + re.escape(ext) + '$',
                              os.path.basename(p)))


class FeederLibrary(object):
    """The reels that stay loaded on the machine: slot -> (width, value).

    From a spreadsheet with columns slot, tape width and value (a package
    column is optional). Slots with no value are free positions.
    """

    def __init__(self, rows=None, path=''):
        self.path = path
        self.rows = []              # [slot, width, value, package]
        for row in rows or []:
            try:
                slot = int(float(str(row[0]).strip()))
            except (ValueError, TypeError, IndexError):
                continue
            try:
                width = int(float(str(row[1]).strip())) if len(row) > 1 and str(row[1]).strip() else 0
            except ValueError:
                width = 0
            value = clean(row[2]) if len(row) > 2 else ''
            package = clean(row[3]) if len(row) > 3 else ''
            self.rows.append([slot, width, value, package])
        self.rows.sort(key=lambda r: r[0])

    @classmethod
    def read(cls, path):
        import pads2yy1
        lower = path.lower()
        if lower.endswith('.ods'):
            table = pads2yy1.rows_from_ods(path)
        elif lower.endswith(('.xlsx', '.xls')):
            table = pads2yy1.rows_from_workbook(path)
        else:
            import csv
            with io.open(path, encoding='utf-8-sig') as handle:
                table = [[c.strip() for c in r] for r in csv.reader(handle)]
        rows = [r for r in table if r and re.match(r'^\d+(\.0)?$', str(r[0]).strip())]
        return cls(rows, path)

    def loaded(self):
        return [r for r in self.rows if r[2]]

    def slot(self, number):
        for row in self.rows:
            if row[0] == number:
                return row
        return None

    def set_value(self, number, value, width=None, package=''):
        """Put a reel in a slot (creating the slot if the list lacked it)."""
        row = self.slot(number)
        if row is None:
            row = [number, width or 0, '', '']
            self.rows.append(row)
            self.rows.sort(key=lambda r: r[0])
        row[2] = clean(value)
        if width:
            row[1] = int(width)
        if package:
            row[3] = clean(package)

    def clear(self, number):
        row = self.slot(number)
        if row is not None:
            row[2], row[3] = '', ''

    def save_csv(self, path):
        """Write the list back as a plain CSV: slot, width, value, package."""
        import csv
        backup_file(path)
        with io.open(path, 'w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['slot', 'tape width mm', 'value', 'package'])
            for slot, width, value, package in self.rows:
                writer.writerow([slot, width or '', value, package])
        self.path = path
        return path

    def free(self, width=None):
        return [r for r in self.rows if not r[2] and (width in (None, 0) or r[1] in (0, width))]

    def save_ods(self, path):
        """Write the list as a spreadsheet like the one it was read from:
        Feeder, Feeder width, Value, Footprint, highest slot first."""
        backup_file(path)
        def footprint(p):
            # a spreadsheet that dropped the zero off 0805 gets it back
            return '0' + p if p.isdigit() and len(p) == 3 else p
        rows = [[slot, width or '', value, footprint(package)]
                for slot, width, value, package in sorted(self.rows, key=lambda r: -r[0])]
        write_ods(path, ['Feeder', 'Feeder width', 'Value', 'Footprint'], rows)
        return path

    def completed(self, reels):
        """A copy of this list with a board's reels written into their slots.

        `reels` is {slot: (package, value)} for every reel the board puts in
        a slot. A slot the list already has with the same reel keeps its
        entry (gaining the footprint if it lacked one); any other slot takes
        the board's reel. The result is what the machine will hold once the
        loading sheet is done.
        """
        out = FeederLibrary([list(r) for r in self.rows], self.path)
        for slot, (package, value) in sorted(reels.items()):
            row = out.slot(slot)
            same = (row and row[2] and values_match(row[2], value)
                    and (not row[3] or packages_match(row[3], package)))
            if same:
                if not row[3]:
                    row[3] = clean(package)
                continue
            out.set_value(slot, value,
                          (row[1] if row and row[1] else 0) or tape_width(package) or 0,
                          package)
        return out

    def match(self, package, value):
        """Best slot for a reel: (slot, quality, note) or None.

        Exact value wins; a probable match (BAT54 ~ BAT54S, or a voltage
        written on one side only) is offered with a note. A package column,
        when the list has one, has to agree; the tape width is checked
        against the board's package and mismatches are noted, not refused.
        """
        best = None
        for slot, width, lib_value, lib_package in self.loaded():
            quality = values_match(lib_value, value)
            if not quality:
                continue
            if lib_package and not packages_match(lib_package, package):
                continue
            expected = tape_width(package)
            note = ''
            if expected and width and expected != width:
                note = '%s is %d mm tape, slot %d is %d mm' % (package, expected, slot, width)
                quality = min(quality, 1)
            if quality == 1 and not note:
                note = 'probable: list says %s' % lib_value
            if best is None or quality > best[1]:
                best = (slot, quality, note)
        return best

    def near_misses(self, package, value):
        """Slots whose reel has this value but another footprint:
        [(slot, lib_package), ...]. Worth a line in the report, since the
        reel may still be the right one."""
        out = []
        for slot, width, lib_value, lib_package in self.loaded():
            if (values_match(lib_value, value) and lib_package
                    and not packages_match(lib_package, package)):
                out.append((slot, lib_package))
        return out


def package_family(name):
    """A footprint name boiled down to what a reel cares about.

    805 (a spreadsheet dropped the zero) is 0805; SOT-23-3, SOT23 and
    SOT-23 are one family; SOIC8, SO8, SOP-8 and SO8M1 all start SO8.
    """
    n = re.sub(r'[^A-Z0-9]', '', normalise_package(name).upper())
    if n.isdigit() and len(n) == 3:
        n = '0' + n
    n = re.sub(r'^(SOIC|SOP|SO)(\d+)', lambda m: 'SO' + m.group(2), n)
    return n


def packages_match(a, b):
    """Two footprint names as the same package, spelling and variant
    suffixes aside: 805 and 0805, SOT-23-3 and SOT23, SOIC8 and SO8M1.
    CTSA and CTSD stay apart."""
    fa, fb = package_family(a), package_family(b)
    if not fa or not fb:
        return True
    if fa == fb:
        return True
    short, long_ = sorted((fa, fb), key=len)
    return len(short) >= 3 and long_.startswith(short)


def apply_library(keys, groups, profile, library, report, retry_skipped=False,
                  list_only=False):
    """Feeders from the library first; free slots of the right width for
    the rest; whatever has no slot is set to Skip and named.

    With retry_skipped, a reel that was skipped for want of a slot is
    un-skipped when a slot turns up for it now. With list_only, nothing goes
    into a free slot: a reel the list lacks is set to Skip -- a run that
    places only what is already on the machine."""
    matched, new, manual = [], [], []
    matched_keys = []
    # New reels go into free tape slots only. 53-80 are stick, tray and
    # strip feeders: a slot there is set up for one part by hand, never
    # handed a tape reel because it happens to be empty on the list.
    free = {} if list_only else dict((r[0], r[1]) for r in library.free()
                                     if TAPE_FIRST <= r[0] <= TAPE_LAST)
    # A slot on the machine holds one reel. When two reels on the board both
    # match it -- "10k" on 0805 and "10k" on 0603 -- the better match takes it
    # and the other is treated as new, with a note saying why; if they really
    # are one reel, that is what the cross-footprint merge is for.
    claims = []
    for key in keys:
        package, value = key.split('|', 1)
        hit = library.match(package, value)
        claims.append((key, hit))
    taken = {}
    for key, hit in sorted(claims, key=lambda c: -(c[1][1] if c[1] else 0)):
        if hit and hit[0] not in taken:
            taken[hit[0]] = key
    contested = {}
    for key in keys:
        hit = dict(claims)[key]
        if hit and taken.get(hit[0]) == key:
            slot, quality, note = hit
            profile.set_type(key, 'feeder', slot)
            profile.set_type(key, 'feeder_source', 'library')
            if profile.types[key].get('skip') and retry_skipped:
                profile.set_type(key, 'skip', 0)
            matched.append((key, slot, note))
            matched_keys.append((key, slot))
            continue
        if hit:
            contested[key] = (hit[0], taken[hit[0]])
        package, value = key.split('|', 1)
        width = tape_width(package) or 0
        candidates = [s for s, w in sorted(free.items()) if w in (0, width) or not width]
        if candidates:
            slot = candidates[0]
            del free[slot]
            profile.set_type(key, 'feeder', slot)
            profile.set_type(key, 'feeder_source', 'new')
            if profile.types[key].get('skip') and retry_skipped:
                profile.set_type(key, 'skip', 0)
            new.append((key, slot, width))
        else:
            # 'noslot' marks a reel skipped only for want of a slot, so a
            # later match (after freeing one) may pick it up again;
            # 'notlisted' one skipped because the list has no such reel.
            profile.set_type(key, 'skip', 1)
            profile.set_type(key, 'feeder_source', 'notlisted' if list_only else 'noslot')
            manual.append(key)
    report.append('library  %s: %d reel(s) already on the machine'
                  % (os.path.basename(library.path) or 'feeder list', len(matched)))
    for key, slot, note in matched:
        if note:
            report.append('         slot %-3d %-22s %s' % (slot, key.replace('|', ' '), note))
    for key, (slot, winner) in contested.items():
        report.append('         %-22s matches slot %d, but that reel is %s -- '
                      'merge them if it is the same reel'
                      % (key.replace('|', ' '), slot, winner.replace('|', ' ')))
    for key in keys:
        if key in dict(matched_keys):
            continue
        package, value = key.split('|', 1)
        for slot, lib_package in library.near_misses(package, value):
            report.append('         %-22s slot %d holds %s but as %s, not %s -- '
                          'right-click the slot to put it there if it is the same reel'
                          % (key.replace('|', ' '), slot, value, lib_package, package))
    if new:
        report.append('         %d reel(s) to load into free slots: %s'
                      % (len(new), ', '.join('%d <- %s' % (s, k.replace('|', ' '))
                                              for k, s, _ in new)))
    if manual and list_only:
        report.append('WARNING: %d reel(s) not on the list -- set to Skip, place by '
                      'hand: %s'
                      % (len(manual), ', '.join(k.replace('|', ' ') for k in manual)))
    elif manual:
        report.append('WARNING: no free slot for %d reel(s) -- set to Skip, place by '
                      'hand or free a slot: %s'
                      % (len(manual), ', '.join(k.replace('|', ' ') for k in manual)))
    return matched, new, manual


SHEET_CSS = (
    '<style>body{font:13px/1.35 Segoe UI,Arial,sans-serif;margin:24px;color:#111;'
    'background:#fff}'
    'h1{font-size:20px;margin:0 0 4px}h2{font-size:14px;margin:18px 0 6px;'
    'border-bottom:1px solid #999;padding-bottom:2px}table{border-collapse:collapse;'
    'width:100%}th,td{border:1px solid #bbb;padding:3px 6px;text-align:left;'
    'vertical-align:top}th{background:#eee}td.n,th.n{text-align:right}'
    '.muted{color:#666}.warn{color:#b00020;font-weight:bold}'
    '.box{text-align:center;width:2.2em}.box input{width:18px;height:18px;'
    'cursor:pointer;margin:0}tr.done td{background:#e8f5e9}'
    'ul.check{list-style:none;padding-left:4px}ul.check li{margin:4px 0}'
    'ul.check input{width:18px;height:18px;vertical-align:middle;margin-right:8px}'
    '.tools{float:right;font-size:12px}.tools a{margin-left:12px}'
    '@media print{.tools{display:none}}'
    'tr.skip td{color:#888;text-decoration:line-through}'
    '@media print{body{margin:10mm}h2{page-break-after:avoid}tr{page-break-inside:avoid}}'
    '</style>')

SHEET_SCRIPT = (
    '<script>(function(){'
    'var key="yy1sheet:"+location.pathname;'
    'var saved={};try{saved=JSON.parse(localStorage.getItem(key)||"{}")}catch(e){}'
    'var boxes=document.querySelectorAll("input[type=checkbox][data-key]");'
    'function paint(b){var tr=b.closest("tr");if(tr){tr.classList.toggle("done",b.checked)}}'
    'boxes.forEach(function(b){b.checked=!!saved[b.dataset.key];paint(b);'
    'b.addEventListener("change",function(){saved[b.dataset.key]=b.checked;paint(b);'
    'try{localStorage.setItem(key,JSON.stringify(saved))}catch(e){}})});'
    'var tools=document.createElement("div");tools.className="tools";'
    'tools.innerHTML=\'<a href="#" id="yy1print">print</a>\'+'
    '\'<a href="#" id="yy1reset">clear all ticks</a>\';'
    'document.body.insertBefore(tools,document.body.firstChild);'
    'document.getElementById("yy1print").onclick=function(ev){ev.preventDefault();window.print()};'
    'document.getElementById("yy1reset").onclick=function(ev){ev.preventDefault();'
    'if(!confirm("Clear every tick on this sheet?"))return;saved={};'
    'try{localStorage.removeItem(key)}catch(e){}boxes.forEach(function(b){b.checked=false;paint(b)})};'
    '})();</script>')


def setup_sheet(parts, profile, side, board=(0, 0), title='', csv_name=''):
    """The operator's sheet: what to load where, and the nozzle plan.

    `parts` must be in file order -- the component numbers in the change
    list count down the file the machine reads. Returns (text, html): the
    text prints on anything and reads in a terminal, the HTML has tick boxes
    and page-break styling for the bench.
    """
    import datetime
    import html as H

    side_name = 'top' if side == 'T' else 'bottom'
    stamp = datetime.date.today().isoformat()
    homes = profile.homes_for(side)
    changes = profile.changes_for(side) or []

    # reels, keyed the way the machine groups them
    reels = {}
    for index, part in enumerate(parts, start=1):
        key = type_key(part['package'], part['comment'])
        reels.setdefault(key, {'parts': [], 'first': index})['parts'].append(part)
    rows = []
    for key, reel in reels.items():
        first = reel['parts'][0]
        settings = profile.settings_for(first['package'], first['comment'])
        skipped = [p['ref'] for p in reel['parts']
                   if profile.settings_for(p['package'], p['comment'], p['ref'])['skip']]
        rows.append({
            'slot': settings['feeder'], 'package': first['package'],
            'value': first['comment'], 'qty': len(reel['parts']),
            'nozzle': profile.nozzle_for(key, first.get('nozzle', '')),
            'head': settings['head'], 'speed': settings['speed'],
            'mode': settings['mode'], 'skip': settings['skip'],
            'refs': [p['ref'] for p in reel['parts']], 'skipped': skipped,
            'first': reel['first'],
            # 'library' = already on the machine, 'new' = to be loaded
            'source': profile.types.get(key, {}).get('feeder_source', ''),
        })
    rows.sort(key=lambda r: (r['skip'], r['slot'], r['first']))

    nozzle_count = {}
    for row in rows:
        if row['nozzle'] and not row['skip']:
            nozzle_count[row['nozzle']] = nozzle_count.get(row['nozzle'], 0) + row['qty']
    positions = profile.station_positions
    station = {}
    heads = {}
    for nozzle, (where, slot) in homes.items():
        if where == 'head':
            for hd in ((1, 2) if int(slot) == 0 else (int(slot),)):
                heads[hd] = nozzle
        else:
            station[int(slot)] = nozzle

    by_number = dict((i, p) for i, p in enumerate(file_parts(parts, profile), start=1))
    placed = [p for p in parts
              if not profile.settings_for(p['package'], p['comment'], p['ref'])['skip']]
    skipped_all = [p['ref'] for p in parts if p not in placed]
    unassigned = [r for r in rows if not r['skip'] and
                  'feeder' not in profile.types.get(
                      type_key(r['package'], r['value']), {})]
    beyond = [r for r in rows if not r['skip'] and r['slot'] > TAPE_LAST]

    # ---------------------------------------------------------------- text
    t = []
    head_line = 'NeoDen YY1 setup sheet -- %s, %s side' % (title or profile.board, side_name)
    t += [head_line, '=' * len(head_line)]
    t.append('board %.1f x %.1f mm   %d placements on %d reels   file %s   %s'
             % (board[0], board[1], len(placed), len([r for r in rows if not r['skip']]),
                csv_name, stamp))
    job = profile.job
    if job.fid_x or job.fid_y:
        t.append('fiducial 1 at %.3f, %.3f mm -- the machine finds it first '
                 'when you press Step' % (float(job.fid_x), float(job.fid_y)))
    if job.offset_x or job.offset_y:
        t.append('overall offset %s, %s mm added to every placement'
                 % (num(job.offset_x), num(job.offset_y)))
    t.append('')
    t.append('NOZZLES')
    def count_of(nozzle):
        n = nozzle_count.get(nozzle)
        return '' if not n else '(%d part%s)' % (n, '' if n == 1 else 's')

    for h in (1, 2):
        nozzle = heads.get(h, '')
        t.append('  Head %d      %-6s %s' % (h, nozzle or '-', count_of(nozzle)))
    for s in range(1, positions + 1):
        nozzle = station.get(s, '')
        t.append('  Station %d   %-6s %s'
                 % (s, nozzle or '-',
                    count_of(nozzle) if nozzle else
                    'keep EMPTY -- the head drops into it'))
    if not homes and nozzle_count:
        t.append('  (no plan -- press Set up board; nozzles in use: %s)'
                 % ', '.join('%s x%d' % kv for kv in sorted(nozzle_count.items())))
    t.append('')
    t.append('NOZZLE CHANGES   (machine screen: Before Component / Head / Drop To / Pick Up)')
    if changes:
        for n, (on, before, head, drop, pick) in enumerate(changes, start=1):
            if not on:
                continue
            part = by_number.get(before)
            fitted = station.get(pick, '?')
            t.append('  %d.  before #%-4d %-7s Head %d    drop to station %d    '
                     'pick up station %d  -> %s'
                     % (n, before, '(%s)' % part['ref'] if part else '', head,
                        drop, pick, fitted))
            # follow the plan so later rows name the right nozzle
            station[drop], station[pick] = heads.get(head, '?'), None
            heads[head] = fitted
        t.append('  Tick each row ON in the Auto Nozzle Change screen; the file '
                 'carries them already.')
    else:
        t.append('  none -- everything runs on the nozzles fitted at the start')
    t.append('')
    t.append('FEEDERS   (tape 1-52, flexible 53-80, bulk 81-99)')
    if unassigned:
        t.append('  ! %d reel(s) still on the default slot -- run Set up board '
                 'or number them' % len(unassigned))
    if beyond:
        t.append('  ! %d reel(s) past tape slot %d: load in the flexible bank'
                 % (len(beyond), TAPE_LAST))
    widths = (max(len(r['package']) for r in rows) if rows else 8,
              max(len(r['value']) for r in rows) if rows else 8)
    fmt = '  %-4s %-' + str(widths[0]) + 's  %-' + str(widths[1]) + 's  %3s  %-6s  %-4s  %-5s  %s'
    t.append(fmt % ('Slot', 'Package', 'Value', 'Qty', 'Nozzle', 'Head', 'Mode', 'Designators'))
    t.append('  ' + '-' * (max(len(t[-1]) - 2, 40)))
    for row in rows:
        if row['skip']:
            continue
        # Every designator, wrapped -- a reel of eighteen 10k resistors is
        # exactly the one the operator has to be able to check off.
        lines, current = [], ''
        for ref in row['refs']:
            if current and len(current) + 1 + len(ref) > 58:
                lines.append(current)
                current = ref
            else:
                current = (current + ' ' + ref).strip()
        lines.append(current)
        mark = {'library': 'on machine  ', 'new': 'LOAD  '}.get(row['source'], '')
        t.append(fmt % (row['slot'], row['package'], row['value'], row['qty'],
                        row['nozzle'] or '-', row['head'] or 'auto',
                        row['mode'], mark + lines[0]))
        for more in lines[1:]:
            t.append(fmt % ('', '', '', '', '', '', '', more))
        if row['skipped']:
            t.append(fmt % ('', '', '', '', '', '', '',
                            'skip: ' + ' '.join(row['skipped'])))
    skipped_reels = [r for r in rows if r['skip']]
    if skipped_reels or skipped_all:
        t.append('')
        t.append('NOT PLACED THIS RUN')
        for row in skipped_reels:
            t.append('  %-*s  %-*s  %3d   %s' % (widths[0], row['package'],
                                                  widths[1], row['value'],
                                                  row['qty'], ' '.join(row['refs'])))
        loose = [r for r in skipped_all
                 if not any(r in row['refs'] for row in skipped_reels)]
        if loose:
            t.append('  single parts: ' + ' '.join(loose))
    t.append('')
    t.append('BEFORE RUNNING')
    t += ['  [ ] each reel in the slot above, tape direction set on the machine',
          '  [ ] pick position checked on the first reel of each package',
          '  [ ] nozzles on the heads and in the station as listed; empty position empty',
          '  [ ] board origin on the lower-left corner (lower-right for the bottom side)',
          '  [ ] step a polarised part before the full run']
    text = '\n'.join(t) + '\n'

    # ---------------------------------------------------------------- html
    e = H.escape
    h = ['<!doctype html><html><head><meta charset="utf-8">',
         '<title>%s</title>' % e(head_line),
         SHEET_CSS, '</head><body>']
    h.append('<h1>%s</h1>' % e(head_line))
    h.append('<div class="muted">board %.1f &times; %.1f mm &middot; %d placements on %d '
             'reels &middot; file <b>%s</b> &middot; %s</div>'
             % (board[0], board[1], len(placed),
                len([r for r in rows if not r['skip']]), e(csv_name), stamp))

    if job.fid_x or job.fid_y:
        h.append('<div><b>Fiducial 1</b> at %.3f, %.3f mm &ndash; the machine '
                 'finds it first when you press Step.</div>'
                 % (float(job.fid_x), float(job.fid_y)))
    if job.offset_x or job.offset_y:
        h.append('<div class="warn">Overall offset %s, %s mm is added to every '
                 'placement.</div>' % (num(job.offset_x), num(job.offset_y)))

    # rebuild the plan state for the html pass
    station = {}
    heads = {}
    for nozzle, (where, slot) in homes.items():
        if where == 'head':
            for hd in ((1, 2) if int(slot) == 0 else (int(slot),)):
                heads[hd] = nozzle
        else:
            station[int(slot)] = nozzle
    h.append('<h2>Nozzles</h2><table><tr><th>Where</th><th>Nozzle</th><th class="n">Parts</th><th>Loaded</th></tr>')
    for hd in (1, 2):
        nozzle = heads.get(hd, '')
        h.append('<tr><td>Head %d</td><td>%s</td><td class="n">%s</td>'
                 '<td class="box"><input type="checkbox" data-key="head-%d"></td></tr>'
                 % (hd, e(nozzle or '&ndash;') if nozzle else '&ndash;',
                    nozzle_count.get(nozzle, '') if nozzle else '', hd))
    for s in range(1, positions + 1):
        nozzle = station.get(s, '')
        h.append('<tr><td>Station %d</td><td>%s</td><td class="n">%s</td><td class="box">%s</td></tr>'
                 % (s, e(nozzle) if nozzle else '<span class="warn">keep EMPTY</span>',
                    nozzle_count.get(nozzle, '') if nozzle else '',
                    '<input type="checkbox" data-key="station-%d">' % s if nozzle else ''))
    h.append('</table>')

    h.append('<h2>Nozzle changes <span class="muted">(Auto Nozzle Change screen)</span></h2>')
    active = [c for c in changes if c[0]]
    if active:
        h.append('<table><tr><th>#</th><th>Before component</th><th>Head</th>'
                 '<th>Drop to station</th><th>Pick up from station</th><th>Fits</th><th>Set</th></tr>')
        for n, (on, before, head, drop, pick) in enumerate(changes, start=1):
            if not on:
                continue
            part = by_number.get(before)
            fitted = station.get(pick, '?')
            h.append('<tr><td>%d</td><td>%d %s</td><td>%d</td><td>%d</td><td>%d</td>'
                     '<td>%s</td><td class="box"><input type="checkbox" '
                     'data-key="change-%d"></td></tr>'
                     % (n, before, e('(%s)' % part['ref']) if part else '', head,
                        drop, pick, e(str(fitted)), n))
            station[drop], station[pick] = heads.get(head, '?'), None
            heads[head] = fitted
        h.append('</table><div class="muted">The rows are in the file already; '
                 'check they show as ON.</div>')
    else:
        h.append('<div>None &ndash; everything runs on the nozzles fitted at the start.</div>')

    h.append('<h2>Feeders <span class="muted">(tape 1&ndash;52, flexible 53&ndash;80, bulk 81&ndash;99)</span></h2>')
    if unassigned:
        h.append('<div class="warn">%d reel(s) still on the default slot &ndash; '
                 'run Set up board or number them.</div>' % len(unassigned))
    if beyond:
        h.append('<div class="warn">%d reel(s) past tape slot %d: load them in the '
                 'flexible bank.</div>' % (len(beyond), TAPE_LAST))
    h.append('<table><tr><th class="n">Slot</th><th>Package</th><th>Value</th>'
             '<th class="n">Qty</th><th>Nozzle</th><th>Head</th><th>Mode</th>'
             '<th>Designators</th><th>Loaded</th></tr>')
    for row in rows:
        if row['skip']:
            continue
        refs = ' '.join(row['refs'])
        if row['skipped']:
            refs += ' <span class="muted">(skip: %s)</span>' % e(' '.join(row['skipped']))
        h.append('<tr><td class="n"><b>%d</b></td><td>%s</td><td>%s</td><td class="n">%d</td>'
                 '<td>%s</td><td>%s</td><td>%d %s</td><td>%s</td><td class="box">'
                 '<input type="checkbox" data-key="slot-%d-%s"></td></tr>'
                 % (row['slot'], e(row['package']),
                    e(row['value']) + {'library': ' <span class="muted">(on machine)</span>',
                                       'new': ' <b class="warn">LOAD</b>'}.get(row['source'], ''),
                    row['qty'],
                    e(row['nozzle'] or '&ndash;') if row['nozzle'] else '&ndash;',
                    row['head'] or 'auto', row['mode'], e(MODES.get(row['mode'], '')),
                    refs if row['skipped'] else e(refs),
                    row['slot'], e(re.sub(r'\W+', '_', row['package'] + '_' + row['value']))))
    h.append('</table>')
    if skipped_reels or skipped_all:
        h.append('<h2>Not placed this run</h2><table><tr><th>Package</th><th>Value</th>'
                 '<th class="n">Qty</th><th>Designators</th></tr>')
        for row in skipped_reels:
            h.append('<tr class="skip"><td>%s</td><td>%s</td><td class="n">%d</td><td>%s</td></tr>'
                     % (e(row['package']), e(row['value']), row['qty'], e(' '.join(row['refs']))))
        loose = [r for r in skipped_all if not any(r in row['refs'] for row in skipped_reels)]
        if loose:
            h.append('<tr class="skip"><td colspan="3">single parts</td><td>%s</td></tr>'
                     % e(' '.join(loose)))
        h.append('</table>')
    h.append('<h2>Before running</h2><ul class="check">')
    for k, line in enumerate(t[-5:]):
        h.append('<li><label><input type="checkbox" data-key="before-%d">%s'
                 '</label></li>' % (k, e(line.strip()[4:])))
    h.append('</ul>')
    # The boxes are real: a tick is remembered in the browser, keyed by this
    # file, so the sheet survives a reload or a print halfway through loading
    # the reels. Reset clears the lot.
    h.append(SHEET_SCRIPT)
    h.append('</body></html>')
    return text, '\n'.join(h)


def write_ods(path, header, rows, sheet='Sheet1'):
    """A plain .ods with one sheet: header row, then rows.

    Numbers go as numbers, everything else as text -- so a footprint such
    as 0805 keeps its zero when LibreOffice opens it.
    """
    import zipfile
    from xml.sax.saxutils import escape

    def cell(value):
        if value is None or value == '':
            return '<table:table-cell/>'
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return ('<table:table-cell office:value-type="float" office:value="%s">'
                    '<text:p>%s</text:p></table:table-cell>' % (value, value))
        return ('<table:table-cell office:value-type="string"><text:p>%s</text:p>'
                '</table:table-cell>' % escape(str(value)))

    body = ''.join('<table:table-row>%s</table:table-row>'
                   % ''.join(cell(v) for v in row) for row in [header] + list(rows))
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'office:version="1.2"><office:body><office:spreadsheet>'
        '<table:table table:name="%s"><table:table-column '
        'table:number-columns-repeated="%d"/>%s</table:table>'
        '</office:spreadsheet></office:body></office:document-content>'
        % (escape(sheet), len(header), body))
    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
        'manifest:version="1.2">'
        '<manifest:file-entry manifest:full-path="/" '
        'manifest:media-type="application/vnd.oasis.opendocument.spreadsheet"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        '</manifest:manifest>')
    with zipfile.ZipFile(path, 'w') as zf:
        # the mimetype comes first and uncompressed, as the format demands
        zf.writestr(zipfile.ZipInfo('mimetype'),
                    'application/vnd.oasis.opendocument.spreadsheet',
                    compress_type=zipfile.ZIP_STORED)
        zf.writestr('content.xml', content, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr('META-INF/manifest.xml', manifest, compress_type=zipfile.ZIP_DEFLATED)
    return path


# --- the loading sheet: what goes into which feeder, from a match -----------

def loading_sheet(groups, profile, library, side, title='', list_name=''):
    """What the operator has to do at the feeder banks after a match.

    `groups` is the side's reels as the machine groups them (type_key ->
    parts). Three lists: reels to LOAD (slot, tape width, what to take out
    first), reels already on the machine to check, and reels placed by
    hand. Returns (text, html); the html has tick boxes like the setup
    sheet.
    """
    import datetime
    import html as H

    side_name = 'top' if side == 'T' else 'bottom'
    stamp = datetime.date.today().isoformat()
    lib_rows = dict((r[0], r) for r in (library.rows if library else []))
    load, on_machine, by_hand = [], [], []
    for key in sorted(groups, key=lambda k: (profile.settings_for(
            groups[k][0]['package'], groups[k][0]['comment'])['feeder'], k)):
        parts = groups[key]
        first = parts[0]
        settings = profile.settings_for(first['package'], first['comment'])
        package, value = first['package'], first['comment']
        entry = {'key': key, 'package': package, 'value': value,
                 'qty': len(parts), 'refs': sorted(p['ref'] for p in parts),
                 'slot': settings['feeder'],
                 'source': profile.types.get(key, {}).get('feeder_source', '')}
        if settings['skip']:
            entry['why'] = {'noslot': 'no free slot',
                            'notlisted': 'not on the feeder list'}.get(
                entry['source'], 'skipped')
            by_hand.append(entry)
            continue
        row = lib_rows.get(entry['slot'])
        entry['width'] = (row[1] if row and row[1] else 0) or tape_width(package) or 0
        entry['listed'] = row[2] if row and row[2] else ''
        entry['listed_package'] = row[3] if row and row[3] else ''
        same = bool(entry['listed']) and values_match(entry['listed'], value) and (
            not entry['listed_package'] or packages_match(entry['listed_package'], package))
        if same:
            on_machine.append(entry)
        else:
            load.append(entry)
    load.sort(key=lambda r: r['slot'])
    on_machine.sort(key=lambda r: r['slot'])
    free = [r[0] for r in library.free()] if library else []
    taken = set(r['slot'] for r in load)
    free_left = [s for s in free if s not in taken]

    head_line = 'Feeder loading -- %s, %s side' % (title or profile.board, side_name)
    sub = '%d reel(s) to load, %d already on the machine, %d by hand' % (
        len(load), len(on_machine), len(by_hand))
    if list_name:
        sub += '   list %s' % list_name
    sub += '   %s' % stamp

    # ---------------------------------------------------------------- text
    t = [head_line, '=' * len(head_line), sub, '']
    t.append('LOAD INTO FEEDERS')
    if load:
        t.append('  [ ] slot  mm  reel                          qty  take out first')
        for r in load:
            out = ('%s' % r['listed']) if r['listed'] else '-'
            t.append('  [ ] %-4d  %-2s  %-28s %4d  %s' % (
                r['slot'], r['width'] or '', ('%s %s' % (r['value'], r['package']))[:28],
                r['qty'], out))
    else:
        t.append('  nothing -- every reel is on the machine already')
    t.append('')
    t.append('ALREADY ON THE MACHINE -- check the reel is really there')
    if on_machine:
        t.append('  [ ] slot  list says        board wants                   qty')
        for r in on_machine:
            t.append('  [ ] %-4d  %-16s %-28s %4d' % (
                r['slot'], r['listed'][:16], ('%s %s' % (r['value'], r['package']))[:28],
                r['qty']))
    else:
        t.append('  none')
    t.append('')
    t.append('PLACE BY HAND -- not in the feeders this run')
    if by_hand:
        for r in by_hand:
            t.append('  %-28s %4d  %-22s %s' % (
                ('%s %s' % (r['value'], r['package']))[:28], r['qty'], r['why'],
                ' '.join(r['refs'])))
    else:
        t.append('  none')
    t.append('')
    if library:
        t.append('free slots left on the list: %s' % (
            ', '.join(str(s) for s in free_left) or 'none'))
    text = '\n'.join(t) + '\n'

    # ---------------------------------------------------------------- html
    e = H.escape
    h = ['<!doctype html><html><head><meta charset="utf-8">',
         '<title>%s</title>' % e(head_line), SHEET_CSS, '</head><body>']
    h.append('<h1>%s</h1>' % e(head_line))
    h.append('<div class="muted">%s</div>' % e(sub))
    h.append('<h2>Load into feeders (%d)</h2>' % len(load))
    if load:
        h.append('<table><tr><th class="box">&#10003;</th><th class="n">Slot</th>'
                 '<th class="n">Tape mm</th><th>Reel</th><th>Footprint</th>'
                 '<th class="n">Qty</th><th>Take out first</th><th>Designators</th></tr>')
        for r in load:
            out = e(r['listed']) if r['listed'] else '<span class="muted">empty</span>'
            h.append('<tr><td class="box"><input type="checkbox" data-key="load-%d"></td>'
                     '<td class="n"><b>%d</b></td><td class="n">%s</td><td><b>%s</b></td>'
                     '<td>%s</td><td class="n">%d</td><td>%s</td>'
                     '<td class="muted">%s</td></tr>'
                     % (r['slot'], r['slot'], r['width'] or '', e(r['value']),
                        e(r['package']), r['qty'], out, e(' '.join(r['refs']))))
        h.append('</table>')
    else:
        h.append('<div class="muted">Nothing &ndash; every reel is on the machine already.</div>')
    h.append('<h2>Already on the machine &ndash; check (%d)</h2>' % len(on_machine))
    if on_machine:
        h.append('<table><tr><th class="box">&#10003;</th><th class="n">Slot</th>'
                 '<th>List says</th><th>Board wants</th><th class="n">Qty</th>'
                 '<th>Designators</th></tr>')
        for r in on_machine:
            h.append('<tr><td class="box"><input type="checkbox" data-key="have-%d"></td>'
                     '<td class="n"><b>%d</b></td><td>%s</td><td>%s %s</td>'
                     '<td class="n">%d</td><td class="muted">%s</td></tr>'
                     % (r['slot'], r['slot'], e(r['listed']), e(r['value']),
                        e(r['package']), r['qty'], e(' '.join(r['refs']))))
        h.append('</table>')
    else:
        h.append('<div class="muted">None.</div>')
    h.append('<h2>Place by hand (%d)</h2>' % len(by_hand))
    if by_hand:
        h.append('<table><tr><th>Reel</th><th>Footprint</th><th class="n">Qty</th>'
                 '<th>Why</th><th>Designators</th></tr>')
        for r in by_hand:
            h.append('<tr><td><b>%s</b></td><td>%s</td><td class="n">%d</td>'
                     '<td class="warn">%s</td><td>%s</td></tr>'
                     % (e(r['value']), e(r['package']), r['qty'], e(r['why']),
                        e(' '.join(r['refs']))))
        h.append('</table>')
    else:
        h.append('<div class="muted">None.</div>')
    if library:
        h.append('<p class="muted">Free slots left on the list: %s</p>'
                 % (e(', '.join(str(s) for s in free_left)) or 'none'))
    h.append(SHEET_SCRIPT)
    h.append('</body></html>')
    return text, '\n'.join(h)


def write_loading_sheet(base, groups, profile, library, side, title='', list_name=''):
    """Write <base>_feeders_<side>.txt and .html. Returns the two paths."""
    text, html = loading_sheet(groups, profile, library, side, title, list_name)
    side_name = 'top' if side == 'T' else 'bottom'
    paths = ('%s_feeders_%s.txt' % (base, side_name), '%s_feeders_%s.html' % (base, side_name))
    with io.open(paths[0], 'w', encoding='utf-8', newline='') as handle:
        handle.write(text.replace('\n', '\r\n'))
    with io.open(paths[1], 'w', encoding='utf-8') as handle:
        handle.write(html)
    return paths


def write_setup_sheet(csv_path, parts, profile, side, board=(0, 0), title=''):
    """Write <csv>_setup.txt and .html beside a placement file. Returns paths."""
    base = csv_path[:-4] if csv_path.lower().endswith('.csv') else csv_path
    text, html = setup_sheet(parts, profile, side, board, title,
                             os.path.basename(csv_path))
    paths = (base + '_setup.txt', base + '_setup.html')
    with io.open(paths[0], 'w', encoding='utf-8', newline='') as handle:
        handle.write(text.replace('\n', '\r\n'))
    with io.open(paths[1], 'w', encoding='utf-8') as handle:
        handle.write(html)
    return paths


def feeder_bank(number):
    for low, high, name in FEEDER_BANKS:
        if low <= number <= high:
            return name
    return 'out of range'


def norm_rot(rot):
    """The native file uses 0..360, unlike the generic layout's -180..180."""
    return round(float(rot) % 360.0, 2)


def clean(text):
    """No commas anywhere -- the machine splits on them and nothing else."""
    return re.sub(r'\s+', ' ', str(text).replace(',', ' ')).strip()


# --- job settings (the preamble) -------------------------------------------

class Job(object):
    """The header block: panelisation, fiducial, nozzle changes."""

    def __init__(self, **kw):
        self.unit_length = kw.get('unit_length', 0)
        self.unit_width = kw.get('unit_width', 0)
        self.rows = kw.get('rows', 1)
        self.columns = kw.get('columns', 1)
        self.fid_x = kw.get('fid_x', 0.0)
        self.fid_y = kw.get('fid_y', 0.0)
        self.offset_x = kw.get('offset_x', 0)
        self.offset_y = kw.get('offset_y', 0)
        # (enabled, before_component, head, drop_station, pickup_station)
        self.nozzle_changes = kw.get('nozzle_changes') or [
            (False, 1, 1, 2, 1),
            (False, 2, 2, 3, 2),
            (False, 1, 1, 1, 1),
            (False, 1, 1, 1, 1),
        ]

    def to_dict(self):
        return {'unit_length': self.unit_length, 'unit_width': self.unit_width,
                'rows': self.rows, 'columns': self.columns,
                'fid_x': self.fid_x, 'fid_y': self.fid_y,
                'offset_x': self.offset_x, 'offset_y': self.offset_y,
                'nozzle_changes': [list(n) for n in self.nozzle_changes]}

    @classmethod
    def from_dict(cls, data):
        data = dict(data or {})
        if data.get('nozzle_changes'):
            data['nozzle_changes'] = [tuple(n) for n in data['nozzle_changes']]
        return cls(**data)

    def preamble(self):
        out = ['NEODEN,YY1,P&P FILE,,,,,,,,,,,', BLANK]
        out.append('PanelizedPCB,UnitLength,%s,UnitWidth,%s,Rows,%d,Columns,%d,'
                   % (num(self.unit_length), num(self.unit_width),
                      int(self.rows), int(self.columns)))
        out.append(BLANK)
        out.append('Fiducial,1-X,%.2f,1-Y,%.2f,OverallOffsetX,%s,OverallOffsetY,%s,'
                   % (float(self.fid_x), float(self.fid_y),
                      num(self.offset_x), num(self.offset_y)))
        out.append(BLANK)
        for on, before, head, drop, pick in self.nozzle_changes:
            out.append('NozzleChange,%s,BeforeComponent,%d,Head%d,Drop,Station%d,'
                       'PickUp,Station%d,'
                       % ('ON' if on else 'OFF', before, head, drop, pick))
        out.append(BLANK)
        return out


def num(value):
    """Whole numbers print bare, the way the machine writes them."""
    f = float(value)
    return str(int(f)) if f == int(f) else ('%g' % f)


# --- reading and writing ---------------------------------------------------

def read(path):
    """Parse a YY1 native file. Returns (Job, [part dict, ...]).

    A bare table with no preamble is accepted too, so a hand-made CSV and the
    machine's own re-export come back the same shape.
    """
    text = io.open(path, encoding='utf-8-sig', newline='').read()
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')

    kw, nozzles, header_at = {}, [], None
    for i, line in enumerate(lines):
        cells = [c.strip() for c in line.split(',')]
        tag = cells[0] if cells else ''
        if tag == 'Designator':
            header_at = i
            break
        if tag == 'PanelizedPCB' and len(cells) >= 9:
            kw.update(unit_length=fnum(cells[2]), unit_width=fnum(cells[4]),
                      rows=int(fnum(cells[6]) or 1),
                      columns=int(fnum(cells[8]) or 1))
        elif tag == 'Fiducial' and len(cells) >= 9:
            kw.update(fid_x=fnum(cells[2]), fid_y=fnum(cells[4]),
                      offset_x=fnum(cells[6]), offset_y=fnum(cells[8]))
        elif tag == 'NozzleChange' and len(cells) >= 9:
            nozzles.append((cells[1].upper() == 'ON', int(fnum(cells[3]) or 1),
                            int(re.sub(r'\D', '', cells[4]) or 1),
                            int(re.sub(r'\D', '', cells[6]) or 1),
                            int(re.sub(r'\D', '', cells[8]) or 1)))
    if nozzles:
        kw['nozzle_changes'] = nozzles
    if header_at is None:
        raise ValueError('%s: no "Designator" header row -- not a YY1 file'
                         % os.path.basename(path))

    parts = []
    for line in lines[header_at + 1:]:
        cells = [c.strip() for c in line.split(',')]
        if not cells or not cells[0] or len(cells) < 6:
            continue
        cells += [''] * (13 - len(cells))
        parts.append({
            'ref': cells[0], 'comment': cells[1], 'package': cells[2],
            'x': fnum(cells[3]), 'y': fnum(cells[4]), 'rot': fnum(cells[5]),
            'head': int(fnum(cells[6]) or 0), 'feeder': int(fnum(cells[7]) or 0),
            'speed': int(fnum(cells[8]) or 75), 'pick_h': fnum(cells[9]),
            'place_h': fnum(cells[10]), 'mode': int(fnum(cells[11]) or 1),
            'skip': int(fnum(cells[12]) or 0),
        })
    return Job.from_dict(kw), parts


def fnum(text):
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return 0.0


def row(part):
    """One data line, formatted exactly as the machine writes it."""
    return ('%s,%s,%s,%.2f,%.2f,%.2f,%d,%d,%d,%.1f,%.1f,%d,%d'
            % (clean(part['ref']), clean(part['comment']), clean(part['package']),
               float(part['x']), float(part['y']), norm_rot(part['rot']),
               int(part['head']), int(part['feeder']), int(part['speed']),
               float(part['pick_h']), float(part['place_h']),
               int(part['mode']), int(part['skip'])))


def write(path, parts, job=None):
    """Write a YY1 native file. CRLF, no BOM, trailing newline."""
    job = job or Job()
    lines = job.preamble() + [','.join(COLUMNS)] + [row(p) for p in parts]
    with io.open(path, 'w', encoding='utf-8', newline='') as handle:
        handle.write(NEWLINE.join(lines) + NEWLINE)
    return path


# --- the generic 11-column layout the machine auto-converts on load --------

LEGACY_HEADER = ['Designator', 'Footprint', 'Mid X', 'Mid Y', 'Ref X', 'Ref Y',
                 'Pad X', 'Pad Y', 'Layer', 'Rotation', 'Comment']


def write_legacy(path, parts, layer='T'):
    """The 11-column layout from NeoDen USA's support page.

    Kept because it is what ran before the native format was confirmed, and a
    machine on older firmware may still prefer it. Rotation goes back to
    -180..180 here; that is this layout's convention, not the native one.
    """
    lines = [','.join(LEGACY_HEADER)]
    for p in parts:
        rot = norm_rot(p['rot'])
        if rot > 180.0:
            rot -= 360.0
        x, y = round(float(p['x']), 3), round(float(p['y']), 3)
        lines.append('%s,%s,%g,%g,%g,%g,%g,%g,%s,%g,%s'
                     % (clean(p['ref']), clean(p['package']), x, y, x, y, x, y,
                        layer, rot, clean(p['comment'])))
    with io.open(path, 'w', encoding='utf-8', newline='') as handle:
        handle.write(NEWLINE.join(lines) + NEWLINE)
    return path


# --- turning KiCad names into what the machine's screen shows --------------

# Imperial chip codes the machine screen uses.
IMPERIAL = set(['01005', '0201', '0402', '0603', '0805', '1206', '1210',
                '1218', '1806', '1812', '2010', '2512', '2920'])

# Metric body codes (and IPC-7351 body sizes) -> the imperial code.
METRIC = {'0402': '01005', '0603': '0201', '1005': '0402', '1608': '0603',
          '2012': '0805', '3216': '1206', '3225': '1210', '4516': '1806',
          '4532': '1812', '5025': '2010', '6332': '2512', '6431': '2512',
          '5750': '2220', '2220': '0805',
          # IPC chip bodies that are an 0805/0603 reel in practice: the
          # wider 0805 capacitor (2.1 x 1.4) and the 0805 chip diode
          '2114': '0805', '2013': '0805', '1610': '0603'}

# Two-terminal packages that are already the name the machine wants, once the
# KiCad class prefix is off: D_SOD-123 -> SOD-123, D_MELF -> MELF.
_CLASS_PREFIX = re.compile(r'^(?:R|C|L|D|Q|U|F|K|J|SW|LED|FB)_(?=[A-Za-z])')

# A leading segment that is a package in its own right: letters then a size or
# pin count, as in SOIC-8, QFP64, SOT23, TSSOP-20.
_PACKAGE_HEAD = re.compile(r'^[A-Za-z]{2,6}-?\d+[A-Za-z]?$')


def normalise_package(name, overrides=None):
    """KiCad footprint name -> the short package name the YY1 shows.

    The rules below reproduce every mapping in the machine-verified file.
    Anything they cannot derive (a manufacturer part number standing in for a
    package, say) belongs in the profile's `packages` override table, which is
    consulted first and is what the GUI writes when you edit the cell.
    """
    raw = (name or '').strip()
    overrides = overrides or {}
    if raw in overrides:
        return overrides[raw]
    n = raw.split(':')[-1].strip()          # drop a KiCad library prefix
    if '@' in n:
        # PADS writes "parttype@decal" when a part overrides the decal its
        # part type would give it; the decal is the half that matters here.
        n = n.split('@')[-1].strip()
    if n in overrides:
        return overrides[n]
    if not n:
        return ''
    if n in IMPERIAL:
        return n

    # IPC-7351 small outline transistors: the pitch tells the family.
    # SOT95P280X145-5N -> SOT23-5, SOT65P210X110-3N -> SOT323, SOT230P700
    # -> SOT223.
    m = re.search(r'SOT(\d+)P(\d+)(?:X\d+)+-(\d+)N', n)
    if m:
        pitch, pins = int(m.group(1)), int(m.group(2 + 1))
        if pitch in (95, 100):
            family = 'SOT23'
        elif pitch == 65:
            family = 'SOT323'
        elif pitch in (230, 229):
            family = 'SOT223'
        elif pitch == 150:
            family = 'SOT89'
        else:
            family = 'SOT%d' % pitch
        return family if pins == 3 or family == 'SOT223' else '%s-%d' % (family, pins)

    # IPC-7351 IC body: SOIC127P600X175-8N -> SOIC-8, QFP50P1200X1200X120-64N
    # -> QFP-64. Searched, not anchored, because vendors prefix the part
    # number: TCAN332_SOIC127P600X175-8N.
    m = re.search(r'([A-Z]{2,8})\d+P\d+(?:X\d+)+-(\d+)N', n)
    if m:
        return '%s-%s' % (m.group(1), m.group(2))

    # IPC-7351 chip body: RESC6431X65N -> 6431 -> 2512.
    m = re.search(r'(?:RES|CAP|IND|DIO|FUS|LED|FER)[A-Z]{0,2}(\d{4})X\d+', n)
    if m:
        return METRIC.get(m.group(1), m.group(1))

    # Tantalum and other EIA case codes: CP_EIA-7343-40_Kemet-Y -> 7343.
    m = re.search(r'EIA-(\d{3,4})', n)
    if m:
        return m.group(1)

    # KiCad standard passives: R_0805_2012Metric -> 0805 (imperial wins).
    m = re.match(r'^[A-Za-z]{1,3}_(\d{4})(?:[_-]|$)', n)
    if m:
        code = m.group(1)
        if code in IMPERIAL:
            return code
        if code in METRIC:
            return METRIC[code]

    # A library prefix glued to a chip code, with an optional variant letter:
    # SMD0805 -> 0805, SMD0805A -> 0805, RES1206 -> 1206. PADS decal libraries
    # name them this way, and the variants are pad-geometry tweaks of one
    # package -- which come off one reel, so merging them is right.
    m = re.match(r'^[A-Z]{1,4}(\d{4})[A-Z]?$', n)
    if m and m.group(1) in IMPERIAL:
        return m.group(1)

    # An imperial code anywhere in an underscore-separated name:
    # L_CommonModeChoke_Coilcraft_1812CAN -> 1812.
    for token in re.split(r'[_\-. ]', n):
        m = re.match(r'^(\d{4})[A-Za-z]*$', token)
        if m and m.group(1) in IMPERIAL:
            return m.group(1)

    # Strip the KiCad class prefix, then keep the first segment only when it
    # is itself a package name -- SOIC-8_3.9x4.9mm_P1.27mm -> SOIC-8, and
    # SOT23_LMH6642 -> SOT23. A custom decal like MURATA_NFM21CC223R1H3D has
    # its meaning in the tail, so truncating there would throw it away and
    # merge parts that need different reels.
    stripped = _CLASS_PREFIX.sub('', n)
    head = stripped.split('_')[0].strip()
    if head != stripped and _PACKAGE_HEAD.match(head):
        return clean(head)
    return clean(stripped or n)


# A leading value token: 10uF, 4K7, 470p, 100nF, 22uH, 120R.
_VALUE_HEAD = re.compile(
    r'^(\d+(?:[.,]\d+)?[pnuµmkKMRrGT]?[FHRΩ]?\d*)(?:[_\s].+)$')
# A voltage rating in front of the real part: "18V SMCJ14A".
_LEAD_VOLTS = re.compile(r'^\d+(?:\.\d+)?\s*V[_\s]+(\S.*)$')
# KiCad's generic-variant placeholder: MCP16331x-xCH -> MCP16331.
_GENERIC_TAIL = re.compile(r'^(.+?)x-x\w*$')


def normalise_value(value, overrides=None):
    """BOM value -> the Comment the YY1 shows.

    The machine groups feeders by Comment, so the point is to shorten without
    merging two things that need different reels. Rules here only ever strip a
    ratings/packaging suffix. Anything else goes in the profile's `values`
    override table -- which the GUI edits.
    """
    raw = clean(value)
    overrides = overrides or {}
    if raw in overrides:
        return overrides[raw]
    if not raw:
        return ''

    m = _LEAD_VOLTS.match(raw)
    if m:
        raw = m.group(1).strip()
    m = _VALUE_HEAD.match(raw)
    if m:
        return m.group(1)
    m = _GENERIC_TAIL.match(raw)
    if m:
        return m.group(1)
    return raw


def value_essence(value):
    """What a value means once wording is stripped: 10uF/16V, 10uF, 10u and
    10 uF all give '10u'; 100n and 100nF give '100n'; 4k7, 4K7 and 4.7k give
    '4.7k'. Used only to *suggest* reels that may be the same part."""
    v = clean(value).lower().replace('µ', 'u')
    # a voltage rating, a tolerance, and anything written after them
    v = re.sub(r'[\s/,_-]*\d+(\.\d+)?\s*v\b.*$', '', v)
    v = re.sub(r'[\s/,_-]*\d+(\.\d+)?\s*%.*$', '', v)
    v = v.replace(' ', '')
    # 4k7 -> 4.7k, 2n2 -> 2.2n: the multiplier letter as decimal point
    v = re.sub(r'^(\d+)([kmrunp])(\d+)$', r'\1.\3\2', v)
    # unit letters carry no information: 330r -> 330, 100nf -> 100n, 22uh -> 22u
    v = re.sub(r'(?<=\d)(ohm|r)$', '', v)
    v = re.sub(r'(?<=[0-9kmunp])[fh]$', '', v)
    return v.strip()


def type_key(package, comment):
    """The machine's own grouping: same footprint + same value = same type."""
    return '%s|%s' % (clean(package), clean(comment))


# --- the profile: feeders, rotations and the rest of the columns -----------

DEFAULTS = {'head': 0, 'feeder': 1, 'speed': 75, 'pick_h': 0.0, 'place_h': 0.0,
            'mode': 1, 'skip': 0, 'rot_offset': 0.0}

# Placement order in the written file. 'ref' is designator order; 'nozzle'
# groups each nozzle into one contiguous run, which is what makes a nozzle
# change plan possible -- the machine changes before a component number, so
# the runs have to be contiguous for one change to cover a whole group.
ORDERS = ['ref', 'nozzle', 'feeder']

EDITABLE = ['feeder', 'head', 'speed', 'pick_h', 'place_h', 'mode', 'skip',
            'rot_offset']

INT_FIELDS = set(['feeder', 'head', 'speed', 'mode', 'skip'])


class Profile(object):
    """Per-board settings that survive a re-export.

    Three layers, each overriding the one before:

        defaults   -> every part
        types      -> every part of one Comment+Footprint type
        parts      -> one designator

    plus two rename tables (`packages`, `values`) feeding the normalisers, and
    a global rotation offset. A re-export from KiCad changes coordinates; this
    file holds everything you set by hand, so you never re-enter it.
    """

    def __init__(self, data=None):
        data = data or {}
        self.board = data.get('board', '')
        self.rot_offset = float(data.get('rot_offset', 0.0))
        self.order = data.get('order', 'ref')
        self.sequence = list(data.get('sequence') or [])
        # Each board side is its own file with its own header, so a nozzle
        # plan is per side. A profile written before that keyed homes by
        # nozzle alone; read it as the top side's.
        homes = dict(data.get('nozzle_homes') or {})
        if homes and not set(homes) <= set('TB'):
            homes = {'T': homes}
        self.nozzle_homes = homes
        self.nozzle_changes = dict(data.get('nozzle_changes') or {})
        self.station_positions = int(data.get('station_positions',
                                              STATION_POSITIONS))
        # Skipped parts normally stay in the file with Skip=1 (the machine
        # counts them). With this set they are left out altogether: a run
        # that places only what is on the machine and leaves the rest to
        # the bench.
        self.omit_skipped = bool(data.get('omit_skipped', False))
        # Board size in mm, so a CSV opened on its own can still be drawn.
        self.board_w = float(data.get('board_w', 0) or 0)
        self.board_h = float(data.get('board_h', 0) or 0)
        self.defaults = dict(DEFAULTS)
        self.defaults.update(data.get('defaults') or {})
        self.job = Job.from_dict(data.get('job'))
        self.types = dict(data.get('types') or {})
        self.parts = dict(data.get('parts') or {})
        self.packages = dict(data.get('packages') or {})
        self.values = dict(data.get('values') or {})
        # Reels merged across footprints: "10k 0603" exported as "10k 0805"
        # because that is the part actually on the reel. Keyed by the reel
        # it applies to, so no other 0603 is touched.
        self.aliases = dict(data.get('aliases') or {})
        self.notes = data.get('notes', '')

    # -- persistence
    @classmethod
    def load(cls, path):
        if not path or not os.path.isfile(path):
            return cls()
        with io.open(path, encoding='utf-8') as handle:
            return cls(json.load(handle))

    def save(self, path):
        with io.open(path, 'w', encoding='utf-8') as handle:
            handle.write(json.dumps(self.to_dict(), indent=2, sort_keys=True,
                                    ensure_ascii=False))
        return path

    def to_dict(self):
        """Everything the profile holds, as plain data (what save writes,
        and what the window's undo keeps)."""
        return {'version': 1, 'board': self.board, 'notes': self.notes,
                'rot_offset': self.rot_offset, 'order': self.order,
                'station_positions': self.station_positions,
                'omit_skipped': self.omit_skipped,
                'board_w': self.board_w, 'board_h': self.board_h,
                'sequence': self.sequence, 'nozzle_homes': self.nozzle_homes,
                'nozzle_changes': self.nozzle_changes,
                'defaults': self.defaults,
                'job': self.job.to_dict(), 'packages': self.packages,
                'values': self.values, 'aliases': self.aliases,
                'types': self.types, 'parts': self.parts}

    # -- lookup
    def settings_for(self, package, comment, ref=None):
        """Merged column values for one part: defaults < type < designator."""
        out = dict(self.defaults)
        out.update(self.types.get(type_key(package, comment), {}))
        if ref:
            out.update(self.parts.get(ref, {}))
        return {k: (int(out[k]) if k in INT_FIELDS else float(out[k])
                    if k in ('pick_h', 'place_h', 'rot_offset') else out[k])
                for k in DEFAULTS if k in out}

    def homes_for(self, side):
        """Where each nozzle starts on one side: {nozzle: (where, slot)}."""
        return dict((n, tuple(h))
                    for n, h in (self.nozzle_homes.get(side) or {}).items())

    def set_homes(self, side, homes):
        self.nozzle_homes[side] = dict((n, list(h)) for n, h in homes.items())

    def changes_for(self, side):
        """The side's planned changes, or None if it has no plan of its own."""
        rows = self.nozzle_changes.get(side)
        return [tuple(r) for r in rows] if rows else None

    def set_changes(self, side, changes):
        self.nozzle_changes[side] = [list(c) for c in changes]

    def job_for(self, side):
        """The header for one side: shared settings, that side's changes."""
        job = Job.from_dict(self.job.to_dict())
        rows = self.changes_for(side)
        if rows:
            job.nozzle_changes = rows + [(False, 1, 1, 1, 1)] * \
                max(0, MAX_NOZZLE_CHANGES - len(rows))
        return job

    def alias(self, package, comment):
        """The reel a part is exported on, after any cross-footprint merge.

        Applied after normalisation wherever reels are named, so a 10k on an
        0603 footprint can leave the file as "10k 0805" -- the reel that is
        really loaded -- while every other 0603 stays what it is.
        """
        target = self.aliases.get(type_key(package, comment))
        if target:
            return clean(target[0]), clean(target[1])
        return package, comment

    def nozzle_for(self, key, suggested=''):
        """What this type will be picked with: your choice, else the suggestion."""
        return self.types.get(key, {}).get('nozzle') or suggested

    def set_type(self, key, field, value):
        self.types.setdefault(key, {})[field] = value

    def set_part(self, ref, field, value):
        self.parts.setdefault(ref, {})[field] = value

    # -- feeder assignment
    def assign_feeders(self, keys, start=TAPE_FIRST, overwrite=False):
        """Number types sequentially through the tape bank.

        Types you already pinned keep their number unless overwrite is set;
        the run then fills the gaps around them, so adding a part to a board
        does not renumber the reels already on the machine.
        """
        taken = set()
        if not overwrite:
            for key in keys:
                pinned = self.types.get(key, {}).get('feeder')
                if pinned:
                    taken.add(int(pinned))
        nxt = start
        assigned = []
        for key in keys:
            pinned = self.types.get(key, {}).get('feeder')
            if pinned and not overwrite:
                assigned.append((key, int(pinned)))
                continue
            while nxt in taken and nxt <= TAPE_LAST:
                nxt += 1
            self.set_type(key, 'feeder', nxt)
            taken.add(nxt)
            assigned.append((key, nxt))
            nxt += 1
        return assigned

    # -- learning from the machine
    def absorb(self, parts, per_part=False):
        """Pull settings back out of a CSV the machine (or an operator) edited.

        After a run, the file on the SD card holds the feeder numbers, modes
        and heights actually used. Feeding it back here means the next export
        from KiCad keeps them.

        A column that reads the same on every part and equals the default is a
        placeholder rather than a decision -- a converter's starting value that
        nobody has touched. Recording those would pin all 93 part types of a
        board to feeder 1 and then leave auto-numbering with nothing it is
        allowed to change, so they are skipped.
        """
        fields = ('feeder', 'head', 'speed', 'pick_h', 'place_h', 'mode', 'skip')
        untouched = set()
        for field in fields:
            seen = set(p[field] for p in parts)
            if len(seen) == 1 and seen.pop() == self.defaults.get(field):
                untouched.add(field)

        changed = 0
        for part in parts:
            key = type_key(part['package'], part['comment'])
            target = self.parts.setdefault(part['ref'], {}) if per_part \
                else self.types.setdefault(key, {})
            for field in fields:
                if field in untouched:
                    continue
                if target.get(field) != part[field]:
                    target[field] = part[field]
                    changed += 1
        return changed
