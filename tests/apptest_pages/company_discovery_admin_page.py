from src.ui.pages import company_discovery_admin
from src.ui.ui import with_chrome

with_chrome(company_discovery_admin.render, "company_discovery_admin")()
