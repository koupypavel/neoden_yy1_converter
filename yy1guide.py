#!/usr/bin/env python3
"""The guide: one job from the CAD files to the SD card, a step at a time.

Five steps, each driving the main window rather than copying it, so what the
guide does is what the tabs show:

    1  Board files        what to export from KiCad or PADS, and a check of
                          the folder before anything is read
    2  Check the board    what was read, which side, the fiducial
    3  Heads and nozzles  what is on the heads and in the station now (or
                          the drawer), then Set up board
    4  Feeders            the board against the feeder list: what is on the
                          machine, what to load, what goes by hand
    5  Check and export   what is still wrong, then the file and the sheets

The guide keeps no state of its own beyond its choices; the board, the
profile and the feeder list stay the main window's.
"""

import glob
import json
import os
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import altium2yy1 as a2y
import pads2yy1 as p2y
import yy1lib as yy1

STEPS = ['Board files', 'Check the board', 'Heads and nozzles', 'Feeders',
         'Check and export']
EMPTY = '—'

MARK = {'ok': ('✓', '#1b7a1b'), 'warn': ('!', '#b35900'),
        'bad': ('✗', '#b00020'), 'info': ('•', '#666666')}

HOWTO = {
    'kicad': [
        'Open the board in the KiCad PCB editor. If the folder holds more '
        'than one .kicad_pcb, open the one meant for the machine.',
        'Optional: Place > Drill/Place File Origin on the board\'s lower-left '
        'corner. The converter measures from the outline either way.',
        'Run Fabrication Toolkit (its toolbar button, or Tools > External '
        'Plugins). Keep "Apply automatic component translations" ticked: '
        'the board tested on the machine was exported that way.',
        'Press Generate. It writes production/ next to the board, with '
        '...positions.csv and ...bom.csv in it.',
        'Choose the board\'s folder below.',
    ],
    'pads': [
        'In PADS Layout: File > Export..., save as type ASCII (*.asc), into '
        'the project folder. In the list of sections tick Parts and Lines.',
        'Values come from the .asc itself (its Value attributes). For parts '
        'without one, add PADS\'s Bill of Materials report (keep its '
        'BillOfMaterials... file name) or a PADS Logic export (File > '
        'Export, .txt) to the same folder.',
        'Optional: a Component Placement report (.xlsx) is read too, and the '
        'two are cross-checked.',
        'Choose the project folder below.',
    ],
}

HOWTO['altium'] = [
    'Nothing to export: the .PcbDoc is read as it is -- positions, rotation, '
    'side, footprint and the Comment of every part, the board outline, and '
    'which parts are through-hole (from their pads).',
    'Values are the parts\' Comment. For a part whose Comment is empty or is '
    'not the value, put a BOM beside the board (Reports > Bill of Materials, '
    'saved as .xlsx with Comment and Designator columns): it fills in and '
    'overrides.',
    'Altium\'s own pick-and-place export (File > Assembly Outputs, CSV) is '
    'read too when there is one -- positions from it, outline and pads from '
    'the board.',
    'Choose the project folder below, or pick the .PcbDoc itself when the '
    'folder holds more than one.',
]

PADS_BOM = ['BillOfMaterials*.txt', 'BillOfMaterials*.ods', 'BillOfMaterials*.xlsx',
            'BillOfMaterials*.xls', '*[Pp]art[_ ]?[Ll]ist*.ods',
            '*[Pp]art[_ ]?[Ll]ist*.xls', '*[Pp]art[_ ]?[Ll]ist*.xlsx']


def when(path):
    return time.strftime('%d.%m. %H:%M', time.localtime(os.path.getmtime(path)))


def check_kicad(folder):
    """What the folder holds for a KiCad import: [(state, text), ...] and
    whether it can be read."""
    out = []
    boards = sorted(glob.glob(os.path.join(folder, '*.kicad_pcb')))
    if not boards:
        out.append(('bad', 'No .kicad_pcb here -- choose the folder the board '
                           'file is in.'))
        return out, False
    out.append(('ok', 'Board: %s' % ', '.join(os.path.basename(b) for b in boards)))
    exports = sorted(glob.glob(os.path.join(folder, 'production', '*positions.csv')),
                     key=os.path.getmtime, reverse=True)
    if not exports:
        out.append(('bad', 'No Fabrication Toolkit export yet (production/'
                           '...positions.csv). Run it as described above.'))
        return out, False
    positions = exports[0]
    stem = os.path.basename(positions)[:-len('positions.csv')].rstrip('_')
    bom = os.path.join(os.path.dirname(positions), (stem + '_bom.csv') if stem else 'bom.csv')
    out.append(('ok', 'Placement: production/%s (%s)'
                % (os.path.basename(positions), when(positions))))
    if len(exports) > 1:
        out.append(('info', '%d exports in production/; the newest is used.' % len(exports)))
    if os.path.isfile(bom):
        out.append(('ok', 'BOM: production/%s' % os.path.basename(bom)))
    else:
        out.append(('bad', 'No %s beside the placement file -- run the export '
                           'again.' % os.path.basename(bom)))
        return out, False
    if len(boards) > 1:
        import kicad2yy1 as k2y
        log = []
        try:
            chosen = k2y.pick_board(folder, positions, bom, log)
            out.append(('info', '%d boards here; the export matches %s, which '
                                'is used.' % (len(boards), os.path.basename(chosen))))
        except SystemExit:
            chosen = boards[0]
    else:
        chosen = boards[0]
    if os.path.getmtime(chosen) > os.path.getmtime(positions) + 60:
        out.append(('warn', '%s was saved after the export (%s). If the board '
                            'changed, run Fabrication Toolkit again.'
                    % (os.path.basename(chosen), when(chosen))))
    options = os.path.join(os.path.dirname(chosen), 'fabrication-toolkit-options.json')
    if os.path.isfile(options):
        try:
            with open(options, encoding='utf-8') as handle:
                auto = json.load(handle).get('AUTO TRANSLATE')
        except (IOError, OSError, ValueError):
            auto = None
        if auto is True:
            out.append(('ok', 'Automatic component translations: ticked, as '
                              'for the board tested on the machine.'))
        elif auto is False:
            out.append(('warn', 'Automatic component translations were off. '
                                'the tested board ran on the machine with them on -- '
                                'tick it and export again, or step the '
                                'SOT-23, SOIC and TSSOP parts first.'))
    return out, True


def check_pads(folder):
    """What the folder holds for a PADS import."""
    out = []
    ascs = p2y.find(folder, ['*.asc'])
    layout = []
    for path in ascs:
        try:
            with open(path, encoding='cp1250', errors='replace') as handle:
                head = handle.readline()
        except (IOError, OSError):
            continue
        if head.startswith('!PADS-POWERPCB'):
            layout.append(path)
    placing = p2y.find(folder, ['*COMPPLACING*.xlsx', '*[Pp]lacement*.xlsx'])
    if layout:
        out.append(('ok', 'Layout export: %s (%s)'
                    % (os.path.relpath(layout[0], folder), when(layout[0]))))
    elif ascs:
        out.append(('bad', '%s is not a PADS Layout export. Export again from '
                           'PADS Layout (not Logic).' % os.path.basename(ascs[0])))
    if placing:
        out.append(('ok', 'Placement report: %s' % os.path.relpath(placing[0], folder)))
    if not layout and not placing:
        out.append(('bad', 'No placement export here. PADS keeps positions in '
                           'the binary .pcb, so they have to be exported (step 1 '
                           'above).'))
        return out, False
    bom = p2y.find(folder, PADS_BOM)
    logic = [p for p in p2y.find(folder, ['*.txt']) if p2y.is_logic_export(p)]
    if bom:
        out.append(('ok', 'BOM: %s' % os.path.relpath(bom[0], folder)))
    if logic:
        out.append(('ok', 'Logic export (values): %s' % os.path.relpath(logic[0], folder)))
    if not bom and not logic:
        out.append(('info', 'No BOM or Logic export: values come from the '
                            'Layout export\'s attributes, which is usually '
                            'enough.'))
    if not layout:
        out.append(('warn', 'Only the placement report: it has no board '
                            'outline, so the board size is estimated.'))
    return out, True


def check_altium(folder):
    """What the folder holds for an Altium import."""
    out = []
    boards = a2y.boards_in(folder)
    places = a2y.pick_places_in(folder)
    if boards:
        out.append(('ok', 'Board: %s (%s)' % (os.path.basename(boards[0]), when(boards[0]))))
        if len(boards) > 1:
            out.append(('info', '%d boards here; the newest is used -- pick the '
                                '.PcbDoc itself to choose: %s'
                        % (len(boards), ', '.join(os.path.basename(b) for b in boards[1:]))))
        try:
            comps, points = a2y.read_pcbdoc(boards[0])
        except Exception as exc:
            out.append(('bad', 'The board could not be read: %s' % exc))
            return out, False
        named = sum(1 for c in comps if c['comment'])
        out.append(('ok', '%d component(s), %d with a Comment; outline with %d vertices'
                    % (len(comps), named, len(points))))
        if not points:
            out.append(('warn', 'No board outline in the file -- the board size '
                                'will be estimated from the parts.'))
        if named < len(comps):
            out.append(('warn', '%d part(s) have no Comment -- their library '
                                'reference stands in; a BOM fixes that.'
                        % (len(comps) - named)))
    elif places:
        out.append(('warn', 'No .PcbDoc here; the pick-and-place export %s is used '
                            'on its own (no outline, no through-hole check).'
                    % os.path.relpath(places[0], folder)))
    else:
        out.append(('bad', 'No .PcbDoc here -- choose the folder the board is in.'))
        return out, False
    if places and boards:
        out.append(('ok', 'Pick-and-place export: %s' % os.path.relpath(places[0], folder)))
    boms = [b for b in a2y.boms_in(folder) if a2y.read_bom(b)]
    if boms:
        out.append(('ok', 'BOM: %s' % os.path.relpath(boms[0], folder)))
    else:
        out.append(('info', 'No BOM with Comment and Designator columns; values '
                            'come from the board.'))
    return out, True


CHECKS = {'kicad': check_kicad, 'pads': check_pads, 'altium': check_altium}


class Guide(tk.Toplevel):

    def __init__(self, app):
        tk.Toplevel.__init__(self, app)
        self.app = app
        # the window module, whichever name it runs under
        self.G = sys.modules[type(app).__module__]
        px = self.G.px
        self.title('Guide')
        self.transient(app)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.step = 0
        self._stale = False
        self.source = tk.StringVar(value=app.setting('guide_source', 'kicad'))
        folder = ''
        if app.result:
            folder = app.result.project
            self.source.set(getattr(app.result, 'source', '') or
                            ('kicad' if app.result.pcb else 'pads'))
        self.folder = tk.StringVar(value=folder)
        self.head_mode = tk.StringVar(value=app.setting('guide_head_mode', 'fixed'))
        heads = app.setting('guide_heads', {}) or {}
        self.head1 = tk.StringVar(value=heads.get('head1', EMPTY))
        self.head2 = tk.StringVar(value=heads.get('head2', EMPTY))
        stations = list(heads.get('station', [])) + [EMPTY] * 3
        self.stations = [tk.StringVar(value=stations[i]) for i in range(3)]
        self.match_mode = tk.StringVar(value=app.setting('guide_match', 'match'))
        self.at_start = tk.BooleanVar(value=bool(app.setting('guide_at_start', True)))
        self.setup_report = None
        self.export_dir = None
        self._build(px)
        self._place(px)
        self.bind('<FocusIn>', self._focus)
        self.show(1 if app.result else 0)

    # -- frame
    def _build(self, px):
        G = self.G
        outer = ttk.Frame(self, padding=(0, 0, 0, 0))
        outer.pack(fill='both', expand=True)

        steps = tk.Frame(outer, background='#eef2f7', padx=px(8), pady=px(10))
        steps.pack(side='left', fill='y')
        tk.Label(steps, text='New job', background='#eef2f7', foreground='#0b3d91',
                 font=('', 11, 'bold')).pack(anchor='w', pady=(0, px(8)))
        self.step_labels = []
        for i, name in enumerate(STEPS):
            label = tk.Label(steps, text='', anchor='w', justify='left',
                             background='#eef2f7', padx=px(6), pady=px(5),
                             cursor='hand2', width=18)
            label.pack(fill='x', pady=1)
            label.bind('<Button-1>', lambda e, i=i: self.show(i))
            self.step_labels.append(label)

        right = ttk.Frame(outer, padding=(px(12), px(10), px(12), px(8)))
        right.pack(side='left', fill='both', expand=True)
        self.heading = ttk.Label(right, text='', font=('', 13, 'bold'))
        self.heading.pack(anchor='w')
        foot = ttk.Frame(right, padding=(0, px(8), 0, 0))
        foot.pack(side='bottom', fill='x')
        ttk.Checkbutton(foot, text='Open at start', variable=self.at_start,
                        command=lambda: self.app.set_setting(
                            'guide_at_start', bool(self.at_start.get()))).pack(side='left')
        self.next_button = ttk.Button(foot, text='Next  >', command=self.next)
        self.next_button.pack(side='right')
        self.back_button = ttk.Button(foot, text='<  Back', command=self.back)
        self.back_button.pack(side='right', padx=px(6))
        ttk.Separator(right).pack(side='bottom', fill='x', pady=(px(6), 0))
        holder = ttk.Frame(right)
        holder.pack(side='top', fill='both', expand=True, pady=(px(6), 0))
        self.body = G.scrollable(holder, padding=(0, 0, px(8), 0))
        self.body.master.bind('<Configure>', self._resized, add='+')

    def _place(self, px):
        app = self.app
        app.update_idletasks()
        width = px(600)
        height = min(px(720), self.winfo_screenheight() - px(90))
        x = app.winfo_rootx() + app.winfo_width() + px(8)
        if x + width > self.winfo_screenwidth():
            x = max(0, app.winfo_rootx() + app.winfo_width() - width - px(24))
        y = max(0, app.winfo_rooty() + px(40))
        self.geometry('%dx%d+%d+%d' % (width, height, x, y))
        self.minsize(px(460), px(420))

    def close(self):
        self.app.guide = None
        self.destroy()

    def mark_stale(self):
        """The main window changed something; redraw the step next time the
        guide is looked at."""
        self._stale = True

    def _focus(self, event):
        if self._stale:
            self._stale = False
            self.after_idle(lambda: self.show(self.step))

    # -- helpers for the steps
    def px(self, value):
        return self.G.px(value)

    def wrap(self, indent=0):
        """Text width for the space the step has now, less an indent (in
        unscaled pixels) for boxes and bullets."""
        width = self.body.master.winfo_width()
        if width < 100:
            width = self.px(380)
        return max(self.px(200), width - self.px(20 + indent))

    def _resized(self, event):
        # re-wrap the text once the guide has settled at a new width
        if abs(event.width - getattr(self, '_wrapped_at', 0)) > self.px(24):
            self._wrapped_at = event.width
            if getattr(self, '_rewrap', None):
                self.after_cancel(self._rewrap)
            self._rewrap = self.after(150, lambda: self.show(self.step))

    def para(self, text, colour='#333', parent=None, bold=False, pad=4):
        label = ttk.Label(parent or self.body, text=text, foreground=colour,
                          wraplength=self.wrap(0 if parent is None else 30), justify='left',
                          font=('', 10, 'bold') if bold else None)
        label.pack(anchor='w', fill='x', pady=(0, self.px(pad)))
        return label

    def section(self, title):
        frame = ttk.LabelFrame(self.body, text=title, padding=self.px(8))
        frame.pack(fill='x', pady=(self.px(4), self.px(8)))
        return frame

    def checklist(self, parent, items):
        for state, text in items:
            row = ttk.Frame(parent)
            row.pack(fill='x', anchor='w', pady=1)
            sign, colour = MARK[state]
            tk.Label(row, text=sign, foreground=colour, font=('', 11, 'bold'),
                     width=2, anchor='n').pack(side='left', anchor='n')
            ttk.Label(row, text=text, wraplength=self.wrap(60), justify='left',
                      foreground='#b00020' if state == 'bad' else '#222').pack(
                side='left', anchor='w', fill='x')

    def need_board(self):
        if self.app.result:
            return False
        self.para('Load a board first (step 1).', '#b00020', bold=True)
        return True

    def main_tab(self, name):
        try:
            self.app.book.select(self.app.tabs[name])
        except (tk.TclError, KeyError):
            pass

    # -- navigation
    def show(self, step):
        if step > 0 and not self.app.result:
            step = 0
        self.step = step
        for child in self.body.winfo_children():
            child.destroy()
        canvas = self.body.master
        canvas.yview_moveto(0)
        for i, label in enumerate(self.step_labels):
            done = i < step and self.app.result is not None
            current = i == step
            label.configure(text='%s  %d  %s' % ('✓' if done else ' ', i + 1, STEPS[i]),
                            background='#cfe0f7' if current else '#eef2f7',
                            foreground='#0b3d91' if current else
                            ('#1b7a1b' if done else
                             ('#999' if i > 0 and not self.app.result else '#333')),
                            font=('', 10, 'bold') if current else ('', 10))
        self.heading.configure(text='%d. %s' % (step + 1, STEPS[step]))
        self.back_button.configure(state='normal' if step > 0 else 'disabled')
        self.next_button.configure(text='Next  >', state='normal')
        [self.step_files, self.step_board, self.step_heads, self.step_feeders,
         self.step_export][step]()
        self._stale = False

    def back(self):
        if self.step > 0:
            self.show(self.step - 1)

    def next(self):
        if self.step == 0:
            if not self.load():
                return
            self.show(1)
        elif self.step == 2 and self.setup_report is None and not messagebox.askyesno(
                'Guide', 'Set up board has not run in this guide. Carry on '
                         'with the setup the board has now?', parent=self):
            return
        elif self.step == len(STEPS) - 1:
            self.close()
        else:
            self.show(self.step + 1)

    # ---------------------------------------------------------------- 1
    def step_files(self):
        app = self.app
        self.para('Where does the board come from?', bold=True)
        row = ttk.Frame(self.body)
        row.pack(anchor='w', pady=(0, self.px(6)))
        for value, label in (('kicad', 'KiCad'), ('pads', 'PADS Layout'),
                             ('altium', 'Altium')):
            ttk.Radiobutton(row, text=label, value=value, variable=self.source,
                            command=lambda: (self.app.set_setting(
                                'guide_source', self.source.get()),
                                self.show(0))).pack(side='left', padx=(0, 16))

        how = self.section('What to export')
        for i, line in enumerate(HOWTO[self.source.get()], start=1):
            r = ttk.Frame(how)
            r.pack(fill='x', anchor='w', pady=1)
            ttk.Label(r, text='%d.' % i, width=3, anchor='ne').pack(side='left', anchor='n')
            ttk.Label(r, text=line, wraplength=self.wrap(60), justify='left').pack(
                side='left', anchor='w')

        pick = self.section('The folder')
        r = ttk.Frame(pick)
        r.pack(fill='x')
        entry = ttk.Entry(r, textvariable=self.folder)
        entry.pack(side='left', fill='x', expand=True)
        entry.bind('<Return>', lambda e: self.show(0))
        ttk.Button(r, text='Browse…', command=self.browse).pack(side='left', padx=(6, 0))
        if self.source.get() in ('kicad', 'altium'):
            ttk.Button(pick, text='…or pick the %s itself'
                             % ('.kicad_pcb' if self.source.get() == 'kicad' else '.PcbDoc'),
                       command=self.browse_board).pack(anchor='w', pady=(self.px(4), 0))

        folder = self.folder.get().strip()
        ready = False
        if folder and os.path.isdir(folder):
            check = self.section('What is there')
            items, ready = CHECKS[self.source.get()](folder)
            self.checklist(check, items)
            ttk.Button(check, text='Check again', command=lambda: self.show(0)).pack(
                anchor='e', pady=(self.px(4), 0))
        elif folder:
            self.para('That folder does not exist.', '#b00020')

        if app.result:
            same = folder and os.path.abspath(folder) == os.path.abspath(app.result.project)
            self.para('Open now: %s (%s)%s' % (
                app.result.prefix, app.result.project,
                ' -- Next carries on with it; "Read again" picks up a new export.'
                if same else ''), '#0b3d91')
            if same:
                ttk.Button(self.body, text='Read again',
                           command=lambda: self.load(force=True) and self.show(1)).pack(anchor='w')
        self.next_button.configure(
            text='Read the board  >' if folder and not (
                app.result and os.path.abspath(folder) == os.path.abspath(app.result.project))
            else 'Next  >',
            state='normal' if ready or app.result else 'disabled')

    def browse(self):
        path = filedialog.askdirectory(parent=self, title='The board\'s folder',
                                       initialdir=self.folder.get() or None)
        if path:
            self.folder.set(path)
            self.show(0)

    def browse_board(self):
        kicad = self.source.get() == 'kicad'
        path = filedialog.askopenfilename(
            parent=self, title='KiCad board' if kicad else 'Altium board',
            filetypes=[('KiCad board', '*.kicad_pcb') if kicad else ('Altium board', '*.PcbDoc')],
            initialdir=self.folder.get() or None)
        if path:
            self._board_file = path
            self.folder.set(os.path.dirname(path))
            self.show(0)

    def load(self, force=False):
        """Read the folder into the main window. True when a board is open."""
        app = self.app
        folder = self.folder.get().strip()
        if not folder:
            return app.result is not None
        if (not force and app.result and
                os.path.abspath(folder) == os.path.abspath(app.result.project)):
            return True
        if app._dirty and app.result and not messagebox.askyesno(
                'Guide', 'The board open now has unsaved changes. Read the '
                         'new one anyway? (Cancel and press Save profile to '
                         'keep them.)', parent=self):
            return False
        before = app.result
        board = getattr(self, '_board_file', None)
        if not (board and os.path.normcase(os.path.dirname(board)) == os.path.normcase(folder)):
            board = None
        if self.source.get() == 'kicad':
            app.open_project(board or folder)
        elif self.source.get() == 'altium':
            app.open_conversion(board or folder, 'altium')
        else:
            app.open_pads(folder)
        self.setup_report = None
        self.export_dir = None
        ok = app.result is not None and app.result is not before
        if ok:
            self.main_tab('board')
        return ok

    # ---------------------------------------------------------------- 2
    def step_board(self):
        app = self.app
        if self.need_board():
            return
        self.main_tab('board')
        result = app.result
        profile = result.profile
        facts = self.section('What was read')
        top, bottom = len(result.sides['T']), len(result.sides['B'])
        side = app.side.get()
        groups = result.type_rows(side)
        items = [('ok', '%s, %.1f × %.1f mm%s' % (
                     result.prefix, result.width, result.height,
                     ' (estimated -- no outline in the export)'
                     if getattr(result, 'board_estimated', False) else '')),
                 ('ok', '%d part(s) on top, %d on the bottom' % (top, bottom)),
                 ('ok', '%d reel(s) on the %s side' % (len(groups),
                                                     'top' if side == 'T' else 'bottom'))]
        reasons = {}
        for ref, why in result.dropped:
            reasons.setdefault(why.split(' -- ')[0], []).append(ref)
        for why, refs in sorted(reasons.items(), key=lambda r: -len(r[1])):
            items.append(('info', 'Left out, %s: %s' % (why, ', '.join(sorted(refs)[:10]) +
                                                       (' …' if len(refs) > 10 else ''))))
        blank = sorted(k for k in groups if not k.split('|', 1)[1])
        if blank:
            items.append(('warn', '%d reel(s) with no value -- every part of one '
                                  'footprint would share a reel: %s. Add a BOM '
                                  '(PADS) or fill the values in the CAD tool.'
                          % (len(blank), ', '.join(k.split('|')[0] for k in blank[:6]))))
        if profile.rot_offset:
            items.append(('info', 'Rotation offset %+g° is applied to every part.'
                          % profile.rot_offset))
        self.checklist(facts, items)

        if top and bottom:
            pick = self.section('Which side now')
            r = ttk.Frame(pick)
            r.pack(anchor='w')
            for value, label in (('T', 'Top (%d parts)' % top),
                                 ('B', 'Bottom (%d parts)' % bottom)):
                ttk.Radiobutton(r, text=label, value=value, variable=app.side,
                                command=lambda: (app.switch_side(), self.show(1))).pack(
                    side='left', padx=(0, 16))
            self.para('One file per side. Do the other side as a second job.',
                      '#666', parent=pick)

        fid = self.section('Fiducial')
        job = profile.job
        if float(job.fid_x or 0) or float(job.fid_y or 0):
            self.checklist(fid, [('ok', 'Fiducial 1 at %.2f, %.2f mm -- the machine '
                                        'finds it first and corrects the board\'s '
                                        'position.' % (float(job.fid_x), float(job.fid_y)))])
        else:
            marks = [m for m in getattr(app, '_fid_marks', []) or []]
            if marks:
                self.checklist(fid, [('warn', 'No fiducial set yet. The board has '
                                              '%d mark(s):' % len(marks))])
                r = ttk.Frame(fid)
                r.pack(anchor='w', pady=(self.px(4), 0))
                names = ['%s  (%.1f, %.1f)' % (m[0], m[1], m[2]) for m in marks]
                box = ttk.Combobox(r, values=names, state='readonly', width=28)
                box.current(0)
                box.pack(side='left')

                def use():
                    app.fid_pick.current(box.current())
                    app.use_fiducial()
                    self.show(1)
                ttk.Button(r, text='Use it', command=use).pack(side='left', padx=6)
            else:
                self.checklist(fid, [('warn', 'No fiducial mark on the board. On the '
                                              'Board tab click a part with a crisp '
                                              'outline and press "Set as fiducial 1", '
                                              'or run without fiducial correction.')])

    # ---------------------------------------------------------------- 3
    def step_heads(self):
        app = self.app
        if self.need_board():
            return
        if self.setup_report is None:
            self.main_tab('nozzles')
        self.para('Set up board picks a nozzle for every reel, keeps the file to '
                  'the nozzles that fit, and plans the nozzle changes (at most '
                  'four). Tell it what it has to work with.', '#444')
        mode = self.section('Nozzles')
        ttk.Radiobutton(mode, text='What is on the machine now (the heads and the '
                                   'station)', value='fixed', variable=self.head_mode,
                        command=lambda: self.show(2)).pack(anchor='w')
        ttk.Radiobutton(mode, text='Let the planner choose from my nozzle drawer',
                        value='drawer', variable=self.head_mode,
                        command=lambda: self.show(2)).pack(anchor='w')

        try:
            positions = max(2, min(4, int(app.station_var.get())))
        except ValueError:
            positions = yy1.STATION_POSITIONS
        choices = [EMPTY] + yy1.NOZZLE_CHOICES
        if self.head_mode.get() == 'fixed':
            box = self.section('On the machine now')
            grid = ttk.Frame(box)
            grid.pack(anchor='w')
            rows = [('Head 1', self.head1), ('Head 2', self.head2)]
            rows += [('Station %d' % (i + 1), self.stations[i])
                     for i in range(positions - 1)]
            for r, (label, var) in enumerate(rows):
                ttk.Label(grid, text=label).grid(row=r, column=0, sticky='w',
                                                 padx=(0, 8), pady=2)
                ttk.Combobox(grid, textvariable=var, values=choices, width=9,
                             state='readonly').grid(row=r, column=1, sticky='w')
            ttk.Label(grid, text='Station %d' % positions).grid(
                row=len(rows), column=0, sticky='w', padx=(0, 8), pady=2)
            ttk.Label(grid, text='kept empty -- the head drops a nozzle there',
                      foreground='#666').grid(row=len(rows), column=1, sticky='w')
            r = ttk.Frame(box)
            r.pack(anchor='w', pady=(self.px(6), 0))
            ttk.Label(r, text='Station positions').pack(side='left')
            ttk.Spinbox(r, from_=2, to=4, width=4, textvariable=app.station_var,
                        justify='center',
                        command=lambda: self.after_idle(lambda: self.show(2))).pack(
                side='left', padx=6)
            self.para('The plan uses these nozzles only. A part whose best nozzle '
                      'is not here goes to the next smaller one loaded; what none '
                      'of them can pick is set to Skip.', '#666', parent=box)
        else:
            box = self.section('Nozzles you have')
            ttk.Checkbutton(box, text='Plan only with these', variable=app.limit_var,
                            command=app.save_inventory).pack(anchor='w')
            grid = ttk.Frame(box)
            grid.pack(anchor='w', pady=(self.px(4), 0))
            for i, nozzle in enumerate(yy1.NOZZLE_CHOICES):
                r, c = i // 4, (i % 4) * 2
                ttk.Label(grid, text=nozzle).grid(row=r, column=c, sticky='e',
                                                  padx=(6, 2), pady=1)
                ttk.Spinbox(grid, from_=0, to=4, width=3, justify='center',
                            textvariable=app.stock_vars[nozzle],
                            command=app.save_inventory).grid(row=r, column=c + 1,
                                                             sticky='w')
            r = ttk.Frame(box)
            r.pack(anchor='w', pady=(self.px(6), 0))
            ttk.Label(r, text='Station positions').pack(side='left')
            ttk.Spinbox(r, from_=2, to=4, width=4, textvariable=app.station_var,
                        justify='center').pack(side='left', padx=6)

        run = ttk.Frame(self.body)
        run.pack(fill='x', pady=(self.px(4), self.px(6)))
        ttk.Button(run, text='  Set up board  ', style='Accent.TButton',
                   command=self.run_setup).pack(side='left')
        ttk.Label(run, text='  nothing is saved until Export or Save profile',
                  foreground='#666').pack(side='left')

        if self.setup_report:
            out = self.section('Result')
            text = tk.Text(out, height=min(16, len(self.setup_report) + 1), wrap='word',
                           font=(self.G.fixed_family(), 9), relief='flat',
                           background='#fafafa')
            text.pack(fill='x')
            text.tag_configure('warn', foreground='#b00020')
            for line in self.setup_report:
                text.insert('end', line + '\n',
                            'warn' if line.startswith('WARNING') else '')
            text.configure(state='disabled')
            self.para('The Board tab shows every part coloured by nozzle. '
                      'Details and manual changes: Order & nozzles tab.', '#666')

    def fixed_homes(self):
        """{nozzle: home} and {nozzle: count} from what is on the machine."""
        try:
            positions = max(2, min(4, int(self.app.station_var.get())))
        except ValueError:
            positions = yy1.STATION_POSITIONS
        pick = lambda v: '' if v.get() in ('', EMPTY) else v.get()
        h1, h2 = pick(self.head1), pick(self.head2)
        homes, counts, notes = {}, {}, []
        if h1 and h1 == h2:
            homes[h1], counts[h1] = ('head', 0), 2
        else:
            for n, slot in ((h1, 1), (h2, 2)):
                if n:
                    homes[n], counts[n] = ('head', slot), 1
        for i in range(positions - 1):
            n = pick(self.stations[i])
            if not n:
                continue
            if n in homes:
                notes.append('%s is on a head already; the plan can use one '
                             'place per nozzle, so station %d is ignored' % (n, i + 1))
                counts[n] = counts.get(n, 0) + 1
                continue
            homes[n], counts[n] = ('station', i + 1), counts.get(n, 0) + 1
        return homes, counts, notes

    def run_setup(self):
        app = self.app
        if self.head_mode.get() == 'fixed':
            homes, counts, notes = self.fixed_homes()
            if not homes:
                messagebox.showinfo('Guide', 'Choose the nozzle on at least one '
                                             'head.', parent=self)
                return
            planned = app.prepare_setup(homes=homes, available=counts)
            app.set_setting('guide_heads', {
                'head1': self.head1.get(), 'head2': self.head2.get(),
                'station': [v.get() for v in self.stations]})
        else:
            notes = []
            planned = app.prepare_setup(homes={})
        app.set_setting('guide_head_mode', self.head_mode.get())
        if not planned:
            return
        trial, report = planned
        app.apply_setup(trial, report)
        self.setup_report = ['note: ' + n for n in notes] + report
        self.main_tab('board')
        self.show(2)

    # ---------------------------------------------------------------- 4
    def step_feeders(self):
        app = self.app
        if self.need_board():
            return
        self.main_tab('bank')
        lib = app.library
        box = self.section('Feeder list (the reels that stay on the machine)')
        r = ttk.Frame(box)
        r.pack(fill='x')
        if lib:
            ttk.Label(r, text='%s: %d reels, %d free slots'
                      % (os.path.basename(lib.path), len(lib.loaded()), len(lib.free()))).pack(
                side='left')
        else:
            ttk.Label(r, text='No list loaded -- reels are numbered 1…n instead.',
                      foreground='#b35900').pack(side='left')
        ttk.Button(r, text='Choose list…', command=lambda: (app.load_library(),
                                                            self.show(3))).pack(side='right')

        if lib:
            how = self.section('How to match')
            ttk.Radiobutton(how, text='Use the reels on the machine; load the rest '
                                      'into free slots', value='match',
                            variable=self.match_mode).pack(anchor='w')
            ttk.Radiobutton(how, text='Only the reels on the machine; the rest by '
                                      'hand', value='only',
                            variable=self.match_mode).pack(anchor='w')
            ttk.Checkbutton(how, text='Skipped parts out of CSV', variable=app.omit_var,
                            command=app.omit_changed).pack(anchor='w', pady=(self.px(4), 0))
            r = ttk.Frame(how)
            r.pack(fill='x', pady=(self.px(6), 0))
            ttk.Button(r, text='Match with the list', command=self.run_match).pack(side='left')
            sheet = ttk.Button(r, text='Open loading sheet', command=app.open_loading_sheet)
            sheet.pack(side='left', padx=6)
            if not getattr(app, '_loading_sheet', None):
                sheet.configure(state='disabled')

        self.feeder_summary()
        self.para('To change a reel\'s slot: on the Feeder bank tab click the reel '
                  'in the right-hand table, then a slot, then "Put … into slot". '
                  'Right-click a slot to take a reel out, or to fix what the list '
                  'says is there.', '#666')

    def run_match(self):
        app = self.app
        app.set_setting('guide_match', self.match_mode.get())
        app.bank_match(list_only=self.match_mode.get() == 'only', ask=False)
        self.show(3)

    def feeder_summary(self):
        app = self.app
        state = app._bank_state()
        load, differs, clash, used = [], [], [], 0
        for slot, (row, keys, s) in sorted(state.items()):
            if s == 'used':
                used += 1
            elif s == 'load':
                load.append((slot, keys[0]))
            elif s == 'differs':
                differs.append((slot, row[2], keys[0]))
            elif s == 'clash':
                clash.append((slot, keys))
        noslot, notlisted, hand = app._bank_missing()
        box = self.section('This board on the machine')
        items = []
        if app.library:
            items.append(('ok', '%d reel(s) already on the machine' % used))
        if load:
            items.append(('warn', 'Load %d reel(s): %s' % (len(load), ', '.join(
                'slot %d ← %s' % (slot, key.replace('|', ' ')) for slot, key in load))))
        for slot, listed, key in differs:
            items.append(('warn', 'Slot %d holds %s on the list; this board wants %s '
                                  'there -- swap the reel' % (slot, listed,
                                                              key.replace('|', ' '))))
        for slot, keys in clash:
            items.append(('bad', 'Slot %d has two reels: %s' % (
                slot, ', '.join(k.replace('|', ' ') for k in keys))))
        by_hand = noslot + notlisted + hand
        if by_hand:
            parts = sum(len(app.result.type_rows(app.side.get())[k]) for k in by_hand)
            items.append(('info', 'By hand: %d reel(s), %d part(s) -- %s' % (
                len(by_hand), parts, ', '.join(k.replace('|', ' ') for k in by_hand))))
        if not items:
            items.append(('info', 'Nothing to report.'))
        self.checklist(box, items)

    # ---------------------------------------------------------------- 5
    def step_export(self):
        app = self.app
        if self.need_board():
            return
        self.main_tab('feeders')
        self.next_button.configure(text='Finish')
        result = app.result
        profile = result.profile
        side = app.side.get()
        states = app.reel_states()
        items = []
        problems = sorted((k, why) for k, (tag, why) in states.items() if tag == 'problem')
        todo = sorted(k for k, (tag, why) in states.items() if tag == 'unassigned')
        for key, why in problems[:8]:
            items.append(('bad', '%s: %s' % (key.replace('|', ' '), why)))
        if len(problems) > 8:
            items.append(('bad', '… and %d more red rows on the Feeders tab'
                          % (len(problems) - 8)))
        if todo:
            items.append(('warn', '%d reel(s) still on the default slot: %s' % (
                len(todo), ', '.join(k.replace('|', ' ') for k in todo[:6]))))
        if not problems and not todo:
            items.append(('ok', 'Every reel has a slot and a loaded nozzle.'))

        import kicad2yy1 as k2y
        changes, trouble, rows, _ = yy1.side_changes(k2y.ordered(result, side), profile, side)
        planned = profile.changes_for(side) or []
        if not profile.homes_for(side):
            items.append(('warn', 'No nozzle plan for this side yet -- run Set up '
                                  'board (step 3).'))
        else:
            if len(planned) > 4:
                items.append(('bad', '%d nozzle changes -- the machine holds four. '
                                     'Skip some reels for a second pass.' % len(planned)))
            else:
                items.append(('ok', '%d nozzle change(s) in the file' % len(planned)
                              if planned else 'No nozzle change needed'))
            for line in trouble:
                items.append(('bad', line))
            if [tuple(c) for c in changes] != [tuple(c) for c in planned]:
                items.append(('warn', 'The nozzle change rows no longer fit the '
                                      'file. Run Set up board again (step 3).'))

        job = profile.job
        if float(job.fid_x or 0) or float(job.fid_y or 0):
            items.append(('ok', 'Fiducial 1 at %.2f, %.2f mm' % (float(job.fid_x),
                                                               float(job.fid_y))))
        else:
            items.append(('warn', 'No fiducial -- placed straight from the file '
                                  '(step 2).'))
        skipped = [k for k, (tag, _) in states.items() if tag == 'skipped']
        if skipped:
            count = sum(len(result.type_rows(side)[k]) for k in skipped)
            items.append(('info', '%d reel(s), %d part(s) by hand%s' % (
                len(skipped), count, ' -- left out of the CSV' if profile.omit_skipped
                else ' -- in the CSV with Skip = 1')))
        items.append(('info', '%d row(s) in the file for the %s side'
                      % (len(rows), 'top' if side == 'T' else 'bottom')))
        box = self.section('Before the machine')
        self.checklist(box, items)
        if problems or todo:
            ttk.Button(box, text='Show them on the Feeders tab',
                       command=lambda: self.main_tab('feeders')).pack(
                anchor='e', pady=(self.px(4), 0))

        out = self.section('Export')
        self.para('Writes the CSV for the machine, the operator sheet (feeders and '
                  'nozzle changes, .html to print) and a copy of the profile.',
                  '#444', parent=out)
        r = ttk.Frame(out)
        r.pack(fill='x', pady=(self.px(4), 0))
        ttk.Button(r, text='Export next to the board',
                   command=lambda: self.run_export(None)).pack(side='left')
        ttk.Button(r, text='Export to SD card…',
                   command=self.export_to_card).pack(side='left', padx=6)
        if self.export_dir:
            sheet = self.sheet_path()
            self.checklist(out, [('ok', 'Written to %s' % self.export_dir)])
            r = ttk.Frame(out)
            r.pack(fill='x', pady=(self.px(4), 0))
            b = ttk.Button(r, text='Open operator sheet',
                           command=lambda: self.open_file(self.sheet_path()))
            b.pack(side='left')
            if not (sheet and os.path.isfile(sheet)):
                b.configure(state='disabled')
            ttk.Button(r, text='Open folder',
                       command=lambda: self.open_file(self.export_dir)).pack(side='left', padx=6)
        self.para('On the machine: load the reels, check the nozzles, then Step '
                  'the first parts of each reel before running the board (manual '
                  'p.7). Step a polarised part first.', '#666')

    def run_export(self, folder):
        written = self.app.export(folder)
        if written:
            self.written = [path for path, _ in written]
            self.export_dir = os.path.dirname(self.written[0])
        self.show(4)

    def export_to_card(self):
        folder = filedialog.askdirectory(parent=self,
                                         title='Where to write (the SD card, for instance)')
        if folder:
            self.run_export(folder)

    def sheet_path(self):
        """The operator sheet written for the side on screen."""
        label = '_%s.csv' % ('top' if self.app.side.get() == 'T' else 'bottom')
        for path in getattr(self, 'written', []):
            if path.endswith(label):
                return path[:-4] + '_setup.html'
        return None

    def open_file(self, path):
        if path and os.path.exists(path):
            try:
                os.startfile(path)
            except (AttributeError, OSError):
                import webbrowser
                webbrowser.open('file:///' + os.path.abspath(path).replace('\\', '/'))
