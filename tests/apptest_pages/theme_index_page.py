from src.ui.pages import theme_index
from src.ui.ui import with_chrome

with_chrome(theme_index.render, "theme_index")()
