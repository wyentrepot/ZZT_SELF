"""证据限量保留测试（REQS-0042 v11 D11-4）。

用户口径（2026-11-23 直批）：
> 「证据目录也不能无限存储，**只保留近 30 轮**的记录。」

本文件守六件事（全是 ``prune_runs`` 的契约）：

1. **按运行时间倒序只留最近 ``keep`` 轮**：35 轮 → 留 30，最旧 5 轮被删且 ``pruned`` 逐字对；
2. **``keep <= 0`` 不裁剪**（``--keep-runs 0`` / 负数 = 不限），返回 ``enabled=False``；
3. **当前这一轮永不删除**——即使它按运行时间排在第 30 名之外（名单最老的那一头）；
4. **只删目录、不删文件**：混进来的 ``README.txt`` 之类一律不碰、不崩；
5. **排序口径**：目录名（运行号 = 运行时间）优先，名字解析不出时用 mtime 兜底；
6. **删除失败只 warning 不抛**（占用/权限不能把一次运行搞挂）。

全部离线：只在 ``tmp_path`` 下造空目录，不碰 ``data/**``、不碰台架、零真实等待。
"""

from __future__ import annotations

import datetime as dt
import os
import pathlib

import pytest

from case_runner.core.retention import (
    DEFAULT_KEEP_RUNS,
    looks_like_run_id,
    prune_runs,
)

# --------------------------------------------------------------------------- #
# 夹具：造 run 目录
# --------------------------------------------------------------------------- #


def make_dir(
    root: pathlib.Path, name: str, *, mtime: dt.datetime | None = None
) -> pathlib.Path:
    """造一个目录（带 result.json，形状与实际证据目录一致）；``mtime`` 可指定。"""

    target = root / name
    target.mkdir(parents=True, exist_ok=True)
    (target / "result.json").write_text('{"run_id": "%s"}' % name, encoding="utf-8")
    if mtime is not None:
        stamp = mtime.timestamp()
        os.utime(target, (stamp, stamp))
    return target


def run_id_at(index: int) -> str:
    """第 ``index`` 轮运行号：``20261101-000000-xxxxxx``（递增 → 名字天然可排序）。"""

    return "20261101-%06d-%06x" % (index, index)


def seed_runs(root: pathlib.Path, count: int, *, start: int = 0) -> list[str]:
    """造 ``count`` 轮合法运行目录（名字按时间递增）。"""

    names = []
    for offset in range(count):
        index = start + offset
        name = run_id_at(index)
        make_dir(root, name)
        names.append(name)
    return names


def names_of(root: pathlib.Path) -> list[str]:
    return sorted(path.name for path in root.iterdir())


@pytest.fixture()
def runs_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "case_runs"
    root.mkdir()
    return root


# --------------------------------------------------------------------------- #
# 1. 35 轮 → 留 30，最旧 5 轮被删
# --------------------------------------------------------------------------- #


def test_keeps_latest_thirty_of_thirty_five(runs_dir: pathlib.Path) -> None:
    names = seed_runs(runs_dir, 35)

    report = prune_runs(runs_dir, 30, current_run_id=names[-1])

    assert report["enabled"] is True
    assert report["pruned"] == names[:5], "最旧的 5 轮必须被删，pruned 按从旧到新列出"
    assert report["kept"] == 30
    assert names_of(runs_dir) == names[5:]
    for doomed in report["pruned"]:
        assert not (runs_dir / doomed).exists()


def test_default_keep_is_thirty(runs_dir: pathlib.Path) -> None:
    """缺省就是 30 轮（D11-4 写死的那个数），不传参也成立。"""

    assert DEFAULT_KEEP_RUNS == 30
    names = seed_runs(runs_dir, 33)
    report = prune_runs(runs_dir)
    assert report["pruned"] == names[:3]
    assert report["kept"] == 30


def test_nothing_to_do_when_fewer_runs_than_keep(runs_dir: pathlib.Path) -> None:
    seed_runs(runs_dir, 3)
    report = prune_runs(runs_dir, 30, current_run_id=run_id_at(3))
    assert report == {"pruned": [], "kept": 3, "enabled": True}


def test_empty_runs_dir_is_a_noop(runs_dir: pathlib.Path) -> None:
    assert prune_runs(runs_dir, 30) == {"pruned": [], "kept": 0, "enabled": True}


def test_missing_runs_dir_does_not_raise(tmp_path: pathlib.Path) -> None:
    report = prune_runs(tmp_path / "never-created", 30)
    assert report == {"pruned": [], "kept": 0, "enabled": True}


# --------------------------------------------------------------------------- #
# 2. keep <= 0 → 不裁剪
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("keep", [0, -1, -30])
def test_keep_zero_or_negative_never_prunes(runs_dir: pathlib.Path, keep: int) -> None:
    names = seed_runs(runs_dir, 40)
    report = prune_runs(runs_dir, keep, current_run_id=names[-1])
    assert report == {"pruned": [], "kept": 40, "enabled": False}
    assert len(list(runs_dir.iterdir())) == 40, "不限轮数时一个目录都不许删"


# --------------------------------------------------------------------------- #
# 3. 当前这一轮永不删除（哪怕它排在第 30 名之外）
# --------------------------------------------------------------------------- #


def test_current_run_survives_even_when_it_is_the_oldest(runs_dir: pathlib.Path) -> None:
    names = seed_runs(runs_dir, 35)
    oldest = names[0]

    report = prune_runs(runs_dir, 30, current_run_id=oldest)

    assert oldest not in report["pruned"]
    assert (runs_dir / oldest).is_dir(), "当前轮（哪怕最旧）绝不许被删"
    # 「最近 30 轮」照旧全留，当前轮额外保住 → 共 31 轮；被删的是原本第 2..5 旧的那 4 轮
    assert report["pruned"] == names[1:5]
    assert report["kept"] == 31


def test_current_run_survives_even_with_an_unknown_name(runs_dir: pathlib.Path) -> None:
    """当前轮名字不像运行号（坏名字）也必须保住。"""

    seed_runs(runs_dir, 35)
    make_dir(runs_dir, "current-weird-name", mtime=dt.datetime(2020, 1, 1))

    report = prune_runs(runs_dir, 30, current_run_id="current-weird-name")

    assert "current-weird-name" not in report["pruned"]
    assert (runs_dir / "current-weird-name").is_dir()


# --------------------------------------------------------------------------- #
# 4. 只删目录：文件不动、不崩
# --------------------------------------------------------------------------- #


def test_files_are_never_pruned(runs_dir: pathlib.Path) -> None:
    names = seed_runs(runs_dir, 35)
    keep_file = runs_dir / "README.txt"
    keep_file.write_text("证据根目录说明，不许删", encoding="utf-8")

    report = prune_runs(runs_dir, 30, current_run_id=names[-1])

    assert report["pruned"] == names[:5]
    assert keep_file.is_file(), "普通文件不在裁剪范围"
    assert keep_file.read_text(encoding="utf-8").startswith("证据根目录说明")


def test_nested_files_inside_a_pruned_run_go_with_the_directory(
    runs_dir: pathlib.Path,
) -> None:
    names = seed_runs(runs_dir, 31)
    doomed = runs_dir / names[0]
    (doomed / "cli.log").write_text("日志", encoding="utf-8")
    (doomed / "frames").mkdir()
    (doomed / "frames" / "f.jsonl").write_text("{}\n", encoding="utf-8")

    prune_runs(runs_dir, 30, current_run_id=names[-1])

    assert not doomed.exists(), "整轮目录（含日志/帧）一起清掉"


def test_run_id_detection() -> None:
    assert looks_like_run_id("20261123-172601-1778df")
    assert looks_like_run_id("20261123-172601-ABCDEF")
    assert not looks_like_run_id("lost+found")
    assert not looks_like_run_id("20261123")
    assert not looks_like_run_id("backup-20261123-172601-1778df")


# --------------------------------------------------------------------------- #
# 5. 排序口径：名字（运行时间）优先，mtime 只兜底
# --------------------------------------------------------------------------- #


def test_name_order_wins_over_mtime(runs_dir: pathlib.Path) -> None:
    """名字就是运行时间：mtime 被 touch 到很新也不许改变「谁旧谁新」。

    起因：拷贝/恢复/解包会把 mtime 全部刷成当前时间，只看 mtime 就会把**真旧**那轮
    当成新轮留下、把真新的删掉——运行号由 ``new_run_id()`` 生成，才是可信的时间戳。
    """

    names = seed_runs(runs_dir, 35)
    oldest, newest = names[0], names[-1]
    later = dt.datetime.now() + dt.timedelta(hours=1)
    os.utime(runs_dir / oldest, (later.timestamp(), later.timestamp()))

    report = prune_runs(runs_dir, 30, current_run_id=newest)

    assert report["pruned"] == names[:5], "排序看名字，不看被 touch 过的 mtime"


def test_mtime_is_the_fallback_for_unparseable_names(runs_dir: pathlib.Path) -> None:
    """名字解析不出运行号 → 用 mtime 兜底，与运行号同一条时间轴比较。"""

    seed_runs(runs_dir, 29)                     # 名字里是 2026-11-01
    old_junk = make_dir(runs_dir, "manual-old", mtime=dt.datetime(2026, 10, 1))
    new_junk = make_dir(runs_dir, "manual-new", mtime=dt.datetime(2026, 11, 22))

    report = prune_runs(runs_dir, 30, current_run_id=run_id_at(28))

    assert report["pruned"] == ["manual-old"], "坏名字按 mtime 兜底后，它就是最旧那一个"
    assert not old_junk.exists()
    assert new_junk.is_dir(), "mtime 更近的坏名字目录照旧保留"
    assert report["kept"] == 30


# --------------------------------------------------------------------------- #
# 6. 删除失败只 warning，不抛
# --------------------------------------------------------------------------- #


def test_delete_failure_only_warns(
    runs_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """删不掉（占用/权限）不能把一次运行搞挂：记 warning、继续删下一个。"""

    names = seed_runs(runs_dir, 32)
    from case_runner.core import retention

    real_rmtree = retention.shutil.rmtree
    refused = names[0]

    def fake_rmtree(path, *args, **kwargs):
        if pathlib.Path(path).name == refused:
            raise PermissionError("被占用：模拟删除失败")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(retention.shutil, "rmtree", fake_rmtree)
    warnings: list[str] = []

    report = prune_runs(runs_dir, 30, current_run_id=names[-1], warn=warnings.append)

    assert report["pruned"] == names[1:2], "删成功的那轮才算 pruned"
    assert (runs_dir / refused).is_dir(), "删失败的目录原样留着"
    assert warnings and refused in warnings[0]
    assert report["kept"] == 31


def test_warn_defaults_to_stderr(
    runs_dir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    names = seed_runs(runs_dir, 31)
    from case_runner.core import retention

    def always_fail(path, *args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(retention.shutil, "rmtree", always_fail)
    report = prune_runs(runs_dir, 30, current_run_id=names[-1])

    assert report["pruned"] == []
    assert "boom" in capsys.readouterr().err
