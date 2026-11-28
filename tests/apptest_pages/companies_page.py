from src.ui.pages import companies
from src.ui.ui import with_chrome

with_chrome(companies.render, "companies")()
