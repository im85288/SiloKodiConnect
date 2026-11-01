# -*- coding: utf-8 -*-
"""The context menu's "Remove from Continue Watching" on a Continue Watching
or Next Up card: Silo hides it (an episode's whole show) until it's played
again, and Home's rows refresh."""
import sys

from resources.lib.silokodi import context

if __name__ == '__main__':
    context.dismiss(sys.listitem)
