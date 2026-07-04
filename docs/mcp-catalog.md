# Catalogue MCP — données parlementaires et légales françaises

> Recherche vérifiée (multi-agents + vérification adversariale à 3 votes) — 3 juillet 2026,
> jour 1 du hackathon. 11/12 affirmations confirmées à l'unanimité, corrections intégrées.

## ⚡ Priorité n°1 — les MCPs officiels du hackathon (identifiés ✅)

La [page ressources officielle du hackathon](https://hackathon2026.assemblee-nationale.fr/ressources)
liste **27 ressources**, dont **2 serveurs MCP** hébergés par **Tricoteuses.fr** (projet
communautaire lié à Code4code) — Licence Ouverte, gratuits pour le hackathon :

| Serveur MCP | Page | Couverture | MàJ |
|---|---|---|---|
| **mcp-moulineuse** (`an-et-co-serveur-mcp-regroupement-toutes-donnees`) | https://www.tricoteuses.fr/services/mcp-moulineuse | **AN + Sénat + Légifrance + Service-public.fr**, depuis la 14e législature | Plusieurs fois/jour + temps réel |
| **mcp-parlement** (LegiWatch, `legiwatch-serveur-mcp-parlement`) | https://www.tricoteuses.fr/services/mcp-parlement | Parlement français, 17e législature | Plusieurs fois/jour |

Le même écosystème expose aussi **2 APIs REST (OpenAPI 3.0)** et **2 bases PostgreSQL** :

| Ressource | Page | Notes |
|---|---|---|
| API unifiée « canutes-legifrance » | https://www.tricoteuses.fr/services/api-canutes-legifrance/documentation | JSON, OpenAPI 3.0, dès 14e lég. |
| API Parlement (LegiWatch) | https://www.tricoteuses.fr/services/api-parlement | JSON, OpenAPI 3.0, Express, 17e lég. |
| DB unifiée « database-canutes » | https://www.tricoteuses.fr/services/database-canutes | PostgreSQL, AN+Sénat+Légifrance+Service-public |
| DB Parlement « database-canutes-parlement » | https://www.tricoteuses.fr/services/database-canutes-parlement | PostgreSQL, 17e lég., source = dépôts Git AN/Sénat |

⚠️ **Accès vérifié (2026-07-03)** : tricoteuses.fr est protégé par **Anubis** (anti-bot,
challenge JavaScript) → les pages sont **inaccessibles en fetch programmatique**. Ouvrir les
pages **dans un navigateur** pour récupérer endpoints exacts, tools et éventuels credentials,
puis les documenter ici. À défaut, demander aux organisateurs sur place.

Hors hackathon : il n'existe **pas de MCP publié** par l'AN elle-même (rien sur
[github.com/Assemblee-nationale](https://github.com/Assemblee-nationale), rien dans le
[registre MCP](https://registry.modelcontextprotocol.io/)). Contexte : la DINUM confirme un
effort d'État autour de MCPs publics — [ia.numerique.gouv.fr](https://ia.numerique.gouv.fr/actualit%C3%A9s/les-mcp-le-standard-qui-connecte-lia-aux-donn%C3%A9es-vivantes-de-l%C3%A9tat/).

## 🟢 Branchables immédiatement (HTTP, sans clé)

### data.gouv.fr — MCP officiel de l'État
- Repo : [`datagouv/datagouv-mcp`](https://github.com/datagouv/datagouv-mcp) (officiel DINUM,
  MIT, très actif — v0.2.29 juin 2026).
- Endpoint hébergé : **`https://mcp.data.gouv.fr/mcp`** — Streamable HTTP uniquement.
- Config : `npx mcp-remote https://mcp.data.gouv.fr/mcp`
  ```json
  { "mcpServers": { "datagouv": { "command": "npx", "args": ["mcp-remote", "https://mcp.data.gouv.fr/mcp"] } } }
  ```
- Tools : `search_datasets`, `get_dataset_info`, `list_dataset_resources`,
  `get_resource_info`, `query_resource_data`, `search_organizations`, `search_dataservices`,
  `get_dataservice_info`, `get_dataservice_openapi_spec`, `get_metrics`.
- Limite : catalogue générique (découverte de datasets), pas de tooling scrutins/députés
  dédié. Statut « expérimentation » ([billet](https://www.data.gouv.fr/posts/experimentation-autour-dun-serveur-mcp-pour-datagouv)).

### JusticeLibre — droit français sans clé ⭐
- Repo : [`Dahliyaal/justicelibre`](https://github.com/Dahliyaal/justicelibre).
- Endpoint hébergé : **`https://justicelibre.org/mcp`** — sans auth.
- 29 tools : `search_legi` (1,5M articles), `search_jorf` (1,24M), `search_kali`,
  jurisprudence Cass/CE/CEDH/CJUE — bulks DILA ~6M docs.
- **Fallback Légifrance idéal si les credentials PISTE ne sont pas actifs.**

### mcp-service-public
- Repo : [`OneNicolas/mcp-service-public`](https://github.com/OneNicolas/mcp-service-public).
- Endpoint : `https://mcp-service-public.nhaultcoeur.workers.dev/mcp` (Cloudflare, sans auth).
- 38 tools : fiches Service-public.fr (~5 500), Légifrance, JORF…

## 🟡 MCPs communautaires (avec clé / minimalistes)

| MCP | Repo | Transport | Auth | Notes |
|---|---|---|---|---|
| mcp-nosdeputes-fr | [pipeworx-io/mcp-nosdeputes-fr](https://github.com/pipeworx-io/mcp-nosdeputes-fr) | HTTP (Pipeworx) | — | Seul MCP NosDéputés existant ; **minimal (6 tools)**, juin 2026 |
| droit-francais-mcp | [jmtanguy/droit-francais-mcp](https://github.com/jmtanguy/droit-francais-mcp) | stdio (Python/FastMCP) | PISTE OAuth2 (env `PISTE_CLIENT_ID`/`PISTE_CLIENT_SECRET`) | `rechercher_legifrance`, `rechercher_jurisprudence_judilibre`… |
| mcp-server-legifrance | [pylegifrance/mcp-server-legifrance](https://github.com/pylegifrance/mcp-server-legifrance) | stdio (Python) | clé lab.dassignies.law | `rechercher_dans_texte_legal`, `rechercher_code`… |
| legifrance-mcpserver | [sancelot/legifrance-mcpserver](https://github.com/sancelot/legifrance-mcpserver) | stdio + HTTP/SSE | PISTE | ~10 tools, beta |
| French-law-mcp | [Ansvar-Systems/French-law-mcp](https://github.com/Ansvar-Systems/French-law-mcp) | HTTP | — | **Archivé fév. 2026 — ne pas utiliser** |

Commercial (veille post-hackathon uniquement) : [Code4code.eu](https://code4code.eu/)
(projet Tricoteuses — AN + Sénat temps réel : débats, amendements, scrutins, textes ; 4 modes
d'accès dont MCP, API REST, réplication PostgreSQL, streaming ; 200 €HT/mois « Démarrage »,
1 200 €HT/mois « Entreprise » — contact@code4code.eu),
[MCP Factory](https://mcp-juridique.fr/) (~40 €/mois, ~19 connecteurs dont « Parlementaire »
à 6 tools : dossiers, questions écrites, débats AN+Sénat),
[OpenLegi](https://www.openlegi.fr/) (Légifrance uniquement, pas de données parlementaires).

## 🔴 Niches sans MCP (à combler nous-mêmes — notre différenciant)

**ParlAPI, CIVIX, Datan.fr, data.assemblee-nationale.fr (direct), Vie Publique** : aucun MCP
dédié. C'est là que notre MCP « Parlement » maison apporte de la valeur.

### Notre MCP « Parlement » (à construire)
- Wrapper **CIVIX** (JSON sans auth) pour la 17e législature, avec deux caveats vérifiés
  (voir `docs/data-sources.md`) :
  - ⚠️ **NosDéputés.fr est vide pour la 17e législature** — ne l'utiliser que pour
    l'historique (≤ 16e) et la synthèse de présence/activité.
  - ⚠️ **CIVIX `/scrutins` timeout systématiquement** — timeout client agressif + fallback
    sur les **CSV quotidiens CIVIX** (`civix-votes-l17.csv`…) ou les ZIP officiels AN
    (source de vérité).
- Tools suggérés (calqués sur le blueprint European-Parliament-MCP-Server) :
  `search_depute`, `get_votes_depute`, `get_scrutin`, `search_amendements`,
  `get_dossier_legislatif`.
- Transport **HTTP streamable** (pattern officiel datagouv-mcp) → s'insère proprement dans
  le Docker Compose multi-agents.

## 📐 Références architecturales (parlements étrangers)

- [Hack23/European-Parliament-MCP-Server](https://github.com/Hack23/European-Parliament-MCP-Server)
  — npm `european-parliament-mcp-server`, **63 tools** (votes, coalitions, feeds temps réel),
  Apache-2.0, très actif. **Meilleur blueprint** pour nos tools scrutins/analytics.
- [i-dot-ai/parliament-mcp](https://github.com/i-dot-ai/parliament-mcp) — incubateur du
  gouvernement UK (Hansard + Members).
- [sh-patterson/legiscan-mcp](https://github.com/sh-patterson/legiscan-mcp) — pattern
  roll-call/votes par lot.
- [amurshak/congressMCP](https://github.com/amurshak/congressMCP) — pattern amendements
  (91+ opérations).

## 🌐 APIs wrappables (détail)

### NosDéputés.fr (Regards Citoyens) — le plus simple
- Pattern : ajouter `/json`, `/xml` ou `/csv` à quasi toute URL. **Sans auth.**
- Endpoints clés :
  - `https://www.nosdeputes.fr/deputes/enmandat/json`
  - `https://www.nosdeputes.fr/{slug}/json` · `/{slug}/votes/json`
  - `https://www.nosdeputes.fr/{legislature}/scrutins/json`
  - `/{legislature}/amendements/{n°texte}/json` · `/{legislature}/dossiers/{tri}/json`
  - Recherche : `/recherche/{termes}?format=json`
- Licence ODbL + CC-BY-SA, **attribution requise**.
  [Doc API](https://github.com/regardscitoyens/nosdeputes.fr/blob/master/doc/api.md) ·
  client Python [cpc-api](https://github.com/regardscitoyens/cpc-api).

### CIVIX
- Base : `https://www.civix.fr/api` — lecture seule, **sans auth**, JSON, spec
  Swagger/OpenAPI. Députés, scrutins, votes individuels, dossiers, statistiques.
- Licence Ouverte 2.0. [Fiche data.gouv.fr](https://www.data.gouv.fr/dataservices/api-publique-civix).

### data.assemblee-nationale.fr — source officielle
- **Dépôt de fichiers bulk** (XML/JSON zippés), pas d'API REST live → un wrapper direct
  exigerait téléchargement + indexation (SQLite/Postgres). **À éviter en hackathon** :
  NosDéputés/CIVIX ont déjà digéré ces données.
- Datasets : scrutins, amendements, dossiers législatifs, débats, agenda, référentiel AMO
  (Acteurs/Mandats/Organes). Licence Ouverte Etalab. [Portail](https://data.assemblee-nationale.fr/).

### Légifrance via PISTE (DILA)
- Prod : `https://api.piste.gouv.fr/dila/legifrance/lf-engine-app` · sandbox :
  `https://sandbox-api.piste.gouv.fr`.
- OAuth2 client-credentials : POST `https://oauth.piste.gouv.fr/api/oauth/token` → Bearer
  (cacher selon `expires_in`). Endpoints POST/JSON : `/consult/getArticle`, `/consult/*`,
  `/search`. Quotas par token.
- Inscription gratuite [piste.gouv.fr/registration](https://piste.gouv.fr/registration) —
  ⚠️ **activation en jours** : utiliser JusticeLibre en attendant.
- [Doc officielle](https://www.legifrance.gouv.fr/contenu/pied-de-page/open-data-et-api) ·
  client de référence [SocialGouv/dila-api-client](https://github.com/SocialGouv/dila-api-client).

### data.gouv.fr (API REST)
- `https://www.data.gouv.fr/api/1` — `GET /datasets/?q=...`, sans auth en lecture.
  [OpenAPI](https://www.data.gouv.fr/api/1/swagger.json).
- **Vie Publique : pas d'API propre** — passer par ses datasets data.gouv.fr (ex.
  [100 000+ discours publics](https://www.data.gouv.fr/datasets/discours-publics-metadonnees-de-vie-publique-fr)).

## ✅ Plan de branchement recommandé

1. **MCP du hackathon AN** (dès l'endpoint récupéré) — AN + Sénat + Légifrance +
   Service-public.fr en un seul serveur.
2. **`https://mcp.data.gouv.fr/mcp`** — officiel, découverte de datasets.
3. **`https://justicelibre.org/mcp`** — textes de loi sans clé.
4. **Notre MCP « Parlement »** (CIVIX + NosDéputés) — le cœur différenciant
   scrutins/députés/amendements.

À éviter/différer : wrapper bulk data.assemblee-nationale.fr, PISTE direct (délai
d'activation), solutions payantes.
