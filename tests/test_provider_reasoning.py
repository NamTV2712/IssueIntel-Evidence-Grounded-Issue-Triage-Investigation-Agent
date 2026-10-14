from dataclasses import replace
from io import BytesIO
import json
from issueintel.investigation.providers import GroqProvider
from test_runtime import configured


def test_gpt_oss_uses_include_reasoning_and_retains_durable_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-test-key")
    seen = []
    def transport(request, timeout):
        seen.append(json.loads(request.data))
        return BytesIO(json.dumps({"choices":[{"finish_reason":"stop","message":{"content":"{}"}}],
            "usage":{"prompt_tokens":100,"completion_tokens":20,"total_tokens":120}}).encode())
    provider = GroqProvider(replace(configured(tmp_path), reasoning_effort="high"), transport=transport)
    provider.complete_json([{"role":"user","content":"synthetic fixture only"}], 256)
    assert seen[0]["reasoning_effort"] == "high"
    assert seen[0]["include_reasoning"] is False and "reasoning_format" not in seen[0]
    assert provider.last_reservation and provider.last_usage["total_tokens"] == 120
