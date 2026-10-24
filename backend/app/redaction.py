"""Secret-free diagnostic text shared by task, transport and detail APIs."""
import re


def redact_text(value, limit: int = 500) -> str:
    text = str(value or "")
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)", "***", text, flags=re.S)
    text = re.sub(r"://[^/@\s]+@", "://***@", text)
    text = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer ***", text)
    text = re.sub(r'''(?i)(?:__password__|(?:agent_token|token|ssh_key|authorization|password|api_key|secret|client-secret)["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\r\n,;]+)''', "***", text)
    return text[:limit]
