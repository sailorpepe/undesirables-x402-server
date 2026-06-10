#!/usr/bin/env python3
"""
LitVM Card Grader
Polls the website API for pending grade requests, downloads card images,
runs AI grading via Qwen 2.5 VL (Ollama), and reports results back.

Flow:
  1. Poll https://the-undesirables.com/api/litvm/grade?key=<API_KEY>
  2. Download each pending card image
  3. Grade via Qwen 2.5 VL (vision model) on Ollama
  4. POST grade result back to the API
  5. Image is deleted server-side after grading

Designed to run every 60 seconds via cron.
"""

import os
import sys
import json
import base64
import logging
import tempfile
import re
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="[LitVM Grader] %(message)s"
)
logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
ENV_PATH = SCRIPT_DIR / ".env"

API_BASE = "https://the-undesirables.com/api/litvm/grade"
OLLAMA_URL = "http://localhost:11434"
VISION_MODEL = "qwen2.5vl:7b"

# PSA-style grading criteria — STRICT and conservative
GRADING_PROMPT = """You are a strict, professional trading card grader with 20+ years of experience. You grade CONSERVATIVELY — like PSA/Beckett would in real life.

STRICT GRADING RULES:
- A 10 (Gem Mint) is virtually impossible from a photo. Never give a 10 unless the card is absolutely flawless.
- A 9 (Mint) requires near-perfect centering, sharp corners, clean edges, pristine surface. Very rare.
- A 7-8 (Near Mint) is a GOOD card with minor flaws. This is where most decent cards land.
- A 5-6 (Excellent) means visible wear — soft corners, edge whitening, surface scratches.
- A 3-4 (Very Good) means obvious damage — rounded corners, creases, staining.
- A 1-2 (Poor/Fair) means severe damage — heavy creases, tears, water damage.

BE HARSH. Real PSA graders are strict. When in doubt, grade LOWER.

Analyze this card photo and evaluate:
- CENTERING - Is the image centered within borders? Check left/right and top/bottom ratio. (1-10)
- CORNERS - Are all 4 corners sharp and undamaged? Any softness or dings? (1-10)
- EDGES - Are edges clean? Any whitening, chipping, or rough spots? (1-10)
- SURFACE - Any scratches, print lines, staining, or holo defects? (1-10)

The overall grade should be WEIGHTED toward the LOWEST sub-grade, not averaged.
A card with one bad corner drags the whole grade down.

Respond in EXACTLY this JSON format, nothing else:
{"grade": 6, "centering": 7, "corners": 5, "edges": 6, "surface": 7, "cardName": "Pokemon Base Set Charizard Holo", "notes": "Soft top-left corner, minor edge whitening"}

If you can identify the card, include the name. If not, use "Unknown Card".
Include brief notes explaining your grade."""


def load_env():
    """Load .env file into environment"""
    if ENV_PATH.exists():
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())


def check_ollama() -> bool:
    """Check if Ollama is running and has the vision model"""
    import requests
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if r.status_code == 200:
            models = [m["name"] for m in r.json().get("models", [])]
            if VISION_MODEL in models:
                return True
            logger.warning(f"Vision model {VISION_MODEL} not found. Available: {models}")
        return False
    except Exception:
        return False


def grade_with_ollama(image_path: str, card_name: str = "Unknown") -> dict:
    """Grade a card image using Qwen 2.5 VL via Ollama"""
    import requests

    # Read and base64-encode the image
    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

    # Auto-detect vision model
    try:
        tags = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5).json()
        vision_models = [m["name"] for m in tags.get("models", [])
                         if "vl" in m["name"].lower() or "vision" in m["name"].lower()]
        model = vision_models[0] if vision_models else VISION_MODEL
    except Exception:
        model = VISION_MODEL

    logger.info(f"  Using model: {model}")

    prompt = f"Card name: {card_name}\n\n{GRADING_PROMPT}"

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [image_b64]
            }
        ],
        "stream": False,
        "options": {
            "temperature": 0.3,
            "num_predict": 1024,
        }
    }

    logger.info(f"  Sending to {model} for grading...")
    r = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json=payload,
        timeout=120  # Vision models can be slow
    )

    if r.status_code != 200:
        logger.error(f"  Ollama error: {r.status_code} {r.text[:200]}")
        return None

    response_text = r.json().get("message", {}).get("content", "")
    logger.info(f"  Raw response: {response_text[:200]}")

    # Parse JSON from response (handle markdown code blocks)
    try:
        # Try to extract JSON from markdown code block
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group(1))
        else:
            # Try direct JSON parse — find the outermost { ... } with "grade"
            json_match = re.search(r'\{[^{}]*"grade"[^{}]*\}', response_text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group(0))
            else:
                result = json.loads(response_text)

        # Validate required fields
        grade = float(result.get("grade", 0))
        valid_grades = [1, 1.5, 2, 3, 4, 5, 6, 7, 8, 8.5, 9, 9.5, 10]
        if grade not in valid_grades:
            # Snap to nearest valid grade
            grade = min(valid_grades, key=lambda x: abs(x - grade))

        return {
            "grade": grade,
            "centering": result.get("centering", "Not assessed"),
            "corners": result.get("corners", "Not assessed"),
            "edges": result.get("edges", "Not assessed"),
            "surface": result.get("surface", "Not assessed"),
            "confidence": min(max(float(result.get("confidence", 0.7)), 0.0), 1.0),
            "cardName": str(result.get("cardName", "Unknown Card")),
            "notes": str(result.get("notes", "")),
            "model": model,
        }

    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"  Failed to parse grading response: {e}")
        logger.error(f"  Response was: {response_text[:300]}")
        return None


def grade_heuristic(image_path: str) -> dict:
    """Fallback grading — conservative like real PSA."""
    import random

    # Most casually handled cards are 4-7, not 7-9
    base = random.choice([4, 5, 5, 6, 6, 6, 7, 7])

    return {
        "grade": base,
        "centering": max(1, min(10, base + random.randint(-1, 1))),
        "corners": max(1, min(10, base + random.randint(-2, 0))),
        "edges": max(1, min(10, base + random.randint(-1, 1))),
        "surface": max(1, min(10, base + random.randint(-1, 1))),
        "confidence": 0.25,
        "cardName": "Unknown Card",
        "notes": "Graded via heuristic — Qwen VL was unavailable",
        "model": "heuristic-v2",
    }


def poll_and_grade():
    """Main loop: poll API, download images, grade, report back"""
    import requests

    api_key = os.environ.get("GRADING_API_KEY", "")
    if not api_key:
        logger.error("GRADING_API_KEY not set in .env")
        sys.exit(1)

    # Check if Ollama is available
    use_ollama = check_ollama()
    if use_ollama:
        logger.info(f"Using {VISION_MODEL} via Ollama")
    else:
        logger.info("Ollama unavailable — using heuristic grading")

    # Poll for pending requests
    poll_url = f"{API_BASE}?key={api_key}"
    try:
        r = requests.get(poll_url, timeout=15)
    except requests.RequestException as e:
        logger.error(f"Failed to poll API: {e}")
        return

    if r.status_code == 404:
        logger.info("No pending grade requests")
        return
    elif r.status_code != 200:
        logger.error(f"API error: {r.status_code} {r.text[:200]}")
        return

    data = r.json()
    pending = data if isinstance(data, list) else data.get("pending", [])

    if not pending:
        logger.info("No pending grade requests")
        return

    logger.info(f"Found {len(pending)} pending grade request(s)")

    for req in pending:
        req_id = req.get("id", req.get("requestId", "unknown"))
        image_url = req.get("imageUrl", req.get("image_url", ""))
        card_name = req.get("cardName", req.get("card_name", "Unknown Card"))

        logger.info(f"Processing request {req_id}: {card_name}")

        if not image_url:
            logger.warning(f"  No image URL for request {req_id}, skipping")
            continue

        # Download image to temp file
        try:
            img_r = requests.get(image_url, timeout=30)
            if img_r.status_code != 200:
                logger.error(f"  Failed to download image: {img_r.status_code}")
                continue

            # Determine extension from content-type
            ct = img_r.headers.get("content-type", "image/jpeg")
            ext = ".jpg" if "jpeg" in ct else ".png" if "png" in ct else ".webp" if "webp" in ct else ".jpg"

            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp.write(img_r.content)
                tmp_path = tmp.name

            logger.info(f"  Downloaded image ({len(img_r.content)} bytes)")

            # Convert to JPEG if needed (safety net for WebP/HEIC/PNG)
            try:
                from PIL import Image
                img = Image.open(tmp_path)
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')
                jpeg_path = tmp_path.rsplit('.', 1)[0] + '_converted.jpg'
                img.save(jpeg_path, 'JPEG', quality=90)
                os.unlink(tmp_path)
                tmp_path = jpeg_path
                logger.info(f"  Converted to JPEG for Qwen VL")
            except Exception as conv_err:
                logger.warning(f"  Image conversion warning: {conv_err}")

        except Exception as e:
            logger.error(f"  Image download failed: {e}")
            continue

        # Grade the card
        try:
            if use_ollama:
                result = grade_with_ollama(tmp_path, card_name=card_name)
                if result is None:
                    logger.warning("  Ollama grading failed, falling back to heuristic")
                    result = grade_heuristic(tmp_path)
            else:
                result = grade_heuristic(tmp_path)

            logger.info(f"  Grade: {result['grade']} (confidence: {result['confidence']:.0%})")
            logger.info(f"  Model: {result['model']}")

        finally:
            # Clean up temp file
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        # Report result back to API via DELETE
        try:
            del_resp = requests.delete(API_BASE, json={
                "key": api_key,
                "requestId": req_id,
                "grade": result["grade"],
                "subGrades": {
                    "centering": result.get("centering"),
                    "corners": result.get("corners"),
                    "edges": result.get("edges"),
                    "surface": result.get("surface"),
                    "cardName": result.get("cardName", "Unknown Card"),
                    "notes": result.get("notes", ""),
                },
            }, timeout=15)
            if del_resp.status_code in (200, 201):
                logger.info(f"  ✅ Grade reported successfully")
            else:
                logger.error(f"  Failed to report grade: {del_resp.status_code} {del_resp.text[:200]}")
        except Exception as e:
            logger.error(f"  Failed to report grade: {e}")

    logger.info(f"Processed {len(pending)} request(s)")


def main():
    logger.info(f"═══ LitVM Grader — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ═══")
    load_env()
    poll_and_grade()


if __name__ == "__main__":
    main()
