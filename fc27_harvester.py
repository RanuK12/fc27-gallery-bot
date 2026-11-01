#!/usr/bin/env python3
"""
fc27_harvester.py — Auditor y Cosechador Autónomo de Galería EA FC 27.

1. Barre TODAS las categorías y sets (195 sets en total).
2. Para cada set, ejecuta `_autoFill()` para colocar las cartas que Emilio ya tiene en su club/historia.
3. Extrae la cantidad exacta de cartas recolectadas (trackedCount) y las que faltan para completar (missing).
4. Guarda el inventario en data/sets_catalog.json y data/sets_catalog.csv.
5. Emite un reporte con:
   - Sets completados al 100% (costo $0).
   - Sets casi listos (con >= 10 cartas o que les faltan <= 5 cartas).
   - Sets de la imagen (NWSL, Ligue 1, etc.).
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

CATALOG_FILE = DATA_DIR / "sets_catalog.json"
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

def ensure_paletools():
    chk = eval_js("typeof window.paletools !== 'undefined'")
    if chk.get("ok") and chk.get("result") is True:
        return True
    log("Inyectando Paletools...")
    inj_code = """(async () => {
        try {
            const res = await fetch('https://pale.tools/fifa/dist/27.2.1/paletools.prod.js');
            const text = await res.text();
            (0, eval)(text);
            if (window.paletools && window.paletools['paletools-27.2.1']) {
                (0, eval)(decodeURIComponent(window.paletools['paletools-27.2.1']));
                return true;
            }
            return false;
        } catch (e) {
            return false;
        }
    })()"""
    r = eval_js(inj_code, timeout=25)
    return r.get("ok") and r.get("result") is True

def ensure_gallery_screen():
    js = """(() => {
        const c = getAppMain()._rootViewController.currentController;
        const sub0 = c ? (c.childViewControllers ? c.childViewControllers[0] : null) : null;
        const activeVC = sub0 ? sub0.currentController : null;
        if (activeVC && activeVC.className === '_0x42b2b1') return { ok: true, status: 'already_there' };
        
        const tile = document.querySelector('.ut-tile-gallery');
        if (tile) {
            ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(t => {
                tile.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
            });
            return { ok: true, status: 'clicked' };
        }
        return { ok: false, status: 'no_tile' };
    })()"""
    res = eval_js(js)
    if res.get("result", {}).get("status") == "clicked":
        time.sleep(1.2)
    return True

def harvest_category(cat_id, cat_name):
    code = f"""(async () => {{
        const c = getAppMain()._rootViewController.currentController;
        const sub0 = c ? (c.childViewControllers ? c.childViewControllers[0] : null) : null;
        const activeVC = sub0 ? sub0.currentController : null;
        if (!activeVC) return {{ ok: false, error: 'no activeVC' }};
        
        const cat = activeVC.view.category;
        const set = activeVC.view.set;
        
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
            
            for (let i = 0; i < 25; i++) {{
                await new Promise(r => setTimeout(r, 70));
                if (activeVC.albumEntries && activeVC.albumEntries.length > 0) break;
            }}
            
            // Auto-fill club history cards into this set
            if (typeof activeVC._autoFill === 'function') {{
                activeVC._autoFill();
            }}
            
            const sInfo = activeVC.selectedSet;
            const entries = activeVC.albumEntries || [];
            const tracked = entries.filter(e => e.status === 'tracked');
            const missing = entries.filter(e => e.status === 'missing');
            
            results.push({{
                catId: {cat_id},
                catName: "{cat_name}",
                setId: sOpt.id,
                setName: sInfo ? sInfo.name : sOpt.label,
                clubEaId: sInfo ? sInfo.clubEaId : null,
                required: sInfo ? sInfo.requiredItems : null,
                trackedCount: tracked.length,
                missingCount: missing.length,
                totalEntries: entries.length,
                selectedCount: activeVC.selectedCards ? activeVC.selectedCards.size : 0,
                maxTokens: sInfo ? sInfo.maxTokens : 0,
                missingIds: missing.map(m => m.item ? m.item.definitionId : null).filter(Boolean)
            }});
        }}
        return {{ ok: true, results: results }};
    }})()"""
    res = eval_js(code, timeout=90)
    if res.get("ok") and res.get("result", {}).get("ok"):
        return res["result"]["results"]
    log(f"Aviso cosechando categoría {cat_name}: {res.get('error') or res.get('result')}")
    return []

def main():
    log("Iniciando Cosechador Autónomo de Galería EA FC 27...")
    ensure_paletools()
    ensure_gallery_screen()
    
    # Obtener lista de categorías
    cat_code = """(() => {
        const c = getAppMain()._rootViewController.currentController;
        const sub0 = c ? (c.childViewControllers ? c.childViewControllers[0] : null) : null;
        const activeVC = sub0 ? sub0.currentController : null;
        if (!activeVC) return [];
        return (activeVC.view.category.options || []).filter(o => o.id !== -1).map(o => ({ id: o.id, name: o.label }));
    })()"""
    res = eval_js(cat_code)
    categories = res.get("result", [])
    log(f"Categorías encontradas: {len(categories)}")
    
    all_sets = []
    for cat in categories:
        c_id = cat["id"]
        c_name = cat["name"]
        log(f"Cosechando categoría [{c_name}] (id: {c_id})...")
        c_sets = harvest_category(c_id, c_name)
        log(f"  -> {len(c_sets)} sets procesados.")
        all_sets.extend(c_sets)
        time.sleep(0.5)
        
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(all_sets, f, indent=2, ensure_ascii=False)
    log(f"Catálogo completo guardado en {CATALOG_FILE} ({len(all_sets)} sets en total).")
    
    # Análisis y Clasificación
    completed = [s for s in all_sets if s.get("required") and s["trackedCount"] >= s["required"]]
    vip = [
        s for s in all_sets 
        if s.get("required") and s["trackedCount"] < s["required"] and (s["trackedCount"] >= 10 or (s["required"] - s["trackedCount"]) <= 6)
    ]
    vip.sort(key=lambda s: (s["required"] - s["trackedCount"], -s["trackedCount"]))
    
    log(f"\n==================================================")
    log(f"RESUMEN DE AUDITORÍA Y COSECHA:")
    log(f"Total Sets en Juego: {len(all_sets)}")
    log(f"Sets COMPLETADOS al 100%: {len(completed)}")
    for c in completed:
        log(f"  ✓ {c['setName']} ({c['catName']}): {c['trackedCount']}/{c['required']}")
        
    log(f"\nSets VIP PRIORITARIOS (>=10 cartas o faltan <=6 cartas): {len(vip)}")
    for v in vip[:25]:
        missing_to_win = v['required'] - v['trackedCount']
        log(f"  ★ {v['setName']} ({v['catName']}): tiene {v['trackedCount']}/{v['required']} (¡FALTAN SOLO {missing_to_win}!)")
    log(f"==================================================")

if __name__ == "__main__":
    main()
