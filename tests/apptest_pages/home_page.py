from src.ui.pages import home
from src.ui.ui import with_chrome

with_chrome(home.render, "home", show_sidebar=False)()
