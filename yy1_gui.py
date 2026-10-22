#!/usr/bin/env python3
"""Desktop front end for the KiCad -> NeoDen YY1 converter.

The point of this window is the Feeders table. The YY1 groups parts into feeder
types by Comment + Footprint, so that table has one row per reel you will load,
and every column the machine reads is editable in place: feeder slot, head,
mount speed, pick and place height, recognition mode, skip, and a per-type
rotation offset. Click a cell, type, press Enter.

Whatever you set is written to a JSON profile beside the board, keyed by part
type rather than by designator -- so re-exporting from KiCad after a schematic
change keeps every feeder assignment you have already made.

    python yy1_gui.py                 # then File > Open KiCad project
    python yy1_gui.py path/to/project  # or straight into a board

Standard library only (tkinter), so it packages into a small exe.
"""

import ctypes
import math
import os
import re
import sys
import traceback

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from tkinter import font as tkfont
except ImportError:                                    # pragma: no cover
    sys.exit('error: this needs tkinter (python3-tk on Linux)')


# Windows stretches the bitmap of a DPI-unaware window, which on a laptop
# panel at 175%% leaves the tables soft and oversized. Claim awareness before
# the first window exists, then scale fonts and pixel widths to match.
HIDPI = 1.0


def claim_dpi_awareness():
    if not sys.platform.startswith('win'):
        return
    for call in (lambda: ctypes.windll.shcore.SetProcessDpiAwareness(1),
                 lambda: ctypes.windll.user32.SetProcessDPIAware()):
        try:
            call()
            return
        except (AttributeError, OSError):
            continue


def tune_for_dpi(root):
    global HIDPI
    dpi = root.winfo_fpixels('1i')
    HIDPI = max(1.0, dpi / 96.0)
    if HIDPI == 1.0:
        return
    root.tk.call('tk', 'scaling', dpi / 72.0)
    for name in ('TkDefaultFont', 'TkTextFont', 'TkHeadingFont', 'TkMenuFont',
                 'TkFixedFont', 'TkIconFont', 'TkSmallCaptionFont',
                 'TkCaptionFont', 'TkTooltipFont'):
        try:
            font = tkfont.nametofont(name)
        except tk.TclError:
            continue
        size = font.cget('size')
        if size < 0:                      # already in pixels; points scale
            font.configure(size=int(size * HIDPI))   # themselves via scaling
    ttk.Style().configure('Treeview', rowheight=int(24 * HIDPI))


def px(value):
    return int(round(value * HIDPI))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yy1lib as yy1
import kicad2yy1 as k2y
import pads2yy1 as p2y
import altium2yy1 as a2y
import yy1help as HELP

APP = 'NeoDen YY1 converter'

# (field, heading, width, anchor, editable)
TYPE_COLUMNS = [
    ('feeder', 'Feeder', 58, 'center', True),
    ('package', 'Footprint', 110, 'w', True),
    ('comment', 'Value', 120, 'w', True),
    ('qty', 'Qty', 40, 'center', False),
    ('nozzle', 'Nozzle', 62, 'center', True),
    ('head', 'Head', 48, 'center', True),
    ('speed', 'Speed%', 58, 'center', True),
    ('pick_h', 'Pick mm', 60, 'center', True),
    ('place_h', 'Place mm', 62, 'center', True),
    ('mode', 'Mode', 100, 'center', True),
    ('skip', 'Skip', 44, 'center', True),
    ('rot_offset', 'Rot +', 50, 'center', True),
    ('refs', 'Designators', 320, 'w', False),
]

PART_COLUMNS = [
    ('seq', '#', 50, 'e', False),
    ('ref', 'Designator', 96, 'w', False),
    ('package', 'Footprint', 140, 'w', False),
    ('comment', 'Value', 110, 'w', False),
    ('x', 'X mm', 62, 'e', False),
    ('y', 'Y mm', 62, 'e', False),
    ('rot', 'Rot', 52, 'e', False),
    ('feeder', 'Feeder', 58, 'center', True),
    ('nozzle', 'Nozzle', 62, 'center', False),
    ('head', 'Head', 48, 'center', True),
    ('speed', 'Speed%', 58, 'center', True),
    ('pick_h', 'Pick mm', 60, 'center', True),
    ('place_h', 'Place mm', 62, 'center', True),
    ('mode', 'Mode', 100, 'center', True),
    ('skip', 'Skip', 44, 'center', True),
]

ORDER_LABELS = [
    ('ref', 'Designator order',
     'C1, C10, C11, C2 … — what the machine-verified file used.'),
    ('feeder', 'Feeder by feeder',
     'All the parts off one reel, then the next. Least feeder travel.'),
    ('nozzle', 'Grouped by nozzle',
     'One unbroken run per nozzle. Needed before nozzle changes can work.'),
    ('custom', 'Custom order',
     'Whatever you arrange with Move up / Move down on the Parts tab.'),
]

NOZZLE_HOMES = ['Head 1', 'Head 2', 'Head 1+2', 'Station 1', 'Station 2',
                'Station 3', 'Station 4', 'not loaded']


def home_label(home):
    if not home:
        return 'not loaded'
    where, slot = home[0], int(home[1])
    if where == 'head':
        return 'Head 1+2' if slot == 0 else 'Head %d' % slot
    return 'Station %d' % slot


def home_value(label):
    if label.startswith('Head 1+2'):
        return ('head', 0)
    if label.startswith('Head'):
        return ('head', int(label.split()[1]))
    if label.startswith('Station'):
        return ('station', int(label.split()[1]))
    return None

MODE_CHOICES = ['%d %s' % (k, v) for k, v in sorted(yy1.MODES.items())]
HEAD_CHOICES = ['%d' % k for k in sorted(yy1.HEADS)]
INT_FIELDS = set(['feeder', 'head', 'speed', 'mode', 'skip'])
FLOAT_FIELDS = set(['pick_h', 'place_h', 'rot_offset'])

LIMITS = {'feeder': (0, 99), 'head': (0, 2), 'speed': (1, 100),
          'mode': (0, 4), 'skip': (0, 1)}


def parse_field(field, text):
    """Validate one cell. Returns the value, or raises ValueError."""
    text = str(text).strip()
    if field in INT_FIELDS:
        value = int(round(float(text)))
        low, high = LIMITS[field]
        if not low <= value <= high:
            raise ValueError('%s must be %d-%d' % (field, low, high))
        return value
    if field in FLOAT_FIELDS:
        return round(float(text), 3)
    return yy1.clean(text)


def fixed_family():
    for family in ('Consolas', 'DejaVu Sans Mono', 'Courier New'):
        if family in tkfont.families():
            return family
    return 'TkFixedFont'


def show_mode(value):
    return '%d %s' % (int(value), yy1.MODES.get(int(value), '?'))


# --- tooltips ----------------------------------------------------------------

def scrolled_tab(book, text, padding=12):
    """A notebook tab whose content scrolls when the window is shorter than
    the content -- a form-style tab on a small or heavily scaled screen.
    Returns the frame to build the tab into."""
    outer = ttk.Frame(book)
    book.add(outer, text=text)
    return scrollable(outer, padding)


def scrollable(outer, padding=12):
    """Fill `outer` with a frame that scrolls vertically when its content is
    taller than the space. Returns the frame to build into."""
    canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0,
                       background=ttk.Style().lookup('TFrame', 'background') or None)
    bar = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
    canvas.configure(yscrollcommand=bar.set)
    canvas.pack(side='left', fill='both', expand=True)
    inner = ttk.Frame(canvas, padding=padding)
    window = canvas.create_window(0, 0, window=inner, anchor='nw')

    def fit(event=None):
        width, height = canvas.winfo_width(), canvas.winfo_height()
        need = inner.winfo_reqheight()
        # as wide as the tab; as tall as the tab when the content is
        # shorter, so anything that expands still fills it
        canvas.itemconfigure(window, width=width, height=max(need, height))
        canvas.configure(scrollregion=(0, 0, width, max(need, height)))
        if need > height + 2:
            if not bar.winfo_ismapped():
                bar.pack(side='right', fill='y', before=canvas)
        elif bar.winfo_ismapped():
            bar.pack_forget()
            canvas.yview_moveto(0)
    canvas.bind('<Configure>', fit)
    inner.bind('<Configure>', fit)

    def wheel(event):
        # the wheel scrolls the tab under the pointer, except over a list or
        # text box that scrolls on its own
        try:
            under = outer.winfo_containing(event.x_root, event.y_root)
        except (tk.TclError, KeyError):
            return
        if under is None or not (str(under) + '.').startswith(str(outer) + '.'):
            return
        if isinstance(under, (tk.Text, ttk.Treeview, tk.Listbox)):
            return
        if bar.winfo_ismapped():
            canvas.yview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1),
                                'units')
    outer.bind_all('<MouseWheel>', wheel, add='+')
    return inner


class WrapRow(ttk.Frame):
    """Groups of controls side by side, wrapping onto another line when the
    window is too narrow for one -- a small laptop screen, or a large one
    at 150-200 % scaling, must still show every button. Groups marked east
    sit at the right end of whichever line they land on."""

    def __init__(self, master, **kw):
        ttk.Frame.__init__(self, master, **kw)
        self.groups = []
        self._layout = None
        self.bind('<Configure>', self._fit)

    def group(self, east=False, **kw):
        frame = ttk.Frame(self, **kw)
        frame.east = east
        self.groups.append(frame)
        return frame

    def _fit(self, event=None):
        width = self.winfo_width()
        if width <= 1:
            width = self.winfo_toplevel().winfo_width()
        pad = px(4)
        rows, x = [[]], 0
        for group in self.groups:
            w = group.winfo_reqwidth()
            if rows[-1] and x + w > width - 2 * px(8):
                rows.append([])
                x = 0
            rows[-1].append(group)
            x += w + pad
        # placed children ignore the frame's padding, so the margin is ours
        mx, my = px(8), px(6)
        layout, y = [], my
        for line in rows:
            h = max(g.winfo_reqheight() for g in line)
            left, right = mx, width - mx
            for g in line:
                w = g.winfo_reqwidth()
                if g.east:
                    right -= w
                    layout.append((g, right, y, h))
                    right -= pad
                else:
                    layout.append((g, left, y, h))
                    left += w + pad
            y += h + pad
        key = [(x, y, h) for _, x, y, h in layout] + [y]
        if key == self._layout:
            return
        self._layout = key
        for group, gx, gy, h in layout:
            group.place(x=gx, y=gy, height=h)
        self.configure(height=max(1, y - pad + my))


class Tip(object):
    """A one-line explanation that appears under the pointer after a pause."""

    def __init__(self, widget, text=None, provider=None):
        self.widget, self.text, self.provider = widget, text, provider
        self.window = None
        self.after_id = None
        widget.bind('<Enter>', self._arm, add='+')
        widget.bind('<Leave>', self._hide, add='+')
        widget.bind('<Motion>', self._move, add='+')
        widget.bind('<ButtonPress>', self._hide, add='+')

    def _arm(self, event):
        self._cancel()
        self.after_id = self.widget.after(600, lambda: self._show(event))

    def _move(self, event):
        if self.window:
            self._hide()
        self._arm(event)

    def _cancel(self):
        if self.after_id:
            self.widget.after_cancel(self.after_id)
            self.after_id = None

    def _show(self, event):
        text = self.provider(event) if self.provider else self.text
        if not text:
            return
        self._hide()
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_attributes('-topmost', True)
        label = tk.Label(self.window, text=text, justify='left',
                         background='#fffbe6', relief='solid', borderwidth=1,
                         wraplength=px(360), padx=px(6), pady=px(4))
        label.pack()
        x = self.widget.winfo_pointerx() + px(14)
        y = self.widget.winfo_pointery() + px(18)
        self.window.wm_geometry('+%d+%d' % (x, y))

    def _hide(self, *_):
        self._cancel()
        if self.window:
            self.window.destroy()
            self.window = None


# One colour per nozzle, in size order, so the board view reads at a glance.
NOZZLE_COLOURS = {
    'CN030': '#b39ddb', 'CN040': '#7986cb', 'CN065': '#4fc3f7',
    'CN100': '#66bb6a', 'CN140': '#d4e157', 'CN220': '#ffb74d',
    'CN400': '#ef5350', 'CN750': '#8d6e63',
}
NO_NOZZLE_COLOUR = '#bdbdbd'


# --- a Treeview you can type into ------------------------------------------

class EditableTree(ttk.Treeview):
    """Treeview with in-place cell editing.

    Double-click (or Enter, or just start typing) opens an entry or a combobox
    over the cell. Enter commits and steps to the row below, Escape cancels,
    Tab moves right. `on_edit(row_id, field, text)` does the work and returns
    True if the value was accepted.
    """

    def __init__(self, master, columns, on_edit, **kw):
        self.spec = columns
        self.fields = [c[0] for c in columns]
        self.editable = set(c[0] for c in columns if c[4])
        self.on_edit = on_edit
        ttk.Treeview.__init__(self, master, columns=self.fields,
                              show='headings', selectmode='extended', **kw)
        for field, heading, width, anchor, _ in columns:
            self.heading(field, text=heading,
                         command=lambda f=field: self.sort_by(f))
            self.column(field, width=px(width), minwidth=px(30), anchor=anchor,
                        stretch=(field in ('refs', 'comment')))
        self._editor = None
        self._sort_desc = {}
        self.bind('<Double-1>', self._begin)
        self.bind('<Return>', self._begin)
        self.bind('<Key>', self._typed)
        # Hovering a column heading explains the column.
        Tip(self, provider=self._heading_tip)

    def _heading_tip(self, event):
        if self.identify_region(event.x, event.y) != 'heading':
            return ''
        column = self.identify_column(event.x)
        if not column:
            return ''
        field = self.fields[int(column[1:]) - 1]
        return HELP.tip(field)

    # -- sorting
    def sort_by(self, field):
        desc = self._sort_desc.get(field, False)

        def key(item):
            raw = self.set(item, field)
            try:
                return (0, float(raw.split()[0]), '')
            except (ValueError, IndexError):
                return (1, 0.0, raw.lower())

        for index, item in enumerate(sorted(self.get_children(''), key=key,
                                            reverse=desc)):
            self.move(item, '', index)
        self._sort_desc[field] = not desc

    # -- editing
    def _cell_at(self, event):
        if self.identify_region(event.x, event.y) not in ('cell', 'tree'):
            return None, None
        row = self.identify_row(event.y)
        column = self.identify_column(event.x)
        if not row or not column:
            return None, None
        return row, self.fields[int(column[1:]) - 1]

    def _typed(self, event):
        """Typing a character starts an edit on the first editable column."""
        if self._editor or not event.char or not event.char.isprintable():
            return None
        row = self.focus()
        if not row:
            return None
        for field in self.fields:
            if field in self.editable:
                self.edit(row, field, initial=event.char)
                return 'break'
        return None

    def _begin(self, event):
        if isinstance(event, tk.Event) and event.type == tk.EventType.KeyPress:
            row = self.focus()
            field = next((f for f in self.fields if f in self.editable), None)
        else:
            row, field = self._cell_at(event)
        if row and field and field in self.editable:
            self.edit(row, field)
            return 'break'
        return None

    def edit(self, row, field, initial=None):
        self.cancel()
        box = self.bbox(row, field)
        if not box:
            self.see(row)
            box = self.bbox(row, field)
            if not box:
                return
        x, y, width, height = box
        current = self.set(row, field)

        if field == 'mode':
            widget = ttk.Combobox(self, values=MODE_CHOICES, state='readonly')
            widget.set(current)
        elif field == 'head':
            widget = ttk.Combobox(self, values=HEAD_CHOICES, state='readonly')
            widget.set(current)
        elif field == 'skip':
            widget = ttk.Combobox(self, values=['0', '1'], state='readonly')
            widget.set(current)
        elif field == 'nozzle':
            # The list is the machine's nozzles; a name outside it can still
            # be typed, for a custom tip.
            widget = ttk.Combobox(self, values=yy1.NOZZLE_CHOICES)
            widget.set(current)
        else:
            widget = ttk.Entry(self)
            widget.insert(0, initial if initial is not None else current)
            if initial is None:
                widget.select_range(0, 'end')

        widget.place(x=x, y=y, width=width, height=height)
        widget.focus_set()
        self._editor = (widget, row, field)
        widget.bind('<Return>', lambda e: self._commit('down'))
        widget.bind('<Tab>', lambda e: self._commit('right'))
        widget.bind('<Escape>', lambda e: self.cancel())
        widget.bind('<FocusOut>', lambda e: self._commit(None))
        if isinstance(widget, ttk.Combobox):
            widget.bind('<<ComboboxSelected>>', lambda e: self._commit(None))

    def cancel(self, *_):
        if self._editor:
            self._editor[0].destroy()
            self._editor = None
        return 'break'

    def _commit(self, step):
        if not self._editor:
            return 'break'
        widget, row, field = self._editor
        text = widget.get()
        self._editor = None
        widget.destroy()
        self.on_edit(row, field, text)
        if step == 'down':
            nxt = self.next(row)
            if nxt:
                self.selection_set(nxt)
                self.focus(nxt)
                self.see(nxt)
        elif step == 'right':
            after = self.fields[self.fields.index(field) + 1:]
            nxt = next((f for f in after if f in self.editable), None)
            if nxt:
                self.after(1, lambda: self.edit(row, nxt))
        return 'break'


# --- the window ------------------------------------------------------------

class App(tk.Tk):

    def __init__(self, project=None):
        tk.Tk.__init__(self)
        tune_for_dpi(self)
        self.title(APP)
        width = min(px(1360), self.winfo_screenwidth() - px(40))
        height = min(px(830), self.winfo_screenheight() - px(80))
        self.geometry('%dx%d' % (width, height))
        self.minsize(min(px(760), self.winfo_screenwidth() - px(20)),
                     min(px(480), self.winfo_screenheight() - px(80)))

        self.result = None
        self.side = tk.StringVar(value='T')
        self.rot_var = tk.StringVar(value='0')
        self.format_var = tk.StringVar(value='yy1')
        self.omit_var = tk.BooleanVar(value=False)
        self.tabs = {}           # name -> notebook index
        self.status = tk.StringVar(value='Open a KiCad project, or a YY1 CSV.')
        self.library = None      # reels that stay on the machine, if a list is loaded
        self.type_rows = {}      # tree item id -> type key
        self._type_order = []    # display order, kept stable across edits
        self.part_rows = {}      # tree item id -> designator
        self._dirty = False

        try:
            ttk.Style().theme_use('vista')
        except tk.TclError:
            pass
        self.guide = None
        self._build()
        self.protocol('WM_DELETE_WINDOW', self.quit_app)
        if project:
            self.after(80, lambda: self.open_project(project))
        elif self.setting('guide_at_start', True):
            self.after(400, self.open_guide)

    def open_guide(self, step=None):
        """The step-by-step guide, beside the window; one at a time."""
        import yy1guide
        if self.guide is not None and self.guide.winfo_exists():
            self.guide.deiconify()
            self.guide.lift()
            if step is not None:
                self.guide.show(step)
            return self.guide
        self.guide = yy1guide.Guide(self)
        if step is not None:
            self.guide.show(step)
        return self.guide

    # -- settings that belong to the machine and the user, not to a board
    def setting(self, key, default=None):
        try:
            import json
            with open(self.SETTINGS, encoding='utf-8') as handle:
                return json.load(handle).get(key, default)
        except (IOError, OSError, ValueError):
            return default

    def set_setting(self, key, value):
        import json
        try:
            with open(self.SETTINGS, encoding='utf-8') as handle:
                data = json.load(handle)
        except (IOError, OSError, ValueError):
            data = {}
        data[key] = value
        try:
            with open(self.SETTINGS, 'w', encoding='utf-8') as handle:
                json.dump(data, handle, indent=2)
        except (IOError, OSError):
            pass

    # -- layout
    def _build(self):
        self._menu()

        row = WrapRow(self, padding=(8, 6))
        row.pack(fill='x')
        bar = row.group()
        guide = ttk.Button(bar, text='Guide…', command=self.open_guide)
        guide.pack(side='left', padx=(0, 6))
        self.undo_button = ttk.Button(bar, text='↶ Undo', width=7,
                                      command=self.undo, state='disabled')
        self.undo_button.pack(side='left')
        self.redo_button = ttk.Button(bar, text='↷ Redo', width=7,
                                      command=self.redo, state='disabled')
        self.redo_button.pack(side='left', padx=(2, 10))
        Tip(self.undo_button, provider=lambda: self._undo_tip('undo'))
        Tip(self.redo_button, provider=lambda: self._undo_tip('redo'))
        Tip(guide, 'Step by step: the files to export from KiCad or PADS, the '
                   'heads and nozzles, the feeders against your list, then the '
                   'check and the export.')
        ttk.Button(bar, text='Open KiCad project…',
                   command=self.open_project).pack(side='left')
        ttk.Button(bar, text='Open PADS project…',
                   command=self.open_pads).pack(side='left', padx=(6, 0))
        ttk.Button(bar, text='Open Altium…',
                   command=self.open_altium).pack(side='left', padx=(6, 0))
        ttk.Button(bar, text='Open YY1 CSV…',
                   command=self.open_csv).pack(side='left', padx=(6, 14))
        # The two things a job comes down to, at the right and the same
        # size: decide it, then write it. Everything else is a tab.
        ttk.Style().configure('Accent.TButton', font=('', 13, 'bold'),
                              padding=(px(14), px(4)))
        bar = row.group(east=True)
        ttk.Label(bar, text='Side').pack(side='left')
        for value, label in (('T', 'Top'), ('B', 'Bottom')):
            ttk.Radiobutton(bar, text=label, value=value, variable=self.side,
                            command=self.switch_side).pack(side='left')
        ttk.Separator(bar, orient='vertical').pack(side='left', fill='y', padx=12)
        setup = ttk.Button(bar, text='  Set up board  ', command=self.setup_board,
                           style='Accent.TButton')
        setup.pack(side='left', ipady=px(2))
        bar = row.group(east=True)
        ttk.Button(bar, text='Save profile',
                   command=self.save_profile).pack(side='left', padx=(0, 12))
        export = ttk.Button(bar, text='  Export  ', command=self.export,
                            style='Accent.TButton')
        export.pack(side='left', ipady=px(2))
        Tip(export, 'Writes the CSV for the machine next to the board, with '
                    'the operator sheet and a copy of the profile. Format and '
                    'the skipped parts: Job header tab.')
        card = ttk.Button(bar, text='  to SD card…  ', command=self.export_as,
                          style='Accent.TButton')
        card.pack(side='left', ipady=px(2), padx=(4, 0))
        Tip(card, 'The same, into a folder you choose -- the SD card.')

        main = ttk.PanedWindow(self, orient='vertical')
        main.pack(fill='both', expand=True, padx=8, pady=(0, 4))
        self._main_split = main
        self._main_height = 0
        main.bind('<Configure>', lambda e: self.after_idle(self._fit_report))
        book = ttk.Notebook(main)
        main.add(book, weight=1)
        self.book = book

        def add(name, frame, text):
            book.add(frame, text=text)
            self.tabs[name] = book.index('end') - 1

        # Feeders
        feeders = ttk.Frame(book, padding=6)
        add('feeders', feeders, '  Feeders  ')
        self.type_tree = self._tree(feeders, TYPE_COLUMNS, self.edit_type)

        # below the table, but laid out before it so a short window
        # shrinks the table rather than hiding the buttons
        toolrow = WrapRow(feeders, padding=(0, 6, 0, 0))
        toolrow.pack(side='bottom', fill='x', before=self.type_tree.master)
        tools = toolrow.group()
        ttk.Button(tools, text='Auto-number feeders 1…n',
                   command=self.auto_number).pack(side='left')
        ttk.Button(tools, text='Renumber all',
                   command=lambda: self.auto_number(True)).pack(side='left', padx=(4, 14))
        tools = toolrow.group()
        ttk.Button(tools, text='Feeder list…',
                   command=self.load_library).pack(side='left', padx=(0, 4))
        self.library_label = ttk.Label(tools, text='no feeder list',
                                       foreground='#666')
        self.library_label.pack(side='left', padx=(0, 10))
        Tip(self.library_label, 'The reels that stay on the machine: a '
                                'spreadsheet of slot, tape width, value. Set '
                                'up board matches the board against it and '
                                'lists what is new.')
        tools = toolrow.group()
        ttk.Button(tools, text='Group values…',
                   command=self.group_values).pack(side='left')
        ttk.Button(tools, text='Find look-alikes…',
                   command=self.find_lookalikes).pack(side='left', padx=4)
        tools = toolrow.group()
        ttk.Label(tools, text='• right-click rows for nozzle, skip, merge',
                  foreground='#666').pack(side='left', padx=6)
        self.type_tree.bind('<Button-3>', self._type_menu)
        ttk.Label(tools, text='   • double-click any cell to edit it',
                  foreground='#666').pack(side='left')

        # Board
        board = ttk.Frame(book, padding=6)
        add('board', board, '  Board  ')
        self._board_tab(board)

        # Feeder bank
        bank = ttk.Frame(book, padding=6)
        add('bank', bank, '  Feeder bank  ')
        self._bank_tab(bank)

        # Order and nozzle changes
        plan = scrolled_tab(book, '  Order & nozzles  ')
        self.tabs['nozzles'] = book.index('end') - 1
        self._plan_tab(plan)

        # Parts
        parts = ttk.Frame(book, padding=6)
        add('parts', parts, '  Parts  ')
        self.part_tree = self._tree(parts, PART_COLUMNS, self.edit_part)
        hint = ttk.Frame(parts, padding=(0, 6, 0, 0))
        hint.pack(side='bottom', fill='x', before=self.part_tree.master)
        ttk.Label(hint, text='Editing here overrides one designator only. For a '
                             'whole reel, use the Feeders tab.',
                  foreground='#666').pack(side='left')
        ttk.Button(hint, text='Clear overrides on selected',
                   command=self.clear_part_overrides).pack(side='right')
        ttk.Separator(hint, orient='vertical').pack(side='right', fill='y',
                                                    padx=10)
        ttk.Button(hint, text='Move down',
                   command=lambda: self.move_part(1)).pack(side='right', padx=2)
        ttk.Button(hint, text='Move up',
                   command=lambda: self.move_part(-1)).pack(side='right', padx=2)

        # Job
        job = scrolled_tab(book, '  Job header  ')
        self.tabs['job'] = book.index('end') - 1
        self._job_tab(job)

        # Manual
        manual = ttk.Frame(book, padding=6)
        add('manual', manual, '  Manual  ')
        self._manual_tab(manual)
        self.after(50, self._tooltips)

        # Log
        frame = ttk.LabelFrame(main, text='Report  (drag the divider above to resize)',
                               padding=4)
        main.add(frame, weight=0)
        # a short screen gets a short report; the divider gives it room back
        short = self.winfo_screenheight() / HIDPI < 900
        self.log = tk.Text(frame, height=4 if short else 7, wrap='none',
                           font=(fixed_family(), 9))
        scroll = ttk.Scrollbar(frame, orient='vertical', command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set, state='disabled')
        scroll.pack(side='right', fill='y')
        self.log.pack(fill='both', expand=True)

        foot = ttk.Frame(self, padding=(8, 2, 8, 6))
        foot.pack(side='bottom', fill='x', before=main)
        ttk.Label(foot, textvariable=self.status, foreground='#444').pack(
            side='left', fill='x', expand=True)

    def _fit_report(self):
        """When the window changes height, the report gets about a fifth of
        it (three to eight lines) and the tabs the rest. Dragging the divider
        still works; it holds until the window is resized again."""
        main = self._main_split
        height = main.winfo_height()
        if height < 100 or height == self._main_height:
            return
        self._main_height = height
        line = tkfont.Font(font=self.log.cget('font')).metrics('linespace')
        chrome = px(34)                  # the frame's caption and padding
        report = max(3 * line, min(8 * line, int(height * 0.2) - chrome)) + chrome
        try:
            main.sashpos(0, height - report)
        except tk.TclError:
            pass

    def _menu(self):
        menu = tk.Menu(self)
        files = tk.Menu(menu, tearoff=0)
        files.add_command(label='Guided setup…', accelerator='Ctrl+G',
                          command=self.open_guide)
        self.bind_all('<Control-g>', lambda e: self.open_guide())
        files.add_command(label='Undo', accelerator='Ctrl+Z', command=self.undo)
        files.add_command(label='Redo', accelerator='Ctrl+Y', command=self.redo)
        self.bind_all('<Control-z>', self._undo_key)
        self.bind_all('<Control-y>', self._redo_key)
        files.add_separator()
        files.add_command(label='Open KiCad project…', accelerator='Ctrl+O',
                          command=self.open_project)
        files.add_command(label='Open KiCad board…', command=self.open_board_file)
        files.add_command(label='Open PADS project…', command=self.open_pads)
        files.add_command(label='Open Altium project…', command=self.open_altium)
        files.add_command(label='Open Altium board…', command=self.open_altium_file)
        files.add_command(label='Open YY1 CSV…', command=self.open_csv)
        files.add_command(label='Reload from disk', command=self.reload)
        files.add_separator()
        files.add_command(label='Save profile', accelerator='Ctrl+S',
                          command=self.save_profile)
        files.add_command(label='Load profile…', command=self.load_profile)
        files.add_command(label='Import settings from a machine CSV…',
                          command=self.import_machine)
        files.add_separator()
        files.add_command(label='Export next to the board', accelerator='Ctrl+E',
                          command=self.export)
        files.add_command(label='Export to SD card…', command=self.export_as)
        files.add_separator()
        files.add_command(label='Quit', command=self.quit_app)
        menu.add_cascade(label='File', menu=files)

        tools = tk.Menu(menu, tearoff=0)
        tools.add_command(label='Most used reels across projects…',
                          command=self.survey_projects)
        menu.add_cascade(label='Tools', menu=tools)

        helpm = tk.Menu(menu, tearoff=0)
        helpm.add_command(label='Manual',
                          command=lambda: self.book.select(self.tabs['manual']))
        helpm.add_command(label='Feeder banks and modes', command=self.show_help)
        menu.add_cascade(label='Help', menu=helpm)
        self.config(menu=menu)
        self.bind('<Control-o>', lambda e: self.open_project())
        self.bind('<Control-s>', lambda e: self.save_profile())
        self.bind('<Control-e>', lambda e: self.export())

    def _tree(self, parent, columns, on_edit):
        wrap = ttk.Frame(parent)
        wrap.pack(fill='both', expand=True)
        tree = EditableTree(wrap, columns, on_edit)
        vsb = ttk.Scrollbar(wrap, orient='vertical', command=tree.yview)
        hsb = ttk.Scrollbar(wrap, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        tree.tag_configure('unassigned', background='#fff4d6')
        tree.tag_configure('clash', background='#ffd9d9')
        tree.tag_configure('problem', background='#ffd9d9')
        tree.tag_configure('ready', background='#e3f6e3')
        tree.tag_configure('skipped', foreground='#999')
        tree.tag_configure('override', background='#e8f1ff')
        return tree

    def _job_tab(self, parent):
        self.job_vars = {}
        panel = ttk.LabelFrame(parent, text='Panelised board', padding=10)
        panel.pack(fill='x')
        ttk.Label(panel, text='A single board is Rows 1, Columns 1 and unit '
                              'size 0. For an array, give one unit’s size '
                              'and how many.',
                  foreground='#666').grid(row=0, column=0, columnspan=8,
                                          sticky='w', pady=(0, 8))
        for col, (field, label) in enumerate(
                [('unit_length', 'Unit length mm'), ('unit_width', 'Unit width mm'),
                 ('rows', 'Rows'), ('columns', 'Columns')]):
            ttk.Label(panel, text=label).grid(row=1, column=col * 2, sticky='e',
                                              padx=(0, 4), pady=3)
            var = tk.StringVar()
            self.job_vars[field] = var
            ttk.Entry(panel, textvariable=var, width=9).grid(
                row=1, column=col * 2 + 1, sticky='w', padx=(0, 16))

        fid = ttk.LabelFrame(parent, text='Fiducial and overall offset', padding=10)
        fid.pack(fill='x', pady=10)
        ttk.Label(fid, text='Fiducial 1 X/Y is where the fiducial mark sits, in '
                            'the same mm-from-the-corner frame as the parts. '
                            'With it set, the machine looks for the mark before '
                            'each board (Step does it first) and corrects the '
                            'whole board\'s position from what it finds. Leave '
                            'at 0 to place straight from the file.',
                  foreground='#666', wraplength=px(880), justify='left').grid(
            row=0, column=0, columnspan=8, sticky='w', pady=(0, 6))
        ttk.Label(fid, text='Offset X/Y is a constant added to every placement, '
                            'in mm. Leave it at 0. If a whole board comes out '
                            'shifted the same way, the manual prefers you '
                            'fine-tune in the machine\'s Preview (3.2.4 note 6) '
                            'rather than shift the file.',
                  foreground='#666', wraplength=px(880), justify='left').grid(
            row=2, column=0, columnspan=8, sticky='w', pady=(6, 4))
        pick = ttk.Frame(fid)
        pick.grid(row=3, column=0, columnspan=8, sticky='w', pady=(2, 0))
        ttk.Label(pick, text='Fiducial marks found on the board:').pack(side='left')
        self.fid_pick = ttk.Combobox(pick, values=[], state='readonly', width=34)
        self.fid_pick.pack(side='left', padx=6)
        ttk.Button(pick, text='Use as fiducial 1',
                   command=self.use_fiducial).pack(side='left')
        ttk.Label(pick, text='   or click a part on the Board tab and press '
                             '"Set as fiducial 1"', foreground='#666').pack(side='left')
        for col, (field, label) in enumerate(
                [('fid_x', 'Fiducial 1 X'), ('fid_y', 'Fiducial 1 Y'),
                 ('offset_x', 'Offset X'), ('offset_y', 'Offset Y')]):
            ttk.Label(fid, text=label).grid(row=1, column=col * 2, sticky='e',
                                            padx=(0, 4), pady=3)
            var = tk.StringVar()
            self.job_vars[field] = var
            ttk.Entry(fid, textvariable=var, width=9).grid(
                row=1, column=col * 2 + 1, sticky='w', padx=(0, 16))

        opts = ttk.LabelFrame(parent, text='The file', padding=10)
        opts.pack(fill='x', pady=(0, 10))
        r = ttk.Frame(opts)
        r.pack(anchor='w')
        ttk.Label(r, text='Rotation offset').pack(side='left')
        rot = ttk.Entry(r, textvariable=self.rot_var, width=7, justify='center')
        rot.pack(side='left', padx=4)
        rot.bind('<Return>', lambda e: self.apply_rotation())
        Tip(rot, HELP.tip('rotation'))
        ttk.Label(r, text='°').pack(side='left')
        ttk.Button(r, text='Apply', command=self.apply_rotation).pack(side='left', padx=4)
        ttk.Label(r, text='added to every part -- set it if a test board comes '
                          'out turned as a whole', foreground='#666').pack(side='left', padx=6)
        r = ttk.Frame(opts)
        r.pack(anchor='w', pady=(6, 0))
        ttk.Label(r, text='Board on the machine turned').pack(side='left')
        self.board_rot_var = tk.StringVar(value='0')
        for angle in yy1.BOARD_ROTATIONS:
            ttk.Radiobutton(r, text='%d°' % angle, value=str(angle),
                            variable=self.board_rot_var,
                            command=lambda: self.set_board_rotation(
                                int(self.board_rot_var.get()))).pack(side='left', padx=(6, 0))
        ttk.Label(r, text='counter-clockwise -- for a panel that only fits the '
                          'machine the other way. Positions, rotations, size, '
                          'fiducial and panel all turn with it.',
                  foreground='#666').pack(side='left', padx=8)
        r = ttk.Frame(opts)
        r.pack(anchor='w', pady=(6, 0))
        ttk.Label(r, text='Format').pack(side='left')
        self.format_box = ttk.Combobox(r, textvariable=self.format_var,
                                       width=8, state='readonly',
                                       values=['yy1', 'legacy'])
        self.format_box.pack(side='left', padx=(4, 16))
        self.omit_box = ttk.Checkbutton(r, text='Skipped parts out of the CSV',
                                        variable=self.omit_var,
                                        command=self.omit_changed)
        self.omit_box.pack(side='left')
        Tip(self.omit_box, HELP.tip('omit'))

        nz = ttk.LabelFrame(parent, text='Nozzle changes', padding=10)
        nz.pack(fill='x')
        ttk.Label(nz, text='Off unless one board needs more than the two nozzles '
                           'the heads hold. Each line: before which component, '
                           'which head, which station to drop into and pick from.',
                  foreground='#666').grid(row=0, column=0, columnspan=10,
                                          sticky='w', pady=(0, 8))
        heads = ['', 'On', 'Before component', 'Head', 'Drop station',
                 'Pick-up station']
        for col, text in enumerate(heads):
            ttk.Label(nz, text=text, font=('', 8, 'bold')).grid(
                row=1, column=col, padx=6, sticky='w')
        self.nozzle_vars = []
        for i in range(4):
            row = {}
            ttk.Label(nz, text='%d' % (i + 1)).grid(row=2 + i, column=0, padx=6)
            row['on'] = tk.BooleanVar()
            ttk.Checkbutton(nz, variable=row['on']).grid(row=2 + i, column=1)
            for col, field in enumerate(['before', 'head', 'drop', 'pick'], start=2):
                row[field] = tk.StringVar()
                ttk.Entry(nz, textvariable=row[field], width=7,
                          justify='center').grid(row=2 + i, column=col, padx=6,
                                                 pady=2)
            self.nozzle_vars.append(row)

        ttk.Button(parent, text='Apply job header',
                   command=self.apply_job).pack(anchor='w', pady=12)

    def refresh_fiducials(self):
        """Fill the fiducial picker from what the board carries."""
        if not hasattr(self, 'fid_pick') or not self.result:
            return
        side = self.side.get()
        marks = [f for f in getattr(self.result, 'fiducials', []) if f[3] == side]
        self.fid_pick['values'] = ['%s  at  %.3f, %.3f mm' % (r, x, y)
                                   for r, x, y, _ in marks]
        self._fid_marks = marks
        if marks and not self.fid_pick.get():
            self.fid_pick.current(0)

    def use_fiducial(self, ref=None):
        """Put a fiducial mark's position into the job header."""
        if not self.result:
            return
        if ref is None:
            marks = getattr(self, '_fid_marks', [])
            index = self.fid_pick.current()
            if index < 0 or index >= len(marks):
                messagebox.showinfo(APP, 'No fiducial mark found on this side. '
                                         'Click a part on the Board tab and '
                                         'press "Set as fiducial 1" instead.')
                return
            ref, x, y, _ = marks[index]
        else:
            part = next((p for p in self.result.sides[self.side.get()]
                         if p['ref'] == ref), None)
            if not part:
                return
            x, y = part['x'], part['y']
        self.job_vars['fid_x'].set('%.3f' % x)
        self.job_vars['fid_y'].set('%.3f' % y)
        self.apply_job()
        self.say('fiducial 1 set to %s at %.3f, %.3f mm -- press Step on the '
                 'machine and it goes there first' % (ref, x, y))
        self.draw_board()

    def set_part_as_fiducial(self):
        if self._board_selected:
            self.use_fiducial(self._board_selected)

    # -- merging reels whose values differ only in wording
    def merge_reels(self, keys=None, name=None, package=None):
        """Make several reels one, by renaming their values to one name.

        The rename is stored against each raw value in the profile, so the
        next export from CAD keeps the merge. Packages have to match: two
        values on different packages are two physical reels.
        """
        if not self.result:
            return
        keys = keys or [self.type_rows[i] for i in self.type_tree.selection()
                        if i in self.type_rows]
        if len(keys) < 2:
            messagebox.showinfo(APP, 'Select two or more reels on the Feeders '
                                     'tab, then merge them.')
            return
        groups = self.result.type_rows(self.side.get())
        packages = sorted(set(k.split('|')[0] for k in keys))
        choices = [k.split('|', 1)[1] for k in keys]
        if name is None or (len(packages) > 1 and package is None):
            picked = self._merge_dialog(keys, packages, choices,
                                        name or choices[0])
            if not picked:
                return
            name, package = picked
        name = yy1.clean(name)
        package = yy1.clean(package or packages[0])
        profile = self.result.profile
        kept = None
        for key in keys:
            key_package, key_value = key.split('|', 1)
            if key_package == package:
                # Same footprint: rename the raw values, which also covers
                # any part with that value that appears in a later export.
                for part in groups.get(key, []):
                    raw = part.get('raw_value') or part['comment']
                    if raw:
                        profile.values[raw] = name
                    part['comment'] = name
            else:
                # Different footprint: this reel alone is exported as the
                # reel that is really loaded. Nothing else on that footprint
                # changes.
                profile.aliases[key] = [package, name]
                for part in groups.get(key, []):
                    part['package'], part['comment'] = package, name
            if key in profile.types:
                settings = profile.types.pop(key)
                kept = kept or settings
        new_key = yy1.type_key(package, name)
        if kept:
            profile.types[new_key] = kept
        self.say('merged %d reels into "%s %s"%s'
                 % (len(keys), package, name,
                    '' if len(packages) == 1 else
                    ' -- %s parts will be placed from the %s reel'
                    % ('/'.join(p for p in packages if p != package), package)))
        self.mark_dirty()
        self._type_order = []
        self.refresh()

    def _merge_dialog(self, keys, packages, choices, default):
        """Name the merged reel and, when footprints differ, say which one
        the parts on it really are. Returns (name, package) or None."""
        dialog = tk.Toplevel(self)
        dialog.title('Merge reels')
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Merging:\n  ' + '\n  '.join(
            k.replace('|', '  ') for k in keys)).pack(anchor='w')
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=(10, 4))
        ttk.Label(row, text='Value the machine shows').pack(side='left')
        name_var = tk.StringVar(value=default)
        entry = ttk.Entry(row, textvariable=name_var, width=18)
        entry.pack(side='left', padx=6)
        pkg_var = tk.StringVar(value=packages[0])
        if len(packages) > 1:
            ttk.Label(frame, text='These are different footprints. Which part is '
                                  'actually on the reel? The others get placed '
                                  'with it -- an 0805 on 0603 pads works if the '
                                  'pads can take it; the reverse usually does '
                                  'not.', wraplength=px(420), justify='left',
                      foreground='#666').pack(anchor='w', pady=(6, 2))
            for pkg in packages:
                ttk.Radiobutton(frame, text=pkg, value=pkg,
                                variable=pkg_var).pack(anchor='w')
        outcome = {}

        def ok(*_):
            if name_var.get().strip():
                outcome['value'] = (name_var.get(), pkg_var.get())
                dialog.destroy()
        buttons = ttk.Frame(frame, padding=(0, 10, 0, 0))
        buttons.pack(fill='x')
        ttk.Button(buttons, text='Merge', command=ok).pack(side='right')
        ttk.Button(buttons, text='Cancel', command=dialog.destroy).pack(side='right', padx=6)
        dialog.bind('<Return>', ok)
        dialog.bind('<Escape>', lambda e: dialog.destroy())
        entry.focus_set()
        self.wait_window(dialog)
        return outcome.get('value')

    def find_lookalikes(self):
        """Offer reels that are probably the same part written two ways."""
        if not self.result:
            return
        groups = self.result.type_rows(self.side.get())
        buckets = {}
        for key in groups:
            package, value = key.split('|', 1)
            buckets.setdefault((package, yy1.value_essence(value)), []).append(key)
        candidates = [keys for (pkg, ess), keys in buckets.items()
                      if ess and len(keys) > 1]
        if not candidates:
            messagebox.showinfo(APP, 'No look-alike reels: every value on this '
                                     'side is distinct once units and ratings '
                                     'are ignored.')
            return

        dialog = tk.Toplevel(self)
        dialog.title('Reels that look like the same part')
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Same package, same value once units and voltage '
                              'ratings are ignored. Tick the groups to merge; '
                              'the first name in each is what the reel will be '
                              'called. Untick anything that really is two '
                              'reels (10uF/16V and 10uF/50V may well be).',
                  wraplength=px(560), justify='left').pack(anchor='w', pady=(0, 8))
        chosen = []
        for keys in sorted(candidates):
            var = tk.BooleanVar(value=True)
            chosen.append((var, keys))
            names = ['%s  (%d)' % (k.split('|', 1)[1], len(groups[k])) for k in keys]
            ttk.Checkbutton(frame, variable=var,
                            text='%s:  %s' % (keys[0].split('|')[0],
                                              '   +   '.join(names))).pack(anchor='w')

        def go():
            merged = 0
            for var, keys in chosen:
                if var.get():
                    self.merge_reels(keys, name=keys[0].split('|', 1)[1])
                    merged += 1
            dialog.destroy()
            if merged:
                self.say('%d look-alike group(s) merged' % merged)
        buttons = ttk.Frame(frame, padding=(0, 10, 0, 0))
        buttons.pack(fill='x')
        ttk.Button(buttons, text='Merge ticked', command=go).pack(side='right')
        ttk.Button(buttons, text='Cancel', command=dialog.destroy).pack(side='right', padx=6)


    # -- placement order and nozzle changes
    def _plan_tab(self, parent):
        order = ttk.LabelFrame(parent, text='Placement order', padding=10)
        order.pack(fill='x')
        ttk.Label(order, text='The YY1 places straight down the file, so the '
                              'row order is the order the board gets built in.',
                  foreground='#666').pack(anchor='w', pady=(0, 8))
        self.order_var = tk.StringVar(value='ref')
        for value, label, why in ORDER_LABELS:
            row = ttk.Frame(order)
            row.pack(fill='x', anchor='w')
            ttk.Radiobutton(row, text=label, value=value,
                            variable=self.order_var,
                            command=self.apply_order).pack(side='left')
            ttk.Label(row, text=why, foreground='#666').pack(side='left', padx=8)

        nozzles = ttk.LabelFrame(parent, text='Nozzle changes', padding=10)
        nozzles.pack(fill='both', expand=True, pady=12)
        ttk.Label(nozzles, text='The head carries two nozzles and the station '
                                'holds more, so four types run without stopping '
                                '(manual 4.5.2). Past that a nozzle has to be '
                                'swapped mid-board, and the file holds four '
                                'changes.', foreground='#666',
                  wraplength=px(880), justify='left').pack(anchor='w',
                                                           pady=(0, 8))

        top = ttk.Frame(nozzles)
        top.pack(fill='x', pady=(0, 6))
        ttk.Button(top, text='Plan it for me',
                   command=self.plan_nozzles).pack(side='left')
        ttk.Button(top, text='Apply to the job header',
                   command=self.apply_nozzle_plan).pack(side='left', padx=6)
        ttk.Label(top, text='   Nozzle station positions').pack(side='left')
        self.station_var = tk.StringVar(value=str(yy1.STATION_POSITIONS))
        self.station_spin = ttk.Spinbox(top, from_=2, to=4, width=4,
                                        textvariable=self.station_var,
                                        justify='center',
                                        command=self.plan_nozzles)
        self.station_spin.pack(side='left', padx=4)
        ttk.Label(top, text='(one is kept empty to drop the outgoing nozzle '
                            'into)', foreground='#666').pack(side='left')

        body = ttk.Frame(nozzles)
        body.pack(fill='both', expand=True)
        left = ttk.Frame(body)
        left.pack(side='left', fill='y')
        # One row per nozzle in use, its home chosen straight from a list --
        # no double-clicking a cell to find the editor.
        ttk.Label(left, text='Where each nozzle starts',
                  font=('', 9, 'bold')).pack(anchor='w')
        self.home_frame = ttk.Frame(left)
        self.home_frame.pack(anchor='w', pady=(4, 8))
        self.home_boxes = {}

        drawer = ttk.LabelFrame(left, text='Nozzles you have', padding=8)
        drawer.pack(fill='x', pady=(0, 8))
        self.limit_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(drawer, text='Plan only with these',
                        variable=self.limit_var,
                        command=self.inventory_changed).grid(
            row=0, column=0, columnspan=6, sticky='w', pady=(0, 4))
        self.stock_vars = {}
        for i, nozzle in enumerate(yy1.NOZZLE_CHOICES):
            r, c = 1 + i // 4, (i % 4) * 2
            ttk.Label(drawer, text=nozzle).grid(row=r, column=c, sticky='e',
                                                padx=(6, 2), pady=1)
            var = tk.StringVar(value='0')
            self.stock_vars[nozzle] = var
            spin = ttk.Spinbox(drawer, from_=0, to=4, width=3, textvariable=var,
                               justify='center', command=self.inventory_changed)
            spin.grid(row=r, column=c + 1, sticky='w')
            spin.bind('<FocusOut>', lambda e: self.inventory_changed())
            spin.bind('<Return>', lambda e: self.inventory_changed())
        ttk.Label(drawer, text='Two of a nozzle lets it sit on both heads. '
                               'Parts whose nozzle you lack go to the next '
                               'smaller one you own; what nothing picks is '
                               'skipped.', foreground='#666',
                  wraplength=px(330), justify='left').grid(
            row=6, column=0, columnspan=8, sticky='w', pady=(4, 0))
        self.load_inventory()

        quick = ttk.LabelFrame(left, text='Quick set-up', padding=8)
        quick.pack(fill='x')
        row = ttk.Frame(quick)
        row.pack(anchor='w')
        ttk.Label(row, text='Same nozzle on both heads:').pack(side='left')
        self.both_var = tk.StringVar()
        self.both_box = ttk.Combobox(row, textvariable=self.both_var, width=8,
                                     state='readonly', values=[])
        self.both_box.pack(side='left', padx=6)
        ttk.Button(row, text='Set', command=self.set_both_heads).pack(side='left')
        ttk.Label(quick, text='The other nozzles go to the station and get '
                              'fetched with one change each. Use this when the '
                              'board is mostly one size of part.',
                  foreground='#666', wraplength=px(330),
                  justify='left').pack(anchor='w', pady=(4, 0))

        right = ttk.Frame(body, padding=(12, 0, 0, 0))
        right.pack(side='left', fill='both', expand=True)
        ttk.Label(right, text='Plan  (recomputed whenever a home changes)').pack(anchor='w')
        self.plan_text = tk.Text(right, height=9, wrap='word',
                                 font=(fixed_family(), 9))
        self.plan_text.pack(fill='both', expand=True)
        self.plan_text.configure(state='disabled')
        self._plan = ([], [])

    def apply_order(self):
        if not self.result:
            return
        how = self.order_var.get()
        if how == 'custom' and not self.result.profile.sequence:
            self.result.profile.sequence = [
                p['ref'] for p in k2y.ordered(self.result, self.side.get())]
        self.result.profile.order = how
        self.say('placement order: %s'
                 % dict((v, l) for v, l, _ in ORDER_LABELS)[how])
        self.mark_dirty()
        self.refresh()

    def move_part(self, step):
        """Move one placement up or down, which switches to a custom order."""
        if not self.result:
            return
        chosen = self.part_tree.selection()
        if len(chosen) != 1:
            messagebox.showinfo(APP, 'Select exactly one placement to move.')
            return
        sequence = [self.part_rows[i] for i in self.part_tree.get_children()]
        ref = self.part_rows[chosen[0]]
        at = sequence.index(ref)
        to = at + step
        if not 0 <= to < len(sequence):
            return
        sequence[at], sequence[to] = sequence[to], sequence[at]
        self.result.profile.sequence = sequence
        self.result.profile.order = 'custom'
        self.order_var.set('custom')
        self.mark_dirty()
        self.refresh()
        self.part_tree.selection_set(ref)
        self.part_tree.focus(ref)
        self.part_tree.see(ref)

    def nozzle_summary(self):
        """Nozzle -> (part types, placements) for the side on screen."""
        summary = {}
        profile = self.result.profile
        for key, parts in self.result.type_rows(self.side.get()).items():
            nozzle = profile.nozzle_for(key, parts[0].get('nozzle', ''))
            if not nozzle:
                continue
            types, count = summary.get(nozzle, (0, 0))
            summary[nozzle] = (types + 1, count + len(parts))
        return summary

    def refresh_nozzles(self):
        """Rebuild the nozzle-home form: one row per nozzle in use."""
        if not self.result or not hasattr(self, 'home_frame'):
            return
        summary = self.nozzle_summary()
        homes = self.result.profile.homes_for(self.side.get())
        for child in self.home_frame.winfo_children():
            child.destroy()
        self.home_boxes = {}
        if not summary:
            ttk.Label(self.home_frame, text='No nozzle set on any reel yet.',
                      foreground='#666').grid(row=0, column=0, sticky='w')
        for r, nozzle in enumerate(sorted(summary,
                                          key=lambda n: (-summary[n][1], n))):
            types, count = summary[nozzle]
            ttk.Label(self.home_frame, text=nozzle,
                      font=('', 10, 'bold'), width=7).grid(row=r, column=0,
                                                           sticky='w', pady=2)
            ttk.Label(self.home_frame,
                      text='%d reel%s, %d part%s' % (types, '' if types == 1 else 's',
                                                    count, '' if count == 1 else 's'),
                      foreground='#666', width=18).grid(row=r, column=1, sticky='w')
            box = ttk.Combobox(self.home_frame, values=NOZZLE_HOMES,
                               state='readonly', width=11)
            box.set(home_label(homes.get(nozzle)))
            box.grid(row=r, column=2, sticky='w', padx=(6, 0))
            box.bind('<<ComboboxSelected>>',
                     lambda e, n=nozzle, b=box: self.set_home(n, b.get()))
            self.home_boxes[nozzle] = box
        self.both_box['values'] = sorted(summary,
                                         key=lambda n: (-summary[n][1], n))
        if summary and self.both_var.get() not in summary:
            self.both_var.set(self.both_box['values'][0])

    def set_home(self, nozzle, label):
        """Move one nozzle to a head, both heads, a station position, or out."""
        side = self.side.get()
        homes = self.result.profile.homes_for(side)
        home = home_value(label)
        if home:
            # A head carries one nozzle: whatever was on the head(s) just
            # claimed goes to the first free station position.
            taken = set(h for h in ((1, 2) if home == ('head', 0) else
                                    (home[1],)) if home[0] == 'head')
            for other, (where, slot) in list(homes.items()):
                if other == nozzle or where != 'head':
                    continue
                if slot == 0 and home != ('head', 0):
                    # It had both heads; it keeps the one not just claimed.
                    homes[other] = ('head', 3 - home[1])
                elif slot in taken or home == ('head', 0):
                    parked = self._free_station(homes, exclude=nozzle)
                    if parked:
                        homes[other] = parked
                    else:
                        homes.pop(other)
                        self.say('%s has nowhere to go -- the station is full '
                                 'with one position kept empty; it is now '
                                 'not loaded' % other)
            if home[0] == 'station':
                # A position holds one nozzle: whatever sat there moves on.
                for other, (where, slot) in list(homes.items()):
                    if other != nozzle and where == 'station' and slot == home[1]:
                        homes.pop(other)
                        parked = self._free_station(homes, exclude=nozzle,
                                                    avoid=home[1])
                        if parked:
                            homes[other] = parked
                        else:
                            self.say('%s had to make room for %s and there is '
                                     'no position left; it is now not loaded'
                                     % (other, nozzle))
            homes[nozzle] = home
        else:
            homes.pop(nozzle, None)
        self.result.profile.set_homes(side, homes)
        self.mark_dirty()
        self.plan_nozzles(assign=False)

    def _free_station(self, homes, exclude=None, avoid=None):
        used = set(slot for n, (where, slot) in homes.items()
                   if where == 'station' and n != exclude)
        if avoid is not None:
            used.add(avoid)
        try:
            positions = max(2, min(4, int(self.station_var.get())))
        except ValueError:
            positions = yy1.STATION_POSITIONS
        for slot in range(1, positions):          # the last one stays empty
            if slot not in used:
                return ('station', slot)
        return None                                # station full

    def set_both_heads(self):
        """One nozzle on both heads; the others parked while positions last.

        One station position always stays empty for the drop, so with three
        positions two more nozzles can be parked. Anything beyond that is
        left not loaded and said so -- merge those reels onto a loaded nozzle
        or skip them for a second pass.
        """
        nozzle = self.both_var.get()
        if not self.result or not nozzle:
            return
        side = self.side.get()
        summary = self.nozzle_summary()
        stock = self.available()
        if stock is not None and stock.get(nozzle, 0) < 2:
            messagebox.showwarning(APP, 'You have %d x %s in the drawer -- both '
                                        'heads need two. Change the count under '
                                        '"Nozzles you have", or pick another.'
                                   % (stock.get(nozzle, 0), nozzle))
            return
        homes = {nozzle: ('head', 0)}
        left_out = []
        for other in sorted(summary, key=lambda n: (-summary[n][1], n)):
            if other == nozzle:
                continue
            parked = self._free_station(homes, exclude=other)
            if parked:
                homes[other] = parked
            else:
                left_out.append(other)
        self.result.profile.set_homes(side, homes)
        self.mark_dirty()
        self.plan_nozzles(assign=False)
        self.say('%s on both heads; %s parked in the station'
                 % (nozzle, ', '.join(n for n in homes if n != nozzle) or 'nothing'))
        if left_out:
            self.say('WARNING: no station position left for %s -- give those '
                     'reels a loaded nozzle (right-click on the Feeders tab) or '
                     'skip them for a second pass' % ', '.join(left_out))

    def plan_nozzles(self, assign=True):
        """Work out where each nozzle starts and when it has to be swapped."""
        if not self.result:
            return
        summary = self.nozzle_summary()
        if not summary:
            self.write_plan(['No nozzle is set on any part type yet. The '
                             'Feeders tab suggests one per type from the body '
                             'size; edit its Nozzle cell to change it.'])
            return
        try:
            positions = max(2, min(4, int(self.station_var.get())))
        except ValueError:
            positions = yy1.STATION_POSITIONS
        self.result.profile.station_positions = positions

        side = self.side.get()
        counts = [(n, c) for n, (_, c) in summary.items()]
        if assign or not self.result.profile.homes_for(side):
            homes, unplaced = yy1.auto_homes(counts, positions, self.available())
        else:
            homes = dict((n, h) for n, h in
                         self.result.profile.homes_for(side).items()
                         if n in summary)
            unplaced = [n for n in summary if n not in homes]
        self.result.profile.set_homes(side, homes)

        lines = []
        if len(summary) <= yy1.HEAD_SLOTS:
            lines.append('%d nozzle type(s) in use -- one per head, nothing to '
                         'change mid-board.' % len(summary))
        for nozzle in sorted(summary, key=lambda n: (-summary[n][1], n)):
            count = summary[nozzle][1]
            lines.append('  %-6s %3d %-6s %s'
                         % (nozzle, count, 'part' if count == 1 else 'parts',
                            home_label(homes.get(nozzle))))
        if unplaced:
            lines += ['',
                      'No room for %s. Four nozzle types is the limit for an '
                      'uninterrupted run (manual 4.5.2 note 1). Put those parts '
                      'on a nozzle already loaded, mark them Skip and do them '
                      'as a second pass, or free a station position.'
                      % ', '.join(unplaced)]

        # A change happens before one component number, so it only covers a
        # whole group if the file is grouped by nozzle. Plan against that order.
        saved = self.result.profile.order
        self.result.profile.order = 'nozzle'
        parts = yy1.file_parts(k2y.ordered(self.result, side), self.result.profile)
        self.result.profile.order = saved
        nozzle_of = dict(
            (p['ref'], self.result.profile.nozzle_for(
                yy1.type_key(p['package'], p['comment']), p.get('nozzle', '')))
            for p in parts)
        runs = yy1.nozzle_runs(parts, nozzle_of)
        changes, problems = yy1.plan_nozzle_changes(runs, homes, positions)

        if changes:
            lines += ['', '%d change(s) once the file is grouped by nozzle:'
                      % len(changes)]
            for on, before, head, drop, pick in changes:
                nozzle = nozzle_of.get(parts[before - 1]['ref'], '?')
                lines.append('  before #%-4d (%-6s) park head %d in station %d,'
                             ' fit %s from station %d'
                             % (before, parts[before - 1]['ref'], head, drop,
                                nozzle, pick))
        elif len(summary) > yy1.HEAD_SLOTS and not problems:
            lines += ['', 'Everything needed is already on a head.']
        if problems:
            lines += [''] + ['! ' + line for line in problems]
        if changes and saved != 'nozzle':
            lines += ['',
                      'Applying this also sets the placement order to "grouped '
                      'by nozzle" -- one change covers one unbroken run, so the '
                      'file has to be grouped for it to mean anything.']
        self._plan = (changes, problems)
        self.write_plan(lines)
        self.refresh_nozzles()

    def write_plan(self, lines):
        self.plan_text.configure(state='normal')
        self.plan_text.delete('1.0', 'end')
        self.plan_text.insert('1.0', '\n'.join(lines))
        self.plan_text.configure(state='disabled')

    def apply_nozzle_plan(self):
        if not self.result:
            return
        changes, problems = self._plan
        if not changes:
            messagebox.showinfo(APP, 'No nozzle change to apply. Press "Plan it '
                                     'for me" first -- or this board needs no '
                                     'change at all.')
            return
        if problems and not messagebox.askyesno(
                APP, 'The plan has warnings:\n\n%s\n\nApply anyway?'
                     % '\n'.join(problems)):
            return
        side = self.side.get()
        self.result.profile.set_changes(side, changes)
        self.result.profile.order = 'nozzle'
        self.order_var.set('nozzle')
        self.load_job_tab()
        self.say('%d nozzle change(s) written into the %s side header; '
                 'placement order set to grouped by nozzle'
                 % (len(changes), 'top' if side == 'T' else 'bottom'))
        self.mark_dirty()
        self.refresh()


    # -- the one button
    def prepare_setup(self, renumber=False, homes=None, available=False):
        """Plan the side on screen on a copy of the profile.

        `homes` fixes where each nozzle starts -- what is on the heads and in
        the station right now -- and the plan then uses those nozzles only.
        `available` overrides the drawer (False: use the drawer as set).
        Returns (trial profile, report), or None when there is nothing to do.
        """
        if not self.result:
            messagebox.showinfo(APP, 'Open a board first.')
            return None
        side = self.side.get()
        parts = self.result.sides[side]
        if not parts:
            messagebox.showinfo(APP, 'Nothing on this side.')
            return None
        try:
            positions = max(2, min(4, int(self.station_var.get())))
        except ValueError:
            positions = yy1.STATION_POSITIONS
        self.result.profile.station_positions = positions

        # Work on a copy, so Cancel really cancels.
        import copy
        trial = copy.deepcopy(self.result.profile)
        if homes is not None:
            trial.set_homes(side, homes)
        report = yy1.setup_side(parts, trial, side, renumber=renumber,
                                available=self.available() if available is False
                                else available,
                                library=self.library)
        return trial, report

    def apply_setup(self, trial, report, show_board=True):
        self.result.profile = trial
        self.order_var.set(trial.order)
        self.load_job_tab()
        self._type_order = []
        self.clear_log()
        self.say(*report)
        self.mark_dirty()
        self.refresh()
        if show_board:
            self.book.select(self.tabs['board'])    # to see what was decided

    def replan_changes(self):
        """Redo the nozzle change rows for the side after the file changed
        shape -- reels skipped or brought back, say. Returns the problems."""
        if not self.result:
            return []
        side = self.side.get()
        profile = self.result.profile
        if not profile.homes_for(side):
            return []
        changes, problems, _, _ = yy1.side_changes(k2y.ordered(self.result, side),
                                                   profile, side)
        profile.set_changes(side, changes)
        self.load_job_tab()
        return problems

    def setup_board(self, renumber=False):
        """Decide everything for the side on screen, show it, then apply."""
        planned = self.prepare_setup(renumber)
        if not planned:
            return
        trial, report = planned
        side = self.side.get()

        dialog = tk.Toplevel(self)
        dialog.title('Set up board -- %s side' % ('top' if side == 'T' else 'bottom'))
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='This is what will be set. Nothing is saved '
                              'until you press Save profile or Export.',
                  foreground='#666').pack(anchor='w', pady=(0, 8))
        text = tk.Text(frame, width=88, height=min(24, len(report) + 2),
                       wrap='word', font=(fixed_family(), 9))
        text.pack(fill='both', expand=True)
        text.tag_configure('warn', foreground='#b00020')
        for line in report:
            text.insert('end', line + '\n',
                        'warn' if line.startswith('WARNING') else '')
        text.configure(state='disabled')

        buttons = ttk.Frame(frame, padding=(0, 10, 0, 0))
        buttons.pack(fill='x')
        outcome = {'apply': False}

        def apply():
            outcome['apply'] = True
            dialog.destroy()
        ttk.Button(buttons, text='Apply', command=apply).pack(side='right')
        ttk.Button(buttons, text='Cancel',
                   command=dialog.destroy).pack(side='right', padx=6)
        if not renumber:
            ttk.Button(buttons, text='Renumber feeders too',
                       command=lambda: (dialog.destroy(),
                                        self.setup_board(renumber=True))
                       ).pack(side='left')
        dialog.bind('<Return>', lambda e: apply())
        dialog.bind('<Escape>', lambda e: dialog.destroy())
        self.wait_window(dialog)

        if not outcome['apply']:
            return
        self.apply_setup(trial, report)

    # -- tooltips on the controls, matched by their label
    def _tooltips(self):
        by_text = {
            'Set up board': 'setup', 'Apply': 'rotation', 'Top': 'side',
            'Bottom': 'side', 'Auto-number feeders 1…n': 'auto_number',
            'Renumber all': 'renumber', 'Plan it for me': 'setup',
            'Merge selected reels…': 'merge', 'Find look-alikes…': 'lookalikes',
            'Use as fiducial 1': 'fiducial', 'Set as fiducial 1': 'fiducial',
            'Import settings from a machine CSV…': 'import',
        }

        def walk(widget):
            try:
                text = str(widget.cget('text')).strip()
            except tk.TclError:
                text = ''
            key = by_text.get(text)
            if key:
                Tip(widget, HELP.tip(key))
            for child in widget.winfo_children():
                walk(child)
        walk(self)
        Tip(self.station_spin, HELP.tip('station'))
        Tip(self.format_box, HELP.tip('format'))

    # -- the manual
    def _manual_tab(self, parent):
        pane = ttk.Panedwindow(parent, orient='horizontal')
        pane.pack(fill='both', expand=True)
        left = ttk.Frame(pane)
        toc = tk.Listbox(left, width=26, exportselection=False,
                         font=('', 10), activestyle='none')
        toc.pack(fill='both', expand=True)
        for title, _ in HELP.SECTIONS:
            toc.insert('end', '  ' + title)
        pane.add(left, weight=0)

        right = ttk.Frame(pane)
        text = tk.Text(right, wrap='word', padx=px(14), pady=px(10),
                       font=('', 10), state='disabled', cursor='arrow')
        scroll = ttk.Scrollbar(right, orient='vertical', command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        text.pack(fill='both', expand=True)
        pane.add(right, weight=1)

        text.tag_configure('h1', font=('', 15, 'bold'), spacing3=px(8))
        text.tag_configure('h2', font=('', 11, 'bold'), spacing1=px(10),
                           spacing3=px(3))
        text.tag_configure('p', spacing3=px(6))
        text.tag_configure('li', lmargin1=px(16), lmargin2=px(30), spacing3=px(3))
        text.tag_configure('code', font=(fixed_family(), 9), background='#f3f3f3',
                           lmargin1=px(12), lmargin2=px(12), spacing1=px(4),
                           spacing3=px(8))
        text.tag_configure('note', background='#fff8e1', lmargin1=px(12),
                           lmargin2=px(12), spacing1=px(6), spacing3=px(8))
        text.tag_configure('cell', font=(fixed_family(), 9), lmargin1=px(12),
                           lmargin2=px(12))
        self.manual_text = text

        text.tag_configure('caption', foreground='#555', font=('', 9),
                           lmargin1=px(12), lmargin2=px(12), spacing3=px(10))
        text.tag_configure('gallery', spacing3=px(2))
        self._manual_photos = []        # PhotoImages must stay referenced

        def photo(key, height=None):
            try:
                import yy1images
                image = tk.PhotoImage(data=yy1images.IMAGES[key])
            except (ImportError, KeyError, tk.TclError):
                return None
            if HIDPI >= 1.5:
                # PhotoImage only scales by integers; doubled is closer to
                # the text size on a 150-200% display than unscaled.
                image = image.zoom(2, 2)
            self._manual_photos.append(image)
            return image

        def show(index):
            title, body = HELP.SECTIONS[index]
            text.configure(state='normal')
            text.delete('1.0', 'end')
            self._manual_photos = []
            text.insert('end', title + '\n', 'h1')
            for kind, content in body:
                if kind == 'img':
                    key, caption = content
                    image = photo(key)
                    if image:
                        text.insert('end', '    ')
                        text.image_create('end', image=image, padx=px(8), pady=px(4))
                        text.insert('end', '\n')
                    text.insert('end', caption + '\n', 'caption')
                elif kind == 'gallery':
                    # A row of photos, each with its caption beneath, laid out
                    # as a frame inside the text so the captions line up.
                    row = ttk.Frame(text)
                    for key, caption in content:
                        cell = ttk.Frame(row, padding=(4, 2))
                        cell.pack(side='left', anchor='n')
                        image = photo(key)
                        if image:
                            tk.Label(cell, image=image, background='white').pack()
                        ttk.Label(cell, text=caption, justify='center',
                                  font=('', 8)).pack()
                    text.window_create('end', window=row, padx=px(8), pady=px(4))
                    text.insert('end', '\n', 'gallery')
                elif kind == 'table':
                    width = max(len(row[0]) for row in content) + 2
                    for model, use in content:
                        text.insert('end', model.ljust(width) + use + '\n', 'cell')
                    text.insert('end', '\n')
                elif kind == 'li':
                    text.insert('end', '\u2022  ' + content + '\n', 'li')
                else:
                    text.insert('end', content + '\n', kind)
            text.configure(state='disabled')
            text.yview_moveto(0)

        toc.bind('<<ListboxSelect>>',
                 lambda e: show(toc.curselection()[0]) if toc.curselection() else None)
        self.manual_show = show
        self.manual_toc = toc
        toc.selection_set(0)
        show(0)

    # -- the board
    def _board_tab(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill='x', pady=(0, 6))
        self.board_label = ttk.Label(top, text='')
        self.board_label.pack(side='left')
        self.path_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text='Show placement path', variable=self.path_var,
                        command=self.draw_board).pack(side='right')
        self.colour_var = tk.StringVar(value='nozzle')
        for value, label in (('nozzle', 'Colour by nozzle'),
                             ('feeder', 'by feeder'), ('skip', 'skipped')):
            ttk.Radiobutton(top, text=label, value=value, variable=self.colour_var,
                            command=self.draw_board).pack(side='right', padx=4)

        body = ttk.Frame(parent)
        body.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(body, background='white', highlightthickness=1,
                                highlightbackground='#ccc')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda e: self.draw_board())
        self.canvas.bind('<Button-1>', self._board_click)
        self.canvas.bind('<Motion>', self._board_hover)

        panel = ttk.Frame(body, padding=(10, 0, 0, 0), width=px(300))
        panel.pack(side='right', fill='y')
        panel.pack_propagate(False)
        self.hover_label = ttk.Label(panel, text='', foreground='#666')
        self.hover_label.pack(anchor='w')
        self.part_title = ttk.Label(panel, text='Click a part', font=('', 12, 'bold'))
        self.part_title.pack(anchor='w', pady=(6, 2))
        self.part_info = ttk.Label(panel, text='', justify='left', wraplength=px(280))
        self.part_info.pack(anchor='w')

        form = ttk.LabelFrame(panel, text='This reel', padding=8)
        form.pack(fill='x', pady=10)
        self.board_vars = {}
        rows = [('feeder', 'Feeder'), ('nozzle', 'Nozzle'), ('head', 'Head'),
                ('speed', 'Speed %'), ('mode', 'Mode'), ('rot_offset', 'Rot +')]
        for i, (field, label) in enumerate(rows):
            lab = ttk.Label(form, text=label)
            lab.grid(row=i, column=0, sticky='e', padx=(0, 6), pady=2)
            Tip(lab, HELP.tip(field))
            var = tk.StringVar()
            self.board_vars[field] = var
            if field == 'mode':
                widget = ttk.Combobox(form, textvariable=var, values=MODE_CHOICES,
                                      state='readonly', width=16)
            elif field == 'head':
                widget = ttk.Combobox(form, textvariable=var, values=HEAD_CHOICES,
                                      state='readonly', width=16)
            elif field == 'nozzle':
                widget = ttk.Combobox(form, textvariable=var,
                                      values=yy1.NOZZLE_CHOICES, width=16)
            else:
                widget = ttk.Entry(form, textvariable=var, width=18)
            widget.grid(row=i, column=1, sticky='w', pady=2)
        ttk.Button(form, text='Apply to reel',
                   command=self._board_apply).grid(row=len(rows), column=0,
                                                   columnspan=2, pady=(8, 0),
                                                   sticky='ew')
        ttk.Button(panel, text='Set as fiducial 1',
                   command=self.set_part_as_fiducial).pack(anchor='w', pady=(2, 6))
        self.skip_part_var = tk.BooleanVar()
        skip = ttk.Checkbutton(panel, text='Skip this part only',
                               variable=self.skip_part_var,
                               command=self._board_skip_part)
        skip.pack(anchor='w')
        Tip(skip, HELP.tip('skip'))

        legend = ttk.Frame(panel)
        legend.pack(fill='x', pady=(14, 0))
        self.legend = legend
        self._board_items = {}      # canvas item -> ref
        self._board_selected = None
        self._board_scale = None

    def _board_geometry(self):
        """Scale and offset mapping mm to canvas pixels, fit to the widget."""
        width_mm = max(self.result.width, 1.0)
        height_mm = max(self.result.height, 1.0)
        cw = max(self.canvas.winfo_width(), 50)
        ch = max(self.canvas.winfo_height(), 50)
        margin = px(24)
        scale = min((cw - 2 * margin) / width_mm, (ch - 2 * margin) / height_mm)
        ox = (cw - width_mm * scale) / 2.0
        oy = (ch - height_mm * scale) / 2.0

        def to_canvas(x_mm, y_mm):
            # The machine's Y grows upwards; the canvas's grows downwards.
            return ox + x_mm * scale, oy + (height_mm - y_mm) * scale
        return scale, to_canvas

    def _body_mm(self, part):
        ref = part['ref']
        if self.result.body_of.get(ref):
            w, h, _ = self.result.body_of[ref]
            return w, h
        name = yy1.clean(part['package']).upper()
        size = yy1.PACKAGE_SIZE.get(name)
        if size:
            return size
        # For the picture only: an IC's outline from its pin count. Nothing
        # downstream depends on this, so a rough size is better than a dot.
        pins = re.search(r'(\d{1,3})\s*$', name.replace('-', '').replace('_', ''))
        count = int(pins.group(1)) if pins else 0
        if re.match(r'^(T|L)?QFP|^QFN|^BGA|^DFN|^SON|^MLF', name):
            side = 7.0 if count <= 32 else (10.0 if count <= 64 else 14.0)
            return side, side
        if re.match(r'^(SOIC|SO|SOP|TSSOP|MSOP|SSOP|SO\d)', name):
            length = max(3.0, min(12.0, 0.65 * max(count, 8) + 0.5))
            return length, 3.9 if 'SOIC' in name or name.startswith('SO') else 4.4
        return 2.0, 1.2

    def draw_board(self):
        if not self.result or not hasattr(self, 'canvas'):
            return
        canvas = self.canvas
        canvas.delete('all')
        self._board_items.clear()
        side = self.side.get()
        parts = k2y.ordered(self.result, side)
        profile = self.result.profile
        estimated = getattr(self.result, 'board_estimated', False)
        self.board_label.configure(
            text='%s side  %.1f x %.1f mm%s  --  %d parts'
                 % ('Top' if side == 'T' else 'Bottom', self.result.width,
                    self.result.height,
                    ' (estimated from the parts)' if estimated else '',
                    len(parts)))
        if not parts:
            return
        scale, to_canvas = self._board_geometry()
        self._board_scale = (scale, to_canvas)

        x0, y0 = to_canvas(0, 0)
        x1, y1 = to_canvas(self.result.width, self.result.height)
        canvas.create_rectangle(x0, y0, x1, y1, outline='#333', width=2,
                                fill='#fafaf0')
        canvas.create_text(x0 + 4, y0 - 4, anchor='sw', text='origin',
                           fill='#999', font=('', 8))

        # Fiducial marks the board carries (small rings), and the one set in
        # the job header (crosshair) -- so a wrong number is visible at once.
        for ref, fx, fy, fside in getattr(self.result, 'fiducials', []):
            if fside != side:
                continue
            cx, cy = to_canvas(fx, fy)
            r = px(5)
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline='#c62828',
                               width=2)
            canvas.create_text(cx + r + 2, cy, anchor='w', text=ref,
                               fill='#c62828', font=('', 7))
        job = profile.job
        if job.fid_x or job.fid_y:
            cx, cy = to_canvas(float(job.fid_x), float(job.fid_y))
            r = px(9)
            canvas.create_line(cx - r, cy, cx + r, cy, fill='#c62828', width=2)
            canvas.create_line(cx, cy - r, cx, cy + r, fill='#c62828', width=2)
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline='#c62828')
            canvas.create_text(cx, cy + r + 2, anchor='n', text='fiducial 1',
                               fill='#c62828', font=('', 8, 'bold'))

        used_colours = {}
        for part in parts:
            key = yy1.type_key(part['package'], part['comment'])
            nozzle = profile.nozzle_for(key, part.get('nozzle', ''))
            settings = profile.settings_for(part['package'], part['comment'],
                                            part['ref'])
            how = self.colour_var.get()
            if how == 'feeder':
                colour = '#%02x%02x%02x' % tuple(
                    int(120 + 120 * abs(math.sin((settings['feeder'] * (i + 1)) * 1.7)))
                    for i in range(3))
                label = 'feeder %d' % settings['feeder']
            elif how == 'skip':
                colour = '#bdbdbd' if settings['skip'] else '#66bb6a'
                label = 'skipped' if settings['skip'] else 'placed'
            else:
                colour = NOZZLE_COLOURS.get(nozzle, NO_NOZZLE_COLOUR)
                label = nozzle or 'no nozzle'
            used_colours[label] = colour

            w, h = self._body_mm(part)
            cx, cy = to_canvas(part['x'], part['y'])
            angle = math.radians(part['rot'])
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            corners = []
            for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2),
                           (w / 2, h / 2), (-w / 2, h / 2)):
                rx = dx * cos_a - dy * sin_a
                ry = dx * sin_a + dy * cos_a
                corners += [cx + rx * scale, cy - ry * scale]
            stipple = 'gray25' if settings['skip'] else ''
            outline = '#000' if part['ref'] == self._board_selected else '#555'
            item = canvas.create_polygon(
                corners, fill=colour, outline=outline,
                width=3 if part['ref'] == self._board_selected else 1,
                stipple=stipple)
            self._board_items[item] = part['ref']
            # pin-1 tick, so rotation is visible
            tx = cx + (w / 2) * cos_a * scale
            ty = cy - (w / 2) * sin_a * scale
            canvas.create_line(cx, cy, tx, ty, fill='#000', width=1)
            if scale * max(w, h) > px(22):
                canvas.create_text(cx, cy, text=part['ref'], font=('', 7),
                                   fill='#000')

        if self.path_var.get() and len(parts) > 1:
            points = []
            for part in parts:
                points += list(to_canvas(part['x'], part['y']))
            canvas.create_line(points, fill='#1976d2', width=1, arrow='last')

        for child in self.legend.winfo_children():
            child.destroy()
        for label in sorted(used_colours, key=lambda l: (
                yy1.NOZZLE_ORDER.index(l) if l in yy1.NOZZLE_ORDER else 99, l)):
            row = ttk.Frame(self.legend)
            row.pack(anchor='w')
            tk.Label(row, background=used_colours[label], width=2).pack(side='left')
            ttk.Label(row, text=' ' + label).pack(side='left')

    def _part_at(self, event):
        items = self.canvas.find_overlapping(event.x - 2, event.y - 2,
                                             event.x + 2, event.y + 2)
        for item in reversed(items):
            if item in self._board_items:
                return self._board_items[item]
        return None

    def _board_hover(self, event):
        ref = self._part_at(event)
        if ref:
            part = next(p for p in self.result.sides[self.side.get()]
                        if p['ref'] == ref)
            self.hover_label.configure(
                text='%s  %s  %s  (%.2f, %.2f) %g°'
                     % (ref, part['comment'], part['package'],
                        part['x'], part['y'], part['rot']))
        else:
            self.hover_label.configure(text='')

    def _board_click(self, event):
        ref = self._part_at(event)
        if not ref:
            return
        self.select_part(ref)

    def select_part(self, ref):
        self._board_selected = ref
        part = next(p for p in self.result.sides[self.side.get()]
                    if p['ref'] == ref)
        profile = self.result.profile
        key = yy1.type_key(part['package'], part['comment'])
        settings = profile.settings_for(part['package'], part['comment'], ref)
        siblings = [p['ref'] for p in self.result.sides[self.side.get()]
                    if yy1.type_key(p['package'], p['comment']) == key]
        self.part_title.configure(text='%s   %s' % (ref, part['comment']))
        self.part_info.configure(
            text='%s\nat %.2f, %.2f mm, %g°\nreel of %d: %s'
                 % (part['package'], part['x'], part['y'], part['rot'],
                    len(siblings), ', '.join(siblings[:10])
                    + (' …' if len(siblings) > 10 else '')))
        self.board_vars['feeder'].set(str(settings['feeder']))
        self.board_vars['nozzle'].set(profile.nozzle_for(key, part.get('nozzle', '')))
        self.board_vars['head'].set(str(settings['head']))
        self.board_vars['speed'].set(str(settings['speed']))
        self.board_vars['mode'].set(show_mode(settings['mode']))
        self.board_vars['rot_offset'].set('%g' % profile.types.get(key, {})
                                          .get('rot_offset', 0.0))
        self.skip_part_var.set(bool(profile.parts.get(ref, {}).get('skip', 0)))
        if ref in self.part_rows:
            self.part_tree.selection_set(ref)
            self.part_tree.see(ref)
        self.draw_board()

    def _board_apply(self):
        ref = self._board_selected
        if not ref or not self.result:
            return
        part = next(p for p in self.result.sides[self.side.get()]
                    if p['ref'] == ref)
        key = yy1.type_key(part['package'], part['comment'])
        profile = self.result.profile
        try:
            for field in ('feeder', 'head', 'speed', 'rot_offset'):
                profile.set_type(key, field,
                                 parse_field(field, self.board_vars[field].get()))
            profile.set_type(key, 'mode',
                             parse_field('mode', self.board_vars['mode'].get().split()[0]))
            nozzle = yy1.clean(self.board_vars['nozzle'].get()).upper()
            if nozzle:
                profile.set_type(key, 'nozzle', nozzle)
        except (ValueError, IndexError) as exc:
            messagebox.showwarning(APP, str(exc))
            return
        self.say('%s: reel settings updated from the board view' % key.replace('|', ' '))
        self.mark_dirty()
        self.refresh()

    def _board_skip_part(self):
        ref = self._board_selected
        if not ref:
            return
        if self.skip_part_var.get():
            self.result.profile.set_part(ref, 'skip', 1)
        else:
            entry = self.result.profile.parts.get(ref, {})
            entry.pop('skip', None)
            if not entry:
                self.result.profile.parts.pop(ref, None)
        self.mark_dirty()
        self.refresh()

    # -- the Feeders right-click menu
    def _type_menu(self, event):
        if not self.result:
            return
        row = self.type_tree.identify_row(event.y)
        if row and row not in self.type_tree.selection():
            self.type_tree.selection_set(row)
            self.type_tree.focus(row)
        selected = [self.type_rows[i] for i in self.type_tree.selection()
                    if i in self.type_rows]
        if not selected:
            return
        menu = tk.Menu(self, tearoff=0)
        n = len(selected)
        what = '%d reel%s' % (n, '' if n == 1 else 's')
        nozzles = tk.Menu(menu, tearoff=0)
        for nozzle in yy1.NOZZLE_CHOICES:
            nozzles.add_command(label=nozzle,
                                command=lambda z=nozzle: self.set_nozzle_selected(z))
        nozzles.add_separator()
        nozzles.add_command(label='clear', command=lambda: self.set_nozzle_selected(''))
        menu.add_cascade(label='Nozzle for %s' % what, menu=nozzles)
        if n >= 2:
            menu.add_command(label='Merge %s into one…' % what,
                             command=self.merge_reels)
        menu.add_command(label='Group values of this footprint…',
                         command=lambda: self.group_values(selected[0].split('|')[0]))
        menu.add_separator()
        skipped = all(self.result.profile.types.get(k, {}).get('skip') for k in selected)
        menu.add_command(label=('Place %s again' if skipped else 'Skip %s') % what,
                         command=lambda: self.skip_selected(not skipped))
        menu.tk_popup(event.x_root, event.y_root)

    def set_nozzle_selected(self, nozzle):
        keys = [self.type_rows[i] for i in self.type_tree.selection()
                if i in self.type_rows]
        for key in keys:
            if nozzle:
                self.result.profile.set_type(key, 'nozzle', nozzle)
            else:
                self.result.profile.types.get(key, {}).pop('nozzle', None)
        self.say('%s on %d reel(s)' % (nozzle or 'nozzle cleared', len(keys)))
        self.mark_dirty()
        self.refresh()

    def skip_selected(self, flag):
        keys = [self.type_rows[i] for i in self.type_tree.selection()
                if i in self.type_rows]
        for key in keys:
            self.result.profile.set_type(key, 'skip', 1 if flag else 0)
        self.say('%s %d reel(s)' % ('skipped' if flag else 'placing again', len(keys)))
        self.mark_dirty()
        self.refresh()

    def group_values(self, package=None):
        """Pick, by hand, which values of one footprint share a reel."""
        if not self.result:
            return
        groups = self.result.type_rows(self.side.get())
        packages = sorted(set(k.split('|')[0] for k in groups))
        if not packages:
            return

        dialog = tk.Toplevel(self)
        dialog.title('Group values into one reel')
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill='both', expand=True)
        top = ttk.Frame(frame)
        top.pack(fill='x')
        ttk.Label(top, text='Footprint').pack(side='left')
        ANY = '(any footprint)'
        pkg_var = tk.StringVar(value=package if package in packages else packages[0])
        pkg_box = ttk.Combobox(top, textvariable=pkg_var, values=[ANY] + packages,
                               state='readonly', width=16)
        pkg_box.pack(side='left', padx=6)
        ttk.Label(frame, text='Tick the values that are really the same part, '
                              'name the reel, and merge. Choose "(any footprint)" '
                              'to merge across footprints -- you will be asked '
                              'which part is really on the reel.',
                  foreground='#666', wraplength=px(480),
                  justify='left').pack(anchor='w', pady=(6, 6))
        listing = ttk.Frame(frame)
        listing.pack(fill='both', expand=True)
        checks = []

        def fill(*_):
            for child in listing.winfo_children():
                child.destroy()
            del checks[:]
            pkg = pkg_var.get()
            for key in sorted(k for k in groups if pkg == ANY or k.split('|')[0] == pkg):
                value = key.split('|', 1)[1]
                if pkg == ANY:
                    value = key.replace('|', '  ')
                var = tk.BooleanVar(value=False)
                text = '%-16s  %d part%s   %s' % (value or '(blank)', len(groups[key]),
                                                  '' if len(groups[key]) == 1 else 's',
                                                  ' '.join(p['ref'] for p in groups[key][:8]))
                ttk.Checkbutton(listing, text=text, variable=var,
                                command=lambda: name_var.set(
                                    name_var.get() or next((k.split('|', 1)[1] for v, k in checks if v.get()), ''))
                                ).pack(anchor='w')
                checks.append((var, key))
        pkg_box.bind('<<ComboboxSelected>>', fill)

        bottom = ttk.Frame(frame, padding=(0, 10, 0, 0))
        bottom.pack(fill='x')
        ttk.Label(bottom, text='Reel name').pack(side='left')
        name_var = tk.StringVar()
        ttk.Entry(bottom, textvariable=name_var, width=18).pack(side='left', padx=6)

        def go():
            keys = [k for v, k in checks if v.get()]
            if len(keys) < 2:
                messagebox.showinfo(APP, 'Tick at least two values.', parent=dialog)
                return
            name = name_var.get().strip() or keys[0].split('|', 1)[1]
            dialog.destroy()
            self.merge_reels(keys, name=name)
        ttk.Button(bottom, text='Merge ticked', command=go).pack(side='right')
        ttk.Button(bottom, text='Cancel', command=dialog.destroy).pack(side='right', padx=6)
        fill()

    # -- the feeder bank: the machine's slots as a picture
    BANK_COLOURS = {
        'empty': '#f7f7f7', 'listed': '#d6e4f7', 'used': '#bfe8bf',
        'load': '#ffd08a', 'differs': '#e4ccf5', 'clash': '#ff9e9e',
        'flex': '#eeeeee', 'none': '#ffffff',
    }
    BANK_WORDS = {
        'empty': 'free', 'listed': 'on the list', 'used': 'board reel matches the list',
        'load': 'LOAD for this board', 'differs': 'board reel differs from the list',
        'clash': 'two reels on one slot', 'flex': 'flexible bank', 'none': '',
    }

    def _bank_tab(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill='x')
        self.bank_label = ttk.Label(top, text='No feeder list loaded -- '
                                              'Feeder list… on the Feeders tab.')
        self.bank_label.pack(side='left')
        legend = ttk.Frame(parent)
        legend.pack(fill='x', pady=(2, 6))
        ttk.Label(legend, text='Show').pack(side='left')
        self.bank_view = tk.StringVar(value='both')
        for value, label in (('both', 'list + board'), ('list', 'list only'),
                             ('board', 'board only')):
            ttk.Radiobutton(legend, text=label, value=value, variable=self.bank_view,
                            command=self.draw_bank).pack(side='left', padx=(6, 0))
        for label, key in (('free', 'empty'), ('on the list', 'listed'),
                           ('matches', 'used'), ('LOAD', 'load'),
                           ('differs', 'differs'), ('two reels', 'clash')):
            tk.Label(legend, text=' %s ' % label, background=self.BANK_COLOURS[key],
                     relief='solid', borderwidth=1).pack(side='right', padx=3)
        ttk.Label(legend, text='each slot: upper band = the list, lower band = this board',
                  foreground='#666').pack(side='right', padx=(0, 8))

        # Picture above, tables below, with a divider to drag: on a short
        # screen the tables must not be squeezed out of sight.
        split = ttk.PanedWindow(parent, orient='vertical')
        split.pack(fill='both', expand=True)
        self._bank_split, self._bank_needed = split, 0
        split.bind('<Configure>', lambda e: self.after_idle(self._fit_bank_split))
        body = ttk.Frame(split)
        split.add(body, weight=0)       # the picture keeps its height
        self.bank = tk.Canvas(body, background='white', highlightthickness=1,
                              highlightbackground='#ccc', height=px(360))
        self.bank.pack(side='top', fill='x')
        self.bank.bind('<Configure>', lambda e: self.draw_bank())
        self.bank.bind('<Button-1>', self._bank_click)
        self.bank.bind('<Button-3>', self._bank_menu)
        self.bank.bind('<Motion>', self._bank_hover)

        # What is selected, in words, where the eye is: the slot and the
        # reel, and one button to put the one into the other.
        sel = tk.Frame(parent, background='#e3eefc', highlightthickness=1,
                       highlightbackground='#0a64d8')
        sel.pack(side='bottom', fill='x', pady=(6, 0), before=split)
        self.bank_sel = tk.Label(sel, text='Nothing selected -- click a slot, or a '
                                           'reel in the table on the right.',
                                 background='#e3eefc', foreground='#0b3d91',
                                 font=('', 10, 'bold'), anchor='w', justify='left')
        self.bank_sel.pack(side='left', fill='x', expand=True, padx=8, pady=4)
        self.bank_put_button = ttk.Button(sel, text='Put reel into slot',
                                          command=self.bank_put_selected, state='disabled')
        self.bank_put_button.pack(side='right', padx=6, pady=3)
        self._bank_reel = None      # the reel (type key) selected on this tab

        panel = ttk.Frame(parent, padding=(0, 6, 0, 0))
        panel.pack(side='bottom', fill='x', before=sel)
        self.bank_match_button = ttk.Button(panel, text='Match board to list',
                                            command=self.bank_match)
        self.bank_match_button.pack(side='left')
        Tip(self.bank_match_button,
            'Match every reel on this board against the feeder list: a reel '
            'the machine already has keeps that slot, the rest go into free '
            'slots of the right tape width, and whatever has no free slot is '
            'set to Skip (place by hand). Only feeders change -- nozzles and '
            'order are left alone. Nothing is saved until Save profile or '
            'Export.')
        self.bank_only_button = ttk.Button(panel, text='Only reels on the list',
                                           command=lambda: self.bank_match(list_only=True))
        self.bank_only_button.pack(side='left', padx=(6, 0))
        self.bank_sheet_button = ttk.Button(panel, text='Open loading sheet',
                                            command=self.open_loading_sheet, state='disabled')
        self.bank_sheet_button.pack(side='left', padx=(6, 0))
        Tip(self.bank_sheet_button,
            'The sheet written by the last match: what to load into which slot '
            '(and what to take out first), what to check, what goes by hand. '
            'Written beside the project as <board>_yy1_feeders_<side>.html and .txt.')
        self._loading_sheet = None
        Tip(self.bank_only_button,
            'Place only what is already on the machine: reels the list has '
            'keep their slot, every other reel is set to Skip and goes on the '
            'bench. Tick "Skipped parts out of CSV" (next to Export) to keep them out '
            'of the file altogether.')
        self.bank_info = ttk.Label(panel, text='Click a slot. Right-click to edit '
                                               'the list.', foreground='#666')
        ttk.Button(panel, text='Update list from this board',
                   command=self.bank_update_from_board).pack(side='right')
        ttk.Button(panel, text='Save list as…',
                   command=self.bank_save_as).pack(side='right', padx=6)
        earlier = ttk.Button(panel, text='Earlier lists…', command=self.bank_history)
        earlier.pack(side='right')
        common = ttk.Button(panel, text='Most used reels…', command=self.survey_projects)
        common.pack(side='right', padx=(0, 6))
        Tip(common, 'Scan a folder of projects (KiCad, PADS, Altium) and count on '
                    'how many boards each reel is needed -- the ones to leave on '
                    'the machine. Tick them and they go into the feeder list.')
        Tip(earlier, 'Every time the feeder list is saved, the one before is '
                     'kept in feeder_list_backups/ next to it. Pick one to '
                     'bring it back.')
        self.bank_info.pack(side='left', padx=(10, 0), fill='x', expand=True)
        self._bank_items = {}       # canvas item -> slot
        self._bank_selected = None

        # The two sides of the picture as plain lists, side by side: what the
        # machine has (the list) and what this board wants.
        lists = ttk.Frame(split, padding=(0, 8, 0, 0))
        split.add(lists, weight=1)
        left = ttk.LabelFrame(lists, text='On the machine (feeder list)', padding=4)
        left.pack(side='left', fill='both', expand=True, padx=(0, 4))
        self.list_tree = ttk.Treeview(left, columns=['slot', 'width', 'value', 'use'],
                                      show='headings', height=6)
        for field, head, width, anchor in (('slot', 'Slot', 46, 'e'),
                                           ('width', 'mm', 40, 'e'),
                                           ('value', 'Reel', 120, 'w'),
                                           ('use', 'This board', 200, 'w')):
            self.list_tree.heading(field, text=head)
            self.list_tree.column(field, width=px(width), anchor=anchor,
                                  stretch=(field == 'use'))
        self.list_tree.pack(fill='both', expand=True)
        right = ttk.LabelFrame(lists, text="This board's reels", padding=4)
        right.pack(side='left', fill='both', expand=True, padx=(4, 0))
        self.board_tree = ttk.Treeview(right, columns=['reel', 'qty', 'slot', 'state'],
                                       show='headings', height=6)
        for field, head, width, anchor in (('reel', 'Reel', 170, 'w'),
                                           ('qty', 'Qty', 40, 'e'),
                                           ('slot', 'Slot', 46, 'e'),
                                           ('state', 'State', 200, 'w')):
            self.board_tree.heading(field, text=head)
            self.board_tree.column(field, width=px(width), anchor=anchor,
                                   stretch=(field == 'state'))
        self.board_tree.pack(fill='both', expand=True)
        for tree in (self.list_tree, self.board_tree):
            for tag in ('used', 'load', 'clash', 'differs'):
                tree.tag_configure(tag, background=self.BANK_COLOURS[tag])
            tree.tag_configure('hand', background='#e0e0e0')
            tree.tag_configure('noslot', background='#e0e0e0', foreground='#b00020')
            tree.tag_configure('spare', foreground='#888')
        self.list_tree.bind('<<TreeviewSelect>>', self._list_pick)
        self.board_tree.bind('<<TreeviewSelect>>', self._board_pick)
        for tree in (self.list_tree, self.board_tree):
            # when the divider moves, the selected row stays in view
            tree.bind('<Configure>', lambda e, t=tree: t.selection() and
                      self.after_idle(lambda: t.selection() and t.see(t.selection()[0])))
        # coming to this tab picks up the reel selected on the Feeders tab
        self.book.bind('<<NotebookTabChanged>>', self._bank_tab_shown, add='+')

    def _bank_tab_shown(self, *_):
        try:
            if self.book.index('current') != self.tabs['bank'] or not self.result:
                return
        except tk.TclError:
            return
        chosen = [self.type_rows[i] for i in self.type_tree.selection()
                  if i in self.type_rows]
        if len(chosen) == 1 and chosen[0] != self._bank_reel:
            self._bank_select(reel=chosen[0])

    def _reel_slot(self, key):
        """The slot a reel of this side is in, or None when it is skipped."""
        if not self.result or not key:
            return None
        parts = self.result.type_rows(self.side.get()).get(key)
        if not parts:
            return None
        settings = self.result.profile.settings_for(parts[0]['package'], parts[0]['comment'])
        return None if settings['skip'] else settings['feeder']

    def _bank_select(self, slot=None, reel=None, from_slot=False):
        """Select a slot and/or a reel on this tab, and show it everywhere:
        the picture, both tables, the bar, and the Feeders tab."""
        groups = self.result.type_rows(self.side.get()) if self.result else {}
        if reel is not None and reel not in groups:
            reel = None
        if from_slot:
            # a slot holding a board reel selects that reel; an empty one
            # keeps the reel you picked, so it can be put there
            keys = self._bank_state().get(slot, (None, [], ''))[1] if slot else []
            if keys:
                reel = keys[0]
            else:
                reel = self._bank_reel
        elif reel is not None:
            slot = self._reel_slot(reel)
        self._bank_selected, self._bank_reel = slot, reel
        if reel:
            for item, key in self.type_rows.items():
                if key == reel:
                    self.type_tree.selection_set(item)
                    self.type_tree.see(item)
                    break
        self.draw_bank()

    def _list_pick(self, *_):
        chosen = self.list_tree.selection()
        if not chosen:
            return
        try:
            slot = int(self.list_tree.set(chosen[0], 'slot'))
        except ValueError:
            return
        if slot != self._bank_selected:
            self._bank_select(slot=slot, from_slot=True)

    def _board_pick(self, *_):
        chosen = self.board_tree.selection()
        if not chosen or not chosen[0].startswith('reel:'):
            return
        key = chosen[0][len('reel:'):]
        if key != self._bank_reel:
            self._bank_select(reel=key)

    def _show_bank_selection(self, state):
        """The bar under the picture, and the tables' selection."""
        # another board, or the other side: a reel that is not here is gone
        if self._bank_reel and (not self.result or self._bank_reel not in
                                self.result.type_rows(self.side.get())):
            self._bank_reel = None
        slot, reel = self._bank_selected, self._bank_reel
        bits = []
        if reel:
            parts = self.result.type_rows(self.side.get()).get(reel, []) if self.result else []
            refs = sorted(p['ref'] for p in parts)
            home = self._reel_slot(reel)
            where = ('in slot %d' % home) if home else 'no slot (placed by hand)'
            bits.append('Reel %s  --  %d part%s (%s)  --  %s'
                        % (reel.replace('|', ' '), len(parts), '' if len(parts) == 1 else 's',
                           ' '.join(refs[:8]) + (' ...' if len(refs) > 8 else ''), where))
        if slot and (not reel or self._reel_slot(reel) != slot):
            row, keys, s = state.get(slot, (None, [], 'empty'))
            what = row[2] if row and row[2] else 'free'
            bits.append('Slot %d  --  list: %s%s' % (
                slot, what, ('  --  board: ' + ', '.join(k.replace('|', ' ') for k in keys))
                if keys else ''))
        if not bits:
            bits = ['Nothing selected -- click a slot, or a reel in the table on the right.']
        self.bank_sel.configure(text='\n'.join(bits))
        # the one move the selection suggests
        home = self._reel_slot(reel) if reel else None
        if reel and slot and slot != home:
            self.bank_put_button.configure(
                state='normal', text='Put %s into slot %d' % (reel.split('|', 1)[1], slot))
        else:
            self.bank_put_button.configure(state='disabled', text='Put reel into slot')
        # the tables follow, without firing their handlers into a loop
        iid = 'slot:%d' % slot if slot else None
        if iid and self.list_tree.exists(iid):
            self.list_tree.selection_set(iid)
            self.after_idle(lambda t=self.list_tree, i=iid: t.exists(i) and t.see(i))
        else:
            self.list_tree.selection_remove(*self.list_tree.selection())
        iid = 'reel:' + reel if reel else None
        if iid and self.board_tree.exists(iid):
            self.board_tree.selection_set(iid)
            self.after_idle(lambda t=self.board_tree, i=iid: t.exists(i) and t.see(i))
        else:
            self.board_tree.selection_remove(*self.board_tree.selection())

    def bank_put_selected(self):
        if self._bank_reel and self._bank_selected:
            self.bank_assign(self._bank_selected, [self._bank_reel])

    def fill_bank_lists(self, state):
        """The list and the board as two tables, each saying what the other
        makes of it."""
        self.list_tree.delete(*self.list_tree.get_children())
        self.board_tree.delete(*self.board_tree.get_children())
        if self.library:
            for slot, width, value, package in self.library.rows:
                row, keys, s = state.get(slot, (None, [], 'empty'))
                if s == 'clash':
                    use = 'two reels: ' + ', '.join(k.replace('|', ' ') for k in keys)
                    tag = 'clash'
                elif s == 'differs':
                    use = 'board puts %s here instead' % keys[0].replace('|', ' ')
                    tag = 'differs'
                elif keys:
                    use = ', '.join(k.replace('|', ' ') for k in keys)
                    tag = 'used' if value else 'load'
                    if not value:
                        use = 'LOAD ' + use
                elif value:
                    use, tag = 'not needed by this board' if self.result else '', 'spare'
                else:
                    use, tag = 'free', ''
                shown = value or '(empty)'
                if value and package:
                    shown = '%s  (%s)' % (value, '0' + package if package.isdigit()
                                          and len(package) == 3 else package)
                if self.list_tree.exists('slot:%d' % slot):
                    continue            # a list naming one slot twice
                self.list_tree.insert('', 'end', iid='slot:%d' % slot,
                                      values=[slot, width or '', shown, use], tags=(tag,))
        if self.result:
            profile = self.result.profile
            groups = self.result.type_rows(self.side.get())

            def slot_of(k):
                first = groups[k][0]
                return profile.settings_for(first['package'], first['comment'])['feeder']
            for key in sorted(groups, key=lambda k: (slot_of(k), k)):
                parts = groups[key]
                settings = profile.settings_for(parts[0]['package'], parts[0]['comment'])
                slot = settings['feeder']
                if settings['skip']:
                    shown = ''
                    source = profile.types.get(key, {}).get('feeder_source')
                    if source == 'noslot':
                        text, tag = 'no free slot -- place by hand', 'noslot'
                    elif source == 'notlisted':
                        text, tag = 'not on the list -- place by hand', 'noslot'
                    else:
                        text, tag = 'skipped -- place by hand', 'hand'
                else:
                    row, keys, s = state.get(slot, (None, [], 'load'))
                    shown = slot
                    if s == 'clash':
                        text, tag = 'slot shared with another reel', 'clash'
                    elif s == 'used':
                        text, tag = 'already on the machine', 'used'
                    elif s == 'differs':
                        text, tag = ('slot %d holds %s on the list -- swap the reel'
                                     % (slot, row[2]), 'differs')
                    else:
                        text, tag = 'LOAD into slot %d' % slot, 'load'
                self.board_tree.insert('', 'end', iid='reel:' + key,
                                       values=[key.replace('|', '  '), len(parts),
                                               shown, text], tags=(tag,))

    def _bank_state(self):
        """slot -> (library row or None, [board reel keys], state)."""
        lib = self.library
        reels = {}
        if self.result:
            profile = self.result.profile
            for key, parts in self.result.type_rows(self.side.get()).items():
                settings = profile.settings_for(parts[0]['package'], parts[0]['comment'])
                if settings['skip']:
                    continue
                reels.setdefault(settings['feeder'], []).append(key)
        state = {}
        for slot in range(1, 81):
            row = lib.slot(slot) if lib else None
            keys = reels.get(slot, [])
            listed = bool(row and row[2])
            if len(keys) > 1:
                s = 'clash'
            elif keys and listed:
                s = 'used' if yy1.values_match(row[2], keys[0].split('|', 1)[1]) else 'differs'
            elif keys:
                s = 'load'
            elif listed:
                s = 'listed'
            else:
                s = 'flex' if slot > yy1.TAPE_LAST else 'empty'
            state[slot] = (row, keys, s)
        return state

    def _bank_missing(self):
        """Reels of this side that have no slot at all: skipped for want of
        one, skipped because the list lacks them, or skipped by hand."""
        noslot, notlisted, hand = [], [], []
        if self.result:
            profile = self.result.profile
            for key, parts in self.result.type_rows(self.side.get()).items():
                settings = profile.settings_for(parts[0]['package'], parts[0]['comment'])
                if settings['skip']:
                    source = profile.types.get(key, {}).get('feeder_source')
                    if source == 'noslot':
                        noslot.append(key)
                    elif source == 'notlisted':
                        notlisted.append(key)
                    else:
                        hand.append(key)
        return sorted(noslot), sorted(notlisted), sorted(hand)

    def draw_bank(self):
        if not hasattr(self, 'bank'):
            return
        canvas = self.bank
        canvas.delete('all')
        self._bank_items.clear()
        state = self._bank_state()
        self.fill_bank_lists(state)
        if hasattr(self, 'bank_sel'):
            self._show_bank_selection(state)
        view = self.bank_view.get() if hasattr(self, 'bank_view') else 'both'
        colours = self.BANK_COLOURS
        # Frames drawn last, over the bands, so they are never hidden. The
        # selected slot gets a solid frame; with a reel selected that has no
        # slot yet, the free slots of its tape width get a dashed one.
        marks = []
        could_go = set()
        reel = getattr(self, '_bank_reel', None)
        home = self._reel_slot(reel) if reel else None
        if reel and self.library and (home is None or state[home][2] == 'load'):
            need = yy1.tape_width(reel.split('|', 1)[0])
            for number, (row, keys, s) in state.items():
                if row and not row[2] and not keys and number <= yy1.TAPE_LAST and (
                        not need or not row[1] or row[1] == need):
                    could_go.add(number)
        cw = max(canvas.winfo_width(), 200)
        margin = px(10)
        head_h = px(15)
        band_h = px(31)
        box_h = head_h + 2 * band_h
        gap = px(8)
        rows = [(range(1, 27), 'tape 1-26'), (range(27, 53), 'tape 27-52'),
                (range(53, 81), 'flexible 53-80 (tray, tube, strip)')]
        y = margin
        for numbers, title in rows:
            per_row = len(numbers)
            if per_row != 26 and self._bank_selected not in numbers and not any(
                    state[n][1] or (state[n][0] and state[n][0][2]) for n in numbers):
                # nothing on the flexible bank: one line, not a row of boxes
                canvas.create_text(margin, y, anchor='nw', fill='#666', font=('', 8),
                                   text=title + ' -- nothing on it')
                y += px(20)
                continue
            canvas.create_text(margin, y, anchor='nw', text=title, fill='#666',
                               font=('', 8))
            y += px(13)
            w = (cw - 2 * margin) / per_row
            tape = per_row == 26
            h = box_h if tape else px(30)
            fit = max(4, int((w - 6) / px(6.2)))
            for i, slot in enumerate(numbers):
                row, keys, s = state[slot]
                x0, y0 = margin + i * w, y
                x1, y1 = x0 + w - 2, y0 + h
                listed = bool(row and row[2])
                list_text = row[2] if listed else ('free' if row else '')
                list_lines = [list_text] if list_text else []
                if listed and row[3]:
                    fp = row[3]                     # the list's footprint column
                    list_lines.append('0' + fp if fp.isdigit() and len(fp) == 3 else fp)
                list_fill = colours['listed'] if listed else colours['empty']
                board_lines = []
                if keys:
                    board_fill = colours[s]
                    for key in keys:
                        pkg, val = key.split('|', 1)
                        if s == 'used':
                            board_lines.append(pkg)     # the value is in the list band
                        elif s == 'load':
                            board_lines.append(val)
                        else:
                            board_lines.append('%s %s' % (pkg, val))
                    if s == 'load':
                        board_lines.insert(0, 'LOAD')
                    elif s == 'differs':
                        board_lines = board_lines[:1] + ['≠']
                else:
                    board_fill = colours['none']
                selected = slot == self._bank_selected
                outline = '#9a9a9a'
                width_line = 1
                if selected:
                    marks.append((x0, y0, x1, y1, 'selected'))
                elif slot in could_go:
                    marks.append((x0, y0, x1, y1, 'could'))
                if not tape:
                    # the flexible bank: one band, the combined state
                    item = canvas.create_rectangle(x0, y0, x1, y1, fill=colours[s],
                                                   outline=outline, width=width_line)
                    self._bank_items[item] = slot
                    canvas.create_text(x0 + 3, y0 + 2, anchor='nw', text=str(slot),
                                       font=('', 8, 'bold'))
                    line = (board_lines[-1] if board_lines and view != 'list' else
                            (list_text if view != 'board' and listed else ''))
                    if line:
                        canvas.create_text(x0 + w / 2, y0 + px(21), text=line[:fit],
                                           font=('', 7))
                    continue
                # header strip: slot number and tape width
                item = canvas.create_rectangle(x0, y0, x1, y1, fill='#ffffff',
                                               outline=outline, width=width_line)
                self._bank_items[item] = slot
                canvas.create_rectangle(x0, y0, x1, y0 + head_h, fill='#e9e9e9',
                                        outline='')
                canvas.create_text(x0 + 3, y0 + 1, anchor='nw', text=str(slot),
                                   font=('', 8, 'bold'))
                if row and row[1]:
                    canvas.create_text(x1 - 3, y0 + 2, anchor='ne',
                                       text='%d' % row[1], fill='#777', font=('', 7))
                if view == 'both':
                    bands = [(list_fill, list_lines, 'list'),
                             (board_fill, board_lines, 'board')]
                elif view == 'list':
                    bands = [(list_fill, list_lines, 'list')]
                else:
                    fill = board_fill if keys else colours['empty']
                    bands = [(fill, board_lines, 'board')]
                by = y0 + head_h
                bh = (h - head_h) / len(bands)
                for fill, lines, which in bands:
                    it = canvas.create_rectangle(x0 + 1, by, x1 - 1, by + bh - 1,
                                                 fill=fill, outline='')
                    self._bank_items[it] = slot
                    shown = lines[:2] if bh > px(28) else lines[:1]
                    for k, text in enumerate(shown):
                        bold = text == 'LOAD'
                        colour = ('#b35900' if bold else '#999' if text == 'free'
                                  else '#7a2a9a' if text.startswith('≠') else '#000')
                        canvas.create_text(x0 + w / 2, by + px(4) + k * px(13),
                                           anchor='n', text=text[:fit], fill=colour,
                                           font=('', 8, 'bold') if bold else ('', 8))
                    if which == 'list' and view == 'both':
                        canvas.create_line(x0 + 1, by + bh - 1, x1 - 1, by + bh - 1,
                                           fill='#bbb')
                    by += bh
            y += h + gap

        for x0, y0, x1, y1, kind in marks:
            if kind == 'selected':
                canvas.create_rectangle(x0 - 2, y0 - 2, x1 + 2, y1 + 2, outline='#0a64d8',
                                        width=4)
                canvas.create_polygon(x0 + (x1 - x0) / 2 - px(6), y0 - px(10),
                                      x0 + (x1 - x0) / 2 + px(6), y0 - px(10),
                                      x0 + (x1 - x0) / 2, y0 - px(3), fill='#0a64d8', outline='')
            else:
                canvas.create_rectangle(x0, y0, x1, y1, outline='#0a64d8', width=2,
                                        dash=(4, 3))

        # what has no slot at all, spelled out under the picture
        noslot, notlisted, hand = self._bank_missing()
        for keys, label, colour, font in (
                (noslot, 'No free slot -- place by hand: ', '#b00020', ('', 9, 'bold')),
                (notlisted, 'Not on the list -- place by hand: ', '#b00020', ('', 9, 'bold')),
                (hand, 'Skipped by hand: ', '#666', ('', 8))):
            if keys:
                item = canvas.create_text(
                    margin, y, anchor='nw', fill=colour, font=font,
                    width=cw - 2 * margin,
                    text=label + ', '.join(k.replace('|', ' ') for k in keys))
                y = canvas.bbox(item)[3] + px(6)     # as tall as it wrapped to
        # the picture is as tall as its content, so the tables get the rest
        needed = int(y + px(4))
        if abs(needed - canvas.winfo_height()) > 3:
            canvas.configure(height=needed)
        if needed != self._bank_needed:
            # the divider follows when the picture grows or shrinks (a red
            # "place by hand" line appearing), but leaves the tables a few
            # rows; between changes it stays where you dragged it
            self._bank_needed = needed
            self.after_idle(self._fit_bank_split)
        if self.library:
            listed = len(self.library.loaded())
            free = len(self.library.free())
        else:
            listed = free = 0
        used = sum(1 for _, keys, s in state.values() if s == 'used')
        load = sum(1 for _, keys, s in state.values() if s == 'load')
        differs = sum(1 for _, keys, s in state.values() if s == 'differs')
        clash = sum(1 for _, keys, s in state.values() if s == 'clash')
        bits = []
        if self.library:
            bits.append('%s: %d reels on the machine, %d free slots'
                        % (os.path.basename(self.library.path), listed, free))
        else:
            bits.append('no feeder list loaded -- Feeder list… on the Feeders tab')
        if self.result:
            extra = ''
            if differs:
                extra += ', %d differ from the list' % differs
            if clash:
                extra += ', %d slots with two reels' % clash
            if noslot:
                extra += ', %d with no slot' % len(noslot)
            if notlisted:
                extra += ', %d not on the list' % len(notlisted)
            bits.append('this board: %d reels already loaded, %d to LOAD%s'
                        % (used, load, extra))
        self.bank_label.configure(text='   •   '.join(bits))

    def _fit_bank_split(self):
        split = self._bank_split
        total = split.winfo_height()
        if total < 50:
            return
        want = self._bank_needed + 4
        try:
            # the tables keep room for their titles and two rows; the
            # picture gets the rest, and at least its first tape row
            split.sashpos(0, max(px(160), min(want, total - px(190))))
        except tk.TclError:
            pass
        # the tables just changed height: bring the selected rows back in view
        self.update_idletasks()
        for tree in (self.list_tree, self.board_tree):
            chosen = tree.selection()
            if chosen:
                tree.see(chosen[0])

    def _bank_slot_at(self, event):
        for item in self.bank.find_overlapping(event.x, event.y, event.x, event.y):
            if item in self._bank_items:
                return self._bank_items[item]
        return None

    def _bank_describe(self, slot):
        row, keys, s = self._bank_state()[slot]
        bits = ['slot %d' % slot]
        if row and row[1]:
            bits.append('%d mm tape' % row[1])
        bits.append('list: %s' % (row[2] if row and row[2] else 'empty'))
        if keys:
            bits.append('board: ' + ', '.join(k.replace('|', ' ') for k in keys))
        if self.BANK_WORDS.get(s) and s not in ('flex', 'empty', 'listed'):
            bits.append(self.BANK_WORDS[s])
        return '   '.join(bits)

    def _bank_hover(self, event):
        slot = self._bank_slot_at(event)
        if slot:
            self.bank_info.configure(text=self._bank_describe(slot))

    def _bank_click(self, event):
        slot = self._bank_slot_at(event)
        if not slot:
            return
        self._bank_select(slot=slot, from_slot=True)
        self.bank_info.configure(text=self._bank_describe(slot))

    def _bank_menu(self, event):
        slot = self._bank_slot_at(event)
        if not slot:
            return
        reel = self._bank_reel
        self._bank_selected = slot
        self.draw_bank()
        menu = tk.Menu(self, tearoff=0)
        # the reel picked on this tab first, else what the Feeders tab has
        if reel:
            selected = [reel]
        else:
            selected = [self.type_rows[i] for i in self.type_tree.selection()
                        if i in self.type_rows] if self.result else []
        if selected:
            menu.add_command(label='Put %s here (slot %d)'
                                   % (selected[0].replace('|', ' ') if len(selected) == 1
                                      else '%d selected reels' % len(selected), slot),
                             command=lambda: self.bank_assign(slot, selected))
        row, keys, s = self._bank_state()[slot]
        if keys and self.result:
            menu.add_command(label='Take %s out of slot %d (skip it)'
                                   % (keys[0].replace('|', ' '), slot),
                             command=lambda: self.bank_unassign(keys))
        if s == 'differs':
            menu.add_command(label='List: slot %d now holds %s'
                                   % (slot, keys[0].split('|', 1)[1]),
                             command=lambda: self.bank_take_board(slot, keys[0]))
        menu.add_command(label='Set list value for slot %d…' % slot,
                         command=lambda: self.bank_set_value(slot))
        menu.add_command(label='Clear slot %d in the list' % slot,
                         command=lambda: self.bank_clear(slot))
        menu.tk_popup(event.x_root, event.y_root)

    def _ensure_library(self):
        if not self.library:
            self.library = yy1.FeederLibrary([], '')
        return self.library

    def _bank_saved(self):
        """Edits to the list go to a CSV of our own; the original file is
        never rewritten."""
        lib = self._ensure_library()
        path = lib.path
        if not path or not path.lower().endswith('.csv'):
            path = os.path.join(os.path.expanduser('~'), 'yy1_feeder_list.csv')
        lib.save_csv(path)
        self.save_inventory()
        self._show_library()
        self.remember()
        return path

    def bank_match(self, list_only=False, ask=True):
        """Feeders for this side from the list: matched reels keep the
        machine's slot, new ones take free slots, the rest are skipped.
        With list_only, nothing is loaded: the rest are skipped."""
        if not self.result:
            messagebox.showinfo(APP, 'Open a board first.')
            return
        if not self.library:
            if messagebox.askyesno(APP, 'No feeder list is loaded. Load one now?'):
                self.load_library()
            if not self.library:
                return
        side = self.side.get()
        profile = self.result.profile
        groups = self.result.type_rows(side)
        if not groups:
            messagebox.showinfo(APP, 'Nothing on this side.')
            return
        keys = []
        for key in sorted(groups, key=lambda k: (groups[k][0]['feeder'], k)):
            settings = profile.types.get(key, {})
            if settings.get('skip') and settings.get('feeder_source') not in ('noslot', 'notlisted'):
                continue            # skipped on purpose; leave it be
            keys.append(key)
        report = []
        matched, new, manual = yy1.apply_library(keys, groups, profile, self.library,
                                                  report, retry_skipped=True,
                                                  list_only=list_only)
        self.clear_log()
        self.say('%s -- %s side' % ('Only reels on the list' if list_only else
                                    'Match board to list',
                                    'top' if side == 'T' else 'bottom'))
        self.say(*report)
        for line in self.replan_changes():
            self.say('WARNING: ' + line)
        self.mark_dirty()
        self.refresh()
        self.write_loading_sheet()
        self._last_match = (matched, new, manual)
        if list_only:
            self.bank_info.configure(
                text='%d on the machine, %d not on the list (skipped, by hand) -- '
                     'details in the log.' % (len(matched), len(manual)))
            if ask and manual and not profile.omit_skipped and messagebox.askyesno(
                    APP, '%d reel(s) are not on the list and will be placed by '
                         'hand.\n\nLeave them out of the CSV altogether? (The '
                         'operator sheet still lists them.)' % len(manual)):
                self.omit_var.set(True)
                self.omit_changed()
        else:
            self.bank_info.configure(
                text='%d on the machine, %d to LOAD, %d with no slot -- details in the log.'
                     % (len(matched), len(new), len(manual)))

    def write_loading_sheet(self, folder=None, quiet=False):
        """The operator's feeder sheet for this side, beside the project --
        or in `folder` (the export's, so it goes to the SD card too)."""
        if not self.result:
            return None
        folder = folder or os.path.dirname(self.result.profile_path or '') or \
            self.result.outdir or '.'
        base = os.path.join(folder, '%s_yy1' % (self.result.prefix or 'board'))
        try:
            paths = yy1.write_loading_sheet(
                base, self.result.type_rows(self.side.get()), self.result.profile,
                self.library, self.side.get(), self.result.prefix,
                os.path.basename(self.library.path) if self.library else '')
        except (IOError, OSError) as exc:
            self.say('could not write the loading sheet: %s' % exc)
            return None
        self._loading_sheet = paths[1]
        self.bank_sheet_button.configure(state='normal')
        if not quiet:
            self.say('sheet    %s  (and .txt) -- Open loading sheet on the Feeder bank tab'
                     % paths[1])
        # And the list as it will be once the sheet is done: the feeder
        # list with this board's reels in their slots, ready to be the next
        # feeders.ods.
        if self.library:
            ods = '%s_feeders_%s.ods' % (base, 'top' if self.side.get() == 'T' else 'bottom')
            try:
                self.completed_library().save_ods(ods)
                if not quiet:
                    self.say('list     %s -- the feeder list with this board loaded '
                             '(open it as the list for the next board)' % ods)
            except (IOError, OSError) as exc:
                self.say('could not write %s: %s' % (ods, exc))
        return paths

    def completed_library(self):
        """The feeder list with this side's reels written into their slots."""
        reels = {}
        for slot, (row, keys, s) in self._bank_state().items():
            if len(keys) == 1:
                pkg, val = keys[0].split('|', 1)
                reels[slot] = (pkg, val)
        return self.library.completed(reels)

    def open_loading_sheet(self):
        if not self._loading_sheet or not os.path.isfile(self._loading_sheet):
            if not self.write_loading_sheet():
                return
        import webbrowser
        webbrowser.open('file:///' + os.path.abspath(self._loading_sheet).replace('\\', '/'))

    def bank_assign(self, slot, keys):
        for key in keys:
            self.result.profile.set_type(key, 'feeder', slot)
            if self.result.profile.types[key].get('skip'):
                self.result.profile.set_type(key, 'skip', 0)
            self.result.profile.types[key].pop('feeder_source', None)
        self.say('slot %d: %s' % (slot, ', '.join(k.replace('|', ' ') for k in keys)))
        if len(keys) == 1:
            self._bank_reel, self._bank_selected = keys[0], slot
        self.mark_dirty()
        self.refresh()

    def bank_unassign(self, keys):
        for key in keys:
            self.result.profile.set_type(key, 'skip', 1)
            self.result.profile.types[key].pop('feeder_source', None)
        self.say('skipped: %s' % ', '.join(k.replace('|', ' ') for k in keys))
        self.mark_dirty()
        self.refresh()

    def bank_take_board(self, slot, key):
        """The list learns that this slot now holds the board's reel."""
        pkg, val = key.split('|', 1)
        lib = self._ensure_library()
        row = lib.slot(slot)
        lib.set_value(slot, val, yy1.tape_width(pkg) or (row[1] if row else 0), pkg)
        path = self._bank_saved()
        self.say('feeder list: slot %d = %s, saved to %s' % (slot, val, path))
        self.draw_bank()

    def bank_set_value(self, slot):
        from tkinter import simpledialog
        lib = self._ensure_library()
        row = lib.slot(slot)
        value = simpledialog.askstring(
            'Feeder list', 'Reel in slot %d (value the machine shows):' % slot,
            initialvalue=row[2] if row else '', parent=self)
        if value is None:
            return
        width = simpledialog.askinteger(
            'Feeder list', 'Tape width in mm for slot %d (8, 12, 16, 24):' % slot,
            initialvalue=(row[1] if row and row[1] else 8), parent=self, minvalue=0,
            maxvalue=56)
        lib.set_value(slot, value, width or 0)
        path = self._bank_saved()
        self.say('feeder list: slot %d = %s (%d mm), saved to %s' % (slot, value, width or 0, path))
        self.draw_bank()

    def bank_clear(self, slot):
        lib = self._ensure_library()
        lib.clear(slot)
        path = self._bank_saved()
        self.say('feeder list: slot %d cleared, saved to %s' % (slot, path))
        self.draw_bank()

    def bank_update_from_board(self):
        """Every reel this board has in a slot becomes the list's entry there."""
        if not self.result:
            return
        lib = self._ensure_library()
        state = self._bank_state()
        changed = 0
        for slot, (row, keys, s) in state.items():
            if len(keys) != 1:
                continue
            pkg, val = keys[0].split('|', 1)
            if not (row and row[2] and yy1.values_match(row[2], val)):
                lib.set_value(slot, val, yy1.tape_width(pkg) or (row[1] if row else 0), pkg)
                changed += 1
        path = self._bank_saved()
        self.say('feeder list updated from this board: %d slot(s) changed, saved to %s'
                 % (changed, path))
        self.draw_bank()

    def survey_projects(self):
        """Which reels most boards need: scan a folder of projects, tick the
        ones to keep on the machine, put them into the feeder list."""
        import yy1survey
        start = self.setting('survey_root', '') or (
            os.path.dirname(self.result.project) if self.result else '')
        folder = filedialog.askdirectory(
            title='Folder with your projects (KiCad, PADS and Altium, any depth)',
            initialdir=start or None)
        if not folder:
            return
        self.set_setting('survey_root', folder)
        self.clear_log()
        self.say('Surveying %s ...' % folder)
        self.update_idletasks()
        try:
            tally, boards = yy1survey.survey(folder, say=self.say)
        except Exception:
            messagebox.showerror(APP, traceback.format_exc())
            return
        if not tally:
            messagebox.showinfo(APP, 'No board could be read under %s -- see the '
                                     'report for what was found.' % folder)
            return
        self._survey_dialog(folder, tally, boards)

    def _survey_dialog(self, folder, tally, boards):
        import yy1survey
        listed = yy1survey.on_list(tally, self.library)
        order = yy1survey.ranked(tally)
        dialog = tk.Toplevel(self)
        dialog.title('Most used reels -- %d boards under %s' % (len(boards), os.path.basename(folder)))
        dialog.transient(self)
        dialog.geometry('%dx%d' % (px(900), px(620)))
        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Boards read: %s' % ', '.join(n for n, _, _ in boards),
                  wraplength=px(860), justify='left', foreground='#666').pack(anchor='w')
        ttk.Label(frame, text='Sorted by how many boards need the reel. Tick the '
                              'ones to keep on the machine; "Add ticked" puts them '
                              'into the feeder list\'s free tape slots by tape '
                              'width. Green rows are on the list already.',
                  wraplength=px(860), justify='left').pack(anchor='w', pady=(4, 6))
        cols = ['tick', 'value', 'package', 'boards', 'parts', 'slot', 'used']
        tree = ttk.Treeview(frame, columns=cols, show='headings', height=18)
        for field, head, width, anchor in (('tick', '', 30, 'center'),
                                           ('value', 'Value', 150, 'w'),
                                           ('package', 'Footprint', 110, 'w'),
                                           ('boards', 'Boards', 70, 'e'),
                                           ('parts', 'Parts', 60, 'e'),
                                           ('slot', 'On list', 60, 'e'),
                                           ('used', 'Boards using it', 300, 'w')):
            tree.heading(field, text=head)
            tree.column(field, width=px(width), anchor=anchor, stretch=(field == 'used'))
        tree.tag_configure('listed', background=self.BANK_COLOURS['used'])
        tree.tag_configure('ticked', background='#fff3c4')
        scroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='left', fill='y')
        ticked = set()

        def row_tags(key):
            if key in listed:
                return ('listed',)
            return ('ticked',) if key in ticked else ()

        def fill():
            tree.delete(*tree.get_children())
            for key in order:
                e = tally[key]
                tree.insert('', 'end', iid=key, values=[
                    '\u2611' if key in ticked else ('' if key in listed else '\u2610'),
                    e['value'], e['package'], '%d / %d' % (len(e['boards']), len(boards)),
                    e['parts'], listed.get(key, ''), ', '.join(e['boards'])],
                    tags=row_tags(key))

        def toggle(event):
            item = tree.identify_row(event.y)
            if not item or item in listed:
                return
            if item in ticked:
                ticked.discard(item)
            else:
                ticked.add(item)
            tree.item(item, values=['\u2611' if item in ticked else '\u2610'] +
                      list(tree.item(item, 'values'))[1:], tags=row_tags(item))
            count.configure(text='%d ticked' % len(ticked))
        tree.bind('<Button-1>', toggle, add='+')

        side = ttk.Frame(dialog, padding=(0, 10, 10, 10))
        side.pack(side='right', fill='y')
        ttk.Label(side, text='Tick the top').pack(anchor='w')
        top = ttk.Spinbox(side, from_=1, to=80, width=5, justify='center')
        top.set(min(20, len(order)))
        top.pack(anchor='w')

        def tick_top():
            want = int(top.get() or 0)
            ticked.clear()
            for key in order:
                if key not in listed and (len(tally[key]['boards']) > 1 or want > 0):
                    if len(ticked) >= want:
                        break
                    ticked.add(key)
            fill()
            count.configure(text='%d ticked' % len(ticked))
        ttk.Button(side, text='not on the list', command=tick_top).pack(anchor='w', pady=(2, 10))
        count = ttk.Label(side, text='0 ticked', foreground='#666')
        count.pack(anchor='w')

        def add():
            if not ticked:
                messagebox.showinfo(APP, 'Tick some reels first.', parent=dialog)
                return
            lib = self._ensure_library()
            keys = [k for k in order if k in ticked]
            placed, left = yy1survey.place_static(keys, tally, lib)
            path = self._bank_saved()
            for key, slot in placed:
                listed[key] = slot
                ticked.discard(key)
            fill()
            self.say('static reels added to the feeder list: %d, saved to %s' % (len(placed), path))
            for key, slot in placed:
                self.say('   slot %-3d <- %s' % (slot, key.replace('|', ' ')))
            if left:
                self.say('WARNING: no free tape slot for %d reel(s): %s'
                         % (len(left), ', '.join(k.replace('|', ' ') for k in left)))
                messagebox.showwarning(APP, 'No free tape slot for %d reel(s); the rest '
                                            'went in. Free some slots on the Feeder bank '
                                            'tab and add again.' % len(left), parent=dialog)
            self.refresh()
        ttk.Button(side, text='Add ticked to feeder list', command=add).pack(anchor='w', pady=(10, 4))
        ttk.Label(side, text='free tape slots: %d' % (
            len([r for r in self.library.free() if r[0] <= yy1.TAPE_LAST]) if self.library else 0),
            foreground='#666').pack(anchor='w')

        def save():
            path = filedialog.asksaveasfilename(
                parent=dialog, title='Save the survey', defaultextension='.csv',
                filetypes=[('CSV', '*.csv')], initialfile='most_used_reels.csv')
            if path:
                yy1survey.write_csv(path, tally, boards, listed)
                self.say('survey saved to %s' % path)
        ttk.Button(side, text='Save survey as CSV…', command=save).pack(anchor='w', pady=(20, 4))
        ttk.Button(side, text='Close', command=dialog.destroy).pack(anchor='w', side='bottom')
        fill()

    def bank_history(self):
        """Bring back a feeder list as it was before a save."""
        lib = self.library
        current = lib.path if lib else ''
        # edits to a spreadsheet list go to the tool's own CSV, so its
        # backups are looked for there as well as beside the list itself
        own = os.path.join(os.path.expanduser('~'), 'yy1_feeder_list.csv')
        found = []
        for path in dict.fromkeys(p for p in (current, own) if p):
            found += yy1.backups_of(path)
        found.sort(key=os.path.getmtime, reverse=True)
        if not found:
            messagebox.showinfo(APP, 'No earlier feeder list yet. One is kept '
                                     'each time the list is saved.')
            return
        dialog = tk.Toplevel(self)
        dialog.title('Earlier feeder lists')
        dialog.transient(self)
        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Newest first. The one in use now is kept too '
                              'before it is replaced.', foreground='#666').pack(
            anchor='w', pady=(0, 6))
        tree = ttk.Treeview(frame, columns=['when', 'reels', 'file'], show='headings',
                            height=min(14, len(found)))
        for field, head, width in (('when', 'Saved', 150), ('reels', 'Reels', 60),
                                   ('file', 'File', 330)):
            tree.heading(field, text=head)
            tree.column(field, width=px(width), anchor='w')
        tree.pack(fill='both', expand=True)
        import time
        for path in found:
            try:
                reels = len(yy1.FeederLibrary.read(path).loaded())
            except Exception:
                reels = '?'
            tree.insert('', 'end', iid=path, values=[
                time.strftime('%d.%m.%Y %H:%M:%S', time.localtime(os.path.getmtime(path))),
                reels, os.path.basename(path)])
        tree.selection_set(found[0])

        def use(*_):
            chosen = tree.selection()
            if not chosen:
                return
            old = yy1.FeederLibrary.read(chosen[0])
            lib = self._ensure_library()
            lib.rows = [list(r) for r in old.rows]
            path = self._bank_saved()
            dialog.destroy()
            self.say('feeder list brought back from %s (saved as %s)'
                     % (os.path.basename(chosen[0]), path))
            self.refresh()
        buttons = ttk.Frame(frame, padding=(0, 8, 0, 0))
        buttons.pack(fill='x')
        ttk.Button(buttons, text='Use this list', command=use).pack(side='right')
        ttk.Button(buttons, text='Cancel', command=dialog.destroy).pack(side='right', padx=6)
        ttk.Button(buttons, text='Open folder', command=lambda: os.startfile(
            os.path.dirname(found[0]))).pack(side='left')
        tree.bind('<Double-1>', use)

    def bank_save_as(self):
        if not self.library:
            return
        path = filedialog.asksaveasfilename(
            title='Save feeder list', defaultextension='.ods',
            filetypes=[('Spreadsheet', '*.ods'), ('CSV', '*.csv')],
            initialfile=os.path.splitext(os.path.basename(self.library.path or 'feeders'))[0] + '.ods')
        if path:
            if path.lower().endswith('.ods'):
                self.library.save_ods(path)
                self.library.path = path
            else:
                self.library.save_csv(path)
            self.save_inventory()
            self._show_library()
            self.say('feeder list saved to %s' % path)

    # -- reporting
    def say(self, *lines):
        self.log.configure(state='normal')
        for line in lines:
            self.log.insert('end', line.rstrip() + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def clear_log(self):
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

    # -- undo: the board's profile and the feeder list, after every change
    HISTORY = 60

    def _snapshot(self):
        import json
        profile = self.result.profile.to_dict() if self.result else None
        library = ([list(r) for r in self.library.rows], self.library.path) \
            if self.library else None
        return json.dumps([profile, library], sort_keys=True, default=str)

    def reset_history(self):
        self._history_owner = self.result
        self._history = [self._snapshot()] if self.result else []
        self._future = []
        self._update_undo_buttons()

    def remember(self):
        """Keep the state as it is now, if it changed."""
        if not self.result or getattr(self, '_restoring', False):
            return
        if getattr(self, '_history_owner', None) is not self.result:
            self.reset_history()
            return
        now = self._snapshot()
        if self._history and self._history[-1] == now:
            return
        self._history.append(now)
        del self._history[:-self.HISTORY]
        self._future = []
        self._update_undo_buttons()

    def _restore(self, snapshot):
        import json
        profile, library = json.loads(snapshot)
        self._restoring = True
        try:
            self.result.profile = yy1.Profile(profile)
            if library is not None:
                rows, path = library
                if (not self.library or [list(r) for r in self.library.rows] != rows):
                    lib = yy1.FeederLibrary([], path)
                    lib.rows = [list(r) for r in rows]
                    self.library = lib
                    self._bank_saved()
            profile = self.result.profile
            self.rot_var.set('%g' % profile.rot_offset)
            self.order_var.set(profile.order)
            self.station_var.set(str(profile.station_positions))
            self.load_job_tab()
            self._type_order = []
            self.refresh()
            self.mark_dirty(True)
        finally:
            self._restoring = False
        self._update_undo_buttons()

    def undo(self):
        if not self.result or len(getattr(self, '_history', [])) < 2:
            return
        self._future.append(self._history.pop())
        self._restore(self._history[-1])
        self.say('undone -- Ctrl+Y brings it back')

    def redo(self):
        if not self.result or not getattr(self, '_future', None):
            return
        state = self._future.pop()
        self._history.append(state)
        self._restore(state)
        self.say('redone')

    def _typing(self, event):
        # Ctrl+Z in a text box being typed into is the box's own business
        return isinstance(event.widget, (tk.Entry, ttk.Entry, tk.Text, ttk.Combobox,
                                         ttk.Spinbox))

    def _undo_key(self, event):
        if not self._typing(event):
            self.undo()
            return 'break'

    def _redo_key(self, event):
        if not self._typing(event):
            self.redo()
            return 'break'

    def _update_undo_buttons(self):
        if not hasattr(self, 'undo_button'):
            return
        self.undo_button.configure(
            state='normal' if len(getattr(self, '_history', [])) > 1 else 'disabled')
        self.redo_button.configure(
            state='normal' if getattr(self, '_future', None) else 'disabled')

    def _undo_tip(self, which):
        if which == 'undo':
            n = max(0, len(getattr(self, '_history', [])) - 1)
            return ('Step back one change (Ctrl+Z). %d step(s) available. Covers '
                    'reels, nozzles, feeders, order, job header and the feeder '
                    'list.' % n)
        return 'Bring back what Undo took away (Ctrl+Y).'

    def mark_dirty(self, dirty=True):
        self._dirty = dirty
        if dirty:
            self.remember()
        name = self.result.prefix if self.result else ''
        self.title('%s%s%s' % (APP, ' — ' + name if name else '',
                               ' *' if dirty else ''))

    # -- opening
    def open_board_file(self):
        """A KiCad board picked as a file: for a folder with more than one
        .kicad_pcb, this says which board the export belongs to."""
        path = filedialog.askopenfilename(
            title='KiCad board (its production/ export is read alongside)',
            filetypes=[('KiCad board', '*.kicad_pcb')])
        if path:
            self.open_project(path)

    def open_project(self, path=None):
        path = path or filedialog.askdirectory(title='KiCad project directory')
        if not path:
            return
        self.clear_log()
        try:
            args = self._args(path)
            result = k2y.analyse(args)
        except SystemExit as exc:
            messagebox.showerror(APP, str(exc))
            return
        except Exception:
            messagebox.showerror(APP, traceback.format_exc())
            return
        self.result = result
        self._type_order = []
        result.profile.board_w, result.profile.board_h = result.width, result.height
        self.say(*result.log)
        if not result.origin_ok:
            self.say('', 'Coordinates are compensated either way, but re-export '
                         'if the board has moved since.')
        self.side.set('T' if result.sides['T'] else 'B')
        self.rot_var.set('%g' % result.profile.rot_offset)
        self.order_var.set(result.profile.order)
        self.station_var.set(str(result.profile.station_positions))
        self.load_job_tab()
        self.refresh()
        self.mark_dirty(False)
        self.status.set('%s — %.1f × %.1f mm, profile %s'
                        % (result.prefix, result.width, result.height,
                           os.path.basename(result.profile_path)))

    def _args(self, project):
        import argparse
        return argparse.Namespace(
            project=project, pcb=None, positions=None, bom=None, outdir=None,
            prefix=None, variant=None, exclude='', only='', origin_ok=True,
            include_tht=False, max_size=k2y.MAX_COMPONENT,
            min_size=k2y.MIN_COMPONENT, no_size_check=False, bottom_rot='as-is',
            format='yy1', profile=None, rot_offset=None, assign_feeders=False,
            renumber=False, save_profile=False, raw_names=False)

    def open_pads(self, path=None):
        """A PADS Layout project: the .asc export, the placement report and
        the BOM are found under the directory, like the command line does."""
        path = path or filedialog.askdirectory(
            title='PADS project directory (with the .asc export in it)')
        if not path:
            return
        self.open_conversion(path, 'pads')

    def open_altium(self, path=None):
        """An Altium project: the .PcbDoc is read directly, a BOM beside it
        supplies values the board lacks."""
        path = path or filedialog.askdirectory(
            title='Altium project directory (with the .PcbDoc in it)')
        if not path:
            return
        self.open_conversion(path, 'altium')

    def open_altium_file(self):
        path = filedialog.askopenfilename(
            title='Altium board', filetypes=[('Altium board', '*.PcbDoc')])
        if path:
            self.open_conversion(path, 'altium')

    def open_conversion(self, path, source):
        """Read a PADS or Altium project through its converter and show it."""
        self.clear_log()
        log = []
        module = a2y if source == 'altium' else p2y
        try:
            conv = module.analyse(module.defaults(path), say=log.append)
        except SystemExit as exc:
            self.say(*log)
            messagebox.showerror(APP, str(exc))
            return
        except Exception:
            self.say(*log)
            messagebox.showerror(APP, traceback.format_exc())
            return
        if not conv.placed:
            self.say(*log)
            messagebox.showinfo(APP, '\n'.join(log[-6:]) if log else
                                'Nothing to place -- see the report.')
            return
        p2y.summarise(conv, say=log.append)

        result = k2y.Result()
        result.source = source
        result.board_file = getattr(conv, 'board', '') or ''
        result.project = conv.project
        result.outdir = conv.project
        result.prefix = conv.prefix
        result.width, result.height = conv.width, conv.height
        result.board_estimated = not conv.outline
        result.sides = conv.sides
        result.dropped = conv.dropped
        result.fiducials = conv.fiducials
        result.log = log
        result.profile = conv.profile
        result.profile.board = result.profile.board or conv.prefix
        result.profile.board_w, result.profile.board_h = conv.width, conv.height
        # The conversion itself loaded the profile beside the project, so
        # renames and merges were applied while the reels were named -- no
        # swapping afterwards, which is what used to lose them.
        result.profile_path = os.path.join(conv.project,
                                           '%s_yy1_profile.json' % conv.prefix)
        k2y.refresh_columns(result)

        self.result = result
        self._type_order = []
        self.say(*log)
        self.side.set('T' if result.sides['T'] else 'B')
        self.rot_var.set('%g' % result.profile.rot_offset)
        self.order_var.set(result.profile.order)
        self.station_var.set(str(result.profile.station_positions))
        self.load_job_tab()
        self.refresh()
        self.mark_dirty(False)
        self.status.set('%s (%s) — %.1f × %.1f mm, profile %s'
                        % (result.prefix, 'Altium' if source == 'altium' else 'PADS',
                           result.width, result.height,
                           os.path.basename(result.profile_path)))

    def open_csv(self, path=None):
        """Load a YY1 file on its own -- one off the SD card, say."""
        path = path or filedialog.askopenfilename(
            title='YY1 placement CSV', filetypes=[('CSV', '*.csv'), ('All', '*.*')])
        if not path:
            return
        self.clear_log()
        try:
            job, parts = yy1.read(path)
        except Exception as exc:
            messagebox.showerror(APP, str(exc))
            return

        result = k2y.Result()
        result.project = os.path.dirname(path)
        result.outdir = os.path.dirname(path)
        result.prefix = os.path.splitext(os.path.basename(path))[0]
        for suffix in ('_yy1_top', '_yy1_bottom', '_top', '_bottom'):
            if result.prefix.endswith(suffix):
                result.prefix = result.prefix[:-len(suffix)]
                break
        side = 'B' if 'bottom' in os.path.basename(path).lower() else 'T'
        early = yy1.Profile.load(os.path.join(
            result.project, '%s_yy1_profile.json' % result.prefix))
        for part in parts:
            part['base_rot'] = part['rot']
            part['raw_package'] = part['package']
            part['raw_value'] = part['comment']
            # A cross-footprint merge made since the file was written still
            # applies; one already in the file maps to itself.
            part['package'], part['comment'] = early.alias(part['package'],
                                                           part['comment'])
            # A file off the SD card carries no body size, so fall back to
            # what the package name implies.
            part['nozzle'] = yy1.nozzle_for_package(part['package'])
        result.sides[side] = parts
        result.profile_path = os.path.join(
            result.project, '%s_yy1_profile.json' % result.prefix)
        result.profile = yy1.Profile.load(result.profile_path)
        if not os.path.isfile(result.profile_path):
            self.say('note:   no %s beside this file -- nozzles, homes and reel '
                     'settings come from package guesses and the file\'s own '
                     'columns. Export from the project again to get a copy of '
                     'the profile written next to the CSV.'
                     % os.path.basename(result.profile_path))
        result.profile.job = job
        result.profile.board = result.prefix
        # The file's own columns are the truth here, so take them as the
        # profile's per-type settings rather than overwriting them.
        result.profile.absorb(parts)
        # A CSV carries no board size. The profile may; failing that, the
        # parts' extent plus a margin is drawn and labelled as an estimate.
        angle = result.profile.board_rotation
        if result.profile.board_w and result.profile.board_h:
            result.width, result.height = result.profile.board_w, result.profile.board_h
        else:
            tw = max(p['x'] for p in parts) + 3.0
            th = max(p['y'] for p in parts) + 3.0
            result.width, result.height = yy1.turn_size(tw, th, angle)
            result.board_estimated = True
        if angle:
            # The file was written for the board turned by `angle`; its
            # positions and rotations are taken back to the CAD frame, and
            # refresh turns them again -- the same numbers come out.
            for part in parts:
                part['base_x'], part['base_y'] = yy1.unturn_point(
                    part['x'], part['y'], result.width, result.height, angle)
                part['base_rot'] = yy1.norm_rot(part['base_rot'] - angle)
            job = result.profile.job
            if float(job.fid_x or 0) or float(job.fid_y or 0):
                pass                # the fiducial stays in the turned frame

        self.result = result
        self._type_order = []
        self.side.set(side)
        self.rot_var.set('%g' % result.profile.rot_offset)
        self.order_var.set(result.profile.order)
        self.station_var.set(str(result.profile.station_positions))
        self.load_job_tab()
        self.refresh()
        self.mark_dirty(False)
        self.say('read    %s' % path,
                 'parts   %d on the %s side, %d part types'
                 % (len(parts), 'top' if side == 'T' else 'bottom',
                    len(result.type_rows(side))),
                 'Feeder numbers, modes and heights from this file are now the '
                 'profile’s.')
        self.status.set('%s — %d parts from CSV' % (result.prefix, len(parts)))

    def reload(self):
        if not self.result:
            return
        source = getattr(self.result, 'source', '')
        if self.result.pcb:
            # the same board again, not whichever the folder offers first
            self.open_project(self.result.pcb)
        elif source == 'altium':
            self.open_conversion(getattr(self.result, 'board_file', '') or
                                 self.result.project, 'altium')
        elif source == 'pads' or (
                getattr(self.result, 'board_estimated', None) is not None and
                any(os.path.isfile(os.path.join(self.result.project, f))
                    for f in os.listdir(self.result.project)
                    if f.lower().endswith('.asc'))):
            self.open_pads(self.result.project)

    # -- tables
    def reel_state(self, key, first, pinned):
        """What a reel's row should say at a glance.

        Returns (tag, reason). 'ready' is green: a slot of its own, a nozzle,
        and -- once a plan exists for this side -- that nozzle loaded on a
        head or in the station. 'problem' is red: something the machine
        would trip over. 'unassigned' is amber: not decided yet. 'skipped'
        is grey.
        """
        profile = self.result.profile
        settings = profile.types.get(key, {})
        if first['skip']:
            return 'skipped', 'skipped this run'
        if len(pinned.get(first['feeder'], [])) > 1:
            return 'problem', 'feeder %d shared with another reel' % first['feeder']
        nozzle = profile.nozzle_for(key, first.get('nozzle', ''))
        if not nozzle:
            return 'problem', 'no nozzle'
        stock = self.available()
        if stock is not None and stock.get(nozzle, 0) < 1:
            return 'problem', 'you have no %s' % nozzle
        homes = profile.homes_for(self.side.get())
        if homes and nozzle not in homes:
            return 'problem', '%s is not loaded on a head or in the station' % nozzle
        if 'feeder' not in settings:
            return 'unassigned', 'still on the default feeder slot'
        if first['feeder'] > yy1.TAPE_LAST:
            return 'ready', 'flexible bank'
        return 'ready', ''

    def _pinned(self, groups):
        """slot -> reels pinned there. A skipped reel is not on the machine,
        so it holds no slot: a reel placed by hand that still carries an old
        feeder number must not turn the reel really in that slot red."""
        profile = self.result.profile
        pinned = {}
        for key, parts in groups.items():
            if 'feeder' in profile.types.get(key, {}) and not parts[0]['skip']:
                pinned.setdefault(parts[0]['feeder'], []).append(key)
        return pinned

    def reel_states(self):
        """{reel: (state, reason)} for the side on screen -- the colours
        of the Feeders table as data."""
        if not self.result:
            return {}
        k2y.refresh_columns(self.result)
        groups = self.result.type_rows(self.side.get())
        pinned = self._pinned(groups)
        return dict((key, self.reel_state(key, parts[0], pinned))
                    for key, parts in groups.items())

    def switch_side(self):
        self._type_order = []
        if self.result:
            self.load_job_tab()
        self.refresh()

    def refresh(self):
        if getattr(self, 'guide', None) is not None:
            try:
                self.guide.mark_stale()
            except tk.TclError:
                self.guide = None
        if self.result is not getattr(self, '_history_owner', None):
            self.reset_history()
        if not self.result:
            return
        side = self.side.get()
        profile = self.result.profile
        k2y.refresh_columns(self.result)

        groups = self.result.type_rows(side)
        # A feeder counts as assigned only once the profile pins it. Before
        # that every type sits on the default slot, which is a to-do list, not
        # 43 conflicts -- so only pinned duplicates are flagged as a clash.
        pinned = self._pinned(groups)

        # Rows keep their place across an edit. Re-sorting by feeder every time
        # would slide the row you just typed into out from under the cursor, so
        # order only changes when the board is loaded or feeders are renumbered.
        order = [k for k in self._type_order if k in groups]
        order += sorted(set(groups) - set(order),
                        key=lambda k: (groups[k][0]['feeder'], k))
        self._type_order = order

        chosen = [i for i in self.type_tree.selection() if i in groups]
        focused = self.type_tree.focus()
        self.type_tree.delete(*self.type_tree.get_children())
        self.type_rows.clear()
        states = {}
        for key in order:
            parts = groups[key]
            first = parts[0]
            refs = sorted(p['ref'] for p in parts)
            nozzles = sorted(set(p['nozzle'] for p in parts if p['nozzle']))
            settings = profile.types.get(key, {})
            values = {
                'feeder': first['feeder'], 'package': first['package'],
                'comment': first['comment'], 'qty': len(parts),
                'nozzle': profile.nozzle_for(key, '/'.join(nozzles)),
                'head': first['head'],
                'speed': first['speed'], 'pick_h': '%g' % first['pick_h'],
                'place_h': '%g' % first['place_h'],
                'mode': show_mode(first['mode']), 'skip': first['skip'],
                'rot_offset': '%g' % settings.get('rot_offset', 0.0),
                'refs': ', '.join(refs),
            }
            state, _ = self.reel_state(key, first, pinned)
            tags = [state]
            states[state] = states.get(state, 0) + 1
            # The type key is the row id, so selection and "next row" survive
            # the rebuild.
            item = self.type_tree.insert(
                '', 'end', iid=key,
                values=[values[f] for f in self.type_tree.fields], tags=tags)
            self.type_rows[item] = key
        if chosen:
            self.type_tree.selection_set(chosen)
        if focused in groups:
            self.type_tree.focus(focused)

        part_chosen = [i for i in self.part_tree.selection()]
        part_focus = self.part_tree.focus()
        self.part_tree.delete(*self.part_tree.get_children())
        self.part_rows.clear()
        # In file order, which is the order the machine places them.
        for seq, part in enumerate(k2y.ordered(self.result, side), start=1):
            key = yy1.type_key(part['package'], part['comment'])
            values = {
                'seq': seq,
                'ref': part['ref'], 'package': part['package'],
                'comment': part['comment'], 'x': '%.2f' % part['x'],
                'y': '%.2f' % part['y'], 'rot': '%.2f' % part['rot'],
                'feeder': part['feeder'],
                'nozzle': profile.nozzle_for(key, part['nozzle']),
                'head': part['head'], 'speed': part['speed'],
                'pick_h': '%g' % part['pick_h'],
                'place_h': '%g' % part['place_h'],
                'mode': show_mode(part['mode']), 'skip': part['skip'],
            }
            state, _ = self.reel_state(key, part, pinned)
            # A designator with its own settings shows blue unless something
            # is actually wrong with it.
            if state in ('ready', 'unassigned') and part['ref'] in profile.parts:
                state = 'override'
            tags = [state]
            item = self.part_tree.insert(
                '', 'end', iid=part['ref'],
                values=[values[f] for f in self.part_tree.fields], tags=tags)
            self.part_rows[item] = part['ref']
        alive = set(self.part_rows)
        keep = [i for i in part_chosen if i in alive]
        if keep:
            self.part_tree.selection_set(keep)
        if part_focus in alive:
            self.part_tree.focus(part_focus)

        clashes = [n for n, keys in pinned.items() if len(keys) > 1]
        missing = len([k for k in groups
                       if 'feeder' not in profile.types.get(k, {})])
        bits = ['%d part types, %d placements'
                % (len(groups), len(self.result.sides[side]))]
        if states.get('ready'):
            bits.append('%d reel%s ready' % (states['ready'],
                                             '' if states['ready'] == 1 else 's'))
        if states.get('problem'):
            bits.append('%d need attention (red)' % states['problem'])
        if missing:
            bits.append('%d still on the default feeder slot' % missing)
        if clashes:
            bits.append('feeder %s shared by two types'
                        % ', '.join(str(c) for c in sorted(clashes)))
        if len(groups) > yy1.TAPE_LAST:
            bits.append('more types than the %d tape slots' % yy1.TAPE_LAST)
        order = dict((v, l) for v, l, _ in ORDER_LABELS)[profile.order]
        bits.append(order.lower())
        self.status.set('  •  '.join(bits))
        self.refresh_nozzles()
        self.refresh_fiducials()
        self.draw_board()
        self.draw_bank()

    # -- edits
    def edit_type(self, item, field, text):
        key = self.type_rows.get(item)
        if not key:
            return False
        profile = self.result.profile
        parts = self.result.type_rows(self.side.get()).get(key, [])

        if field in ('package', 'comment'):
            return self._rename(key, parts, field, text)
        if field == 'nozzle':
            profile.set_type(key, 'nozzle', yy1.clean(text).upper())
            self.mark_dirty()
            self.refresh()
            return True
        try:
            value = parse_field(field, text.split()[0] if field == 'mode' else text)
        except (ValueError, IndexError) as exc:
            messagebox.showwarning(APP, '%s: %s' % (field, exc))
            return False
        profile.set_type(key, field, value)
        # A type-wide setting supersedes anything pinned on its designators.
        for part in parts:
            if part['ref'] in profile.parts:
                profile.parts[part['ref']].pop(field, None)
                if not profile.parts[part['ref']]:
                    del profile.parts[part['ref']]
        self.mark_dirty()
        self.refresh()
        return True

    def _rename(self, key, parts, field, text):
        """Change the Footprint or Value the machine will show for a type.

        The rename is stored against the raw KiCad name, so it survives a
        re-export -- this is where a part number that means nothing on the
        machine's screen gets turned into the package or value you loaded.
        """
        new = yy1.clean(text)
        if not new:
            messagebox.showwarning(APP, 'Footprint and Value cannot be blank -- '
                                        'the machine groups feeders by them.')
            return False
        table = self.result.profile.packages if field == 'package' \
            else self.result.profile.values
        source = 'raw_package' if field == 'package' else 'raw_value'
        for part in parts:
            raw = part.get(source) or part[field]
            if raw:
                table[raw] = new
            part[field] = new
        # Carry the type's settings over to the key it now has.
        types = self.result.profile.types
        if key in types:
            first = parts[0]
            types[yy1.type_key(first['package'], first['comment'])] = types.pop(key)
        self.mark_dirty()
        self.refresh()
        return True

    def edit_part(self, item, field, text):
        ref = self.part_rows.get(item)
        if not ref:
            return False
        try:
            value = parse_field(field, text.split()[0] if field == 'mode' else text)
        except (ValueError, IndexError) as exc:
            messagebox.showwarning(APP, '%s: %s' % (field, exc))
            return False
        self.result.profile.set_part(ref, field, value)
        self.mark_dirty()
        self.refresh()
        return True

    def clear_part_overrides(self):
        refs = [self.part_rows[i] for i in self.part_tree.selection()
                if i in self.part_rows]
        gone = [r for r in refs if self.result.profile.parts.pop(r, None)]
        if gone:
            self.say('cleared per-designator overrides on %s' % ', '.join(gone))
            self.mark_dirty()
            self.refresh()

    def auto_number(self, renumber=False):
        if not self.result:
            return
        groups = self.result.type_rows(self.side.get())
        if not groups:
            return
        # Placement order, so reels that get picked together sit together.
        keys = sorted(groups, key=lambda k: groups[k][0]['ref'])
        assigned = self.result.profile.assign_feeders(keys, overwrite=renumber)
        # Renumbering is exactly when re-sorting by slot is what you want.
        self._type_order = [k for k, _ in sorted(assigned, key=lambda a: a[1])]
        over = [k for k, n in assigned if n > yy1.TAPE_LAST]
        self.say('numbered %d part type(s) into feeder slots %d-%d'
                 % (len(assigned), min(n for _, n in assigned),
                    max(n for _, n in assigned)))
        if over:
            self.say('WARNING: %d type(s) landed past tape slot %d and need the '
                     'flexible (53-80) or bulk (81-99) bank: %s'
                     % (len(over), yy1.TAPE_LAST,
                        ', '.join(k.replace('|', ' ') for k in over)))
        self.mark_dirty()
        self.refresh()

    def set_board_rotation(self, angle):
        """Turn the whole board on the machine, CCW, to `angle`."""
        if not self.result:
            self.board_rot_var.set('0')
            return
        profile = self.result.profile
        old = profile.board_rotation
        angle = int(angle) % 360
        if angle == old:
            return
        bw, bh = getattr(self.result, 'base_size', (self.result.width, self.result.height))
        # the fiducial and the panel live in the turned frame: carry them over
        job = profile.job
        if float(job.fid_x or 0) or float(job.fid_y or 0):
            x, y = yy1.unturn_point(float(job.fid_x), float(job.fid_y), bw, bh, old)
            x, y = yy1.turn_point(x, y, bw, bh, angle)
            job.fid_x, job.fid_y = round(x, 3), round(y, 3)
        if (angle - old) % 180 == 90:
            job.unit_length, job.unit_width = job.unit_width, job.unit_length
            job.rows, job.columns = job.columns, job.rows
        profile.board_rotation = angle
        self.load_job_tab()
        self.say('board turned %d° counter-clockwise on the machine -- every '
                 'part, the fiducial and the panel follow; the board is now '
                 '%.1f x %.1f mm' % ((angle,) + yy1.turn_size(bw, bh, angle)))
        self.mark_dirty()
        self.refresh()
        self.draw_board()

    def apply_rotation(self):
        if not self.result:
            return
        try:
            offset = float(self.rot_var.get())
        except ValueError:
            messagebox.showwarning(APP, 'Rotation offset must be a number.')
            return
        self.result.profile.rot_offset = offset
        self.say('global rotation offset %+g° applied to every part' % offset)
        self.mark_dirty()
        self.refresh()

    # -- job header
    def omit_changed(self):
        """Skipped parts in the file or not. The nozzle change rows count
        the file's rows, so an existing plan is redone."""
        if not self.result:
            return
        profile = self.result.profile
        profile.omit_skipped = bool(self.omit_var.get())
        side = self.side.get()
        self.say('skipped parts are %s the CSV%s'
                 % ('left out of' if profile.omit_skipped else 'kept in',
                    ' (with Skip = 1)' if not profile.omit_skipped else
                    ' -- the operator sheet still lists them to place by hand'))
        if profile.changes_for(side):
            self.plan_nozzles(assign=False)
            changes, problems = getattr(self, '_plan', ([], []))
            profile.set_changes(side, changes)
            self.load_job_tab()
            self.say('nozzle change rows re-numbered for the file as it will be written')
        self.mark_dirty()
        self.refresh()

    def load_job_tab(self):
        job = self.result.profile.job
        if hasattr(self, 'board_rot_var'):
            self.board_rot_var.set(str(self.result.profile.board_rotation))
        if hasattr(self, 'omit_var'):
            self.omit_var.set(bool(self.result.profile.omit_skipped))
        for field in ('unit_length', 'unit_width', 'rows', 'columns',
                      'fid_x', 'fid_y', 'offset_x', 'offset_y'):
            self.job_vars[field].set('%g' % float(getattr(job, field)))
        rows = self.result.profile.changes_for(self.side.get()) or \
            job.nozzle_changes
        rows = list(rows) + [(False, 1, 1, 1, 1)] * (4 - len(rows))
        for row, (on, before, head, drop, pick) in zip(self.nozzle_vars, rows):
            row['on'].set(bool(on))
            row['before'].set(str(before))
            row['head'].set(str(head))
            row['drop'].set(str(drop))
            row['pick'].set(str(pick))

    def apply_job(self):
        if not self.result:
            return
        job = self.result.profile.job
        try:
            for field in ('unit_length', 'unit_width', 'fid_x', 'fid_y',
                          'offset_x', 'offset_y'):
                setattr(job, field, float(self.job_vars[field].get()))
            for field in ('rows', 'columns'):
                setattr(job, field, max(1, int(float(self.job_vars[field].get()))))
            changes = [
                (row['on'].get(), int(float(row['before'].get())),
                 int(float(row['head'].get())), int(float(row['drop'].get())),
                 int(float(row['pick'].get()))) for row in self.nozzle_vars]
            # Only this side's rows -- leaving job.nozzle_changes alone keeps
            # the machine defaults as the other side's starting point.
            self.result.profile.set_changes(self.side.get(), changes)
        except ValueError as exc:
            messagebox.showwarning(APP, 'Job header: %s' % exc)
            return
        self.say('job header updated: %d × %d panel, fiducial (%.2f, %.2f)'
                 % (job.rows, job.columns, job.fid_x, job.fid_y))
        self.mark_dirty()

    # -- profile and export
    def save_profile(self):
        if not self.result:
            return
        path = self.result.profile.save(self.result.profile_path)
        self.say('profile %s' % path)
        self.mark_dirty(False)

    def load_profile(self):
        if not self.result:
            return
        path = filedialog.askopenfilename(title='Profile JSON',
                                          filetypes=[('JSON', '*.json')])
        if not path:
            return
        self.result.profile = yy1.Profile.load(path)
        self.result.profile_path = path
        self.rot_var.set('%g' % self.result.profile.rot_offset)
        self.order_var.set(self.result.profile.order)
        self.station_var.set(str(self.result.profile.station_positions))
        self.load_job_tab()
        self.say('loaded profile %s' % path)
        self.refresh()

    def import_machine(self):
        """Take feeder numbers and modes back out of a file the machine wrote."""
        if not self.result:
            messagebox.showinfo(APP, 'Open a board first.')
            return
        path = filedialog.askopenfilename(
            title='YY1 CSV from the machine',
            filetypes=[('CSV', '*.csv'), ('All', '*.*')])
        if not path:
            return
        try:
            _, parts = yy1.read(path)
        except Exception as exc:
            messagebox.showerror(APP, str(exc))
            return
        changed = self.result.profile.absorb(parts)
        job, _ = yy1.read(path)
        profile = self.result.profile
        if job.fid_x or job.fid_y or job.offset_x or job.offset_y:
            profile.job.fid_x, profile.job.fid_y = job.fid_x, job.fid_y
            profile.job.offset_x, profile.job.offset_y = job.offset_x, job.offset_y
            self.say('fiducial from the machine: %.2f, %.2f  (offset %s, %s)'
                     % (job.fid_x, job.fid_y, job.offset_x, job.offset_y))
        rows = [c for c in job.nozzle_changes if c[0]]
        if rows and messagebox.askyesno(
                APP, 'The file carries %d nozzle change(s) as set on the machine:\n\n'
                     '%s\n\nTake them as this side\'s plan? (They only stay right '
                     'while the placement order does not change.)'
                     % (len(rows), '\n'.join('before #%d  head %d  drop %d  pick %d'
                                             % r[1:] for r in rows))):
            profile.set_changes(self.side.get(), rows)
            self.say('nozzle changes taken from the machine file')
        self.load_job_tab()
        self.say('imported %d setting(s) from %s' % (changed, os.path.basename(path)),
                 'Feeder numbers, modes and heights set on the machine are now '
                 'in the profile and will survive the next KiCad export.')
        self.mark_dirty()
        self.refresh()

    def export(self, outdir=None):
        if not self.result:
            return
        try:
            written = k2y.write_files(self.result, fmt=self.format_var.get(),
                                      outdir=outdir)
        except Exception:
            messagebox.showerror(APP, traceback.format_exc())
            return
        if not written:
            messagebox.showinfo(APP, 'Nothing to write -- no parts on either side.')
            return
        self.clear_log()
        self.say(*k2y.report(self.result, written))
        self.result.profile.save(self.result.profile_path)
        self.mark_dirty(False)
        sheets = [os.path.basename(p)[:-4] + '_setup.html' for p, _ in written]
        # The feeder list travels with the file: for every side written, the
        # loading sheet (what to load where) and the list as it will be once
        # loaded, in the same folder -- on the SD card when that is where the
        # export went.
        folder = os.path.dirname(written[0][0])
        lists = []
        side_now = self.side.get()
        try:
            for path, _ in written:
                side = 'B' if path.endswith('_bottom.csv') else 'T'
                self.side.set(side)
                made = self.write_loading_sheet(folder, quiet=True)
                if made:
                    lists.append(os.path.basename(made[1]))
                    if self.library:
                        lists.append('%s_yy1_feeders_%s.ods' % (
                            self.result.prefix, 'top' if side == 'T' else 'bottom'))
        finally:
            self.side.set(side_now)
        for name in lists:
            self.say('wrote   %s' % os.path.join(folder, name))
        if not self.library:
            self.say('note:   no feeder list loaded -- the loading sheet lists every '
                     'reel as one to load')
        sheets += [''] + ['Feeder list and loading sheet:'] + lists if lists else []
        messagebox.showinfo(APP, 'Written:\n\n' +
                            '\n'.join('%s  (%d parts)' % w for w in written) +
                            '\n\nOperator sheets beside each file:\n' +
                            '\n'.join(sheets) +
                            '\n(.html to print, .txt to read anywhere)')
        return written

    def export_as(self):
        if not self.result:
            return
        folder = filedialog.askdirectory(
            title='Where to write (the SD card, for instance)')
        if folder:
            self.export(folder)

    def show_help(self):
        messagebox.showinfo(
            'Feeder banks, heads and modes',
            'Feeder numbers (manual 3.4)\n'
            '    1-52   tape reel\n'
            '    53-80  flexible: vibration, IC tray, strip\n'
            '    81-99  bulk (loose parts, found by camera)\n\n'
            'Head\n'
            '    0 lets the machine choose, 1 and 2 pin it.\n\n'
            'Recognition mode (manual 3.2.1)\n'
            '    0 Null   1 Camera   2 Vacuum\n'
            '    3 Camera+Vacuum\n'
            '    4 Big IC Camera - wider view, but shield it from\n'
            '      overhead light.\n\n'
            'Mount speed is a percentage of the machine’s global speed,\n'
            'which defaults to 70%. Pick and place heights of 0 use the\n'
            'machine’s nozzle height datum.\n\n'
            'Two part types on one feeder number is an error -- those\n'
            'rows are shown in red.\n\n'
            'Nozzle changes (manual 4.5.2)\n'
            '    The head carries two nozzles and the station holds\n'
            '    more, so four types run without stopping. The file\n'
            '    holds four changes, and the position you drop the\n'
            '    outgoing nozzle into must be empty.\n\n'
            'Placement order\n'
            '    The machine works straight down the file. A nozzle\n'
            '    change happens before one component number, so each\n'
            '    nozzle needs one unbroken run for a change to cover\n'
            '    the whole group.')

    # -- the nozzle drawer: which nozzles this machine actually has
    SETTINGS = os.path.join(os.path.expanduser('~'), '.yy1_converter.json')

    def available(self):
        """{nozzle: count} when planning is limited to what you own, else None."""
        if not getattr(self, 'limit_var', None) or not self.limit_var.get():
            return None
        stock = {}
        for nozzle, var in self.stock_vars.items():
            try:
                count = int(var.get())
            except ValueError:
                count = 0
            if count > 0:
                stock[nozzle] = count
        return stock

    # -- the feeder list: reels that stay on the machine
    def load_library(self, path=None):
        path = path or filedialog.askopenfilename(
            title='Feeder list (slot, tape width, value)',
            filetypes=[('Spreadsheet or CSV', '*.ods *.xlsx *.xls *.csv *.txt'),
                       ('All', '*.*')])
        if not path:
            return
        try:
            library = yy1.FeederLibrary.read(path)
        except Exception as exc:
            messagebox.showerror(APP, 'Could not read %s:\n%s' % (path, exc))
            return
        if not library.rows:
            messagebox.showwarning(APP, 'No rows with a numeric slot in the first '
                                        'column were found in %s.' % os.path.basename(path))
            return
        self.library = library
        self.save_inventory()
        self._show_library()
        self.say('feeder list %s: %d reel(s) loaded, %d free slot(s) (%s)'
                 % (os.path.basename(path), len(library.loaded()), len(library.free()),
                    ', '.join(str(r[0]) for r in library.free())))
        if self.result:
            self.refresh()

    def _show_library(self):
        if getattr(self, 'library_label', None) is None:
            return
        if self.library:
            self.library_label.configure(
                text='%s: %d reels, %d free' % (os.path.basename(self.library.path),
                                                 len(self.library.loaded()),
                                                 len(self.library.free())))
        else:
            self.library_label.configure(text='no feeder list')

    def load_inventory(self):
        try:
            import json
            with open(self.SETTINGS, encoding='utf-8') as handle:
                data = json.load(handle)
            self.limit_var.set(bool(data.get('limit', False)))
            path = data.get('library_path')
            if path and os.path.isfile(path):
                try:
                    self.library = yy1.FeederLibrary.read(path)
                    self._show_library()
                except Exception:
                    self.library = None
            for nozzle, count in (data.get('nozzles') or {}).items():
                if nozzle in self.stock_vars:
                    self.stock_vars[nozzle].set(str(int(count)))
        except (IOError, OSError, ValueError):
            pass

    def save_inventory(self):
        import json
        stock = {}
        for nozzle, var in self.stock_vars.items():
            try:
                stock[nozzle] = int(var.get())
            except ValueError:
                stock[nozzle] = 0
        try:
            with open(self.SETTINGS, encoding='utf-8') as handle:
                data = json.load(handle)
        except (IOError, OSError, ValueError):
            data = {}
        data.update({'limit': self.limit_var.get(), 'nozzles': stock,
                     'library_path': self.library.path if self.library else ''})
        try:
            with open(self.SETTINGS, 'w', encoding='utf-8') as handle:
                json.dump(data, handle, indent=2)
        except (IOError, OSError):
            pass

    def inventory_changed(self):
        """The drawer is a machine fact, not a board one: it is remembered
        across boards, and the current plan is redone against it."""
        self.save_inventory()
        if self.result:
            self.plan_nozzles(assign=False)
            self.refresh()

    def quit_app(self):
        if self._dirty and self.result:
            answer = messagebox.askyesnocancel(
                APP, 'Save changes to %s?' % os.path.basename(
                    self.result.profile_path))
            if answer is None:
                return
            if answer:
                self.save_profile()
        self.destroy()


def main():
    claim_dpi_awareness()
    project = sys.argv[1] if len(sys.argv) > 1 else None
    App(project).mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
