# Contributing

Thanks for looking at this. The tool exists because one machine had to be
fed from three CAD systems; if it helps with your machine or your CAD tool,
improvements are welcome.

## What helps most

- **A board that does not convert right.** Open an issue with the *Board
  import problem* template. Please do not attach a real board: use
  `examples/make_demo.py` as a starting point and reduce your case to a few
  parts, or describe the footprint names and the rows that came out wrong.
- **A file that ran on the machine.** The format is verified against one
  such file. If you have run a converted file on a YY1 and it placed
  correctly (or not), the setup sheet, the CSV and what happened are the
  most valuable report there is.
- **Footprint names** the normaliser gets wrong (`yy1lib.normalise_package`)
  -- the name, and what reel it should be.
- **Documentation**: anything in `docs/USER-GUIDE.md` that did not match what
  you saw.

## Sending a change

1. Fork, branch, change. Keep the tool standard-library only (tkinter
   included); optional readers (`openpyxl`, `xlrd`) may be imported lazily.
2. `python verify_yy1.py` must still pass. It needs the machine-run reference
   file, which is not in the repository; if you do not have one, say so in
   the pull request and the maintainer will run it.
3. Try the window on the demo board: `python examples/make_demo.py`, open
   `examples/demo_yy1_top.csv`, press *Set up board*, *Match board to list*
   and *Export*.
4. Update `README.md` (the format and the rules) or `docs/USER-GUIDE.md`
   (how to use it) when behaviour changes, and the built-in manual in
   `yy1help.py` when a control changes.
5. Open the pull request with the template filled in.

## Style

Plain Python 3.8+, no type checkers or linters required. Comments say why,
not what. Every rule about the machine cites the YY1 user manual by section.

## Never in the repository

Real boards, exports of real boards, feeder lists of a real workshop,
customer names, machine-run files of real products. The `.gitignore` covers
the usual folders; look before you `git add`.
