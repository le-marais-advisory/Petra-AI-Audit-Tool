from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.generate_capital_event_fixtures import (
    FixtureManifest,
    FixtureSpec,
    build_fixture,
    default_specs,
)


class CapitalEventFixtures:
    """Session-wide cache of generated capital-event workbooks, keyed by fixture id."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self._cache: dict[str, FixtureManifest] = {}

    def get(self, event_type: str = "capital_call", variant: str = "standard", defect: str | None = None,
            with_prior: bool = False) -> FixtureManifest:
        spec = FixtureSpec(event_type=event_type, variant=variant, defect=defect, with_prior=with_prior)
        if spec.fixture_id not in self._cache:
            self._cache[spec.fixture_id] = build_fixture(spec, self.out_dir)
        return self._cache[spec.fixture_id]

    def all(self) -> list[FixtureManifest]:
        return [self.get(s.event_type, s.variant, s.defect, s.with_prior) for s in default_specs()]


@pytest.fixture(scope="session")
def capital_event_fixtures(tmp_path_factory: pytest.TempPathFactory) -> CapitalEventFixtures:
    return CapitalEventFixtures(tmp_path_factory.mktemp("capital_event_fixtures"))
