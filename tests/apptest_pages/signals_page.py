from src.ui.pages import signals
from src.ui.ui import with_chrome

with_chrome(signals.render, "signals")()
