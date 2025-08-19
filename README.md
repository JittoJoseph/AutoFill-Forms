# Google Forms MCQ Auto-Answer (Gemini Flash)

Fills MCQ questions (radio/checkbox) in a Google Form using your existing Chrome or Edge profile and Gemini Flash suggestions. It never submits the form.

## Setup

1. Install Python 3.9+
2. Create `.env`:

```
GEMINI_API_KEY=your_api_key_here
```

3. Install dependencies:

```
pip install -r requirements.txt
```

## Run

```
python main.py
```

## What it does

- Opens the form using your existing browser profile (already signed in).
- Detects only MCQs (radio/checkbox) and ignores other input types.
- Sends each MCQ to Gemini via HTTP and selects the suggested option.
- If there’s a Next button, waits for you to click and continues.
- Never submits the form; shows:

```
All MCQs answered. Please review and click Submit manually.
```

## Optional config

- Choose browser: `BROWSER=edge|chrome` (default: edge)
- Choose profile: `BROWSER_PROFILE_NAME=Default` (e.g., "Profile 1")
- Override profile paths if needed:
  - Chrome: `CHROME_USER_DATA_DIR=C:\Users\<you>\AppData\Local\Google\Chrome\User Data`
  - Edge: `EDGE_USER_DATA_DIR=C:\Users\<you>\AppData\Local\Microsoft\Edge\User Data`

Note: If the Gemini API call fails, it defaults to option 1.
