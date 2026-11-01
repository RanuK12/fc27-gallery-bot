#!/usr/bin/env python3
"""
fc27_audit_all_plantillas.py — Auditor Exhaustivo de Plantillas de Galería EA FC 27.

Prueba CADA UNO de los 195 sets de todas las ligas y categorías:
1. Llena la plantilla con los jugadores que Emilio ya tiene en su club (_autoFill).
2. Determina cuántos jugadores entran en la plantilla (canFillInPlantilla) y cuántos faltan (missingToFullPlantilla).
3. Identifica a los jugadores faltantes específicos (nombre, media, rareza).
4. Clasifica los sets por proximidad a completarse para llegar a Nivel 10 al instante.
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

AUDIT_FILE = DATA_DIR / "plantillas_audit.json"
CONFIG_FILE = ROOT_DIR / "config.json"

def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

CONFIG = load_config()
ENDPOINT = os.getenv("RANUKITA_EXT_ENDPOINT", CONFIG.get("bridge", {}).get("endpoint", "http://127.0.0.1:9224/command"))
TAB_ID = CONFIG.get("bridge", {}).get("defaultTabId", 1233340013)

def log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)

def eval_js(js_code, timeout=30):
    payload = {"action": "eval", "tabId": int(TAB_ID), "js": js_code}
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log(f"Error evaluando JS: {e}")
        return {"ok": False, "error": str(e)}

def ensure_gallery_screen():
    js = """(async () => {
        const topC = getAppMain()._rootViewController.currentController;
        const sub0 = topC ? (topC.childViewControllers ? topC.childViewControllers[0] : null) : null;
        const curVC = sub0 ? sub0.currentController : null;
        if (curVC && curVC.className === '_0x42b2b1' && curVC.view && curVC.view.category) {
            return { ok: true, status: 'already_ready' };
        }
        
        let tile = document.querySelector('.ut-tile-gallery');
        if (!tile) {
            const home = document.querySelector('.icon-home');
            if (home) { home.click(); await new Promise(r => setTimeout(r, 600)); }
            tile = document.querySelector('.ut-tile-gallery');
        }
        if (tile) {
            ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(t => {
                tile.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
            });
            await new Promise(r => setTimeout(r, 1000));
        }
        return { ok: true, status: 'mounted' };
    })()"""
    res = eval_js(js, timeout=15)
    return res.get("ok")

def audit_category(cat_id, cat_name):
    code = f"""(async () => {{
        const topC = getAppMain()._rootViewController.currentController;
        const sub0 = topC ? (topC.childViewControllers ? topC.childViewControllers[0] : null) : null;
        const vc = sub0 ? sub0.currentController : null;
        if (!vc || !vc.view || !vc.view.category) return {{ error: 'no_vc_or_view' }};
        
        const cat = vc.view.category;
        const set = vc.view.set;
        
        if (Number(cat.getValue()) !== {cat_id}) {{
            cat.setIndexById({cat_id});
            cat._targets._collection.change[0].action.call(cat._targets._collection.change[0].target);
            await new Promise(r => setTimeout(r, 200));
        }}
        
        const setOpts = (set.options || []).filter(s => s.id !== -1);
        const results = [];
        
        for (const sOpt of setOpts) {{
            set.setIndexById(sOpt.id);
            set._targets._collection.change[0].action.call(set._targets._collection.change[0].target);
            
            for (let i = 0; i < 20; i++) {{
                await new Promise(r => setTimeout(r, 50));
                if (vc.albumEntries && vc.albumEntries.length > 0) break;
            }}
            
            // Auto-fill club history cards into this set
            if (typeof vc._autoFill === 'function') {{
                vc._autoFill();
            }}
            
            const sInfo = vc.selectedSet;
            const req = sInfo ? sInfo.requiredItems : 0;
            const inClub = vc.selectedCards ? vc.selectedCards.size : 0;
            const entries = vc.albumEntries || [];
            const tracked = entries.filter(e => e.status === 'tracked');
            const missing = entries.filter(e => e.status === 'missing');
            
            // Unique missing players
            const missingUnique = [];
            const seen = new Set();
            for (const m of missing) {{
                if (m.item && m.item._staticData) {{
                    const name = m.item._staticData.name;
                    if (!seen.has(name)) {{
                        seen.add(name);
                        missingUnique.push({{
                            name: name,
                            rating: m.item.rating,
                            defId: m.item.definitionId,
                            rareflag: m.item.rareflag
                        }});
                    }}
                }}
            }}
            
            results.push({{
                catId: {cat_id},
                catName: "{cat_name}",
                setId: sOpt.id,
                setName: sInfo ? sInfo.name : sOpt.label,
                clubEaId: sInfo ? sInfo.clubEaId : null,
                required: req,
                canFillInPlantilla: inClub,
                missingToFullPlantilla: Math.max(0, req - inClub),
                totalTrackedEver: tracked.length,
                totalMissingInSet: missing.length,
                missingPlayersCount: missingUnique.length,
                missingPlayersSample: missingUnique.slice(0, 8),
                is100PercentInClub: req > 0 && inClub >= req
            }});
        }}
        return {{ ok: true, results: results }};
    }})()"""
    res = eval_js(code, timeout=90)
    if res.get("ok") and res.get("result", {}).get("ok"):
        return res["result"]["results"]
    log(f"Aviso auditando categoría {cat_name}: {res.get('error') or res.get('result')}")
    return []

def main():
    log("Iniciando Auditor Exhaustivo de Plantillas de Galería...")
    ensure_gallery_screen()
    
    # Obtener lista de categorías
    cat_code = """(() => {
        const topC = getAppMain()._rootViewController.currentController;
        const sub0 = topC ? (topC.childViewControllers ? topC.childViewControllers[0] : null) : null;
        const vc = sub0 ? sub0.currentController : null;
        if (!vc || !vc.view || !vc.view.category) return [];
        return (vc.view.category.options || []).filter(o => o.id !== -1).map(o => ({ id: o.id, name: o.label }));
    })()"""
    res = eval_js(cat_code)
    categories = res.get("result", [])
    log(f"Categorías encontradas: {len(categories)}")
    
    all_sets = []
    for cat in categories:
        c_id = cat["id"]
        c_name = cat["name"]
        log(f"Auditando plantillas de [{c_name}] (id: {c_id})...")
        c_sets = audit_category(c_id, c_name)
        log(f"  -> {len(c_sets)} sets auditados.")
        all_sets.extend(c_sets)
        time.sleep(0.3)
        
    with open(AUDIT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_sets, f, indent=2, ensure_ascii=False)
    log(f"Auditoría completa guardada en {AUDIT_FILE} ({len(all_sets)} sets en total).")
    
    # Clasificación por proximidad
    ready_now = [s for s in all_sets if s.get("required") and s["canFillInPlantilla"] >= s["required"]]
    missing_1 = [s for s in all_sets if s.get("required") and (s["required"] - s["canFillInPlantilla"]) == 1]
    missing_2 = [s for s in all_sets if s.get("required") and (s["required"] - s["canFillInPlantilla"]) == 2]
    missing_3_to_5 = [s for s in all_sets if s.get("required") and 3 <= (s["required"] - s["canFillInPlantilla"]) <= 5]
    
    print("\n" + "="*70)
    print("REPORTE REAL DE PLANTILLAS Y DISPONIBILIDAD EN CLUB (EA FC 27)")
    print("="*70)
    
    print(f"\n1. SETS CON PLANTILLA 100% LISTA EN EL CLUB ({len(ready_now)} sets):")
    print("   (Ya tenés los jugadores en el club. Solo requieren armar y enviar plantilla en consola)")
    for r in ready_now:
        print(f"   ✓ {r['setName']} ({r['catName']}): {r['canFillInPlantilla']}/{r['required']} jugadores en club")
        
    print(f"\n2. SETS A SOLO 1 JUGADOR DE PLANTILLA COMPLETA ({len(missing_1)} sets):")
    for m in missing_1:
        samples = ", ".join([p["name"] for p in m["missingPlayersSample"][:3]])
        print(f"   ★ {m['setName']} ({m['catName']}): {m['canFillInPlantilla']}/{m['required']} (Falta 1!). Ejemplos faltantes: {samples}")
        
    print(f"\n3. SETS A SOLO 2 JUGADORES DE PLANTILLA COMPLETA ({len(missing_2)} sets):")
    for m in missing_2:
        samples = ", ".join([p["name"] for p in m["missingPlayersSample"][:3]])
        print(f"   ★ {m['setName']} ({m['catName']}): {m['canFillInPlantilla']}/{m['required']} (Faltan 2). Ejemplos faltantes: {samples}")

    print(f"\n4. SETS A 3-5 JUGADORES DE PLANTILLA COMPLETA ({len(missing_3_to_5)} sets):")
    for m in missing_3_to_5[:15]:
        missing_n = m["required"] - m["canFillInPlantilla"]
        samples = ", ".join([p["name"] for p in m["missingPlayersSample"][:3]])
        print(f"   • {m['setName']} ({m['catName']}): {m['canFillInPlantilla']}/{m['required']} (Faltan {missing_n}). Ejemplos: {samples}")
    print("="*70)

if __name__ == "__main__":
    main()
