# -*- coding: utf-8 -*-
"""Create or open the SteelReuse Passport schedule (reuse status / partner / CO2e).

Default IronPython 3 engine. Thin wrapper over steelreuse_schedule.run.
"""

import steelreuse_schedule as schedule  # noqa: E402 -- extension lib/ is on the path

schedule.run(schedule.PASSPORT)
