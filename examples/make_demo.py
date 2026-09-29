#!/usr/bin/env python3
"""A made-up demo board and feeder list, for trying the converter and for
the screenshots in the user guide. Nothing here is a real product.

    python examples/make_demo.py      # writes demo_yy1_top.csv and demo_feeders.csv here
"""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import yy1lib as yy1

random.seed(7)
W, H = 60.0, 40.0
REELS = [
    ('0805', '100nF', 12), ('0805', '10k', 8), ('0805', '1k', 4), ('0805', '4k7', 3),
    ('0805', '22pF', 2), ('0805', '10uF', 3), ('0603', '100nF', 6), ('0603', '33k', 2),
    ('SOT23', 'BC847', 3), ('SOT23-5', 'MCP6001', 1), ('SOD-123', '1N4148W', 2),
    ('SOIC-8', 'MCP2551', 1), ('SOIC-16', 'ST3232', 1), ('TSSOP-20', 'STM32G030', 1),
    ('CTSA', '10uF/16V', 2), ('QFP-32', 'ATMEGA328', 1), ('DPAK', 'LM1117-3.3', 1),
]
prefix = {'0805': 'C', '0603': 'C', 'SOT23': 'Q', 'SOT23-5': 'U', 'SOD-123': 'D',
          'SOIC-8': 'U', 'SOIC-16': 'U', 'TSSOP-20': 'U', 'CTSA': 'C', 'QFP-32': 'U',
          'DPAK': 'U'}
counters = {}
parts = []
for package, value, qty in REELS:
    letter = 'R' if value[-1] in 'kK' or value.endswith('R') or value in ('1k', '10k', '4k7', '33k') else prefix[package]
    for _ in range(qty):
        counters[letter] = counters.get(letter, 0) + 1
        parts.append({
            'ref': '%s%d' % (letter, counters[letter]), 'comment': value, 'package': package,
            'x': round(random.uniform(3, W - 3), 2), 'y': round(random.uniform(3, H - 3), 2),
            'rot': random.choice([0, 90, 180, 270]), 'head': 0, 'feeder': 1, 'speed': 75,
            'pick_h': 0, 'place_h': 0, 'mode': 1, 'skip': 0,
        })
parts.append({'ref': 'FID1', 'comment': 'FIDUCIAL', 'package': 'Fiducial_1mm', 'x': 4.0, 'y': 4.0,
              'rot': 0, 'head': 0, 'feeder': 1, 'speed': 75, 'pick_h': 0, 'place_h': 0,
              'mode': 1, 'skip': 1})
job = yy1.Job()
job.rows, job.columns = 1, 1
yy1.write(os.path.join(HERE, 'demo_yy1_top.csv'), parts, job)

profile = yy1.Profile()
profile.board = 'demo'
profile.board_w, profile.board_h = W, H
profile.save(os.path.join(HERE, 'demo_yy1_profile.json'))

# the reels that stay on the machine: a plausible workshop set
static = [(1, 8, '10k', '0805'), (2, 8, '1k', '0805'), (3, 8, '4k7', '0805'), (4, 8, '100R', '0805'),
          (5, 8, '100nF', '0805'), (6, 8, '10uF', '0805'), (7, 8, '22pF', '0805'), (8, 8, '100nF', '0603'),
          (9, 8, '10k', '0603'), (10, 8, 'BC847', 'SOT23'), (11, 8, '1N4148W', 'SOD-123'),
          (12, 12, 'MCP2551', 'SOIC-8'), (13, 8, '10uF/16V', 'CTSA'), (14, 12, 'LM1117-3.3', 'DPAK'),
          (15, 8, '', ''), (16, 8, '', ''), (17, 8, '', ''), (18, 12, '', ''), (19, 16, '', ''),
          (20, 16, '', ''), (21, 24, '', '')]
lib = yy1.FeederLibrary([list(r) for r in static], '')
lib.save_csv(os.path.join(HERE, 'demo_feeders.csv'))
print('demo board: %d parts, %d reels; feeder list: %d reels, %d free slots'
      % (len(parts), len(REELS), len(lib.loaded()), len(lib.free())))
