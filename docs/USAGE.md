# Guida utente — `pld`

Riferimento completo di ogni comando, del file di configurazione, e degli
exit code. Per scrivere un plugin (lato sviluppatore), vedi invece
[PLUGINS.md](PLUGINS.md).

## Installazione

```bash
pip install -e ".[dev]"    # dalla root del progetto
pld --version
```

### Ambiente isolato

Consigliato un `venv` dedicato (stdlib, zero dipendenze extra):

```bash
python3 -m venv .venv
source .venv/bin/activate   # .venv\Scripts\activate su Windows
pip install -e ".[dev]"
```

In alternativa, se vuoi `pld` come comando globale (non legato a un
singolo progetto Python), **`pipx`** è pensato apposta per questo:
crea un venv isolato automaticamente ed espone solo il comando `pld`
nel PATH.

### Ambienti aziendali/rete chiusa

`payload` non ha nessuna dipendenza compilata (vedi sezione
Compatibilità più sotto), quindi le wheel sono "universali" — la stessa
identica wheel funziona ovunque, nessuna compilazione richiesta anche
in un ambiente completamente offline. In ordine di preferenza:

1. **Indice pip interno** (Artifactory, Nexus, devpi): `pip install
   --index-url https://pypi.tuaazienda.it/simple payload` — identico
   all'uso normale.
2. **Wheelhouse offline**: da una macchina con internet, `pip download
   payload -d ./wheelhouse` scarica tutto (incluse le dipendenze);
   trasferisci la cartella nella rete chiusa e installa con `pip
   install --no-index --find-links=./wheelhouse -e .`.
3. **Git interno**: `pip install git+https://git.tuaazienda.it/team/payload.git`
   se il server git è raggiungibile dalla rete chiusa.

Gli stessi tre metodi valgono per installare un plugin di terze parti
(`pld plugin new` genera un pacchetto pip come un altro). Per un plugin
specifico di un solo progetto, vedi anche "Plugin locali" in
[PLUGINS.md](PLUGINS.md#plugin-locali-senza-pip-install) — non
richiede nessuna installazione.

## Concetti in breve

```
sorgente (.raw | .csv | ...) → [Reader] → TableIR → [Writer] → output (.bin | .hex | ...)
```

Ogni tabella è un file sorgente indipendente. Il **reader** è scelto per
estensione (o esplicitamente con `--from`); il **writer** di destinazione
si sceglie con `--to` (o dal default in config). Una **cache basata su
hash** evita di ricompilare tabelle il cui sorgente/config non è
cambiato; i **golden file** permettono di rilevare se l'output è
cambiato rispetto a un riferimento congelato in precedenza.

---

## Comandi

### `pld init [nome] [--force] [--wizard/-w] [--yes/-y]`

Crea lo scaffold minimo di un progetto: `table-tool.toml`, le cartelle
`build/`, `golden/` e `local_plugins/` (per plugin esterni senza `pip
install`, vedi [PLUGINS.md](PLUGINS.md#plugin-locali-senza-pip-install)),
e una tabella di esempio (`example_table.raw`).

```bash
pld init mio-progetto     # crea una cartella nuova dedicata (consigliato)
pld init                  # nella cartella corrente, con conferma se non è vuota
pld init --force          # sovrascrive file esistenti
pld init --wizard         # modalità guidata, chiede cosa includere
```

**Con un nome**, crea una cartella nuova con quel nome e ci mette dentro
lo scaffold — non puoi finire per sbaglio con i file sparsi altrove.
**Senza nome**, se la cartella corrente non è vuota, chiede conferma
esplicita prima di scrivere qualsiasi cosa; se rifiuti, non tocca nulla
e ti suggerisce di usare `pld init <nome>`.

**Con `--wizard`**, guida passo passo attraverso le scelte invece di
usare tutti i default: nome del progetto (se non già dato come
argomento), se includere `local_plugins/`, se includere la tabella di
esempio, writer di default, `byte_order` di default, se inizializzare
un repository git (`git init`, solo se `git` è nel PATH). `--yes`
combinato con `--wizard` salta tutte le domande e usa i default —
utile per script/automazione che vogliono comunque lo scaffold "completo"
del wizard senza interazione.

### `pld build <sorgente> [opzioni]`

Compila una singola tabella.

| Opzione | Default | Significato |
|---|---|---|
| `--from <reader>` | auto-detect | forza un reader specifico invece dell'auto-detect per estensione |
| `--to <writer>` | da config | writer di output da usare |
| `--out <dir>` | `build` | cartella di output |
| `--force` | off | ignora la cache, ricompila comunque |
| `--dry-run` | off | mostra cosa verrebbe fatto senza scrivere nulla |
| `--check-golden` | off | fallisce (exit 3) se l'output non combacia col golden salvato |
| `--opt chiave=valore` | — | override una tantum per il plugin attivo, ripetibile (vedi [PLUGINS.md](PLUGINS.md#passare-informazioni-extra-a-un-plugin)) |

```bash
pld build sensors/temp_table.raw --to bin
pld build sensors/temp_table.raw --from raw_text --to hex --out release/
```

### `pld build-all [root] [opzioni]`

Batch build ricorsivo su tutte le tabelle trovate sotto `root` (default
`.`). Le tabelle sono indipendenti tra loro: nessun grafo di dipendenze,
quindi il batch è parallelizzabile in sicurezza.

| Opzione | Default | Significato |
|---|---|---|
| `--to <writer>` | da config | writer da usare per tutte le tabelle trovate |
| `--out <dir>` | `build` | cartella di output |
| `--jobs N` | `1` | grado di parallelismo (thread pool) |
| `--filter <glob>` | tutti i sorgenti noti | limita la scansione, es. `"sensors/**"` |
| `--force`, `--dry-run`, `--check-golden` | come `build` | applicati a ogni tabella |
| `--opt chiave=valore` | — | override una tantum applicato a tutte le tabelle del batch, ripetibile |

```bash
pld build-all . --to bin --jobs 4
pld build-all . --filter "sensors/**" --check-golden
```

Al termine mostra un riepilogo: quante tabelle costruite, quante servite
da cache, quante con golden mismatch, quanti errori. Se ci sono errori,
esce con `BatchBuildError` (exit 1) ma **ha comunque tentato tutte le
tabelle**, non si ferma alla prima che fallisce.

### `pld watch [file|root] [--to <writer>] [--out <dir>]`

Rebuild automatico ad ogni salvataggio, con debounce (raggruppa eventi
ravvicinati dello stesso file, es. write+rename dell'editor). `Ctrl+C`
per uscire. Un errore di build in watch mode non interrompe il watch:
viene loggato e l'osservazione continua.

```bash
pld watch sensors/temp_table.raw --to bin
pld watch .   # osserva l'intera cartella ricorsivamente
```

### `pld config show [tabella] [--root <dir>]`

Mostra il config risolto (3 livelli: default → globale → sidecar) e
**da dove viene ogni valore** — utile quando non è ovvio quale livello
sta vincendo per una tabella specifica.

```bash
pld config show                # config globale, nessun sidecar coinvolto
pld config show temp_table     # include l'eventuale sidecar di questa tabella
```

### `pld report [root]`

Vista d'insieme del progetto: una riga per tabella con dimensione
sorgente, dimensione output (o "mai buildata"), `byte_order`, stato
golden, ultimo snapshot history. Utile prima di condividere o archiviare
un progetto, o per un colpo d'occhio generale.

```bash
pld report
```

### `pld export <output.zip> [--include-history] [root]`

Crea un archivio `.zip` portabile con tutti i sorgenti tabella
scoperti, `table-tool.toml`, e ogni sidecar `.config.toml` trovato —
utile per condividere un sotto-progetto o farne backup fuori da git.

```bash
pld export backup.zip
pld export backup.zip --include-history   # include anche .payload_history/
```

### `pld status [root]`

Mostra quali tabelle sono cambiate rispetto all'ultimo snapshot salvato
(`mai salvata` / `modificata` / `invariata`).

```bash
pld status
```

### `pld commit -m "messaggio" [--only <tabella>] [root]`

Salva uno snapshot di **sorgente + output generato** per ogni tabella
modificata (o solo per quelle indicate con `--only`, ripetibile). A
differenza di git non c'è un passo `add` separato: le tabelle sono
indipendenti tra loro, quindi non c'è nulla da comporre insieme — ogni
`commit` cattura da sola tutto ciò che è cambiato.

```bash
pld commit -m "calibrazione sensore aggiornata"
pld commit -m "solo questa tabella" --only temp_table
```

### `pld log [tabella] [--root <dir>]`

Storico degli snapshot, come `git log`. Senza argomento, mostra tutte le
tabelle mai salvate.

```bash
pld log temp_table
pld log                # tutte le tabelle tracciate
```

### `pld diff <tabella> [--snapshot <N>] [--root <dir>]`

Confronta il sorgente attuale con uno snapshot (l'ultimo, se `--snapshot`
è omesso). Byte per byte, stesso motore di `golden diff`.

```bash
pld diff temp_table
pld diff temp_table --snapshot 2
```

### `pld restore <tabella> <N> [--root <dir>] [--yes]`

Riporta **sorgente e output generato** allo stato dello snapshot `N`.
Chiede conferma salvo `--yes`.

```bash
pld restore temp_table 3
```

**Nota**: questo non sostituisce git per il progetto nel suo complesso —
`build/` è tipicamente escluso da git (è un artefatto), quindi git da
solo non lega mai insieme "sorgente com'era" e "binario generato com'era"
nello stesso istante. Questo sistema serve proprio a colmare quel buco,
con storage deduplicato in `.payload_history/` (contenuti identici tra
snapshot non occupano spazio doppio). Se vuoi che anche questi snapshot
siano al sicuro su un remoto, `.payload_history/` può tranquillamente
stare dentro il repo git del progetto.

### `pld view <sorgente> [--from <reader>]`

Ispeziona il contenuto raw (bytes esadecimali + eventuali commenti) di
una tabella, senza scrivere alcun output.

```bash
pld view sensors/temp_table.raw
```

### `pld golden update <file|dir>`

Congela l'output attuale come riferimento. Da usare quando un cambio di
output è intenzionale, mai in automatico.

```bash
pld golden update build/sensors/temp_table.bin
pld golden update build/    # tutti i file in build/
```

### `pld golden check <file>`

Verifica senza aggiornare. Exit 3 se mismatch.

### `pld golden diff <file>`

Mostra le differenze byte per byte tra output attuale e golden salvato.

### `pld doctor`

Verifica pre-volo: toolchain raggiungibile, plugin caricabili (con nomi
di quelli rotti), config valida (globale + tutti i sidecar), nomi
tabella duplicati, dipendenze dei plugin locali soddisfatte, git
disponibile (informativo, non bloccante), directory scrivibili, cache
non corrotta. Da eseguire prima di un batch grosso o in CI. Exit 2 se
almeno un check fallisce (FAIL) — un check `WARN` (es. git assente, o
un plugin locale con dipendenze mancanti) non blocca l'exit code.

```bash
pld doctor
```

### `pld plugins`

Elenca reader/writer/doctor-check registrati, con estensioni e versione
API supportata.

### `pld plugin info <nome>`

Mostra la documentazione di un plugin specifico: la docstring della sua
classe (dovrebbe spiegare il formato che gestisce, con un esempio),
estensioni supportate, writer suggerito, eventuali vincoli di
compatibilità. È la documentazione "utente" di un plugin — per scriverne
uno nuovo vedi invece [PLUGINS.md](PLUGINS.md).

```bash
pld plugin info csv
```

### `pld plugin install-deps <file.py> [--yes]`

Installa con `pip` le dipendenze dichiarate da un **plugin locale**
(`REQUIRES = [...]` a livello di modulo, vedi
[PLUGINS.md](PLUGINS.md#plugin-locali-senza-pip-install)). Non c'entra
con un plugin installato via pip — quello gestisce già le proprie
dipendenze da solo tramite il suo `pyproject.toml`.

```bash
pld plugin install-deps local_plugins/mio_writer.py
```

### `pld plugin new <nome-pacchetto> --kind reader|writer|doctor-check [--dest <dir>]`

Genera lo scaffold di un nuovo plugin installabile. Vedi [PLUGINS.md](PLUGINS.md).

```bash
pld plugin new payload-writer-hex --kind writer
```

### `pld plugin validate <nome> [--sample <file>]`

Verifica che un plugin già installato rispetti il contratto
Reader/Writer, a runtime. Vedi [PLUGINS.md](PLUGINS.md#validare-che-il-plugin-rispetti-davvero-il-contratto).

```bash
pld plugin validate csv --sample esempio.csv
```

### `pld clean [--target cache|build|golden|all] [--yes]`

Svuota cache, output di build, o golden file. Chiede conferma salvo `--yes`.

```bash
pld clean --target cache
pld clean --target all --yes
```

### `pld --version`

Mostra la versione installata.

### Verbosità: `-v`, `-vv`

Applicabile a qualsiasi comando, prima del sottocomando:

```bash
pld -v build sensors/temp_table.raw --to bin     # INFO: passi principali
pld -vv build sensors/temp_table.raw --to bin    # DEBUG: timing parse/emit, dettagli cache/config
```

I log vanno su `stderr`, mai su `stdout` — puoi sempre fare pipe
dell'output "vero" del comando senza che i log lo sporchino.

---

## File di configurazione

Tre livelli, precedenza crescente: **`table-tool.toml`** (globale, root
del progetto) → **sidecar per-tabella** (`<nome>.config.toml` accanto al
sorgente) → **flag CLI**. Il merge è profondo: il sidecar sovrascrive
solo le chiavi che dichiara esplicitamente.

`table-tool.toml`:

```toml
[defaults]
writer = "bin"              # writer usato quando --to non è specificato
output_dir = "build"
golden_dir = "golden"
cache_dir = ".payload_cache"
byte_order = "little"       # "little" | "big" — target per reader/writer che gestiscono valori multi-byte

[toolchain]
compiler = "gcc"
compiler_flags = []
objcopy = "objcopy"
objcopy_target = ""   # richiesto solo dal writer 'obj', es. "elf32-littlearm"
objcopy_arch = ""     # richiesto solo dal writer 'obj', es. "arm"
```

Sidecar `sensors/temp_table.config.toml` (opzionale, solo override):

```toml
[defaults]
writer = "hex"               # solo questa tabella usa hex invece del default globale
```

Una terza sezione, `[plugin.<nome>]`, è riservata a informazioni
specifiche di un plugin (non validata dal core) — vedi
[PLUGINS.md](PLUGINS.md#passare-informazioni-extra-a-un-plugin).

---

## Exit code

| Code | Categoria | Esempio |
|---|---|---|
| `0` | successo | build completata, doctor senza FAIL |
| `1` | errore di build | parsing fallito, toolchain fallito, batch con fallimenti |
| `2` | config/plugin | config malformata, plugin non caricabile, doctor con FAIL |
| `3` | golden mismatch | `--check-golden` o `golden check` con differenze |
| `4` | non trovato | sorgente/reader/writer/plugin inesistente |
| `5` | history | snapshot inesistente, nulla da salvare |

Utile per script esterni e CI: `pld build-all . --check-golden || echo "regressione rilevata"`.

---

## Compatibilità

Il core (`payload.core.*`) non usa nessuna API specifica di un sistema
operativo: `pathlib` ovunque al posto di stringhe con `/` a mano,
`shutil.which`/`subprocess.run` (senza `shell=True`) per invocare
toolchain esterni, nessun modulo POSIX-only (`fcntl`, `pwd`, ecc.). Le
uniche tre dipendenze (`typer`, `rich`, `watchdog`) sono pure Python o
hanno backend nativi per ciascun OS gestiti in automatico da watchdog
(inotify su Linux, FSEvents su macOS, ReadDirectoryChanges su Windows).

**Nessuna dipendenza compilata**: `pydantic` è stata deliberatamente
rimossa (era l'unica) perché la sua estensione Rust (`pydantic-core`)
non ha wheel precompilate per diverse piattaforme ARM (es. Termux su
Android) — con solo dipendenze pure Python, `pip install -e .` funziona
ovunque ci sia un interprete Python 3.10+, senza bisogno di toolchain di
compilazione sull'host.

**Non testato automaticamente su tutti gli OS in questo momento** — la
correttezza cross-platform sopra descritta è basata su audit del codice
(niente API OS-specifiche), non su un run reale della test suite su
Windows/macOS. Se lo usi su un OS diverso da Linux e trovi un problema,
è utile saperlo.

Un dettaglio noto e gestito: `Path.glob("cartella/**")` da solo, su
qualsiasi OS, matcha solo la cartella e non i file al suo interno
(comportamento di pathlib, non del sistema operativo) — `--filter`
normalizza questo caso automaticamente.

## Distribuzione come `.exe` standalone (Windows)

Per chi non vuole/può installare Python o `pip` (o vuole distribuire
`pld` a colleghi senza spiegare `pip install`), esiste una build
autocontenuta con [PyInstaller](https://pyinstaller.org): un singolo
`pld.exe` con Python e tutte le dipendenze già dentro.

**Come si ottiene**: la pipeline `.github/workflows/build-exe.yml` lo
compila automaticamente su push di un tag `v*` (allegato alla GitHub
Release) o manualmente da "Actions → build-exe → Run workflow".

Per buildarlo in locale (serve Windows, PyInstaller non fa cross-compiling):
```bash
pip install -e ".[build]"
pyinstaller --onefile --name pld --copy-metadata payload ^
    --hidden-import watchdog.observers.read_directory_changes ^
    scripts/pld_entry.py
```

### Come funzionano i plugin con l'exe

I **plugin builtin** (`raw_text`, `csv`, `c_source`, `bin`, `hex`, `obj`)
sono già dentro l'exe, funzionano subito — richiedono `--copy-metadata
payload` in fase di build (già incluso nella pipeline) perché sono
scoperti via `entry_points`, che ha bisogno dei metadata del pacchetto
anche dentro un binario congelato.

I **plugin esterni** (`.py` di terze parti, senza ricompilare l'exe)
funzionano tramite lo stesso meccanismo di "plugin locali" già visto in
[PLUGINS.md](PLUGINS.md#plugin-locali-senza-pip-install) — `local_plugins/`
accanto a dove lanci `pld.exe`, o `PAYLOAD_PLUGIN_PATH`. Funziona
perché quel meccanismo carica i file `.py` dal disco a runtime
(`importlib.util`), un'operazione indipendente da come il processo
Python stesso è stato impacchettato — **nessuna differenza rispetto
all'installazione normale**.

**Limite reale da conoscere**: un plugin locale usato con l'exe può
importare solo la libreria standard e i moduli già bundled nell'exe
(`payload.*`, `typer`, `rich`, `watchdog`) — se il tuo plugin ha
bisogno di una libreria terza non inclusa nella build (es. `numpy`),
non la troverà, perché PyInstaller include solo quello che rileva
staticamente durante il build. Con `pip install` normale questo
problema non esiste (l'ambiente Python ha accesso a tutto quello che
installi).

Per lo stesso motivo, **`pld plugin install-deps` non funziona dentro
l'exe congelato**: quel comando invoca `pip` tramite l'interprete
Python corrente, ma dentro un binario PyInstaller non c'è un vero
interprete Python dietro le quinte da usare per installare pacchetti —
se un plugin locale dichiara `REQUIRES`, con l'exe quella dipendenza va
già presente nell'exe (quindi impacchettata al momento del build) o il
plugin semplicemente non funzionerà. Se hai bisogno di plugin con
dipendenze dinamiche, l'installazione normale via `pip` resta la scelta
giusta.

## Workflow tipico end-to-end

```bash
pld init                                    # scaffold iniziale
pld doctor                                  # verifica che tutto sia a posto
pld build example_table.raw --to bin        # prima build
pld golden update build/example_table.bin   # congela il riferimento
# ... modifichi il tool o il plugin ...
pld build-all . --check-golden --jobs 4     # verifica di non aver rotto nulla
```
