import json

from tests.ma_gpodder.client import (
    EpisodeActionDownload,
    EpisodeActionGet,
    EpisodeActionNew,
    EpisodeActionPlay,
    SubscriptionsGet,
)

FEED = "https://feeds.test/show.xml"
EPISODE = "https://cdn.example.com/1.mp3"


def test_parses_mygpo_episode_actions():
    body = {
        "actions": [
            {
                "podcast": FEED,
                "episode": EPISODE,
                "guid": "ep-1",
                "action": "play",
                "timestamp": "2026-10-12T08:00:00",
                "started": 0,
                "position": 120,
                "total": 3600,
                "device": "phone",
            },
            {"podcast": FEED, "episode": EPISODE, "action": "NEW"},
            {"podcast": FEED, "episode": EPISODE, "action": "download"},
        ],
        "timestamp": 1234,
    }

    parsed = EpisodeActionGet.from_json(json.dumps(body))

    play, new, download = parsed.actions
    assert isinstance(play, EpisodeActionPlay)
    assert (play.guid, play.position, play.total) == ("ep-1", 120, 3600)
    assert isinstance(new, EpisodeActionNew)
    assert isinstance(download, EpisodeActionDownload)
    assert parsed.timestamp == 1234


def test_parses_subscriptions():
    parsed = SubscriptionsGet.from_json('{"add": ["a"], "remove": ["b"], "timestamp": 7}')

    assert (parsed.add, parsed.remove, parsed.timestamp) == (["a"], ["b"], 7)


def test_play_upload_has_the_mygpo_shape():
    action = EpisodeActionPlay(
        podcast=FEED, episode=EPISODE, timestamp="2026-10-12T08:00:00", position=1, total=2
    )

    assert action.to_dict() == {
        "podcast": FEED,
        "episode": EPISODE,
        "timestamp": "2026-10-12T08:00:00",
        "action": "play",
        "started": 0,
        "position": 1,
        "total": 2,
    }
