"""
Data Processor and Output Handler
Formats and outputs event data to terminal and files
"""

import json
import logging
from typing import Dict
from pathlib import Path
from datetime import datetime
from colorama import Fore, Style, init

# Initialize colorama
init(autoreset=True)

logger = logging.getLogger(__name__)


class DataProcessor:
    """Process and output FourMeme events"""

    def __init__(self, output_dir: str = "data/events"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Current output file
        self.current_file = None
        self.current_date = None

        # Statistics
        self.events_saved = 0
        self.events_by_type = {
            'TokenCreate': 0,
            'TokenPurchase': 0,
            'TokenPurchase2': 0,
            'TokenSale': 0,
            'TokenSale2': 0,
            'TradeStop': 0,
            'LiquidityAdded': 0
        }

    def _get_output_file(self) -> Path:
        """Get output file for current date (auto-rotate daily)"""
        today = datetime.now().strftime('%Y%m%d')

        if today != self.current_date:
            self.current_date = today
            self.current_file = self.output_dir / f"fourmeme_events_{today}.jsonl"
            logger.info(f"Output file: {self.current_file}")

        return self.current_file

    async def process_event(self, event_name: str, event_data: Dict):
        """Process an event: format, print, and save"""
        try:
            # Format event data
            formatted = await self._format_event(event_name, event_data)

            # Print to terminal
            self._print_event(event_name, formatted)

            # Save to file
            await self._save_event(event_name, formatted)

            # Update statistics
            self.events_by_type[event_name] = self.events_by_type.get(event_name, 0) + 1
            self.events_saved += 1

        except Exception as e:
            logger.error(f"Error processing event {event_name}: {e}")

    async def _format_event(self, event_name: str, event_data: Dict) -> Dict:
        """Format raw event data into structured format"""
        timestamp = event_data.get('timestamp', int(datetime.now().timestamp()))
        block_number = event_data.get('blockNumber', 0)
        tx_hash_raw = event_data.get('transactionHash', b'')
        tx_hash = tx_hash_raw.hex() if isinstance(tx_hash_raw, bytes) else str(tx_hash_raw)

        # Base structure
        formatted = {
            'event_type': self._event_type_map(event_name),
            'timestamp': timestamp,
            'datetime': datetime.fromtimestamp(timestamp).isoformat(),
            'block_number': block_number,
            'tx_hash': tx_hash,
        }

        # Extract event-specific data (convert AttributeDict to regular dict)
        args_raw = event_data.get('args', {})
        args = dict(args_raw) if args_raw else {}

        if event_name == 'TokenCreate':
            formatted.update({
                'creator': args.get('creator', ''),
                'token_address': args.get('token', ''),
                'request_id': args.get('requestId', 0),
                'token_name': args.get('name', 'Unknown'),
                'token_symbol': args.get('symbol', 'Unknown'),
                'total_supply': float(args.get('totalSupply', 0)) / 1e18,
                'launch_time': args.get('launchTime', 0),
                'launch_fee': float(args.get('launchFee', 0)) / 1e18,  # Wei to BNB
            })

        elif event_name == 'TokenPurchase':
            formatted.update({
                'token_address': args.get('token', ''),
                'account': args.get('account', ''),
                'token_amount': float(args.get('amount', 0)) / 1e18,  # Changed from tokenAmount to amount
                'ether_amount': float(args.get('cost', 0)) / 1e18,    # Changed from etherAmount to cost
                'fee': float(args.get('fee', 0)) / 1e18,
            })

        elif event_name == 'TokenPurchase2':
            formatted.update({
                'origin': args.get('origin', 0),
            })

        elif event_name == 'TokenSale':
            formatted.update({
                'token_address': args.get('token', ''),
                'account': args.get('account', ''),
                'token_amount': float(args.get('amount', 0)) / 1e18,  # Changed from tokenAmount to amount
                'ether_amount': float(args.get('cost', 0)) / 1e18,    # Changed from etherAmount to cost
                'fee': float(args.get('fee', 0)) / 1e18,
            })

        elif event_name == 'TokenSale2':
            formatted.update({
                'origin': args.get('origin', 0),
            })

        elif event_name == 'TradeStop':
            formatted.update({
                'token_address': args.get('token', ''),
            })

        return formatted

    def _event_type_map(self, event_name: str) -> str:
        """Map event names to simplified types"""
        mapping = {
            'TokenCreate': 'launch',
            'TokenPurchase': 'buy',
            'TokenPurchase2': 'buy',
            'TokenSale': 'sell',
            'TokenSale2': 'sell',
            'TradeStop': 'graduate',
            'LiquidityAdded': 'graduate'
        }
        return mapping.get(event_name, 'unknown')

    def _print_event(self, event_name: str, data: Dict):
        """Print formatted event to terminal with colors"""
        # Skip auxiliary events
        if event_name in ['TokenPurchase2', 'TokenSale2']:
            return

        # 核心改动: 如果是全链买卖，默认不打印 (太频繁)
        # 除非是 TokenCreate (Launch) 或 TradeStop (Graduate)
        event_type = data['event_type']
        if event_type in ['buy', 'sell']:
            return

        timestamp = datetime.fromtimestamp(data['timestamp']).strftime('%Y-%m-%d %H:%M:%S')

        # Event type with emoji and color
        event_styles = {
            'launch': (Fore.GREEN + '🚀 LAUNCH', Style.RESET_ALL),
            'buy': (Fore.BLUE + '💰 BUY   ', Style.RESET_ALL),
            'sell': (Fore.YELLOW + '💸 SELL  ', Style.RESET_ALL),
            'graduate': (Fore.CYAN + '🎓 GRADUATE', Style.RESET_ALL),
        }

        event_type = data['event_type']
        emoji_prefix, reset = event_styles.get(event_type, (Fore.WHITE + '📍 EVENT', Style.RESET_ALL))

        # Build output string based on event type
        if event_type == 'launch':
            output = (
                f"[{timestamp}] {emoji_prefix}{reset} | "
                f"{Fore.MAGENTA}${data.get('token_symbol', 'N/A')}{reset} "
                f"({data.get('token_name', 'N/A')}) | "
                f"{data.get('token_address', '')[:10]}... | "
                f"Creator: {data.get('creator', '')[:10]}..."
            )

        elif event_type == 'buy':
            output = (
                f"[{timestamp}] {emoji_prefix}{reset} | "
                f"{data.get('token_address', '')[:10]}... | "
                f"Buyer: {data.get('account', '')[:10]}... | "
                f"{Fore.GREEN}{data.get('ether_amount', 0):.4f} BNB{reset} → "
                f"{data.get('token_amount', 0):,.2f} tokens"
            )

        elif event_type == 'sell':
            output = (
                f"[{timestamp}] {emoji_prefix}{reset} | "
                f"{data.get('token_address', '')[:10]}... | "
                f"Seller: {data.get('account', '')[:10]}... | "
                f"{Fore.YELLOW}{data.get('token_amount', 0):,.2f} tokens{reset} → "
                f"{data.get('ether_amount', 0):.4f} BNB"
            )

        elif event_type == 'graduate':
            output = (
                f"[{timestamp}] {emoji_prefix}{reset} | "
                f"{data.get('token_address', '')[:10]}... | "
                f"{Fore.CYAN}Trading Stopped - Ready for DEX{reset}"
            )

        else:
            output = f"[{timestamp}] {emoji_prefix}{reset} | {json.dumps(data)}"

        print(output)

    async def _save_event(self, event_name: str, data: Dict):
        """Save event to JSONL file"""
        try:
            output_file = self._get_output_file()

            # Append to file (one JSON per line)
            with output_file.open('a', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
                f.write('\n')

        except Exception as e:
            logger.error(f"Failed to save event to file: {e}")

            # Try to save to error log
            error_file = self.output_dir / "error_events.jsonl"
            try:
                with error_file.open('a', encoding='utf-8') as f:
                    json.dump({
                        'error': str(e),
                        'event_name': event_name,
                        'data': data,
                        'timestamp': datetime.now().isoformat()
                    }, f)
                    f.write('\n')
            except Exception as err:
                logger.critical(f"Failed to save to error log: {err}")

    def print_stats(self):
        """Print statistics"""
        print(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}Statistics:{Style.RESET_ALL}")
        print(f"  Total events processed: {Fore.GREEN}{self.events_saved}{Style.RESET_ALL}")
        for event_type, count in self.events_by_type.items():
            if count > 0:
                print(f"    {event_type}: {count}")
        print(f"  Output directory: {self.output_dir}")
        print(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")

    def get_stats(self) -> Dict:
        """Get processor statistics"""
        return {
            'total_events': self.events_saved,
            'events_by_type': self.events_by_type.copy(),
            'output_dir': str(self.output_dir),
            'current_file': str(self.current_file) if self.current_file else None
        }
