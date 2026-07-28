import unittest

from counter import running_total


class RunningTotalStartTests(unittest.TestCase):
    def test_start_offsets_the_running_total(self) -> None:
        self.assertEqual(
            running_total([2, -1, 3], start=10),
            [12, 11, 14],
        )

    def test_start_does_not_add_an_item_for_empty_input(self) -> None:
        self.assertEqual(running_total([], start=7), [])


if __name__ == "__main__":
    unittest.main()
