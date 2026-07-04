"""Analyse et normalisation des alertes événementielles STAM (alert-event).

Fournit :
- `sidc_for` : génère un SIDC MIL-STD-2525C 15 caractères depuis source/type objet.
- `parse_alert_event` : normalise un payload alert-event brut vers un dict structuré
  utilisé par le gateway (fan-out multi-channel, sommaire LLM, affichage géo).
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# SIDC MIL-STD-2525C
# ---------------------------------------------------------------------------

_NAVAL_TYPES = frozenset({"cargo", "tanker", "fishing", "ship", "vessel", "boat"})
_AIR_TYPES   = frozenset({"aircraft", "helicopter", "drone", "uav"})
_NAVAL_SRC   = frozenset({"ais"})


def sidc_for(source: str, obj_type: str) -> str:
    """Retourne un SIDC MIL-STD-2525C de 15 caractères pour un objet détecté.

    Forme fixe : ``S{aff}{dim}P-----------`` (toujours 15 chars).
    Affiliation ``U`` (inconnu) par défaut. Dimension :

    - ``S`` (surface navale) : source AIS ou type contenant cargo/tanker/ship/…
    - ``A`` (aérien)         : type contenant aircraft/helicopter/drone/uav
    - ``G`` (sol)            : tous les autres (VEHICLE, IMINT par défaut)
    """
    src_lo  = (source or "").lower()
    type_lo = (obj_type or "").lower()

    if src_lo in _NAVAL_SRC or any(t in type_lo for t in _NAVAL_TYPES):
        dim = "S"
    elif any(t in type_lo for t in _AIR_TYPES):
        dim = "A"
    else:
        dim = "G"

    sidc = f"SU{dim}P-----------"
    assert len(sidc) == 15, f"SIDC invalide ({len(sidc)} chars) : {sidc!r}"
    return sidc


# ---------------------------------------------------------------------------
# Helpers internes
# ---------------------------------------------------------------------------

_SEVERITY_MAP: dict[str, str] = {
    "CRITICAL": "critical",
    "HIGH":     "warning",
}


def _severity(priority: str | None) -> str:
    """Convertit la priorité STAM en sévérité Hémicycle (info/warning/critical)."""
    return _SEVERITY_MAP.get((priority or "").upper(), "info")


def _extract_position(obj: dict, sr: dict) -> tuple[float, float] | None:
    """Extrait (lat, lon) depuis un matchingObject, ou None si aucune position valide.

    Ordre de priorité :
    1. ``obj["coordinates"]`` → ``[lon, lat]``
    2. ``sr["location"]["coordinates"]`` → ``[lon, lat]``
    3. ``sr["lat"]`` + ``sr["lon"]``
    """
    def _is_valid_pair(v: object) -> bool:
        return (
            isinstance(v, list)
            and len(v) == 2
            and all(isinstance(c, (int, float)) for c in v)
        )

    # 1. obj.coordinates
    coords = obj.get("coordinates")
    if _is_valid_pair(coords):
        return float(coords[1]), float(coords[0])  # type: ignore[index]

    # 2. sourceRecord.location.coordinates
    loc = sr.get("location")
    if isinstance(loc, dict):
        lc = loc.get("coordinates")
        if _is_valid_pair(lc):
            return float(lc[1]), float(lc[0])  # type: ignore[index]

    # 3. sourceRecord.lat / sourceRecord.lon
    lat = sr.get("lat")
    lon = sr.get("lon")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return float(lat), float(lon)

    return None


def _build_note(source: str, sr: dict) -> str:
    """Construit une note courte à partir des champs saillants du sourceRecord.

    Défensif : n'inclut que les clés effectivement présentes et valides.
    """
    parts: list[str] = []
    src_up = (source or "").upper()

    if src_up == "AIS":
        # Statut AIS + durée d'extinction
        status  = sr.get("aisStatus")
        off_min = sr.get("aisOffSinceMinutes")
        if status:
            if isinstance(off_min, (int, float)) and off_min > 0:
                parts.append(f"AIS {status} {int(off_min)} min")
            else:
                parts.append(f"AIS {status}")
        zone = sr.get("zone")
        if zone:
            parts.append(str(zone))
    else:
        # IMINT / autres
        zone = sr.get("zone")
        if zone:
            parts.append(str(zone))
        conf = sr.get("confidence")
        if conf is not None:
            try:
                parts.append(f"conf. {float(conf):.0%}")
            except (TypeError, ValueError):
                pass
        meta = sr.get("metadata")
        if isinstance(meta, dict):
            platform = meta.get("platform")
            if platform:
                parts.append(str(platform))

    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Parseur principal
# ---------------------------------------------------------------------------

def parse_alert_event(event: dict) -> dict:
    """Normalise un payload alert-event STAM brut vers un dict structuré.

    Retourne ::

        {
            "title":       str,
            "severity":    "info" | "warning" | "critical",
            "priority":    str,
            "sources":     list[str],   # sources uniques, ordre d'apparition
            "match_count": int,         # objets effectivement placés (avec position)
            "locations":   list[dict],  # {lat, lon, name, note, sidc[, course, speed]}
            "polygons":    list[dict],  # [] (réservé pour extension future)
            "text":        str,         # résumé markdown déterministe
        }

    Clés de chaque ``location`` (miroir du type frontend ``Loc``) :
    ``lat``, ``lon``, ``name``, ``note``, ``sidc``, ``course``?, ``speed``?.

    Entièrement défensif : les clés manquantes ou les types inattendus sont ignorés.
    """
    title    = event.get("title") or "Alerte"
    priority = str(event.get("priority") or "")
    severity = _severity(priority)

    seen_sources: set[str] = set()
    sources:      list[str] = []
    locations:    list[dict] = []
    text_items:   list[str] = []  # fragments pour le résumé markdown

    query_results = event.get("queryResults") or []
    if not isinstance(query_results, list):
        query_results = []

    for qr in query_results:
        if not isinstance(qr, dict):
            continue
        source = str(qr.get("source") or "")
        if source and source not in seen_sources:
            seen_sources.add(source)
            sources.append(source)

        matching = qr.get("matchingObjects") or []
        if not isinstance(matching, list):
            continue

        for obj in matching:
            if not isinstance(obj, dict):
                continue
            sr = obj.get("sourceRecord")
            if not isinstance(sr, dict):
                sr = {}

            pos = _extract_position(obj, sr)
            if pos is None:
                continue  # aucune position exploitable → objet ignoré

            lat, lon    = pos
            obj_type    = str(obj.get("type") or sr.get("objectType") or "")
            sr_name     = sr.get("name")         # nom lisible (ex. nom de navire AIS)
            name        = sr_name or obj.get("id") or "?"
            note        = _build_note(source, sr)
            sidc        = sidc_for(source, obj_type)

            loc: dict = {
                "lat":  lat,
                "lon":  lon,
                "name": str(name),
                "note": note,
                "sidc": sidc,
                # Type brut de la détection (aircraft/helicopter/truck/vehicle/…) — conservé
                # pour que l'agent C2 le mappe vers son `obs_type` (PLANE/HELICOPTER/…) sans
                # avoir à le ré-inférer depuis le SIDC ou la prose.
                "type": obj_type,
            }
            # course / speed : AIS uniquement, valeurs numériques
            course = sr.get("course")
            if isinstance(course, (int, float)):
                loc["course"] = float(course)
            speed = sr.get("speed")
            if isinstance(speed, (int, float)):
                loc["speed"] = float(speed)

            locations.append(loc)

            # Fragment de texte : TYPE « nom réel » (note) ou TYPE (note) si pas de nom réel
            type_str = obj_type.upper() if obj_type else "?"
            if sr_name and note:
                text_items.append(f"1× {type_str} « {sr_name} » ({note})")
            elif sr_name:
                text_items.append(f"1× {type_str} « {sr_name} »")
            elif note:
                text_items.append(f"1× {type_str} ({note})")
            else:
                text_items.append(f"1× {type_str}")

    match_count = len(locations)
    src_str     = ", ".join(sources) if sources else "?"
    header      = f"**{title}** — {match_count} détection(s) ({src_str})."
    text        = f"{header} {', '.join(text_items)}." if text_items else header

    return {
        "title":       title,
        "severity":    severity,
        "priority":    priority,
        "sources":     sources,
        "match_count": match_count,
        "locations":   locations,
        "polygons":    [],
        "text":        text,
    }
