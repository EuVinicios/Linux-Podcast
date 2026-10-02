import time
import unittest
from datetime import datetime

import tests  # noqa: F401
from src.utils import time_format as tf


class TimeFormatTests(unittest.TestCase):
    def test_clock(self):
        self.assertEqual(tf.format_clock(0), "0:00")
        self.assertEqual(tf.format_clock(42.9), "0:42")
        self.assertEqual(tf.format_clock(725), "12:05")
        self.assertEqual(tf.format_clock(3723), "1:02:03")
        self.assertEqual(tf.format_clock(None), "0:00")
        self.assertEqual(tf.format_clock(float("nan")), "0:00")

    def test_remaining(self):
        self.assertEqual(tf.format_remaining(60, 3600), "-59:00")
        self.assertEqual(tf.format_remaining(0, 3723), "-1:02:03")
        self.assertEqual(tf.format_remaining(10, 0), "--:--")
        self.assertEqual(tf.format_remaining(4000, 3600), "-0:00")

    def test_duration(self):
        self.assertEqual(tf.format_duration(4920), "1h 22min")
        self.assertEqual(tf.format_duration(3600), "1h")
        self.assertEqual(tf.format_duration(2700), "45min")
        self.assertEqual(tf.format_duration(30), "30s")
        self.assertEqual(tf.format_duration(0), "")

    def test_time_left(self):
        self.assertEqual(tf.format_time_left(0, 1380), "Faltam 23 min")
        self.assertEqual(tf.format_time_left(0, 3900), "Faltam 1h 05min")
        self.assertEqual(tf.format_time_left(1370, 1380), "Falta menos de 1 min")

    def test_dates(self):
        now = datetime(2026, 10, 1, 15, 0).timestamp()
        self.assertEqual(tf.format_date(datetime(2026, 10, 1, 8, 0).timestamp(), now), "Hoje")
        self.assertEqual(tf.format_date(datetime(2026, 9, 30, 8, 0).timestamp(), now), "Ontem")
        self.assertEqual(tf.format_date(datetime(2026, 9, 28, 8, 0).timestamp(), now),
                         "Segunda-feira")
        self.assertEqual(tf.format_date(datetime(2026, 9, 3, 8, 0).timestamp(), now), "3 de set.")
        self.assertEqual(tf.format_date(datetime(2024, 1, 15, 8, 0).timestamp(), now),
                         "15 de jan. de 2024")
        self.assertEqual(tf.format_date(0, now), "")
        self.assertEqual(tf.format_long_date(datetime(2026, 9, 25).timestamp()), "25 de set. de 2026")

    def test_parse_duration(self):
        self.assertEqual(tf.parse_duration("3723"), 3723)
        self.assertEqual(tf.parse_duration("62:03"), 3723)
        self.assertEqual(tf.parse_duration("1:02:03"), 3723)
        self.assertEqual(tf.parse_duration("3723.6"), 3723)
        self.assertEqual(tf.parse_duration(""), 0)
        self.assertEqual(tf.parse_duration("abc"), 0)

    def test_speed_and_size(self):
        self.assertEqual(tf.format_speed(1.0), "1×")
        self.assertEqual(tf.format_speed(1.25), "1,25×")
        self.assertEqual(tf.format_speed(0.75), "0,75×")
        self.assertEqual(tf.format_speed(2.0), "2×")
        self.assertEqual(tf.format_size(45_200_000), "45,2 MB")
        self.assertEqual(tf.format_size(999), "999 B")
        self.assertEqual(tf.format_size(0), "")

    def test_now_is_today(self):
        self.assertEqual(tf.format_date(time.time()), "Hoje")


if __name__ == "__main__":
    unittest.main()
