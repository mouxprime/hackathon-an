"""Tests unitaires — table de transition FSM + idempotence des handlers (aucun I/O réseau/DB)."""

import fsm
import llm
from hemicycle_shared.db import Conversation, Task
from hemicycle_shared.messages import Result


# --- Heuristique de planification ---

# Catalogue de test minimal — manifestes représentatifs pour le routage par mots-clés.
_CATALOG = {
    "mi_search": {
        "discipline": "SEARCH",
        "when_to_use": "rechercher récupérer corréler bases connaissances fédérées documents entités embeddings",
        "description": "Recherche fédérée sur le corpus interne.",
    },
    "imint": {
        "discipline": "IMINT",
        "when_to_use": "imagerie satellite photos aériennes relevés visuels",
        "description": "Analyse d'imagerie.",
    },
    "geoint": {
        "discipline": "GEOINT",
        "when_to_use": "positions zones coordonnées terrain itinéraires secteur géographique",
        "description": "Analyse géospatiale.",
    },
}


def test_plan_investigation_requete_generique_ne_dispatche_rien():
    """Sans mot-clé matchant aucun manifest, l'heuristique renvoie [] (à charge de la FSM)."""
    steps = llm.plan_investigation("tu trouves des choses ?", _CATALOG)
    assert steps == []


def test_plan_investigation_routage_par_mots_cles():
    """Les mots-clés 'imagerie satellite' et 'zone' déclenchent IMINT et GEOINT."""
    steps = llm.plan_investigation(
        "analyse cette imagerie satellite de la zone", _CATALOG
    )
    agents = {s["agent_type"] for s in steps}
    assert "imint" in agents
    assert "geoint" in agents
    assert "mi_search" not in agents


def test_plan_investigation_registry_vide():
    """Registry vide → liste vide, peu importe la requête."""
    assert llm.plan_investigation("Iran situation", {}) == []


# --- Forme des tables déclaratives ---


def test_transition_table_bien_formee():
    """Chaque entrée de TRANSITIONS est (état, événement) → handler appelable."""
    for (state, event), handler in fsm.TRANSITIONS.items():
        assert isinstance(state, str) and isinstance(event, str)
        assert callable(handler)


def test_llm_prep_est_sous_ensemble_des_transitions():
    """Toute clé de préparation LLM doit correspondre à une transition déclarée."""
    assert set(fsm.LLM_PREP).issubset(set(fsm.TRANSITIONS))


# --- Idempotence des handlers ---


class _FakeSession:
    """Session minimale : `.get` (lecture) et `.add` (collecte) suffisent aux chemins testés ici."""

    def __init__(self, objects: dict):
        self._objects = objects
        self.added: list = []

    async def get(self, _model, pk):
        return self._objects.get(pk)

    def add(self, obj):
        self.added.append(obj)


def _ctx(conv: Conversation, objects: dict) -> fsm.TransitionContext:
    """Construit un contexte de transition adossé à une fausse session."""
    return fsm.TransitionContext(
        session=_FakeSession(objects),
        conv=conv,
        event=None,
        event_type="subagent_result",
    )


async def test_h_collect_result_idempotent_sur_tache_terminale():
    """Rejouer un résultat sur une tâche déjà 'done' ne produit AUCUN effet (invariant n°4)."""
    conv = Conversation(
        conv_id="c1", fsm_state="AWAITING_SUBAGENTS", event_seq=5, plan={}
    )
    task = Task(corr_id="t1", conv_id="c1", agent_type="mi_search", status="done")
    ctx = _ctx(conv, {"t1": task})
    ctx.event = Result(
        corr_id="t1",
        conv_id="c1",
        agent_id="mi_search",
        worker_id="w1",
        status="done",
        output={},
    )
    next_state = await fsm.h_collect_result(ctx, None)
    assert next_state == "AWAITING_SUBAGENTS"
    assert ctx.pending_ui == []  # aucun événement émis
    assert conv.event_seq == 5  # event_seq inchangé


async def test_h_collect_result_tache_absente_est_un_noop():
    """Un résultat orphelin (tâche introuvable) est ignoré sans effet."""
    conv = Conversation(
        conv_id="c1", fsm_state="AWAITING_SUBAGENTS", event_seq=0, plan={}
    )
    ctx = _ctx(conv, {})
    ctx.event = Result(
        corr_id="absent",
        conv_id="c1",
        agent_id="mi_search",
        worker_id="w1",
        status="done",
        output={},
    )
    next_state = await fsm.h_collect_result(ctx, None)
    assert next_state == "AWAITING_SUBAGENTS"
    assert ctx.pending_ui == []


async def test_h_noop_laisse_l_etat_inchange():
    """h_noop (événement hors-séquence) retourne l'état courant sans rien émettre."""
    conv = Conversation(conv_id="c1", fsm_state="DONE", event_seq=3, plan={})
    ctx = _ctx(conv, {})
    next_state = await fsm.h_noop(ctx, None)
    assert next_state == "DONE"
    assert ctx.pending_ui == []
    assert conv.event_seq == 3


# --- Boucle humaine + exécution séquentielle ---


def _set_catalog():
    """Renseigne fsm.AGENT_MAP avec un catalogue de test (mi_search, geoint)."""
    fsm.AGENT_MAP = {
        "mi_search": {"hard_timeout_s": 120, "discipline": "SEARCH"},
        "geoint": {"hard_timeout_s": 150, "discipline": "GEOINT"},
    }


async def test_h_dispatch_plan_ne_lance_que_la_premiere_etape():
    """À l'approbation, seule la 1re étape part : le séquentiel garantit un seul task en vol."""
    _set_catalog()
    conv = Conversation(
        conv_id="c1",
        fsm_state="AWAITING_PLAN",
        event_seq=0,
        plan={
            "turn": 1,
            "status": "proposed",
            "cursor": -1,
            "text": "secteur Nord",
            "attachments": [],
            "steps": [
                {
                    "plan_step_id": "step-1",
                    "agent_type": "mi_search",
                    "instruction": "a",
                    "corr_id": None,
                    "status": "pending",
                },
                {
                    "plan_step_id": "step-2",
                    "agent_type": "geoint",
                    "instruction": "b",
                    "corr_id": None,
                    "status": "pending",
                },
            ],
        },
    )
    ctx = fsm.TransitionContext(
        session=_FakeSession({}), conv=conv, event=None, event_type="plan_submit"
    )
    prepared = {
        "steps": [
            {"agent_type": "mi_search", "instruction": "a"},
            {"agent_type": "geoint", "instruction": "b"},
        ],
        "tools_by_agent": {},
    }
    next_state = await fsm.h_dispatch_plan(ctx, prepared)
    assert next_state == "AWAITING_SUBAGENTS"
    assert len(ctx.pending_dispatches) == 1  # une seule tâche dispatchée
    assert conv.plan["cursor"] == 0
    assert conv.plan["steps"][0]["status"] == "dispatched"
    assert conv.plan["steps"][1]["status"] == "pending"  # la 2e attend


async def test_h_collect_result_avance_a_l_etape_suivante():
    """Un résultat 'done' sur l'étape courante déclenche le dispatch de la suivante."""
    _set_catalog()
    conv = Conversation(
        conv_id="c1",
        fsm_state="AWAITING_SUBAGENTS",
        event_seq=0,
        plan={
            "turn": 1,
            "status": "running",
            "cursor": 0,
            "text": "x",
            "attachments": [],
            "steps": [
                {
                    "plan_step_id": "step-1",
                    "agent_type": "mi_search",
                    "instruction": "a",
                    "corr_id": "t1",
                    "status": "dispatched",
                },
                {
                    "plan_step_id": "step-2",
                    "agent_type": "geoint",
                    "instruction": "b",
                    "corr_id": None,
                    "status": "pending",
                },
            ],
        },
    )
    task = Task(corr_id="t1", conv_id="c1", agent_type="mi_search", status="running")
    ctx = fsm.TransitionContext(
        session=_FakeSession({"t1": task}),
        conv=conv,
        event=None,
        event_type="subagent_result",
    )
    ctx.event = Result(
        corr_id="t1",
        conv_id="c1",
        agent_id="mi_search",
        worker_id="w1",
        status="done",
        output={"summary": "s"},
    )
    next_state = await fsm.h_collect_result(ctx, None)
    assert next_state == "AWAITING_SUBAGENTS"
    assert len(ctx.pending_dispatches) == 1  # l'étape suivante part
    assert conv.plan["cursor"] == 1
    assert conv.plan["steps"][1]["status"] == "dispatched"
    # Chaînage par référence : l'étape amont expose son artefact (= corr_id) + preview,
    # et l'étape aval reçoit la référence (et non un résumé tronqué).
    assert conv.plan["steps"][0]["artifact_id"] == "t1"
    assert conv.plan["steps"][0]["preview"] == "s"
    assert "result_text" not in conv.plan["steps"][0]
    assert (
        len(ctx.pending_artifacts) == 1
    )  # miroir Object Store (Result sans artifact_id)
    assert ctx.pending_artifacts[0].artifact_id == "t1"
    td = ctx.pending_dispatches[0]
    assert [r.artifact_id for r in td.input_refs] == ["t1"]
    assert td.input_refs[0].agent_type == "mi_search"


async def test_h_collect_result_ne_miroite_pas_si_le_worker_a_deja_stocke():
    """Result.artifact_id présent (worker interne) → pas de miroir Object Store par l'orchestrateur."""
    _set_catalog()
    conv = Conversation(
        conv_id="c1",
        fsm_state="AWAITING_SUBAGENTS",
        event_seq=0,
        plan={
            "turn": 1,
            "status": "running",
            "cursor": 0,
            "text": "x",
            "attachments": [],
            "steps": [
                {
                    "plan_step_id": "step-1",
                    "agent_type": "mi_search",
                    "instruction": "a",
                    "corr_id": "t1",
                    "status": "dispatched",
                }
            ],
        },
    )
    task = Task(corr_id="t1", conv_id="c1", agent_type="mi_search", status="running")
    ctx = fsm.TransitionContext(
        session=_FakeSession({"t1": task}),
        conv=conv,
        event=None,
        event_type="subagent_result",
    )
    ctx.event = Result(
        corr_id="t1",
        conv_id="c1",
        agent_id="mi_search",
        worker_id="w1",
        status="done",
        output={"summary": "s"},
        artifact_id="t1",
    )
    await fsm.h_collect_result(ctx, None)
    assert ctx.pending_artifacts == []  # déjà stocké par le producteur
    assert conv.plan["steps"][0]["artifact_id"] == "t1"


async def test_h_collect_result_derniere_etape_declenche_le_rapport():
    """force_report=True : dernière étape `done` → WRITING_REPORT (synthèse opt-in)."""
    _set_catalog()
    conv = Conversation(
        conv_id="c1",
        fsm_state="AWAITING_SUBAGENTS",
        event_seq=0,
        plan={
            "turn": 1,
            "status": "running",
            "cursor": 0,
            "text": "x",
            "attachments": [],
            "force_report": True,
            "steps": [
                {
                    "plan_step_id": "step-1",
                    "agent_type": "mi_search",
                    "instruction": "a",
                    "corr_id": "t1",
                    "status": "dispatched",
                }
            ],
        },
    )
    task = Task(corr_id="t1", conv_id="c1", agent_type="mi_search", status="running")
    ctx = fsm.TransitionContext(
        session=_FakeSession({"t1": task}),
        conv=conv,
        event=None,
        event_type="subagent_result",
    )
    ctx.event = Result(
        corr_id="t1",
        conv_id="c1",
        agent_id="mi_search",
        worker_id="w1",
        status="done",
        output={"summary": "s"},
    )
    next_state = await fsm.h_collect_result(ctx, None)
    assert next_state == "WRITING_REPORT"
    assert len(ctx.pending_dispatches) == 0  # plus rien à dispatcher
    assert len(ctx.pending_internal) == 1  # WriteReport mis en file


async def test_h_collect_result_derniere_etape_sans_rapport_va_en_done():
    """Sans force_report : dernière étape `done` → DONE direct (pas de rédaction de rapport)."""
    _set_catalog()
    conv = Conversation(
        conv_id="c1",
        fsm_state="AWAITING_SUBAGENTS",
        event_seq=0,
        plan={
            "turn": 1,
            "status": "running",
            "cursor": 0,
            "text": "x",
            "attachments": [],
            "steps": [
                {
                    "plan_step_id": "step-1",
                    "agent_type": "mi_search",
                    "instruction": "a",
                    "corr_id": "t1",
                    "status": "dispatched",
                }
            ],
        },
    )
    task = Task(corr_id="t1", conv_id="c1", agent_type="mi_search", status="running")
    ctx = fsm.TransitionContext(
        session=_FakeSession({"t1": task}),
        conv=conv,
        event=None,
        event_type="subagent_result",
    )
    ctx.event = Result(
        corr_id="t1",
        conv_id="c1",
        agent_id="mi_search",
        worker_id="w1",
        status="done",
        output={"summary": "s"},
    )
    next_state = await fsm.h_collect_result(ctx, None)
    assert next_state == "DONE"
    assert len(ctx.pending_internal) == 0  # aucun rapport mis en file


# --- input-required (round-trip A2A) ---


async def test_h_collect_result_input_required_passe_en_awaiting_user_input():
    """Result(input_required) : la tâche est mise en attente, la question affichée, état AWAITING_USER_INPUT."""
    _set_catalog()
    conv = Conversation(
        conv_id="c1",
        fsm_state="AWAITING_SUBAGENTS",
        event_seq=0,
        plan={
            "turn": 1,
            "status": "running",
            "cursor": 0,
            "text": "lance une simulation",
            "attachments": [],
            "steps": [
                {
                    "plan_step_id": "step-1",
                    "agent_type": "geoint",
                    "instruction": "simuler",
                    "corr_id": "t1",
                    "status": "dispatched",
                }
            ],
        },
    )
    task = Task(corr_id="t1", conv_id="c1", agent_type="geoint", status="running")
    ctx = fsm.TransitionContext(
        session=_FakeSession({"t1": task}),
        conv=conv,
        event=None,
        event_type="subagent_result",
    )
    ctx.event = Result(
        corr_id="t1",
        conv_id="c1",
        agent_id="geoint",
        worker_id="w1",
        status="input_required",
        output={"question": "Quel COA ?", "context_id": "ctx-42"},
    )
    next_state = await fsm.h_collect_result(ctx, None)
    assert next_state == "AWAITING_USER_INPUT"
    assert task.status == "awaiting_input"  # statut non-terminal, hors balayeurs
    assert conv.plan["steps"][0]["status"] == "awaiting_input"
    assert (
        conv.plan["steps"][0]["context_id"] == "ctx-42"
    )  # contextId mémorisé pour reprise
    # La question est émise comme message assistant ; aucune étape suivante n'est dispatchée.
    assert any(
        ev.type == "message" and ev.payload.get("text") == "Quel COA ?"
        for ev in ctx.pending_ui
    )
    assert len(ctx.pending_dispatches) == 0


async def test_h_collect_result_input_required_idempotent():
    """Un Result(input_required) re-livré sur une tâche déjà 'awaiting_input' est ignoré."""
    conv = Conversation(
        conv_id="c1", fsm_state="AWAITING_SUBAGENTS", event_seq=5, plan={}
    )
    task = Task(
        corr_id="t1", conv_id="c1", agent_type="geoint", status="awaiting_input"
    )
    ctx = _ctx(conv, {"t1": task})
    ctx.event = Result(
        corr_id="t1",
        conv_id="c1",
        agent_id="geoint",
        worker_id="w1",
        status="input_required",
        output={"question": "Quel COA ?"},
    )
    next_state = await fsm.h_collect_result(ctx, None)
    assert next_state == "AWAITING_SUBAGENTS"  # état courant inchangé
    assert ctx.pending_ui == []
    assert conv.event_seq == 5


async def test_h_answer_input_redispatche_avec_context_id():
    """La réponse de l'analyste re-dispatche le MÊME agent avec le context_id, retour en AWAITING_SUBAGENTS."""
    import types

    _set_catalog()
    conv = Conversation(
        conv_id="c1",
        fsm_state="AWAITING_USER_INPUT",
        event_seq=0,
        plan={
            "turn": 1,
            "status": "running",
            "cursor": 0,
            "text": "lance une simulation",
            "attachments": [],
            "steps": [
                {
                    "plan_step_id": "step-1",
                    "agent_type": "geoint",
                    "instruction": "simuler",
                    "corr_id": "t1",
                    "status": "awaiting_input",
                    "context_id": "ctx-42",
                }
            ],
        },
    )
    ctx = fsm.TransitionContext(
        session=_FakeSession({}), conv=conv, event=None, event_type="user_input"
    )
    ctx.event = types.SimpleNamespace(text="COA ATK_0, 20 runs")
    next_state = await fsm.h_answer_input(ctx, None)
    assert next_state == "AWAITING_SUBAGENTS"
    assert len(ctx.pending_dispatches) == 1
    td = ctx.pending_dispatches[0]
    assert td.agent_type == "geoint"
    assert td.params.get("context_id") == "ctx-42"  # reprise A2A
    assert td.corr_id != "t1"  # nouveau corr_id (évite la dédup NATS)
    step = conv.plan["steps"][0]
    assert step["status"] == "dispatched"
    assert step["corr_id"] == td.corr_id
    assert "context_id" not in step  # consommé


def test_build_report_prompt_reprend_les_resultats():
    """Le prompt de rapport inclut le sujet et les apports des agents (plus de prompt vide)."""
    prompt = llm.build_report_prompt(
        "activité secteur Nord",
        [
            {
                "agent_type": "mi_search",
                "output": {"summary": "trois sources concordantes"},
            }
        ],
    )
    assert "activité secteur Nord" in prompt
    assert "trois sources concordantes" in prompt


# --- Prompts sub-agents générés par le LLM (instructions ciblées par agent) ---


def test_build_steps_utilise_les_instructions_du_routeur():
    """Les consignes ciblées renvoyées par le routeur LLM priment sur le gabarit."""
    catalog = {"mi_search": {"discipline": "SEARCH"}}
    steps = llm._build_steps(
        "activité secteur Nord",
        ["mi_search"],
        catalog,
        {
            "mi_search": "Rechercher dans les bases internes les éléments récents sur le secteur Nord."
        },
    )
    assert (
        steps[0]["instruction"]
        == "Rechercher dans les bases internes les éléments récents sur le secteur Nord."
    )


def test_build_steps_repli_cible_par_discipline():
    """Sans instruction LLM, le repli est ciblé par discipline (fini « Investiguer « … » via »)."""
    catalog = {"mi_search": {"discipline": "SEARCH"}}
    steps = llm._build_steps("activité secteur Nord", ["mi_search"], catalog)
    instr = steps[0]["instruction"]
    assert "Investiguer «" not in instr  # ancien gabarit supprimé
    assert "SEARCH" in instr
    assert "activité secteur Nord" in instr


# --- Routeur LLM : posture active + gardes déterministes ---

# Catalogue calqué sur la registry réelle (ids des agents A2A enrôlés).
_ROUTE_CATALOG = {
    "mi_search": {
        "discipline": "SEARCH",
        "when_to_use": "rechercher récupérer corréler bases connaissances fédérées",
        "description": "Recherche fédérée sur le corpus interne.",
    },
    "imint-agent": {
        "discipline": "IMINT",
        "when_to_use": "imagerie satellite photos aériennes",
        "description": "Analyse d'imagerie.",
    },
    "data-management-agent": {
        "discipline": "DATAMGT",
        "when_to_use": "ingérer lister consulter collections datasets état backend",
        "description": "Gestion de datasets.",
    },
}


def _fake_complete_usage(
    json_text: str, prompt_tokens: int = 0, completion_tokens: int = 0
):
    """Fabrique un faux `complete_usage` renvoyant un JSON routeur fixe + usage nul (aucun I/O)."""

    async def _inner(*_a, **_k):
        return json_text, {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }

    return _inner


def test_last_substantive_user_topic_ignore_les_ordres_laconiques():
    """Le sujet ancré saute les ordres de délégation et reprend le dernier vrai sujet."""
    history = [
        {"role": "user", "text": "situation à Kaliningrad cette semaine"},
        {"role": "assistant", "text": "…"},
        {"role": "user", "text": "demande à Search"},
    ]
    assert "Kaliningrad" in llm._last_substantive_user_topic(history)


async def test_route_mention_agent_force_investigate(monkeypatch):
    """« demande à Search » : même si le LLM esquive (chitchat), la mention force le routage.

    Reproduit l'échec réel : l'instruction de l'agent est ancrée sur le sujet du
    tour précédent (« …sur l'Allemagne »), pas sur l'ordre laconique courant.
    """
    monkeypatch.setattr(
        llm,
        "complete_usage",
        _fake_complete_usage(
            '{"intent":"chitchat","agents":[],'
            '"reply":"Je ne peux pas exécuter ça, reformulez."}'
        ),
    )
    history = [
        {
            "role": "user",
            "text": "Que peux-tu me dire sur les données qu'on a sur l'Allemagne ?",
        },
        {"role": "assistant", "text": "Je n'ai pas accès…"},
    ]
    out = await llm.route_request("demande à Search", _ROUTE_CATALOG, history=history)
    assert out["intent"] == "investigate"
    assert "mi_search" in out["agents"]
    assert "Allemagne" in out["instructions"]["mi_search"]


async def test_route_interrogation_catalogue_va_au_data_agent(monkeypatch):
    """« les data qu'on a sur X » : même si le LLM esquive, on route vers l'agent data."""
    monkeypatch.setattr(
        llm,
        "complete_usage",
        _fake_complete_usage(
            '{"intent":"chitchat","agents":[],'
            '"reply":"Utilisez l\'agent de gestion de données."}'
        ),
    )
    out = await llm.route_request(
        "Que peux-tu me dire sur les data qu'on a sur l'Allemagne ?", _ROUTE_CATALOG
    )
    assert out["intent"] == "investigate"
    assert "data-management-agent" in out["agents"]


async def test_route_data_ops_normalise_en_investigate(monkeypatch):
    """`data_ops` renvoyé par le LLM → investigate vers l'agent de gestion de données."""
    monkeypatch.setattr(
        llm,
        "complete_usage",
        _fake_complete_usage('{"intent":"data_ops","agents":[],"reply":""}'),
    )
    out = await llm.route_request("liste les collections disponibles", _ROUTE_CATALOG)
    assert out["intent"] == "investigate"
    assert out["agents"] == ["data-management-agent"]


async def test_route_chitchat_reste_chitchat(monkeypatch):
    """Une vraie salutation reste chitchat : aucun agent mobilisé, réponse directe conservée."""
    monkeypatch.setattr(
        llm,
        "complete_usage",
        _fake_complete_usage(
            '{"intent":"chitchat","agents":[],"reply":"Bonjour ! Comment puis-je aider ?"}'
        ),
    )
    out = await llm.route_request("bonjour", _ROUTE_CATALOG)
    assert out["intent"] == "chitchat"
    assert out["agents"] == []
    assert "Bonjour" in out["reply"]


async def test_route_investigate_sans_agent_connu_pose_une_question(monkeypatch):
    """investigate sur un agent inconnu → chitchat avec UNE question de clarification (pas un refus)."""
    monkeypatch.setattr(
        llm,
        "complete_usage",
        _fake_complete_usage(
            '{"intent":"investigate","agents":[{"id":"inconnu","instruction":"x"}],"reply":""}'
        ),
    )
    out = await llm.route_request("analyse ce truc vague", _ROUTE_CATALOG)
    assert out["intent"] == "chitchat"
    assert out["agents"] == []
    assert out["reply"].endswith("?")


async def test_route_retourne_tokens():
    """route_request retourne `tokens` = somme prompt+completion de l'appel LLM."""
    # Sans LLM (fallback heuristique, pas d'appel LLM) : tokens = 0.
    out = await llm.route_request("bonjour", {})
    assert "tokens" in out
    assert out["tokens"] == 0


# --- Routeur LLM : priorité à la connaissance déjà disponible (mémoire + @contexte) ---


def test_routing_prompt_priorise_le_contexte_reference():
    """Avec un @contexte référencé, le prompt impose EN PRIORITÉ d'y répondre sans agent.

    Reproduit l'échec réel : « qui est Drez » avec une conv référencée qui contient
    déjà la réponse → le routeur doit répondre (chitchat) sans spawner la recherche.
    """
    prompt = llm._build_routing_prompt(
        "qui est Drez",
        _ROUTE_CATALOG,
        lang="fr",
        ref_context="--- Contexte : Op Drez ---\n[seq 4] assistant : Drez est un cargo.\n---",
    )
    assert "PRIORITÉ ABSOLUE" in prompt
    # La règle de priorité doit PRÉCÉDER la posture/les traitements (sinon elle est noyée).
    assert prompt.index("PRIORITÉ ABSOLUE") < prompt.index("QUATRE traitements")
    assert "aucun agent" in prompt.lower()


def test_routing_prompt_priorise_la_memoire():
    """Avec de la mémoire interne pertinente, le prompt impose d'y répondre en priorité sans agent."""
    prompt = llm._build_routing_prompt(
        "qui est Drez",
        _ROUTE_CATALOG,
        lang="fr",
        memories=[{"id": 7, "content": "Drez est un cargo sous pavillon panaméen."}],
    )
    assert "PRIORITÉ ABSOLUE" in prompt
    assert prompt.index("PRIORITÉ ABSOLUE") < prompt.index("QUATRE traitements")


def test_routing_prompt_en_priorise_la_connaissance_disponible():
    """Version anglaise : même règle de priorité prononcée avant les traitements."""
    prompt = llm._build_routing_prompt(
        "who is Drez",
        _ROUTE_CATALOG,
        lang="en",
        ref_context="--- Context : Op Drez ---\n[seq 4] assistant : Drez is a cargo ship.\n---",
    )
    assert "ABSOLUTE PRIORITY" in prompt
    assert prompt.index("ABSOLUTE PRIORITY") < prompt.index("FOUR possible handlings")
    assert "no agent" in prompt.lower()


def test_routing_prompt_sans_connaissance_pas_de_bloc_priorite():
    """Sans mémoire ni @contexte : pas de bloc priorité (évite le bruit dans le prompt)."""
    prompt = llm._build_routing_prompt(
        "analyse la situation en Allemagne", _ROUTE_CATALOG, lang="fr"
    )
    assert "PRIORITÉ ABSOLUE" not in prompt


# --- Compaction de contexte ---


async def test_h_compact_historique_vide_emets_rien_a_compacter():
    """h_compact sans résumé (historique vide) → message info, état inchangé, pas de compacted_seq."""
    conv = Conversation(
        conv_id="c1",
        fsm_state="DONE",
        event_seq=3,
        plan={},
        context_tokens=500,
        compacted_seq=0,
    )
    ctx = fsm.TransitionContext(
        session=_FakeSession({}), conv=conv, event=None, event_type="user_compact"
    )
    prepared = {"summary": "", "tokens": 0, "lang": "en"}
    next_state = await fsm.h_compact(ctx, prepared)
    assert next_state == "DONE"  # état inchangé
    assert len(ctx.pending_ui) == 1  # un message "rien à compacter"
    assert ctx.pending_ui[0].type == "message"
    assert conv.compacted_seq == 0  # pas de borne posée
    assert conv.context_tokens == 500  # inchangé


async def test_h_compact_avec_resume_pose_compacted_seq_et_rebases_tokens():
    """h_compact avec résumé → message assistant émis, compacted_seq = event_seq, context_tokens re-basé."""
    conv = Conversation(
        conv_id="c1",
        fsm_state="DONE",
        event_seq=10,
        plan={},
        context_tokens=8000,
        compacted_seq=0,
    )
    ctx = fsm.TransitionContext(
        session=_FakeSession({}), conv=conv, event=None, event_type="user_compact"
    )
    prepared = {"summary": "Résumé compact de la conv.", "tokens": 120, "lang": "fr"}
    next_state = await fsm.h_compact(ctx, prepared)
    assert next_state == "DONE"  # état inchangé
    assert len(ctx.pending_ui) == 1
    ev = ctx.pending_ui[0]
    assert ev.type == "message"
    assert ev.payload["kind"] == "compaction"  # marqueur repéré par le front
    assert (
        ev.payload["text"] == "Résumé compact de la conv."
    )  # résumé seul, sans préfixe
    assert conv.compacted_seq == conv.event_seq  # seq du message résumé
    assert conv.context_tokens == 120  # re-basé sur le coût du résumé seul


async def test_h_compact_awaiting_subagents_est_noop():
    """user_compact pendant AWAITING_SUBAGENTS → h_noop, aucun effet."""
    conv = Conversation(
        conv_id="c1", fsm_state="AWAITING_SUBAGENTS", event_seq=5, plan={}
    )
    ctx = fsm.TransitionContext(
        session=_FakeSession({}), conv=conv, event=None, event_type="user_compact"
    )
    next_state = await fsm.h_noop(ctx, None)
    assert next_state == "AWAITING_SUBAGENTS"
    assert ctx.pending_ui == []


def test_user_compact_dans_les_tables_aux_bons_etats():
    """LLM_PREP et TRANSITIONS enregistrent user_compact exactement aux bons états."""
    rest = {"IDLE", "DONE", "FAILED", "AWAITING_PLAN"}
    busy = {"AWAITING_SUBAGENTS", "WRITING_REPORT"}
    for s in rest:
        assert (s, "user_compact") in fsm.LLM_PREP, (
            f"LLM_PREP manque ({s}, user_compact)"
        )
        assert (s, "user_compact") in fsm.TRANSITIONS, (
            f"TRANSITIONS manque ({s}, user_compact)"
        )
    for s in busy:
        assert (s, "user_compact") not in fsm.LLM_PREP, (
            f"LLM_PREP ne devrait pas avoir ({s}, user_compact)"
        )
        assert (s, "user_compact") in fsm.TRANSITIONS, (
            f"TRANSITIONS manque ({s}, user_compact)"
        )
        assert fsm.TRANSITIONS[(s, "user_compact")] is fsm.h_noop, (
            f"({s}, user_compact) devrait être h_noop"
        )


# --- @ Mention : formatage des contextes référencés ---


def test_format_context_refs_liste_vide():
    """Aucune ref → chaîne vide."""
    assert fsm._format_context_refs([]) == ""


def test_format_context_refs_ref_sans_events():
    """Une ref sans événements → bloc vide → pas inclus dans la sortie."""
    refs = [{"conv_id": "abc", "title": "Op Cobra", "events": []}]
    assert fsm._format_context_refs(refs) == ""


def test_format_context_refs_une_ref_avec_events():
    """Une ref avec des events message → bloc balisé avec seq, role, texte."""
    refs = [
        {
            "conv_id": "abc",
            "title": "Op Cobra",
            "events": [
                {
                    "seq": 1,
                    "type": "message",
                    "payload": {"role": "user", "text": "Cherche Tripoli"},
                },
                {
                    "seq": 4,
                    "type": "message",
                    "payload": {"role": "assistant", "text": "3 navires détectés"},
                },
            ],
        }
    ]
    result = fsm._format_context_refs(refs)
    assert "--- Contexte : Op Cobra ---" in result
    assert "[seq 1] user : Cherche Tripoli" in result
    assert "[seq 4] assistant : 3 navires détectés" in result
    assert result.endswith("---")


def test_format_context_refs_deux_refs():
    """Deux refs → deux blocs séparés par une ligne vide."""
    refs = [
        {
            "conv_id": "a",
            "title": "Conv A",
            "events": [
                {"seq": 1, "type": "message", "payload": {"role": "user", "text": "A"}}
            ],
        },
        {
            "conv_id": "b",
            "title": "Conv B",
            "events": [
                {
                    "seq": 2,
                    "type": "message",
                    "payload": {"role": "assistant", "text": "B"},
                }
            ],
        },
    ]
    result = fsm._format_context_refs(refs)
    assert "Conv A" in result
    assert "Conv B" in result
    assert "\n\n" in result


def test_format_context_refs_ignore_events_sans_texte():
    """Les events avec texte vide sont ignorés."""
    refs = [
        {
            "conv_id": "abc",
            "title": "Test",
            "events": [
                {"seq": 1, "type": "message", "payload": {"role": "user", "text": ""}},
                {
                    "seq": 2,
                    "type": "message",
                    "payload": {"role": "assistant", "text": "Réponse"},
                },
            ],
        }
    ]
    result = fsm._format_context_refs(refs)
    assert "[seq 1]" not in result
    assert "[seq 2] assistant : Réponse" in result


def test_format_context_refs_capte_le_summary_des_resultats_agents():
    """La RÉPONSE d'une investigation vit dans `data.summary` du widget résultat
    (mi_search/imint/geoint), pas `text`. Sans la capter, le contexte ne contiendrait
    que la question initiale → l'orchestrateur ré-investiguerait une question déjà
    répondue (bug réel : « Qui est Drez ? » re-spawnait la recherche)."""
    refs = [
        {
            "conv_id": "abc",
            "title": "Qui est Drez",
            "events": [
                {
                    "seq": 1,
                    "type": "message",
                    "payload": {"role": "user", "text": "Qui est Drez ?"},
                },
                {
                    "seq": 10,
                    "type": "widget",
                    "payload": {
                        "widget_type": "markdown",
                        "data": {
                            "agent_type": "mi_search",
                            "queries": ["Drez"],
                            "summary": "Drez est un individu identifié dans la base interne.",
                        },
                    },
                },
            ],
        }
    ]
    result = fsm._format_context_refs(refs)
    assert "Drez est un individu identifié dans la base interne." in result
    assert "résultat mi_search" in result


# --- auto-contexte intra-conversation ---


def test_build_routing_prompt_self_context_declenche_bloc_priorite():
    """self_context seul (sans mémoire LT ni @contexte) suffit à activer le bloc PRIORITÉ.

    Vérifie que la 2e question identique dans une conv recevrait bien le bloc
    d'instruction de réponse directe — sans que l'analyste ait besoin du @contexte.
    """
    self_ctx = (
        "[seq 10] résultat mi_search : Drez est un individu identifié dans la base interne."
    )
    prompt = llm._build_routing_prompt(
        "Qui est Drez ?", _CATALOG, self_context=self_ctx, lang="fr"
    )
    assert "PRIORITÉ ABSOLUE" in prompt
    assert "RÉSULTATS DÉJÀ PRODUITS DANS CETTE CONVERSATION" in prompt
    assert "Drez est un individu identifié dans la base interne." in prompt


def test_build_routing_prompt_self_context_en():
    """Même vérification en anglais : bloc PRIORITY + section RESULTS ALREADY PRODUCED."""
    self_ctx = (
        "[seq 10] résultat mi_search : Drez est un individu identifié dans la base interne."
    )
    prompt = llm._build_routing_prompt(
        "Who is Drez?", _CATALOG, self_context=self_ctx, lang="en"
    )
    assert "ABSOLUTE PRIORITY" in prompt
    assert "RESULTS ALREADY PRODUCED IN THIS CONVERSATION" in prompt
    assert "Drez est un individu identifié dans la base interne." in prompt


def test_build_routing_prompt_sans_self_context_pas_de_bloc_priorite():
    """Sans mémoire, sans @contexte et sans self_context, le bloc PRIORITÉ est absent."""
    prompt = llm._build_routing_prompt("Qui est Drez ?", _CATALOG, lang="fr")
    assert "PRIORITÉ ABSOLUE" not in prompt
    assert "RÉSULTATS DÉJÀ PRODUITS" not in prompt
