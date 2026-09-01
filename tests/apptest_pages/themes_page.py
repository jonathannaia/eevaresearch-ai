from src.ui.pages import themes
from src.ui.ui import with_chrome

with_chrome(themes.render, "theme_browser")()
