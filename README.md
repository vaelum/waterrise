# waterrise

Typed plans, decisions, reviews and notes in a project's `planning/` folder,
a check for them, and one local service per user whose pages are where the
decisions get answered and the gated steps approved.

waterrise was the `planning` command of the butler harness, and it still is
one: in a project on the harness, `python butler.py planning …` runs the
installed `waterrise` with the same arguments.

## Install

```
git clone https://github.com/vaelum/waterrise.git
cd waterrise
python -m waterrise install          # a venv of its own, `waterrise` on PATH
python -m waterrise install --boot   # and the service starting at login
```

Python 3.11 or newer and nothing else.

## Use

```
waterrise guide        # the rules and the format, for agents and people
waterrise init         # make planning/ a typed folder
waterrise new plan x   # an item with a fresh id
waterrise check        # validate the folder
waterrise status       # what waits on whom
waterrise serve        # the pages
```

`waterrise --help` lists the rest.
