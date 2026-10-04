"""Regression checks for the retired user-facing Wallet Safety feature."""
from pathlib import Path
import unittest

from app.routers import safety


class RemovedWalletSafetyTests(unittest.TestCase):
    def test_allowance_and_simulation_routes_are_not_exposed(self):
        paths = {route.path for route in safety.router.routes}
        self.assertFalse(any("allowance" in path for path in paths))
        self.assertNotIn("/safety/simulate", paths)

    def test_wallet_safety_panel_and_button_are_not_in_frontend(self):
        html = (Path(__file__).resolve().parents[2] / "index.html").read_text()
        for retired_marker in (
            "col2BtnSafety", "safetyView", "loadAllowances",
            "confirmRevokeAllowance", "Wallet safety",
        ):
            self.assertNotIn(retired_marker, html)


if __name__ == "__main__":
    unittest.main()
