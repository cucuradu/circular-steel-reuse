# -*- coding: utf-8 -*-
"""Clear everything SteelReuse drew on the model (match colours + data and problem highlights).

Default IronPython 3 engine. Thin wrapper over steelreuse_clear.run.
"""

import steelreuse_clear as clear  # noqa: E402 -- extension lib/ is on the path

clear.run()
