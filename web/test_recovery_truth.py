from pathlib import Path

from features.system import backups
from features.system import restore_drill


def test_recovery_recognizes_azimuth_and_legacy_snapshots(tmp_path: Path) -> None:
    for name in ("azimuth-20260725-040000.db.gz", "azimuth-20260724-040000.db.gz"):
        (tmp_path / name).write_bytes(b"fixture")

    names = [path.name for path in backups._snapshot_paths(tmp_path)]

    assert names == ["azimuth-20260724-040000.db.gz", "azimuth-20260725-040000.db.gz"]
    assert restore_drill.newest_sealed_snapshot(tmp_path).name == "azimuth-20260725-040000.db.gz"
