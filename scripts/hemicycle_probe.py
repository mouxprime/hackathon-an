"""Harnais de test de l'orchestrateur Hémicycle via le WebSocket du gateway.

Pilote une conversation de bout en bout : envoi user_input → attente plan_proposal
→ auto-approbation → collecte des résultats d'agents → réponse finale. Restitue le
routage (intent, agents, instructions), les widgets, les messages et la chronologie
des status. Sert à juger la pertinence des réponses et la mémoire inter-conv.

Usage :
    python3 hemicycle_probe.py send <conv_id> "<texte>" [--lang fr|en] [--report] [--think]
    python3 hemicycle_probe.py raw   <conv_id> "<texte>"   # sans auto-approbation
"""
import json
import sys
import time
import uuid

import requests
import websocket  # websocket-client

GW = "ws://localhost:18000"
GW_HTTP = "http://localhost:18000"


def last_seq(conv_id):
    """Dernier seq d'événement de la conv (0 si neuve) — pour isoler un tour du replay."""
    try:
        evs = requests.get(f"{GW_HTTP}/api/events/{conv_id}", timeout=5).json()
        return max((e.get("seq", 0) for e in evs), default=0)
    except Exception:
        return 0


def _send(ws, obj):
    ws.send(json.dumps(obj))


def run_turn(conv_id, text, lang="en", report=False, think=False,
             auto_approve=True, idle_timeout=12.0, hard_cap=240.0,
             user_id="analyst-1", quiet=False, auto_confirm_memory=False):
    """Joue un tour complet et retourne la trace structurée des events reçus.

    Isole le tour courant du replay JetStream (deliver ALL) en ignorant tous les
    événements de seq <= au dernier seq connu AVANT l'envoi.
    """
    base_seq = last_seq(conv_id)
    url = f"{GW}/ws/{conv_id}"
    ws = websocket.create_connection(url, timeout=idle_timeout)
    events = []          # (kind, obj) bruts
    status_chain = []    # suite des états FSM
    messages = []        # messages assistant/user
    widgets = []         # widgets émis
    spawned = []         # spawn_widget
    plan_submitted = False
    final_state = None
    t0 = time.time()

    msg = {"type": "user_input", "text": text, "lang": lang, "user_id": user_id}
    if report:
        msg["force_report"] = True
    if think:
        msg["thinking"] = True
    _send(ws, msg)

    last_rx = time.time()
    seen_seqs = set()
    while True:
        if time.time() - t0 > hard_cap:
            final_state = final_state or "TIMEOUT_HARDCAP"
            break
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            # silence : si on est dans un état terminal/attente, on sort
            if final_state in ("DONE", "FAILED") and time.time() - last_rx > 3:
                break
            if time.time() - last_rx > idle_timeout:
                # rien ne vient — soit terminal, soit bloqué
                if final_state in ("DONE", "FAILED", "AWAITING_PLAN",
                                   "AWAITING_USER_INPUT", None):
                    break
            continue
        if not raw:
            continue
        last_rx = time.time()
        env = json.loads(raw)
        kind = env.get("kind")
        data = env.get("data")
        if kind == "event":
            seq = data.get("seq")
            if seq in seen_seqs or (seq is not None and seq <= base_seq):
                continue  # déjà vu, ou événement d'un tour antérieur rejoué
            seen_seqs.add(seq)
            etype = data.get("type")
            payload = data.get("payload") or {}
            events.append(("event", data))
            if etype == "status":
                st = payload.get("state")
                status_chain.append(st)
                final_state = st
                if not quiet:
                    print(f"    · status → {st}  (+{time.time()-t0:4.1f}s)")
            elif etype == "message":
                role = payload.get("role")
                body = (payload.get("text") or "").strip()
                messages.append({"role": role, "text": body,
                                 "kind": payload.get("kind")})
                if not quiet and role == "assistant":
                    print(f"    💬 assistant: {body[:400]}")
            elif etype == "widget":
                wt = payload.get("widget_type")
                widgets.append({"type": wt, "data": payload.get("data") or {}})
                if wt == "plan_proposal" and auto_approve and not plan_submitted:
                    steps = (payload.get("data") or {}).get("steps") or []
                    if not quiet:
                        ags = [s.get("agent_type") for s in steps]
                        print(f"    📋 plan proposé: {ags} → auto-approve")
                    _send(ws, {"type": "plan_submit", "steps": steps,
                               "user_id": user_id, "lang": lang})
                    plan_submitted = True
                elif wt == "memory_proposal" and auto_confirm_memory:
                    d = payload.get("data") or {}
                    props = d.get("proposals") or []
                    decisions = [
                        {"index": i, "action": "confirm",
                         "kind": p.get("kind"), "content": p.get("content")}
                        for i, p in enumerate(props)
                    ]
                    if decisions:
                        _send(ws, {"type": "memory_decision",
                                   "widget_id": payload.get("widget_id"),
                                   "decisions": decisions,
                                   "user_id": user_id, "lang": lang})
                        if not quiet:
                            print(f"    🧠 memory_proposal: {[p.get('content') for p in props]} → confirm")
            elif etype == "spawn_widget":
                spawned.append(payload.get("widget"))
                if not quiet:
                    print(f"    🪟 spawn_widget: {payload.get('widget')}")
        elif kind == "progress":
            # live thinking / report_chunk — on ignore pour la lisibilité
            pass

    try:
        ws.close()
    except Exception:
        pass
    return {
        "conv_id": conv_id, "elapsed_s": round(time.time() - t0, 1),
        "final_state": final_state, "status_chain": status_chain,
        "messages": messages, "widgets": widgets, "spawned": spawned,
    }


def _fmt_result(r):
    print(f"\n  ── bilan conv={r['conv_id'][:18]} state={r['final_state']} "
          f"({r['elapsed_s']}s) ──")
    print(f"     status: {' → '.join(str(s) for s in r['status_chain'])}")
    for w in r["widgets"]:
        d = w["data"]
        if w["type"] == "plan_proposal":
            ags = [(s.get('agent_type'), (s.get('instruction') or '')[:60])
                   for s in d.get("steps", [])]
            print(f"     plan: {ags}")
        elif w["type"] == "memory_recall":
            mems = [m.get("content") for m in d.get("memories", [])]
            print(f"     memory_recall: {d.get('count')} → {mems}  "
                  f"refs={d.get('context_refs')}")
        elif w["type"] == "memory_proposal":
            print(f"     memory_proposal: {[p for p in d.get('proposals', [])]}")
        elif w["type"] in ("source_list", "image_card", "map", "markdown"):
            keys = [k for k in d.keys() if k not in ('agent_type', 'corr_id')]
            print(f"     widget[{w['type']}] agent={d.get('agent_type')} keys={keys}")
        elif w["type"] == "report":
            print(f"     report: {len(d.get('text',''))} chars")
    finals = [m for m in r["messages"] if m["role"] == "assistant"]
    if finals:
        print(f"     RÉPONSE FINALE: {finals[-1]['text'][:600]}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "send"
    conv = sys.argv[2] if len(sys.argv) > 2 else f"probe-{uuid.uuid4()}"
    text = sys.argv[3] if len(sys.argv) > 3 else "Bonjour"
    lang = "fr" if "--lang" in sys.argv and sys.argv[sys.argv.index("--lang")+1] == "fr" else \
           ("en" if "--lang" in sys.argv else "fr")
    report = "--report" in sys.argv
    think = "--think" in sys.argv
    auto = mode != "raw"
    r = run_turn(conv, text, lang=lang, report=report, think=think, auto_approve=auto)
    _fmt_result(r)
    print("\nJSON:", json.dumps(r, ensure_ascii=False)[:200])
