from src.ui.pages import themes_research
from src.ui.ui import with_chrome

with_chrome(themes_research.render, "themes")()
