#!/usr/bin/env python3
"""
fc27_level10_engine.py — Motor de Alta Eficiencia para Galería Nivel 10 (EA Sports FC 27).

Estrategia:
1. High-ROI Gold Flipper (84-88 OVR):
   - Compra cartas de oro accesibles al menor Buy Now del mercado.
   - Las estampa en el Club (sumando 830 a 8,300 pts base + multiplicadores en Club y Liga).
   - Las mueve a transferencias y las lista de inmediato al precio mínimo de mercado para rápida reventa.
   - Pérdida neta despreciable (solo 5% de impuesto EA = 70 a 450 monedas por flip).
2. Anti-Softban Estricto:
   - Lectura de tracked status directamente desde IndexedDB de Paletools (0 llamadas de red).
   - Búsqueda quirúrgica por defId + maskedDefId con clearTransferMarketCache().
   - Jitter de 8 a 13s entre compras, cooldown de 45s cada 3 operaciones.
   - Si las monedas bajan del buffer (15,000 monedas) o la lista de transferencias está llena,
     espera 5 minutos (300s) monitoreando cobros automáticos (clearSoldItems) y relistados.
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
TRANSACTIONS_CSV = DATA_DIR / "level10_transactions.csv"
ENGINE_LOG = DATA_DIR / "level10_engine.log"
CANDIDATES_FILE = DATA_DIR / "real_gold_candidates.json"

ENDPOINT = "http://127.0.0.1:9224/command"
DEFAULT_TAB = 1233340559
MIN_BUFFER_COINS = 15000
RESUME_BUFFER_COINS = 22000

# 23 Active Squad Players — INMUTABLES
ACTIVE_SQUAD_SHIELD = [
    "donnarumma", "hakimi", "saliba", "van dijk", "capdevila", "touré", "toure",
    "bellingham", "aimar", "lamine yamal", "yamal", "ferran torres", "diomande",
    "rodman", "anyomi", "barcola", "anderson", "camavinga", "lacroix", "nkunku",
    "matheus nunes", "doku", "spence", "barella", "delgado"
]

MAX_BUY_BY_OVR = {
    84: 1700,
    85: 4000,
    86: 5800,
    87: 9200,
    88: 14000,
}

BASE_SCORES = {
    84: 830,
    85: 2100,
    86: 4100,
    87: 5500,
    88: 8300,
}

def log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now}] {msg}"
    print(line, flush=True)
    with open(ENGINE_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def log_tx(def_id, name, rating, buy_price, list_price, status, points_est):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_exists = TRANSACTIONS_CSV.exists()
    with open(TRANSACTIONS_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(["timestamp", "def_id", "name", "rating", "buy_price", "list_price", "status", "points_est"])
        w.writerow([now, def_id, name, rating, buy_price, list_price, status, points_est])

def eval_js(js_code, tab_id=DEFAULT_TAB, timeout=30):
    payload = {
        "action": "eval",
        "tabId": int(tab_id),
        "js": js_code
    }
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data
    except Exception as e:
        log(f"Error evaluando JS en bridge: {e}")
        return {"ok": False, "error": str(e)}

def get_coins(tab_id=DEFAULT_TAB):
    res = eval_js("services.User.getUser().coins.amount", tab_id=tab_id, timeout=10)
    if res.get("ok"):
        try:
            return int(res.get("result", 0))
        except (TypeError, ValueError):
            return 0
    return 0

def get_live_tracked_ids(tab_id=DEFAULT_TAB):
    js = """
    (async () => {
        return new Promise((resolve) => {
            const req = indexedDB.open("paletools");
            req.onsuccess = (e) => {
                const db = e.target.result;
                const tx = db.transaction("trackedPlayers", "readonly");
                const store = tx.objectStore("trackedPlayers");
                const getAll = store.getAll();
                getAll.onsuccess = () => resolve(getAll.result.map(x => x.definitionId));
                getAll.onerror = () => resolve([]);
            };
            req.onerror = () => resolve([]);
        });
    })()
    """
    res = eval_js(js, tab_id=tab_id, timeout=10)
    if res.get("ok") and isinstance(res.get("result"), list):
        return set(res["result"])
    return set()

def clear_sold_and_relist(tab_id=DEFAULT_TAB):
    js = """
    (async () => {
        let cleared = 0;
        let relisted = 0;
        
        await new Promise(r => {
            services.Item.clearSoldItems().observe(null, (s, res) => {
                cleared = res && res.success ? 1 : 0;
                r();
            });
        });
        
        await new Promise(r => {
            services.Item.relistExpiredAuctions().observe(null, (s, res) => {
                relisted = res && res.success ? 1 : 0;
                r();
            });
        });

        const transfer = (repositories.Item && repositories.Item.getTransferItems && repositories.Item.getTransferItems()) || [];
        const coins = services.User.getUser().coins.amount;

        return JSON.stringify({
            cleared,
            relisted,
            transferCount: transfer.length,
            activeCount: transfer.filter(i => i._auction && i._auction.tradeState === "active").length,
            coins
        });
    })()
    """
    res = eval_js(js, tab_id=tab_id, timeout=15)
    if res.get("ok"):
        try:
            return json.loads(res.get("result", "{}"))
        except Exception:
            pass
    return {}

def buy_stamp_and_flip(candidate, tab_id=DEFAULT_TAB):
    def_id = candidate["defId"]
    max_buy = candidate.get("maxBuy", MAX_BUY_BY_OVR.get(candidate.get("rating", 84), 1800))
    name = candidate["name"]
    rating = candidate["rating"]
    points_est = candidate.get("pointsEst", BASE_SCORES.get(rating, 830) * 2)

    if any(p in name.lower() for p in ACTIVE_SQUAD_SHIELD):
        log(f"🛡️ ESCUDO ACTIVO: {name} está en el 11 titular. Omitido.")
        return {"ok": False, "reason": "squad_shield"}

    js = f"""
    (async () => {{
        const wait = (ms) => new Promise(r => setTimeout(r, ms));
        
        // Clear market cache for clean defId lookup
        services.Item.clearTransferMarketCache();

        // 1. Search specific player on market
        const searchRes = await new Promise(resolve => {{
            const c = new UTSearchCriteriaDTO();
            c.type = "player";
            c.defId = [{def_id}];
            c.maskedDefId = {def_id};
            c.count = 20;
            services.Item.searchTransferMarket(c, 1).observe(null, (s, r) => {{
                if (!r || !r.success) return resolve({{ ok: false, error: "search_failed", status: r ? r.status : null }});
                const rawItems = (r.data && r.data.items) || [];
                const validItems = rawItems.filter(i => i && i.id > 0 && i._auction && i._auction.buyNowPrice > 0);
                if (!validItems.length) return resolve({{ ok: false, error: "no_items" }});
                validItems.sort((a, b) => a._auction.buyNowPrice - b._auction.buyNowPrice);
                resolve({{ ok: true, item: validItems[0], lowestBN: validItems[0]._auction.buyNowPrice }});
            }});
        }});

        if (!searchRes.ok) return JSON.stringify(searchRes);
        const item = searchRes.item;
        const buyPrice = searchRes.lowestBN;

        if (buyPrice > {max_buy}) {{
            return JSON.stringify({{ ok: false, error: "price_exceeds_budget", buyPrice, maxBuy: {max_buy} }});
        }}

        // 2. Buy Now
        const buyRes = await new Promise(resolve => {{
            services.Item.bid(item, buyPrice).observe(null, (s, r) => {{
                if (r.success) resolve({{ ok: true, item }});
                else resolve({{ ok: false, error: "bid_failed", status: r.status }});
            }});
        }});

        if (!buyRes.ok) return JSON.stringify(buyRes);
        await wait(1200);

        // 3. Move to CLUB (Stamps tracked definitionId)
        const clubRes = await new Promise(resolve => {{
            services.Item.move(item, ItemPile.CLUB).observe(null, (s, r) => {{
                resolve({{ ok: r.success, status: r.status }});
            }});
        }});
        await wait(1400);

        // 4. Move to TRANSFER list
        const transferRes = await new Promise(resolve => {{
            services.Item.move(item, ItemPile.TRANSFER).observe(null, (s, r) => {{
                resolve({{ ok: r.success, status: r.status }});
            }});
        }});
        await wait(1200);

        // 5. Populate price limits if needed
        if (!item._itemPriceLimits) {{
            await new Promise(r => services.Item.requestMarketData(item).observe(null, (s, res) => r(res)));
        }}

        const minLimit = (item._itemPriceLimits && item._itemPriceLimits.minimum) ? item._itemPriceLimits.minimum : 300;
        const listPrice = buyPrice;
        const minBid = Math.max(minLimit, listPrice - 100);

        // 6. List on market
        const listRes = await new Promise(resolve => {{
            services.Item.list(item, minBid, listPrice, 3600).observe(null, (s, r) => {{
                resolve({{ ok: r.success, status: r.status }});
            }});
        }});

        const remainingCoins = services.User.getUser().coins.amount;

        return JSON.stringify({{
            ok: true,
            buyPrice,
            listPrice,
            minBid,
            clubStamping: clubRes.ok,
            transferMoving: transferRes.ok,
            marketListing: listRes.ok,
            remainingCoins
        }});
    }})()
    """
    res = eval_js(js, tab_id=tab_id, timeout=35)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error")}
    try:
        data = json.loads(res.get("result", "{}"))
        return data
    except Exception as e:
        return {"ok": False, "error": str(e)}

def load_candidate_pool():
    if not CANDIDATES_FILE.exists():
        log("No se encontró real_gold_candidates.json.")
        return []
    with open(CANDIDATES_FILE, "r", encoding="utf-8") as f:
        pool = json.load(f)
    # Re-order: sort by rating ascending (84, 85 first for fastest turnover, then 86, 87, 88)
    pool.sort(key=lambda x: (x["rating"], x["baseScore"]))
    return pool

def run_engine(target_points=450000):
    log("=" * 65)
    log("🚀 INICIANDO FC 27 GALLERY LEVEL 10 ENGINE")
    log(f"🎯 Meta: +{target_points:,} Puntos de Galería")
    log(f"🛡️ Buffer Mínimo de Monedas: {MIN_BUFFER_COINS:,}")
    log("=" * 65)

    all_candidates = load_candidate_pool()
    log(f"Candidatos Oro cargados: {len(all_candidates)}")

    accumulated_points = 0
    consecutive_buys = 0

    while accumulated_points < target_points:
        current_coins = get_coins()
        log(f"💰 Monedas actuales: {current_coins:,}")

        # Check coin buffer
        if current_coins < MIN_BUFFER_COINS:
            log(f"⚠️ Saldo por debajo del buffer ({current_coins:,} < {MIN_BUFFER_COINS:,}).")
            log("⏳ Esperando 5 minutos a que se vendan items en el mercado...")
            while current_coins < RESUME_BUFFER_COINS:
                stats = clear_sold_and_relist()
                current_coins = stats.get("coins", get_coins())
                log(f"🔄 Check 5 min: Monedas={current_coins:,} | TransferList={stats.get('transferCount', '?')}")
                if current_coins >= RESUME_BUFFER_COINS:
                    log("✅ Monedas recuperadas. Reanudando compras.")
                    break
                time.sleep(300)

        # Refresh live tracked IDs from Paletools IndexedDB (0 network calls)
        tracked_ids = get_live_tracked_ids()
        log(f"Total jugadores trackeados en Galería: {len(tracked_ids)}")

        # Filter pending untracked candidates
        pending = [c for c in all_candidates if c["defId"] not in tracked_ids]
        if not pending:
            log("🎉 ¡Todos los candidatos de oro cargados ya han sido trackeados!")
            break

        # Pick next candidate
        cand = pending[0]
        did = cand["defId"]
        name = cand["name"]
        rating = cand["rating"]
        max_buy = cand["maxBuy"]
        pts = cand["pointsEst"]

        log(f"🔎 Buscando: {name} ({rating} OVR, defId={did}) | MaxBuy: {max_buy:,} monedas | Pts Est: ~{pts:,}")
        res = buy_stamp_and_flip(cand)

        if res.get("ok"):
            buy_p = res.get("buyPrice", 0)
            list_p = res.get("listPrice", 0)
            rem_coins = res.get("remainingCoins", 0)
            accumulated_points += pts
            consecutive_buys += 1
            log_tx(did, name, rating, buy_p, list_p, "SUCCESS", pts)
            log(f"✅ ÉXITO: {name} comprado a {buy_p:,}, estampado en Club y listado a {list_p:,}!")
            log(f"📈 Puntos acumulados en esta sesión: +{accumulated_points:,} / {target_points:,} pts")
            log(f"💳 Saldo restante: {rem_coins:,} monedas")

            # Cooldown every 3 buys to protect against softban
            if consecutive_buys >= 3:
                cooldown = random.randint(45, 65)
                log(f"☕ Cooldown anti-softban (3 compras consecutivas): descansando {cooldown}s...")
                consecutive_buys = 0
                time.sleep(cooldown)
            else:
                jitter = random.randint(8, 14)
                log(f"⏳ Jitter humano: esperando {jitter}s...")
                time.sleep(jitter)
        else:
            err = res.get("error", "unknown")
            log(f"⏭️ No comprado ({err}). Pasando al siguiente candidato...")
            status = res.get("status")
            if status in (429, 403):
                log("🚨 Advertencia de rate limit del mercado. Pausa de seguridad de 3 minutos...")
                time.sleep(180)
            else:
                all_candidates.remove(cand)
                all_candidates.append(cand)
                time.sleep(random.randint(4, 7))

    log("=" * 65)
    log(f"🏁 MOTOR FINALIZADO: +{accumulated_points:,} Puntos conseguidos!")
    log("=" * 65)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FC 27 Gallery Level 10 Engine")
    parser.add_argument("--points", type=int, default=450000, help="Puntos objetivo a sumar")
    args = parser.parse_args()
    run_engine(target_points=args.points)
