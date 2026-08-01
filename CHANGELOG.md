# Changelog

## v0.2.0

**Pipeline configurabile** — modello unico per ogni build, vedi
[src/payload/docs/PIPELINE.md](src/payload/docs/PIPELINE.md) per il design completo.

- Tre tipi di stage: `reader` (file → dati), `writer` (dati → file),
  `exec` (file → file, comando shell/software host)
- Un solo motore di esecuzione (`core/pipeline.py`) per ogni build: una
  build "semplice" (`--from`/`--to`) è internamente una pipeline
  implicita a 2 stage, nessun percorso di codice separato
- `[pipeline]` in `table-tool.toml`/sidecar per dichiarare pipeline
  esplicite multi-stage — sidecar sostituisce l'intera lista `stages`,
  non la fonde elemento per elemento
- Regole di alternanza validate **prima** di eseguire qualunque stage
  (`InvalidPipelineError`, exit 2): reader sempre seguito da writer,
  pipeline minimo 2 stage, `exec` finale richiede `output_extension`
- Compatibilità reader/writer verificata su **ogni** coppia della
  pipeline, non solo la prima
- File intermedi in `tmp/` accanto al sorgente, ripuliti automaticamente
  — `--keep-intermediate` su `build`/`build-all` per ispezionarli
- `--dry-run` non esegue mai stage `exec` (possibili effetti collaterali reali)
- `on_error = "warn"` per stage `exec` non essenziali (non blocca la build)
- Cache sull'intera pipeline (`compute_pipeline_cache_key`): cambiare
  anche un solo stage invalida correttamente
- Nuovo `doctor` check `pipeline_exec`: segnala (informativo, non
  bloccante) quanti stage `exec` sono configurati nel progetto —
  eseguono codice arbitrario da config, vedi PIPELINE.md sezione Sicurezza
- Verificato con `gcc`/`objcopy` e comandi shell reali (non solo
  simulati); un bug reale trovato e corretto durante il test:
  `on_error="warn"` sull'ultimo stage lasciava il file di fallback
  dentro `tmp/`, che veniva ripulita subito dopo — ora viene copiato
  nella posizione finale attesa prima del cleanup
- **Cache per singolo stage**: ogni stage `writer`/`exec` non finale
  persiste il proprio output (fuori da `tmp/`) con una chiave sul
  prefisso di pipeline fino a quel punto — cambiare solo l'ultimo
  stage non richiede più ricompilare un `.c` costoso a monte. `--force`
  bypassa anche questi checkpoint, non solo la cache finale. Bug reale
  trovato durante l'implementazione: `c_source.py`/`obj_writer.py`
  usavano la stessa `tmp/` condivisa dalla pipeline e la cancellavano a
  fine parsing/emit, rompendo gli stage successivi — corretto dando a
  ciascuno una sottocartella privata (`tmp/c_source_scratch/`,
  `tmp/obj_writer_scratch/`)
- Nuovo comando `pld pipeline show <tabella>`: mostra la pipeline
  risolta (implicita o esplicita) e quali stage hanno un checkpoint di
  cache valido in questo momento

## v0.1.1

Checkpoint di rollback prima di iniziare la feature "pipeline". Sei fix
emersi dall'uso reale su un progetto vero (SPARC/RTEMS), tutti
verificati con toolchain reali dove applicabile.

- `run_command` mostra ora stderr/stdout del comando fallito con `-vv` — prima la promessa "esegui con -vv" era falsa, non veniva mai mostrato nulla
- `readers/c_source.py` e `writers/obj_writer.py`: cartella `tmp/` locale accanto al sorgente invece di `AppData\Local\Temp` (Windows) — creata e ripulita automaticamente ad ogni build, mai lasciata sporca
- `.gitignore`: aggiunta `tmp/`
- Fix: `pld watch <sottocartella>` non trovava mai la config globale (`table-tool.toml`) se la sottocartella osservata non coincideva con la cartella da cui si lancia `pld` — ora la config globale si cerca sempre da `Path.cwd()`, coerente con `pld build`. Il sidecar per-tabella non era mai stato affetto (si risolve sempre relativo al file, non a `root`)
- Nuovo comando `pld plugin new-local <nome> --kind reader|writer|doctor-check`: scaffold rapido di un plugin locale (singolo file in `local_plugins/`, nessun `pip install`) — prima l'unico scaffold disponibile (`pld plugin new`) generava un intero pacchetto pip, eccessivo per un plugin di progetto

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
- Fix (causa vera, non la prima ipotesi): dentro un exe PyInstaller congelato, `importlib.metadata.entry_points()` non trova i plugin builtin anche con `--copy-metadata payload` e anche se `importlib.metadata.version()` funziona per lo stesso pacchetto — i 6 plugin builtin ora vengono registrati con `import` diretto quando `sys.frozen` è vero (`core/builtin_plugins.py`), bypassando del tutto `entry_points` in quel contesto. Nessun impatto sull'installazione normale (pip/wheel), verificato che continua a usare `entry_points` come sempre
- `build-exe.yml`: step di verifica fallisce esplicitamente se i plugin builtin non sono trovati, invece di un successo silenzioso con tabella vuota
- Fix: `ModuleNotFoundError: payload.core.builtin_plugins` dentro l'exe — gli import lazy annidati (load_plugins → builtin_plugins → singoli reader/writer) non venivano seguiti fino in fondo dall'analisi statica di PyInstaller. Aggiunto `--collect-submodules payload` al comando di build, che impacchetta l'intero pacchetto indipendentemente da cosa l'analisi statica riesce a rilevare da sola

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
- `src/payload/docs/USAGE.md` — guida utente completa
- `src/payload/docs/PLUGINS.md` — guida sviluppatore plugin, incluse sezioni su endianness e legame reader/writer
- Docstring sulla classe di ogni plugin, mostrata da `pld plugin info <nome>`

### Cose note, non ancora fatte
- Nessun test automatico su Windows/macOS reali (solo audit del codice) — non prioritario per questo rilascio
- `pld watch` su singolo file non è stato validato su Android/Termux
- Nessuna soglia di copertura test fissata (misurata, non ancora decisa)
- `tests/test_cli_smoke.py` (incluso il wizard di `init`) scritto ma non eseguito nell'ambiente di sviluppo di questo repository (nessun accesso a `typer` lì) — verificato con cura contro l'API documentata, da confermare con un run reale
- `pld.exe` compilato in CI ma con il bug entry_points appena descritto — il fix (import diretto dei builtin quando congelato) non è ancora stato verificato su una build Windows reale, solo simulato con `sys.frozen = True` in questo ambiente di sviluppo
- `pld plugin install-deps` non funziona dentro `pld.exe` congelato (nessun vero interprete Python dietro `sys.executable` lì) — documentato come limite noto, non un bug da correggere
