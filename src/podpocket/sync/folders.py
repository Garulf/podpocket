from collections.abc import Iterable

from podpocket.models import Folder
from podpocket.protocol import from_timestamp, to_timestamp
from podpocket.protocol import sync_api_pb2 as pb
from podpocket.sync.writer import FOLDER, ChangeWriter
from podpocket.timefmt import from_epoch_ms


async def apply_folder_records(
    writer: ChangeWriter, records: Iterable[pb.SyncUserFolder]
) -> set[str]:
    folders: dict[str, Folder] = {}
    changed: set[str] = set()
    for record in records:
        uuid = record.folder_uuid
        if not uuid:
            continue
        folder = folders.get(uuid) or await writer.session.get(Folder, (writer.user_uuid, uuid))
        if folder is None:
            folder = Folder(
                user_uuid=writer.user_uuid,
                folder_uuid=uuid,
                date_added=from_epoch_ms(writer.now_ms),
            )
            writer.session.add(folder)
        folders[uuid] = folder
        values: dict[str, object] = {
            "name": record.name,
            "color": record.color,
            "sort_position": record.sort_position,
            "podcasts_sort_type": record.podcasts_sort_type,
            "is_deleted": record.is_deleted,
        }
        date_added = from_timestamp(record.date_added) if record.HasField("date_added") else None
        if date_added is not None:
            values["date_added"] = date_added
        for field, value in values.items():
            if getattr(folder, field) != value:
                setattr(folder, field, value)
                writer.record(FOLDER, uuid, field, writer.now_ms)
                changed.add(uuid)
    return changed


def folder_record(folder: Folder) -> pb.SyncUserFolder:
    return pb.SyncUserFolder(
        folder_uuid=folder.folder_uuid,
        is_deleted=folder.is_deleted,
        name=folder.name,
        color=folder.color,
        sort_position=folder.sort_position,
        podcasts_sort_type=folder.podcasts_sort_type,
        date_added=to_timestamp(folder.date_added),
    )


def podcast_folder(folder: Folder) -> pb.PodcastFolder:
    return pb.PodcastFolder(
        folder_uuid=folder.folder_uuid,
        name=folder.name,
        color=folder.color,
        sort_position=folder.sort_position,
        podcasts_sort_type=folder.podcasts_sort_type,
        date_added=to_timestamp(folder.date_added),
    )
