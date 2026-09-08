from src.ui.pages import radar_inbox
from src.ui.ui import with_chrome

with_chrome(radar_inbox.render, "radar_inbox")()
