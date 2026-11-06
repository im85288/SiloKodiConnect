"""A release's notes: README.md's "### vX.Y.Z" section, then how to install.

    python3 .github/release_notes.py 0.12.0
"""
import re
import sys

version = sys.argv[1]
readme = open('README.md', encoding='utf-8').read()
found = re.search(r'^### v%s\b.*?\n(.*?)(?=^### |^## |\Z)' % re.escape(version), readme, re.S | re.M)
if not found:
    sys.exit('README.md has no "### v%s" section' % version)
print(found.group(1).strip())
print('\n**Install:** download `plugin.video.kodiconnectforsilo-%s.zip` below and use Kodi\'s *Add-ons → Install from '
      'zip file* (installs over earlier versions). Requires Kodi 20 or newer.' % version)
