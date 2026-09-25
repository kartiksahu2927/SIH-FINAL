"""Replaceable, safety-first decision-support adapters.

The default is deliberately transparent rule-based support. Setting
`AI_PROVIDER=ollama` and `OLLAMA_MODEL=<installed-model>` enables a real local
model without exposing a provider key in the browser.
"""
from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod

import httpx


SAFETY_NOTE = "Decision support only. It is not a diagnosis or final decision; qualified professionals remain responsible."


class AIProvider(ABC):
    @abstractmethod
    async def analyse(self, task: str, text: str) -> dict: ...


class RuleBasedProvider(AIProvider):
    """A named non-AI fallback. It never pretends to be model output."""
    async def analyse(self, task: str, text: str) -> dict:
        normalized = re.sub(r"\s+", " ", text).strip()
        if task == "summarize_record":
            result = {"summary": normalized[:900], "signals": ["summary generated from submitted text only"]}
        elif task == "classify_complaint":
            categories = {"medicine": "MEDICINE_NOT_PROVIDED", "delay": "UNNECESSARY_DELAY", "diagnostic": "DIAGNOSTIC_SERVICE_ISSUE", "referral": "REFERRAL_NOT_PROVIDED", "discharge": "DISCHARGE_ISSUE", "treatment": "TREATMENT_NOT_PROVIDED"}
            category = next((value for term, value in categories.items() if term in normalized.lower()), "OTHER_HEALTHCARE_SERVICE_ISSUE")
            result = {"suggested_category": category, "review_priority": "HIGH" if any(word in normalized.lower() for word in ("urgent", "not treating", "emergency", "refused")) else "NORMAL"}
        else:
            priority = "HIGH" if any(word in normalized.lower() for word in ("pregnancy", "tb", "chest", "uncontrolled", "missed")) else "ROUTINE"
            result = {"suggested_priority": priority, "signals": ["configured keyword rule"]}
        return {"provider": "rule-based", "ai_assisted": False, "method": "transparent rule-based fallback", "result": result, "safety_note": SAFETY_NOTE}


class OllamaProvider(AIProvider):
    def __init__(self, base_url: str, model: str) -> None:
        self.base_url, self.model = base_url.rstrip("/"), model

    async def analyse(self, task: str, text: str) -> dict:
        prompt = f"You are an assistive healthcare operations tool. Task: {task}. {SAFETY_NOTE} Return concise neutral JSON-like analysis. Input: {text}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(f"{self.base_url}/api/generate", json={"model": self.model, "prompt": prompt, "stream": False})
            response.raise_for_status()
            output = response.json().get("response", "")
        return {"provider": "ollama", "model": self.model, "ai_assisted": True, "method": "local model output", "result": {"analysis": output}, "safety_note": SAFETY_NOTE}


def get_ai_provider() -> AIProvider:
    if os.getenv("AI_PROVIDER", "rule-based").lower() == "ollama" and os.getenv("OLLAMA_MODEL"):
        return OllamaProvider(os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"), os.environ["OLLAMA_MODEL"])
    return RuleBasedProvider()
