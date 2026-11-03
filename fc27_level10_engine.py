#!/usr/bin/env python3
"""
fc27_level10_engine.py — Motor de Alta Eficiencia para Galería Nivel 10 (EA Sports FC 27).

Estrategia & Mejoras:
1. Multi-Tier Buy, Stamp & Flip:
   - Rango base: 84-87 OVR (1,200 a 9,500 monedas).
   - Rango preferido: 88 OVR (10,000 a 17,000 monedas) — suma 8,300 pts base (Club+Liga = ~16,600 pts).
   - Excepciones de alto puntaje: 89-90 OVR (20,000 a 36,000 monedas, ej. Harry Kane) — suma 14,000 pts base (Club+Liga = ~28,000 pts).
2. Regla Dinámica de Liquidez (Coin Shield):
   - Para comprar una carta de precio P, se exige: current_coins >= P + MIN_BUFFER_COINS (15,000 monedas).
   - Nunca agota el capital; siempre preserva el colchón de seguridad.
3. Reventa Inmediata al Mínimo de Mercado:
   - Compra la carta más barata disponible y la lista al mismo precio (o 100 monedas menos) para venta rápida.
   - Pérdida neta de solo 5% (EA tax = 70 a 1,600 monedas por flip).
4. Detector Proactivo de Softban (Paletools & EA):
   - Escaneo de DOM (toasts, alertas, banners de Paletools o EA con 'softban', 'too many requests', 'rate limit').
   - Detección en códigos HTTP (429, 403, 461, 512, 521).
   - Si se detecta: PAUSA TOTAL automática de enfriamiento en bloques de 15 minutos con comprobación pasiva.
5. Ciclo de Ventas cada 5 Minutos:
   - Si las monedas bajan de 15,000 o no hay liquidez para la siguiente carta, espera 5 minutos llamando
     a clearSoldItems() y relistExpiredAuctions() hasta recuperar fondos.
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
RESUME_BUFFER_COINS = 20000

# 23 Active Squad Players — INMUTABLES
ACTIVE_SQUAD_SHIELD = [
    "donnarumma", "hakimi", "saliba", "van dijk", "capdevila", "touré", "toure",
    "bellingham", "aimar", "lamine yamal", "yamal", "ferran torres", "diomande",
    "rodman", "anyomi", "barcola", "anderson", "camavinga", "lacroix", "nkunku",
    "matheus nunes", "doku", "spence", "barella", "delgado"
]

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
            return json.loads(resp.read().decode("utf-8"))
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

def check_softban(tab_id=DEFAULT_TAB):
    """Verifica proactivamente si Paletools o EA muestran un aviso de softban en pantalla."""
    js = """
    (() => {
        const textElements = Array.from(document.querySelectorAll(
            ".ut-toast-view, .notification, .Dialog, .ea-dialog-view, .banner, .paletools-alert, .ut-notification"
        ));
        for (const el of textElements) {
            const text = (el.innerText || "").toLowerCase();
            if (
                text.includes("softban") ||
                text.includes("demasiadas") ||
                text.includes("too many requests") ||
                text.includes("rate limit") ||
                text.includes("bloqueo temporal") ||
                text.includes("error with your bid")
            ) {
                return { isSoftban: true, message: el.innerText.trim() };
            }
        }
        return { isSoftban: false };
    })()
    """
    res = eval_js(js, tab_id=tab_id, timeout=10)
    if res.get("ok") and isinstance(res.get("result"), dict):
        return res["result"]
    return {"isSoftban": False}

def enter_softban_cooloff(reason="Rate limit detectado", tab_id=DEFAULT_TAB):
    """Pausa completa en bloques de 15 minutos hasta que el softban se levante."""
    log("=" * 65)
    log(f"🚨 SOFTBAN DETECTADO ({reason}).")
    log("🛑 PAUSA TOTAL DE ENFRIAMIENTO INICIADA.")
    log("⏳ El bot esperará 15 minutos antes de hacer una comprobación pasiva.")
    log("=" * 65)

    while True:
        time.sleep(900)  # 15 minutos
        log("🔍 Comprobando pasivamente si el softban continúa...")

        # 1. Check DOM
        sb = check_softban(tab_id=tab_id)
        if sb.get("isSoftban"):
            log(f"⚠️ Softban sigue activo en pantalla ({sb.get('message')}). Esperando otros 15 minutos...")
            continue

        # 2. Test lightweight search probe (1 search only)
        probe_js = """
        (async () => {
            return new Promise(resolve => {
                services.Item.clearTransferMarketCache();
                const c = new UTSearchCriteriaDTO();
                c.type = "player";
                c.defId = [193698]; // Baumann test
                c.maskedDefId = 193698;
                c.count = 5;
                services.Item.searchTransferMarket(c, 1).observe(null, (s, r) => {
                    if (r && r.success) resolve({ ok: true });
                    else resolve({ ok: false, status: r ? r.status : null });
                });
            });
        })()
        """
        probe_res = eval_js(probe_js, tab_id=tab_id, timeout=15)
        if probe_res.get("ok"):
            probe_data = probe_res.get("result", {})
            if isinstance(probe_data, dict) and probe_data.get("ok"):
                log("=" * 65)
                log("✅ ¡EL MERCADO RESPONDE NORMALMENTE! SOFTBAN LEVANTADO.")
                log("🟢 Reanudando operaciones con ritmo seguro.")
                log("=" * 65)
                time.sleep(10)
                break
            else:
                log(f"⚠️ Probe de mercado falló (status={probe_data.get('status')}). Continuando enfriamiento por 15 min...")
        else:
            log("⚠️ Error en probe. Continuando enfriamiento por 15 min...")

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
    max_buy = candidate.get("maxBuy", 2000)
    name = candidate["name"]
    rating = candidate["rating"]
    points_est = candidate.get("pointsEst", 2000)

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
        return json.loads(res.get("result", "{}"))
    except Exception as e:
        return {"ok": False, "error": str(e)}

def load_candidate_pool():
    if not CANDIDATES_FILE.exists():
        log("No se encontró real_gold_candidates.json.")
        return []
    with open(CANDIDATES_FILE, "r", encoding="utf-8") as f:
        pool = json.load(f)
    return pool

def pick_best_candidate(candidates, tracked_ids, current_coins):
    """
    Selecciona el mejor candidato disponible respetando la regla dinámica de liquidez:
    - current_coins >= candidate.maxBuy + MIN_BUFFER_COINS
    - Prioriza según balance entre rating alto (10-20k, y excepciones de 30-35k) y liquidez.
    """
    available_to_spend = max(0, current_coins - MIN_BUFFER_COINS)
    untracked = [c for c in candidates if c["defId"] not in tracked_ids]
    
    # Filter candidates we can afford while keeping MIN_BUFFER_COINS safe
    affordable = [c for c in untracked if c.get("maxBuy", 2000) <= available_to_spend]
    
    if not affordable:
        return None

    # Sorting strategy:
    # If coins > 60k: Prioritize high-impact cards (90 OVR like Kane, 88-89 OVR 10k-20k)
    # If coins 30k-60k: Prioritize 87-88 OVR (7k-17k)
    # If coins < 30k: Prioritize 84-86 OVR (1.2k-6k)
    if current_coins >= 60000:
        affordable.sort(key=lambda x: (x["rating"], x["baseScore"]), reverse=True)
    elif current_coins >= 35000:
        # Prefer 88 and 87 (10-18k range)
        affordable.sort(key=lambda x: (x["rating"] in (88, 87), x["rating"]), reverse=True)
    else:
        # Fast turnover 84-86
        affordable.sort(key=lambda x: (x["rating"] in (85, 86, 84), x["rating"]), reverse=True)
    
    return affordable[0]

def run_engine(target_points=450000):
    log("=" * 65)
    log("🚀 INICIANDO FC 27 GALLERY LEVEL 10 ENGINE (v2 Multi-Tier & Softban Shield)")
    log(f"🎯 Meta: +{target_points:,} Puntos de Galería")
    log(f"🛡️ Buffer Mínimo de Monedas: {MIN_BUFFER_COINS:,}")
    log("=" * 65)

    all_candidates = load_candidate_pool()
    log(f"Candidatos Oro (84-90 OVR) cargados: {len(all_candidates)}")

    accumulated_points = 0
    consecutive_buys = 0

    while accumulated_points < target_points:
        # Proactive Softban Check in DOM before anything
        sb = check_softban()
        if sb.get("isSoftban"):
            enter_softban_cooloff(reason=sb.get("message", "Aviso en DOM"))

        current_coins = get_coins()
        log(f"💰 Monedas actuales: {current_coins:,}")

        # Check if we can afford any purchase while preserving the buffer
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

        # Pick best candidate matching current wallet capacity
        cand = pick_best_candidate(all_candidates, tracked_ids, current_coins)
        if not cand:
            log(f"⏳ No hay candidatos asequibles con el saldo disponible actual ({current_coins:,}).")
            log("⏳ Esperando 5 minutos a que se libere liquidez de las ventas activas...")
            time.sleep(300)
            clear_sold_and_relist()
            continue

        did = cand["defId"]
        name = cand["name"]
        rating = cand["rating"]
        max_buy = cand["maxBuy"]
        pts = cand["pointsEst"]

        log(f"🔎 Buscando: {name} ({rating} OVR, defId={did}) | MaxBuy: {max_buy:,} monedas | Pts Est: ~{pts:,}")
        res = buy_stamp_and_flip(cand)

        # Check for softban in response status
        status = res.get("status")
        if status in (429, 403, 461, 512, 521):
            enter_softban_cooloff(reason=f"HTTP status {status} en mercado")
            continue

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
