#!/usr/bin/env python3
"""
fc27_liquidate_and_snipe.py — Liquidación de cartas de Galería y francotirador continuo.

Reglas estrictas de Emilio:
1. NUNCA tocar los 23 jugadores de la plantilla activa (once titular, suplentes y reservas).
2. NUNCA tocar jugadores protegidos (Yamal, Messi, Mbappe, Vinicius, Bellingham, etc.).
3. Mover a Transfer y vender todas las cartas que el bot compró para la galería y quedaron en el Club.
4. Continuar francotirador en los sets más cercanos (Bayern 17/20, Tottenham 12/15, Liga F 24/30).
"""

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
DEFAULT_TAB = CONFIG.get("bridge", {}).get("defaultTabId", 1233340208)

# Escudo de plantilla activa y protegidos
PROTECTED_NAMES = [
    "donnarumma", "hakimi", "saliba", "van dijk", "capdevila", "touré", "toure",
    "bellingham", "aimar", "lamine yamal", "yamal", "ferran torres", "diomande",
    "rodman", "anyomi", "barcola", "anderson", "camavinga", "lacroix", "nkunku",
    "matheus nunes", "doku", "spence", "barella", "delgado",
    "messi", "mbappe", "vinicius", "rodri", "haaland", "valverde"
]

TARGET_SETS = [
    {"name": "FC Bayern München", "catId": 4, "setId": 52, "clubIds": [21, 115995], "leagueId": None, "req": 20, "maxBuy": 1100},
    {"name": "Tottenham Hotspur", "catId": 5, "setId": 8, "clubIds": [18, 116011], "leagueId": None, "req": 15, "maxBuy": 1000},
    {"name": "Liga F Moeve", "catId": 6, "setId": 121, "clubId": None, "leagueId": 2222, "isGold": False, "req": 30, "maxBuy": 850},
    {"name": "Newcastle United", "catId": 5, "setId": 92, "clubIds": [13], "leagueId": None, "req": 15, "maxBuy": 850},
    {"name": "Al Hilal", "catId": 8, "setId": 190, "clubIds": [605], "leagueId": None, "req": 15, "maxBuy": 850},
]

def log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now}] {msg}"
    print(line, flush=True)
    with open(SNIPER_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def eval_js(js_code, tab_id=DEFAULT_TAB, timeout=25):
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

def get_bot_bought_def_ids():
    def_ids = set()
    if TRANSACTIONS_CSV.exists():
        with open(TRANSACTIONS_CSV, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("action") == "BUY" and row.get("defId"):
                    try:
                        def_ids.add(int(row["defId"]))
                    except ValueError:
                        pass
    return def_ids

def liquidate_club_fodder(tab_id=DEFAULT_TAB):
    log("==================================================================")
    log("INICIANDO LIQUIDACIÓN DE CARTAS DE GALERÍA EN CLUB")
    log("Protección Activa: Once titular, suplentes y jugadores protegidos")
    log("==================================================================")
    
    bot_ids = list(get_bot_bought_def_ids())
    log(f"Cartas históricas compradas por el bot: {len(bot_ids)} definición(es).")
    
    # 1. Cobrar cualquier venta previa y relistar expiradas
    recover_js = """(async () => {
        let sold = 0;
        try {
            if (services.Item && typeof services.Item.clearSoldItems === 'function') {
                sold = await new Promise(r => services.Item.clearSoldItems().observe({}, (s, res) => r(res && res.success ? 1 : 0)));
            }
        } catch(e) {}
        
        let relisted = 0;
        try {
            const transfer = repositories.Item.transfer;
            const list = transfer ? Object.values(transfer._collection || {}) : [];
            const expired = list.filter(i => !i._auction || i._auction.tradeState === 'expired' || i._auction.tradeState === 'inactive');
            for (const item of expired) {
                const minBid = (item._itemPriceLimits && item._itemPriceLimits.minimum) || ((item.rating || 0) >= 75 ? 650 : 250);
                const buyNow = ((item.rating || 0) >= 75) ? Math.max(minBid + 50, 750) : (minBid + 100);
                try {
                    const r = await new Promise(res => services.Item.list(item, minBid, buyNow, 3600).observe({}, (s, res2) => res(res2)));
                    if (r && r.success) relisted++;
                } catch(e) {}
                await new Promise(r => setTimeout(r, 400));
            }
        } catch(e) {}
        return { sold: sold, relisted: relisted };
    })()"""
    eval_js(recover_js, tab_id=tab_id)
    time.sleep(1.0)
    
    # 2. Buscar en el club las cartas compradas por el bot
    search_js = f"""(async () => {{
        const botIds = new Set({json.dumps(bot_ids)});
        const criteria = new UTSearchCriteriaDTO();
        criteria.type = 'player';
        criteria.count = 250;
        
        return await new Promise(resolve => {{
            services.Club.search(criteria).observe({{}}, (s, res) => {{
                if (!res.success || !res.response || !res.response.items) return resolve({{ items: [] }});
                const matches = res.response.items.filter(i => {{
                    if (i.untradeable || i.tradable === false) return false;
                    return botIds.has(i.definitionId);
                }});
                resolve({{
                    items: matches.map(m => ({{
                        id: m.id,
                        defId: m.definitionId,
                        name: m._staticData ? m._staticData.name : null,
                        rating: m.rating
                    }}))
                }});
            }});
        }});
    }})()"""
    
    res = eval_js(search_js, tab_id=tab_id)
    items_to_sell = res.get("result", {}).get("items", [])
    
    log(f"Encontradas {len(items_to_sell)} cartas de galería transferibles en el Club.")
    
    sold_count = 0
    for item_data in items_to_sell:
        p_name = (item_data.get("name") or "").lower()
        if any(prot in p_name for prot in PROTECTED_NAMES):
            log(f"ESCUDO: {item_data.get('name')} está en la lista de protegidos. Se conserva en club.")
            continue
            
        move_and_list_js = f"""(async () => {{
            const criteria = new UTSearchCriteriaDTO();
            criteria.type = 'player';
            criteria.count = 250;
            
            return await new Promise(resolve => {{
                services.Club.search(criteria).observe({{}}, async (s, res) => {{
                    const item = (res.response && res.response.items ? res.response.items : []).find(i => i.id === {item_data['id']});
                    if (!item) return resolve({{ error: 'not_in_club' }});
                    
                    // Mover a transfer
                    const moveRes = await new Promise(r => services.Item.move(item, ItemPile.TRANSFER).observe({{}}, (s2, res2) => r(res2)));
                    if (!moveRes || !moveRes.success) return resolve({{ error: 'move_failed', status: moveRes ? moveRes.status : null }});
                    
                    await new Promise(r => setTimeout(r, 450));
                    
                    const minBid = (item._itemPriceLimits && item._itemPriceLimits.minimum) || ((item.rating || 0) >= 75 ? 650 : 250);
                    const buyNow = ((item.rating || 0) >= 75) ? Math.max(minBid + 50, 750) : (minBid + 100);
                    
                    const listRes = await new Promise(r => services.Item.list(item, minBid, buyNow, 3600).observe({{}}, (s3, res3) => r(res3)));
                    resolve({{
                        success: true,
                        moved: moveRes.success,
                        listed: listRes ? listRes.success : false,
                        minBid: minBid,
                        buyNow: buyNow
                    }});
                }});
            }});
        }})()"""
        
        m_res = eval_js(move_and_list_js, tab_id=tab_id)
        r_data = m_res.get("result", {})
        if r_data.get("listed"):
            sold_count += 1
            log(f"[{sold_count}/{len(items_to_sell)}] LISTADO: {item_data.get('name')} ({item_data.get('rating')}) por {r_data.get('minBid')}/{r_data.get('buyNow')} monedas.")
        elif r_data.get("moved"):
            log(f"MOVIDO A TRANSFER: {item_data.get('name')} ({item_data.get('rating')}) quedó en lista de transferibles.")
        else:
            log(f"Aviso al listar {item_data.get('name')}: {r_data.get('error') or m_res.get('error')}")
            
        time.sleep(random.uniform(1.2, 1.8))
        
    log(f"Liquidación finalizada: {sold_count} cartas puestas a la venta en el mercado.")
    return sold_count

if __name__ == "__main__":
    liquidate_club_fodder()
