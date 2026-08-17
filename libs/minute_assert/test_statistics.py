"""statistics：分钟采集周期统计引擎 契约测试（移植自 H_CCO/analyze_minute_logs.py）。"""
import tempfile
from pathlib import Path

from minute_assert.statistics import collect_task_statistics, format_task_statistics


def _report_hex(addr_wire: bytes, task: int, fz: bytes, data_region: str = "000000") -> str:
    return (
        "11e40000013240000000120001"
        + addr_wire.hex()
        + f"{task:02x}02"
        + fz.hex()
        + data_region
    )


def _read_reply_hex(addr_wire: bytes, task: int, fz: bytes, data_region: str = "000000") -> str:
    return (
        "11e30000c11502000000"
        + "02"
        + addr_wire.hex()
        + f"{task:02x}"
        + fz.hex()
        + data_region
    )


def _read_request_hex(addr_wire: bytes, task: int, fz: bytes) -> str:
    return (
        "11e30000010503000000"
        + "42"
        + addr_wire.hex()
        + f"{task:02x}"
        + fz.hex()
    )


def _f232_hex(task: int, addrs_wire: list[bytes]) -> str:
    payload = (
        bytes((task,))
        + len(addrs_wire).to_bytes(2, "little")
        + b"".join(addrs_wire)
    )
    frame = (
        bytearray([0x68, 0, 0, 0x43])
        + bytes(5)
        + bytes((0x01, 0x11, 0x80, 0x1C))
        + payload
    )
    frame[1:3] = len(frame).to_bytes(2, "little")
    frame.append(sum(frame[3:]) & 0xFF)
    frame.append(0x16)
    return frame.hex()


def _write_log(lines: list[str]) -> Path:
    root = Path(tempfile.mkdtemp()) / "logs"
    root.mkdir()
    (root / "minute.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


class TestCollectTaskStatistics:
    def test_all_cycles_reported_with_scene_classification(self):
        config = "11e20000c10315000000080000000000010102"  # 任务1/周期2/启停1
        fz1 = bytes.fromhex("004608310726")  # 08:46
        fz2 = bytes.fromhex("004808310726")  # 08:48
        addr_a = bytes.fromhex("080000000000")
        addr_b = bytes.fromhex("500000141223")
        addr_c = bytes.fromhex("610000141223")
        addr_d = bytes.fromhex("620000141223")
        addr_e = bytes.fromhex("630000141223")
        lines = [
            f"[20260731-09:13:02:241]{config}",
            f"[20260731-09:13:02:250]{_f232_hex(1, [addr_a, addr_b, addr_c, addr_d, addr_e])}",
            # 周期1（08:46-08:48）
            f"[20260731-08:48:01:857]{_report_hex(addr_a, 1, fz1, data_region='012500')}",
            f"[20260731-08:48:01:869]{_report_hex(addr_c, 1, fz1)}",
            f"[20260731-08:48:02:100]{_read_reply_hex(addr_b, 1, fz1, data_region='030000')}",
            f"[20260731-08:48:02:150]{_read_reply_hex(addr_e, 1, fz1)}",
            f"[20260731-08:48:02:200]{_read_request_hex(addr_d, 1, fz1)}",
            # 周期2（08:48-08:50）
            f"[20260731-08:50:03:000]{_report_hex(addr_a, 1, fz2, data_region='012500')}",
        ]
        log_dir = _write_log(lines)

        period_map, task_stats, broadcast_delete = collect_task_statistics([log_dir / "minute.log"])

        assert broadcast_delete == 0
        assert period_map == {1: 2}
        assert len(task_stats) == 1
        task = task_stats[0]
        assert task.task_id == 1
        assert task.period == 2
        assert task.configured_addresses == (
            "000000000008", "231214000050", "231214000061", "231214000062", "231214000063",
        )
        assert len(task.cycles) == 2
        cycle1 = task.cycles[0]
        assert cycle1.active_ok == 1
        assert cycle1.passive_ok == 1
        assert len(cycle1.passive_collect_failed) == 2
        # 失败明细（地址反序后）：231214000062 下发采集无响应，231214000063 被动上报无数据
        assert cycle1.passive_collect_failed[0][0] == "231214000062"
        assert cycle1.passive_collect_failed[0][2] == "下发采集无响应"
        assert cycle1.passive_collect_failed[1][0] == "231214000063"
        assert cycle1.passive_collect_failed[1][2] == "被动上报无数据"
        cycle2 = task.cycles[1]
        assert cycle2.active_ok == 1
        assert cycle2.passive_ok == 0
        assert cycle2.passive_collect_failed == ()

    def test_broadcast_delete_counted(self):
        config = "11e20000c10315000000080000000000030102"
        delete = "11e20000c10315000000080000000000030002"
        broadcast = "11e20000c10315000000080000000000ff0002"
        report = "11e400000132400000001200010800000000000343004608310726000000"
        log_dir = _write_log([
            f"[20260731-09:13:02:241]{config}",
            f"[20260731-09:13:03:241]{delete}",
            f"[20260731-09:13:04:241]{broadcast}",
            f"[20260731-08:48:01:857]{report}",
        ])

        period_map, task_stats, broadcast_delete = collect_task_statistics([log_dir / "minute.log"])

        assert broadcast_delete == 1
        assert len(task_stats) == 1
        task = task_stats[0]
        assert task.delete_count == 1  # 任务级删除
        assert task.task_id == 3
        assert task.period == 2


class TestFormatTaskStatistics:
    def test_formats_summary_block(self):
        config = "11e20000c10315000000080000000000010102"
        fz1 = bytes.fromhex("004608310726")
        addr_a = bytes.fromhex("080000000000")
        log_dir = _write_log([
            f"[20260731-09:13:02:241]{config}",
            f"[20260731-08:48:01:857]{_report_hex(addr_a, 1, fz1, data_region='012500')}",
        ])

        _, task_stats, _ = collect_task_statistics([log_dir / "minute.log"])
        text = format_task_statistics(task_stats)

        assert "任务专项统计" in text
        assert "任务1：配置电表0只，周期为2，关联表档案为空" in text
        assert "周期1【08:46-08:48】统计信息：1只表上报成功" in text
