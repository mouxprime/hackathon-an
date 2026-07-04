"""Handlers de discipline des sub-agents Hémicycle : VOTES, DEPUTES, LOIS.

Chaque handler interroge les données officielles de l'Assemblée nationale
(seedées depuis data.assemblee-nationale.fr dans Postgres — LECTURE SEULE,
c'est l'assouplissement assumé de l'invariant n°7 : les workers lisent la base
de données parlementaire mais n'y écrivent jamais), trace chaque étape en
français citoyen (`thinking` / `tool_call` / `tool_result`) et rend un payload
typé + `sources[]` officielles.

Enrichissements externes, tous en échec-doux (la base locale répond toujours) :
- CIVIX (API REST) : timeout 5 s, appelé seulement si la base locale ne suffit pas ;
- MCPs hackathon (moulineuse / parlement / JusticeLibre) : URL lue de l'env,
  vide → `tool_result` skipped tracé (visible dans l'UI, honnête pour le jury).
"""

import asyncio
import json
import logging
import os
import re
import unicodedata
from datetime import date

import httpx

from sqlalchemy import text

from hemicycle_shared.config import settings
from hemicycle_shared.db import get_sessionmaker
from hemicycle_shared.llm_client import complete, complete_stream
from hemicycle_shared.manifests import MCPServerRef
from hemicycle_shared.mcp_client import MCPClient

log = logging.getLogger("subagent.disciplines")

_OPENDATA_SCRUTINS = ("https://data.assemblee-nationale.fr/travaux-parlementaires/votes",
                      "Open data Assemblée nationale — Scrutins (17e législature)")


def _tool_paused(td, tool_name: str) -> bool:
    """Vrai si l'outil est marqué `paused` dans la config injectée par l'orchestrateur."""
    cfg = (td.params or {}).get("tools", {}).get(tool_name)
    return bool(cfg and cfg.get("paused"))


def _lang(td) -> str:
    """Langue attendue de la sortie de l'agent ("en" | "fr")."""
    return getattr(td, "lang", "fr") or "fr"


def _norm(s: str) -> str:
    """Normalisation accent/casse — DOIT rester alignée avec la normalisation du seed."""
    nfkd = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def _with_attachments(td, base: str) -> str:
    """Préfixe le prompt LLM avec le contenu des pièces jointes (injectées via `params`)."""
    atts = (td.params or {}).get("attachments") or []
    if not atts:
        return base
    blocks = [f"[Document joint : {a.get('filename', '?')}]\n{(a.get('text') or '')[:2000]}"
              for a in atts[:5]]
    return "DOCUMENTS FOURNIS PAR LE CITOYEN :\n" + "\n\n".join(blocks) + f"\n\nREQUÊTE :\n{base}"


def _with_upstream(td, base: str) -> str:
    """Préfixe le prompt avec les synthèses des agents amont, si présentes."""
    arts = (td.params or {}).get("upstream_artifacts") or []
    blocks = []
    for a in arts:
        p = a.get("payload") or {}
        lines = [f"[{a.get('agent_type', '?')}]"]
        if p.get("summary"):
            lines.append(str(p["summary"]))
        for s in (p.get("sources") or [])[:5]:
            lines.append(f"- source : {s.get('title', '')} — {s.get('snippet', '')}")
        if len(lines) == 1 and a.get("preview"):
            lines.append(a["preview"])
        blocks.append("\n".join(lines))
    if not blocks:
        return base
    return "SORTIES DES AGENTS AMONT :\n" + "\n\n".join(blocks) + f"\n\nMISSION :\n{base}"


_DRAFT_FLUSH_CHARS = 24


async def _stream_summary(td, trace, prompt: str) -> str:
    """Rédige le résumé de l'agent en STREAMING vers l'UI (draft_chunk), repli bloquant."""
    sid = f"draft-{td.corr_id}"
    lang = _lang(td)
    text_acc = ""
    buf = ""
    try:
        async for chunk in complete_stream("subagent-reasoning", "low", prompt, lang=lang,
                                           enable_thinking=False):
            if chunk.get("type") != "token":
                continue
            tok = chunk.get("text", "")
            if not tok:
                continue
            text_acc += tok
            buf += tok
            if len(buf) >= _DRAFT_FLUSH_CHARS or tok.endswith((" ", "\n", ".", ",")):
                await trace("draft_chunk", {"text": buf, "stream_id": sid, "done": False})
                buf = ""
        if buf:
            await trace("draft_chunk", {"text": buf, "stream_id": sid, "done": False})
    except Exception:  # noqa: BLE001 - le streaming ne doit jamais casser la tâche
        log.warning("stream summary indisponible (corr=%s) — repli bloquant", td.corr_id,
                    exc_info=True)
    if not text_acc.strip():
        try:
            text_acc = await complete("subagent-reasoning", "low", prompt, lang=lang,
                                      enable_thinking=False)
        except Exception:  # noqa: BLE001 - LLM down : le payload structuré reste la réponse
            log.warning("complete indisponible (corr=%s) — résumé déterministe", td.corr_id)
            text_acc = ""
        if text_acc.strip():
            await trace("draft_chunk", {"text": text_acc, "stream_id": sid, "done": False})
    await trace("draft_chunk", {"text": "", "stream_id": sid, "done": True})
    return text_acc


# --- Accès Postgres (LECTURE SEULE) ---------------------------------------------

_sessionmaker = None


def _sm():
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = get_sessionmaker(settings.postgres_dsn)
    return _sessionmaker


async def _rows(sql: str, **params) -> list[dict]:
    """Exécute un SELECT et rend des dicts. Toute écriture est un bug (invariant)."""
    async with _sm()() as session:
        res = await session.execute(text(sql), params)
        return [dict(r._mapping) for r in res.fetchall()]


# --- Enrichissements externes en échec-doux --------------------------------------

_CIVIX_BASE = os.environ.get("HEMI_CIVIX_BASE_URL", "https://www.civix.fr/api/v1")


async def _civix_get(trace, path: str, fr: bool = True):
    """GET CIVIX avec timeout 5 s. Échec → tool_result d'erreur tracé + None."""
    url = f"{_CIVIX_BASE}{path}"
    await trace("tool_call", {"tool": "civix_api", "endpoint": url, "params": {}})
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            r = await http.get(url)
            r.raise_for_status()
            data = r.json()
        await trace("tool_result", {"tool": "civix_api", "preview": str(data)[:180]})
        return data
    except Exception as e:  # noqa: BLE001 - repli volontaire, la base locale répond
        await trace("tool_result", {"tool": "civix_api",
                                    "error": f"indisponible ({type(e).__name__}) — je continue avec les données officielles locales"})
        return None


async def _mcp_tool(trace, server_name: str, env_url: str, tool: str, args: dict):
    """Appel MCP en échec-doux : URL non configurée → skipped tracé, erreur → tracée."""
    url = os.environ.get(env_url, "").strip()
    if not url:
        await trace("tool_result", {"tool": tool, "mcp_server": server_name,
                                    "skipped": "serveur MCP non configuré"})
        return None
    await trace("tool_call", {"tool": tool, "mcp_server": server_name,
                              "endpoint": url, "params": args})
    try:
        client = MCPClient(MCPServerRef(name=server_name, url=url, tools=[]))
        res = await asyncio.wait_for(client.call_tool(tool, args), timeout=15.0)
        await trace("tool_result", {"tool": tool, "mcp_server": server_name,
                                    "preview": str(res)[:200]})
        return res
    except Exception as e:  # noqa: BLE001
        await trace("tool_result", {"tool": tool, "mcp_server": server_name,
                                    "error": str(e)[:120]})
        return None


# --- Helpers métier ---------------------------------------------------------------

_MONTHS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
           "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
           "decembre": 12}

# Tournures génériques retirées AVANT la similarité trigram : « où en est le projet
# de loi sur la fin de vie » doit matcher le dossier « Fin de vie », pas « Projet de
# loi de finances » (mesuré : sans ce strip, PLF 2026 gagne à 0,34 contre 0,28).
# Ordre = du plus long au plus court (un strip partiel casserait les suivants).
_GENERIC_PHRASES = (
    "quel est l etat d avancement", "ou en est on de", "ou en est", "que dit",
    "comment les deputes ont ils vote", "comment ont vote les deputes",
    "comment a vote", "comment ont vote", "a t il vote", "a t elle vote",
    "le projet de loi sur", "la proposition de loi sur",
    "le projet de loi", "la proposition de loi", "projet de loi",
    "proposition de loi", "le texte de loi", "texte de loi", "le dossier",
    "resultat du scrutin sur", "resultat du scrutin", "le scrutin sur",
    "le vote sur", "la loi sur", "la loi", "le texte", "s il te plait",
    "s il vous plait", "est ce que", "pouvez vous me dire", "peux tu me dire",
    "dis moi", "je veux savoir", "j aimerais savoir", "actuellement",
)


def _residue(txt: str) -> str:
    """Résidu « utile » d'une question pour la recherche floue (sans le boilerplate).

    Ponctuation → espaces AVANT le strip des phrases (« ont-ils voté » doit matcher
    « ont ils vote ») — contrairement à `_norm`, qui conserve les tirets pour rester
    aligné sur les `nom_normalise`/`titre_normalise` du seed.
    """
    base = re.sub(r"[^a-z0-9]+", " ", _norm(txt)).strip()
    n = f" {base} "
    for p in _GENERIC_PHRASES:
        n = n.replace(f" {p} ", " ")
    n = re.sub(r"\s+", " ", n).strip()
    return n if len(n) >= 4 else base


def _extract_date(txt_norm: str) -> date | None:
    m = re.search(r"(\d{1,2})(?:er)?\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})", txt_norm)
    if m:
        return date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", txt_norm)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None


def _extract_scrutin_numero(txt_norm: str) -> int | None:
    m = re.search(r"scrutin\s*(?:n\s*[°o]?\s*)?(\d+)", txt_norm)
    return int(m.group(1)) if m else None


# --- « Qui est mon député ? » par adresse ------------------------------------------
# Chaîne 100 % officielle : géocodage BAN (api-adresse.data.gouv.fr) → contours des
# circonscriptions (data.gouv.fr, table `circonscriptions`, préfiltre bbox SQL) →
# point-in-polygon par ray-casting en Python pur (pas de PostGIS).

_ADDRESS_HINTS = ("j habite", "mon adresse", "je vis", "je reside", "chez moi")


def _extract_address(query: str) -> str | None:
    """Adresse probable dans la question (code postal 5 chiffres ou « j'habite… »)."""
    flat = re.sub(r"[^a-z0-9]+", " ", _norm(query))
    if not re.search(r"\b\d{5}\b", query) and not any(h in flat for h in _ADDRESS_HINTS):
        return None
    for part in re.split(r"[?!.\n]", query):
        if re.search(r"\b\d{5}\b", part):
            cleaned = re.sub(
                r"(?i).*?(j'habite(?:\s+au)?|mon adresse(?:\s+est)?|je vis(?:\s+au)?|je réside(?:\s+au)?)\s*[:,]?\s*",
                "", part).strip()
            return cleaned or part.strip()
    return None


async def _ban_geocode(trace, address: str) -> dict | None:
    """Géocode une adresse via l'API Adresse officielle (Base Adresse Nationale)."""
    url = "https://api-adresse.data.gouv.fr/search/"
    await trace("tool_call", {"tool": "api_adresse_geocode",
                              "mcp_server": "api-adresse.data.gouv.fr (BAN officielle)",
                              "endpoint": url, "params": {"q": address[:120]}})
    try:
        async with httpx.AsyncClient(timeout=6.0) as http:
            r = await http.get(url, params={"q": address, "limit": 1})
            r.raise_for_status()
            feats = (r.json() or {}).get("features") or []
        if not feats:
            await trace("tool_result", {"tool": "api_adresse_geocode", "count": 0,
                                        "preview": "adresse introuvable dans la BAN"})
            return None
        f = feats[0]
        lon, lat = (f.get("geometry") or {}).get("coordinates", [None, None])[:2]
        label = (f.get("properties") or {}).get("label", "")
        await trace("tool_result", {"tool": "api_adresse_geocode",
                                    "preview": f"{label} → ({lat:.4f}, {lon:.4f})"})
        return {"lon": lon, "lat": lat, "label": label}
    except Exception as e:  # noqa: BLE001 - échec-doux, repli sur la recherche textuelle
        await trace("tool_result", {"tool": "api_adresse_geocode",
                                    "error": f"API Adresse indisponible ({type(e).__name__})"})
        return None


def _in_ring(x: float, y: float, ring: list) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _in_geom(x: float, y: float, geom: dict) -> bool:
    """Point dans une geometry geojson (Polygon/MultiPolygon, trous gérés)."""
    def in_poly(poly: list) -> bool:
        if not poly or not _in_ring(x, y, poly[0]):
            return False
        return not any(_in_ring(x, y, hole) for hole in poly[1:])
    if geom.get("type") == "Polygon":
        return in_poly(geom.get("coordinates") or [])
    if geom.get("type") == "MultiPolygon":
        return any(in_poly(p) for p in geom.get("coordinates") or [])
    return False


async def _circo_from_point(trace, lon: float, lat: float) -> dict | None:
    """Circonscription contenant le point (préfiltre bbox SQL puis ray-casting)."""
    await trace("tool_call", {"tool": "sql_circo_lookup",
                              "params": {"lat": round(lat, 4), "lon": round(lon, 4)},
                              "mcp_server": "opendata-an (base locale)"})
    rows = await _rows(
        """SELECT code, dept_code, dept_nom, dept_normalise, circo_numero, geometrie
           FROM circonscriptions
           WHERE :x BETWEEN bbox_minx AND bbox_maxx AND :y BETWEEN bbox_miny AND bbox_maxy""",
        x=lon, y=lat)
    for r in rows:
        geom = r["geometrie"]
        if isinstance(geom, str):
            geom = json.loads(geom)
        if _in_geom(lon, lat, geom):
            await trace("tool_result", {"tool": "sql_circo_lookup",
                                        "preview": f"{r['dept_nom']} — "
                                                   f"{r['circo_numero']}e circonscription"})
            return r
    await trace("tool_result", {"tool": "sql_circo_lookup", "count": 0,
                                "preview": "aucune circonscription trouvée pour ce point"})
    return None


async def _depute_by_circo(dept_normalise: str, numero: int) -> dict | None:
    """Député (actif d'abord) d'une circonscription (département normalisé + numéro)."""
    rows = await _rows(
        """SELECT d.uid, d.nom, d.prenom, d.nom_complet, d.groupe_ref, d.actif,
                  d.circo_departement, d.circo_numero, d.url_an, d.url_photo,
                  o.libelle_abrege AS groupe_abrege, o.libelle AS groupe,
                  o.couleur_associee AS groupe_couleur
           FROM deputes d LEFT JOIN organes o ON o.uid = d.groupe_ref
           WHERE lower(unaccent(d.circo_departement)) = :dept
             AND ltrim(d.circo_numero::text, '0') = :n
           ORDER BY d.actif DESC LIMIT 1""",
        dept=dept_normalise, n=str(numero))
    return rows[0] if rows else None


async def _find_depute(txt: str) -> dict | None:
    """Retrouve un député cité dans le texte (nom complet normalisé, puis nom seul)."""
    txt_norm = _norm(txt)
    rows = await _rows(
        """SELECT d.uid, d.nom, d.prenom, d.nom_complet, d.groupe_ref, d.actif,
                  d.circo_departement, d.circo_numero, d.url_an, d.url_photo,
                  o.libelle_abrege AS groupe_abrege, o.libelle AS groupe,
                  o.couleur_associee AS groupe_couleur
           FROM deputes d LEFT JOIN organes o ON o.uid = d.groupe_ref
           WHERE :txt LIKE '%' || d.nom_normalise || '%'
           ORDER BY length(d.nom_normalise) DESC, d.actif DESC LIMIT 1""",
        txt=txt_norm)
    if rows:
        return rows[0]
    rows = await _rows(
        """SELECT d.uid, d.nom, d.prenom, d.nom_complet, d.groupe_ref, d.actif,
                  d.circo_departement, d.circo_numero, d.url_an, d.url_photo,
                  o.libelle_abrege AS groupe_abrege, o.libelle AS groupe,
                  o.couleur_associee AS groupe_couleur
           FROM deputes d LEFT JOIN organes o ON o.uid = d.groupe_ref
           WHERE length(d.nom) >= 4
             AND :txt LIKE '%' || lower(d.nom) || '%'
           ORDER BY d.actif DESC, length(d.nom) DESC LIMIT 1""",
        txt=txt_norm)
    return rows[0] if rows else None


async def _find_scrutin(txt: str) -> dict | None:
    """Retrouve le scrutin visé : n° explicite > date > similarité trigram sur le titre."""
    txt_norm = _norm(txt)
    numero = _extract_scrutin_numero(txt_norm)
    if numero is not None:
        rows = await _rows("SELECT * FROM scrutins WHERE numero = :n LIMIT 1", n=numero)
        if rows:
            return rows[0]
    d = _extract_date(txt_norm)
    if d is not None:
        rows = await _rows(
            """SELECT *, similarity(titre_normalise, :q) AS sim FROM scrutins
               WHERE date_scrutin = :d ORDER BY sim DESC LIMIT 1""",
            d=d, q=_residue(txt))
        if rows:
            return rows[0]
    rows = await _rows(
        """SELECT *, similarity(titre_normalise, :q) AS sim FROM scrutins
           ORDER BY sim DESC, date_scrutin DESC LIMIT 1""",
        q=_residue(txt))
    if rows and (rows[0].get("sim") or 0) > 0.05:
        return rows[0]
    return None


def _majoritaire(g: dict) -> str:
    counts = {"pour": g.get("pour", 0) or 0, "contre": g.get("contre", 0) or 0,
              "abstention": g.get("abstention", 0) or 0, "nonVotant": g.get("nonVotant", 0) or 0}
    return max(counts, key=counts.get)


async def _groupes_scrutin(uid: str) -> list[dict]:
    rows = await _rows(
        """SELECT coalesce(o.libelle, v.groupe_ref, 'Sans groupe') AS nom,
                  coalesce(o.libelle_abrege, '') AS abrege,
                  coalesce(o.couleur_associee, '#94a3b8') AS couleur,
                  count(*) FILTER (WHERE v.position = 'pour')       AS pour,
                  count(*) FILTER (WHERE v.position = 'contre')     AS contre,
                  count(*) FILTER (WHERE v.position = 'abstention') AS abstention,
                  count(*) FILTER (WHERE v.position = 'nonVotant')  AS "nonVotant"
           FROM votes v LEFT JOIN organes o ON o.uid = v.groupe_ref
           WHERE v.scrutin_uid = :uid
           GROUP BY 1, 2, 3 ORDER BY count(*) DESC""",
        uid=uid)
    for g in rows:
        g["position_majoritaire"] = _majoritaire(g)
    return rows


def _scrutin_block(s: dict) -> dict:
    return {
        "uid": s.get("uid", ""), "numero": s.get("numero"),
        "date": str(s.get("date_scrutin") or ""), "titre": s.get("titre", ""),
        "sort": s.get("sort", ""),
        "pour": s.get("pour", 0), "contre": s.get("contre", 0),
        "abstentions": s.get("abstentions", 0), "non_votants": s.get("non_votants", 0),
        "url_an": s.get("url_an", ""), "url_data": s.get("url_data", ""),
    }


def _scrutin_sources(s: dict) -> list[dict]:
    out = []
    if s.get("url_an"):
        out.append({"title": f"Scrutin n°{s.get('numero')} — assemblee-nationale.fr",
                    "url": s["url_an"], "snippet": (s.get("titre") or "")[:160],
                    "source": "assemblee-nationale.fr", "reliability": "A", "score": 1.0})
    out.append({"title": _OPENDATA_SCRUTINS[1], "url": s.get("url_data") or _OPENDATA_SCRUTINS[0],
                "snippet": "Jeu de données officiel des scrutins publics (Licence Ouverte).",
                "source": "data.assemblee-nationale.fr", "reliability": "A", "score": 1.0})
    return out


# --- Handlers ---------------------------------------------------------------------

async def votes_handler(td, progress, trace) -> dict:
    """AGENT VOTES : retrouve un scrutin, son détail par groupe et le vote d'un député."""
    query = td.params.get("text") or td.instruction
    await trace("thinking", {"text": "Je cherche le scrutin concerné dans les données "
                                     "officielles de l'Assemblée nationale."})
    await progress(0.15, "recherche du scrutin")

    scrutin = None
    if not _tool_paused(td, "sql_scrutins_search"):
        await trace("tool_call", {"tool": "sql_scrutins_search",
                                  "params": {"query": query[:100]},
                                  "mcp_server": "opendata-an (base locale)"})
        scrutin = await _find_scrutin(query)
        await trace("tool_result", {"tool": "sql_scrutins_search",
                                    "count": 1 if scrutin else 0,
                                    "preview": (scrutin or {}).get("titre", "aucun scrutin trouvé")[:160]})

    if scrutin is None:
        # Dernier recours : CIVIX (souvent en timeout — tracé honnêtement), puis MCP parlement.
        await _civix_get(trace, "/scrutins?legislature=17&limit=1")
        await _mcp_tool(trace, "mcp-parlement", "HEMI_MCP_PARLEMENT_URL",
                        "search_scrutins", {"query": query[:120]})
        summary = ("Je n'ai pas trouvé ce scrutin dans les données officielles disponibles "
                   "(scrutins publics de la 17e législature). Précisez le numéro du scrutin, "
                   "sa date ou le titre du texte : je pourrai alors donner le résultat exact.")
        await trace("draft_chunk", {"text": summary, "stream_id": f"draft-{td.corr_id}", "done": False})
        await trace("draft_chunk", {"text": "", "stream_id": f"draft-{td.corr_id}", "done": True})
        return {"scrutin": {}, "position_depute": None, "groupes": [],
                "sources": [{"title": _OPENDATA_SCRUTINS[1], "url": _OPENDATA_SCRUTINS[0],
                             "snippet": "Jeu de données officiel des scrutins publics.",
                             "source": "data.assemblee-nationale.fr", "reliability": "A",
                             "score": 1.0}],
                "summary": summary}

    await progress(0.4, "détail des votes par groupe")
    await trace("tool_call", {"tool": "sql_scrutin_detail", "params": {"uid": scrutin["uid"]},
                              "mcp_server": "opendata-an (base locale)"})
    groupes = await _groupes_scrutin(scrutin["uid"])
    await trace("tool_result", {"tool": "sql_scrutin_detail", "count": len(groupes),
                                "preview": f"{len(groupes)} groupes, sort : {scrutin.get('sort', '?')}"})

    position_depute = None
    dep = await _find_depute(query)
    if dep is not None:
        await progress(0.6, f"vote de {dep['nom_complet']}")
        await trace("tool_call", {"tool": "sql_vote_depute",
                                  "params": {"depute": dep["nom_complet"], "scrutin": scrutin["uid"]},
                                  "mcp_server": "opendata-an (base locale)"})
        vrows = await _rows(
            "SELECT position, par_delegation FROM votes WHERE scrutin_uid = :s AND acteur_ref = :a",
            s=scrutin["uid"], a=dep["uid"])
        if vrows:
            position_depute = {"acteur_ref": dep["uid"], "nom": dep["nom_complet"],
                               "groupe": dep.get("groupe") or "", "position": vrows[0]["position"]}
            await trace("tool_result", {"tool": "sql_vote_depute",
                                        "preview": f"{dep['nom_complet']} : {vrows[0]['position']}"})
        else:
            position_depute = {"acteur_ref": dep["uid"], "nom": dep["nom_complet"],
                               "groupe": dep.get("groupe") or "", "position": "absent"}
            await trace("tool_result", {"tool": "sql_vote_depute",
                                        "preview": f"{dep['nom_complet']} n'apparaît pas dans ce "
                                                   "scrutin (n'a pas pris part au vote)"})

    sources = _scrutin_sources(scrutin)
    await progress(0.8, "rédaction de la synthèse")
    brief = (f"{td.instruction}\n\nDONNÉES OFFICIELLES (scrutin n°{scrutin.get('numero')}, "
             f"{scrutin.get('date_scrutin')}) :\n"
             f"- Titre : {scrutin.get('titre')}\n- Sort : {scrutin.get('sort')}\n"
             f"- Pour : {scrutin.get('pour')} · Contre : {scrutin.get('contre')} · "
             f"Abstentions : {scrutin.get('abstentions')} · Non-votants : {scrutin.get('non_votants')}\n")
    if position_depute:
        if position_depute["position"] == "absent":
            brief += (f"- {position_depute['nom']} ({position_depute['groupe']}) n'apparaît pas "
                      "dans ce scrutin : il/elle n'a pas pris part au vote (à ne pas confondre "
                      "avec « abstention » ou « non-votant »).\n")
        else:
            brief += (f"- Vote de {position_depute['nom']} ({position_depute['groupe']}) : "
                      f"{position_depute['position']}\n")
    top = groupes[:6]
    if top:
        brief += "Par groupe (pour/contre/abst/NV) : " + " ; ".join(
            f"{g['nom']} {g['pour']}/{g['contre']}/{g['abstention']}/{g['nonVotant']}" for g in top)
    brief += ("\n\nRédige une synthèse factuelle et neutre en 3-5 phrases avec ces chiffres "
              "EXACTS (aucun autre chiffre). Pas de jugement de valeur.")
    summary = await _stream_summary(td, trace, _with_attachments(td, _with_upstream(td, brief)))
    if not summary.strip():
        summary = (f"Scrutin n°{scrutin.get('numero')} du {scrutin.get('date_scrutin')} : "
                   f"{scrutin.get('titre')}. Résultat : {scrutin.get('sort')} — "
                   f"{scrutin.get('pour')} pour, {scrutin.get('contre')} contre, "
                   f"{scrutin.get('abstentions')} abstentions, {scrutin.get('non_votants')} non-votants.")
    await progress(0.95, "terminé")
    return {"scrutin": _scrutin_block(scrutin), "position_depute": position_depute,
            "groupes": groupes, "sources": sources, "summary": summary}


async def deputes_handler(td, progress, trace) -> dict:
    """AGENT DÉPUTÉS : fiche d'un député, votes récents, statistiques de participation."""
    query = td.params.get("text") or td.instruction
    await trace("thinking", {"text": "Je cherche le député concerné dans le registre officiel "
                                     "des acteurs de l'Assemblée nationale."})
    await progress(0.15, "recherche du député")

    dep = None
    circo_note = ""
    # Chemin ADRESSE d'abord (« j'habite … 75011 Paris ») : géocodage BAN officiel →
    # contour officiel de circonscription → député. Passe AVANT la recherche par nom
    # (une adresse contient des noms de rues qui ressemblent à des noms de députés).
    address = _extract_address(query)
    if address and not _tool_paused(td, "api_adresse_geocode"):
        await trace("thinking", {"text": "Une adresse est mentionnée : je la localise via "
                                         "l'API Adresse officielle (Base Adresse Nationale), "
                                         "puis je retrouve la circonscription législative "
                                         "par ses contours officiels."})
        await progress(0.25, "géocodage de l'adresse")
        geo = await _ban_geocode(trace, address)
        if geo and geo.get("lon") is not None:
            circo = await _circo_from_point(trace, geo["lon"], geo["lat"])
            if circo:
                dep = await _depute_by_circo(circo["dept_normalise"], circo["circo_numero"])
                if dep:
                    circo_note = (f"Adresse localisée : {geo['label']} → "
                                  f"{circo['dept_nom']}, {circo['circo_numero']}e "
                                  "circonscription (contours officiels data.gouv.fr).")

    if dep is None and not _tool_paused(td, "sql_depute_lookup"):
        await trace("tool_call", {"tool": "sql_depute_lookup", "params": {"query": query[:100]},
                                  "mcp_server": "opendata-an (base locale)"})
        dep = await _find_depute(query)
        if dep is None:
            # Recherche par circonscription : « 1re circonscription de la Gironde »
            txt_norm = _norm(query)
            m = re.search(r"(\d{1,2})\s*(?:re|e|ere|eme)?\s*circonscription\s+(?:de\s+la\s+|de\s+l'|du\s+|des\s+|de\s+)?([a-z' -]+)",
                          txt_norm)
            if m:
                rows = await _rows(
                    """SELECT d.uid, d.nom, d.prenom, d.nom_complet, d.groupe_ref, d.actif,
                              d.circo_departement, d.circo_numero, d.url_an, d.url_photo,
                              o.libelle_abrege AS groupe_abrege, o.libelle AS groupe,
                              o.couleur_associee AS groupe_couleur
                       FROM deputes d LEFT JOIN organes o ON o.uid = d.groupe_ref
                       WHERE ltrim(d.circo_numero::text, '0') = :n
                         AND lower(unaccent(d.circo_departement)) LIKE '%' || :dept || '%'
                       ORDER BY d.actif DESC LIMIT 1""",
                    n=str(int(m.group(1))), dept=m.group(2).strip()[:24])
                dep = rows[0] if rows else None
        await trace("tool_result", {"tool": "sql_depute_lookup", "count": 1 if dep else 0,
                                    "preview": (dep or {}).get("nom_complet", "aucun député trouvé")})

    if dep is None:
        await _mcp_tool(trace, "mcp-moulineuse", "HEMI_MCP_MOULINEUSE_URL",
                        "search_depute", {"query": query[:120]})
        summary = ("Je n'ai pas identifié ce député dans le registre officiel de la 17e "
                   "législature. Donnez son nom complet ou sa circonscription "
                   "(département + numéro) : je pourrai alors afficher sa fiche.")
        await trace("draft_chunk", {"text": summary, "stream_id": f"draft-{td.corr_id}", "done": False})
        await trace("draft_chunk", {"text": "", "stream_id": f"draft-{td.corr_id}", "done": True})
        return {"depute": {}, "deputes": [], "votes_recents": [], "stats": None,
                "sources": [{"title": "Open data AN — Acteurs et mandats",
                             "url": "https://data.assemblee-nationale.fr/acteurs/deputes-en-exercice",
                             "snippet": "Registre officiel des députés (Licence Ouverte).",
                             "source": "data.assemblee-nationale.fr", "reliability": "A",
                             "score": 1.0}],
                "summary": summary}

    await progress(0.45, "votes récents")
    await trace("tool_call", {"tool": "sql_depute_votes_recents", "params": {"uid": dep["uid"]},
                              "mcp_server": "opendata-an (base locale)"})
    votes_recents = await _rows(
        """SELECT s.titre AS scrutin_titre, s.date_scrutin::text AS date, v.position, s.url_an
           FROM votes v JOIN scrutins s ON s.uid = v.scrutin_uid
           WHERE v.acteur_ref = :a ORDER BY s.date_scrutin DESC LIMIT 5""",
        a=dep["uid"])
    await trace("tool_result", {"tool": "sql_depute_votes_recents", "count": len(votes_recents)})

    await progress(0.65, "statistiques de participation")
    await trace("tool_call", {"tool": "sql_depute_stats", "params": {"uid": dep["uid"]},
                              "mcp_server": "opendata-an (base locale)"})
    stats = None
    try:
        # Dénominateur = scrutins depuis le PREMIER vote du député (un élu de
        # partielle ne doit pas être rapporté aux 8000 scrutins de la législature).
        prows = await _rows(
            """WITH premier AS (
                 SELECT min(s.date_scrutin) AS d0
                 FROM votes v JOIN scrutins s ON s.uid = v.scrutin_uid
                 WHERE v.acteur_ref = :a)
               SELECT (SELECT count(DISTINCT scrutin_uid) FROM votes WHERE acteur_ref = :a
                         AND position <> 'nonVotant')::float
                      / greatest((SELECT count(*) FROM scrutins, premier
                                  WHERE date_scrutin >= premier.d0), 1) AS participation""",
            a=dep["uid"])
        lrows = await _rows(
            """WITH grp AS (
                 SELECT scrutin_uid, position,
                        row_number() OVER (PARTITION BY scrutin_uid ORDER BY count(*) DESC) AS rn
                 FROM votes WHERE groupe_ref = :g GROUP BY scrutin_uid, position)
               SELECT avg((v.position = g.position)::int) AS loyaute
               FROM votes v JOIN grp g ON g.scrutin_uid = v.scrutin_uid AND g.rn = 1
               WHERE v.acteur_ref = :a""",
            a=dep["uid"], g=dep.get("groupe_ref"))
        participation = round(float(prows[0]["participation"] or 0) * 100, 1) if prows else None
        loyaute = (round(float(lrows[0]["loyaute"]) * 100, 1)
                   if lrows and lrows[0]["loyaute"] is not None else None)
        if participation is not None:
            stats = {"participation": participation, "loyaute": loyaute}
        await trace("tool_result", {"tool": "sql_depute_stats",
                                    "preview": f"participation {participation}% · "
                                               f"votes avec son groupe {loyaute}%"})
    except Exception:  # noqa: BLE001 - les stats sont un bonus, jamais bloquantes
        await trace("tool_result", {"tool": "sql_depute_stats", "error": "stats indisponibles"})

    depute_block = {
        "uid": dep["uid"], "nom_complet": dep.get("nom_complet", ""),
        "groupe": dep.get("groupe") or "", "groupe_abrege": dep.get("groupe_abrege") or "",
        "groupe_couleur": dep.get("groupe_couleur") or "",
        "circo": f"{dep.get('circo_departement') or '?'} — {dep.get('circo_numero') or '?'}e circonscription",
        "actif": bool(dep.get("actif")), "url_an": dep.get("url_an", ""),
        "url_photo": dep.get("url_photo", ""),
    }
    sources = [{"title": f"Fiche officielle de {dep.get('nom_complet')} — assemblee-nationale.fr",
                "url": dep.get("url_an", ""), "snippet": "Fiche député officielle.",
                "source": "assemblee-nationale.fr", "reliability": "A", "score": 1.0},
               {"title": "Open data AN — Acteurs et mandats (AMO)",
                "url": "https://data.assemblee-nationale.fr/acteurs/deputes-en-exercice",
                "snippet": "Registre officiel des députés et mandats (Licence Ouverte).",
                "source": "data.assemblee-nationale.fr", "reliability": "A", "score": 1.0}]
    if circo_note:
        sources.append({"title": "Contours des circonscriptions législatives — data.gouv.fr",
                        "url": "https://www.data.gouv.fr/fr/datasets/contours-geographiques-des-circonscriptions-legislatives/",
                        "snippet": "Localisation de l'adresse via l'API Adresse (BAN) puis "
                                   "les contours officiels des circonscriptions.",
                        "source": "data.gouv.fr", "reliability": "A", "score": 1.0})

    await progress(0.85, "rédaction de la synthèse")
    brief = (f"{td.instruction}\n\nDONNÉES OFFICIELLES :\n"
             + (f"- {circo_note}\n" if circo_note else "")
             + f"- {depute_block['nom_complet']}, groupe : {depute_block['groupe'] or 'non renseigné'}, "
             f"circonscription : {depute_block['circo']}, "
             f"{'mandat en cours' if depute_block['actif'] else 'ANCIEN député (mandat terminé)'}\n")
    if stats:
        brief += (f"- Présence dans les scrutins publics depuis son premier vote enregistré : "
                  f"{stats['participation']} % ; votes alignés avec la majorité de son "
                  f"groupe : {stats.get('loyaute')} % (calculs locaux — les scrutins publics "
                  "ne reflètent pas tout le travail parlementaire : commissions, "
                  "amendements, rapports)\n")
    for v in votes_recents[:3]:
        brief += f"- Vote {v['position']} le {v['date']} : {v['scrutin_titre'][:90]}\n"
    brief += ("\nRédige une présentation factuelle et neutre en 3-5 phrases avec ces données "
              "EXACTES. Aucun jugement de valeur, aucune donnée inventée. La date et les "
              "circonstances de son élection NE SONT PAS dans les données : ne les affirme "
              "pas. Si tu cites la présence dans les scrutins, précise que cela ne mesure "
              "pas tout le travail parlementaire.")
    summary = await _stream_summary(td, trace, _with_attachments(td, _with_upstream(td, brief)))
    if not summary.strip():
        summary = (f"{depute_block['nom_complet']} — {depute_block['groupe']} — "
                   f"{depute_block['circo']}"
                   f"{'' if depute_block['actif'] else ' (ancien député)'}.")
    await progress(0.95, "terminé")
    return {"depute": depute_block, "deputes": [], "votes_recents": votes_recents,
            "stats": stats, "sources": sources, "summary": summary}


def _flatten_actes(actes, out: list) -> None:
    """Aplatis l'arbre des actes officiel en étapes datées (défensif).

    Structure réelle (vérifiée en base) : la racine est `{"acteLegislatif": [...]}`
    et chaque nœud porte ses enfants sous `actesLegislatifs.acteLegislatif`
    (dict OU liste — listes-à-1-élément sérialisées en objet).
    """
    if isinstance(actes, dict) and "acteLegislatif" in actes:
        actes = actes["acteLegislatif"]
    if isinstance(actes, dict):
        actes = [actes]
    if not isinstance(actes, list):
        return
    for a in actes:
        if not isinstance(a, dict):
            continue
        libelle = ""
        la = a.get("libelleActe")
        if isinstance(la, dict):
            libelle = la.get("nomCanonique") or la.get("libelleCourt") or ""
        elif isinstance(la, str):
            libelle = la
        code = str(a.get("codeActe") or "")
        d = a.get("dateActe") or a.get("date") or ""
        chambre = ("Assemblée nationale" if code.startswith("AN")
                   else "Sénat" if code.startswith("SN")
                   else "CMP" if code.startswith("CMP")
                   else "Conseil constitutionnel" if code.startswith("CC")
                   else "Promulgation" if "PROM" in code
                   else "")
        if libelle and d:
            out.append({"date": str(d)[:10], "libelle": libelle, "chambre": chambre,
                        "code": code})
        children = a.get("actesLegislatifs")
        if children is not None:
            inner = children.get("acteLegislatif") if isinstance(children, dict) else children
            _flatten_actes(inner, out)


async def lois_handler(td, progress, trace) -> dict:
    """AGENT LOIS : retrouve un dossier législatif et reconstruit son parcours (navette)."""
    query = td.params.get("text") or td.instruction
    await trace("thinking", {"text": "Je cherche le dossier législatif dans les données "
                                     "officielles, pour reconstruire le parcours du texte."})
    await progress(0.2, "recherche du dossier législatif")

    dossier = None
    if not _tool_paused(td, "sql_dossier_search"):
        await trace("tool_call", {"tool": "sql_dossier_search", "params": {"query": query[:100]},
                                  "mcp_server": "opendata-an (base locale)"})
        rows = await _rows(
            """SELECT *, similarity(titre_normalise, :q) AS sim FROM dossiers
               ORDER BY sim DESC LIMIT 1""",
            q=_residue(query))
        if rows and (rows[0].get("sim") or 0) > 0.05:
            dossier = rows[0]
        await trace("tool_result", {"tool": "sql_dossier_search", "count": 1 if dossier else 0,
                                    "preview": (dossier or {}).get("titre", "aucun dossier trouvé")[:160]})

    # Enrichissement pédagogique optionnel via MCP (Légifrance / moulineuse).
    await _mcp_tool(trace, "mcp-moulineuse", "HEMI_MCP_MOULINEUSE_URL",
                    "search_dossier", {"query": query[:120]})

    if dossier is None:
        summary = ("Je n'ai pas trouvé ce texte parmi les dossiers législatifs suivis. "
                   "Donnez le titre exact ou le thème principal du texte : je pourrai "
                   "alors retracer son parcours (dépôt, lectures, navette, promulgation).")
        await trace("draft_chunk", {"text": summary, "stream_id": f"draft-{td.corr_id}", "done": False})
        await trace("draft_chunk", {"text": "", "stream_id": f"draft-{td.corr_id}", "done": True})
        return {"dossier": {}, "etapes": [], "articles": [],
                "sources": [{"title": "Open data AN — Dossiers législatifs",
                             "url": "https://data.assemblee-nationale.fr/travaux-parlementaires/dossiers-legislatifs",
                             "snippet": "Jeu de données officiel des dossiers législatifs.",
                             "source": "data.assemblee-nationale.fr", "reliability": "A",
                             "score": 1.0}],
                "summary": summary}

    await progress(0.5, "reconstruction du parcours du texte")
    await trace("tool_call", {"tool": "sql_dossier_timeline", "params": {"uid": dossier["uid"]},
                              "mcp_server": "opendata-an (base locale)"})
    flat: list[dict] = []
    _flatten_actes(dossier.get("actes"), flat)
    flat.sort(key=lambda e: e["date"])
    # Jalons citoyens : un dossier riche compte >100 micro-actes — on garde le
    # DERNIER acte daté de chaque phase (préfixe codeActe à 2 segments : AN1-COM,
    # AN1-DBTS, SN1-…), dans l'ordre chronologique, plafonné à 12 étapes.
    if len(flat) > 12:
        par_phase: dict[str, dict] = {}
        for e in flat:
            phase = "-".join((e.get("code") or "?").split("-")[:2])
            par_phase[phase] = e
        flat = sorted(par_phase.values(), key=lambda e: e["date"])[-12:]
    promulgue = any("PROM" in (e.get("code") or "") for e in flat)
    etapes = []
    for i, e in enumerate(flat):
        etapes.append({"date": e["date"], "libelle": e["libelle"], "chambre": e["chambre"],
                       "statut": "done" if i < len(flat) - 1 else ("done" if promulgue else "current")})
    if not promulgue:
        etapes.append({"date": "", "libelle": "Étapes suivantes de la procédure "
                                              "(navette, éventuelle CMP, promulgation)",
                       "chambre": "", "statut": "pending"})
    await trace("tool_result", {"tool": "sql_dossier_timeline", "count": len(etapes),
                                "preview": f"{len(flat)} étapes ; "
                                           f"{'texte promulgué' if promulgue else 'procédure en cours'}"})

    statut = dossier.get("statut") or ("Loi promulguée" if promulgue else "En cours d'examen")
    dossier_block = {"titre": dossier.get("titre", ""), "uid": dossier.get("uid", ""),
                     "statut": statut, "url_an": dossier.get("url_an", "")}
    sources = [{"title": f"Dossier législatif — assemblee-nationale.fr",
                "url": dossier.get("url_an", ""), "snippet": (dossier.get("titre") or "")[:160],
                "source": "assemblee-nationale.fr", "reliability": "A", "score": 1.0},
               {"title": "Open data AN — Dossiers législatifs",
                "url": "https://data.assemblee-nationale.fr/travaux-parlementaires/dossiers-legislatifs",
                "snippet": "Jeu de données officiel des dossiers législatifs (Licence Ouverte).",
                "source": "data.assemblee-nationale.fr", "reliability": "A", "score": 1.0}]

    await progress(0.85, "rédaction de la synthèse")
    steps_brief = "\n".join(f"- {e['date']} : {e['libelle']} ({e['chambre']})"
                            for e in flat[-6:])
    brief = (f"{td.instruction}\n\nDONNÉES OFFICIELLES (dossier législatif) :\n"
             f"- Titre : {dossier_block['titre']}\n- Statut : {statut}\n"
             f"Dernières étapes :\n{steps_brief}\n\n"
             "Rédige 3-5 phrases factuelles et neutres : où en est ce texte, quelles sont les "
             "étapes passées et celles qui restent. RÈGLE ABSOLUE : un texte adopté par une "
             "chambre n'est PAS encore une loi (navette, éventuelle CMP, Conseil "
             "constitutionnel, promulgation). N'affirme jamais qu'un texte est une loi si le "
             "statut ne dit pas « promulguée ».")
    summary = await _stream_summary(td, trace, _with_attachments(td, _with_upstream(td, brief)))
    if not summary.strip():
        summary = (f"{dossier_block['titre']} — statut : {statut}. "
                   f"{len(flat)} étapes enregistrées à ce jour.")
    await progress(0.95, "terminé")
    return {"dossier": dossier_block, "etapes": etapes, "articles": [],
            "sources": sources, "summary": summary}


# Catalogue des handlers — la clé correspond à HEMI_AGENT_ID.
DISCIPLINES = {
    "agent_votes": votes_handler,
    "agent_deputes": deputes_handler,
    "agent_lois": lois_handler,
}
