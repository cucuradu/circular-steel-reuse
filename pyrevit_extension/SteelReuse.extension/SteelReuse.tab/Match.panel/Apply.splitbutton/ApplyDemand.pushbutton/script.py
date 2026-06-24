# -*- coding: utf-8 -*-
"""Apply a SteelReuse run to THIS model as the DEMAND (new design) side.

Default IronPython 3 engine. Thin wrapper over steelreuse_apply_button.run -- the side is fixed by
which split-button entry you click, so there is no "which side?" popup.
"""

import steelreuse_apply_button as applybtn  # noqa: E402 -- extension lib/ is on the path

applybtn.run("demand")
