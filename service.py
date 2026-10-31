# -*- coding: utf-8 -*-
# Home's saved rows first, using only Kodi's modules: loading the rest of the
# add-on takes seconds on a small device (see quickstart.py).
from resources.lib.silokodi import quickstart

quickstart.apply()

from resources.lib.silokodi import service  # noqa: E402

if __name__ == '__main__':
    service.run()
