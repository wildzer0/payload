"""Random phrases shown when long operations start (build-all, watch).
Replaced by the real table name as soon as the first task starts."""
import random

LOADING_PHRASES = [
    "Brushing the cat...",
    "Aligning the satellites...",
    "Calibrating onboard sensors...",
    "Convincing the linker to cooperate...",
    "Packing bytes with care...",
    "Searching for deep space signal...",
    "Recompacting telemetry...",
    "Syncing the onboard clock...",
    "Checking the orbit...",
]


def random_loading_phrase() -> str:
    return random.choice(LOADING_PHRASES)
