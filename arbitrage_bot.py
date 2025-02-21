import os
import asyncio
import requests
import logging
from solana.rpc.async_api import AsyncClient
from solana.publickey import PublicKey
from solana.keypair import Keypair
from solders.keypair import Keypair as SoldersKeypair
from solders.transaction import VersionedTransaction
from dotenv import load_dotenv
import base64
from flask import Flask, request, jsonify, render_template
from threading import Thread
from datetime import datetime

load_dotenv()
RPC_ENDPOINT = "https://api.mainnet-beta.solana.com"
WALLET_PRIVATE_KEY = os.getenv("SOLANA_PRIVATE_KEY")
JUPITER_API = "https://quote-api.jup.ag/v6/quote"
GMGN_API = "https://gmgn.ai/defi/router/v1/sol"

logging.basicConfig(
    filename=f"arbitrage_bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

app = Flask(__name__)

class SolanaArbitrageBot:
    def __init__(self):
        self.client = AsyncClient(RPC_ENDPOINT)
        self.keypair = SoldersKeypair.from_seed(bytes.fromhex(WALLET_PRIVATE_KEY))
        self.min_profit = float(os.getenv("MIN_PROFIT", 0.005))
        self.amount = float(os.getenv("TRADE_AMOUNT", 0.1))
        self.slippage = float(os.getenv("SLIPPAGE", 0.5))
        self.fee_per_tx = float(os.getenv("FEE_PER_TX", 0.0005))
        self.volume_spike_threshold = float(os.getenv("VOLUME_SPIKE_THRESHOLD", 2.0))
        self.jupiter_base = JUPITER_API
        self.gmgn_base = GMGN_API
        self.pairs = [
            {"base": "So11111111111111111111111111111111111111112", "quote": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "name": "SOL/USDC"},
            {"base": "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R", "quote": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "name": "RAY/USDC"}
        ]
        self.previous_volumes = {}
        self.running = False
        self.loop = asyncio.new_event_loop()

    async def get_jupiter_price(self, input_mint: str, output_mint: str, amount: int):
        try:
            params = {"inputMint": input_mint, "outputMint": output_mint, "amount": amount * 10**9, "slippageBps": int(self.slippage * 100)}
            response = requests.get(self.jupiter_base, params=params)
            if response.status_code == 200:
                data = response.json()
                return float(data["outAmount"]) / 10**9, data
            logging.warning(f"Jupiter API error: {response.status_code}")
            return None, None
        except Exception as e:
            logging.error(f"Jupiter price fetch error: {e}")
            return None, None

    async def get_gmgn_price_and_volume(self, input_mint: str, output_mint: str, amount: float):
        try:
            url = f"{self.gmgn_base}/tx/get_swap_route?token_in_address={input_mint}&token_out_address={output_mint}&in_amount={int(amount * 10**9)}&from_address={self.keypair.public_key}&slippage={self.slippage}"
            response = requests.get(url)
            if response.status_code == 200:
                data = response.json()
                if data["success"]:
                    out_amount = float(data["data"]["out_amount"]) / 10**9
                    token_info = requests.get(f"{self.gmgn_base}/tokens/{input_mint}").json()
                    volume = float(token_info.get("data", {}).get("volume_24h", 0))
                    return out_amount, volume
            logging.warning(f"GMGN API error: {response.status_code}")
            return None, None
        except Exception as e:
            logging.error(f"GMGN price/volume fetch error: {e}")
            return None, None

    async def execute_jupiter_trade(self, input_mint: str, output_mint: str, amount: float):
        try:
            quote_params = {"inputMint": input_mint, "outputMint": output_mint, "amount": int(amount * 10**9), "slippageBps": int(self.slippage * 100)}
            quote_response = requests.get(self.jupiter_base, params=quote_params).json()
            tx_params = {"quoteResponse": quote_response, "userPublicKey": str(self.keypair.public_key), "wrapAndUnwrapSol": True}
            tx_response = requests.post("https://quote-api.jup.ag/v6/swap", json=tx_params)
            if tx_response.status_code == 200:
                tx_data = tx_response.json()["swapTransaction"]
                tx = Transaction.deserialize(bytes.fromhex(tx_data))
                result = await self.client.send_transaction(tx, self.keypair)
                logging.info(f"Jupiter trade executed: {result}")
                return result
            return None
        except Exception as e:
            logging.error(f"Jupiter trade error: {e}")
            return None

    async def execute_gmgn_trade(self, input_mint: str, output_mint: str, amount: float):
        for attempt in range(3):
            try:
                url = f"{self.gmgn_base}/tx/get_swap_route?token_in_address={input_mint}&token_out_address={output_mint}&in_amount={int(amount * 10**9)}&from_address={self.keypair.public_key}&slippage={self.slippage}"
                route_response = requests.get(url).json()
                if not route_response["success"]:
                    continue
                swap_tx_buf = base64.b64decode(route_response["data"]["raw_tx"]["swapTransaction"])
                transaction = VersionedTransaction.from_bytes(swap_tx_buf)
                transaction.sign([self.keypair])
                signed_tx = base64.b64encode(transaction.serialize()).decode('utf-8')
                submit_response = requests.post(
                    f"{self.gmgn_base}/tx/submit_signed_transaction",
                    json={"signed_tx": signed_tx},
                    headers={'content-type': 'application/json'}
                )
                if submit_response.status_code == 200:
                    tx_hash = submit_response.json()["data"]["hash"]
                    for _ in range(10):
                        status_response = requests.get(f"{self.gmgn_base}/tx/get_transaction_status?hash={tx_hash}")
                        if status_response.status_code == 200 and status_response.json()["data"]["status"] == "confirmed":
                            logging.info(f"GMGN trade confirmed: {tx_hash}")
                            return tx_hash
                        await asyncio.sleep(1)
                    logging.info(f"GMGN trade pending: {tx_hash}")
                    return tx_hash
                await asyncio.sleep(2)
            except Exception as e:
                logging.error(f"GMGN trade attempt {attempt + 1} failed: {e}")
                await asyncio.sleep(2)
        return None

    async def calculate_profit(self, price_buy: float, price_sell: float, amount: float):
        buy_cost = amount * price_buy
        sell_revenue = amount * price_sell
        total_fees = self.fee_per_tx * 2
        return (sell_revenue - buy_cost - total_fees) / buy_cost

    async def find_arbitrage(self, pair):
        amount_in_lamports = int(self.amount * 10**9)
        jupiter_price, _ = await self.get_jupiter_price(pair["base"], pair["quote"], amount_in_lamports)
        gmgn_price, _ = await self.get_gmgn_price_and_volume(pair["base"], pair["quote"], self.amount)

        if jupiter_price and gmgn_price:
            if jupiter_price < gmgn_price:
                profit = await self.calculate_profit(jupiter_price, gmgn_price, self.amount)
                if profit > self.min_profit:
                    return "jupiter_to_gmgn", profit, jupiter_price, gmgn_price
            elif gmgn_price < jupiter_price:
                profit = await self.calculate_profit(gmgn_price, jupiter_price, self.amount)
                if profit > self.min_profit:
                    return "gmgn_to_jupiter", profit, gmgn_price, jupiter_price
        return None, 0, 0, 0

    async def run(self):
        self.running = True
        while self.running:
            try:
                for pair in self.pairs:
                    if not self.running:
                        break
                    logging.info(f"Checking pair: {pair['name']}")
                    direction, profit, price1, price2 = await self.find_arbitrage(pair)
                    if direction == "jupiter_to_gmgn":
                        logging.info(f"Arbitrage: Buy Jupiter (${price1}) -> Sell GMGN (${price2}) - Profit: {profit*100:.2f}%")
                        await self.execute_jupiter_trade(pair["base"], pair["quote"], self.amount)
                        await self.execute_gmgn_trade(pair["quote"], pair["base"], self.amount)
                    elif direction == "gmgn_to_jupiter":
                        logging.info(f"Arbitrage: Buy GMGN (${price1}) -> Sell Jupiter (${price2}) - Profit: {profit*100:.2f}%")
                        await self.execute_gmgn_trade(pair["base"], pair["quote"], self.amount)
                        await self.execute_jupiter_trade(pair["quote"], pair["base"], self.amount)
                    else:
                        logging.info(f"No profitable arbitrage for {pair['name']}")
                await asyncio.sleep(5)
            except Exception as e:
                logging.error(f"Error in run loop: {e}")
                await asyncio.sleep(5)

    def start(self):
        if not self.running:
            Thread(target=lambda: asyncio.run(self.run()), daemon=True).start()

    def stop(self):
        self.running = False

bot = SolanaArbitrageBot()

@app.route('/')
def index():
    return render_template('index.html', status="Running" if bot.running else "Stopped")

@app.route('/start', methods=['POST'])
def start_bot():
    bot.start()
    return jsonify({"message": "Bot started", "status": "Running"})

@app.route('/stop', methods=['POST'])
def stop_bot():
    bot.stop()
    return jsonify({"message": "Bot stopped", "status": "Stopped"})

@app.route('/status', methods=['GET'])
def status():
    return jsonify({"status": "Running" if bot.running else "Stopped"})

@app.route('/logs', methods=['GET'])
def get_logs():
    try:
        with open(logging.root.handlers[0].baseFilename, 'r') as f:
            logs = f.read()
        return jsonify({"logs": logs})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if request.method == 'POST':
        bot.min_profit = float(request.json.get('min_profit', bot.min_profit))
        bot.amount = float(request.json.get('amount', bot.amount))
        bot.slippage = float(request.json.get('slippage', bot.slippage))
        bot.fee_per_tx = float(request.json.get('fee_per_tx', bot.fee_per_tx))
        bot.volume_spike_threshold = float(request.json.get('volume_spike_threshold', bot.volume_spike_threshold))
        logging.info(f"Settings updated: min_profit={bot.min_profit}, amount={bot.amount}, slippage={bot.slippage}")
        return jsonify({"message": "Settings updated"})
    return jsonify({
        "min_profit": bot.min_profit,
        "amount": bot.amount,
        "slippage": bot.slippage,
        "fee_per_tx": bot.fee_per_tx,
        "volume_spike_threshold": bot.volume_spike_threshold
    })

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)
  
