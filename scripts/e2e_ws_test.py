"""Test e2e par WebSocket : pose une question citoyenne et suit le tour complet.

Usage (dans le conteneur gateway) :
    python scripts/e2e_ws_test.py "Comment les députés ont-ils voté sur la motion de censure du 8 octobre 2024 ?"

Mime le frontend : envoie `user_input`, auto-approuve le `plan_proposal` (comme le
flag AUTO_APPROVE_PLAN du front), et imprime le déroulé (états FSM, tâches, widgets,
synthèse) jusqu'à DONE ou timeout.
"""
import asyncio
import json
import sys
import uuid

import websockets


async def main() -> int:
    question = sys.argv[1] if len(sys.argv) > 1 else "Bonjour !"
    timeout_s = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0
    conv = f"e2e-{uuid.uuid4().hex[:8]}"
    uri = f"ws://localhost:8000/ws/{conv}"
    print(f"conv={conv}\nQ: {question}\n---")
    approved = set()
    final_msg = ""
    widgets_seen = []
    state = "?"
    async with websockets.connect(uri, max_size=8 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "user_input", "text": question, "lang": "fr"}))
        try:
            async with asyncio.timeout(timeout_s):
                while True:
                    raw = await ws.recv()
                    frame = json.loads(raw)
                    if frame.get("kind") == "progress":
                        d = frame.get("data") or {}
                        if d.get("kind") in ("thinking", "tool_call", "tool_result"):
                            td = d.get("trace_data") or {}
                            print(f"[trace:{d['kind']}] {d.get('agent_id')} "
                                  f"{td.get('tool') or (td.get('text') or '')[:80]} "
                                  f"{'via ' + td['mcp_server'] if td.get('mcp_server') else ''} "
                                  f"{td.get('preview') or td.get('error') or td.get('skipped') or ''}")
                        continue
                    ev = frame.get("data") or {}
                    et = ev.get("type")
                    p = ev.get("payload") or {}
                    if et == "status":
                        state = p.get("state", "?")
                        print(f"[état] {state}")
                        if state in ("DONE", "FAILED"):
                            break
                    elif et == "widget":
                        wt = p.get("widget_type")
                        widgets_seen.append(wt)
                        if wt == "plan_proposal" and p.get("widget_id") not in approved:
                            approved.add(p.get("widget_id"))
                            steps = (p.get("data") or {}).get("steps", [])
                            print(f"[plan] {[s['agent_type'] for s in steps]} → auto-approve")
                            await ws.send(json.dumps(
                                {"type": "plan_submit", "steps": steps, "lang": "fr"}))
                        elif wt in ("vote_chart", "fiche_depute", "timeline_loi", "source_list"):
                            d = p.get("data") or {}
                            srcs = [s.get("url") for s in (d.get("sources") or [])]
                            print(f"[widget] {wt} — sources: {srcs}")
                            if wt == "vote_chart":
                                sc = d.get("scrutin") or {}
                                print(f"         scrutin n°{sc.get('numero')} {sc.get('sort')} "
                                      f"pour={sc.get('pour')} contre={sc.get('contre')} "
                                      f"abst={sc.get('abstentions')} nv={sc.get('non_votants')}")
                            if wt == "fiche_depute":
                                dep = d.get("depute") or {}
                                print(f"         {dep.get('nom_complet')} — {dep.get('groupe')} — "
                                      f"{dep.get('circo')} actif={dep.get('actif')}")
                            if wt == "timeline_loi":
                                print(f"         dossier: {(d.get('dossier') or {}).get('titre')} "
                                      f"statut: {(d.get('dossier') or {}).get('statut')} "
                                      f"étapes: {len(d.get('etapes') or [])}")
                        else:
                            print(f"[widget] {wt}")
                    elif et == "message":
                        final_msg = p.get("text", "")
                        print(f"[message] {final_msg[:400]}")
                    elif et == "task_update":
                        print(f"[tâche] {p.get('agent_type')} → {p.get('status')} "
                              f"{p.get('note') or ''}")
        except TimeoutError:
            print(f"!! TIMEOUT après {timeout_s}s (état={state})")
            return 2
    print(f"---\nétat final: {state} · widgets: {widgets_seen}")
    return 0 if state == "DONE" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
