from src.ui.pages import theme_workspace
from src.ui.ui import with_chrome

with_chrome(theme_workspace.render, "theme_workspace")()
