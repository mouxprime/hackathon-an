# Sources de données — lois, députés, votes

> Recherche vérifiée sur données réelles (2026-07-03) : URLs d'archives AN testées HTTP 200,
> schéma des scrutins confirmé sur le fichier brut `VTANR5L17V1.json`, endpoints CIVIX et
> NosDéputés testés en direct. Les points vérifiés sont marqués ✅.

## 🗺️ Mapping question citoyenne → source

| Question | Source primaire | Fallback |
|---|---|---|
| « Comment mon député a-t-il voté sur X ? » | AN `Scrutins.json.zip` (17e) via `acteurRef` | CIVIX `/scrutins/{uid}/votes` |
| « Qui est mon député ? » | AN AMO10/AMO50 | CIVIX `/deputes` |
| « Quel est le parcours d'un projet de loi ? » | Vie-publique fiche 19521 (pédagogie) + AN Dossiers_Legislatifs (état réel) | DILA DOLE |
| « Que dit la loi / le Code ? » | Légifrance PISTE `/consult` + `/search` | dumps LEGI / JusticeLibre MCP |
| « Mon député est-il actif/loyal ? » | Datan (datasets data.gouv) | NosDéputés synthèse (≤ 16e lég.) |
| « Qu'a-t-il dit en séance ? » | AN syceron XML | NosDéputés recherche (≤ 16e) |

## ⚠️ Pièges vérifiés

- **NosDéputés.fr ne couvre PAS la 17e législature** ✅ : `deputes/enmandat/json` renvoie
  `{"deputes":[]}`, `/17/scrutins/json` redirige à tort vers `2017-2022.nosdeputes.fr`.
  Utilisable seulement pour l'historique (13e–16e via sous-domaines `2007-2012.`,
  `2012-2017.`, `2017-2022.nosdeputes.fr`).
- **CIVIX `/scrutins` instable** ✅ : timeouts systématiques constatés → timeout client
  agressif + fallback sur les ZIP officiels AN (source de vérité).
- **Comptes rendus AN : XML uniquement** (syceron), pas de JSON.
- **Licences** : ODbL share-alike + attribution sur NosDéputés ; Licence Ouverte Etalab
  partout ailleurs (AN, CIVIX, DILA, data.gouv).
- **Base Légifrance** : le segment est `lf-engine-app` (pas `legifrance`) ; activation PISTE
  en plusieurs jours.
- **ParlAPI** : doc quasi absente, GitLab inaccessible — **à éviter**.

---

## 0. Ressources officielles du hackathon (27 ressources) ✅

Source : https://hackathon2026.assemblee-nationale.fr/ressources — c'est le **périmètre
béni par les organisateurs** ; démontrer l'usage de ces ressources est un critère jury.

### Assemblée nationale (11) — data.assemblee-nationale.fr
Dossiers législatifs (lég. courante), amendements (17e), comptes rendus séance publique,
**votes des députés** (⚠️ la page précise « vote de chaque député pour les scrutins
*solennels* »), députés en exercice (CSV/XML, depuis 8 juillet 2024), historique députés
(depuis 11e lég./1997), députés+sénateurs+ministres par législature (depuis 15e),
agenda des réunions, questions au gouvernement / écrites / orales (depuis 16e lég.).
Formats XML/JSON (CSV pour certains), Licence Ouverte. URLs détaillées en §1.

### Premier ministre / DILA (3) — via data.gouv.fr
- **LEGI** — codes, lois et règlements consolidés (XML, depuis 1945) :
  https://www.data.gouv.fr/datasets/legi-codes-lois-et-reglements-consolides
- **DOLE** — dossiers législatifs Légifrance (XML, depuis 12e lég./2002) :
  https://www.data.gouv.fr/datasets/dole-les-dossiers-legislatifs
- **JORF** — Journal officiel « Lois et décrets » (XML, depuis 1990) :
  https://www.data.gouv.fr/datasets/jorf-les-donnees-de-l-edition-lois-et-decrets-du-journal-officiel

### Sénat (6) — data.senat.fr ⚠️ format PostgreSQL (dumps)
- Dispositifs des textes (XML, norme **Akoma Ntoso**, depuis 1977) :
  https://data.senat.fr/les-donnees-dispositifs-des-textes-monalisa/
- Dossiers législatifs (depuis 1977) : https://data.senat.fr/dosleg/
- Amendements (**temps réel**, séance depuis 2001, commission depuis 2010) :
  https://data.senat.fr/ameli/
- Sénateurs (mandats, commissions, organes) : https://data.senat.fr/les-senateurs/
- Questions au gouvernement (depuis 1978) : https://data.senat.fr/la-base-questions/
- Comptes rendus séance publique (depuis janv. 2023) : https://data.senat.fr/la-base-comptes-rendus/

⚠️ Les données Sénat sont des **bases PostgreSQL** (pas des API REST) → coût d'intégration
élevé en direct ; passer par les MCPs/APIs Tricoteuses qui les unifient (voir
`docs/mcp-catalog.md` §Priorité n°1).

### Ressources « retravaillées » (7)
- **OpenFisca France — paramètres socio-fiscaux** (YAML, depuis 1945, taux et barèmes du
  simulateur) : https://github.com/openfisca/openfisca-france/tree/master/openfisca_france/parameters
  → utile pour vulgariser l'impact chiffré d'une loi fiscale/sociale.
- **Tricoteuses / LegiWatch** : 2 serveurs **MCP** + 2 **APIs OpenAPI 3.0** + 2 bases
  PostgreSQL unifiant AN + Sénat + Légifrance + Service-public.fr — **la voie d'accès
  recommandée** ; détails et caveat d'accès (protection Anubis, ouvrir dans un navigateur)
  dans `docs/mcp-catalog.md`.

---

## 1. data.assemblee-nationale.fr — la source de vérité

- **Portail** : https://data.assemblee-nationale.fr/ · **Licence** : [Licence Ouverte Etalab](https://data.assemblee-nationale.fr/licence-ouverte-open-licence).
- **Pas d'API REST** : fichiers bulk (ZIP XML/JSON, CSV pour les jeux « divisés »)
  + fichiers unitaires temps réel.
- **Schéma d'URL stable** ✅ :
  `https://data.assemblee-nationale.fr/static/openData/repository/{législature}/{domaine}/{jeu}/{Fichier}.{xml|json|csv}.zip`

| Jeu | URL (préfixe `…/repository/`) | Contenu |
|---|---|---|
| **Députés actifs** (AMO10) ✅ | `17/amo/deputes_actifs_mandats_actifs_organes/AMO10_deputes_actifs_mandats_actifs_organes.json.zip` | députés + mandats + organes |
| Députés divisé (AMO40) | `17/amo/deputes_actifs_mandats_actifs_organes_divises/AMO40_….{csv,json,xml}.zip` | 1 fichier par entité |
| Tous acteurs (AMO50) | `17/amo/acteurs_mandats_organes_divises/AMO50_acteurs_mandats_organes_divises.{csv,json,xml}.zip` | acteurs complets |
| Historique depuis 1997 (AMO30) | `AMO30_tous_acteurs_tous_mandats_tous_organes_historique` | toutes législatures |
| **Scrutins** ✅ | `17/loi/scrutins/Scrutins.json.zip` (aussi `16/…`) | 1 fichier JSON par scrutin |
| **Amendements** | `17/loi/amendements_div_legis/Amendements.{xml,json}.zip` | auteur, exposé, sort |
| **Dossiers législatifs** ✅ | `17/loi/dossiers_legislatifs/Dossiers_Legislatifs.json.zip` | parcours des textes |
| Questions écrites ✅ | `17/questions/questions_ecrites/Questions_ecrites.json.zip` | |
| Questions au gouvernement | `16/questions/questions_gouvernement/Questions_gouvernement.{xml,json}.zip` | QAG |
| Questions orales sans débat ✅ | `17/questions/questions_orales_sans_debat/Questions_orales_sans_debat.json.zip` | |
| **Réunions/Agenda** ✅ | `17/vp/reunions/Agenda.json.zip` + CSV `reunions_init_*.csv` | ordre du jour |
| **Comptes rendus (débats)** | `17/VP/syceronbrut/syseron.xml.zip` | verbatim, XML uniquement |

- **Fréquence de MàJ** ([FAQ](https://data.assemblee-nationale.fr/foire-aux-questions)) :
  archives ~1 jour ; listes d'amendements ~1 h ; amendements unitaires ~1 min.
- **Temps réel unitaire** : `http://www.assemblee-nationale.fr/dyn/opendata/{ID}.{xml,json,pdf}`
  et liste quotidienne `…/dyn/opendata/list-publication/publication_{AAAA-MM-JJ}.csv`.

### Structure d'un scrutin — vérifiée sur fichier réel ✅

(`VTANR5L17V1.json`, motion de censure du 08/10/2024)

```
scrutin
├── uid: "VTANR5L17V1"        numero, legislature, dateScrutin
├── organeRef (PO…), sessionRef, seanceRef
├── typeVote {codeTypeVote, libelleTypeVote, typeMajorite}
├── sort {code: adopté/rejeté, libelle}
├── titre, demandeur
├── objet {libelle, dossierLegislatif ← lien vers le dossier législatif}
├── syntheseVote {nombreVotants, suffragesExprimes,
│                 decompte {pour, contre, abstentions, nonVotants}}
└── ventilationVotes.organe.groupes.groupe[]
    ├── organeRef (PO… = groupe politique), nombreMembresGroupe
    └── vote {positionMajoritaire, decompteVoix,
              decompteNominatif {pours|contres|abstentions|nonVotants
                → votant[] {acteurRef: "PA794906", mandatRef: "PM843284",
                            parDelegation, numPlace}}}
```

**Chaînage des identifiants** : `PA…` = acteur (député) · `PO…` = organe/groupe ·
`PM…` = mandat · `VTANR5L{lég}V{n}` = scrutin.
Référentiel : http://www.assemblee-nationale.fr/opendata/Index_pub.html
(schémas : `…/Schemas_Entites/Schemas_EntitesTOC.html`).

## 2. CIVIX — votes en JSON, sans clé (17e législature)

- **Base** : `https://www.civix.fr/api/v1` · [OpenAPI](https://www.civix.fr/api/openapi?v=20260328)
  · sans auth · Licence Ouverte 2.0 · [fiche data.gouv](https://www.data.gouv.fr/dataservices/api-publique-civix).
- Couvre la 17e législature (~602 députés, 12 groupes).

| Endpoint (GET) | Rôle |
|---|---|
| `/api/v1/about`, `/index`, `/search` | méta / recherche |
| `/api/v1/deputes?legislature=17&groupe=&page_size=` ✅ | députés |
| `/api/v1/deputes/{uid}` | fiche député |
| `/api/v1/scrutins?search=&include_votes=&legislature=17` ⚠️ | **timeouts systématiques** ✅ |
| `/api/v1/scrutins/{uid}/votes`, `/{uid}/statements` | votes nominatifs, positions groupes |
| `/api/v1/groupes`, `/groupes/{abbr}` ✅ | groupes |

- Réponses enveloppées : `{publisher, license, answer, confidence, meta.pagination{…}}`.
- **Alternative CSV quotidienne** ([dataset](https://www.data.gouv.fr/datasets/donnees-parlementaires-francaises-votes-deputes-scrutins-civix)) :
  `civix-votes-l17.csv`, `civix-scrutins.csv`, `civix-deputes-actifs-l17.csv`,
  `civix-groupes-l17.csv`, `civix-groupes-stats-l17.csv` — URLs stables type
  `https://www.data.gouv.fr/api/1/datasets/r/bb1757e3-ccfd-43a8-b7d3-bb5624ff97a4`.

## 3. NosDéputés.fr (Regards Citoyens) — historique ≤ 16e lég.

- **Base** : `https://www.nosdeputes.fr` (Sénat : nossenateurs.fr). Ajouter `/json`, `/xml`
  ou `/csv` à toute URL. [Doc API](https://github.com/regardscitoyens/nosdeputes.fr/blob/master/doc/api.md).
- **Licence** : CC-BY-SA (contenus) + **ODbL** (données — share-alike, attribution).
- ⚠️ **Pas de 17e législature** ✅ — historique via `2007-2012.`, `2012-2017.`,
  `2017-2022.nosdeputes.fr`.
- Endpoints clés :
  - Députés : `/deputes/json` ✅ · fiche `/{slug}/json` · photo `/depute/photo/{slug}/60`
  - Synthèse activité/présence : `/synthese/data/json` · mensuelle `/synthese/{AAAAMM}/json`
    (unique en son genre)
  - Scrutins : `/16/scrutins/json` · détail `/16/scrutin/{n}/json` · par député `/{slug}/votes/json`
  - Organismes : `/organismes/groupe/json`, `/groupe/{sigle}/json` (`?includePast=true`)
  - Débats : `/16/seances/{texte}/json` (`?commission=1`, `?hemicycle=1`)
  - Dossiers : `/16/dossiers/date/json` · `/16/dossier/{id}/json`
  - Amendements : `/14/amendements/{texte}/json` · cosignatures `…/links/json`
  - **Recherche plein-texte** : `/recherche/{requête}?format=json&object_name={Parlementaire|Intervention|Amendement|QuestionEcrite|Texteloi|Section|Organisme}`
    + `&page=`, `&count=` (max 500), `&date=`, `&tag=parlementaire=Prenom+Nom`,
    facettes `&parlfacet=1`, `&timefacet=1`
  - Dumps SQL : https://www.regardscitoyens.org/telechargement/donnees/

## 4. Légifrance via PISTE (DILA)

- **Inscription** : compte https://piste.gouv.fr → CGU Légifrance → créer une application →
  souscrire l'API → `client_id`/`client_secret`. Gratuit. Sandbox auto ; production sur
  demande (quotas dans le dashboard). [FAQ officielle](https://www.legifrance.gouv.fr/contenu/pied-de-page/foire-aux-questions-api).
- **OAuth 2.0** `client_credentials` (jeton 3600 s) :
  - Prod : `POST https://oauth.piste.gouv.fr/api/oauth/token` (`grant_type=client_credentials&scope=openid`)
  - Sandbox : `https://sandbox-oauth.piste.gouv.fr/api/oauth/token`
- **Base API** (⚠️ segment `lf-engine-app`) :
  - Prod : `https://api.piste.gouv.fr/dila/legifrance/lf-engine-app`
  - Sandbox : `https://sandbox-api.piste.gouv.fr/dila/legifrance/lf-engine-app`
- **Endpoints** (POST + `Authorization: Bearer`) : `/search` (fonds `LODA_DATE`, `CODE_DATE`,
  `JORF`, `JURI`, `KALI`, `ALL`…), `/consult/getArticle` (`{"id":"LEGIARTI000006307920"}`),
  `/consult/legiPart` (texte consolidé complet), `/consult/jorf`, `/consult/lastNJo`,
  `/list/ping` (GET, test).
- **Bulk sans quota** : dumps XML DILA `https://echanges.dila.gouv.fr/OPENDATA/` — fonds
  **LEGI** (codes/lois consolidés), **JORF**, **KALI**, **DOLE** (dossiers législatifs).
  Fiches : [LEGI](https://www.data.gouv.fr/datasets/legi-codes-lois-et-reglements-consolides),
  [DOLE](https://www.data.gouv.fr/datasets/dole-les-dossiers-legislatifs).

## 5. Couche vulgarisation

- **data.gouv.fr** — API `https://www.data.gouv.fr/api/1/` (GET sans clé,
  [swagger](https://www.data.gouv.fr/api/1/swagger.json) ✅). Recherche `GET /datasets/?q=…`.
  MCP officiel : voir `docs/mcp-catalog.md`.
- **Vie-publique.fr** (DILA) — vulgarisation officielle, **pas d'API** (éditorial,
  réutilisable avec citation source + date) :
  - [Étapes du vote d'une loi — fiche 19521](https://www.vie-publique.fr/fiches/19521-quelles-sont-les-etapes-du-vote-dune-loi)
  - [Élaboration d'un projet de loi](https://www.vie-publique.fr/fiches/19476-quelles-sont-les-etapes-delaboration-dun-projet-de-loi)
  - [Panorama des lois](https://www.vie-publique.fr/panorama-des-lois) — suivi de chaque loi
    avec schéma du parcours
  - +1 100 [fiches institutions](https://www.vie-publique.fr/fiches) → **base RAG idéale**
    pour « comment une loi est-elle votée ? »
- **Datan.fr** — analyse des votes (proximité entre députés, loyauté au groupe, cohésion,
  [simulateur de coalition](https://datan.fr/outils/coalition-simulateur)). Pas d'API ;
  [4 datasets data.gouv](https://www.data.gouv.fr/organizations/datan/datasets) (MàJ hebdo),
  backups DB `https://datan.fr/assets/dataset_backup/general/`,
  code : https://github.com/datanfr/datan.
