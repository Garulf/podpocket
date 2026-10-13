import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from google.protobuf.timestamp_pb2 import Timestamp
from google.protobuf.wrappers_pb2 import BoolValue

from podpocket.protocol import from_timestamp, to_timestamp, wrapped
from podpocket.protocol import sync_api_pb2 as pb

PROTOCOL_DIR = Path(__file__).resolve().parents[1] / "src" / "podpocket" / "protocol"
SOURCE_COMMIT = "ef03cbcd1e91e8aec2ac8d9bf161a9963b4c7514"


def test_committed_stub_matches_the_vendored_proto(tmp_path):
    subprocess.run(
        [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            f"-I{PROTOCOL_DIR}",
            f"--python_out={tmp_path}",
            str(PROTOCOL_DIR / "sync_api.proto"),
        ],
        check=True,
    )

    regenerated = (tmp_path / "sync_api_pb2.py").read_bytes()
    assert regenerated == (PROTOCOL_DIR / "sync_api_pb2.py").read_bytes()


def test_vendored_proto_records_its_source_commit():
    first_line = (PROTOCOL_DIR / "sync_api.proto").read_text().splitlines()[0]

    assert SOURCE_COMMIT in first_line


def test_wrapper_presence_survives_a_round_trip():
    record = pb.Record(podcast=pb.SyncUserPodcast(uuid="p", subscribed=BoolValue(value=False)))

    parsed = pb.Record.FromString(record.SerializeToString())

    assert parsed.WhichOneof("record") == "podcast"
    assert parsed.podcast.HasField("subscribed")
    assert wrapped(parsed.podcast, "subscribed") is False
    assert wrapped(parsed.podcast, "is_deleted") is None


def test_timestamps_round_trip_in_utc():
    value = datetime(2026, 10, 12, 10, 0, 0, 123000, tzinfo=UTC)

    stamp = to_timestamp(value)

    assert stamp.seconds == 1791799200
    assert from_timestamp(stamp) == value


@pytest.mark.parametrize(
    "stamp",
    [
        Timestamp(seconds=2**62),
        Timestamp(seconds=-(2**62)),
        Timestamp(seconds=253402300800),
        Timestamp(seconds=1, nanos=-5),
    ],
)
def test_timestamps_outside_the_valid_range_are_none(stamp):
    assert from_timestamp(stamp) is None
