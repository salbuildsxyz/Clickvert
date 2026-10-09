"""Lets you run Clickvert with ``python -m clickvert`` or ``python path\\to\\src\\clickvert``.

The second form is what the right-click menu uses: it works without
installing Clickvert or setting PYTHONPATH.
"""

import sys
from pathlib import Path

if not __package__:
    # Started as a folder. Python put src\clickvert itself on the import path;
    # swap it for src\ so "import clickvert" works (and so our module names
    # like "paths" or "errors" can't shadow anything else).
    sys.path[0] = str(Path(__file__).resolve().parent.parent)
    from clickvert.cli import main
else:
    from .cli import main

sys.exit(main())
