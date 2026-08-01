# Scrivere un plugin per payload

Questa guida risponde alla domanda che chiunque si pone la prima volta:
**cosa deve fare esattamente un reader? cosa riceve un writer? conoscono
entrambi la stessa cosa?**

## Il contratto in una riga

```
sorgente (qualsiasi formato) → Reader.parse() → TableIR → Writer.emit() → file di output
```

**Sì, sia il reader che il writer conoscono `TableIR`.** È l'unico oggetto
che li mette in comunicazione. Il reader non sa nulla del formato di
output finale; il writer non sa nulla (e non deve sapere nulla) di come
i dati sono stati letti. Questo disaccoppiamento è tutto il punto del
sistema a plugin: un reader nuovo funziona automaticamente con ogni
writer esistente, e viceversa.

`TableIR` è definita in `payload/core/ir.py`:

```python
@dataclass
class TableIR:
    name: str                      # nome tabella (es. dal filename)
    data: bytes                    # payload raw — QUESTO è il contenuto vero della tabella
    source_path: Path              # file di origine, per cache/debug/errori
    source_format: str             # nome del reader che l'ha prodotta

    comments: list[tuple[int, str]] = field(default_factory=list)  # (offset, testo) — solo per 'pld view'
    extra: dict = field(default_factory=dict)  # estensioni future, vuoto per ora
```

| Campo | Obbligatorio | A cosa serve |
|---|---|---|
| `name` | sì | nome del file di output (`{name}{writer.extension}`) |
| `data` | sì | **è il contenuto che ogni writer serializza** — il cuore della IR |
| `source_path` | sì | usato per messaggi di errore e come parte della cache key |
| `source_format` | sì | usato nella cache key (reader diverso → cache invalidata) |
| `byte_order` | no (default `"little"`) | ordine in cui `data` è già impacchettata — vedi sezione dedicata sotto |
| `comments` | no | solo `pld view` li mostra; un writer può ignorarli tranquillamente |
| `extra` | no | valvola di sfogo per metadati futuri; convenzione `extra["fields"]` per l'endianness (sotto) |

---

## Cosa deve fare un Reader

Un reader **legge un file e ritorna un `TableIR`**. Tutto qui. Non decide
il formato di output, non scrive nulla su disco.

Interfaccia richiesta (da `payload/core/plugin_base.py`):

```python
class Reader(Protocol):
    name: str              # identificatore univoco, usato con --from
    extensions: list[str]  # es. [".csv"] — per l'auto-detect
    api_version: str       # = PLUGIN_API_VERSION (da payload.core.ir)

    def sniff(self, path: Path) -> bool:
        """Fallback per ambiguità: più reader stessa estensione? Il core
        chiama sniff() su ognuno e usa quello che risponde True. Se sei
        un caso semplice, basta 'return False' — l'estensione da sola
        già ti fa matchare quando non c'è ambiguità."""
        ...

    def parse(self, path: Path, config: dict) -> TableIR:
        """L'unico metodo che conta davvero. Legge path, ritorna TableIR."""
        ...
```

**Cosa succede se il file è malformato?** Solleva `ReaderParseError`
(da `payload.core.errors`), mai una `Exception` generica — è quello che
garantisce un formato di errore coerente in CLI indipendentemente da chi
ha scritto il plugin:

```python
from payload.core.errors import ReaderParseError

raise ReaderParseError(path, "riga 12: valore fuori range")
```

### Esempio reale: `readers/csv_reader.py`

```python
class CsvReader:
    name = "csv"
    extensions = [".csv"]
    api_version = PLUGIN_API_VERSION

    def sniff(self, path: Path) -> bool:
        # usato solo se un'altra estensione .csv fosse rivendicata da
        # un altro reader — qui controlliamo che l'header contenga 'value'
        head = path.read_text(errors="ignore").splitlines()[:1]
        return bool(head) and "value" in head[0].lower()

    def parse(self, path: Path, config: dict) -> TableIR:
        data = bytearray()
        comments = []
        with path.open(newline="") as f:
            for row_num, row in enumerate(csv.DictReader(f), start=2):
                value = int(row["value"], 0)   # accetta '0x0A' o '10'
                data.append(value)
                if row.get("comment"):
                    comments.append((len(data) - 1, row["comment"]))
        return TableIR(
            name=path.stem, data=bytes(data),
            source_path=path, source_format=self.name, comments=comments,
        )
```

Formato CSV atteso da questo reader:
```csv
value,comment
0x0A,soglia min
0x1B,
0x2C,soglia max
```

Guarda il file completo in `src/payload/readers/csv_reader.py` — gestisce
anche una colonna `offset` opzionale per dati non contigui, ed è un buon
punto di partenza da copiare per un reader nuovo.

### Estensione opzionale: `parse_many` (tabelle batch)

Un reader può *in più* implementare `parse_many(self, paths: list[Path], config: dict) -> TableIR`
per essere utilizzabile in una **tabella batch** — una tabella logica
costruita da più file sorgente invece di uno solo (vedi
[BATCH.md](BATCH.md) per il design completo). `parse()` resta
obbligatorio e invariato; `parse_many` è rilevato via duck-typing
(`getattr(reader, "parse_many", None)`), quindi un reader che non lo
implementa continua a funzionare esattamente come oggi — semplicemente
non è utilizzabile in `[[batch_table]]`.

```python
def parse_many(self, paths: list[Path], config: dict) -> TableIR:
    """paths è già nell'ordine di concatenazione corretto (deciso dal
    chiamante, non dal reader) — di solito basta riusare la stessa
    logica di parse() per singolo file, iterando su paths."""
    ...
```

**Non fornire un fallback automatico che concatena `path.read_bytes()`
alla cieca per i reader che non implementano `parse_many`**: è corretto
solo per formati puramente riga-per-riga/byte-per-byte (es. `raw_text`,
che infatti implementa `parse_many`), sbagliato per qualunque formato
con un header o struttura non ripetibile (es. binari con lunghezza in
testa) — un reader che non sa gestire il caso multi-file deve dirlo
chiaramente (`ReaderBatchUnsupportedError`, sollevato automaticamente
da `core/pipeline.py::build()`), non produrre un output silenziosamente
sbagliato.

---

## Cosa deve fare un Writer

Un writer **riceve un `TableIR` già pronto e lo serializza su disco**.
Non fa parsing, non sa da dove vengono i dati — riceve `ir.data` (bytes)
e basta, indipendentemente dal fatto che la fonte fosse CSV, `.c`, o
qualsiasi altro formato futuro.

```python
class Writer(Protocol):
    name: str          # identificatore, usato con --to
    extension: str      # es. ".hex" — determina il nome del file di output
    api_version: str

    def emit(self, ir: TableIR, out_path: Path, config: dict) -> Path:
        """Scrive out_path a partire da ir, ritorna il path scritto
        (di solito semplicemente out_path stesso)."""
        ...
```

Se il writer non riesce a produrre output valido (es. dati troppo grandi
per il formato), solleva `WriterEmitError`:

```python
from payload.core.errors import WriterEmitError

raise WriterEmitError(self.name, "tabella troppo grande per questo formato")
```

### Esempio reale: `writers/hex_writer.py`

Un writer minimale (`bin_writer.py`) fa solo `out_path.write_bytes(ir.data)`.
Un esempio più interessante è `hex_writer.py`, che **trasforma** i bytes
in formato Intel HEX (usato per flashare firmware/dati su microcontrollori):

```python
class HexWriter:
    name = "hex"
    extension = ".hex"
    api_version = PLUGIN_API_VERSION

    def emit(self, ir: TableIR, out_path: Path, config: dict) -> Path:
        if len(ir.data) > 0xFFFF:
            raise WriterEmitError(self.name, "tabella troppo grande (>64KB)")

        lines = []
        for offset in range(0, len(ir.data), 16):
            chunk = ir.data[offset:offset + 16]
            lines.append(_data_record(offset, chunk))  # vedi file completo
        lines.append(_eof_record())

        out_path.write_text("\n".join(lines) + "\n")
        return out_path
```

Punto chiave da notare: **questo writer non ha idea se `ir` è arrivata da
un CSV, da `raw_text`, o da un futuro reader `.c`** — riceve solo bytes.
È esattamente questo disaccoppiamento che rende N reader × M writer
implementabili con N+M plugin, non N×M.

---

## Cosa deve fare un Doctor Check

Un doctor check **verifica una precondizione dell'ambiente/progetto e
ritorna un giudizio**, non partecipa alla pipeline build reader→writer.
È il terzo tipo di plugin (oltre a reader/writer) ed è quello che
alimenta `pld doctor` / `GET /api/doctor` — pensato per cose come "il
compilatore è nel PATH?", "la config è valida?", "i nomi tabella sono
univoci?" (i check builtin in `payload/core/doctor.py` sono un buon
riferimento concreto).

Interfaccia richiesta (da `payload/core/plugin_base.py`):

```python
class DoctorCheck(Protocol):
    name: str          # identificatore univoco, mostrato accanto al risultato
    api_version: str

    def run(self, config: dict) -> CheckResult:
        """Esegue la verifica, ritorna SEMPRE un CheckResult — mai
        un'eccezione per un esito negativo, quella è riservata a un
        errore inatteso del check stesso (vedi sotto)."""
        ...
```

`CheckResult` (da `payload.core.plugin_base`):

```python
CheckResult(name: str, status: str, message: str, hint: str | None = None)
```

`status` è una delle tre costanti di `CheckStatus`:

| Status | Significato | Effetto su `pld doctor` |
|---|---|---|
| `CheckStatus.OK` | tutto a posto | nessuno |
| `CheckStatus.WARN` | problema non bloccante, l'utente dovrebbe saperlo | non fa fallire il comando (exit code resta 0) |
| `CheckStatus.FAIL` | problema che probabilmente rompe una build | fa fallire il comando (exit code 1) |

`hint` è facoltativo, mostrato solo se lo status non è `OK` — usalo per
dire **come risolvere**, non solo cosa è andato storto (es. "installa X"
invece di solo "X non trovato").

### Esempio reale: `ToolchainCheck`

```python
class ToolchainCheck:
    name = "toolchain"
    api_version = "1.0"

    def run(self, config: dict) -> CheckResult:
        cmd = config.get("toolchain", {}).get("compiler")
        if not cmd:
            return CheckResult(self.name, CheckStatus.WARN, "'compiler' non configurato")
        if not shutil.which(cmd):
            return CheckResult(
                self.name, CheckStatus.FAIL, f"'{cmd}' non trovato nel PATH",
                hint=f"Installa {cmd} o aggiorna 'compiler' in table-tool.toml",
            )
        return CheckResult(self.name, CheckStatus.OK, f"{cmd} trovato")
```

### Cosa riceve `config`

Lo stesso dict "risolto" (defaults + toolchain già mergiati secondo la
priorità CLI > sidecar > config globale > default) che riceverebbero
`parse()`/`emit()`, con in più una chiave che i doctor check usano
spesso e reader/writer no: **`config["_project_root"]`** (stringa) —
la cartella del progetto, **da usare sempre al posto della cwd del
processo** per risolvere path relativi. `pld serve` può girare da una
cartella diversa dal progetto che sta servendo: un check che scrive/
legge rispetto alla cwd invece che a `_project_root` inquina la
cartella sbagliata (vedi `DirWritableCheck`/`CacheIntegrityCheck` per
l'idioma corretto: `Path(config.get("_project_root", "."))`).

### Un check non deve mai far esplodere `pld doctor`

`run()` gira insieme a tutti gli altri check, builtin e di terze parti:
se il TUO check solleva un'eccezione grezza (non un `CheckResult` con
status `FAIL`), il core la intercetta e la converte automaticamente in
un `CheckResult` di tipo `FAIL` con il messaggio dell'eccezione — non
crasha più l'intero comando/pagina Doctor, ma **è comunque un
comportamento degradato**: il messaggio che l'utente vede ("il check ha
sollevato un errore inatteso...") è molto meno chiaro di un `FAIL`
scritto apposta da te. Quindi: per un esito negativo *previsto*
(binario mancante, file malformato, ecc.) ritorna sempre un
`CheckResult(..., CheckStatus.FAIL, "spiegazione chiara", hint="...")`
esplicito — riserva le eccezioni ai bug veri nel tuo check.

Questo è anche il motivo per cui lo scaffold generato da
`pld plugin new-local <nome> --kind doctor-check` (un file con
`raise NotImplementedError("TODO: implementa il check")` al posto di un
`run()` vero) **non rompe più `pld doctor`** se lo apri prima di
finirlo: il check semplicemente compare come `FAIL` con quel messaggio,
invece di far fallire l'intero comando con un traceback. `pld doctor`
include anche un check dedicato (`local_plugin_stubs`, non bloccante)
che scansiona tutti i plugin locali del progetto (reader/writer/doctor
check, non solo doctor check) e segnala quelli il cui `parse`/`emit`/
`run` è ancora uno scaffold non implementato, così non serve eseguirli
per scoprirlo — e la stessa informazione compare come badge "non
implementato" nella pagina "Plugin" della web UI, accanto a ogni file
in `local_plugins/`.

---

## Come si registra un plugin

Un plugin è scopribile dal core tramite un `entry_point` dichiarato nel
`pyproject.toml` del pacchetto che lo contiene:

```toml
[project.entry-points."payload.readers"]
csv = "payload.readers.csv_reader:CsvReader"

[project.entry-points."payload.writers"]
hex = "payload.writers.hex_writer:HexWriter"
```

Gruppi disponibili: `payload.readers`, `payload.writers`,
`payload.doctor_checks`. Il nome a sinistra (`csv`, `hex`) è quello che
poi userai con `--from csv` / `--to hex`.

**Modo più veloce per partire**: `pld plugin new payload-reader-<nome> --kind reader`
genera uno scaffold completo (pacchetto pip, `pyproject.toml` con
l'entry_point già corretto, stub della classe, test) — vedi README.

---

## Gestire l'endianness

**Il problema**: `TableIR.data` sono bytes già impacchettati. Se un
reader legge `0x1234` come little-endian e scrive `34 12`, un writer che
fa solo `out_path.write_bytes(ir.data)` non ha modo di sapere che quei
due bytes rappresentano *un* valore a 16 bit che andrebbe riscritto
`12 34` per un target big-endian — è cieco rispetto ai confini dei campi.

**La soluzione**: un reader che lavora con valori multi-byte può (non
deve) esporre anche i **valori strutturati**, non solo i bytes finali:

```python
ir.byte_order = "little"          # ordine in cui `data` è già impacchettata
ir.extra["fields"] = [
    {"offset": 0, "width": 2, "value": 0x1234},
    {"offset": 2, "width": 4, "value": 0xDEADBEEF},
]
```

Un writer interessato all'endianness legge `config["defaults"]["byte_order"]`
(il target richiesto dall'utente/config) e, se diverso da `ir.byte_order`,
usa `payload.core.byteorder.repack(ir.extra["fields"], target_order)` per
ricostruire i bytes nell'ordine giusto — **senza dover reinterpretare
byte grezzi alla cieca**, perché lavora sui valori originali, non sui
bytes già impacchettati da qualcun altro.

```python
from payload.core.byteorder import repack

class MyWriter:
    def emit(self, ir, out_path, config):
        target = config.get("defaults", {}).get("byte_order", ir.byte_order)
        if target != ir.byte_order and ir.extra.get("fields"):
            out_path.write_bytes(repack(ir.extra["fields"], target))
        else:
            out_path.write_bytes(ir.data)  # nessuna reinterpretazione possibile/necessaria
        return out_path
```

**Sì, quindi, puoi avere un reader che legge little-endian e un writer
che scrive big-endian** — a patto che il reader popoli `extra["fields"]`.
Se non lo fa (es. `raw_text.py`, che lavora solo con singoli byte, dove
l'ordine non ha senso), il writer non può fare nulla di intelligente:
il comportamento corretto è **avvisare e passare i bytes così come
sono**, mai tentare uno swap alla cieca che potrebbe corrompere dati.
`bin_writer.py` implementa esattamente questo fallback — guardalo come
riferimento.

`config["defaults"]["byte_order"]` è configurabile in `table-tool.toml`
o per singola tabella nel sidecar (vedi [USAGE.md](USAGE.md)); un reader
dovrebbe sempre impacchettare `data` rispettando quel valore (non un
ordine hardcoded) — `csv_reader.py` fa così.

---

## Legare reader e writer: default e compatibilità

Per default, **qualsiasi reader funziona con qualsiasi writer** — è il
punto del disaccoppiamento N reader × M writer. Ma questo crea due
problemi pratici:

1. Devi sempre specificare `--to` esplicitamente, anche quando per quel
   formato di input c'è un output ovviamente naturale.
2. Se scegli (per errore) un writer pensato per un formato diverso,
   niente ti avvisa — ottieni un output "valido" ma sbagliato in silenzio.

Due attributi opzionali risolvono questo:

**`Reader.default_writer`** — suggerisce il writer da usare quando né
`--to` né `defaults.writer` in config specificano nulla:

```python
class RawTextReader:
    name = "raw_text"
    default_writer = "bin"  # formato dati grezzo -> dump binario, scelta naturale
```

Ordine di risoluzione: `--to` esplicito → `defaults.writer` in config
(solo se qualcuno l'ha impostato davvero — il default di progetto è
`None`, non un valore a caso) → `reader.default_writer` → errore chiaro
(`WriterNotSpecifiedError`) invece di un fallback indovinato.

**`Writer.compatible_readers`** — se impostato, il writer rifiuta
qualsiasi reader non elencato, **prima di eseguire `parse()`** (nessun
lavoro sprecato su una combinazione che fallirà comunque):

```python
class MySpecificWriter:
    name = "my_format"
    compatible_readers = ["my_specific_reader"]  # rifiuta tutti gli altri
```

`None` (il default se non lo dichiari) significa "compatibile con
qualsiasi reader" — corretto per writer come `bin`/`hex` che serializzano
bytes senza interpretarli, quindi non hanno motivo di essere restrittivi.
Dichiaralo solo se il tuo writer **richiede** semantica specifica del
reader (es. si aspetta sempre `extra["fields"]` popolato in un modo
particolare).

---

## Passare informazioni extra a un plugin

`config` (il dict che `parse()`/`emit()` ricevono) contiene solo
`defaults`/`toolchain` per default — se il tuo plugin ha bisogno di
qualcosa che il core non conosce (un delimitatore CSV, un indirizzo
base, un flag specifico del tuo formato), hai **due canali**, per due
scopi diversi:

**1. `[plugin.<nome>]` — persistente, in `table-tool.toml`/sidecar**

Non validata dal core (a differenza di `defaults`/`toolchain`): è
territorio del plugin, il core non può sapere quali chiavi siano
legittime per un plugin di terze parti.

```toml
# table-tool.toml, o <tabella>.config.toml per il sidecar
[plugin.csv]
delimiter = ";"
```

```python
def parse(self, path: Path, config: dict) -> TableIR:
    delimiter = config.get("plugin", {}).get("csv", {}).get("delimiter", ",")
    ...
```

**2. `--opt chiave=valore` — una tantum, solo per questa invocazione**

Non tocca nessun file, non persiste. Utile per un test rapido o uno
script che vuole un override diverso ogni volta:

```bash
pld build sensori/temp.csv --to bin --opt delimiter=";"
```

```python
def parse(self, path: Path, config: dict) -> TableIR:
    override = config.get("cli_opts", {}).get("delimiter")
    ...
```

**`--opt` vince su `[plugin.*]`**, che vince sul default del plugin
stesso — stesso principio di priorità già usato altrove (CLI > config >
default). Entrambi i canali entrano nella cache key: cambiare un
`--opt` o un valore in `[plugin.*]` invalida correttamente la cache,
non serve `--force`.

---

## Plugin locali, senza `pip install`

Un plugin "vero" (pensato per essere riusato su più progetti,
distribuito, versionato) va impacchettato con `pld plugin new` +
`pip install -e .` — è quello che ti dà `entry_points`, versioning
indipendente, installabilità via indice pip interno.

Per un esperimento rapido o un formato specifico di **un solo
progetto**, questa cerimonia può essere eccessiva. `payload` scopre
anche plugin come **singoli file `.py`**, senza nessuna installazione:

**Dove metterli** — due modi, anche insieme:
1. Cartella `local_plugins/` accanto a `table-tool.toml` — scoperta
   automaticamente.
2. Variabile d'ambiente `PAYLOAD_PLUGIN_PATH` (lista di cartelle
   separate da `:` su Unix, `;` su Windows) — utile per condividere
   plugin tra più progetti senza pubblicarli come pacchetto.

**Convenzione nel file**: esponi la classe come variabile a livello di
modulo, `READER`/`WRITER`/`DOCTOR_CHECK` per un plugin singolo, o
`READERS`/`WRITERS`/`DOCTOR_CHECKS` (liste) per più plugin nello stesso
file:

```python
# local_plugins/my_writer.py
class UpperWriter:
    """Converte i dati in maiuscolo prima di scriverli (esempio)."""
    name = "upper"
    extension = ".upper"
    api_version = "1.0"

    def emit(self, ir, out_path, config):
        out_path.write_bytes(ir.data.upper())
        return out_path

WRITER = UpperWriter  # <- questa riga è quello che lo rende scopribile
```

Da quel momento `pld build tabella.raw --to upper` funziona, senza
nessun `pip install`. File che iniziano con `_` sono ignorati (utile
per moduli helper condivisi tra più plugin locali che non sono loro
stessi un plugin).

### Se il plugin ha bisogno di librerie terze

Un plugin locale può dichiarare le proprie dipendenze con `REQUIRES` a
livello di modulo:

```python
# local_plugins/my_writer.py
REQUIRES = ["numpy>=1.20", "pyserial"]

class MyWriter:
    ...
```

Verificato **prima** di tentare il caricamento del modulo (lettura
statica del sorgente via `ast`, senza eseguirlo) — così, anche se il
modulo fallirebbe con un `ModuleNotFoundError` poco chiaro perché
`numpy` non è installato, l'errore che vedi dice esattamente quale
dipendenza manca, invece di un traceback generico:

```bash
pld plugin install-deps local_plugins/my_writer.py
```

installa con `pip`, nell'ambiente corrente, tutto quello che `REQUIRES`
dichiara e che non è già presente. `pld doctor` include anche un check
(`local_plugin_deps`, non bloccante) che scansiona tutti i plugin
locali del progetto e segnala quelli con dipendenze mancanti, senza che
tu debba controllarli uno per uno.

**Limite onesto**: il controllo verifica solo "il pacchetto è
importabile sì/no" — non è una vera risoluzione delle dipendenze
(non controlla che la versione installata soddisfi `>=1.20`, per
esempio). Per quello serve comunque un vero ambiente gestito da pip con
`requirements.txt`/pinning versioni, se il tuo progetto ne ha bisogno
sul serio — `REQUIRES` è pensato per "manca completamente" molto più
che per "è la versione sbagliata".

**Limiti da tenere a mente**: nessun versioning indipendente del
plugin stesso, nessuna distribuzione facile ad altri team (per quello
resta meglio un vero pacchetto pip). Per tutto il resto — errori
(`ReaderParseError`/`WriterEmitError`), `default_writer`/`compatible_readers`,
conformità (`pld plugin validate`) — funziona esattamente come un
plugin installato via pip.

---

## Come sono organizzate le tabelle nelle directory

**Nessuna struttura imposta.** Una tabella è un file sorgente; puoi
averne quante vuoi nella stessa cartella, e la gerarchia di cartelle è
libera — `pld build-all` scopre ricorsivamente tutti i sorgenti sotto la
root, a qualunque profondità.

```
progetto/
├── table-tool.toml
├── sensori/
│   ├── temp_table.raw
│   ├── temp_table.config.toml   # sidecar, stesso nome, stessa cartella
│   └── pressure_table.csv
└── attuatori/
    └── output_table.raw
```

**Unico vincolo reale**: il **nome tabella** (il filename senza
estensione) deve essere **unico in tutto il progetto**, non solo nella
stessa cartella. Il nome è l'identità usata ovunque — file di output in
`build/`, snapshot e riferimento golden in `.payload_history/` — tutti
indicizzati per nome, non per percorso completo. Due file con lo
stesso stem in cartelle diverse (`sensori/temp.raw` e `attuatori/temp.raw`)
collidono silenziosamente su questi fronti. `pld build-all` e `pld
doctor` rilevano questa collisione e la segnalano con
`DuplicateTableNameError` invece di lasciarti scoprire una sovrascrittura
a sorpresa.

---

## Checklist prima di considerare un plugin pronto

- [ ] `name`, `api_version` presenti (per reader anche `extensions`, per writer `extension`)
- [ ] `api_version = PLUGIN_API_VERSION` importato da `payload.core.ir` (non una stringa hardcoded)
- [ ] **Docstring sulla CLASSE** (non solo sul modulo) che spiega il formato con un esempio concreto — è quello che `pld plugin info <nome>` mostra a chi installa il tuo plugin senza leggere il codice
- [ ] Errori sollevati come `ReaderParseError`/`WriterEmitError`, mai `Exception` generica
- [ ] `sniff()` implementato solo se serve davvero disambiguare (altrimenti `return False` va bene)
- [ ] Nessuna dipendenza dal formato di provenienza/destinazione dell'altro lato della pipeline
- [ ] Entry point dichiarato nel gruppo giusto (`payload.readers` / `payload.writers`)
- [ ] `pld plugins` mostra il plugin dopo `pip install -e .`

---

## Validare che il plugin rispetti davvero il contratto

"Scrivere dei test" da solo non garantisce che il plugin sia corretto — un
test potrebbe non verificare nulla di significativo. `payload` fornisce
invece una **suite di conformità** (`payload.testing`) che verifica
comportamenti specifici del contratto Reader/Writer: tipo di ritorno
corretto, errori sollevati come si deve, attributi richiesti presenti.

Nel tuo plugin, in un test pytest:

```python
from payload.testing import assert_reader_conforms

def test_my_reader_conforms(tmp_path):
    sample = tmp_path / "sample.myext"
    sample.write_text("...")  # contenuto valido per il tuo formato
    assert_reader_conforms(MyReader(), sample)
```

```python
from payload.testing import assert_writer_conforms
from payload.core.ir import TableIR

def test_my_writer_conforms(tmp_path):
    sample_ir = TableIR(
        name="sample", data=b"\x00\x01\x02",
        source_path=Path("sample"), source_format="testing",
    )
    assert_writer_conforms(MyWriter(), sample_ir, tmp_path)
```

`pld plugin new` genera già questi test come stub commentati — basta
scommentarli e adattare il sample.

**Anche senza pytest**, puoi validare un plugin già installato a runtime:

```bash
pld plugin validate <nome> --sample percorso/file/esempio.ext
```

Questo esegue la stessa suite senza bisogno della test suite del
pacchetto — utile per verificare rapidamente un plugin di terze parti
prima di fidarti, o in CI dopo l'installazione.

**Perché non è un requisito bloccante al caricamento**: quando un
plugin è installato via `pip`, i suoi test di sviluppo non vengono
distribuiti insieme al pacchetto — il core non ha modo di sapere a
runtime se esistono, tanto meno se passano. `pld plugin validate` è
pensato per essere eseguito esplicitamente (a mano o in CI), non come
gate automatico al load — un gate automatico romperebbe silenziosamente
il tool per chiunque installi un plugin di terze parti scritto prima
che questa suite esistesse.
