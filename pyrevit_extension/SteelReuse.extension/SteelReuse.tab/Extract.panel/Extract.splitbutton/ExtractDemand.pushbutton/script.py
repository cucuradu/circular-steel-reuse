# -*- coding: utf-8 -*-
"""Extract the active model as a DEMAND (new design) JSON.

Runs on the default IronPython 3 engine. Locates the project's extractor and runs its main("demand"),
so the donor/demand choice is made on the ribbon, not in a popup.
"""

import os
import sys

# .../ExtractDemand.pushbutton -> Extract.splitbutton -> Extract.panel -> SteelReuse.tab
# -> SteelReuse.extension -> pyrevit_extension -> <repo root>
_repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", ".."))
_extractor_dir = os.path.join(_repo, "extractor")
if _extractor_dir not in sys.path:
    sys.path.insert(0, _extractor_dir)

import pyrevit_extract  # noqa: E402  (path set up above)

pyrevit_extract.main("demand")
