#!/usr/bin/env python3
"""
fc27_gallery_worker.py — Motor Autónomo de Galería EA Sports FC™ 27 Ultimate Team (Paletools).

Estrategia "Buy, Stamp & Flip" con Auto-Liquidación y Priorización de Mayor a Menor:
  1. Ordena los sets de mayor a menor cantidad de cartas que ya tenemos en el club (trackedCount DESC).
  2. Filtra solo sets incompletos (trackedCount < required).
  3. Dentro de cada set prioritario, adquiere primero las cartas más baratas disponibles en el mercado (<= max_buy).
  4. Límite de uso de monedas: Respeta un gasto máximo configurado (default: 15,000 monedas desde el inicio).
  5. Mueve la carta adquirida al Club para estamparla permanentemente en la Galería.
  6. La transfiere inmediatamente a la Lista de Transferibles y la pone en venta en el Mercado.
  7. Auto-Recuperación de Monedas:
     - Reclama automáticamente los fondos de ventas cerradas (clearSoldItems).
     - Relista automáticamente cualquier subasta expirada (relistExpiredAuctions).
     - Reclama objetivos y recompensas de hitos (claimMetaUTRewards).
  8. Notificador a Telegram: Envía alertas inmediatas al completar sets al 100% o superar hitos.
  9. Protocolo Anti-Ban estricto:
     - Zero Focus Theft (evaluación background en pestaña DevTools).
     - Jitter humano gaussiano (3.5s a 7.5s entre acciones).
     - Enfriamiento preventivo de 35s cada 6 búsquedas.
     - Enfriamiento de lote de 40s cada 3 compras exitosas.
"""

import argparse
import csv
import json
import os
import random
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CATALOG_FILE = DATA_DIR / "sets_catalog.json"
TRANSACTIONS_CSV = DATA_DIR / "transactions.csv"
WORKER_LOG = DATA_DIR / "worker.log"
CONFIG_FILE = ROOT_DIR / "config.json"
CONFIG_EXAMPLE = ROOT_DIR / "config.example.json"

def load_config():
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
    elif CONFIG_EXAMPLE.exists():
        try:
            with open(CONFIG_EXAMPLE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
    return cfg

CONFIG = load_config()

DEFAULT_ENDPOINT = os.getenv("RANUKITA_EXT_ENDPOINT", CONFIG.get("bridge", {}).get("endpoint", "http://127.0.0.1:9224/command"))
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", CONFIG.get("notifications", {}).get("telegram", {}).get("botToken", ""))
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", str(CONFIG.get("notifications", {}).get("telegram", {}).get("chatId", "")))
PROTECTED_PLAYERS = [p.lower() for p in CONFIG.get("trading", {}).get("protectedPlayers", ["yamal", "lamine", "messi", "mbappe", "vinicius", "bellingham", "rodri", "haaland", "valverde"])]

def log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now}] {msg}"
    print(line, flush=True)
    with open(WORKER_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def send_telegram_alert(text):
    if not TG_TOKEN or not TG_CHAT or "YOUR_TELEGRAM" in TG_TOKEN:
        return False
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        payload = json.dumps({"chat_id": TG_CHAT, "text": text, "parse_mode": "Markdown"}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode("utf-8")).get("ok", False)
    except Exception as e:
        log(f"Aviso al notificar Telegram: {e}")
        return False

def eval_js(js_code, tab_id, endpoint=DEFAULT_ENDPOINT, timeout=35):
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
        log(f"Aviso evaluando en pestaña {tab_id}: {e}")
        return {"ok": False, "error": str(e)}

def get_coins(tab_id, endpoint=DEFAULT_ENDPOINT):
    code = "services.User.getUser().coins.amount"
    res = eval_js(code, tab_id, endpoint=endpoint, timeout=10)
    return res.get("result", 0) if res.get("ok") else 0

def recover_market_coins_and_auctions(tab_id, endpoint=DEFAULT_ENDPOINT):
    """
    Auto-Cobrador: Reclama ventas cerradas, relista subastas expiradas y devuelve estado.
    """
    code = """
    (async () => {
      let clearedSold = 0;
      let relistedCount = 0;
      
      // 1. Cobrar ventas cerradas
      try {
        const cRes = await new Promise(res => {
          services.Item.clearSoldItems().observe({}, (s, r) => res(r));
        });
        if (cRes && cRes.data && cRes.data.itemIds) {
          clearedSold = cRes.data.itemIds.length;
        }
      } catch(e) {}
      
      // 2. Relistar subastas expiradas
      try {
        const rRes = await new Promise(res => {
          services.Item.relistExpiredAuctions().observe({}, (s, r) => res(r));
        });
        if (rRes && rRes.data && rRes.data.itemIds) {
          relistedCount = rRes.data.itemIds.length;
        }
      } catch(e) {}
      
      // 3. Estado de la lista de transferibles
      let activeCount = 0;
      try {
        await new Promise(r => services.Item.requestTransferItems().observe({}, () => r()));
        const items = Object.values(repositories.Item.transfer._collection || {});
        activeCount = items.filter(i => i._auction && i._auction.tradeState === 'active').length;
      } catch(e) {}
      
      return { clearedSold, relistedCount, activeCount };
    })()
    """
    res = eval_js(code, tab_id, endpoint=endpoint, timeout=15)
    data = res.get("result") or {}
    cleared = data.get("clearedSold", 0)
    relisted = data.get("relistedCount", 0)
    active = data.get("activeCount", 0)
    
    if cleared > 0:
        log(f"RECAUDACIÓN DE MERCADO: {cleared} venta(s) cerrada(s) cobrada(s) exitosamente.")
    if relisted > 0:
        log(f"AUTO-RELIST: {relisted} carta(s) expirada(s) relistada(s) automáticamente.")
        
    return {"cleared": cleared, "relisted": relisted, "active": active}

def auto_claim_rewards(tab_id, endpoint=DEFAULT_ENDPOINT):
    code = """
    (async () => {
      const results = { claimedCount: 0 };
      try {
        if (services.Objectives && services.Objectives.isObjectivesClaimAllEnabled()) {
          const res = await new Promise(r => {
            services.Objectives.claimMetaUTRewards().observe({}, (s, resp) => r(resp));
          });
          if (res && res.success && res.data) {
            const count = (res.data.items || res.data.rewards || []).length;
            results.claimedCount = count;
          }
        }
      } catch(e) {
        results.objectivesError = e.toString();
      }
      return results;
    })()
    """
    res = eval_js(code, tab_id, endpoint=endpoint, timeout=15)
    data = res.get("result") or {}
    claimed = data.get("claimedCount", 0)
    if claimed > 0:
        log(f"RECOMPENSAS DE OBJETIVOS: Se reclamaron {claimed} recompensa(s) en el club.")
    return data

def log_tx(action, set_name, player_name, def_id, buy_price, sell_price, status):
    exists = TRANSACTIONS_CSV.exists()
    with open(TRANSACTIONS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(["timestamp", "action", "setName", "playerName", "defId", "buyPrice", "sellPrice", "status"])
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            action, set_name, player_name, def_id, buy_price, sell_price, status
        ])

def sleep_human(min_s=3.5, max_s=6.5):
    d = round(random.uniform(min_s, max_s), 2)
    time.sleep(d)

def execute_buy_and_flip_cycle(tab_id, set_info, max_buy=750, endpoint=DEFAULT_ENDPOINT):
    set_id = set_info["setId"]
    set_name = set_info["setName"]
    cat_id = set_info["catId"]
    club_id = set_info.get("clubEaId")
    
    if not club_id:
        return {"success": False, "error": "No club ID"}
        
    log(f"Analizando set: {set_name} (Cat: {set_info['catName']}, Club ID: {club_id}, Set ID: {set_id})")
    
    # 1. Obtener lista de variantes faltantes
    get_missing_code = f"""
    (async () => {{
      const c = getAppMain()._rootViewController.currentController;
      const currentSub = c.childViewControllers[0];
      const activeVC = currentSub.currentController;
      if (!activeVC || activeVC.className !== '_0x42b2b1') return {{ error: 'not in gallery view' }};
      
      const v = activeVC.view;
      if (Number(v.category.getValue()) !== {cat_id}) {{
        v.category.setIndexById({cat_id});
        v.category._targets._collection.change[0].action.call(v.category._targets._collection.change[0].target);
        await new Promise(r => setTimeout(r, 250));
      }}
      v.set.setIndexById({set_id});
      v.set._targets._collection.change[0].action.call(v.set._targets._collection.change[0].target);
      
      for (let i = 0; i < 25; i++) {{
        await new Promise(r => setTimeout(r, 100));
        if (activeVC.albumEntries && activeVC.albumEntries.length > 0) break;
      }}
      
      const entries = activeVC.albumEntries || [];
      const missing = entries.filter(e => e.status === 'missing');
      const tracked = entries.filter(e => e.status === 'tracked');
      return {{
        trackedCount: tracked.length,
        required: activeVC.selectedSet ? activeVC.selectedSet.requiredItems : null,
        missingDefIds: missing.map(m => m.item.definitionId)
      }};
    }})()
    """
    m_res = eval_js(get_missing_code, tab_id, endpoint=endpoint, timeout=15)
    missing_data = m_res.get("result", {})
    if not missing_data or "error" in missing_data:
        log(f"Aviso de navegación: {missing_data.get('error', 'sin datos')}")
        return {"success": False, "error": missing_data.get("error")}
        
    missing_ids = missing_data.get("missingDefIds", [])
    tracked = missing_data.get("trackedCount", 0)
    req = missing_data.get("required", 15)
    log(f"Set '{set_name}': {tracked}/{req} completados. {len(missing_ids)} variantes faltantes.")
    
    if tracked >= req:
        log(f"¡Set '{set_name}' YA ESTÁ COMPLETO con {tracked}/{req}!")
        return {"success": True, "alreadyComplete": True}

    # 2. Buscar candidatos en mercado con Buy Now <= max_buy
    sleep_human(3.5, 6.0)
    search_code = f"""
    (async () => {{
      services.Item.clearTransferMarketCache();
      
      const criteria = new UTSearchCriteriaDTO();
      criteria.type = 'player';
      criteria.club = {club_id};
      criteria.maxBuy = {max_buy};
      criteria.count = 20;
      
      return await new Promise((resolve) => {{
        services.Item.searchTransferMarket(criteria, 1).observe({{}}, (sender, resp) => {{
          if (resp.success) {{
            const missingSet = new Set({json.dumps(missing_ids)});
            const validItems = (resp.data.items || []).filter(i => 
              i._auction && 
              i._auction.buyNowPrice > 0 && 
              i._auction.buyNowPrice <= {max_buy} &&
              missingSet.has(i.definitionId)
            );
            
            window._rkCandidates = validItems;
            
            resolve({{
              success: true,
              totalFound: resp.data.items ? resp.data.items.length : 0,
              matchingCount: validItems.length,
              candidates: validItems.map(m => ({{
                id: m.id,
                defId: m.definitionId,
                name: (m._staticData && m._staticData.name) || m.definitionId,
                rating: m.rating,
                buyNow: m._auction.buyNowPrice
              }}))
            }});
          }} else {{
            resolve({{ success: false, status: resp.status }});
          }}
        }});
      }});
    }})()
    """
    s_res = eval_js(search_code, tab_id, endpoint=endpoint, timeout=18)
    s_data = s_res.get("result", {})
    if not s_data or not s_data.get("success"):
        log(f"Mercado sin respuesta o aviso: {s_data.get('status') if s_data else 'error'}")
        return {"success": False, "marketError": s_data.get("status") if s_data else "error"}
        
    candidates = s_data.get("candidates", [])
    log(f"Mercado: {s_data.get('totalFound', 0)} cartas del club, {len(candidates)} faltan en tu Galería con Buy Now <= {max_buy}.")
    
    if not candidates:
        log(f"Sin cartas disponibles <= {max_buy} en este momento para {set_name}.")
        return {"success": True, "candidatesFound": 0}
        
    # Comprar la opción más barata disponible
    candidates.sort(key=lambda c: c["buyNow"])
    target = candidates[0]
    log(f"Comprando carta más barata: {target['name']} (OVR {target['rating']}, DefId: {target['defId']}) por {target['buyNow']} monedas")
    
    # 3. Comprar usando la entidad nativa
    sleep_human(2.5, 4.5)
    buy_code = f"""
    (async () => {{
      const candidate = (window._rkCandidates || []).find(i => i.id === {target['id']});
      if (!candidate) return {{ success: false, error: 'candidate not in memory' }};
      
      return await new Promise((resolve) => {{
        services.Item.bid(candidate, {target['buyNow']}).observe({{}}, (sender, resp) => {{
          resolve({{ success: resp.success, status: resp.status }});
        }});
      }});
    }})()
    """
    b_res = eval_js(buy_code, tab_id, endpoint=endpoint, timeout=15)
    b_data = b_res.get("result") or {}
    if not b_data.get("success"):
        log(f"Fallo en la compra de {target['name']}: {b_data.get('status', b_data.get('error'))}")
        return {"success": False, "buyFailed": b_data.get("status", b_data.get("error"))}
        
    log(f"COMPRA CONFIRMADA: {target['name']} adquirida por {target['buyNow']} monedas.")
    log_tx("BUY", set_name, target["name"], target["defId"], target["buyNow"], 0, "BOUGHT")
    
    # 4. Estampado en Club y Relistado Inmediato a Mercado
    # PROTECCIÓN ABSOLUTA: Si es Yamal o cualquier carta que el usuario quiera conservar, jamás vender
    if any(p in target["name"].lower() for p in PROTECTED_PLAYERS):
        log(f"ESCUDO DE SEGURIDAD: {target['name']} es una carta protegida. Se estampa en el Club y NUNCA se vende.")
        stamp_only_code = f"""
        (async () => {{
          const unassigned = Object.values(repositories.Item.unassigned._collection || {{}});
          const targetItem = unassigned.find(i => i.definitionId === {target['defId']});
          if (!targetItem) return {{ success: false, error: 'not in unassigned' }};
          const toClub = await new Promise(r => services.Item.move(targetItem, ItemPile.CLUB).observe({{}}, (s, res) => r(res)));
          return {{ toClub: toClub.success }};
        }})()
        """
        eval_js(stamp_only_code, tab_id, endpoint=endpoint, timeout=15)
        log_tx("STAMP_KEEP", set_name, target["name"], target["defId"], target["buyNow"], 0, "KEPT_IN_CLUB")
        return {"success": True, "player": target["name"], "price": target["buyNow"], "protected": True}

    sleep_human(2.5, 4.0)
    list_price = target["buyNow"] + 50 if target["buyNow"] >= 450 else 300
    start_bid = target["buyNow"] if target["buyNow"] >= 450 else 200
    
    stamp_and_list_code = f"""
    (async () => {{
      const unassigned = Object.values(repositories.Item.unassigned._collection || {{}});
      const targetItem = unassigned.find(i => i.definitionId === {target['defId']});
      if (!targetItem) return {{ success: false, error: 'not in unassigned' }};
      
      // PROTECCIÓN DE SEGURIDAD: Nunca vender cartas intransferibles o especiales
      if (targetItem.untradeable) {{
        await new Promise(r => services.Item.move(targetItem, ItemPile.CLUB).observe({{}}, (s, res) => r(res)));
        return {{ toClub: true, listed: false, protected: true }};
      }}
      
      // 1. Estampar en Club
      const toClub = await new Promise(r => services.Item.move(targetItem, ItemPile.CLUB).observe({{}}, (s, res) => r(res)));
      
      // 2. Mover a Transfer list
      const toTransfer = await new Promise(r => services.Item.move(targetItem, ItemPile.TRANSFER).observe({{}}, (s, res) => r(res)));
      
      // 3. Listar en mercado
      const listRes = await new Promise(r => services.Item.list(targetItem, {start_bid}, {list_price}, 3600).observe({{}}, (s, res) => r(res)));
      
      return {{
        toClub: toClub.success,
        toTransfer: toTransfer.success,
        listed: listRes.success
      }};
    }})()
    """
    flip_res = eval_js(stamp_and_list_code, tab_id, endpoint=endpoint, timeout=20)
    flip_data = flip_res.get("result") or {}
    
    if flip_data.get("toClub"):
        log(f"ESTAMPADO EN GALERÍA: {target['name']} sumada permanentemente al progreso.")
        log_tx("STAMP", set_name, target["name"], target["defId"], target["buyNow"], 0, "TRACKED_IN_GALLERY")
    
    if flip_data.get("listed"):
        log(f"AUTO-LIQUIDADOR: {target['name']} listada en Mercado por {start_bid}/{list_price} monedas (recuperando saldo).")
        log_tx("LIST", set_name, target["name"], target["defId"], target["buyNow"], list_price, "ACTIVE_ON_MARKET")
    else:
        log("Carta estampada en Galería; pendiente de reventa.")
        
    # Verificar si este fichaje completó el set al 100%
    new_tracked = tracked + 1
    if new_tracked >= req:
        log(f"¡HITO DE GALERÍA ALCANZADO! Set '{set_name}' ha alcanzado {new_tracked}/{req} (100% COMPLETADO).")
        coins = get_coins(tab_id, endpoint=endpoint)
        tg_msg = (
            f"🏆 *¡NUEVO HITO DE GALERÍA COMPLETADO!* 🎮\n\n"
            f"✨ *Set 100% Completado:* `{set_name}` ({new_tracked}/{req})\n"
            f"📁 *Categoría:* {set_info.get('catName')}\n"
            f"💰 *Saldo Actual:* {coins:,} monedas\n"
            f"♻️ *Estado:* Carta de cierre ({target['name']}) estampada y puesta en reventa inmediata."
        )
        send_telegram_alert(tg_msg)
        
    return {"success": True, "player": target["name"], "price": target["buyNow"], "completed": new_tracked >= req}

def run_loop(tab_id, max_rounds=30, max_spend=15000, min_coins_buffer=35000, max_buy_price=750, endpoint=DEFAULT_ENDPOINT):
    log("==================================================================")
    log("INICIANDO MOTOR DE COMPLETADO DE GALERÍA (EA SPORTS FC 27)")
    log("ORDEN: DE MAYOR A MENOR SEGÚN CANTIDAD DE CARTAS QUE YA TENEMOS")
    log(f"Configuración: Max rondas={max_rounds}, Límite gasto={max_spend:,}, Buffer piso={min_coins_buffer:,}, Max Buy={max_buy_price}")
    log(f"Telegram Notificaciones: {'ACTIVADAS' if TG_TOKEN and 'YOUR_TELEGRAM' not in TG_TOKEN else 'DESACTIVADAS'}")
    log("==================================================================")
    
    if not CATALOG_FILE.exists():
        log("No se encontró catálogo previo. Ejecute primero fc27_gallery_auditor.py")
        return
        
    with open(CATALOG_FILE, "r", encoding="utf-8") as f:
        catalog = json.load(f)
        
    # Filtrar solo sets incompletos que tengan ID de club y requerimientos válidos
    valid_sets = [s for s in catalog if s.get("clubEaId") and s.get("required") and s.get("trackedCount", 0) < s.get("required", 15)]
    
    # ORDENAMIENTO ESTRATÉGICO:
    # 1. Mayor cantidad de cartas ya coleccionadas (de mayor a menor) -> -trackedCount
    # 2. Menor cantidad restante para completar -> (required - trackedCount)
    valid_sets.sort(key=lambda s: (-s.get("trackedCount", 0), s.get("required", 15) - s.get("trackedCount", 0)))
    
    log(f"Sets candidatos ordenados por mayor progreso ({len(valid_sets)} sets pendientes):")
    for s in valid_sets[:8]:
        log(f"  • {s['setName']}: {s.get('trackedCount', 0)}/{s.get('required', 15)} (Faltan: {s.get('required', 15) - s.get('trackedCount', 0)})")
        
    initial_coins = get_coins(tab_id, endpoint=endpoint)
    # El piso de monedas no puede bajar de (saldo_inicial - max_spend) ni del buffer_piso
    floor_coins = max(initial_coins - max_spend, min_coins_buffer)
    log(f"Saldo inicial: {initial_coins:,} monedas. Piso mínimo protegido: {floor_coins:,} monedas (Límite gasto: {max_spend:,}).")
    
    rounds_done = 0
    consecutive_searches = 0
    
    # Cobro inicial de ventas y objetivos
    recover_market_coins_and_auctions(tab_id, endpoint=endpoint)
    auto_claim_rewards(tab_id, endpoint=endpoint)
    
    last_limit_alert_time = 0
    while rounds_done < max_rounds:
        coins = get_coins(tab_id, endpoint=endpoint)
        m_state = recover_market_coins_and_auctions(tab_id, endpoint=endpoint)
        
        log(f"\n--- RONDA {rounds_done + 1}/{max_rounds} | Saldo: {coins:,} monedas (Piso: {floor_coins:,}) | Subastas Activas: {m_state['active']} ---")
        
        if coins < floor_coins:
            now = time.time()
            if now - last_limit_alert_time > 1800:
                send_telegram_alert(f"⏳ *Galería FC27:* Límite de monedas alcanzado ({coins:,} / piso {floor_coins:,}). Esperando ventas activas ({m_state['active']} en mercado) para recuperar capital.")
                last_limit_alert_time = now
            log(f"Saldo ({coins:,}) alcanzó el límite permitido ({floor_coins:,}). Esperando ventas en mercado ({m_state['active']} activas)...")
            time.sleep(60)
            continue
            
        action_performed = False
        for target_set in valid_sets:
            req = target_set.get("required", 15)
            tr = target_set.get("trackedCount", 0)
            if tr >= req:
                continue
                
            res = execute_buy_and_flip_cycle(tab_id, target_set, max_buy=max_buy_price, endpoint=endpoint)
            consecutive_searches += 1
            
            if res.get("success") and res.get("player"):
                rounds_done += 1
                target_set["trackedCount"] = tr + 1
                action_performed = True
                
                # Cobrar ventas inmediatamente
                recover_market_coins_and_auctions(tab_id, endpoint=endpoint)
                
                log("Pausa de seguridad humana (8s) antes del siguiente objetivo...")
                time.sleep(8)
                break
            elif res.get("marketError"):
                log("Enfriando mercado por 45s ante aviso de API...")
                time.sleep(45)
                break
            else:
                time.sleep(4.0)
                
            # Cada 6 búsquedas, enfriar 35s
            if consecutive_searches % 6 == 0:
                log("Enfriamiento preventivo anti-ban (35 segundos)...")
                time.sleep(35)
                
        # Pausa de lote cada 3 compras exitosas
        if rounds_done > 0 and rounds_done % 3 == 0:
            log("Pausa de enfriamiento de lote (40 segundos)... Reclamando objetivos y ventas...")
            recover_market_coins_and_auctions(tab_id, endpoint=endpoint)
            auto_claim_rewards(tab_id, endpoint=endpoint)
            time.sleep(40)
            
        if not action_performed and consecutive_searches >= len(valid_sets):
            log("Se recorrieron los sets candidatos y no hay cartas baratas disponibles en este momento. Pausando 60s...")
            time.sleep(60)
            consecutive_searches = 0
            
    # Cobro final
    recover_market_coins_and_auctions(tab_id, endpoint=endpoint)
    auto_claim_rewards(tab_id, endpoint=endpoint)
    final_coins = get_coins(tab_id, endpoint=endpoint)
    log(f"\nSesión finalizada. Saldo final: {final_coins:,} monedas. Todos los eventos registrados en data/transactions.csv")

def main():
    parser = argparse.ArgumentParser(description="Worker de Galería FC 27")
    parser.add_argument("--tab-id", type=int, required=True, help="Tab ID de Chrome donde corre la Web App")
    parser.add_argument("--rounds", type=int, default=30, help="Cantidad de cartas a adquirir y rotar")
    parser.add_argument("--max-spend", type=int, default=15000, help="Límite máximo de monedas a usar del saldo inicial")
    parser.add_argument("--min-buffer", type=int, default=35000, help="Colchón intocable absoluto de monedas")
    parser.add_argument("--max-buy", type=int, default=750, help="Precio máximo en monedas para comprar")
    parser.add_argument("--endpoint", type=str, default=DEFAULT_ENDPOINT, help="Endpoint del bridge de Chrome")
    args = parser.parse_args()
    
    run_loop(args.tab_id, max_rounds=args.rounds, max_spend=args.max_spend, min_coins_buffer=args.min_buffer, max_buy_price=args.max_buy, endpoint=args.endpoint)

if __name__ == "__main__":
    main()
