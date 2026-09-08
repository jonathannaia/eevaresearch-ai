from src.ui.pages import coverage
from src.ui.ui import with_chrome

with_chrome(coverage.render, "coverage")()
