from src.ui.pages import feedback
from src.ui.ui import with_chrome

with_chrome(feedback.render, "feedback")()
