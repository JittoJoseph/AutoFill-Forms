import json
import os
import re
from typing import List, Optional

from google import genai

from models import MCQ


current_api_key_idx = 0


def _coerce_api_key(val: Optional[str]) -> Optional[str]:
	if not val:
		return None
	return val.strip().strip('"').strip("'")


def _get_api_keys() -> List[str]:
	raw_keys = os.getenv("GEMINI_API_KEYS", "").split(",")
	keys = [_coerce_api_key(k) for k in raw_keys]
	keys = [k for k in keys if k]
	return keys


def ask_gemini_batch(questions: List[MCQ]) -> List[Optional[int]]:
	"""Ask Gemini for a batch of questions. Returns 1-based indices or None when unresolved."""
	global current_api_key_idx
	keys = _get_api_keys()
	if not keys:
		print("No API keys set. Skipping batch.")
		return [None for _ in questions]

	models = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3-flash-preview"]  # Priority order

	items = []
	for i, q in enumerate(questions, start=1):
		opts = "\n".join([f"{j+1}. {opt}" for j, opt in enumerate(q.options)])
		items.append(f"Q{i}: {q.question_text}\nOptions:\n{opts}")

	prompt = (
		"You are answering multiple-choice questions. "
		"Return ONLY valid JSON in this exact format: "
		"{\"answers\":[{\"q\":1,\"answer\":<number>},...]}\n"
		"Rules:\n"
		"- answer must be the option number (1-based)\n"
		"- include every question exactly once\n"
		"- no extra keys, no markdown, no commentary\n\n"
		+ "\n\n".join(items)
	)

	current_idx = current_api_key_idx
	for _ in range(20):
		api_key = keys[current_idx]
		client = genai.Client(api_key=api_key)
		switch_key = False

		for model in models:
			try:
				response = client.models.generate_content(model=model, contents=prompt)
				if not response.candidates or not response.candidates[0].content or not response.candidates[0].content.parts:
					print(f"No valid response from {model}. Trying next model...")
					continue
				text = "".join([part.text for part in response.candidates[0].content.parts if hasattr(part, 'text')])

				try:
					data = json.loads(text.strip())
				except json.JSONDecodeError:
					continue

				answers = [None for _ in questions]
				for item in data.get("answers", []):
					try:
						q_idx = int(item.get("q")) - 1
						ans = int(item.get("answer"))
						if 0 <= q_idx < len(questions) and 1 <= ans <= len(questions[q_idx].options):
							answers[q_idx] = ans
					except Exception:
						continue

				if any(a is not None for a in answers):
					return answers
			except Exception as e:
				if "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e):
					print(f"Quota exceeded on {model} with key {current_idx + 1}. Switching to next key...")
					switch_key = True
					break
				print(f"Error with {model}: {e}. Trying next model...")
				continue

		current_api_key_idx = (current_idx + 1) % len(keys)
		current_idx = current_api_key_idx
		if switch_key:
			continue

	return [None for _ in questions]