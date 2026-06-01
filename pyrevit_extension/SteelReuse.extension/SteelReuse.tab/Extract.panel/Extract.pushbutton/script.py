# -*- coding: utf-8 -*-
"""Extract structural steel members from the active model to a JSON file.

Runs on the default IronPython 3 engine (no ``#! python3``): pyRevit 6.x's CPython 3.12 engine has a
version-parsing bug under Revit 2026. Both this button and the extractor are IronPython-safe.

Thin pyRevit pushbutton: it locates the project's `extractor/pyrevit_extract.py` (five folders up
from this button, then into `extractor/`) and runs its `main()`. Keeping the logic in one place means
the button and any other runner share the exact same extraction code.
"""

import os
import sys

# .../Extract.pushbutton -> Extract.panel -> SteelReuse.tab -> SteelReuse.extension
# -> pyrevit_extension -> <repo root>
_here = os.path.dirname(__file__)
_repo = os.path.abspath(os.path.join(_here, "..", "..", "..", "..", ".."))
_extractor_dir = os.path.join(_repo, "extractor")

if _extractor_dir not in sys.path:
    sys.path.insert(0, _extractor_dir)

import pyrevit_extract  # noqa: E402  (path set up above)

pyrevit_extract.main()
