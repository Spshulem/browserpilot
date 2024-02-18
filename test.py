from browserpilot.agents.gpt_selenium_agent import GPTSeleniumAgent
from selenium.webdriver.chrome.options import Options
import os

# Initialize Chrome options
chrome_options = Options()
chrome_options.add_argument("--headless")

# Specify the path to the ChromeDriver
# Adjust this path if your ChromeDriver is located elsewhere
chrome_driver_path = os.path.join(os.getcwd(), 'chromedriver')

instructions = """Go to Google.com
Find all textareas.
Find the first visible textarea.
Click on the first visible textarea.
Type in "buffalo buffalo buffalo buffalo buffalo" and press enter.
Wait 2 seconds.
Find all anchor elements that link to Wikipedia.
Click on the first one.
Wait for 10 seconds."""

# Initialize the GPTSeleniumAgent with instructions and the ChromeDriver path
agent = GPTSeleniumAgent(instructions, chrome_driver_path)
agent.run()