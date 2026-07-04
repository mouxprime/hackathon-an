"""Probe A2A — validation et tests d'un agent A2A externe.

Module pur (pas d'I/O Postgres, pas de NATS) : il prend une URL d'agent A2A
v1.0 et retourne un rapport structuré. Utilisé par :
  - le gateway (endpoint unifié `POST /api/agents/register` + `/api/agents/{id}/probe`)
  - les outils MCP `register_agent` / `probe_agent` (côté OpenCode)
  - les tests d'acceptance manuels

Le rapport contient une liste de `Check` (pass/warn/fail) avec un détail
humain-lisible. Chaque appel du probe peut servir de gate pour bloquer
l'enrôlement d'un agent qui n'est pas conforme.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from .client import A2AClient

Status = Literal["pass", "warn", "fail"]


@dataclass
class Check:
    """Un check unitaire — un nom, un statut, et un détail humain."""

    name: str
    status: Status
    detail: str = ""
    duration_ms: float = 0.0


@dataclass
class ProbeReport:
    """Rapport complet d'un probe d'agent A2A."""

    base_url: str
    ok: bool                                           # vrai si zéro check `fail`
    agent_card: dict[str, Any] | None = None
    latency_ms: float = 0.0
    checks: list[Check] = field(default_factory=list)
    sample_events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Sérialisation JSON-safe (pour persistance et SSE)."""
        return {
            "base_url": self.base_url, "ok": self.ok,
            "agent_card": self.agent_card, "latency_ms": self.latency_ms,
            "checks": [asdict(c) for c in self.checks],
            "sample_events": self.sample_events,
        }


# ---------------------------------------------------------------------------
# Validation statique d'une Agent Card
# ---------------------------------------------------------------------------

# Champs requis A2A v1.0 (spec a2a-protocol.org).
_CARD_REQUIRED_FIELDS = ["name", "description", "url", "version", "capabilities", "skills",
                          "defaultInputModes", "defaultOutputModes"]


def validate_agent_card(card: dict[str, Any]) -> list[Check]:
    """Vérifie qu'une Agent Card a tous les champs requis A2A v1.0.

    Retourne une liste de `Check` — un par règle. `fail` = champ manquant ou
    incompatible spec ; `warn` = recommandé mais pas obligatoire.
    """
    checks: list[Check] = []
    for field_name in _CARD_REQUIRED_FIELDS:
        if field_name not in card:
            checks.append(Check(f"card-field-{field_name}", "fail",
                                 f"champ obligatoire absent : {field_name}"))
        else:
            checks.append(Check(f"card-field-{field_name}", "pass",
                                 f"{field_name} présent"))

    # Capabilities : objet avec au moins `streaming` (bool).
    caps = card.get("capabilities") or {}
    if not isinstance(caps, dict):
        checks.append(Check("card-capabilities", "fail",
                             "capabilities doit être un objet"))
    elif "streaming" not in caps:
        checks.append(Check("card-capabilities", "warn",
                             "capabilities.streaming non déclaré (recommandé)"))
    else:
        checks.append(Check("card-capabilities", "pass",
                             f"streaming={caps.get('streaming')}"))

    # Skills : liste non vide, chaque skill avec id + description.
    skills = card.get("skills") or []
    if not isinstance(skills, list) or len(skills) == 0:
        checks.append(Check("card-skills", "fail",
                             "skills doit être une liste non vide"))
    else:
        for i, sk in enumerate(skills):
            missing = [f for f in ("id", "name", "description") if f not in sk]
            if missing:
                checks.append(Check(f"card-skill-{i}", "fail",
                                     f"skill[{i}] manque : {', '.join(missing)}"))
            else:
                checks.append(Check(f"card-skill-{i}", "pass",
                                     f"skill[{i}].id={sk['id']}"))

    return checks


# ---------------------------------------------------------------------------
# Probe live — discover + roundtrip
# ---------------------------------------------------------------------------

async def probe_agent(base_url: str, *, ping_text: str = "Ping. Réponds OK.",
                       per_skill: bool = True, latency_runs: int = 0,
                       latency_threshold_s: float | None = None,
                       max_events: int = 10) -> ProbeReport:
    """Probe complet d'un agent A2A.

    Étapes :
      1. Discover : GET `/.well-known/agent-card.json` → parse + validation.
      2. Roundtrip principal : POST `message/send` puis SSE `tasks/{id}/stream`
         jusqu'à `result` ou `done`. Mesure la latence wall-clock.
      3. (Si `per_skill`) un roundtrip par skill déclaré dans la card.
      4. (Si `latency_runs > 0`) `latency_runs` roundtrips supplémentaires pour
         calculer une p95 (en fait médiane sur 5 par défaut — robuste, suffisant
         à l'échelle d'un onboarding).

    Le rapport `ok` n'est vrai que si **aucun** check n'est `fail`.
    """
    report = ProbeReport(base_url=base_url, ok=False)
    client = A2AClient(base_url)

    # --- 1. Discover ----
    t0 = time.monotonic()
    try:
        card = await client.discover()
        card_dict = card.model_dump()
        report.agent_card = card_dict
        report.checks.append(Check("discover", "pass",
                                    f"card récupérée ({len(card_dict.get('skills', []))} skills)",
                                    duration_ms=(time.monotonic() - t0) * 1000))
    except Exception as e:  # noqa: BLE001
        report.checks.append(Check("discover", "fail",
                                    f"impossible de fetch l'Agent Card : {e}",
                                    duration_ms=(time.monotonic() - t0) * 1000))
        report.ok = False
        return report

    # --- 2. Validation statique de la card ----
    report.checks.extend(validate_agent_card(card_dict))

    # --- 3. Roundtrip "ping" principal ----
    # On ne coupe pas le stream avant le `result` — `max_events` ne borne que
    # ce qu'on garde dans `sample_events` pour le rapport.
    t0 = time.monotonic()
    try:
        events: list[dict] = []
        final: dict | None = None
        async for evt in client.send_and_stream(ping_text):
            events.append(evt)
            if evt["event"] == "result":
                final = evt["data"]
            if evt["event"] == "done":
                break
        latency_ms = (time.monotonic() - t0) * 1000
        report.latency_ms = latency_ms
        report.sample_events = events[:max_events]
        if final and (final.get("status", {}).get("state") in ("completed", "working")):
            report.checks.append(Check("roundtrip-ping", "pass",
                                        f"task {final.get('status', {}).get('state')} en {latency_ms:.0f}ms",
                                        duration_ms=latency_ms))
        else:
            report.checks.append(Check("roundtrip-ping", "fail",
                                        f"task non complétée : {final}",
                                        duration_ms=latency_ms))
    except Exception as e:  # noqa: BLE001
        report.checks.append(Check("roundtrip-ping", "fail",
                                    f"erreur SSE/JSON-RPC : {e}",
                                    duration_ms=(time.monotonic() - t0) * 1000))

    # --- 4. Test par skill (optionnel) ----
    if per_skill:
        for sk in card_dict.get("skills", []):
            skill_id = sk.get("id", "?")
            prompt = f"Démo de la skill {skill_id} : {sk.get('description', '')}"
            t0 = time.monotonic()
            try:
                final = None
                async for evt in client.send_and_stream(prompt):
                    if evt["event"] == "result":
                        final = evt["data"]
                duration = (time.monotonic() - t0) * 1000
                state = (final or {}).get("status", {}).get("state") if final else None
                if state in ("completed", "working"):
                    report.checks.append(Check(f"skill-{skill_id}", "pass",
                                                f"state={state} ({duration:.0f}ms)",
                                                duration_ms=duration))
                else:
                    report.checks.append(Check(f"skill-{skill_id}", "fail",
                                                f"skill ne répond pas correctement : {final}",
                                                duration_ms=duration))
            except Exception as e:  # noqa: BLE001
                report.checks.append(Check(f"skill-{skill_id}", "fail",
                                            f"erreur : {e}",
                                            duration_ms=(time.monotonic() - t0) * 1000))

    # --- 5. Latence p95 (optionnel) ----
    if latency_runs > 0:
        durations: list[float] = []
        for _ in range(latency_runs):
            t0 = time.monotonic()
            try:
                async for evt in client.send_and_stream("Ping"):
                    if evt["event"] == "result":
                        break
                durations.append((time.monotonic() - t0) * 1000)
            except Exception:
                durations.append(float("inf"))
        durations.sort()
        # p95 sur N runs : index = ceil(0.95 * N) - 1
        p95_idx = max(0, int(round(0.95 * latency_runs)) - 1)
        p95_ms = durations[p95_idx]
        if latency_threshold_s is not None and p95_ms > latency_threshold_s * 1000:
            report.checks.append(Check("latency-p95", "fail",
                                        f"p95={p95_ms:.0f}ms > seuil {latency_threshold_s*1000:.0f}ms"))
        else:
            report.checks.append(Check("latency-p95", "pass",
                                        f"p95={p95_ms:.0f}ms sur {latency_runs} runs"))

    # Verdict global : zéro fail = ok.
    report.ok = all(c.status != "fail" for c in report.checks)
    return report


# Utilitaire CLI/test : exécution synchrone.
def probe_agent_sync(base_url: str, **kwargs) -> ProbeReport:
    """Wrapper synchrone pour appels depuis un contexte non-asyncio (CLI)."""
    return asyncio.run(probe_agent(base_url, **kwargs))
