import unittest

from counter import running_total


class RunningTotalTests(unittest.TestCase):
    def test_accumulates_positive_and_negative_values(self) -> None:
        self.assertEqual(running_total([2, -1, 3]), [2, 1, 4])

    def test_empty_input_is_empty(self) -> None:
        self.assertEqual(running_total([]), [])


if __name__ == "__main__":
    unittest.main()
