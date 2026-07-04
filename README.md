# Hémicycle — Assistant citoyen de l'Assemblée nationale

> Hackathon « Le parcours de la loi : vers une IA de confiance » — 3-4 juillet 2026.
> Un citoyen pose une question en langage naturel (« Comment mon député a-t-il voté ? »,
> « Où en est la loi sur… ? », « C'est quoi le 49.3 ? ») et reçoit une réponse
> **vulgarisée, neutre et sourcée**, construite en direct par des agents spécialisés
> sur les **données officielles ouvertes** de l'Assemblée nationale — avec le parcours
> de réflexion et de délégation de l'IA **visible dans le chat**.

## Pourquoi c'est une « IA de confiance »

1. **Données officielles uniquement** : scrutins, députés et dossiers législatifs sont
   chargés depuis data.assemblee-nationale.fr (Licence Ouverte Etalab) dans Postgres.
   Les agents lisent cette base en **lecture seule** ; le LLM rédige avec les chiffres
   exacts injectés dans son prompt — il n'a pas le droit d'en inventer.
2. **Sources cliquables** : chaque réponse renvoie vers la page officielle
   (assemblee-nationale.fr) du scrutin, du député ou du dossier.
3. **Transparence totale** : le plan de l'orchestrateur, les agents mobilisés, chaque
   appel d'outil (SQL local, API, serveur MCP) et son résultat sont affichés en direct
   dans le « Parcours de l'IA ». Le gate humain d'approbation du plan est réactivable
   par un simple flag.
4. **Neutralité** : règles de rédaction strictes (aucun jugement de valeur, « non-votant »
   ≠ « abstention », un texte adopté n'est pas une loi, absent = « n'a pas pris part au
   vote »).
5. **Résilience** : si le LLM tombe, des replis déterministes clôturent le tour avec les
   données brutes sourcées — jamais de spinner infini, jamais d'invention.

## Architecture (10 lignes)

```
Citoyen ──WS──▶ Frontend React (chat + widgets VoteChart / FicheDepute / TimelineLoi
                 + « Parcours de l'IA » : plan, agents, appels d'outils, sources ①②③)
                    │
             Gateway FastAPI (pont WebSocket ⇄ bus)
                    │
             NATS JetStream — personne n'appelle personne directement
                    │
             Orchestrateur FSM événementiel (routing LLM + gardes déterministes,
             vulgarisation en WRITING_REPORT, replis sans LLM, leases, DLQ, outbox)
              ├─▶ agent_votes    ─┐
              ├─▶ agent_deputes  ─┼─ Postgres 16 (open data AN, lecture seule)
              └─▶ agent_lois     ─┘  + CIVIX (échec-doux) + MCPs hackathon (échec-doux)
```

- **LLM** : NVIDIA NIM (`mistral-large-3-675b-instruct-2512`), OpenAI-compatible via
  pydantic-ai, entièrement configuré par `.env`.
- **MCP** : client Streamable HTTP intégré ; les serveurs du hackathon (Tricoteuses :
  mcp-moulineuse, mcp-parlement) se branchent en renseignant leur URL dans `.env` —
  URL absente = skip tracé honnêtement dans l'UI.
- **État** : tout vit dans Postgres/JetStream, rien en RAM — un service qui crashe
  redémarre et reprend.

## Démarrer

```bash
cp .env.example .env          # puis renseigner la clé NIM
docker compose up -d          # 9 conteneurs (infra + services)
docker compose --profile seed run --rm seed   # charge l'open data AN (~5-10 min)
open http://localhost:5173
```

## Données

| Jeu | Source | Usage |
|---|---|---|
| Scrutins 17e législature (~1,5 M positions de vote) | data.assemblee-nationale.fr | agent_votes |
| Acteurs / mandats / organes (AMO, tous députés y c. mandats clos) | data.assemblee-nationale.fr | agent_deputes |
| Dossiers législatifs (sélection curée) | data.assemblee-nationale.fr | agent_lois |

Licence des données : **Licence Ouverte Etalab** — chaque widget cite sa source.

Architecture technique héritée d'un orchestrateur multi-agents événementiel de
production (NATS JetStream, FSM, idempotence 3 couches, leases + DLQ) reconverti
pour ce hackathon. Détails : `docs/architecture.md`, `docs/data-sources.md`.
