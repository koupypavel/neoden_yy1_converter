# Security

This is a desktop tool that reads CAD exports and writes CSV files. It makes
no network connections and runs no code from the files it reads. The Altium
reader parses a binary container; the PADS and KiCad readers parse text.

**Supported version:** the latest release on the
[releases page](https://github.com/koupypavel/neoden_yy1_converter/releases).

**Reporting a problem:** if you find a way for a crafted board file to make
the tool do something other than read it -- write outside the folder it was
pointed at, run something, hang on input -- please open an issue with the
words "security" in the title and a description of the file shape; do not
attach the file publicly. For something you would rather not describe in
public, open an issue saying only "security: please contact me" and the
maintainer will get in touch.

Expect an answer within two weeks. Fixes ship as a new release.
