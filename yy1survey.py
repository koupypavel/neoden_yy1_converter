#!/usr/bin/env python3
"""Which reels turn up on most boards -- the ones worth leaving on the machine.

Give it a folder; it finds every project under it (KiCad with a Fabrication
Toolkit export, PADS with an ASCII export, Altium with a .PcbDoc), reads each
board the way the converters do, and counts for every reel -- footprint and
value, as the machine groups them -- on how many boards it is needed and how
many placements it has. Sorted by boards first: a reel on six boards earns a
fixed slot even if each board uses it twice.

    ./yy1survey.py path/to/all/projects --list feeders/feeders.ods
    ./yy1survey.py path/to/altium/projects --top 30 --write static_feeders.ods
"""

import argparse
import csv
import io
import os
import sys
from glob import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yy1lib as yy1

SKIP_DIRS = ('backups', '-backups', '.history', '__previews', 'old', 'history',
             'production', 'gerber', 'project outputs', '.git', 'lib', 'libs')


def find_projects(root, say=None):
    """[(kind, folder or board file, name), ...] under root, one per board.

    KiCad: a folder with a .kicad_pcb and production/*positions.csv.
    PADS: a folder with a PADS Layout .asc. Altium: every .PcbDoc.
    Backup, gerber and library folders are left alone.
    """
    found = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs
                         if not any(d.lower().endswith(s) or d.lower() == s for s in SKIP_DIRS))
        lower = [f.lower() for f in files]
        if any(f.endswith('.kicad_pcb') for f in lower):
            if glob(os.path.join(folder, 'production', '*positions.csv')):
                found.append(('kicad', folder, os.path.basename(folder)))
            elif say:
                say('note:   %s: KiCad board without a Fabrication Toolkit export, skipped'
                    % os.path.relpath(folder, root))
        for f in files:
            if f.lower().endswith('.asc'):
                try:
                    with io.open(os.path.join(folder, f), encoding='cp1250',
                                 errors='replace') as handle:
                        head = handle.readline()
                except (IOError, OSError):
                    continue
                if head.startswith('!PADS-POWERPCB'):
                    found.append(('pads', folder, os.path.basename(folder)))
                    break
        for f in files:
            if f.lower().endswith('.pcbdoc'):
                found.append(('altium', os.path.join(folder, f), os.path.splitext(f)[0]))
    return found


def reels_of(kind, path, say):
    """{reel key: placements} for one project, or None when it cannot be read."""
    import argparse as _a
    try:
        if kind == 'kicad':
            import kicad2yy1 as k2y
            args = _a.Namespace(
                project=path, pcb=None, positions=None, bom=None, outdir=None,
                prefix=None, variant=None, exclude='', only='', origin_ok=True,
                include_tht=False, max_size=k2y.MAX_COMPONENT,
                min_size=k2y.MIN_COMPONENT, no_size_check=False, bottom_rot='as-is',
                format='yy1', profile=None, rot_offset=None, assign_feeders=False,
                renumber=False, save_profile=False, raw_names=False)
            result = k2y.analyse(args)
            sides = result.sides
        elif kind == 'pads':
            import pads2yy1 as p2y
            sides = p2y.analyse(p2y.defaults(path), say=lambda m: None).sides
        else:
            import altium2yy1 as a2y
            sides = a2y.analyse(a2y.defaults(path), say=lambda m: None).sides
    except SystemExit as exc:
        say('skip    %s: %s' % (path, exc))
        return None
    except Exception as exc:
        say('skip    %s: %s' % (path, exc))
        return None
    reels = {}
    for parts in sides.values():
        for part in parts:
            if part.get('skip'):
                continue
            key = yy1.type_key(part['package'], part['comment'])
            reels[key] = reels.get(key, 0) + 1
    return reels


def survey(root, say=print, projects=None):
    """The count: {key: {'package', 'value', 'boards': [names], 'parts': n}},
    and the boards read as [(name, kind, reel count)]."""
    projects = projects if projects is not None else find_projects(root, say)
    say('found   %d project(s) under %s' % (len(projects), root))
    tally, boards = {}, []
    for kind, path, name in projects:
        reels = reels_of(kind, path, say)
        if reels is None:
            continue
        boards.append((name, kind, len(reels)))
        say('read    %-28s %-7s %3d reel(s), %4d placement(s)'
            % (name[:28], kind, len(reels), sum(reels.values())))
        for key, count in reels.items():
            package, value = key.split('|', 1)
            entry = tally.setdefault(key, {'package': package, 'value': value,
                                           'boards': [], 'parts': 0})
            entry['boards'].append(name)
            entry['parts'] += count
    return tally, boards


def ranked(tally):
    """Keys by how many boards need them, then placements, then name."""
    return sorted(tally, key=lambda k: (-len(tally[k]['boards']), -tally[k]['parts'], k))


def on_list(tally, library):
    """{key: slot} for reels the feeder list already has."""
    out = {}
    if not library:
        return out
    for key, entry in tally.items():
        hit = library.match(entry['package'], entry['value'])
        if hit:
            out[key] = hit[0]
    return out


def place_static(keys, tally, library):
    """Put reels into the list's free tape slots, by tape width. Returns
    [(key, slot)] placed and [key] that found no slot."""
    placed, left = [], []
    for key in keys:
        entry = tally[key]
        width = yy1.tape_width(entry['package']) or 0
        free = [r for r in library.free(width) if yy1.TAPE_FIRST <= r[0] <= yy1.TAPE_LAST]
        if not free and width:
            free = [r for r in library.free(0) if yy1.TAPE_FIRST <= r[0] <= yy1.TAPE_LAST]
        if not free:
            left.append(key)
            continue
        slot = free[0][0]
        library.set_value(slot, entry['value'], width or free[0][1], entry['package'])
        placed.append((key, slot))
    return placed, left


def write_csv(path, tally, boards, listed=None):
    listed = listed or {}
    with io.open(path, 'w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['value', 'footprint', 'boards', 'of', 'placements', 'slot on list',
                         'boards using it'])
        for key in ranked(tally):
            e = tally[key]
            writer.writerow([e['value'], e['package'], len(e['boards']), len(boards),
                             e['parts'], listed.get(key, ''), ', '.join(e['boards'])])
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('root', help='folder to search for projects')
    parser.add_argument('--list', help='feeder list to compare with (and to add to)')
    parser.add_argument('--top', type=int, default=0,
                        help='put the N most used reels not yet on the list into '
                             'its free slots')
    parser.add_argument('--write', help='where to write the list with the additions '
                                        '(.ods or .csv; default: beside --list)')
    parser.add_argument('--csv', help='write the survey table here')
    args = parser.parse_args()
    tally, boards = survey(args.root)
    library = yy1.FeederLibrary.read(args.list) if args.list else None
    listed = on_list(tally, library)
    print('')
    print('%-22s %-14s %6s %6s  %s' % ('value', 'footprint', 'boards', 'parts', 'slot'))
    for key in ranked(tally)[:60]:
        e = tally[key]
        print('%-22s %-14s %3d/%-3d %6d  %s' % (e['value'][:22], e['package'][:14],
                                               len(e['boards']), len(boards), e['parts'],
                                               listed.get(key, '')))
    if args.csv:
        print('survey  %s' % write_csv(args.csv, tally, boards, listed))
    if args.top and library:
        keys = [k for k in ranked(tally) if k not in listed][:args.top]
        placed, left = place_static(keys, tally, library)
        for key, slot in placed:
            print('slot %-3d <- %s' % (slot, key.replace('|', ' ')))
        if left:
            print('no free slot for: %s' % ', '.join(k.replace('|', ' ') for k in left))
        out = args.write or os.path.splitext(args.list)[0] + '_static.ods'
        (library.save_ods if out.lower().endswith('.ods') else library.save_csv)(out)
        print('list    %s' % out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
