"""Test E2E du partage de fichiers inter-agents par URI bucket (à lancer dans yosh).

Prouve, contre le cluster live (via le port-forward gateway sur :18000) :
 1. upload PJ → gateway pousse les octets dans le bucket `hemicycle` et renvoie s3_uri ;
 2. une investigation avec PJ route vers un agent qui RÉCUPÈRE le document complet
    via pull_doc_from_bucket — vérifié par un SENTINELLE placé APRÈS le seuil d'aperçu
    (_ATTACH_PREVIEW_CHARS = 800). Si la sortie reflète le sentinelle, le pull a marché.

Usage : python3 bucket_e2e_probe.py "<mission>" [lang]
"""
import json
import sys
import time
import uuid

import requests
import websocket

GW_HTTP = "http://localhost:18000"
GW_WS = "ws://localhost:18000"
SENTINEL = "SENTINELLE_OMEGA_47 : la contre-offensive décisive se déclenche au sud à l'aube."


def make_doc() -> bytes:
    """Document > 8 Ko : bourrage avant le sentinelle pour le pousser bien après 800 chars."""
    filler = ("Contexte de renseignement. " * 80 + "\n") * 12   # ~ 27 Ko, sentinelle noyé loin
    body = filler[:6000] + "\n\n" + SENTINEL + "\n\n" + filler[:4000]
    return body.encode("utf-8")


def upload(conv_id: str) -> dict:
    files = {"file": ("note_ops.txt", make_doc(), "text/plain")}
    r = requests.post(f"{GW_HTTP}/api/attachments?conv_id={conv_id}", files=files, timeout=30)
    r.raise_for_status()
    return r.json()


def last_seq(conv_id):
    try:
        evs = requests.get(f"{GW_HTTP}/api/events/{conv_id}", timeout=5).json()
        return max((e.get("seq", 0) for e in evs), default=0)
    except Exception:
        return 0


def run(mission: str, lang: str = "fr"):
    conv_id = f"bucket-e2e-{uuid.uuid4().hex[:8]}"
    att = upload(conv_id)
    print(f"[upload] id={att['id']}  s3_uri={att.get('s3_uri')}  chars_extraits={att.get('chars')}")
    assert att.get("s3_uri"), "ÉCHEC : le gateway n'a pas renvoyé d's3_uri (push bucket KO)"

    base = last_seq(conv_id)
    ws = websocket.create_connection(f"{GW_WS}/ws/{conv_id}", timeout=15)
    ws.settimeout(15)
    msg = {"type": "user_input", "text": mission, "lang": lang,
           "user_id": "analyst-1", "attachments": [{"id": att["id"]}]}
    ws.send(json.dumps(msg))

    plan_submitted = False
    traces, messages, agents = [], [], []
    t0, hard_cap, idle = time.time(), 220.0, 35.0
    last_rx, final_state, seen = time.time(), None, set()
    while time.time() - t0 < hard_cap:
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            if time.time() - last_rx > idle and final_state in ("DONE", "FAILED", None):
                break
            continue
        except Exception:
            break
        if not raw:
            continue
        last_rx = time.time()
        env = json.loads(raw)
        if env.get("kind") != "event":
            continue
        data = env.get("data") or {}
        seq = data.get("seq")
        if seq in seen or (seq is not None and seq <= base):
            continue
        seen.add(seq)
        etype = data.get("type")
        payload = data.get("payload") or {}
        traces.append(json.dumps(data, ensure_ascii=False))
        if etype == "status":
            final_state = payload.get("state")
            print(f"    · status → {final_state}  (+{time.time()-t0:4.1f}s)")
        elif etype == "message":
            role = payload.get("role")
            body = (payload.get("text") or "").strip()
            messages.append(f"{role}: {body}")
            if role == "assistant":
                print(f"    💬 assistant: {body[:300]}")
        elif etype == "widget":
            wt = payload.get("widget_type")
            if wt == "plan_proposal" and not plan_submitted:
                steps = (payload.get("data") or {}).get("steps") or []
                agents = [s.get("agent_type") for s in steps]
                print(f"    📋 plan: {agents} → auto-approve")
                ws.send(json.dumps({"type": "plan_submit", "steps": steps,
                                    "user_id": "analyst-1", "lang": lang}))
                plan_submitted = True
        if final_state in ("DONE", "FAILED") and time.time() - last_rx > 3:
            break
    ws.close()

    haystack = "\n".join(traces).lower()
    pulled = ("omega_47" in haystack) or ("contre-offensive" in haystack) or \
             ("counter-offensive" in haystack) or ("counteroffensive" in haystack)
    print(f"\n[bilan] agents={agents}  events={len(traces)}  sentinelle_dans_sortie={pulled}")
    print("VERDICT PULL =", "OK (document complet récupéré au-delà de l'aperçu)" if pulled
          else "NON CONCLUANT (sentinelle absent — voir trace)")
    # Échantillon de trace pour diagnostic
    for m in messages[-3:]:
        print("  msg:", m[:400])
    return conv_id, att


if __name__ == "__main__":
    mission = sys.argv[1] if len(sys.argv) > 1 else "Traduis ce document en anglais."
    lang = sys.argv[2] if len(sys.argv) > 2 else "fr"
    run(mission, lang)
