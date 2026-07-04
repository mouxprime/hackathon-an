"""Seed Hémicycle — ingestion des données ouvertes de l'Assemblée nationale (17e lég.).

Remplace la fake data du mockup d'origine par les données officielles publiées
sous Licence Ouverte sur data.assemblee-nationale.fr :

- `organes`  : groupes politiques (et organes essentiels) — AMO, avec `couleurAssociee` ;
- `deputes`  : tous les acteurs ayant eu un mandat de député en 17e législature,
  mandats clos inclus (`actif=False` = ancien député) — fusion AMO50 (tous acteurs,
  archive figée au 11/07/2024) + AMO10 (députés actifs, mise à jour quotidienne) ;
- `scrutins` + `votes` : les scrutins publics et leurs votes nominatifs dépliés
  (~8 000 scrutins, ~1,3 M lignes de votes, INSERT par lots de 5 000) ;
- `dossiers` : sélection curée (~10) de dossiers législatifs, arbre `actesLegislatifs`
  conservé en JSONB pour la timeline de l'agent-lois.

Le seed conserve les insertions nécessaires à l'orchestrateur, reconverties citoyennes :
- `agent_types` : 3 manifestes (agent_votes / agent_deputes / agent_lois) ;
- `tools`       : outils natifs + serveurs MCP (URLs lues des env HEMI_MCP_*_URL) ;
- `workflows`   : chaînes d'agents pré-définies ;
- channels      : salons « Suivis » citoyens (cf. hemicycle_shared.channels) ;
- Elasticsearch : index `hemicycle-agents` (upsert) + `mi-search-corpus` recréé VIDE
  (plus de corpus synthétique ; l'index existe pour la recherche fédérée).

⚠️ Piège open data AN : les listes à 1 élément sont sérialisées comme OBJET, pas comme
liste → `_as_list` est utilisé PARTOUT.

Re-exécutable : téléchargements mis en cache dans HEMI_DATA_DIR (défaut ./data) ;
TRUNCATE + reload des tables AN ; upsert idempotent des channels (jamais tronqués).
Sortie non-zéro si les témoins de fin de seed échouent.
"""

import asyncio
import json
import logging
import os
import unicodedata
import uuid
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from elasticsearch import AsyncElasticsearch
from elasticsearch.helpers import async_bulk
from sqlalchemy import delete as sa_delete, func, insert as sa_insert, select, text

from hemicycle_shared.channels import CHANNEL_DEFS, ensure_channels
from hemicycle_shared.config import settings
from hemicycle_shared.db import (
    AgentType,
    Channel,
    ChannelMessage,
    Circonscription,
    Depute,
    Dossier,
    Organe,
    Scrutin,
    Tool,
    Vote,
    Workflow,
    get_sessionmaker,
    init_db,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [seed] %(levelname)s %(message)s"
)
log = logging.getLogger("seed")

CORPUS_INDEX = "mi-search-corpus"
AGENTS_INDEX = "hemicycle-agents"

# --- Cache local des archives open data (re-run sans re-téléchargement) ---
DATA_DIR = Path(os.environ.get("HEMI_DATA_DIR", "./data"))

# URLs vérifiées (HEAD 200 le 2026-07-04) — cf. docs/data-sources.md §1.
# ⚠️ AMO50 « acteurs_mandats_organes_divises » 17e lég. est une archive FIGÉE au
# 11/07/2024 (début de législature) : elle contient bien TOUS les élus de juillet 2024
# (donc ceux partis depuis), mais pas les mandats ouverts/clos après cette date.
# On la FUSIONNE avec AMO10 (députés actifs, MàJ quotidienne) : AMO10 = vérité des
# actifs, groupes et organes frais ; AMO50 = rattrapage des anciens députés.
URL_AMO50 = (
    "https://data.assemblee-nationale.fr/static/openData/repository/17/amo/"
    "acteurs_mandats_organes_divises/AMO50_acteurs_mandats_organes_divises.json.zip"
)
URL_AMO10 = (
    "https://data.assemblee-nationale.fr/static/openData/repository/17/amo/"
    "deputes_actifs_mandats_actifs_organes/AMO10_deputes_actifs_mandats_actifs_organes.json.zip"
)
URL_SCRUTINS = (
    "https://data.assemblee-nationale.fr/static/openData/repository/17/loi/"
    "scrutins/Scrutins.json.zip"
)
URL_DOSSIERS = (
    "https://data.assemblee-nationale.fr/static/openData/repository/17/loi/"
    "dossiers_legislatifs/Dossiers_Legislatifs.json.zip"
)
# Contours géographiques des circonscriptions législatives (data.gouv.fr, geojson
# simplifié p10, 5,4 MB, 559 features — vérifié HTTP 200 le 2026-07-04). Sert au
# « qui est mon député ? » par adresse (géocodage BAN → point-in-polygon worker).
URL_CIRCOS = (
    "https://static.data.gouv.fr/resources/contours-geographiques-des-circonscriptions-"
    "legislatives/20240613-191520/circonscriptions-legislatives-p10.geojson"
)

# Pages publiques officielles (patterns vérifiés HTTP 200 le 2026-07-04).
URL_AN_SCRUTIN = "https://www.assemblee-nationale.fr/dyn/17/scrutins/{numero}"
URL_AN_DEPUTE = "https://www.assemblee-nationale.fr/dyn/deputes/{uid}"
URL_AN_PHOTO = "https://www2.assemblee-nationale.fr/static/tribun/17/photos/{num}.jpg"
URL_AN_DOSSIER = "https://www.assemblee-nationale.fr/dyn/17/dossiers/{chemin}"
URL_DATA_SCRUTINS = "https://data.assemblee-nationale.fr/travaux-parlementaires/votes"

VOTES_BATCH = 5000

# Types d'organes conservés : groupes politiques + l'essentiel institutionnel.
ORGANE_TYPES = {"GP", "PARPOL", "ASSEMBLEE", "SENAT", "GVT", "MINISTERE", "COMPER", "CMP"}

# decompteNominatif → valeur de `votes.position` (⚠️ non-votant ≠ abstention).
POSITIONS = {
    "pours": "pour",
    "contres": "contre",
    "abstentions": "abstention",
    "nonVotants": "nonVotant",
}

# Thèmes médiatiques 17e lég. pour la curation des dossiers (matching sans accents).
DOSSIER_THEMES = [
    "pouvoir d'achat",
    "immigration",
    "fin de vie",
    "budget",
    "loi de finances",  # le PLF ne contient pas le mot « budget » dans son titre
    "sécurité",
    "agriculture",
    "logement",
    "énergie",
    "retraites",
    "narcotrafic",
]
DOSSIERS_TARGET = 10   # taille visée de la sélection curée
DOSSIERS_RICH_TOP = 3  # dossiers récents « riches en actes » toujours inclus
DOSSIERS_RICH_MIN_ACTES = 8

# --- Serveurs MCP du hackathon (Tricoteuses/LegiWatch + JusticeLibre) ---
# URLs lues de l'environnement — vides = OK, le worker saute le serveur (graceful skip).
MCP_SERVERS = {
    "moulineuse": os.environ.get("HEMI_MCP_MOULINEUSE_URL", ""),
    "parlement": os.environ.get("HEMI_MCP_PARLEMENT_URL", ""),
    "justicelibre": os.environ.get("HEMI_MCP_JUSTICELIBRE_URL", ""),
}


def _mcp_refs(*names: str) -> list[dict]:
    """Références MCP (format MCPServerRef) pour `agent_types.mcp_servers`.

    Seuls les serveurs CONFIGURÉS (URL non vide dans l'env) sont déclarés : un
    serveur sans URL afficherait des boutons de test cassés dans l'UI Agents.
    Renseigner HEMI_MCP_*_URL dans .env puis re-seeder pour les faire apparaître.
    """
    return [{"name": n, "url": MCP_SERVERS[n], "tools": []} for n in names if MCP_SERVERS[n]]


# Agent Card A2A de chaque worker interne : le dispatch reste NATS (transport par
# défaut), mais la gateway utilise cette carte pour les BOUTONS DE TEST des tools
# (les workers montent un serveur A2A sur :8600, joignable sur le réseau compose).
A2A_CARDS = {
    "agent_votes": "http://subagent-votes:8600/.well-known/agent-card.json",
    "agent_deputes": "http://subagent-deputes:8600/.well-known/agent-card.json",
    "agent_lois": "http://subagent-lois:8600/.well-known/agent-card.json",
}


def _now() -> datetime:
    """Horodatage UTC courant."""
    return datetime.now(timezone.utc)


def _as_list(x) -> list:
    """Normalise le piège majeur du JSON open data AN : liste à 1 élément = OBJET.

    None → [], dict (singleton sérialisé comme objet) → [dict], list → list.
    """
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


def _uid(x) -> str:
    """Extrait un identifiant AN : soit une chaîne nue, soit {"#text": "PA…", "@…": …}."""
    if isinstance(x, dict):
        return x.get("#text") or ""
    return x or ""


def _norm(s: str | None) -> str:
    """Normalise pour la recherche floue : minuscules, sans accents, espaces réduits."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def _int(x, default: int = 0) -> int:
    """Entier robuste (les décomptes AN sont des chaînes)."""
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def _bool(x) -> bool:
    """Booléen robuste ("true"/"false" chaînes dans l'open data AN)."""
    if isinstance(x, bool):
        return x
    return str(x).strip().lower() == "true"


# --- Manifestes d'agents (Agent Registry) — 3 agents citoyens, when_to_use disjoints ---
MANIFESTS = [
    AgentType(
        id="agent_votes",
        discipline="VOTES",
        description="Scrutins publics de l'Assemblée nationale (17e législature) : "
        "résultats officiels, votes nominatifs des députés, positions des groupes "
        "politiques — données data.assemblee-nationale.fr.",
        when_to_use="Quand la question porte sur un vote ou un scrutin : comment a voté "
        "un député ou un groupe sur un texte, résultat d'un scrutin (adopté/rejeté, "
        "pour/contre/abstentions/non-votants), liste des scrutins liés à une loi.",
        input_schema={"text": "str"},
        output_schema={"scrutins": "list", "votes": "list", "summary": "str"},
        tools=["scrutin_search", "scrutin_detail", "votes_depute"],
        mcp_servers=_mcp_refs("moulineuse", "parlement"),
        cost_hint="medium",
        latency_p95_s=8.0,
        hard_timeout_s=120,
    ),
    AgentType(
        id="agent_deputes",
        discipline="DEPUTES",
        description="Députés et groupes politiques de la 17e législature : fiches "
        "d'identité, circonscriptions, appartenance aux groupes, anciens députés — "
        "données AMO data.assemblee-nationale.fr.",
        when_to_use="Quand la question porte sur une personne ou un groupe : qui est un "
        "député, sa fiche (groupe, circonscription, département, photo), qui me "
        "représente, composition et effectifs d'un groupe, statistiques de participation.",
        input_schema={"text": "str"},
        output_schema={"deputes": "list", "summary": "str"},
        tools=["depute_search", "depute_fiche", "groupe_stats"],
        mcp_servers=_mcp_refs("moulineuse", "parlement"),
        cost_hint="medium",
        latency_p95_s=8.0,
        hard_timeout_s=120,
    ),
    AgentType(
        id="agent_lois",
        discipline="LOIS",
        description="Dossiers législatifs de la 17e législature : parcours d'un texte "
        "(dépôt, commissions, séances, navette, promulgation) et contenu des lois — "
        "données data.assemblee-nationale.fr et Légifrance.",
        when_to_use="Quand la question porte sur un texte de loi : où en est un projet ou "
        "une proposition de loi, son parcours et la navette parlementaire (timeline des "
        "étapes), le contenu ou l'objet d'une loi, la procédure législative.",
        input_schema={"text": "str"},
        output_schema={"dossiers": "list", "timeline": "list", "summary": "str"},
        tools=["dossier_search", "dossier_timeline"],
        mcp_servers=_mcp_refs("moulineuse", "parlement", "justicelibre"),
        cost_hint="medium",
        latency_p95_s=10.0,
        hard_timeout_s=120,
    ),
]
# Agent Card de test (dispatch NATS inchangé — cf. A2A_CARDS).
for _m in MANIFESTS:
    _m.card_url = A2A_CARDS.get(_m.id)


# --- Workflows pré-définis (chaînes d'agents proposées dans le popup Workflow) ---
def _chain_graph(agents: list[str]) -> dict:
    """Construit un graphe React Flow en chaîne linéaire à partir d'une liste d'agents."""
    nodes = [
        {
            "id": f"n{i}",
            "type": "agent",
            "position": {"x": 60 + i * 220, "y": 120},
            "data": {"agent_type": a, "instruction": ""},
        }
        for i, a in enumerate(agents)
    ]
    edges = [
        {"id": f"e{i}", "source": f"n{i}", "target": f"n{i + 1}"}
        for i in range(len(agents) - 1)
    ]
    return {"nodes": nodes, "edges": edges}


SEED_WORKFLOWS = [
    {
        "name": "Mon député et ses votes",
        "description": "Identifier un député puis restituer ses votes en hémicycle.",
        "agents": ["agent_deputes", "agent_votes"],
    },
    {
        "name": "Une loi : parcours puis scrutins",
        "description": "Suivre le parcours d'un texte puis les scrutins associés.",
        "agents": ["agent_lois", "agent_votes"],
    },
]


# --- Tools natifs + MCP, par agent (alimente la table `tools` éditable depuis l'UI) ---
def _mcp_tool(name: str) -> list[dict]:
    """Entrée `tools` pour un serveur MCP du hackathon — UNIQUEMENT s'il est configuré
    (URL non vide dans l'env) : une entrée sans URL n'est pas testable depuis l'UI."""
    if not MCP_SERVERS[name]:
        return []
    return [{
        "name": f"mcp_{name}",
        "description": f"Serveur MCP « {name} » du hackathon (URL: {MCP_SERVERS[name]}).",
        "input_schema": {"query": "str"},
        "source": "mcp",
    }]


TOOL_CATALOG: dict[str, list[dict]] = {
    "agent_votes": [
        {
            "name": "scrutin_search",
            "description": "Recherche floue de scrutins par titre, numéro ou date "
            "(table `scrutins`, index trgm sur titre_normalise).",
            "input_schema": {"query": "str", "limit": "int?"},
            "source": "native",
        },
        {
            "name": "scrutin_detail",
            "description": "Détail d'un scrutin : décomptes officiels et ventilation "
            "nominative par groupe (tables `scrutins` + `votes`).",
            "input_schema": {"scrutin_uid": "str"},
            "source": "native",
        },
        {
            "name": "votes_depute",
            "description": "Positions de vote d'un député (pour/contre/abstention/"
            "non-votant) sur les scrutins de la législature.",
            "input_schema": {"acteur_ref": "str", "limit": "int?"},
            "source": "native",
        },
        *_mcp_tool("moulineuse"),
        *_mcp_tool("parlement"),
    ],
    "agent_deputes": [
        {
            "name": "depute_search",
            "description": "Recherche floue d'un député par nom ou circonscription "
            "(table `deputes`, index trgm sur nom_normalise).",
            "input_schema": {"query": "str", "limit": "int?"},
            "source": "native",
        },
        {
            "name": "depute_fiche",
            "description": "Fiche d'un député : groupe, circonscription, statut "
            "(actif / ancien député), liens officiels AN.",
            "input_schema": {"uid": "str"},
            "source": "native",
        },
        {
            "name": "circo_par_adresse",
            "description": "« Qui est mon député ? » par adresse : géocodage via "
            "l'API Adresse officielle (BAN) puis point-in-polygon sur les contours "
            "officiels des circonscriptions (data.gouv.fr, table `circonscriptions`).",
            "input_schema": {"adresse": "str"},
            "source": "native",
        },
        {
            "name": "groupe_stats",
            "description": "Composition et effectifs des groupes politiques "
            "(table `organes`, type GP, couleur officielle).",
            "input_schema": {"groupe_ref": "str?"},
            "source": "native",
        },
        *_mcp_tool("moulineuse"),
        *_mcp_tool("parlement"),
    ],
    "agent_lois": [
        {
            "name": "dossier_search",
            "description": "Recherche floue d'un dossier législatif par titre "
            "(table `dossiers`, index trgm sur titre_normalise).",
            "input_schema": {"query": "str", "limit": "int?"},
            "source": "native",
        },
        {
            "name": "dossier_timeline",
            "description": "Timeline du parcours d'un texte : arbre `actesLegislatifs` "
            "du dossier (dépôt, lectures, navette, promulgation).",
            "input_schema": {"dossier_uid": "str"},
            "source": "native",
        },
        *_mcp_tool("moulineuse"),
        *_mcp_tool("parlement"),
        *_mcp_tool("justicelibre"),
    ],
}


# ---------------------------------------------------------------------------
# Téléchargement + lecture des archives open data
# ---------------------------------------------------------------------------


async def _download(url: str, dest: Path, attempts: int = 3) -> Path:
    """Télécharge `url` vers `dest` (streaming httpx, retries) — sauté si déjà en cache."""
    if dest.exists() and dest.stat().st_size > 0:
        log.info("cache : %s déjà présent (%d octets), téléchargement sauté",
                 dest.name, dest.stat().st_size)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=httpx.Timeout(600.0, connect=30.0)
            ) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    tmp = dest.with_suffix(dest.suffix + ".part")
                    with open(tmp, "wb") as f:
                        async for chunk in resp.aiter_bytes(1 << 16):
                            f.write(chunk)
                    tmp.rename(dest)
            log.info("téléchargé : %s (%d octets)", dest.name, dest.stat().st_size)
            return dest
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("téléchargement %s : tentative %d/%d échouée (%s)",
                        url, attempt, attempts, e)
            await asyncio.sleep(5 * attempt)
    raise RuntimeError(f"téléchargement impossible : {url} ({last})")


async def _download_all() -> dict[str, Path]:
    """Télécharge (ou lit du cache) les archives AN + contours des circonscriptions."""
    return {
        "amo50": await _download(URL_AMO50, DATA_DIR / "AMO50_acteurs_mandats_organes_divises.json.zip"),
        "amo10": await _download(URL_AMO10, DATA_DIR / "AMO10_deputes_actifs_mandats_actifs_organes.json.zip"),
        "scrutins": await _download(URL_SCRUTINS, DATA_DIR / "Scrutins.json.zip"),
        "dossiers": await _download(URL_DOSSIERS, DATA_DIR / "Dossiers_Legislatifs.json.zip"),
        "circos": await _download(URL_CIRCOS, DATA_DIR / "circonscriptions-legislatives-p10.geojson"),
    }


def _iter_zip_json(path: Path, kind: str):
    """Itère les entrées JSON d'une archive dont le dossier parent est `kind`.

    Ex. AMO10 range ses fichiers sous `json/acteur/…`, AMO50 sous `acteur/…` :
    on matche sur le segment parent, pas sur le préfixe complet.
    """
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            parts = name.split("/")
            if len(parts) < 2 or parts[-2] != kind or not name.endswith(".json"):
                continue
            try:
                yield name, json.loads(z.read(name))
            except Exception as e:  # noqa: BLE001
                log.warning("entrée illisible %s!%s : %s", path.name, name, e)


# ---------------------------------------------------------------------------
# Parsing AMO (organes + députés)
# ---------------------------------------------------------------------------


def _pick_mandat(mandats: list[dict]) -> dict | None:
    """Choisit le mandat pertinent : mandat OUVERT (dateFin null) sinon le plus récent."""
    if not mandats:
        return None
    open_ = [m for m in mandats if not m.get("dateFin")]
    pool = open_ or mandats
    return max(pool, key=lambda m: m.get("dateDebut") or "")


def _depute_row(acteur: dict, mandats: list[dict], actif: bool) -> dict | None:
    """Ligne `deputes` depuis un acteur AMO + ses mandats — None si pas député 17e."""
    pa = _uid(acteur.get("uid"))
    asm = _pick_mandat(
        [m for m in mandats
         if m.get("typeOrgane") == "ASSEMBLEE" and str(m.get("legislature")) == "17"]
    )
    if not pa or asm is None:
        return None
    gp = _pick_mandat(
        [m for m in mandats
         if m.get("typeOrgane") == "GP" and str(m.get("legislature")) == "17"]
    )
    groupe_ref = None
    if gp is not None:
        refs = _as_list((gp.get("organes") or {}).get("organeRef"))
        groupe_ref = _uid(refs[0]) if refs else None
    ident = (acteur.get("etatCivil") or {}).get("ident") or {}
    nom = ident.get("nom") or ""
    prenom = ident.get("prenom") or ""
    nom_complet = " ".join(p for p in (prenom, nom) if p)
    lieu = (asm.get("election") or {}).get("lieu") or {}
    return {
        "uid": pa,
        "nom": nom,
        "prenom": prenom,
        "nom_complet": nom_complet,
        "nom_normalise": _norm(nom_complet),
        "groupe_ref": groupe_ref,
        "circo_departement": lieu.get("departement") or "",
        "circo_numero": str(lieu.get("numCirco") or ""),
        # Mandat clos = ancien député (libellé « ancien député » côté requête/agent).
        "actif": actif and not asm.get("dateFin"),
        "url_an": URL_AN_DEPUTE.format(uid=pa),
        "url_photo": URL_AN_PHOTO.format(num=pa.removeprefix("PA")),
        "raw": acteur,
    }


def _organe_row(organe: dict) -> dict:
    """Ligne `organes` depuis un organe AMO (couleurAssociee = couleur officielle)."""
    return {
        "uid": _uid(organe.get("uid")),
        "libelle": organe.get("libelle") or "",
        "libelle_abrege": organe.get("libelleAbrege") or organe.get("libelleAbrev") or "",
        "type_organe": organe.get("codeType") or "",
        "couleur_associee": organe.get("couleurAssociee"),
        "effectif": None,  # calculé plus bas pour les GP (députés actifs)
        "raw": organe,
    }


def _load_amo(paths: dict[str, Path]) -> tuple[list[dict], list[dict]]:
    """Fusionne AMO10 (frais, actifs) et AMO50 (tous acteurs, figé) → organes + députés."""
    # Organes : AMO10 (frais — contient les 11 GP créés les 18-19/07/2024) prioritaire,
    # AMO50 en complément pour les organes disparus de l'archive des actifs.
    organes: dict[str, dict] = {}
    for _, data in _iter_zip_json(paths["amo10"], "organe"):
        o = data.get("organe") or {}
        if o.get("codeType") in ORGANE_TYPES:
            row = _organe_row(o)
            organes[row["uid"]] = row
    for _, data in _iter_zip_json(paths["amo50"], "organe"):
        o = data.get("organe") or {}
        if o.get("codeType") in ORGANE_TYPES:
            row = _organe_row(o)
            organes.setdefault(row["uid"], row)

    # Députés actifs : AMO10, mandats embarqués dans le fichier acteur.
    deputes: dict[str, dict] = {}
    for _, data in _iter_zip_json(paths["amo10"], "acteur"):
        a = data.get("acteur") or {}
        row = _depute_row(a, _as_list((a.get("mandats") or {}).get("mandat")), actif=True)
        if row:
            deputes[row["uid"]] = row

    # Anciens députés : acteurs AMO50 absents d'AMO10 (mandats en fichiers séparés).
    mandats_by_acteur: dict[str, list[dict]] = {}
    for _, data in _iter_zip_json(paths["amo50"], "mandat"):
        m = data.get("mandat") or {}
        if str(m.get("legislature")) == "17" and m.get("typeOrgane") in ("ASSEMBLEE", "GP"):
            mandats_by_acteur.setdefault(_uid(m.get("acteurRef")), []).append(m)
    anciens = 0
    for _, data in _iter_zip_json(paths["amo50"], "acteur"):
        a = data.get("acteur") or {}
        pa = _uid(a.get("uid"))
        if not pa or pa in deputes:
            continue
        row = _depute_row(a, mandats_by_acteur.get(pa, []), actif=False)
        if row:
            deputes[row["uid"]] = row
            anciens += 1

    # Effectif des groupes politiques = nombre de députés actifs rattachés.
    counts: dict[str, int] = {}
    for d in deputes.values():
        if d["actif"] and d["groupe_ref"]:
            counts[d["groupe_ref"]] = counts.get(d["groupe_ref"], 0) + 1
    for uid, n in counts.items():
        if uid in organes and organes[uid]["type_organe"] == "GP":
            organes[uid]["effectif"] = n

    log.info(
        "AMO : %d organes (%d GP), %d députés dont %d anciens (AMO50 figé au 11/07/2024 "
        "fusionné avec AMO10 quotidien)",
        len(organes),
        sum(1 for o in organes.values() if o["type_organe"] == "GP"),
        len(deputes),
        anciens,
    )
    return list(organes.values()), list(deputes.values())


# ---------------------------------------------------------------------------
# Parsing scrutins + votes nominatifs
# ---------------------------------------------------------------------------


def _txt(x) -> str:
    """Champ texte AN défensif : chaîne nue, ou objet {#text}/{texte}/{libelle}."""
    if isinstance(x, dict):
        x = x.get("#text") or x.get("texte") or x.get("libelle") or ""
    return str(x or "")


def _dossier_ref(x) -> str | None:
    """Référence de dossier d'un scrutin : chaîne nue, {#text}, ou {libelle, dossierRef}."""
    if isinstance(x, dict):
        x = x.get("dossierRef") or x.get("#text") or ""
    x = _uid(x) if isinstance(x, dict) else (x or "")
    return str(x)[:64] or None


def _scrutin_rows(s: dict) -> tuple[dict, list[dict]]:
    """Ligne `scrutins` + lignes `votes` dépliées depuis un JSON de scrutin AN."""
    uid = _uid(s.get("uid"))
    numero = _int(s.get("numero"))
    d = s.get("dateScrutin")
    decompte = (s.get("syntheseVote") or {}).get("decompte") or {}
    scrutin = {
        "uid": uid,
        "numero": numero,
        "date_scrutin": date.fromisoformat(d[:10]) if d else None,
        "titre": _txt(s.get("titre")),
        "titre_normalise": _norm(_txt(s.get("titre"))),
        "sort": _txt((s.get("sort") or {}).get("code")),
        "demandeur": _txt((s.get("demandeur") or {}).get("texte")),
        "pour": _int(decompte.get("pour")),
        "contre": _int(decompte.get("contre")),
        "abstentions": _int(decompte.get("abstentions")),
        "non_votants": _int(decompte.get("nonVotants")),
        # `dossierLegislatif` : tantôt une chaîne "DLR5L17N…", tantôt un objet
        # {libelle, dossierRef} (variante observée sur les scrutins d'articles).
        "dossier_ref": _dossier_ref((s.get("objet") or {}).get("dossierLegislatif")),
        "url_an": URL_AN_SCRUTIN.format(numero=numero),
        "url_data": URL_DATA_SCRUTINS,
    }
    votes: list[dict] = []
    groupes = ((s.get("ventilationVotes") or {}).get("organe") or {}).get("groupes") or {}
    for g in _as_list(groupes.get("groupe")):
        groupe_ref = _uid(g.get("organeRef")) or ""
        dn = (g.get("vote") or {}).get("decompteNominatif") or {}
        for key, position in POSITIONS.items():
            bloc = dn.get(key)
            votants = _as_list(bloc.get("votant")) if isinstance(bloc, dict) else []
            for v in votants:
                acteur_ref = _uid(v.get("acteurRef"))
                if not acteur_ref:
                    continue
                votes.append(
                    {
                        "scrutin_uid": uid,
                        "acteur_ref": acteur_ref,
                        "groupe_ref": groupe_ref,
                        "position": position,
                        "par_delegation": _bool(v.get("parDelegation")),
                    }
                )
    return scrutin, votes


# ---------------------------------------------------------------------------
# Parsing + curation des dossiers législatifs
# ---------------------------------------------------------------------------


def _flatten_actes(node) -> list[dict]:
    """Aplatit récursivement l'arbre actesLegislatifs (chaque niveau peut être objet)."""
    out: list[dict] = []
    for a in _as_list((node or {}).get("acteLegislatif")):
        if isinstance(a, dict):
            out.append(a)
            out.extend(_flatten_actes(a.get("actesLegislatifs")))
    return out


def _dossier_row(d: dict) -> dict | None:
    """Ligne `dossiers` (+ métadonnées de curation _n_actes/_last_date) — None si vide."""
    uid = _uid(d.get("uid"))
    titre_dossier = d.get("titreDossier") or {}
    titre = titre_dossier.get("titre") or ""
    if not uid or not titre:
        return None
    chemin = titre_dossier.get("titreChemin") or ""
    actes = d.get("actesLegislatifs") or {}
    flat = _flatten_actes(actes)
    dated = [a for a in flat if a.get("dateActe")]
    last = max(dated, key=lambda a: a["dateActe"]) if dated else None
    statut = ""
    if last is not None:
        lib = last.get("libelleActe") or {}
        statut = lib.get("nomCanonique") or lib.get("libelleCourt") or ""
    if not statut:
        statut = (d.get("procedureParlementaire") or {}).get("libelle") or ""
    return {
        "uid": uid,
        "titre": titre,
        "titre_chemin": chemin,
        "titre_normalise": _norm(titre),
        "statut": statut,
        "actes": actes,  # arbre brut → timeline côté agent-lois
        "url_an": URL_AN_DOSSIER.format(chemin=chemin) if chemin else "",
        "_n_actes": len(flat),
        "_last_date": (last or {}).get("dateActe") or "",
    }


def _curate_dossiers(rows: list[dict]) -> list[dict]:
    """Sélection curée ~10 dossiers : top récents riches en actes + thèmes médiatiques.

    1. les `DOSSIERS_RICH_TOP` dossiers les plus récents ayant ≥ `DOSSIERS_RICH_MIN_ACTES`
       actes (parcours démontrable en timeline) ;
    2. pour chaque thème médiatique, le dossier matchant (sans accents/casse) le plus
       récent — puis complément par les plus riches jusqu'à `DOSSIERS_TARGET`.
    """
    by_recent = sorted(rows, key=lambda r: (r["_last_date"], r["_n_actes"]), reverse=True)
    selected: dict[str, dict] = {}
    for r in by_recent:
        if len(selected) >= DOSSIERS_RICH_TOP:
            break
        if r["_n_actes"] >= DOSSIERS_RICH_MIN_ACTES:
            selected[r["uid"]] = r
    for theme in DOSSIER_THEMES:
        tn = _norm(theme)
        cands = [r for r in rows if tn in r["titre_normalise"]]
        if not cands:
            log.info("curation dossiers : aucun titre ne matche « %s »", theme)
            continue
        # Préférence aux dossiers avec un vrai parcours (≥ 5 actes) — évite qu'un simple
        # rapport déposé hier masque le texte de loi emblématique du thème.
        best = max(cands, key=lambda r: (r["_n_actes"] >= 5, r["_last_date"], r["_n_actes"]))
        if best["uid"] not in selected:
            selected[best["uid"]] = best
            log.info("curation dossiers : « %s » → %s (%s)", theme, best["uid"],
                     best["titre"][:90])
    for r in by_recent:  # complément si des thèmes n'ont pas matché
        if len(selected) >= DOSSIERS_TARGET:
            break
        selected.setdefault(r["uid"], r)
    return list(selected.values())


# ---------------------------------------------------------------------------
# Seed Postgres
# ---------------------------------------------------------------------------

# DDL au runtime du seed : le volume Postgres peut préexister (db/init/ ne rejoue
# jamais) → extensions + index trgm créés ici, idempotents. Les colonnes normalisées
# (`nom_normalise`, `titre_normalise`) sont remplies en Python — pas besoin d'index
# fonctionnel sur unaccent() (qui exigerait un wrapper IMMUTABLE).
_RUNTIME_DDL = (
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE EXTENSION IF NOT EXISTS unaccent",
    "CREATE INDEX IF NOT EXISTS ix_deputes_nom_trgm "
    "ON deputes USING gin (nom_normalise gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_scrutins_titre_trgm "
    "ON scrutins USING gin (titre_normalise gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_dossiers_titre_trgm "
    "ON dossiers USING gin (titre_normalise gin_trgm_ops)",
)


async def _copy_rows(sm, model, rows: list[dict], batch: int = VOTES_BATCH) -> None:
    """TRUNCATE + reload d'une table AN, par lots, dans UNE transaction."""
    async with sm() as s:
        await s.execute(text(f"TRUNCATE {model.__tablename__} CASCADE"))
        for i in range(0, len(rows), batch):
            await s.execute(sa_insert(model), rows[i : i + batch])
        await s.commit()


async def _seed_registry(sm) -> None:
    """Registry orchestrateur : manifestes, workflows, tools, channels citoyens."""
    async with sm() as s:
        # Migration legacy : l'ancienne table `external_agents` a fusionné dans
        # `agent_types` (transport='a2a'). On la drop si elle traîne d'une version
        # antérieure — sinon `init_db` ne nettoiera pas la table supprimée du modèle.
        await s.execute(text("DROP TABLE IF EXISTS external_agents CASCADE"))
        # Extensions + index trgm (idempotents — cf. _RUNTIME_DDL).
        for stmt in _RUNTIME_DDL:
            await s.execute(text(stmt))
        # État propre sur les tables orchestrateur (les channels ne sont PAS tronqués).
        await s.execute(
            text(
                "TRUNCATE agent_types, conversations, tasks, events, "
                "ltm_memory, processed_messages, tools, execution_trace, "
                "workflows, attachments CASCADE"
            )
        )
        # Agent Registry — 3 agents citoyens.
        for m in MANIFESTS:
            s.add(m)
        # Workflows pré-définis (chaînes d'agents prêtes à proposer).
        for wf in SEED_WORKFLOWS:
            steps = [{"agent_type": a, "instruction": ""} for a in wf["agents"]]
            s.add(
                Workflow(
                    id=str(uuid4()),
                    name=wf["name"],
                    description=wf["description"],
                    graph=_chain_graph(wf["agents"]),
                    steps=steps,
                )
            )
        # Catalogue d'outils éditable depuis l'UI — répliqué au runtime dans KV `tools_config`.
        for agent_id, tools in TOOL_CATALOG.items():
            for t in tools:
                s.add(
                    Tool(
                        agent_id=agent_id,
                        name=t["name"],
                        description=t["description"],
                        input_schema=t["input_schema"],
                        source=t["source"],
                        enabled=True,
                        paused=False,
                    )
                )
        await s.commit()
    # Channels « Suivis » citoyens : purge des salons legacy (état-major G1..G10)
    # puis upsert idempotent — les messages des salons conservés ne sont pas touchés.
    keep = [d["id"] for d in CHANNEL_DEFS]
    async with sm() as s:
        await s.execute(sa_delete(ChannelMessage).where(ChannelMessage.channel_id.not_in(keep)))
        await s.execute(sa_delete(Channel).where(Channel.id.not_in(keep)))
        await s.commit()
    await ensure_channels(sm)
    log.info(
        "Postgres : %d manifestes, %d outils, %d workflows, %d channels",
        len(MANIFESTS),
        sum(len(v) for v in TOOL_CATALOG.values()),
        len(SEED_WORKFLOWS),
        len(CHANNEL_DEFS),
    )


def _circo_rows(path: Path) -> list[dict]:
    """Parse le geojson officiel des contours → lignes `circonscriptions` avec bbox.

    `codeCirconscription` = "{dept}{num:02d}" (ex. "7507") ; la bbox couvre tous les
    anneaux extérieurs (Polygon comme MultiPolygon) — le point-in-polygon exact est
    fait côté worker (ray-casting), la bbox n'est qu'un préfiltre.
    """
    gj = json.loads(path.read_text())
    rows: list[dict] = []
    for f in _as_list(gj.get("features")):
        props = f.get("properties") or {}
        geom = f.get("geometry") or {}
        code = str(props.get("codeCirconscription") or "")
        dept_code = str(props.get("codeDepartement") or "")
        if not code or not geom:
            continue
        num_txt = code[len(dept_code):] if code.startswith(dept_code) else code[-2:]
        try:
            numero = int(num_txt)
        except ValueError:
            continue
        polys = geom.get("coordinates") or []
        if geom.get("type") == "Polygon":
            polys = [polys]
        xs = [pt[0] for poly in polys for ring in poly[:1] for pt in ring]
        ys = [pt[1] for poly in polys for ring in poly[:1] for pt in ring]
        if not xs:
            continue
        dept_nom = str(props.get("nomDepartement") or "")
        rows.append({
            "code": code,
            "dept_code": dept_code,
            "dept_nom": dept_nom,
            "dept_normalise": _norm(dept_nom),
            "circo_numero": numero,
            "bbox_minx": min(xs), "bbox_miny": min(ys),
            "bbox_maxx": max(xs), "bbox_maxy": max(ys),
            "geometrie": geom,
        })
    return rows


async def _seed_assemblee(sm, paths: dict[str, Path]) -> None:
    """Ingestion AN : organes, députés, scrutins + votes, dossiers curés."""
    # --- organes + députés (AMO) ---
    organes, deputes = _load_amo(paths)
    await _copy_rows(sm, Organe, organes)
    await _copy_rows(sm, Depute, deputes)

    # --- scrutins + votes : itération par scrutin, jamais fail-all ---
    errors = 0
    n_scrutins = 0
    n_votes = 0
    scrutin_batch: list[dict] = []
    vote_batch: list[dict] = []
    async with sm() as s:
        await s.execute(text("TRUNCATE scrutins CASCADE"))
        await s.execute(text("TRUNCATE votes CASCADE"))
        for name, data in _iter_zip_json(paths["scrutins"], "json"):
            try:
                scrutin, votes = _scrutin_rows(data.get("scrutin") or {})
                if not scrutin["uid"]:
                    raise ValueError("scrutin sans uid")
            except Exception as e:  # noqa: BLE001
                errors += 1
                log.warning("scrutin illisible (%s) : %s", name, e)
                continue
            scrutin_batch.append(scrutin)
            vote_batch.extend(votes)
            n_scrutins += 1
            n_votes += len(votes)
            if len(scrutin_batch) >= VOTES_BATCH:
                await s.execute(sa_insert(Scrutin), scrutin_batch)
                scrutin_batch = []
            while len(vote_batch) >= VOTES_BATCH:
                await s.execute(sa_insert(Vote), vote_batch[:VOTES_BATCH])
                vote_batch = vote_batch[VOTES_BATCH:]
        if scrutin_batch:
            await s.execute(sa_insert(Scrutin), scrutin_batch)
        if vote_batch:
            await s.execute(sa_insert(Vote), vote_batch)
        await s.commit()
    log.info("scrutins : %d insérés, votes nominatifs : %d, erreurs ignorées : %d",
             n_scrutins, n_votes, errors)

    # --- dossiers législatifs (sélection curée) ---
    all_rows = []
    derr = 0
    for name, data in _iter_zip_json(paths["dossiers"], "dossierParlementaire"):
        try:
            row = _dossier_row(data.get("dossierParlementaire") or {})
        except Exception as e:  # noqa: BLE001
            derr += 1
            log.warning("dossier illisible (%s) : %s", name, e)
            continue
        if row:
            all_rows.append(row)
    curated = _curate_dossiers(all_rows)
    for r in curated:
        r.pop("_n_actes", None)
        r.pop("_last_date", None)
    await _copy_rows(sm, Dossier, curated)
    log.info("dossiers : %d parsés, %d curés insérés, erreurs ignorées : %d",
             len(all_rows), len(curated), derr)

    # --- contours des circonscriptions (geojson data.gouv.fr) ---
    circos = _circo_rows(paths["circos"])
    await _copy_rows(sm, Circonscription, circos, batch=100)
    log.info("circonscriptions : %d contours insérés (bbox pré-calculées)", len(circos))


async def _verify(sm) -> list[str]:
    """Témoins de fin de seed — liste de messages d'erreur (vide = OK)."""
    problems: list[str] = []
    async with sm() as s:
        # (a) VTANR5L17V1 existe et ses décomptes concordent avec les lignes `votes`.
        v1 = (
            await s.execute(select(Scrutin).where(Scrutin.uid == "VTANR5L17V1"))
        ).scalar_one_or_none()
        if v1 is None:
            problems.append("scrutin témoin VTANR5L17V1 absent")
        else:
            rows = (
                await s.execute(
                    select(Vote.position, func.count())
                    .where(Vote.scrutin_uid == "VTANR5L17V1")
                    .group_by(Vote.position)
                )
            ).all()
            counts = dict(rows)
            total = v1.pour + v1.contre + v1.abstentions + v1.non_votants
            if total <= 0:
                problems.append("VTANR5L17V1 : décomptes agrégés vides")
            for attr, pos in (("pour", "pour"), ("contre", "contre"),
                              ("abstentions", "abstention")):
                if getattr(v1, attr) != counts.get(pos, 0):
                    problems.append(
                        f"VTANR5L17V1 : {attr}={getattr(v1, attr)} ≠ "
                        f"{counts.get(pos, 0)} lignes votes"
                    )
            # Non-votants : l'agrégat officiel exclut membres du gouvernement et
            # président de séance, la liste nominative les inclut → ≥ attendu.
            if counts.get("nonVotant", 0) < v1.non_votants:
                problems.append(
                    f"VTANR5L17V1 : nonVotant nominatifs ({counts.get('nonVotant', 0)}) "
                    f"< agrégat officiel ({v1.non_votants})"
                )
            log.info(
                "témoin VTANR5L17V1 : pour=%d contre=%d abst=%d nv=%d "
                "(nominatifs nv=%d, gouvernement/présidence inclus)",
                v1.pour, v1.contre, v1.abstentions, v1.non_votants,
                counts.get("nonVotant", 0),
            )
        # (b) au moins 570 députés.
        n_dep = (await s.execute(select(func.count()).select_from(Depute))).scalar_one()
        if n_dep < 570:
            problems.append(f"seulement {n_dep} députés (< 570)")
        # (c) au moins 1 ancien député (actif=False) résolu avec un nom.
        ancien = (
            await s.execute(
                select(Depute)
                .where(Depute.actif.is_(False), Depute.nom != "")
                .limit(1)
            )
        ).scalar_one_or_none()
        if ancien is None:
            problems.append("aucun ancien député (actif=False) avec nom résolu")
        else:
            log.info("témoin ancien député : %s (%s)", ancien.nom_complet, ancien.uid)
        # (d) nombre de scrutins loggé (attendu > 1000 en 17e législature).
        n_scr = (await s.execute(select(func.count()).select_from(Scrutin))).scalar_one()
        log.info("témoins : %d députés, %d scrutins (attendu > 1000)", n_dep, n_scr)
        if n_scr <= 1000:
            log.warning("nombre de scrutins anormalement bas : %d", n_scr)
    return problems


async def _ping_postgres() -> None:
    """Attend Postgres et crée les tables manquantes (init_db idempotent)."""
    await init_db(settings.postgres_dsn)


# ---------------------------------------------------------------------------
# Elasticsearch (index agents + index corpus vide)
# ---------------------------------------------------------------------------

# Mapping CollectionDTO — aligné sur le template `collections` de la Federated Search.
AGENTS_MAPPING = {
    "mappings": {
        "properties": {
            "coreName": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "coreType": {"type": "keyword"},
            "coreDataId": {"type": "keyword"},
            "coreDataSource": {"type": "keyword"},
            "coreCreateDate": {"type": "date"},
            "coreLastUpdateDate": {"type": "date"},
            "coreMainDate": {"type": "date"},
            "coreDataURI": {"type": "keyword"},
            "coreProtection": {"type": "keyword"},
            "coreDescription": {"type": "text"},
            "coreCreator": {"type": "keyword"},
        }
    }
}


def _agent_to_es_doc(agent: AgentType, now: datetime) -> dict:
    """Convertit un AgentType en document CollectionDTO pour l'index `hemicycle-agents`."""
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    slug = agent.id if agent.id.endswith("-agent") else f"{agent.id}-agent"
    return {
        "coreName": f"{agent.discipline} Agent",
        "coreType": "Agent",
        "coreDataId": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"hemicycle-agent-{agent.id}")),
        "coreDataSource": "Hémicycle",
        "coreCreateDate": ts,
        "coreLastUpdateDate": ts,
        "coreMainDate": ts,
        "coreDataURI": f"{settings.agents_base_url}/agents/{slug}",
        "coreProtection": "unprotected",
        "coreDescription": agent.description,
        "coreCreator": "hemicycle",
    }


async def _seed_elasticsearch() -> None:
    """Recrée `mi-search-corpus` VIDE, crée `hemicycle-agents` (si absent), indexe les agents."""
    es = AsyncElasticsearch(settings.elasticsearch_url, request_timeout=30)

    # --- corpus interne : index vide (plus de fake data) ---
    await es.indices.delete(index=CORPUS_INDEX, ignore_unavailable=True)
    await es.indices.create(
        index=CORPUS_INDEX,
        mappings={
            "properties": {
                "title": {"type": "text"},
                "body": {"type": "text"},
                "source": {"type": "keyword"},
                "reliability": {"type": "keyword"},
                "type": {"type": "keyword"},
                "url": {"type": "keyword"},
                "date": {"type": "date"},
            }
        },
    )
    log.info("Elasticsearch : index '%s' recréé vide", CORPUS_INDEX)

    # --- index agents (CollectionDTO) ---
    if not await es.indices.exists(index=AGENTS_INDEX):
        await es.indices.create(index=AGENTS_INDEX, **AGENTS_MAPPING)
        log.info("Elasticsearch : index '%s' créé", AGENTS_INDEX)
    sm = get_sessionmaker(settings.postgres_dsn)
    async with sm() as s:
        agents = (await s.execute(select(AgentType))).scalars().all()
    now = datetime.now(timezone.utc)
    actions = [
        {"_index": AGENTS_INDEX, "_id": a.id, "_source": _agent_to_es_doc(a, now)}
        for a in agents
    ]
    if actions:
        await async_bulk(es, actions)
        await es.indices.refresh(index=AGENTS_INDEX)
    log.info("Elasticsearch : %d agents indexés dans '%s'", len(actions), AGENTS_INDEX)

    await es.close()


async def _retry(coro_factory, what: str, attempts: int = 40):
    """Réessaie une opération d'init le temps que la dépendance soit prête."""
    for _ in range(attempts):
        try:
            return await coro_factory()
        except Exception as e:  # noqa: BLE001
            log.info("attente de %s (%s)...", what, e)
            await asyncio.sleep(2)
    raise RuntimeError(f"{what} indisponible")


async def main() -> None:
    """Exécute le seed complet : download AN → Postgres → Elasticsearch → témoins."""
    paths = await _download_all()
    # `_retry` n'encadre que l'attente de la dépendance (connexion + DDL idempotent) —
    # l'ingestion elle-même ne tourne qu'UNE fois (pas de re-parse 40× sur bug de data).
    await _retry(_ping_postgres, "Postgres")
    sm = get_sessionmaker(settings.postgres_dsn)
    await _seed_registry(sm)
    await _seed_assemblee(sm, paths)
    await _retry(_seed_elasticsearch, "Elasticsearch")
    problems = await _verify(sm)
    if problems:
        for p in problems:
            log.error("témoin KO : %s", p)
        raise SystemExit(1)
    log.info("seed terminé.")


if __name__ == "__main__":
    asyncio.run(main())
