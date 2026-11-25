# lights-out

Harness di autonomia governata: sei ruoli agente portano un ticket fino alla produzione su un codebase di cui nessuno si fida — ogni cambio passa gate numerici, resta nel perimetro dichiarato dal ticket, torna indietro da solo — e un solo gate umano prima del 100%. Lo stack Google è il target cloud opzionale (L2/L3), non l'identità (ADR 0029). L'app bersaglio è il primo workload dietro una porta, non il soggetto del prodotto.

Il piano completo è in `docs/PLAN.md`. Leggilo prima di lavorare su una milestone. Questo file contiene solo le regole che valgono sempre.

## Regola d'oro

Chi clona il repo deve avere una demo funzionante in meno di 15 minuti, senza account GCP, senza carta di credito. `git clone && ./go demo` deve arrivare fino al rollback automatico di un canary, al livello L0, senza nemmeno una API key.

Questa regola vince su qualunque altra considerazione tecnica. Se una scelta la mette a rischio, fermati e chiedi.

## Non negoziabili

- **Niente mock nascosti.** Se un pezzo è simulato a un livello, il README lo dichiara e il codice fallisce con un messaggio chiaro. Mai una finta implementazione che sembra funzionare.
- **Ogni milestone lascia il repo eseguibile.** Prima di dichiararla chiusa: `./go demo` verde su un container pulito.
- **Nessun segreto nel repo**, nemmeno negli esempi. Solo `.env.example` con valori finti.
- **Niente dati privati nel repo, con qualunque tool** (Claude Code, Antigravity, Codex, Gemini CLI o altro; `AGENTS.md` lo ripete per gli altri tool): niente email o account personali, id di progetti cloud, percorsi locali (`/Users/…`, `/home/…`), id di sessione o di richiesta, nomi di altri progetti o container della macchina, roadmap private. Gli handoff (`.claude/context/HANDOFF.md`, `.claude/context/handoffs/`) e gli appunti di sessione restano locali e gitignored; i piani privati stanno in `.crew/`. Prima di ogni push, gitleaks e una ricerca di questi dati sul range non ancora pushato.
- **ADR per ogni scelta strutturale**, in `docs/adr/`, corti, con le alternative scartate e il motivo.
- **I test si scrivono dalla spec, mai dall'implementazione.** Vale anche per i test di questo repo.
- **Verifica prima di scrivere.** Antigravity, Stitch, Gemini CLI, ADK e Cloud Deploy si muovono in fretta. Se un'API non esiste più come descritta nel piano, fermati e segnalalo invece di adattare il piano in silenzio.
- **Una soglia numerica in un ADR dichiara sempre da dove viene**: una misura, un vincolo esterno, o una decisione mia. Se non ha provenienza, non è una soglia — non entra nell'ADR.
- **Toolchain nel container.** Sull'host solo podman, git e una shell POSIX; make non è un prerequisito. L'entrypoint è ./go, mai make diretto nei criteri di accettazione.
- **Header SPDX** solo nei file di ingresso di ogni componente (entrypoint, Makefile, Containerfile, main del componente), non in ogni file.

## Contratto di output dei gate

Vale per ogni gate, presente e futuro, in `ci/`, `gates/`, `verify/`:

- Ogni gate stampa su stdout **esattamente una riga**: `GO: <nome>`, eventualmente seguito da ` — <dettaglio>` (dove ha girato, ADR 0042 §3), oppure `NO-GO: <nome> — <motivo>`. `GO` esce 0, `NO-GO` esce 1.
- Nessun colore quando stdout non è un TTY: `NO_COLOR=1` e il flag del singolo tool (`--no-color`, `-no-color`, `--color=never`).
- Il rumore dei tool e i dettagli di un `NO-GO` vanno su stderr o in un file di log, mai su stdout.
- Si implementa con `. ci/lib.sh` e le funzioni `go` / `go_with` / `nogo`. Il meta-gate `ci/contract.sh` esegue ogni gate con stdout catturato e fallisce se trova più di una riga, una sequenza ANSI o un exit code incoerente.

## Contro la sovra-ingegnerizzazione

- La cosa più piccola che fa passare il criterio di accettazione. Nient'altro.
- Nessuna astrazione nuova senza un ADR che la giustifichi.
- Nessun adapter fuori dalla tabella della sezione 4 del piano.
- Nessun settimo ruolo: sei è un vincolo di scope voluto, `ci/roles.sh` lo verifica.
- Prima di aggiungere, guarda se puoi togliere.

## Lingua

`docs/PLAN.md` è in italiano, è il documento di lavoro. **Tutto il resto del repo è in inglese**: README, codice, commenti, commit, ADR, messaggi di errore. Il repo è pubblico e i talk sono anche in inglese.

## Due livelli di modelli, da non confondere

- **Runtime**: i ruoli agente girano sul modello del profilo (`MODEL_PROVIDER` e `MODEL`: Gemini, Claude o OpenAI, dal M18), con `thinking` per ruolo in `roles/`. È configurazione di prodotto. Nessun id di modello in `core/`, `gates/`, `verify/`, `roles/`: solo in `profiles/` e `adapters/`.
- **Costruzione**: i modelli Claude che scrivono questo repo stanno nel frontmatter dei subagent in `.claude/agents/`. Non finiscono mai nel codice del prodotto.

## Come si lavora

Una milestone per sessione, con la skill `/milestone`. Plan mode prima, esecuzione dopo. Fra una milestone e l'altra: checkpoint del contesto, `/clear`, sessione nuova.

Subagent disponibili: `architect` per decisioni strutturali e ADR, `gate-writer` per `gates/`, `verify/` e i test, `doc-scribe` per README, docs e commit message.
