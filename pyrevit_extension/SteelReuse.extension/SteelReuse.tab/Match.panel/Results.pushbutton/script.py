# -*- coding: utf-8 -*-
"""Open the SteelReuse Results window: review any saved match run and trace its members in Revit.

Runs on the default IronPython 3 engine (no ``#! python3`` -- see Extract.pushbutton for why). The
window (extension ``lib/steelreuse_results_window.py``) lists the saved runs from the history holder,
binds the picked run's assignments to an interactive grid, and lets each row be selected/zoomed in the
active model or traced to its partner in the other extracted model. The printable HTML report is still
one click away (Open report) but tracing back into Revit is the point -- a browser table cannot do it.
"""

import os

import steelreuse_results_window as results_window  # noqa: E402 -- extension lib/ is on the path

# .../Results.pushbutton -> Match.panel -> SteelReuse.tab -> SteelReuse.extension
_EXT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def main():
    results_window.ResultsWindow(_EXT_ROOT).show()  # modeless: stays open beside Revit


if __name__ == "__main__":
    main()
