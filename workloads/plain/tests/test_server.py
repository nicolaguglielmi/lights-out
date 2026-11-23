import unittest

from plain.server import route


class RouteTest(unittest.TestCase):
    def test_ok_and_root_answer_200(self) -> None:
        self.assertEqual(route("/ok"), (200, b"ok\n"))
        self.assertEqual(route("/"), (200, b"plain\n"))

    def test_no_metrics_or_healthz_json(self) -> None:
        self.assertEqual(route("/metrics")[0], 404)
        self.assertEqual(route("/healthz")[0], 404)


if __name__ == "__main__":
    unittest.main()
