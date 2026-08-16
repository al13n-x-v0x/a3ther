# =========================================================================================
# A.3.T.H.E.R ENGINE
# PHASE 2.1
# backend/ai/reasoning.py
# AL13N INDUSTRIES
# =========================================================================================

import re

# =========================================================================================
# INTENT KEYWORDS
# =========================================================================================

APP_KEYWORDS = [
    "open",
    "launch",
    "start",
    "run"
]

SEARCH_KEYWORDS = [
    "search",
    "find",
    "look up",
    "google"
]

WEATHER_KEYWORDS = [
    "weather",
    "temperature",
    "forecast"
]

YOUTUBE_KEYWORDS = [
    "youtube",
    "video",
    "play"
]

MEMORY_KEYWORDS = [
    "remember",
    "memorize",
    "don't forget"
]

SYSTEM_KEYWORDS = [
    "cpu",
    "ram",
    "battery",
    "system",
    "usage"
]

# A3THER extensions: remote developer mode + plugin/extension management
REMOTE_DEV_KEYWORDS = [
    "act as a dev",
    "dev mode on",
    "connect to server",
    "work on the server",
    "ssh",
    "deploy to",
    "server log",
    "remote server",
]

PLUGIN_KEYWORDS = [
    "plugin",
    "extension",
    "mcp",
    "manage plugins",
    "list plugins",
    "installed extensions",
]

# A3THER features: 3D website maker + multi-agent swarm
WEBSITE_KEYWORDS = [
    "website",
    "web page",
    "landing page",
    "site for",
    "make me a 3d",
    "3d website",
]

AGENT_KEYWORDS = [
    "swarm",
    "multi-agent",
    "delegate",
    "use agents",
    "agents",
    "supervisor",
]

MODE_KEYWORDS = {
    "dev": ["dev mode", "developer mode", "development mode", "programming mode"],
    "research": ["research mode", "analysis mode", "investigation mode"],
    "angry": ["angry mode", "angry tone", "be angry", "surly mode"],
    "chill": ["chill mode", "relaxed mode", "calm mode", "easy mode"],
    "mentor": ["mentor mode", "dad mode", "father mode", "guide me"],
    "ai": ["ai mode", "assistant mode", "normal mode", "standard mode"],
}

# =========================================================================================
# INTENT DETECTION
# =========================================================================================

def DetectIntent(prompt: str) -> str:

    text = prompt.lower().strip()

    # Remote dev mode is the most specific — check before generic keywords
    # so "ssh run ..." / "act as a dev on ..." never fall through to APP.
    if any(word in text for word in REMOTE_DEV_KEYWORDS):
        return "REMOTE_DEV"

    if any(word in text for word in WEBSITE_KEYWORDS):
        return "WEBSITE"

    if any(word in text for word in AGENT_KEYWORDS):
        return "AGENT"

    if any(word in text for word in PLUGIN_KEYWORDS):
        return "PLUGIN"

    if any(word in text for word in APP_KEYWORDS):
        return "OPEN_APP"

    if any(word in text for word in SEARCH_KEYWORDS):
        return "WEB_SEARCH"

    if any(word in text for word in WEATHER_KEYWORDS):
        return "WEATHER"

    if any(word in text for word in YOUTUBE_KEYWORDS):
        return "YOUTUBE"

    if any(word in text for word in MEMORY_KEYWORDS):
        return "MEMORY"

    if any(word in text for word in SYSTEM_KEYWORDS):
        return "SYSTEM"

    return "LLM"

# =========================================================================================
# EXTRACT APPLICATION NAME
# =========================================================================================

def ExtractApplication(prompt: str):

    text = prompt.lower()

    text = re.sub(
        r"(open|launch|start|run)",
        "",
        text
    )

    return text.strip()

# =========================================================================================
# EXTRACT MEMORY CONTENT
# =========================================================================================

def ExtractMemory(prompt: str):

    text = prompt

    for word in [
        "remember",
        "memorize",
        "don't forget"
    ]:

        text = text.replace(word, "")

        text = text.replace(word.title(), "")

    return text.strip()

# =========================================================================================
# MAIN REASONER
# =========================================================================================

def DetectMode(prompt: str) -> str | None:
    normalized = prompt.lower().strip()
    for mode, patterns in MODE_KEYWORDS.items():
        if any(keyword in normalized for keyword in patterns):
            return mode
    return None


def Analyze(prompt: str):
    intent = DetectIntent(prompt)
    mode = DetectMode(prompt)

    return {
        "intent": intent,
        "mode": mode,
        "application": ExtractApplication(prompt),
        "memory": ExtractMemory(prompt),
        "prompt": prompt,
    }

# =========================================================================================
# TEST
# =========================================================================================

if __name__ == "__main__":

    while True:

        query = input(">> ")

        print(Analyze(query))