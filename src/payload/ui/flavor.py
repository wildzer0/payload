"""Frasi random mostrate all'avvio delle operazioni lunghe (build-all, watch).
Sostituite dal nome tabella reale non appena parte il primo task."""
import random

LOADING_PHRASES = [
    "Spazzolando il gatto...",
    "Allineando i satelliti...",
    "Calibrando i sensori di bordo...",
    "Convincendo il linker a cooperare...",
    "Impacchettando i byte con cura...",
    "Cercando segnale dallo spazio profondo...",
    "Ricompattando la telemetria...",
    "Sincronizzando l'orologio di bordo...",
    "Controllando l'orbita...",
]


def random_loading_phrase() -> str:
    return random.choice(LOADING_PHRASES)
