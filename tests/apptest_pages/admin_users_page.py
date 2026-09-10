from src.ui.pages import admin_users
from src.ui.ui import with_chrome

with_chrome(admin_users.render, "admin_users")()
