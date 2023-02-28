"""GPT Selenium Agent abstraction."""
import pdb
import os
import re
import sys
import time
import openai
import traceback
import html2text
import nltk
from nltk.tokenize import sent_tokenize
from bs4 import BeautifulSoup
from bs4.element import NavigableString
from bs4.element import Tag
from llama_index import Document, GPTSimpleVectorIndex
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.relative_locator import locate_with
from .compilers.instruction_compiler import InstructionCompiler

nltk.download("punkt")

import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

NO_RESPONSE_TOKEN = "<NONE>"  # To denote that empty response from model.


class GPTWebElement(webdriver.remote.webelement.WebElement):
    """Wrapper over Selenium's WebElement with an additional iframe ivar for
    recordkeeping."""
    def __init__(self, web_ele, iframe=None):
        # Initialize this object using web_ele.
        super().__init__(web_ele._parent, web_ele._id)
        self.__dict__.update(web_ele.__dict__)
        self.iframe = iframe


class GPTSeleniumAgent:
    def __init__(
        self,
        instructions,
        chromedriver_path,
        user_data_dir="user_data",
        headless=False,
        debug=False,
        debug_html_folder="",
        instruction_output_file=None,
    ):
        """Initialize the agent."""
        # Helpful instance variables.
        assert instruction_output_file is None or instruction_output_file.endswith(
            ".yaml"
        ), "Instruction output file must be a YAML file or None."
        self.instruction_output_file = instruction_output_file
        self.debug = debug
        self.debug_html_folder = debug_html_folder

        # Set up the driver.
        chrome_options = webdriver.ChromeOptions()
        chrome_options.add_argument(f"user-data-dir={user_data_dir}")
        self.headless = headless
        if headless:
            chrome_options.add_argument("--headless")

        # Instantiate Service with the path to the chromedriver and the options.
        service = Service(chromedriver_path)
        self.driver = webdriver.Chrome(service=service, options=chrome_options)

        # Fire up the compiler.
        self.instruction_compiler = InstructionCompiler(instructions=instructions)

    """Helper functions"""

    def _check_danger(self, action_str):
        """Check that the action is not dangerous. If so, just quit."""
        if self._is_potentially_dangerous(action_str):
            logger.warning("Action is potentially dangerous. Exiting.")
            logger.warning("Action: {action}".format(action=action_str))
            sys.exit(1)

    def _is_potentially_dangerous(self, code_str):
        """Isaac Asimov is rolling over in his grave."""
        # Check that the code doesn't try any funny business with the importing.
        if "import " in code_str:
            return True

        # Check that the code doesn't use any of the following libraries.
        blacklisted_libraries = ["shutil", "requests", "urllib"]  # "os", "sys".
        for library in blacklisted_libraries:
            if library in code_str:
                return True

        # # Check that the code doesn't use any of the following functions.
        # blacklisted_functions = ["open", "exec", "eval", "input", "print", "write"]
        # for function in blacklisted_functions:
        #     if function in code_str:
        #         return True

        return False

    def _clean_html(self):
        """Clean HTML to remove blacklisted elements and attributes."""
        blacklisted_elements = set(
            [
                "head",
                "title",
                "meta",
                "script",
                "style",
                "path",
                "svg",
                "br",
                "::marker",
            ]
        )
        blacklisted_attributes = set(
            ["style", "ping", "src", "item*", "aria*", "js*", "data-*"]
        )

        # Get the HTML tag for the entire page, convert into BeautifulSoup.
        html = self.driver.find_element(By.TAG_NAME, "html")
        html_string = html.get_attribute("outerHTML")
        soup = BeautifulSoup(html_string, "lxml")

        # Remove blacklisted items and attributes in it.
        for blacklisted in blacklisted_elements:
            for tag in soup.find_all(blacklisted):
                tag.decompose()

        # Set up a helper function to delete the blacklisted attributes from
        # a tag, as long as the attribute name matches the regex.
        def remove_blacklisted_attributes(tag, blacklisted_attributes):
            for attr in tag.attrs.copy():
                for pattern in blacklisted_attributes:
                    if re.match(pattern, attr):
                        del tag[attr]

        for tag in soup.find_all(True):
            remove_blacklisted_attributes(tag, blacklisted_attributes)

        return soup

    def __run_compiled_instructions(self, instructions):
        """Runs Python code previously compiled by InstructionCompiler."""
        ldict = {"env": self}
        self._check_danger(instructions)
        exec(instructions, globals(), ldict)
        exec("env.driver.quit()", globals(), ldict)

    def __print_instruction_and_action(self, instruction, action):
        """Logging the instruction and action."""
        info_str = "\nInstruction: {instruction}\n".format(instruction=instruction)
        info_str = info_str + "\nAction: {action}\n".format(action=action)
        logger.info(info_str)

    def __get_relevant_part_of_stack_trace(self):
        """Get the relevant part of the stack trace."""
        stack_trace = traceback.format_exc()
        stack_trace = stack_trace.split("\n")[3:5]
        stack_trace = "\n".join(stack_trace)
        # Get the name of this class (GPTSeleniumAgent) and
        # replace it with "env".
        class_name = self.__class__.__name__
        stack_trace = stack_trace.replace(class_name, "env")
        # Get the number after the word "line " in the stack trace.
        line_num = int(stack_trace.split("line ")[1].split(",")[0])
        return {"stack_trace": stack_trace, "line_num": line_num}

    def __save_html_snapshot(self):
        """Helpful for debugging."""
        # Check if the folder exists, and if not, create it.
        if not os.path.exists(self.debug_html_folder):
            os.makedirs(self.debug_html_folder)

        # Save an HTML of the entire page.
        debug_name = "debug.html"
        debug_name = os.path.join(self.debug_html_folder, debug_name)

        html = self.driver.page_source
        with open(debug_name, "w+") as f:
            f.write(html)
        
        # Save a screenshot of the entire page.
        screenshot_name = "debug.png"
        screenshot_name = os.path.join(self.debug_html_folder, screenshot_name)
        self.driver.save_screenshot(screenshot_name)

        # Save screenshots and HTML from each iframe.
        iframes = self.driver.find_elements(by=By.TAG_NAME, value="iframe")
        for i, iframe in enumerate(iframes):
            screenshot_name = "debug_{iframe_num}.png".format(iframe_num=i)
            screenshot_name = os.path.join(self.debug_html_folder, screenshot_name)
            # iframe.screenshot(screenshot_name)
            iframe_debug_name = "debug_{iframe_num}.html".format(iframe_num=i)
            iframe_debug_name = os.path.join(self.debug_html_folder, iframe_debug_name)
            with open(iframe_debug_name, "w+") as f:
                self.driver.switch_to.frame(iframe)
                f.write(self.driver.page_source)
            self.driver.switch_to.default_content()
        self.driver.switch_to.default_content()

    def __step_through_instructions(self):
        """In contrast to `__run_compiled_instructions`, this function will
        step through the instructions queue one at a time, calling the LLM for
        each instruction."""
        ldict = {"env": self}
        while self.instruction_compiler.instructions_queue:
            # `step` will try the instruction for the first time.
            step = self.instruction_compiler.step()

            instruction = step["instruction"]
            action = step["action_output"]
            self.__print_instruction_and_action(instruction, action)

            action = action.replace("```", "")
            self._check_danger(action)

            # Attempt evals.
            attempts = 0
            while attempts < 3:
                attempts = attempts + 1
                try:
                    exec(action, globals(), ldict)
                    break
                except:
                    stack_trace_result = self.__get_relevant_part_of_stack_trace()
                    stack_trace = stack_trace_result["stack_trace"]
                    line_num = stack_trace_result["line_num"]
                    problem_instruction = "\nFailed on line: {line}\n".format(
                        line=action.split("\n")[line_num - 1]
                    )
                    logger.info("\n\n" + stack_trace)
                    logger.info(problem_instruction)

                    if self.debug:
                        if self.debug_html_folder:
                            self.__save_html_snapshot()

                        logger.info(traceback.print_exc())
                        env = self  # For the interactive debugger.
                        pdb.set_trace()

                    step = self.instruction_compiler.retry(problem_instruction + stack_trace)
                    instruction = step["instruction"]
                    action = step["action_output"].replace("```", "")
                    logger.info("RETRYING...")
                    self.__print_instruction_and_action(instruction, action)

        if self.instruction_output_file:
            self.instruction_compiler.save_compiled_instructions(
                self.instruction_output_file
            )
        
        exec("env.driver.quit()", globals(), ldict)

    def __switch_to_element_iframe(func):
        """Decorator function to switch to the iframe of the element."""
        def wrapper(*args):
            self = args[0]
            element = args[1]
            if element is not None:
                iframe = element.iframe
                if iframe is not None:
                    self.driver.switch_to.frame(iframe)
                func(*args)
                self.driver.switch_to.default_content()
        
        return wrapper

    """Functions meant for the client to call."""

    def run(self):
        """Run the agent."""
        should_use_compiled = self.instruction_compiler.use_compiled
        compiled = self.instruction_compiler.compiled_instructions
        if should_use_compiled and compiled:
            logger.info("Found cached instructions. Running...")
            instructions = self.instruction_compiler.compiled_instructions
            instructions = "\n".join(instructions).replace("```", "")
            self.__run_compiled_instructions(instructions)
        else:
            logger.info("No cached instructions found. Running...")
            self.__step_through_instructions()

    """Functions exposed to the agent via the text prompt."""

    def wait(self, seconds):
        time.sleep(seconds)

    def get(self, url):
        if not url.startswith("http"):
            url = "http://" + url
        self.driver.get(url)
        time.sleep(3)

    def scroll(self, direction):
        if direction == "up":
            # Do the python equivalent of the following JavaScript:
            # "(document.scrollingElement || document.body).scrollTop = (document.scrollingElement || document.body).scrollTop - window.innerHeight;"
            self.driver.execute_script("window.scrollBy(0, -window.innerHeight);")
        elif direction == "down":
            # Do the python equivalent of the following JavaScript:
            # "(document.scrollingElement || document.body).scrollTop = (document.scrollingElement || document.body).scrollTop + window.innerHeight;"
            self.driver.execute_script("window.scrollBy(0, window.innerHeight);")

    def find_element(self, by="id", value=None):
        return self.find_elements(by, value)[0]

    def find_elements(self, by="id", value=None):
        """Wrapper over `driver.find_elements` which also scans iframes.

        First, it finds all elements on the page that match the given
        `by` and `value`. Then, it finds all iframes on the page and
        switches to each one. It then finds all elements on the page
        that match the given `by` and `value`. It then switches back
        to the original frame and repeats the process for each iframe.
            
        Finally, it returns the list of all elements found on the page
        and in all iframes. Returns a list of GPTWebElement objects.
        """
        elements = self.driver.find_elements(by, value)
        elements = [GPTWebElement(element) for element in elements]
        iframes = self.driver.find_elements(by=By.TAG_NAME, value="iframe")
        logger.info("Found {num} iframes.".format(num=len(iframes)))
        for iframe in iframes:
            self.driver.switch_to.frame(iframe)
            iframe_elements = self.driver.find_elements(by, value)
            iframe_elements = [
                GPTWebElement(element, iframe=iframe)
                for element in iframe_elements
            ]
            elements.extend(iframe_elements)
            self.driver.switch_to.default_content()
        return elements

    @__switch_to_element_iframe
    def find_nearest_textbox(self, element: GPTWebElement):
        try:
            textbox = self.driver.find_element(
                locate_with(By.XPATH, "//div[@role = 'textbox']").near(element)
            )
        except:
            textbox = self.driver.find_element(
                locate_with(By.TAG_NAME, "input").near(element)
            )
        
        textbox_element = GPTWebElement(textbox, iframe=element.iframe)
        return textbox_element

    @__switch_to_element_iframe
    def find_nearest_text(self, element: GPTWebElement):
        try:
            textbox = self.driver.find_element(
                locate_with(By.XPATH, "//*[text() != '']").near(element)
            )
        except:
            return ""

        return textbox.text

    @__switch_to_element_iframe
    def find_nearest(self, element: GPTWebElement, xpath=None):
        try:
            nearest_elem = self.driver.find_element(locate_with(By.XPATH, xpath).near(element))
        except:
            nearest_elem = self.driver.find_element(locate_with(By.XPATH, xpath).below(element))

        nearest_element = GPTWebElement(nearest_elem, iframe=element.iframe)
        return nearest_element

    @__switch_to_element_iframe
    def send_keys(self, element: GPTWebElement, keys):
        if element is not None:
            ActionChains(self.driver).pause(1).move_to_element(element).pause(1).click(
                element
            ).pause(1).send_keys(keys).pause(1).perform()
        else:
            ActionChains(self.driver).pause(1).send_keys(keys).pause(1).perform()
        

    @__switch_to_element_iframe
    def click(self, element: GPTWebElement):
        ActionChains(self.driver).pause(1).move_to_element(element).pause(1).click(
            element
        ).perform()

    def get_text_from_page(self, entire_page=False):
        """Returns the text from the page."""
        # First, we get the HTML of the page and use html2text to convert it
        # to text.
        if entire_page:
            html = self.driver.page_source
            text = html2text.html2text(html)
        else:
            # Only the paragraph elements.
            soup = BeautifulSoup(self.driver.page_source, "lxml")
            text = "\n".join([p.text for p in soup.find_all("p")])

        # Check for iframes too.
        iframes = self.driver.find_elements(by=By.TAG_NAME, value="iframe")
        for iframe in iframes:
            self.driver.switch_to.frame(iframe)
            soup = BeautifulSoup(self.driver.page_source, "lxml")
            text = text + "\n" + "\n".join([p.text for p in soup.find_all("p")])
            self.driver.switch_to.default_content()

        return text

    def retrieve_information(self, prompt, entire_page=False):
        """Retrieves information using using GPT-Index embeddings from a page."""
        text = self.get_text_from_page(entire_page=entire_page)

        # Tokenize by sentence, and then load each set of three sentences as
        # a doc.
        sentences = sent_tokenize(text)
        docs = []
        for i in range(0, len(sentences), 5):
            doc = " ".join(sentences[i : i + 5])
            docs.append(Document(doc))

        # Then we use GPT Index to summarize the text.
        logger.info("Found {num_docs} documents for indexing.".format(num_docs=len(docs)))
        index = GPTSimpleVectorIndex(docs)
        print(text[:150])
        logger.info("Retrieving information with prompt: \"{prompt}\"".format(prompt=prompt))
        resp = index.query(prompt, similarity_top_k=3)
        return resp.response.strip()

    def get_llm_response(self, prompt, temperature=0.7, model="text-davinci-003"):
        try:
            response = openai.Completion.create(
                model=model,
                prompt=prompt,
                temperature=temperature,
                max_tokens=512,
                top_p=1,
                frequency_penalty=0,
                presence_penalty=0,
                best_of=3,
            )

            # Next, we extract the response that was generated by the API.
            text = response["choices"][0]["text"]
            # Finally, we return the response.
            return text
        except openai.error.RateLimitError as exc:
            logger.info(
                "Rate limit error: {exc}. Sleeping for 10 seconds.".format(exc=str(exc))
            )
            time.sleep(5)
            return self.get_llm_response(prompt, model)

    def ask_llm_to_find_element(self, element_description):
        """Clean the HTML from self.driver, ask GPT-Index to find the element,
        and return Selenium code to access it. Return a GPTWebElement."""
        soup = self._clean_html()
        # Get all of the elements, and for each element, if there are
        # children, then remove them.
        elements = soup.find_all()
        [ele.clear() if ele.contents else ele for ele in elements if ele.contents]
        # Then remove any elements that do not have attributes, e.g., <p></p>.
        elements = [ele for ele in elements if ele.attrs]

        # Create a list of documents of each element prettified.
        docs = [Document(element.prettify()) for element in elements]

        # Set up the index and query it.
        index = GPTSimpleVectorIndex(docs)
        query = "Find element that matches description: {element_description}. If no element matches the description, then return {no_resp_token}.".format(
            element_description=element_description, no_resp_token=NO_RESPONSE_TOKEN
        )
        resp = index.query(query)
        resp = resp.response.strip()
        if NO_RESPONSE_TOKEN in resp:
            logger.info("GPT-Index could not find element. Returning None.")
            return None

        logger.info("Asked GPT-Index to find element. Response: {resp}".format(resp=resp))
        # Get the argument to the find_element_by_xpath function.
        prompt = self.instruction_compiler.prompt_to_find_element.format(
            cleaned_html=resp
        )
        response = self.get_llm_response(prompt, temperature=0).strip().replace('"', "")
        element = self.driver.find_element(by="xpath", value=response)
        return GPTWebElement(element)

    def save(self, text, filename):
        """Save the text to a file."""
        with open(filename, "w") as f:
            f.write(text)