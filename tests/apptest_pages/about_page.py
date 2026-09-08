from src.ui.pages import about
from src.ui.ui import with_chrome

with_chrome(about.render, "about")()
