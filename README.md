# payload (`pld`)

Tool da terminale per creare, compilare, ispezionare e validare tabelle
per sistemi embedded, con formati di input e output estensibili via plugin.

## Pipeline

```
sorgente (.c | .raw | ...) → [Reader plugin] → TableIR → [Writer plugin] → output (.o | .bin | ...)
```

## Documentazione

- **[src/payload/docs/USAGE.md](src/payload/docs/USAGE.md)** — guida utente: ogni comando con
  tutte le opzioni, riferimento del config file, exit code, workflow
  end-to-end. Parti da qui se vuoi *usare* il tool.
- **[src/payload/docs/PLUGINS.md](src/payload/docs/PLUGINS.md)** — guida sviluppatore: contratto
  Reader/Writer/TableIR spiegato per intero, esempi commentati, come
  validare un plugin. Parti da qui se vuoi *estendere* il tool.
- **[src/payload/docs/PIPELINE.md](src/payload/docs/PIPELINE.md)** — pipeline configurabile
  (stage reader/writer/exec): come funziona, sintassi, esempi.
- **[src/payload/docs/BATCH.md](src/payload/docs/BATCH.md)** — tabelle batch: una tabella
  costruita da più file sorgente invece di uno solo (`[[batch_table]]`).

Le stesse guide sono incluse nel pacchetto installato e consultabili
anche senza repository locale, dalla sezione "Documentazione" di `pld serve`.

## Installazione (sviluppo)

```bash
pip install -e ".[dev]"
pld --version
```

## Avvio rapido

```bash
pld init
pld doctor
pld build example_table.raw --to bin
```

## Licenza

Proprietaria, uso interno — vedi [LICENSE](LICENSE).

## Test

```bash
pytest
```

Con `pytest-cov` configurato (report a schermo). La suite è al 100% di
copertura riga per riga. I test del core usano plugin fake in-memory
(`tests/fakes.py`) o subprocess mockati e non richiedono toolchain di
compilazione; `tests/test_c_source_and_obj.py` e
`tests/test_cli_smoke.py::test_c_source_to_obj_via_cli` sono gli unici
che usano `gcc`/`objcopy` reali (integrazione, non necessari per la
copertura) e vengono saltati automaticamente se assenti.

## Build e distribuzione

```bash
python -m pip wheel . -w dist/ --no-deps --no-build-isolation
pip install dist/payload-0.1.0-py3-none-any.whl
```

La wheel è `py3-none-any` (nessuna dipendenza compilata) — la stessa
identica wheel installa ovunque, vedi
[src/payload/docs/USAGE.md](src/payload/docs/USAGE.md#ambienti-aziendalirete-chiusa) per
scenari offline/aziendali.

Per una distribuzione senza `pip` (un singolo `pld.exe` su Windows, con
Python e dipendenze già dentro), vedi
[src/payload/docs/USAGE.md](src/payload/docs/USAGE.md#distribuzione-come-exe-standalone-windows)
— compilata automaticamente da `.github/workflows/build-exe.yml`.


## Plugin inclusi

- `readers/raw_text.py` — formato testuale minimale con commenti
- `readers/csv_reader.py` — CSV strutturato, con gestione offset/gap
- `readers/c_source.py` — compila un `.c` (toolchain reale) ed estrae i bytes di una sezione dati
- `writers/bin_writer.py` — dump binario grezzo
- `writers/hex_writer.py` — formato Intel HEX standard (checksum incluso)
- `writers/obj_writer.py` — `.o` linkabile con sezione dedicata, simboli `__start_`/`__stop_` generati dal linker finale
- `writers/header_writer.py` — header C (`static const uint8_t[]`), zero configurazione di toolchain richiesta
