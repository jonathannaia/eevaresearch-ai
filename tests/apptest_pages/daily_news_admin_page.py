from src.ui.pages import daily_news_admin
from src.ui.ui import with_chrome

with_chrome(daily_news_admin.render, "daily_news_admin")()
