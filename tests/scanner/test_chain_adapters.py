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

    def test_repo_config_live_verified_launchpads_have_sources(self):
        chains = load_chains(Path(__file__).resolve().parents[2] / "config" / "chains.json")
        bsc = {item.name: item for item in chains["bsc"].launchpads}
        self.assertEqual("0xe2ce6ab80874fa9fa2aae65d277dd6b8e65c9de0", bsc["flap_portal"].address)
        self.assertEqual("verified", bsc["flap_portal"].fact_status)
        self.assertEqual("0x912cef0c3ae9ab6eb3ec87cab69371cfb317ab94", bsc["openfour_registry"].address)
        robinhood = {item.name: item for item in chains["robinhood"].launchpads}
        self.assertEqual("0x4e3468951d49f2eea976ed0d6e75ffcb44a9a544",
                         robinhood["doppler_hook_initializer"].address)
        solana = {item.name: item for item in chains["sol"].launchpads}
        self.assertEqual("solana", solana["pump_fun_program"].family)
        for chain in chains.values():
            for launchpad in chain.launchpads:
                if launchpad.fact_status == "verified":
                    self.assertTrue(launchpad.source, f"{chain.chain}/{launchpad.name} lacks a source")

    def test_solana_launchpad_address_validation(self):
        with self.assertRaises(ValueError):
            LaunchpadSpec(name="bad", address="0x" + "11" * 20, fact_status="verified",
                          family="solana", source="test")
        with self.assertRaises(ValueError):
            LaunchpadSpec(name="bad", address="not-a-solana-address", fact_status="verified",
                          family="solana", source="test")
        spec = LaunchpadSpec(name="pump", address="6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
                             fact_status="verified", family="solana", source="test")
        self.assertFalse(spec.enabled)

    def test_verified_launchpad_requires_source(self):
        with self.assertRaises(ValueError):
            LaunchpadSpec(name="flap", address="0x" + "11" * 20, fact_status="verified")

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
