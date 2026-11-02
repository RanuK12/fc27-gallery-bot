#!/usr/bin/env python3
"""
fc27_targeted_sniper.py — Francotirador de Galería de Alta Precisión (EA FC 27).

Prioridad estricta indicada por Emilio:
1. Sets a punto de completarse:
   - Liverpool (Cat 5, Set 46, Club 9) — 16/20
   - NWSL Ligas (Cat 6, Set 193, League 2221) — 17/30 (Golds)
   - Atlético de Madrid (Cat 1, Set 53, Club 240) — 15/20
   - Lombardia FC (Cat 3, Set 107, Club 131682) — 9/15
   - Paris Saint-Germain (Cat 2, Set 95, Club 73) — 13/20
   - FC Barcelona (Cat 1, Set 68, Club 241) — 12/20

Protocolo:
- Buy, Stamp & Flip: compra la carta más barata disponible, la estampa en el Club y la revende en mercado de inmediato.
- Liquidación rápida: 650/750 monedas (Golds) para rotación veloz del saldo.
- Cobro automático de ventas cerradas y relistado de expiradas.
- Reclamo automático de objetivos y recompensas UT.
- Alerta a Telegram cuando un set llega al 100%.
- Límite mínimo de seguridad: Piso absoluto de 15,000 monedas.
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

CONFIG_FILE = ROOT_DIR / "config.json"
TRANSACTIONS_CSV = DATA_DIR / "transactions.csv"
SNIPER_LOG = DATA_DIR / "sniper.log"

def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

CONFIG = load_config()
ENDPOINT = os.getenv("RANUKITA_EXT_ENDPOINT", CONFIG.get("bridge", {}).get("endpoint", "http://127.0.0.1:9224/command"))

def get_ea_tab():
    try:
        req = urllib.request.Request(ENDPOINT, data=json.dumps({"action": "list_tabs"}).encode("utf-8"), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
            for t in data.get("tabs", []):
                if "ea.com" in t.get("url", ""):
                    return t["id"]
    except Exception:
        pass
    return CONFIG.get("bridge", {}).get("defaultTabId", 1233340208)

DEFAULT_TAB = get_ea_tab()
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", CONFIG.get("notifications", {}).get("telegram", {}).get("botToken", ""))
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", str(CONFIG.get("notifications", {}).get("telegram", {}).get("chatId", "")))
MIN_BUFFER = CONFIG.get("trading", {}).get("minCoinsBuffer", 5000)

ACTIVE_SQUAD_SHIELD = [
    "donnarumma", "hakimi", "saliba", "van dijk", "capdevila", "touré", "toure",
    "bellingham", "aimar", "lamine yamal", "yamal", "ferran torres", "diomande",
    "rodman", "anyomi", "barcola", "anderson", "camavinga", "lacroix", "nkunku",
    "matheus nunes", "doku", "spence", "barella", "delgado"
]
PROTECTED_PLAYERS = list(set([p.lower() for p in CONFIG.get("trading", {}).get("protectedPlayers", ["yamal", "lamine", "messi", "mbappe", "vinicius", "bellingham", "rodri", "haaland", "valverde"])] + ACTIVE_SQUAD_SHIELD))

TARGET_SETS = [
    {"name": "Liga F Moeve", "catId": 6, "setId": 121, "clubId": None, "leagueId": 2222, "isGold": False, "req": 30, "maxBuy": 900},
    {"name": "Newcastle United", "catId": 5, "setId": 92, "clubIds": [13], "leagueId": None, "req": 15, "maxBuy": 900},
    {"name": "Al Hilal", "catId": 8, "setId": 190, "clubIds": [605], "leagueId": None, "req": 15, "maxBuy": 900},
    {"name": "Fulham", "catId": 5, "setId": 54, "clubIds": [144], "leagueId": None, "req": 15, "maxBuy": 900},
]

def log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now}] {msg}"
    print(line, flush=True)
    with open(SNIPER_LOG, "a", encoding="utf-8") as f:
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
        log(f"Aviso Telegram: {e}")
        return False

def log_tx(action, set_name, player_name, def_id, buy_price, sell_price, status):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_exists = TRANSACTIONS_CSV.exists()
    with open(TRANSACTIONS_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(["timestamp", "action", "set_name", "player_name", "def_id", "buy_price", "sell_price", "status"])
        w.writerow([now, action, set_name, player_name, def_id, buy_price, sell_price, status])

def eval_js(js_code, tab_id=DEFAULT_TAB, timeout=30):
    payload = {"action": "cdp_eval", "tabId": int(tab_id), "expr": js_code, "_timeout": timeout}
    req = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log(f"Error evaluando JS: {e}")
        return {"ok": False, "error": str(e)}

def get_coins(tab_id):
    res = eval_js("services.User.getUser().coins.amount", tab_id=tab_id, timeout=8)
    if not res.get("ok"):
        return 0
    val = res.get("result")
    try:
        return int(val) if val is not None else 0
    except (ValueError, TypeError):
        return 0

def recover_market_and_rewards(tab_id):
    js = """(async () => {
        let sold = 0;
        let relisted = 0;
        try {
            if (services.Item && typeof services.Item.clearSoldItems === 'function') {
                sold = await new Promise(r => services.Item.clearSoldItems().observe({}, (s, res) => r(res && res.success ? 1 : 0)));
            }
        } catch (e) {}
        try {
            const unassigned = Object.values(repositories.Item.unassigned._collection || {});
            for (const item of unassigned) {
                if (item.untradeable) {
                    await new Promise(r => services.Item.move(item, ItemPile.CLUB).observe({}, (s, res) => r(res)));
                }
            }
        } catch (e) {}
        try {
            const transfer = repositories.Item.transfer;
            const list = transfer ? Object.values(transfer._collection || {}) : [];
            const expiredOrInactive = list.filter(i => !i._auction || i._auction.tradeState === 'expired' || i._auction.tradeState === 'inactive');
            for (const item of expiredOrInactive) {
                const sb = (item._auction && item._auction.startingBid) || ((item.rating || 0) >= 75 ? 650 : 200);
                const bn = (item._auction && item._auction.buyNowPrice) || ((item.rating || 0) >= 75 ? 750 : 300);
                try {
                    const res = await new Promise(r => services.Item.list(item, sb, bn, 3600).observe({}, (s, res) => r(res)));
                    if (res && res.success) relisted++;
                } catch(e) {}
                await new Promise(r => setTimeout(r, 400));
            }
        } catch (e) {}
        try {
            if (services.Objectives && typeof services.Objectives.claimAllRewards === 'function') {
                services.Objectives.claimAllRewards();
            }
        } catch (e) {}
        
        let activeCount = 0;
        try {
            const list = repositories.Item.transfer ? Object.values(repositories.Item.transfer._collection || {}) : [];
            activeCount = list.filter(i => i._auction && i._auction.expires > 0).length;
        } catch (e) {}
        
        return { ok: true, soldCollected: sold, relistedCount: relisted, activeListings: activeCount };
    })()"""
    res = eval_js(js, tab_id=tab_id, timeout=20)
    return res.get("result", {}) or {}

def process_target_set(tab_id, target):
    name = target["name"]
    cat_id = target["catId"]
    set_id = target["setId"]
    club_id = target.get("clubId")
    league_id = target.get("leagueId")
    is_gold = target.get("isGold", False)
    max_buy = target["maxBuy"]
    
    # 1. Navegar al set y obtener missing actual
    nav_js = f"""(async () => {{
        const c = getAppMain()._rootViewController.currentController;
        const sub0 = c ? (c.childViewControllers ? c.childViewControllers[0] : null) : null;
        const activeVC = sub0 ? sub0.currentController : null;
        let curVC = activeVC;
        if (!curVC || curVC.className !== '_0x42b2b1') {{
            const tile = document.querySelector('.ut-tile-gallery');
            if (tile) {{
                ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(t => {{
                    tile.dispatchEvent(new MouseEvent(t, {{ bubbles: true, cancelable: true, view: window }}));
                }});
                await new Promise(r => setTimeout(r, 800));
            }} else {{
                const homeBtn = document.querySelector('.icon-home');
                if (homeBtn) {{
                    ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(t => {{
                        homeBtn.dispatchEvent(new MouseEvent(t, {{ bubbles: true, cancelable: true, view: window }}));
                    }});
                    await new Promise(r => setTimeout(r, 800));
                    const tile2 = document.querySelector('.ut-tile-gallery');
                    if (tile2) {{
                        ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(t => {{
                            tile2.dispatchEvent(new MouseEvent(t, {{ bubbles: true, cancelable: true, view: window }}));
                        }});
                        await new Promise(r => setTimeout(r, 800));
                    }}
                }}
            }}
        }}
        const topC = getAppMain()._rootViewController.currentController;
        const subTop = topC ? (topC.childViewControllers ? topC.childViewControllers[0] : null) : null;
        const finalVC = subTop ? subTop.currentController : null;
        if (!finalVC || finalVC.className !== '_0x42b2b1') return {{ error: 'not_in_gallery' }};
        
        const cat = finalVC.view.category;
        const set = finalVC.view.set;
        
        if (Number(cat.getValue()) !== {cat_id}) {{
            cat.setIndexById({cat_id});
            cat._targets._collection.change[0].action.call(cat._targets._collection.change[0].target);
            await new Promise(r => setTimeout(r, 200));
        }}
        
        set.setIndexById({set_id});
        set._targets._collection.change[0].action.call(set._targets._collection.change[0].target);
        for (let i = 0; i < 25; i++) {{
            await new Promise(r => setTimeout(r, 80));
            if (finalVC.selectedSet && finalVC.selectedSet.id === {set_id} && finalVC.albumEntries && finalVC.albumEntries.length > 0) break;
        }}
        
        if (typeof finalVC._autoFill === 'function') finalVC._autoFill();
        
        const sInfo = finalVC.selectedSet;
        const entries = finalVC.albumEntries || [];
        const tracked = entries.filter(e => e.status === 'tracked');
        const missing = entries.filter(e => e.status === 'missing');
        
        return {{
            ok: true,
            req: sInfo ? sInfo.requiredItems : {target['req']},
            trackedCount: tracked.length,
            missingDefIds: missing.map(m => m.item ? m.item.definitionId : null).filter(Boolean)
        }};
    }})()"""
    nav_res = eval_js(nav_js, tab_id=tab_id, timeout=12)
    data = nav_res.get("result", {})
    if not data or not data.get("ok"):
        log(f"Aviso navegando a {name}: {data.get('error') or nav_res.get('error')}")
        return {"success": False}
        
    req = data.get("req", target["req"])
    tracked = data.get("trackedCount", 0)
    missing_ids = data.get("missingDefIds", [])
    faltan = req - tracked
    
    log(f"Set '{name}': {tracked}/{req} (Faltan {faltan} cartas).")
    if tracked >= req:
        log(f"¡HITO! Set '{name}' YA ESTÁ 100% COMPLETADO ({tracked}/{req})!")
        send_telegram_alert(f"🏆 *¡SET COMPLETADO AL 100%!* \nEquipo: *{name}*\nProgreso: *{tracked}/{req}*\n¡Recompensas e insignias reclamadas!")
        return {"success": True, "completed": True}
        
    # Ajuste dinámico de precio para cerrar sets: si falta 1 carta, pagar hasta 1,600; si faltan 2, pagar hasta 1,350
    if faltan == 1:
        max_buy = max(max_buy, 1600)
    elif faltan <= 2:
        max_buy = max(max_buy, 1350)
        
    time.sleep(random.uniform(1.2, 2.0))
    
    # 2. Búsqueda en mercado de candidatos faltantes
    club_ids = target.get("clubIds") or ([club_id] if club_id else [None])
    all_candidates = []
    total_found = 0
    
    eval_js("window._sniperCandidates = [];", tab_id=tab_id)
    
    def_ids = target.get("defIds")
    for c_id in club_ids:
        level_filter = "criteria.level = 'gold';" if is_gold else ""
        club_filter = f"criteria.club = {c_id};" if c_id else ""
        league_filter = f"criteria.league = {league_id};" if league_id else ""
        def_filter = f"criteria.defId = {json.dumps(def_ids)};" if def_ids else ""
        
        search_js = f"""(async () => {{
            services.Item.clearTransferMarketCache();
            const criteria = new UTSearchCriteriaDTO();
            criteria.type = 'player';
            {club_filter}
            {league_filter}
            {level_filter}
            {def_filter}
            criteria.maxBuy = {max_buy};
            criteria.count = 20;
            
            return await new Promise(resolve => {{
                services.Item.searchTransferMarket(criteria, 1).observe({{}}, (sender, resp) => {{
                    if (resp.success) {{
                        const missingSet = new Set({json.dumps(missing_ids)});
                        const candidates = (resp.data.items || []).filter(i => 
                            i._auction && i._auction.buyNowPrice > 0 && i._auction.buyNowPrice <= {max_buy} && missingSet.has(i.definitionId)
                        );
                        window._sniperCandidates = (window._sniperCandidates || []).concat(candidates);
                        resolve({{
                            success: true,
                            totalFound: resp.data.items.length,
                            candidates: candidates.map(c => ({{
                                id: c.id,
                                defId: c.definitionId,
                                name: (c._staticData && c._staticData.name) || c.definitionId,
                                rating: c.rating,
                                buyNow: c._auction.buyNowPrice
                            }}))
                        }});
                    }} else {{
                        resolve({{ success: false, status: resp.status }});
                    }}
                }});
            }});
        }})()"""
        s_res = eval_js(search_js, tab_id=tab_id, timeout=15)
        s_data = s_res.get("result", {})
        if s_data and s_data.get("success"):
            total_found += s_data.get("totalFound", 0)
            all_candidates.extend(s_data.get("candidates", []))
            if len(all_candidates) >= 5:
                break
        time.sleep(random.uniform(0.6, 1.0))
        
    candidates = all_candidates
    if not candidates:
        log(f"Sin cartas disponibles <= {max_buy} en mercado para {name} (Encontradas {total_found} no faltantes).")
        return {"success": True, "noStock": True}
        
    candidates.sort(key=lambda c: c["buyNow"])
    target_card = candidates[0]
    log(f"Comprando carta para {name}: {target_card['name']} (OVR {target_card['rating']}, DefId: {target_card['defId']}) por {target_card['buyNow']} monedas")
    
    time.sleep(random.uniform(1.2, 1.8))
    
    # 3. Comprar carta
    buy_js = f"""(async () => {{
        const item = (window._sniperCandidates || []).find(i => i.id === {target_card['id']});
        if (!item) return {{ success: false, error: 'item_missing_in_mem' }};
        return await new Promise(r => services.Item.bid(item, {target_card['buyNow']}).observe({{}}, (s, res) => r(res)));
    }})()"""
    b_res = eval_js(buy_js, tab_id=tab_id, timeout=12)
    b_data = b_res.get("result", {})
    if not b_data.get("success"):
        log(f"Fallo al ofertar por {target_card['name']}: {b_data.get('status') or b_data.get('error')}")
        return {"success": False}
        
    log(f"COMPRA CONFIRMADA: {target_card['name']} adquirida por {target_card['buyNow']} monedas.")
    log_tx("BUY", name, target_card["name"], target_card["defId"], target_card["buyNow"], 0, "BOUGHT")
    
    # 4. Estampar en Club y Relistar
    if any(p in target_card["name"].lower() for p in PROTECTED_PLAYERS):
        log(f"ESCUDO: {target_card['name']} protegida. Estampada en Club permanentemente.")
        eval_js(f"""(async () => {{
            const unassigned = Object.values(repositories.Item.unassigned._collection || {{}});
            const item = unassigned.find(i => i.definitionId === {target_card['defId']});
            if (item) await new Promise(r => services.Item.move(item, ItemPile.CLUB).observe({{}}, (s, res) => r(res)));
        }})()""", tab_id=tab_id)
        log_tx("STAMP_KEEP", name, target_card["name"], target_card["defId"], target_card["buyNow"], 0, "KEPT_IN_CLUB")
        return {"success": True, "bought": True}
        
    time.sleep(random.uniform(1.0, 1.6))
    
    start_bid = 650 if (target_card.get("rating") or 0) >= 75 else 200
    list_price = 700 if target_card["buyNow"] <= 700 else min(target_card["buyNow"], 750)
    if (target_card.get("rating") or 0) < 75:
        list_price = 250 if target_card["buyNow"] <= 250 else 300
        
    flip_js = f"""(async () => {{
        const unassigned = Object.values(repositories.Item.unassigned._collection || {{}});
        const item = unassigned.find(i => i.definitionId === {target_card['defId']});
        if (!item) return {{ success: false, error: 'not_unassigned' }};
        
        if (item.untradeable) {{
            await new Promise(r => services.Item.move(item, ItemPile.CLUB).observe({{}}, (s, res) => r(res)));
            return {{ toClub: true, listed: false }};
        }}
        
        // 1. Estampar en Club
        await new Promise(r => services.Item.move(item, ItemPile.CLUB).observe({{}}, (s, res) => r(res)));
        // 2. Mover a Transfer list
        await new Promise(r => services.Item.move(item, ItemPile.TRANSFER).observe({{}}, (s, res) => r(res)));
        // 3. Listar en mercado
        await new Promise(r => setTimeout(r, 450));
        const minBid = (item._itemPriceLimits && item._itemPriceLimits.minimum) || {start_bid};
        const buyNow = Math.max(minBid + 50, {list_price});
        const listRes = await new Promise(r => services.Item.list(item, minBid, buyNow, 3600).observe({{}}, (s, res) => r(res)));
        return {{ toClub: true, listed: listRes ? listRes.success : false, minBid: minBid, buyNow: buyNow }};
    }})()"""
    f_res = eval_js(flip_js, tab_id=tab_id, timeout=15)
    f_data = f_res.get("result", {})
    if f_data.get("toClub"):
        log(f"ESTAMPADO EN GALERÍA: {target_card['name']} sumada exitosamente.")
        log_tx("STAMP", name, target_card["name"], target_card["defId"], target_card["buyNow"], 0, "TRACKED")
    if f_data.get("listed"):
        log(f"AUTO-LIQUIDADOR: {target_card['name']} en venta por {start_bid}/{list_price} monedas.")
        log_tx("LIST", name, target_card["name"], target_card["defId"], target_card["buyNow"], list_price, "LISTED")
        
    new_tr = tracked + 1
    if new_tr >= req:
        log(f"¡HITO! Set '{name}' ALCANZÓ 100% ({new_tr}/{req})!")
        send_telegram_alert(f"🏆 *¡SET COMPLETADO AL 100%!* \nEquipo: *{name}*\nProgreso: *{new_tr}/{req}*\n¡Recompensas e insignias reclamadas!")
        
    return {"success": True, "bought": True}

def run_loop(tab_id=DEFAULT_TAB, max_rounds=25):
    log("==================================================================")
    log("FRANCOTIRADOR DE ALTA PRECISIÓN - GALERÍA FC 27 INICIADO")
    log("Objetivos VIP: ROSHN Saudi (27/30), Barcelona (15/20), Man United (15/20)")
    log(f"Piso Protegido: {MIN_BUFFER:,} monedas | Rondas máximas: {max_rounds}")
    log("==================================================================")
    
    cooldowns = {}
    rounds_done = 0
    queries_count = 0
    
    while rounds_done < max_rounds:
        coins = get_coins(tab_id) or 0
        m_info = recover_market_and_rewards(tab_id) or {}
        active_cnt = m_info.get("activeListings", 0) if isinstance(m_info, dict) else 0
        
        log(f"\n--- CICLO #{rounds_done+1} | Saldo: {coins:,} monedas (Piso: {MIN_BUFFER:,}) | Subastas Activas: {active_cnt} ---")
        if coins < MIN_BUFFER:
            log(f"Saldo ({coins:,}) por debajo del piso ({MIN_BUFFER:,}). Esperando ventas en mercado...")
            time.sleep(40)
            continue
            
        action_in_round = False
        for target in TARGET_SETS:
            if rounds_done >= max_rounds:
                break
                
            t_name = target["name"]
            now = time.time()
            if t_name in cooldowns and now < cooldowns[t_name]:
                continue
                
            res = process_target_set(tab_id, target)
            queries_count += 1
            
            if res.get("noStock"):
                cooldowns[t_name] = time.time() + 120 # 120s cooldown para reintentar mercado
                time.sleep(2.0)
            elif res.get("bought"):
                rounds_done += 1
                action_in_round = True
                recover_market_and_rewards(tab_id)
                time.sleep(random.uniform(5.5, 8.5))
                if rounds_done % 3 == 0:
                    log("Pausa de rotación y enfriamiento (35 segundos)...")
                    recover_market_and_rewards(tab_id)
                    time.sleep(35)
            elif res.get("completed"):
                cooldowns[t_name] = time.time() + 999999 # no volver a buscarlo
            else:
                time.sleep(3.0)
                
            if queries_count % 4 == 0:
                log("Enfriamiento preventivo anti-ban (30 segundos)...")
                time.sleep(30)
                
        if not action_in_round:
            log("Ronda completa de candidatos procesada. Pausando 30s...")
            time.sleep(30)
            
    recover_market_and_rewards(tab_id)
    final_c = get_coins(tab_id) or 0
    log(f"Francotirador finalizado. Saldo final: {final_c:,} monedas.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tab-id", type=int, default=DEFAULT_TAB)
    parser.add_argument("--rounds", type=int, default=25)
    args = parser.parse_args()
    run_loop(tab_id=args.tab_id, max_rounds=args.rounds)
