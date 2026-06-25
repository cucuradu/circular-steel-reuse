# -*- coding: utf-8 -*-
"""Open the SteelReuse Scenario Sweep planner: vary a few dials, run every combination, rank them.

Runs on the default IronPython 3 engine (no ``#! python3`` -- see Extract.pushbutton for why). The
planner (extension ``lib/steelreuse_sweep_planner.py``) shells the engine out per grid point, exactly
as Run Match does; the matching engine never runs inside Revit.
"""

import os

import steelreuse_sweep_planner as planner  # noqa: E402 -- pyRevit puts the extension lib/ on the path

# .../Sweep.pushbutton -> Match.panel -> SteelReuse.tab -> SteelReuse.extension
_EXT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def main():
    planner.SweepPlanner(_EXT_ROOT).show()  # modeless


if __name__ == "__main__":
    main()
