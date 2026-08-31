from src.ui.pages import research_cases
from src.ui.ui import with_chrome

with_chrome(research_cases.render, "research_cases")()
