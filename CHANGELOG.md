# Changelog

## v0.1.0 (non ancora rilasciata)

Prima versione funzionante del tool.

### Pipeline core
- Architettura a plugin: `sorgente → Reader → TableIR → Writer → output`
- Discovery plugin via `entry_points` (`payload.readers`, `payload.writers`, `payload.doctor_checks`)
- Cache incrementale basata su hash del contenuto (sorgente + reader + writer + config)
- Risoluzione automatica del writer: `--to` esplicito → config → `reader.default_writer` → errore chiaro
- `writer.compatible_readers`: combinazioni reader/writer incompatibili rifiutate prima del parsing
- Gestione esplicita dell'endianness (`TableIR.byte_order`, `extra["fields"]`, `payload.core.byteorder`)
- Passaggio informazioni extra ai plugin: `[plugin.<nome>]` persistente in config, `--opt chiave=valore` una tantum da CLI (entrambi entrano nella cache key)
- Plugin locali senza `pip install`: `local_plugins/` accanto al progetto o `PAYLOAD_PLUGIN_PATH`, convenzione `READER`/`WRITER`/`DOCTOR_CHECK` (singolare o plurale) a livello di modulo
- Dipendenze dichiarate dai plugin locali: `REQUIRES = [...]`, letto staticamente (AST, non esecuzione) anche se il modulo non sarebbe importabile; `pld plugin install-deps <file>` le installa con pip
- `pld init --wizard`: modalità guidata (nome progetto, cosa includere, writer/byte_order di default, `git init` opzionale); `local_plugins/` creata di default anche senza wizard
- `doctor`: nuovi check `git` (informativo) e `local_plugin_deps` (dipendenze mancanti nei plugin locali); fix di due check (`plugins`, `table_names`) che ignoravano la project root reale
- Fix: `UnicodeEncodeError` su console Windows con codepage legacy (cp1252) durante la stampa dei tip con emoji — `stdout`/`stderr` riconfigurati con `errors="replace"` all'avvio della CLI
- `build-exe.yml`: installazione non-editable (`pip install .`) prima del build PyInstaller (sospetta causa di `entry_points` vuoti dentro l'exe con installazione editable); step di verifica ora fallisce esplicitamente se i plugin builtin non sono trovati, invece di un successo silenzioso con tabella vuota

### Comandi
- `init`, `doctor`, `plugins`, `plugin new/validate/info`, `clean`
- `build`, `build-all` (con `--jobs` parallelo reale via `ThreadPoolExecutor`)
- `watch` (debounce, esclusione automatica della output dir)
- `view`, `golden update/check/diff`
- `status`, `commit`, `log`, `diff`, `restore` — checkpoint leggero per tabella, con storage a blob deduplicato e sharded
- `config show` — config risolto con provenienza per campo (default/globale/sidecar)
- `report` — vista d'insieme del progetto (dimensioni, byte_order, stato golden, ultimo snapshot)
- `export` — archivio `.zip` portabile di sorgenti + config di progetto

### Plugin inclusi
- Reader: `raw_text` (testo con commenti), `csv` (strutturato, multi-byte, endianness), `c_source` (compila `.c` reale, estrae bytes da sezione dedicata)
- Writer: `bin` (dump grezzo, con repacking automatico su mismatch di endianness), `hex` (Intel HEX), `obj` (`.o` linkabile, sezione nominata per tabella, simboli `__start_`/`__stop_` verificati con un link reale)

### Testing e packaging
- Test a livello CLI (`tests/test_cli_smoke.py`, `CliRunner`) oltre a quelli a livello core
- `tests/test_c_source_and_obj.py` — verifica con `gcc`/`objcopy` reali, incluso un link C completo che legge i dati tramite i simboli `__start_`/`__stop_` generati dal linker
- `pytest-cov` configurato (report, nessuna soglia imposta a priori)
- Verificata la build wheel reale (`py3-none-any`, nessuna dipendenza compilata) e l'installazione non-editable in un venv pulito, entry_points ispezionati direttamente dal pacchetto installato
- `.github/workflows/build-exe.yml` — build automatica di `pld.exe` standalone (Windows, PyInstaller) su tag `v*`, allegato a GitHub Release. I plugin builtin funzionano dentro l'exe (`--copy-metadata payload`); i plugin locali (`local_plugins/`, `PAYLOAD_PLUGIN_PATH`) funzionano identici, nessuna ricompilazione richiesta — **non verificato con una build reale** (richiede un runner Windows, non disponibile in questo ambiente di sviluppo)

### Robustezza
- Gerarchia di eccezioni con exit code dedicati (0-5) e log level coerenti
- Config a 3 livelli (globale → sidecar per-tabella → CLI) validata a mano, **zero dipendenze compilate**
  (rimossa `pydantic`: la sua estensione Rust non installa su diverse piattaforme ARM/Termux)
- Rilevamento nomi tabella duplicati (`build-all`, `doctor`) — build/golden/history sono indicizzati per nome
- `pld init` non scrive mai nella cartella corrente senza conferma esplicita
- Fix: `typer.Exit` sollevato dentro `_run()` (11 comandi: doctor, clean, view, diff, restore, ecc.) veniva erroneamente catturato come bug interno invece che come uscita controllata
- Suite di conformità (`payload.testing`) per validare plugin di terze parti a runtime, senza richiedere pytest

### Documentazione
- `docs/USAGE.md` — guida utente completa
- `docs/PLUGINS.md` — guida sviluppatore plugin, incluse sezioni su endianness e legame reader/writer
- Docstring sulla classe di ogni plugin, mostrata da `pld plugin info <nome>`

### Cose note, non ancora fatte
- Nessun test automatico su Windows/macOS reali (solo audit del codice) — non prioritario per questo rilascio
- `pld watch` su singolo file non è stato validato su Android/Termux
- Nessuna soglia di copertura test fissata (misurata, non ancora decisa)
- `tests/test_cli_smoke.py` (incluso il wizard di `init`) scritto ma non eseguito nell'ambiente di sviluppo di questo repository (nessun accesso a `typer` lì) — verificato con cura contro l'API documentata, da confermare con un run reale
- `pld.exe` mai compilato/eseguito realmente (richiede un runner Windows) — la pipeline è pronta ma la prima verifica vera sarà al primo push di un tag `v*`
- `pld plugin install-deps` non funziona dentro `pld.exe` congelato (nessun vero interprete Python dietro `sys.executable` lì) — documentato come limite noto, non un bug da correggere
