import json
import tempfile
import unittest
from pathlib import Path

from src.radar.adapters.base import LaunchEvent
from src.radar.adapters.registry import AdapterRegistry, ChainConfig, LaunchpadSpec, load_chains


class ChainConfigTests(unittest.TestCase):
    def test_repo_config_loads_verified_chain_facts(self):
        chains = load_chains(Path(__file__).resolve().parents[2] / "config" / "chains.json")
        self.assertEqual(56, chains["bsc"].chain_id)
        self.assertEqual(4663, chains["robinhood"].chain_id)
        self.assertEqual(5042, chains["arc"].chain_id)
        self.assertEqual(988, chains["stable"].chain_id)
        self.assertTrue(chains["bsc"].gmgn_supported)
        self.assertFalse(chains["bsc"].enabled)

    def test_unverified_launchpad_cannot_be_enabled(self):
        with self.assertRaises(ValueError):
            LaunchpadSpec(name="flap", address="0x" + "11" * 20, fact_status="unverified", enabled=True)

    def test_registry_skips_disabled_chains(self):
        configs = {"bsc": ChainConfig(chain="bsc", family="evm", chain_id=56, enabled=False)}
        registry = AdapterRegistry(configs)

        from src.radar.adapters.base import ChainAdapter

        class Fake(ChainAdapter):
            chain = "bsc"
            family = "evm"
            enabled = False

            async def discover(self):
                return [LaunchEvent("bsc", "fourmeme", "0x" + "11" * 20, None, None, None, 1.0, 2.0, "TokenCreate")]

        registry.register(Fake())
        self.assertEqual([], registry.enabled_adapters())
        self.assertEqual("bsc", registry.health()[0]["chain"])

    def test_launch_event_requires_chain_and_token(self):
        with self.assertRaises(ValueError):
            LaunchEvent("", "p", "0x1", None, None, None, 1.0, 2.0, "e")


if __name__ == "__main__":
    unittest.main()
