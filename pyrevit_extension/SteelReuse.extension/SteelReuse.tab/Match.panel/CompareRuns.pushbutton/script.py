# -*- coding: utf-8 -*-
"""Open the SteelReuse Compare Runs window: pick saved runs, compare KPIs + per-slot changes.

Runs on the default IronPython 3 engine (no ``#! python3`` -- see Extract.pushbutton for why). The
window (extension ``lib/steelreuse_compare.py``) reads the auto-saved run history; no engine runs here.
"""

import os

import steelreuse_compare as compare  # noqa: E402 -- pyRevit puts the extension lib/ on the path

# .../CompareRuns.pushbutton -> Match.panel -> SteelReuse.tab -> SteelReuse.extension
_EXT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def main():
    compare.CompareWindow(_EXT_ROOT).show()  # modeless


if __name__ == "__main__":
    main()
