from src.ui.pages import dashboard
from src.ui.ui import with_chrome

with_chrome(dashboard.render, "dashboard")()
