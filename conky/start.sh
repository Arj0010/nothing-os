#!/bin/bash
# Launch the Nothing OS desktop:
#   - interactive GTK widgets (Python)
#   - Conky breathing background (always on; throttled to ~5fps to stay light)
WDIR="$HOME/.config/nothing-widgets"
CDIR="$HOME/.config/conky/nothing"

# stop anything from a previous run
pkill -x conky >/dev/null 2>&1
pkill -f nothing-widgets.py >/dev/null 2>&1
pkill -f power-watch.sh >/dev/null 2>&1
sleep 0.25

# interactive widgets
setsid python3 "$WDIR/nothing-widgets.py" >/dev/null 2>&1 < /dev/null &

# breathing background — always running
setsid conky -c "$CDIR/bg.conf" >/dev/null 2>&1 < /dev/null &

# NOTE: the repo sync panel used to be a conky instance (repos.conf). It is now
# a GTK card in nothing-widgets.py, because conky windows cannot receive clicks
# and the panel needs a ⚙ settings button. repos.conf is kept as a fallback but
# is no longer launched — running both would show the panel twice.

echo "Nothing OS desktop started (interactive widgets + breathing bg)."
