from src.ui.pages import daily_news
from src.ui.ui import with_chrome

with_chrome(daily_news.render, "daily_news")()
