import os
import re
import sys
import time
from typing import List
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.action_chains import ActionChains

from config import get_webhook_url, load_env
from discord_notifier import send_discord_batch
from gemini_client import ask_gemini_batch
from models import MCQ


def _get_chrome_user_data_dir() -> str:
	"""Resolve Chrome profile directory on Windows."""
	override = os.getenv("CHROME_USER_DATA_DIR")
	if override:
		return override
	local_appdata = os.environ.get("LOCALAPPDATA")
	if local_appdata:
		return os.path.join(local_appdata, "Google", "Chrome", "User Data")
	return r"C:\Users\%USERNAME%\AppData\Local\Google\Chrome\User Data"


def _get_edge_user_data_dir() -> str:
	"""Resolve Edge profile directory on Windows."""
	override = os.getenv("EDGE_USER_DATA_DIR")
	if override:
		return override
	local_appdata = os.environ.get("LOCALAPPDATA")
	if local_appdata:
		return os.path.join(local_appdata, "Microsoft", "Edge", "User Data")
	return r"C:\Users\%USERNAME%\AppData\Local\Microsoft\Edge\User Data"


def _get_profile_name() -> str:
	return os.getenv("BROWSER_PROFILE_NAME", "Default")


def _get_browser_choice() -> str:
	return os.getenv("BROWSER", "edge").lower()


# ----------------------------
# Browser (Selenium)
# ----------------------------
def launch_browser(url: str):
	"""Launch browser with existing user profile using Selenium."""
	choice = _get_browser_choice()
	profile = _get_profile_name()
	
	if choice == "chrome":
		user_data_dir = _get_chrome_user_data_dir()
		print(f"Launching Chrome with profile: {user_data_dir} ({profile})")
		opts = ChromeOptions()
		opts.add_argument(f"--user-data-dir={user_data_dir}")
		opts.add_argument(f"--profile-directory={profile}")
		opts.add_argument("--no-first-run")
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
		opts.add_argument("--log-level=3")
		try:
			opts.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
			opts.add_experimental_option("useAutomationExtension", False)
		except Exception:
			pass
		driver = webdriver.Edge(options=opts)

	driver.get(url)
	WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
	return driver


# ----------------------------
# Extraction
# ----------------------------
def extract_mcqs(driver) -> List[MCQ]:
	"""Extract MCQ questions (radio or checkboxes) from the current Google Form page."""
	mcqs: List[MCQ] = []
	question_cards = driver.find_elements(By.CSS_SELECTOR, "div[role='listitem']")
	
	for card in question_cards:
		# Get question text
		q_headings = card.find_elements(By.CSS_SELECTOR, "[role='heading']")
		question_text = q_headings[0].text.strip() if q_headings else card.text.split("\n")[0].strip()
		question_text = re.sub(r'\[.*?\]', '', question_text).strip()
		if not question_text:
			continue

		# Check for radio buttons
		radio_groups = card.find_elements(By.CSS_SELECTOR, "[role='radiogroup']")
		if radio_groups:
			options = []
			for opt in radio_groups[0].find_elements(By.CSS_SELECTOR, "[role='radio']"):
				label = opt.get_attribute("aria-label") or opt.text or ""
				if label.strip():
					options.append(label.strip())
			if options:
				mcqs.append(MCQ(kind="radio", question_text=question_text, options=options))
				continue

		# Check for checkboxes
		checkboxes = card.find_elements(By.CSS_SELECTOR, "[role='checkbox']")
		if checkboxes:
			options = []
			for opt in checkboxes:
				label = opt.get_attribute("aria-label") or opt.text or ""
				if label.strip():
					options.append(label.strip())
			if options:
				mcqs.append(MCQ(kind="checkbox", question_text=question_text, options=options))

	print(f"Detected {len(mcqs)} MCQ question(s) on this page.")
	return mcqs


# ----------------------------
# Selection
# ----------------------------
def select_answer(driver, question_idx: int, answer_idx: int) -> None:
	"""Click the chosen answer on the page."""
	cards = driver.find_elements(By.CSS_SELECTOR, "div[role='listitem']")
	mcq_cards = [i for i, card in enumerate(cards) 
				 if card.find_elements(By.CSS_SELECTOR, "[role='radiogroup'], [role='checkbox']")]
	
	if question_idx >= len(mcq_cards):
		print(f"Question index {question_idx} out of range; skipping.")
		return

	card = cards[mcq_cards[question_idx]]
	idx0 = max(0, answer_idx - 1)

	# Find the appropriate option element
	radio_group = card.find_elements(By.CSS_SELECTOR, "[role='radiogroup']")
	if radio_group:
		options = radio_group[0].find_elements(By.CSS_SELECTOR, "[role='radio']")
	else:
		options = card.find_elements(By.CSS_SELECTOR, "[role='checkbox']")
	
	if idx0 >= len(options):
		print("Answer index out of range; skipping.")
		return

	# Click with retry
	el = options[idx0]
	driver.execute_script("arguments[0].scrollIntoView({block:'center'});", card)
	
	for attempt in range(3):
		try:
			if attempt == 0:
				el.click()
			else:
				driver.execute_script("arguments[0].click();", el)
			
			# Verify selection
			if el.get_attribute("aria-checked") == "true":
				return
			time.sleep(0.1)
		except Exception:
			time.sleep(0.1)
	
	print("   Warning: selection may have failed")


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
	return len(driver.find_elements(By.XPATH, "//div[@role='button']//span[contains(text(), 'Next')]")) > 0


def _has_submit_button(driver) -> bool:
	return len(driver.find_elements(By.XPATH, "//div[@role='button']//span[contains(text(), 'Submit')]")) > 0


def _wait_for_next_page(driver, prev_sig: str) -> None:
	print("Waiting for you to click Next...")
	for _ in range(300):  # 5 minutes max
		time.sleep(1)
		new_sig = _page_signature(driver)
		if new_sig != prev_sig:
			time.sleep(1)  # Let page settle
			return


# ----------------------------
# Main flow
# ----------------------------
def run(url: str) -> None:
	skipped_questions = []
	webhook_url = get_webhook_url()
	driver = launch_browser(url)
	try:
		visited = 0
		while True:
			visited += 1
			print(f"\nScanning page {visited} for MCQs...")
			mcqs = extract_mcqs(driver)

			batch_size = 5
			for start in range(0, len(mcqs), batch_size):
				batch = mcqs[start:start + batch_size]
				answers = ask_gemini_batch(batch)
				batch_results = []
				for j, q in enumerate(batch):
					q_idx = start + j
					ans_idx = answers[j] if j < len(answers) else None
					print(f"Q{q_idx+1}: {q.question_text[:80]}{'...' if len(q.question_text) > 80 else ''}")
					print(f" - Options: {len(q.options)}")
					if ans_idx is None:
						print(" - Failed to get answer. Skipping question.")
						skipped_questions.append(f"Page {visited}, Q{q_idx+1}: {q.question_text[:50]}...")
						batch_results.append({
							"question_number": q_idx + 1,
							"question": q.question_text,
							"answer_number": None,
							"answer_text": None,
						})
						continue
					print(f" - Gemini suggests option #{ans_idx}")
					batch_results.append({
						"question_number": q_idx + 1,
						"question": q.question_text,
						"answer_number": ans_idx,
						"answer_text": q.options[ans_idx - 1] if 1 <= ans_idx <= len(q.options) else None,
					})
					try:
						select_answer(driver, q_idx, ans_idx)
						time.sleep(0.2)
					except Exception as e:
						print(f" - Selection error: {e}")

				send_discord_batch(webhook_url, visited, start + 1, batch_results)

			prev_sig = _page_signature(driver)
			if _has_next_button(driver):
				print("Next button detected. Please click it manually when ready.")
				_wait_for_next_page(driver, prev_sig)
				continue

			if _has_submit_button(driver):
				print("\nAll MCQs answered. Please review and click Submit manually.")
				break
		if skipped_questions:
			print("\nSkipped questions (failed to answer):")
			for sq in skipped_questions:
				print(f" - {sq}")
	finally:
		# Do not close; keep open for review
		pass


def main():
	print("Google Forms MCQ Auto-Answer (Gemini Flash)")
	load_env()
	url = sys.argv[1] if len(sys.argv) > 1 else input("Enter Google Form URL: ").strip()
	run(url)
	input("\nPress Enter to close...")


if __name__ == "__main__":
	main()

