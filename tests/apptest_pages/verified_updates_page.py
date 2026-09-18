from src.ui.pages import verified_updates
from src.ui.ui import with_chrome

with_chrome(verified_updates.render, "verified_updates")()
