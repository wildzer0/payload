"""
Interfaccia web locale per payload ('pld serve') — espone via HTTP/SSE
le stesse funzionalità della CLI, per chi preferisce non usare il
terminale. Importato solo da 'pld serve' (import lazy in cli.py): non
deve MAI essere importato a livello di modulo da payload.cli, altrimenti
Starlette/uvicorn diventerebbero dipendenze obbligatorie per l'intera
CLI invece che per il solo extra opzionale 'serve'."""
