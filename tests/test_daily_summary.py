"""Testes do agendamento do resumo diário (sem esperar o relógio de verdade)."""

from datetime import datetime

from main import DailySummarySchedule


def test_summary_waits_until_configured_time():
    schedule = DailySummarySchedule("09:00", now=datetime(2026, 1, 10, 8, 0))
    assert not schedule.is_due(datetime(2026, 1, 10, 8, 59))
    assert schedule.is_due(datetime(2026, 1, 10, 9, 0))


def test_summary_is_sent_only_once_per_day():
    schedule = DailySummarySchedule("09:00", now=datetime(2026, 1, 10, 8, 0))
    schedule.mark_sent(datetime(2026, 1, 10, 9, 0))
    assert not schedule.is_due(datetime(2026, 1, 10, 15, 0))   # mesmo dia
    assert schedule.is_due(datetime(2026, 1, 11, 9, 0))        # dia seguinte


def test_starting_after_time_skips_today():
    # Bot iniciado às 14h: não manda o resumo das 9h atrasado.
    schedule = DailySummarySchedule("09:00", now=datetime(2026, 1, 10, 14, 0))
    assert not schedule.is_due(datetime(2026, 1, 10, 14, 1))
    assert schedule.is_due(datetime(2026, 1, 11, 9, 0))
