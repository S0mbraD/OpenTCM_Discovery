import math
import unittest

from tcm_core.stats import bh_fdr, hypergeometric_p, log_hypergeom_sf


class HypergeometricTests(unittest.TestCase):
    def test_poker_three_of_a_kind_or_better_in_suit(self):
        # Five-card hand, 13 hearts in a 52-card deck.
        # P(at least 3 hearts) = 241098 / 2598960 ≈ 0.0928.
        p = hypergeometric_p(3, 13, 5, 52)
        self.assertAlmostEqual(p, 241098 / 2598960, places=6)
        self.assertAlmostEqual(p, 0.0928, places=4)

    def test_impossible_count_is_zero(self):
        self.assertEqual(hypergeometric_p(6, 13, 5, 52), 0.0)
        self.assertEqual(log_hypergeom_sf(6, 13, 5, 52), float("-inf"))

    def test_zero_hits_is_certain(self):
        self.assertEqual(hypergeometric_p(0, 13, 5, 52), 1.0)
        self.assertEqual(log_hypergeom_sf(0, 13, 5, 52), 0.0)

    def test_bh_orders_and_caps_at_one(self):
        adj = bh_fdr([0.01, 0.04, 0.20])
        self.assertEqual(len(adj), 3)
        self.assertLess(adj[0], adj[1])
        self.assertLessEqual(max(adj), 1.0)
        self.assertTrue(all(math.isfinite(x) for x in adj))


if __name__ == "__main__":
    unittest.main()
