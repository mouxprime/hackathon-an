"""Client LLM Hémicycle — bascule sur le framework **pydantic-ai**.

Avant : ce module POSTait vers le pool `llm-stub` (`/v1/complete[-stream]`, contrat
custom purpose/priority). Désormais il pilote un **Agent pydantic-ai** parlant
directement au vLLM OpenAI-compatible (qwen3.6-35B-FP8) — même framework que les
sous-agents A2A de `agents/_utils`.

La **surface publique est INCHANGÉE** (`complete` / `complete_usage` /
`complete_stream`, mêmes signatures et mêmes formes de retour) : les 9 sites
d'appel (orchestrateur, gateway, sub-agents) ne bougent pas. On reproduit côté
client ce que le stub faisait : système localisé par purpose/lang, caps
`max_tokens` par purpose, hook `enable_thinking` (qwen `chat_template_kwargs`),
température 0.2, et le contrat de stream `{type: thinking|token|done|error}`.

⚠️ Aucun **tool** n'est défini (agent de pure complétion). Le bypass du pool
`llm-stub` supprime le scheduling priorité/admission (`priority` est conservé
dans la signature mais ignoré) ; réversible en repointant ce module sur le stub.
"""
from __future__ import annotations

import logging
import os
from typing import AsyncIterator

import httpx
from pydantic_ai import Agent
from pydantic_ai.messages import (
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from .config import settings

log = logging.getLogger("hemicycle.llm_client")

# --- Caps max_tokens + défauts <think> (repris à l'identique du llm-stub) -------
_MAX_TOKENS = int(os.environ.get("HEMI_LLM_MAX_TOKENS", "512"))
_PURPOSE_MAX_TOKENS = {
    "report-writing": int(os.environ.get("HEMI_LLM_MAX_TOKENS_REPORT", "4096")),
    "planning": int(os.environ.get("HEMI_LLM_MAX_TOKENS_PLANNING", "4096")),
}
_PLANNING_THINKING_MAX_TOKENS = int(
    os.environ.get("HEMI_LLM_MAX_TOKENS_PLANNING_THINKING", "8192")
)
# Purposes dont le <think> est coupé par défaut (latence + budget tokens).
_THINK_OFF_BY_DEFAULT = frozenset({"report-writing", "subagent-reasoning"})
_NO_THINK = os.environ.get("HEMI_LLM_NO_THINK", "0").strip().lower() not in ("", "0", "false")
_TEMPERATURE = 0.2
# Garde d'envoi des `chat_template_kwargs` : hook spécifique aux templates qwen/vLLM.
# NIM/mistral-large-3 renvoie 400 (« chat_template is not supported for Mistral
# tokenizers ») dès qu'on les envoie — et `_THINK_OFF_BY_DEFAULT` les mettrait sur le
# chemin chaud (workers + rapport). Off par défaut ; « on » uniquement pour un backend qwen.
_THINKING_KWARGS_ON = os.environ.get("HEMI_LLM_THINKING_KWARGS", "off").strip().lower() in (
    "1", "on", "true", "yes",
)


def _max_tokens_for(purpose: str, enable_thinking: bool | None) -> int:
    if purpose == "planning" and enable_thinking is True:
        return _PLANNING_THINKING_MAX_TOKENS
    return _PURPOSE_MAX_TOKENS.get(purpose, _MAX_TOKENS)


def _resolve_thinking(purpose: str, enable_thinking: bool | None) -> bool | None:
    """Même priorité que le stub : explicite > NO_THINK global > défaut par purpose
    > None (défaut du modèle qwen = think ON)."""
    if enable_thinking is True:
        return True
    if enable_thinking is False or _NO_THINK:
        return False
    if purpose in _THINK_OFF_BY_DEFAULT:
        return False
    return None


def _system(purpose: str, lang: str) -> str:
    if lang == "fr":
        return (
            f"Tu es « Hémicycle », l'assistant citoyen de l'Assemblée nationale (rôle : {purpose}). "
            "Tu expliques la vie parlementaire en français clair, neutre et pédagogique. "
            "Tu n'inventes jamais un vote, un chiffre ou une date : si une donnée manque, tu le dis."
        )
    return (
        f"You are Hémicycle, the citizen assistant for the French National Assembly (role: {purpose}). "
        "Respond in English, factually and neutrally. Never invent a vote, figure or date."
    )


def _model_settings(purpose: str, enable_thinking: bool | None) -> dict:
    ms: dict = {
        "max_tokens": _max_tokens_for(purpose, enable_thinking),
        "temperature": _TEMPERATURE,
    }
    et = _resolve_thinking(purpose, enable_thinking)
    if et is not None and _THINKING_KWARGS_ON:
        # Hook qwen3/vLLM : `enable_thinking` passe par `chat_template_kwargs`
        # (relayé tel quel via `extra_body`), pas comme champ direct du body.
        ms["extra_body"] = {"chat_template_kwargs": {"enable_thinking": et}}
    return ms


# --- Modèle pydantic-ai (singleton — réutilise le client HTTP/connexions) -------
_model: OpenAIChatModel | None = None


def _get_model() -> OpenAIChatModel:
    global _model
    if _model is None:
        _model = OpenAIChatModel(
            settings.llm_model,
            provider=OpenAIProvider(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key or "EMPTY",
                http_client=httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)),
            ),
        )
        log.info("pydantic-ai LLM model=%s base_url=%s", settings.llm_model, settings.llm_base_url)
    return _model


def _agent(purpose: str, lang: str) -> Agent:
    """Agent de complétion (AUCUN tool) au système localisé par purpose/lang."""
    return Agent(_get_model(), system_prompt=_system(purpose, lang))


def _prompt(prompt: str) -> str:
    return prompt or "(prompt vide — réponds brièvement.)"


def _estimate_tokens(text: str) -> int:
    """Estimation grossière ~4 caractères/token (repli quand l'usage est absent)."""
    return max(0, len(text or "") // 4)


def _usage_dict(result_or_run, prompt_text: str = "", output_text: str = "") -> dict:
    """Usage tokens (input/output) normalisé vers le contrat historique du stub.

    pydantic-ai 2.x expose `input_tokens` / `output_tokens` sur `RunUsage`. Si
    l'upstream (qwen/litellm) ne renvoie pas d'usage (→ 0), on ESTIME depuis la
    longueur prompt/sortie, exactement comme le faisait le llm-stub (« repli
    estimation si absent ») — l'orchestrateur s'en sert pour `context_tokens`
    (déclenchement de la compaction) : un 0 permanent désactiverait la compaction.
    """
    pt = ct = 0
    try:
        u = result_or_run.usage()
        pt = int(getattr(u, "input_tokens", 0) or 0)
        ct = int(getattr(u, "output_tokens", 0) or 0)
    except Exception:  # noqa: BLE001
        pt = ct = 0
    if pt == 0 and ct == 0:
        pt = _estimate_tokens(prompt_text) or 1
        ct = _estimate_tokens(output_text)
    return {"prompt_tokens": pt, "completion_tokens": ct}


# Backoff unique sur rate-limit (NIM renvoie 429 en rafale de démo) : un seul
# retry après une courte attente — au-delà, les replis déterministes des appelants
# prennent la main (jamais de boucle d'attente devant l'utilisateur).
_RATE_LIMIT_RETRY_S = float(os.environ.get("HEMI_LLM_429_RETRY_S", "6"))


def _is_rate_limit(exc: Exception) -> bool:
    return "429" in str(exc) or "Too Many Requests" in str(exc)


async def _run_once_with_retry(agent: Agent, prompt: str, ms: dict):
    import asyncio
    try:
        return await agent.run(prompt, model_settings=ms)
    except Exception as exc:  # noqa: BLE001
        if not _is_rate_limit(exc):
            raise
        log.warning("LLM 429 — retry unique dans %.1fs", _RATE_LIMIT_RETRY_S)
        await asyncio.sleep(_RATE_LIMIT_RETRY_S)
        return await agent.run(prompt, model_settings=ms)


async def complete(purpose: str, priority: str, prompt: str = "", lang: str = "en",
                   enable_thinking: bool | None = None) -> str:
    """Complétion bufferisée → texte généré. `priority` ignoré (plus de pool)."""
    result = await _run_once_with_retry(
        _agent(purpose, lang), _prompt(prompt), _model_settings(purpose, enable_thinking)
    )
    return result.output or ""


async def complete_usage(purpose: str, priority: str, prompt: str = "", lang: str = "en",
                         enable_thinking: bool | None = None) -> tuple[str, dict]:
    """Comme `complete`, mais renvoie aussi l'usage tokens (prompt/completion)."""
    p = _prompt(prompt)
    result = await _run_once_with_retry(
        _agent(purpose, lang), p, _model_settings(purpose, enable_thinking)
    )
    text = result.output or ""
    return text, _usage_dict(result, p, text)


async def complete_stream(purpose: str, priority: str, prompt: str = "", lang: str = "en",
                          enable_thinking: bool | None = None) -> AsyncIterator[dict]:
    """Stream SSE-équivalent : yield des dicts `{type, text, ...}`.

    Contrat préservé (consommé par l'orchestrateur / sub-agents) :
      - `{type:"thinking", text}`  — delta de raisonnement (ThinkingPart qwen)
      - `{type:"token",    text}`  — delta de réponse finale (TextPart)
      - `{type:"done",     text:"", finish_reason, usage:{prompt_tokens, completion_tokens}}`
      - `{type:"error",    text, error:True}` puis un `done` finish_reason="error"

    Le 1er fragment de chaque part arrive dans `PartStartEvent` (pas en delta) — on
    le relaie sinon le 1er token est perdu (« voici » → « ici »).
    """
    agent = _agent(purpose, lang)
    ms = _model_settings(purpose, enable_thinking)
    p = _prompt(prompt)
    out_acc = ""  # accumule le texte de réponse → estimation usage si l'upstream n'en donne pas
    try:
        async with agent.iter(p, model_settings=ms) as run:
            async for node in run:
                if not Agent.is_model_request_node(node):
                    continue
                async with node.stream(run.ctx) as request_stream:
                    async for event in request_stream:
                        kind, text = _event_delta(event)
                        if text:
                            if kind == "token":
                                out_acc += text
                            yield {"type": kind, "text": text}
            yield {"type": "done", "text": "", "finish_reason": "stop",
                   "usage": _usage_dict(run, p, out_acc)}
    except Exception as exc:  # noqa: BLE001
        log.warning("complete_stream a échoué (%s)", exc, exc_info=True)
        yield {"type": "error", "text": str(exc), "error": True}
        yield {"type": "done", "text": "", "finish_reason": "error", "error": True,
               "usage": {"prompt_tokens": 0, "completion_tokens": 0}}


def _event_delta(event) -> tuple[str, str]:
    """Mappe un event de stream pydantic-ai → (kind, delta). kind ∈ thinking|token."""
    if isinstance(event, PartStartEvent):
        part = event.part
        if isinstance(part, ThinkingPart):
            return "thinking", part.content or ""
        if isinstance(part, TextPart):
            return "token", part.content or ""
    elif isinstance(event, PartDeltaEvent):
        delta = event.delta
        if isinstance(delta, ThinkingPartDelta):
            return "thinking", delta.content_delta or ""
        if isinstance(delta, TextPartDelta):
            return "token", delta.content_delta or ""
    return "", ""
