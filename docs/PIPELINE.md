# La pipeline — come funziona

Questo documento descrive il modello con cui `payload` esegue **ogni**
build, dalla più semplice (un reader + un writer, come nella versione
originaria del tool) alla più complessa (più stage con trasformazioni
esterne in mezzo).

**Implementato.** Verificato con `gcc`/`objcopy` reali e con comandi
shell reali su Linux; su Windows la sintassi del comando `exec` segue
le regole della shell dell'host (`cmd.exe`/PowerShell), non ancora
verificata lì di persona — se qualcosa si comporta diversamente,
segnalalo.

---

## Il concetto in una riga

```
sorgente → [stage] → [stage] → [stage] → ... → output finale
```

**Non esiste più una build "semplice" come caso a parte.** Anche
`pld build tabella.raw --to bin` (un reader + un writer) è internamente
una pipeline di 2 stage — solo che non devi scriverla esplicitamente,
perché il tool la costruisce da solo a partire da `--from`/`--to`. Lo
stesso identico motore di esecuzione gestisce sia questo caso implicito
sia una pipeline a 6 stage scritta a mano in un sidecar. Un solo
percorso di codice, zero casi speciali da mantenere in parallelo.

---

## I tre tipi di stage

| Tipo | Firma | Cosa fa |
|---|---|---|
| `reader` | file → dati in memoria | Legge un file, produce `TableIR` (esattamente come i reader oggi) |
| `writer` | dati in memoria → file | Scrive `TableIR` su disco (esattamente come i writer oggi) |
| `exec` | file → file | Esegue un comando esterno (shell/software host) su un file, produce un altro file |

`exec` è il pezzo nuovo: non tocca mai `TableIR`, lavora solo su file —
è il modo in cui uno strumento esterno (un firmatario, un compressore,
un tool proprietario dell'host) entra nella pipeline senza che
`payload` debba sapere nulla del suo formato interno.

---

## Le regole di alternanza

Non ogni sequenza di stage è valida. Le regole, pensate per essere
verificabili **prima** di lanciare qualunque build (non a metà):

1. **Il primo stage deve essere un `reader`** — la pipeline parte
   sempre dal file sorgente della tabella.
2. **Un `reader` deve essere seguito immediatamente da un `writer`** —
   un reader produce dati in memoria (`TableIR`); l'unica cosa che può
   consumarli è un writer. Non esiste "reader dopo reader": non c'è
   nulla che un secondo reader potrebbe leggere da un `TableIR` ancora
   in memoria, deve prima essere scritto su disco.
3. **Dopo un `writer`, puoi mettere**: un `reader` (per rileggere quel
   file e continuare a lavorarci come dati), un `exec` (per
   trasformarlo restando a livello di file), oppure **niente** — quel
   `writer` produce l'output finale.
4. **Dopo un `exec`, puoi mettere**: un `reader`, un altro `exec`,
   oppure **niente** — quell'`exec` produce l'output finale.
5. **La pipeline deve avere almeno 2 stage**: un `reader` e un
   `writer` — il minimo indispensabile, che è esattamente il
   comportamento di oggi.

In breve, come state machine:

```
[inizio] → reader → writer → { fine | reader | exec }
                                            ↑___________|
                              exec → { fine | reader | exec }
```

Una config che viola queste regole (es. due `reader` di fila, o che
finisce con un `reader`) viene rifiutata **in fase di validazione**,
prima di toccare qualunque file — stesso principio già usato per la
validazione del config esistente (`InvalidConfigError`).

---

## Sintassi in config

### Pipeline esplicita, in `table-tool.toml` o in un sidecar

```toml
[pipeline]
stages = [
    { type = "reader", name = "c_source" },
    { type = "writer", name = "bin" },
    { type = "exec", command = "sign_tool.exe {input} {output}" },
    { type = "reader", name = "raw_text" },
    { type = "writer", name = "obj" },
]
```

Se una tabella ha un `[pipeline]` nel proprio sidecar, quello vince —
`--from`/`--to` da CLI vengono ignorati per quella build (con un
warning, non un errore silenzioso: la pipeline esplicita dichiara
già tutto quello che serve, mescolare i due sistemi di specifica
sarebbe ambiguo).

### Forma implicita (shorthand) — quello che usi già oggi

```bash
pld build tabella.raw --to bin
```

Internamente diventa:
```python
PipelineSpec(stages=[
    ReaderStage(name="raw_text"),   # risolto per estensione, come oggi
    WriterStage(name="bin"),         # da --to, o da default_writer del reader
])
```

Nessuna sintassi nuova da imparare per il caso comune — la pipeline
esplicita è lì per quando ti serve davvero, non un requisito per ogni
tabella.

---

## File intermedi

Ogni stage che non produce l'output finale scrive comunque un file
reale su disco (mai solo in memoria tra un writer e un exec, per
esempio) — sono ispezionabili, e ogni comando fallito dice esattamente
su quale file stava lavorando.

Dove vivono: in una cartella `tmp/` accanto al sorgente (stessa
convenzione già adottata per `c_source`/`obj`), **ripulita
automaticamente a fine build**. Con `--keep-intermediate` (su `pld
build`/`pld build-all`), la cartella resta per ispezione manuale —
utile in debug quando un `exec` produce un risultato inatteso e vuoi
vedere l'input esatto che ha ricevuto.

---

## Stage `exec` — i dettagli

```toml
{ type = "exec", command = "sign_tool.exe {input} {output}", on_error = "fail" }
```

**Placeholder disponibili** nel comando: `{input}` (path del file
corrente), `{output}` (path che il tool si aspetta come risultato —
generato automaticamente in `tmp/`), `{table_name}` (nome della
tabella).

**Dopo l'esecuzione**, il tool verifica che `{output}` esista davvero
sul disco — se il comando ritorna 0 ma non ha prodotto il file
atteso, è un errore chiaro (`ToolchainExecutionError`), non un
crash a sorpresa nello stage successivo che si aspetta di trovarlo.

**`on_error`**: `"fail"` (default, ferma la build) o `"warn"` (logga e
prosegue con l'ultimo file valido — pensato per stage non essenziali
tipo una notifica, dove un fallimento non deve bloccare l'output
principale).

---

## Sicurezza — da non sottovalutare

Uno stage `exec` esegue codice arbitrario letto da un file di config.
Se quel `table-tool.toml`/sidecar arriva da qualcun altro (un collega,
un repo condiviso, un tool esterno che genera config), stai eseguendo
comandi arbitrari senza necessariamente accorgertene.

**`pld doctor` segnala sempre, in modo visibile, quanti stage `exec`
sono configurati nel progetto** — check `pipeline_exec`, scansiona il
config globale e tutti i sidecar: `"3 stage 'exec' configurati in 2
file"`, con l'elenco dei file coinvolti nell'hint. Informativo (`WARN`,
non `FAIL`), ma reso impossibile da ignorare. Se in futuro serve
qualcosa di più severo (un flag esplicito tipo `--allow-exec` richiesto
per far girare build con `exec`), lo aggiungiamo quando emerge un caso
d'uso reale che lo giustifica — non prima, per non aggiungere frizione
a chi non ne ha bisogno.

---

## Cache

Due livelli, entrambi attivi automaticamente (nessuna config da
attivare):

**Cache dell'intera pipeline** — se sorgente, l'intera lista di stage e
config sono identici a un run precedente, l'output finale viene
riusato senza eseguire nulla. Stessa logica di sempre, ora sulla firma
di tutti gli stage invece che solo reader+writer.

**Cache per singolo stage** — ad ogni stage `writer`/`exec` che *non* è
l'ultimo, il suo output viene persistito (fuori da `tmp/`, che viene
ripulita ad ogni build) insieme a una chiave calcolata sul **prefisso**
di pipeline fino a quel punto. Alla build successiva, se un prefisso
coincide con uno già cachato, l'esecuzione **riprende da lì** — gli
stage precedenti (inclusa un'eventuale compilazione `.c` costosa) non
vengono rieseguiti, anche se gli stage *successivi* sono cambiati.

```
reader(c_source) → writer(bin) → exec(sign v1)     [primo run: tutto eseguito]
reader(c_source) → writer(bin) → exec(sign v2)     [secondo run: reader+writer
                                                      SALTATI, riparte da exec]
```

`--force` bypassa anche i checkpoint di stage, non solo la cache finale.
`pld pipeline show <tabella>` mostra quali stage hanno un checkpoint
valido in questo momento.

---

## `--dry-run`

Mostra ogni stage che verrebbe eseguito, in ordine, **senza eseguire
gli `exec`** — un comando esterno può avere effetti collaterali reali
(upload, comunicazione con hardware), un dry-run non deve mai
rischiare di innescarli per sbaglio. Per `reader`/`writer` mostra solo
cosa verrebbe scritto, come già oggi.

---

## Cosa NON c'è in questa prima versione

- **Fan-out**: un reader che alimenta più writer in parallelo (es. per
  produrre `.bin` e `.hex` dallo stesso parse) — richiederebbe rompere
  il modello lineare. Se serve davvero, si affronta come iterazione
  successiva una volta che la pipeline lineare è stabile.
- **Stage condizionali** (es. "esegui solo se `profile == release`") —
  stessa logica: aggiungiamolo quando c'è un caso d'uso concreto, non
  in anticipo.

---

## Cosa tocca il codice, se vuoi orientarti

`core/pipeline_spec.py` (stage + regole di alternanza),
`core/pipeline.py` (motore di esecuzione, unico per implicita/esplicita,
inclusa la cache per stage), `core/cache.py`
(`compute_pipeline_cache_key`, `BuildCache.get_output_path`),
`core/config.py` (sezione `[pipeline]`), `core/doctor.py` (check
`pipeline_exec`), `cli.py` (`--keep-intermediate` su
`build`/`build-all`, comando `pld pipeline show`).

## Idee per un'iterazione successiva

- **`--allow-exec`**: se emerge un caso d'uso reale che lo giustifica,
  un flag esplicito richiesto per eseguire build con stage `exec` —
  non aggiunto ora per non introdurre frizione senza un bisogno concreto.
