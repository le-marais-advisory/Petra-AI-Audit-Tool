"""Investor identity across sheets: (vehicle, name), with vehicle blocks aligned by their investors."""
from __future__ import annotations

from dataclasses import dataclass

from src.pipeline.workbook.keys import InvestorIndex, Matcher, align_vehicles


@dataclass
class Inv:
    name: str
    row: int = 0


@dataclass
class Vehicle:
    name: str
    investors: list


def _vehicle(name, *names):
    return Vehicle(name, [Inv(n, i) for i, n in enumerate(names, start=1)])


def test_same_name_in_two_vehicles_is_matched_block_by_block():
    source = [_vehicle("Fund IV", "Sustar Trust", "BBR", "GP Entity"),
              _vehicle("Executive Fund", "Sustar Trust", "Chaikin", "GP Entity")]
    target = [_vehicle("Main", "Sustar Trust", "BBR", "GP Entity"),
              _vehicle("Exec", "Sustar Trust", "Chaikin", "GP Entity")]
    matcher = Matcher(source, target)
    assert matcher.alignment == {0: 0, 1: 1}
    assert matcher.find(0, "Sustar Trust") is target[0].investors[0]
    assert matcher.find(1, "Sustar Trust") is target[1].investors[0]
    assert matcher.find(1, "GP Entity") is target[1].investors[2]
    assert matcher.label(0, "Sustar Trust") == "Sustar Trust (Fund IV)"
    assert matcher.label(0, "BBR") == "BBR"


def test_blocks_are_aligned_by_overlap_when_their_order_differs():
    source = [_vehicle("A", "x", "y", "z"), _vehicle("B", "p", "q")]
    target = [_vehicle("second", "p", "q", "r"), _vehicle("first", "x", "y")]
    assert align_vehicles(source, target) == {0: 1, 1: 0}


def test_unique_names_fall_back_across_blocks():
    source = [_vehicle("A", "x", "y"), _vehicle("B", "p")]
    target = [_vehicle("One", "x", "y", "p")]  # the target sheet lists every investor in one block
    matcher = Matcher(source, target)
    assert matcher.find(1, "p") is target[0].investors[2]
    assert matcher.find(0, "missing") is None


def test_index_refuses_an_ambiguous_name_outside_its_block():
    index = InvestorIndex([_vehicle("A", "dup", "a"), _vehicle("B", "dup", "b")])
    assert index.get(0, "dup").row == 1
    assert index.get(1, "dup").row == 1 and index.get(1, "dup") is index.vehicles[1].investors[0]
    assert index.get(None, "dup") is None
    assert index.get(None, "a").row == 2


def test_source_has_reports_target_rows_without_a_counterpart():
    source = [_vehicle("A", "x")]
    target = [_vehicle("A", "x", "gone")]
    matcher = Matcher(source, target)
    assert matcher.source_has(0, "x") is True
    assert matcher.source_has(0, "gone") is False
