#!/usr/bin/env python3
"""Check a generated YY1 file against the one the machine actually ran.

`reference/LIN2LIN_yy1_top_YY1_rot0.csv` is the reference: the YY1 loaded it and placed
from it on 2026-09-02. Anything the converter emits for that board has to match
it, so this is the regression test for the whole pipeline -- geometry, name
shortening, column order, preamble, line endings.

    ./verify_yy1.py                       # regenerate the reference board and compare
    ./verify_yy1.py mine.csv reference.csv

Coordinates are compared to 0.01 mm rather than by string. The reference was
produced by re-reading a 3-decimal file and reformatting to 2, so its last
digit truncates where ours rounds -- a 0.005 mm artifact of that round trip,
four times below the machine's own +-0.02 mm placement spec.
"""

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yy1lib as yy1

HERE = os.path.dirname(os.path.abspath(__file__))
REFERENCE = os.path.join(HERE, 'reference', 'LIN2LIN_yy1_top_YY1_rot0.csv')
PROJECT = os.path.join(HERE, 'kicad', 'LIN2LIN')
TOLERANCE = 0.011      # mm


def headers(path):
    """The preamble and column row, verbatim -- these must match exactly."""
    text = io.open(path, encoding='utf-8-sig', newline='').read()
    lines = text.replace('\r\n', '\n').split('\n')
    cut = next(i for i, l in enumerate(lines) if l.startswith('Designator'))
    return lines[:cut + 1]


def compare(mine_path, ref_path, tolerance=TOLERANCE):
    """Return a list of problems; empty means the files agree."""
    problems = []

    mine_head, ref_head = headers(mine_path), headers(ref_path)
    if mine_head != ref_head:
        for i, (a, b) in enumerate(zip(mine_head, ref_head)):
            if a != b:
                problems.append('header line %d\n   ours: %r\n    ref: %r'
                                % (i, a, b))
        if len(mine_head) != len(ref_head):
            problems.append('preamble is %d lines, reference is %d'
                            % (len(mine_head), len(ref_head)))

    _, mine = yy1.read(mine_path)
    _, ref = yy1.read(ref_path)
    mine_by, ref_by = dict((p['ref'], p) for p in mine), \
        dict((p['ref'], p) for p in ref)

    missing = sorted(set(ref_by) - set(mine_by))
    extra = sorted(set(mine_by) - set(ref_by))
    if missing:
        problems.append('missing %d part(s): %s' % (len(missing), ', '.join(missing)))
    if extra:
        problems.append('%d extra part(s): %s' % (len(extra), ', '.join(extra)))

    for ref_name in sorted(set(mine_by) & set(ref_by)):
        a, b = mine_by[ref_name], ref_by[ref_name]
        for field, label in (('x', 'X'), ('y', 'Y')):
            if abs(a[field] - b[field]) > tolerance:
                problems.append('%-6s %s %.2f, reference %.2f (off by %.3f mm)'
                                % (ref_name, label, a[field], b[field],
                                   a[field] - b[field]))
        if yy1.norm_rot(a['rot']) != yy1.norm_rot(b['rot']):
            problems.append('%-6s rotation %.2f, reference %.2f'
                            % (ref_name, a['rot'], b['rot']))
        for field in ('comment', 'package'):
            if a[field] != b[field]:
                problems.append('%-6s %s %r, reference %r'
                                % (ref_name, field, a[field], b[field]))
        for field in ('head', 'feeder', 'speed', 'mode', 'skip'):
            if a[field] != b[field]:
                problems.append('%-6s %s %s, reference %s'
                                % (ref_name, field, a[field], b[field]))
    return problems, len(ref)


def regenerate():
    """Run the converter on LIN2LIN into a scratch directory."""
    import argparse
    import tempfile
    import kicad2yy1

    outdir = tempfile.mkdtemp(prefix='yy1verify')
    args = argparse.Namespace(
        project=PROJECT, pcb=None, positions=None,
        bom=None, outdir=outdir, prefix=None, variant=None, exclude='', only='',
        origin_ok=True, include_tht=False, max_size=kicad2yy1.MAX_COMPONENT,
        min_size=kicad2yy1.MIN_COMPONENT, no_size_check=False,
        bottom_rot='as-is', format='yy1', profile=None, rot_offset=None,
        assign_feeders=False, renumber=False, save_profile=False,
        raw_names=False)
    result = kicad2yy1.analyse(args)
    written = kicad2yy1.write_files(result, outdir=outdir)
    return written[0][0]


def main():
    if len(sys.argv) >= 3:
        mine, ref = sys.argv[1], sys.argv[2]
    elif len(sys.argv) == 2:
        mine, ref = sys.argv[1], REFERENCE
    else:
        print('regenerating LIN2LIN top side...')
        mine, ref = regenerate(), REFERENCE

    problems, count = compare(mine, ref)
    print('ours %s' % mine)
    print('ref  %s  (%d parts)' % (ref, count))
    if problems:
        print('\nFAIL -- %d difference(s):' % len(problems))
        for line in problems:
            print('  ' + line)
        return 1
    print('\nPASS -- preamble, header and all %d placements agree '
          '(coordinates within %.3f mm).' % (count, TOLERANCE))
    return 0


if __name__ == '__main__':
    sys.exit(main())
