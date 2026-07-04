"""Schémas d'artefacts : enveloppe de référence + payloads typés par discipline.

Contrat unique partagé worker/orchestrateur. Un output d'agent est validé contre
le schéma de sa discipline À LA FRONTIÈRE (avant publication) : un champ manquant
ou mal nommé échoue vite (`ValidationError`) au lieu de se perdre silencieusement
en aval — c'est ce qui tue le « téléphone arabe » entre agents enchaînés.
"""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    """Horodatage UTC courant."""
    return datetime.now(timezone.utc)


class Source(BaseModel):
    """Une source de recherche fédérée (corpus interne)."""

    title: str
    url: str = ""
    snippet: str = ""
    source: str = "?"
    reliability: str = "C"
    score: float = 0.0


class ScrutinBlock(BaseModel):
    """Un scrutin public de l'Assemblée nationale (décomptes officiels)."""

    uid: str = ""
    numero: int | None = None
    date: str = ""
    titre: str = ""
    sort: str = ""
    pour: int = 0
    contre: int = 0
    abstentions: int = 0
    non_votants: int = 0
    url_an: str = ""
    url_data: str = ""


class PositionDepute(BaseModel):
    """Position d'un député sur un scrutin ('absent' = n'apparaît pas dans le fichier)."""

    acteur_ref: str = ""
    nom: str = ""
    groupe: str = ""
    position: str = ""                     # pour | contre | abstention | nonVotant | absent


class GroupeVotes(BaseModel):
    """Décompte des positions d'un groupe politique sur un scrutin."""

    nom: str = ""
    abrege: str = ""
    couleur: str = ""
    pour: int = 0
    contre: int = 0
    abstention: int = 0
    nonVotant: int = 0
    position_majoritaire: str = ""


class VotesPayload(BaseModel):
    """Sortie AGENT VOTES : scrutin + détail par groupe + position d'un député."""

    model_config = ConfigDict(extra="forbid")   # un champ inattendu = bug à corriger, pas à ignorer
    scrutin: ScrutinBlock = Field(default_factory=ScrutinBlock)
    position_depute: PositionDepute | None = None
    groupes: list[GroupeVotes] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    summary: str = ""


class DeputeBlock(BaseModel):
    """Fiche d'identité parlementaire d'un député (registre officiel AMO)."""

    uid: str = ""
    nom_complet: str = ""
    groupe: str = ""
    groupe_abrege: str = ""
    groupe_couleur: str = ""
    circo: str = ""
    actif: bool = True
    url_an: str = ""
    url_photo: str = ""


class VoteRecent(BaseModel):
    """Un vote récent d'un député."""

    scrutin_titre: str = ""
    date: str = ""
    position: str = ""
    url_an: str = ""


class DeputeStats(BaseModel):
    """Statistiques locales calculées sur les scrutins publics (pas un jugement)."""

    participation: float | None = None
    loyaute: float | None = None


class DeputesPayload(BaseModel):
    """Sortie AGENT DÉPUTÉS : fiche + votes récents + statistiques."""

    model_config = ConfigDict(extra="forbid")
    depute: DeputeBlock = Field(default_factory=DeputeBlock)
    deputes: list[DeputeBlock] = Field(default_factory=list)
    votes_recents: list[VoteRecent] = Field(default_factory=list)
    stats: DeputeStats | None = None
    sources: list[Source] = Field(default_factory=list)
    summary: str = ""


class DossierBlock(BaseModel):
    """Un dossier législatif (statut EXACT — un texte adopté n'est pas une loi)."""

    titre: str = ""
    uid: str = ""
    statut: str = ""
    url_an: str = ""


class EtapeLoi(BaseModel):
    """Une étape du parcours d'un texte (navette parlementaire)."""

    date: str = ""
    libelle: str = ""
    chambre: str = ""
    statut: str = "done"                   # done | current | pending


class LoisPayload(BaseModel):
    """Sortie AGENT LOIS : dossier + timeline du parcours du texte."""

    model_config = ConfigDict(extra="forbid")
    dossier: DossierBlock = Field(default_factory=DossierBlock)
    etapes: list[EtapeLoi] = Field(default_factory=list)
    articles: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    summary: str = ""


# Discipline (= HEMI_AGENT_ID) → schéma de payload. Les agents A2A externes dont
# on ne possède pas le schéma ne figurent pas ici → passage tel quel (cf. validate_output).
PAYLOAD_SCHEMAS: dict[str, type[BaseModel]] = {
    "agent_votes": VotesPayload,
    "agent_deputes": DeputesPayload,
    "agent_lois": LoisPayload,
}
SCHEMA_VERSION = 1


class ArtifactEnvelope(BaseModel):
    """Enveloppe persistée dans l'Object Store (clé = corr_id de la tâche productrice)."""

    artifact_id: str                       # = corr_id de la tâche productrice
    agent_type: str
    schema_name: str                       # ex. "mi_search"
    schema_version: int = SCHEMA_VERSION
    created_at: datetime = Field(default_factory=_now)
    payload: dict[str, Any]                # payload validé, sérialisé en dict
    preview: str = ""                      # extrait texte court (summary) pour LLM / repli A2A

    def to_bytes(self) -> bytes:
        """Sérialise l'enveloppe pour l'Object Store."""
        return self.model_dump_json().encode()

    @classmethod
    def from_bytes(cls, data: bytes) -> "ArtifactEnvelope":
        """Reconstruit une enveloppe depuis l'Object Store."""
        return cls.model_validate_json(data)


def validate_output(agent_type: str, output: dict | None) -> tuple[dict, str]:
    """Valide l'output d'un handler contre le schéma de sa discipline → (payload, preview).

    Discipline connue : validation STRICTE (`extra="forbid"`) — une `ValidationError`
    remonte et fait échouer la tâche à la source. Discipline inconnue (agent A2A
    externe) : passage tel quel, avec un preview best-effort sur `summary`/`text`.
    """
    schema = PAYLOAD_SCHEMAS.get(agent_type)
    if schema is None:
        o = dict(output or {})
        return o, str(o.get("summary") or o.get("text") or "")[:4000]
    payload = schema.model_validate(output or {}).model_dump()
    return payload, str(payload.get("summary") or "")[:4000]
