from typing import List, Optional

import requests


def _truncate(text: str, limit: int) -> str:
	if len(text) <= limit:
		return text
	return text[: limit - 3] + "..."


def send_discord_batch(
	webhook_url: str,
	page_num: int,
	batch_start: int,
	results: List[dict],
) -> None:
	if not webhook_url:
		return

	fields = []
	for r in results:
		q_num = r.get("question_number")
		q_text = _truncate(str(r.get("question", "")), 200)
		ans_num = r.get("answer_number")
		ans_text = r.get("answer_text") or "(no answer)"
		value = f"Answer #: {ans_num if ans_num is not None else 'N/A'}\nAnswer: {_truncate(ans_text, 500)}"
		fields.append({
			"name": _truncate(f"Q{q_num}: {q_text}", 256),
			"value": _truncate(value, 1024),
			"inline": False,
		})

	end_num = results[-1].get("question_number") if results else batch_start
	payload = {
		"embeds": [
			{
				"title": f"Batch results - Page {page_num} (Q{batch_start}-Q{end_num})",
				"color": 0x5865F2,
				"fields": fields,
			}
		]
	}

	try:
		requests.post(webhook_url, json=payload, timeout=10)
	except Exception as e:
		print(f"Discord webhook error: {e}")