from src.ui.pages import agent_review
from src.ui.ui import with_chrome

with_chrome(agent_review.render, "agent_review")()
