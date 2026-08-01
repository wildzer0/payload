# Tabelle batch — una tabella costruita da più file sorgente

Questo documento descrive `[[batch_table]]`: il modo di dichiarare una
tabella logica composta da **più file sorgente dello stesso formato**
(es. `ROW1.txt, ROW2.txt, ..., ROWn.txt`), letti insieme da un solo
reader in un'unica `TableIR`, che poi prosegue nella normale pipeline
writer/fan-out **senza nessuna differenza** rispetto a una tabella a
file singolo.

---

## Il concetto in una riga

```
[file1, file2, ..., fileN] → Reader.parse_many() → TableIR → [stage] → ... → output
```

Fuori da questo primo passo, una tabella batch è indistinguibile da una
normale: stesso motore di esecuzione (`core/pipeline.py`), stesso
fan-out multi-writer, stesso sistema di history/golden, stessa cache
incrementale — solo l'identità del "sorgente" è un insieme di file
invece di uno solo.

---

## Perché config esplicita, non naming convention

Una tabella batch **si dichiara sempre esplicitamente** in
`table-tool.toml`, mai per convenzione sul nome file o struttura di
cartelle — coerente con l'approccio già usato per `[pipeline.stages]`
e i sidecar: nessuna euristica implicita che potrebbe raggruppare file
per sbaglio.

```toml
[[batch_table]]
name = "rows"
sources = ["ROW*.txt"]
```

`name` è l'identità della tabella ovunque nel tool (build, history,
golden, cache) — esattamente come lo stem del filename lo è per una
tabella normale. Deve essere **unico in tutto il progetto**, allo
stesso modo dei nomi tabella derivati da file: collide con uno stem
reale o con un altro `[[batch_table]]` → `DuplicateTableNameError`.

**Un file dichiarato come sorgente di una `[[batch_table]]` non compare
più come tabella a sé stante** nella discovery normale, anche se la sua
estensione è riconosciuta da un reader — altrimenti `ROW1.txt` verrebbe
scoperto due volte: come parte del batch e come tabella standalone
`ROW1`, con build/output duplicati.

---

## Campi di `[[batch_table]]`

| Campo | Obbligatorio | Significato |
|---|---|---|
| `name` | sì | nome della tabella, unico nel progetto |
| `sources` | sì | lista di path/pattern (vedi sotto per l'ordine) |
| `reader` | no | override del reader, come `defaults.reader` |
| `writer` | no | override del writer, come `defaults.writer` |
| `byte_order` | no | override di `defaults.byte_order` |
| `stages` | no | pipeline esplicita, stesso shape di `[pipeline.stages]` (vedi [PIPELINE.md](PIPELINE.md)) |

Questi override vivono **inline nel blocco `[[batch_table]]`**, non in
un sidecar: una tabella batch non ha un `source_path` singolo da cui
risolvere un `<nome>.config.toml`. Se non specificati, si applicano i
default globali di `[defaults]`/`[pipeline]` esattamente come per una
tabella normale.

```toml
[[batch_table]]
name = "rows"
sources = ["ROW*.txt"]
reader = "raw_text"
writer = "hex"
byte_order = "big"
```

`[[batch_table]]` è letto **solo dal `table-tool.toml` globale** — una
occorrenza in un sidecar (che comunque non avrebbe senso, dato che i
sidecar sono per-tabella-a-file-singolo) viene semplicemente ignorata.

---

## L'ordine dei file conta

I file vengono concatenati nell'ordine in cui compaiono in
`sources` — importante per formati riga-per-riga come `raw_text`, dove
l'ordine dei dati nel file finale dipende dall'ordine dei sorgenti.
`sources` accetta **sia pattern glob che path letterali, anche mescolati
nella stessa lista**:

- **Un elemento letterale** (nessun metacarattere glob: `*`, `?`, `[`)
  mantiene esattamente la posizione data nella lista — controllo totale
  dell'utente.
- **Un elemento glob** viene espanso e ordinato con un confronto
  "natural sort" (numerico sui blocchi di cifre nel filename), **non**
  lessicografico puro — quindi `ROW2.txt` precede `ROW10.txt` anche con
  `sources = ["ROW*.txt"]`, mentre un ordine lessicografico piazzerebbe
  `ROW10.txt` prima di `ROW2.txt`.

```toml
# Espansione automatica, ordine naturale (ROW1, ROW2, ..., ROW10, ...)
sources = ["ROW*.txt"]

# Controllo esplicito dell'ordine (utile se l'ordine "giusto" non è
# quello numerico dei filename)
sources = ["intro.txt", "ROW3.txt", "ROW1.txt", "coda.txt"]
```

Ogni file risolto deve avere un **filename univoco entro il batch**
(indipendentemente dalla cartella) — due sorgenti diversi con lo stesso
nome, es. `sensori/ROW1.txt` e `attuatori/ROW1.txt` nello stesso batch,
sono un errore di configurazione (`BatchTableError`): la storia
(`source_blobs`, vedi sotto) è indicizzata per filename, una collisione
perderebbe silenziosamente un file.

---

## Il contratto del Reader: `parse_many`

Un reader deve implementare `parse_many(self, paths: list[Path], config: dict) -> TableIR`
(in aggiunta a `parse()`, che resta obbligatorio e invariato) per
essere utilizzabile in una tabella batch — vedi
[PLUGINS.md](PLUGINS.md#estensione-opzionale-parse_many-tabelle-batch)
per il contratto completo. Un reader che non lo implementa fa fallire
la build con `ReaderBatchUnsupportedError`, un errore chiaro invece di
un fallback che concatena bytes alla cieca (sbagliato per formati non
riga-per-riga). `raw_text` implementa già `parse_many` — è il reader di
riferimento per l'esempio `ROW1.txt..ROWn.txt`.

---

## Cache, history, golden: cosa cambia

Concettualmente **nulla** — solo l'identità del sorgente diventa
plurale:

- **Cache**: la chiave di freschezza incorpora l'hash di **tutti** i
  file sorgente (nome, lunghezza e contenuto di ciascuno, in ordine),
  non solo di uno — cambiare anche un solo file membro invalida la
  cache dell'intera tabella.
- **History**: uno snapshot registra un blob per **ogni** file sorgente
  (`{filename: hash}`, stesso schema già usato per gli output) invece
  di uno solo. `pld log`/`pld diff`/`pld restore` funzionano allo
  stesso modo, riportando quale file membro è cambiato.
- **Golden**: `stale` scatta se **uno qualunque** dei file sorgente è
  cambiato dopo lo snapshot golden — stessa logica di prima, solo
  applicata a un insieme invece che a un singolo file.
- **Un file aggiunto o rimosso dal batch tra due commit** viene
  rilevato come "modificata"/`dirty` anche se il contenuto degli altri
  file non cambia — le chiavi dell'insieme sorgenti sono cambiate,
  non solo i valori.

---

## Usarla da CLI e web

```bash
pld build rows                 # nome della [[batch_table]], non un path
pld build-all                  # include automaticamente le tabelle batch
pld status                     # mostra "rows" con un marcatore "(batch, N file)"
pld commit -m "..."            # committa anche le tabelle batch modificate
pld log rows
pld diff rows                  # differenze per ciascun file membro cambiato
pld restore rows <N>
pld golden set rows
```

Lato web, `pld serve` espone lo stesso comportamento in lettura/build
attraverso le route esistenti (dashboard, pagina tabella, history,
golden) — nessuna route nuova per creare/editare `[[batch_table]]` in
questa fase: si definisce a mano in `table-tool.toml`, esattamente come
`[pipeline.stages]` è nato config-file-only prima di ricevere un
builder visuale in una fase successiva.

---

## Limiti espliciti di questa fase

- **`pld watch` non ricostruisce automaticamente una tabella batch**
  quando cambia un file membro — il live-reload resta single-file. La
  build iniziale di `pld watch` include comunque le tabelle batch; un
  file membro modificato durante l'osservazione viene segnalato ma non
  ricostruisce nulla — `pld build <nome_batch>` resta il modo di
  aggiornarla. La webapp non ha una pagina "watch" (rimossa: è una
  funzionalità da terminale, non da browser).
- **`pld view` non supporta le tabelle batch** — non c'è un mapping
  ovvio "quale file mostro" per un comando pensato per ispezionare un
  singolo file.
- **Nessun editor sorgente per le tabelle batch** nella web UI (route
  `/api/source/{table}`): un editor a singolo file non si applica a N
  file, la route rifiuta esplicitamente con un errore chiaro invece di
  mostrare/modificare solo uno dei membri in modo fuorviante.
- **Nessun resume da checkpoint intermedio** per una build batch
  interrotta a metà di una pipeline multi-stage — riparte da zero al
  prossimo tentativo (comportamento comunque corretto, solo non
  ottimizzato come per una tabella a file singolo).

---

## Esempio completo

```
progetto/
├── table-tool.toml
├── ROW1.txt
├── ROW2.txt
└── ROW3.txt
```

```toml
# table-tool.toml
[defaults]
writer = "bin"

[[batch_table]]
name = "rows"
sources = ["ROW*.txt"]
```

```bash
pld build rows            # legge ROW1.txt, ROW2.txt, ROW3.txt in ordine naturale
                           # -> build/rows.bin
pld commit -m "prima versione di rows"
pld golden set rows
```
