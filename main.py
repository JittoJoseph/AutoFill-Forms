import os
import re
import sys
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import requests
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.action_chains import ActionChains


# ----------------------------
# Data structures
# ----------------------------
@dataclass
class MCQ:
	kind: str  # 'radio' or 'checkbox'
	question_text: str
	options: List[str]
	# Internal locators are resolved by index during selection; we keep only indices


# ----------------------------
# Environment
# ----------------------------
def load_env() -> None:
	"""Load environment variables from .env (GEMINI_API_KEY)."""
	load_dotenv()


def _get_chrome_user_data_dir() -> str:
	"""Resolve Chrome profile directory on Windows; allow override via CHROME_USER_DATA_DIR env var."""
	override = os.getenv("CHROME_USER_DATA_DIR")
	if override:
		return override

	user_profile = os.environ.get("USERPROFILE")
	local_appdata = os.environ.get("LOCALAPPDATA")
	base = local_appdata or (os.path.join(user_profile, "AppData", "Local") if user_profile else None)
	if not base:
		# Fallback to typical path
		return r"C:\\Users\\%USERNAME%\\AppData\\Local\\Google\\Chrome\\User Data"
	return os.path.join(base, "Google", "Chrome", "User Data")


def _get_default_profile_arg() -> str:
	"""Default Chrome profile directory name. Allow override via CHROME_PROFILE_NAME."""
	return os.getenv("CHROME_PROFILE_NAME", "Default")


def _find_chrome_exe() -> Optional[str]:
	"""Best-effort lookup for chrome.exe. Allow override via CHROME_EXE/CHROME_PATH env var."""
	override = os.getenv("CHROME_EXE") or os.getenv("CHROME_PATH")
	if override and os.path.exists(override):
		return override
	candidates = [
		os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
		os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
		os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
	]
	for c in candidates:
		if c and os.path.exists(c):
			return c
	return None


# ----------------------------
# Browser (Selenium)
# ----------------------------
def _get_edge_user_data_dir() -> str:
	override = os.getenv("EDGE_USER_DATA_DIR")
	if override:
		return override
	user_profile = os.environ.get("USERPROFILE")
	local_appdata = os.environ.get("LOCALAPPDATA")
	base = local_appdata or (os.path.join(user_profile, "AppData", "Local") if user_profile else None)
	if not base:
		return r"C:\\Users\\%USERNAME%\\AppData\\Local\\Microsoft\\Edge\\User Data"
	return os.path.join(base, "Microsoft", "Edge", "User Data")


def _get_profile_name() -> str:
	return os.getenv("BROWSER_PROFILE_NAME", "Default")


def _get_browser_choice() -> str:
	# 'edge' or 'chrome' (default to edge on Windows since it's the default browser)
	return os.getenv("BROWSER", "edge").lower()


def launch_browser(url: str):
	"""Launch device's browser with existing user profile using Selenium (no greenlet)."""
	choice = _get_browser_choice()
	profile = _get_profile_name()
	if choice == "chrome":
		user_data_dir = _get_chrome_user_data_dir()
		print(f"Launching Chrome with profile: {user_data_dir} ({profile})")
		opts = ChromeOptions()
		opts.add_argument(f"--user-data-dir={user_data_dir}")
		opts.add_argument(f"--profile-directory={profile}")
		opts.add_argument("--no-first-run")
		opts.add_argument("--no-default-browser-check")
		# Reduce noisy console logs and automation banner
		opts.add_argument("--log-level=3")
		opts.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
		opts.add_experimental_option("useAutomationExtension", False)
		driver = webdriver.Chrome(options=opts)
	else:
		user_data_dir = _get_edge_user_data_dir()
		print(f"Launching Edge with profile: {user_data_dir} ({profile})")
		opts = EdgeOptions()
		opts.add_argument(f"--user-data-dir={user_data_dir}")
		opts.add_argument(f"--profile-directory={profile}")
		opts.add_argument("--no-first-run")
		opts.add_argument("--no-default-browser-check")
		# Reduce noisy console logs and automation banner
		opts.add_argument("--log-level=3")
		try:
			opts.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
			opts.add_experimental_option("useAutomationExtension", False)
		except Exception:
			pass
		driver = webdriver.Edge(options=opts)

	driver.get(url)
	try:
		WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
	except Exception:
		pass
	time.sleep(1)
	return driver


# ----------------------------
# Extraction
# ----------------------------
def extract_mcqs(driver) -> List[MCQ]:
	"""Extract MCQ questions (radio or checkboxes) from the current Google Form page."""
	mcqs: List[MCQ] = []

	# Google Forms typically puts each question in a listitem
	question_cards = driver.find_elements(By.CSS_SELECTOR, "div[role='listitem']")
	for card in question_cards:

		# Get question heading text
		q_headings = card.find_elements(By.CSS_SELECTOR, "[role='heading']")
		question_text = ""
		if q_headings:
			question_text = (q_headings[0].text or "").strip()
		else:
			# Fallback: try visible text within the card
			try:
				question_text = (card.text or "").split("\n")[0].strip()
			except Exception:
				question_text = ""

		# Determine type and collect options
		# Radio group (single choice)
		radio_groups = card.find_elements(By.CSS_SELECTOR, "[role='radiogroup']")
		radios = []
		if radio_groups:
			radio_options = radio_groups[0].find_elements(By.CSS_SELECTOR, "[role='radio']")
			for opt in radio_options:
				label = (opt.get_attribute("aria-label") or "").strip()
				if not label:
					try:
						label = (opt.text or "").strip()
						if not label:
							spans = opt.find_elements(By.XPATH, "./ancestor::*[1]//span")
							if spans:
								label = (spans[-1].text or "").strip()
					except Exception:
						label = ""
				if label:
					radios.append(label)
			if radios:
				mcqs.append(MCQ(kind="radio", question_text=question_text, options=radios))
				continue

		# Checkbox group (multiple choice)
		# In Google Forms, checkboxes appear as elements with role='checkbox' within the card
		checkbox_options = card.find_elements(By.CSS_SELECTOR, "[role='checkbox']")
		checks: List[str] = []
		if checkbox_options:
			for opt in checkbox_options:
				label = (opt.get_attribute("aria-label") or "").strip()
				if not label:
					try:
						label = (opt.text or "").strip()
						if not label:
							spans = opt.find_elements(By.XPATH, "./ancestor::*[1]//span")
							if spans:
								label = (spans[-1].text or "").strip()
					except Exception:
						label = ""
				if label:
					checks.append(label)
			if checks:
				mcqs.append(MCQ(kind="checkbox", question_text=question_text, options=checks))

	print(f"Detected {len(mcqs)} MCQ question(s) on this page.")
	return mcqs


# ----------------------------
# Gemini API
# ----------------------------
def _coerce_api_key(val: Optional[str]) -> Optional[str]:
	if not val:
		return None
	# Strip any surrounding quotes
	return val.strip().strip('"').strip("'")


def ask_gemini(question: str, options: List[str]) -> int:
	"""Ask Gemini Flash for the best option index (1-based). Falls back to 1 on error."""
	api_key = _coerce_api_key(os.getenv("GEMINI_API_KEY"))
	if not api_key:
		print("GEMINI_API_KEY is not set. Defaulting to option 1.")
		return 1

	prompt = (
		f"Question: {question}\nOptions:\n" +
		"\n".join([f"{i+1}. {opt}" for i, opt in enumerate(options)]) +
		"\nRespond ONLY with the number of the best option."
	)

	url = (
		"https://generativelanguage.googleapis.com/v1beta/models/"
		"gemini-2.0-flash:generateContent"
	)
	headers = {
		"Content-Type": "application/json",
		"X-goog-api-key": api_key,
	}
	body = {
		"contents": [
			{"parts": [{"text": prompt}]}
		]
	}

	try:
		resp = requests.post(url, headers=headers, json=body, timeout=30)
		resp.raise_for_status()
		data = resp.json()
		text = (
			data.get("candidates", [{}])[0]
			.get("content", {})
			.get("parts", [{}])[0]
			.get("text", "")
		)
		# Extract first integer in response
		m = re.search(r"(\d+)", str(text))
		if not m:
			raise ValueError("No number in response")
		idx = int(m.group(1))
		if idx < 1 or idx > len(options):
			print(
				f"Gemini returned out-of-range index {idx}. Clamping to 1..{len(options)}."
			)
			idx = max(1, min(idx, len(options)))
		return idx
	except Exception as e:
		print(f"Gemini API error: {e}. Defaulting to option 1.")
		return 1


# ----------------------------
# Selection
# ----------------------------
def select_answer(driver, question_idx: int, answer_idx: int) -> None:
	"""Click the chosen answer on the page. question_idx is zero-based, answer_idx is one-based."""
	# Re-find the card to avoid stale references
	cards = driver.find_elements(By.CSS_SELECTOR, "div[role='listitem']")
	mcq_positions: List[Tuple[str, int]] = []
	for i, card in enumerate(cards):
		if card.find_elements(By.CSS_SELECTOR, "[role='radiogroup']"):
			mcq_positions.append(("radio", i))
		elif card.find_elements(By.CSS_SELECTOR, "[role='checkbox']"):
			mcq_positions.append(("checkbox", i))

	if question_idx >= len(mcq_positions):
		print(f"Question index {question_idx} out of range; skipping selection.")
		return

	kind, card_i = mcq_positions[question_idx]
	card = cards[card_i]
	idx0 = max(0, answer_idx - 1)

	if kind == "radio":
		group = card.find_elements(By.CSS_SELECTOR, "[role='radiogroup']")[0]
		options = group.find_elements(By.CSS_SELECTOR, "[role='radio']")
		if idx0 >= len(options):
			print("Answer index out of options range; skipping.")
			return
		el = options[idx0]
		# Scroll card and element into view
		driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'nearest'});", card)
		time.sleep(0.1)
		try:
			el.click()
		except Exception:
			try:
				driver.execute_script("arguments[0].click();", el)
			except Exception:
				try:
					parent = el.find_element(By.XPATH, "./ancestor::*[1]")
					driver.execute_script("arguments[0].click();", parent)
				except Exception:
					ActionChains(driver).move_to_element(el).click().perform()
		# Verify selection
		selected = (el.get_attribute("aria-checked") or "").lower() == "true"
		if not selected:
			# Try one more JS click
			driver.execute_script("arguments[0].click();", el)
			time.sleep(0.05)
			selected = (el.get_attribute("aria-checked") or "").lower() == "true"
		if not selected:
			print("   Warning: radio not confirmed selected")
	else:
		options = card.find_elements(By.CSS_SELECTOR, "[role='checkbox']")
		if idx0 >= len(options):
			print("Answer index out of options range; skipping.")
			return
		el = options[idx0]
		driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'nearest'});", card)
		time.sleep(0.1)
		try:
			el.click()
		except Exception:
			try:
				driver.execute_script("arguments[0].click();", el)
			except Exception:
				try:
					parent = el.find_element(By.XPATH, "./ancestor::*[1]")
					driver.execute_script("arguments[0].click();", parent)
				except Exception:
					ActionChains(driver).move_to_element(el).click().perform()
		selected = (el.get_attribute("aria-checked") or "").lower() == "true"
		if not selected:
			driver.execute_script("arguments[0].click();", el)
			time.sleep(0.05)
			selected = (el.get_attribute("aria-checked") or "").lower() == "true"
		if not selected:
			print("   Warning: checkbox not confirmed selected")


# ----------------------------
# Multi-page handling helpers
# ----------------------------
def _page_signature(driver) -> str:
	"""Compute a lightweight signature of current page MCQ layout to detect page changes."""
	js = """
	var cards = Array.from(document.querySelectorAll("div[role='listitem']"));
	var parts = cards.map(function(c){
		var h = c.querySelector("[role='heading']");
		var qt = h ? h.textContent.trim() : '';
		var rc = c.querySelectorAll("[role='radio']").length;
		var cc = c.querySelectorAll("[role='checkbox']").length;
		return qt + '|' + rc + '|' + cc;
	});
	return parts.join("||");
	"""
	try:
		return driver.execute_script(js)
	except Exception:
		return ""


def _has_next_button(driver) -> bool:
	btns = driver.find_elements(
		By.XPATH,
		"//div[@role='button'][.//span[contains(normalize-space(), 'Next')] or contains(normalize-space(), 'Next')]",
	)
	return len(btns) > 0


def _has_submit_button(driver) -> bool:
	btns = driver.find_elements(
		By.XPATH,
		"//div[@role='button'][.//span[contains(normalize-space(), 'Submit')] or contains(normalize-space(), 'Submit')]",
	)
	return len(btns) > 0


def _wait_for_next_page_after_user_click(driver, prev_sig: str) -> None:
	print("Waiting for you to click Next... (the bot will continue after the next page loads)")
	# Use several signals to detect page change reliably
	prev_url = ""
	try:
		prev_url = driver.current_url
	except Exception:
		prev_url = ""

	def _first_heading_text() -> str:
		try:
			el = driver.find_elements(By.CSS_SELECTOR, "div[role='listitem'] [role='heading']")
			if el:
				return (el[0].text or "").strip()
		except Exception:
			pass
		return ""

	def _listitem_count() -> int:
		try:
			return len(driver.find_elements(By.CSS_SELECTOR, "div[role='listitem']"))
		except Exception:
			return -1

	prev_heading = _first_heading_text()
	prev_count = _listitem_count()

	for _ in range(60 * 5):  # up to ~5 minutes
		time.sleep(0.5)
		# 1) URL change
		try:
			if driver.current_url != prev_url and driver.current_url:
				time.sleep(0.5)
				return
		except Exception:
			pass
		# 2) Signature change
		new_sig = _page_signature(driver)
		if new_sig and new_sig != prev_sig:
			time.sleep(0.5)
			return
		# 3) First heading change
		new_heading = _first_heading_text()
		if new_heading and new_heading != prev_heading:
			time.sleep(0.5)
			return
		# 4) Question card count change
		new_count = _listitem_count()
		if new_count != -1 and new_count != prev_count:
			time.sleep(0.5)
			return


# ----------------------------
# Main flow
# ----------------------------
def run(url: str) -> None:
	driver = launch_browser(url)
	try:
		visited = 0
		while True:
			visited += 1
			print(f"\nScanning page {visited} for MCQs...")
			mcqs = extract_mcqs(driver)

			for i, q in enumerate(mcqs):
				if not q.options:
					continue
				print(f"Q{i+1}: {q.question_text[:80]}{'...' if len(q.question_text) > 80 else ''}")
				print(f" - Options: {len(q.options)}")
				ans_idx = ask_gemini(q.question_text, q.options)
				print(f" - Gemini suggests option #{ans_idx}")
				try:
					select_answer(driver, i, ans_idx)
					time.sleep(0.2)
				except Exception as e:
					print(f" - Selection error: {e}")

			prev_sig = _page_signature(driver)
			if _has_next_button(driver):
				print("Next button detected. Please click it manually when ready.")
				_wait_for_next_page_after_user_click(driver, prev_sig)
				# give the page a moment to render new content
				time.sleep(0.5)
				continue

			if _has_submit_button(driver) or not _has_next_button(driver):
				print("\nAll MCQs answered. Please review and click Submit manually.")
				return
	finally:
		# Do not close; keep open for review
		pass


def main():
	# Contract: Reads URL from argv or prompts user, loads env, then runs async flow
	print("Google Forms MCQ Auto-Answer (Gemini Flash)")
	load_env()
	url = None
	if len(sys.argv) > 1:
		url = sys.argv[1]
	while not url:
		url = input("Enter Google Form URL: ").strip()
	run(url)
	# Keep process alive so the browser stays open for review
	try:
		input("\nAll MCQs answered. Please review and click Submit manually.\nPress Enter here to close the helper...")
	except EOFError:
		pass


if __name__ == "__main__":
	main()

