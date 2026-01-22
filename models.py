from dataclasses import dataclass
from typing import List


@dataclass
class MCQ:
	kind: str  # 'radio' or 'checkbox'
	question_text: str
	options: List[str]