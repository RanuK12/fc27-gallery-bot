#!/usr/bin/env python3
"""
fc27_gallery_auditor.py — Auditor y Extractor del Catálogo de Galería de EA Sports FC™ 27.

Inspecciona todos los sets y categorías de la Galería vía Paletools v27+ y la Web App oficial,
extrayendo de forma no destructiva:
  - IDs de Sets y Categorías
  - Umbrales de cartas requeridas por set (requiredItems)
  - Cartas ya coleccionadas (tracked) vs faltantes (missing)
  - Umbrales de puntuación por grado (D, C, B, A, S) y tokens máximos
  - Lista de definitionIds de variantes faltantes para alimentar al worker de mercado
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_ENDPOINT = os.getenv("RANUKITA_EXT_ENDPOINT", "http://127.0.0.1:9224/command")

def log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)

def eval_js(js_code, tab_id, endpoint=DEFAULT_ENDPOINT, timeout=30):
    payload = {
        "action": "eval",
        "tabId": int(tab_id),
        "js": js_code
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log(f"Error evaluando en pestaña {tab_id}: {e}")
        return {"ok": False, "error": str(e)}

def ensure_gallery_view(tab_id, endpoint=DEFAULT_ENDPOINT):
    log("Comprobando y asegurando vista de Galería...")
    code = """
    (() => {
      const c = getAppMain()._rootViewController.currentController;
      if (!c || !c.childViewControllers) return { status: 'no_app' };
      const sub0 = c.childViewControllers[0];
      const activeVC = sub0 ? sub0.currentController : null;
      
      if (activeVC && activeVC.className === '_0x42b2b1') {
        return { status: 'ready' };
      }
      
      // Si estamos en otra pestaña, navegar a Inicio
      c.view.tabItems[0].__root.click();
      if (sub0 && sub0.popToRootViewController) {
        sub0.popToRootViewController();
      }
      
      setTimeout(() => {
        const tile = document.querySelector('.ut-tile-gallery');
        if (tile) {
          ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(t => {
            tile.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
          });
        }
      }, 350);
      return { status: 'navigating' };
    })()
    """
    res = eval_js(code, tab_id, endpoint=endpoint)
    if res.get("result", {}).get("status") == "ready":
        return True
    time.sleep(1.5)
    check = eval_js("getAppMain()._rootViewController.currentController.childViewControllers[0].currentController.className", tab_id, endpoint=endpoint)
    return check.get("result") == "_0x42b2b1"

def audit_catalog(tab_id, endpoint=DEFAULT_ENDPOINT):
    if not ensure_gallery_view(tab_id, endpoint=endpoint):
        log("No se pudo confirmar la vista de Galería. Verifique que la Web App y Paletools estén cargados.")
        return []

    log("Descubriendo categorías y sets registrados...")
    code = """
    (async () => {
      const c = getAppMain()._rootViewController.currentController;
      const currentSub = c.childViewControllers[0];
      const activeVC = currentSub.currentController;
      const v = activeVC.view;
      const cat = v.category;
      const set = v.set;
      const h = cat._targets._collection.change[0];
      const catOpts = cat.options.filter(o => o.id !== -1);
      
      const allSets = [];
      for (let cOpt of catOpts) {
        cat.setIndexById(cOpt.id);
        h.action.call(h.target);
        const setOpts = (set.options || []).filter(s => s.id !== -1);
        for (let sOpt of setOpts) {
          allSets.push({
            catId: cOpt.id,
            catName: cOpt.label,
            setId: sOpt.id,
            setName: sOpt.label
          });
        }
      }
      return allSets;
    })()
    """
    res = eval_js(code, tab_id, endpoint=endpoint, timeout=20)
    sets_list = res.get("result", [])
    log(f"Detectados {len(sets_list)} sets totales en catálogo.")
    
    detailed = []
    for idx, s in enumerate(sets_list):
        set_id = s["setId"]
        cat_id = s["catId"]
        
        detail_code = f"""
        (async () => {{
          const c = getAppMain()._rootViewController.currentController;
          const currentSub = c.childViewControllers[0];
          const activeVC = currentSub.currentController;
          const v = activeVC.view;
          
          if (Number(v.category.getValue()) !== {cat_id}) {{
            v.category.setIndexById({cat_id});
            v.category._targets._collection.change[0].action.call(v.category._targets._collection.change[0].target);
            await new Promise(r => setTimeout(r, 200));
          }}
          
          v.set.setIndexById({set_id});
          v.set._targets._collection.change[0].action.call(v.set._targets._collection.change[0].target);
          
          for (let i = 0; i < 25; i++) {{
            await new Promise(r => setTimeout(r, 80));
            if (activeVC.albumEntries && activeVC.albumEntries.length > 0) break;
          }}
          
          const sInfo = activeVC.selectedSet;
          const entries = activeVC.albumEntries || [];
          const tracked = entries.filter(e => e.status === 'tracked');
          const missing = entries.filter(e => e.status === 'missing');
          
          return {{
            setId: {set_id},
            setName: sInfo ? sInfo.name : "{s['setName']}",
            catId: {cat_id},
            catName: "{s['catName']}",
            clubEaId: sInfo ? sInfo.clubEaId : null,
            required: sInfo ? sInfo.requiredItems : null,
            trackedCount: tracked.length,
            missingCount: missing.length,
            totalEntries: entries.length,
            maxTokens: sInfo ? sInfo.maxTokens : 0,
            grades: sInfo ? sInfo.grades : [],
            missingSamples: missing.slice(0, 5).map(m => ({{
              defId: m.item.definitionId,
              rating: m.item.rating,
              name: (m.item._staticData && m.item._staticData.name) || m.item.definitionId
            }}))
          }};
        }})()
        """
        d_res = eval_js(detail_code, tab_id, endpoint=endpoint, timeout=12)
        d = d_res.get("result")
        if d:
            detailed.append(d)
            if (idx + 1) % 15 == 0 or idx == len(sets_list) - 1:
                log(f"Auditados [{idx+1}/{len(sets_list)}] sets: {d['setName']} (Tracked: {d['trackedCount']}/{d.get('required')})")
        time.sleep(0.3)
        
    json_path = DATA_DIR / "sets_catalog.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(detailed, f, indent=2, ensure_ascii=False)
        
    csv_path = DATA_DIR / "sets_catalog.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["catId", "catName", "setId", "setName", "clubEaId", "required", "trackedCount", "missingCount", "totalEntries", "maxTokens"])
        for d in detailed:
            writer.writerow([
                d["catId"], d["catName"], d["setId"], d["setName"],
                d.get("clubEaId", ""), d.get("required", ""),
                d["trackedCount"], d["missingCount"], d["totalEntries"], d.get("maxTokens", 0)
            ])
            
    log(f"Auditoría completada exitosamente. Guardado en {json_path} y {csv_path}")
    return detailed

def main():
    parser = argparse.ArgumentParser(description="Auditor de Galería EA Sports FC 27")
    parser.add_argument("--tab-id", type=int, required=True, help="Tab ID de Chrome donde corre la Web App")
    parser.add_argument("--endpoint", type=str, default=DEFAULT_ENDPOINT, help="Endpoint del bridge de Chrome")
    args = parser.parse_args()
    
    audit_catalog(args.tab_id, endpoint=args.endpoint)

if __name__ == "__main__":
    main()
