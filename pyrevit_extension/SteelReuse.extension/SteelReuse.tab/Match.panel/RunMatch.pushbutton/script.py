# -*- coding: utf-8 -*-
"""Open the SteelReuse match window -- run the whole pipeline and review results, no command line.

Runs on the default IronPython 3 engine (no ``#! python3`` -- see Extract.pushbutton for why). The
window itself (extension ``lib/steelreuse_panel.py``) collects the run options, shells the heavy
engine out to the signed venv on a background thread (CLAUDE.md hard rule 2), and renders the
results.json it writes. This button is just the launcher.
"""

import os

import steelreuse_panel as panel  # noqa: E402 -- pyRevit puts the extension lib/ on the path

# .../RunMatch.pushbutton -> Match.panel -> SteelReuse.tab -> SteelReuse.extension
_EXT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def main():
    panel.SteelReusePanel(_EXT_ROOT).show()  # modeless: the window stays open beside Revit


if __name__ == "__main__":
    main()
